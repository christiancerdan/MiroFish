"""Regressions for caller-controlled storage paths and duplicate paid prepare work."""
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest
from flask import Flask

from app.api import simulation as simulation_api
from app.config import Config
from app.models.project import ProjectManager, ProjectStatus
from app.models.task import TaskManager, TaskStatus
from app.utils.zep_lifecycle import get_graph_readers
from app.services.simulation_manager import SimulationManager, SimulationStatus


@pytest.fixture
def storage(tmp_path, monkeypatch):
    simulations = tmp_path / "simulations"
    projects = tmp_path / "projects"
    simulations.mkdir()
    projects.mkdir()
    monkeypatch.setattr(SimulationManager, "SIMULATION_DATA_DIR", str(simulations))
    monkeypatch.setattr(Config, "OASIS_SIMULATION_DATA_DIR", str(simulations))
    monkeypatch.setattr(simulation_api.SimulationRunner, "RUN_STATE_DIR", str(simulations))
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(projects))
    return simulations, projects


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(simulation_api.simulation_bp, url_prefix="/api/simulation")
    app.config["TESTING"] = True
    return app.test_client()


@pytest.mark.parametrize("identifier", ["../outside", "..", ".", "a/b", "a\\b", "", None, 12, [], {}])
def test_service_loads_reject_invalid_identifiers(storage, identifier):
    manager = SimulationManager()
    for load in (manager.get_simulation, ProjectManager.get_project):
        with pytest.raises(ValueError):
            load(identifier)


def test_failed_simulation_load_never_creates_storage_directory(storage, tmp_path):
    manager = SimulationManager()
    assert manager.get_simulation("sim_missing") is None
    assert not (storage[0] / "sim_missing").exists()
    outside = tmp_path / "outside"
    with pytest.raises(ValueError):
        manager.get_simulation(str(outside))
    assert not outside.exists()


@pytest.mark.parametrize("kind", ["simulation", "project"])
def test_service_loads_reject_symlinked_record_directories(storage, tmp_path, kind):
    outside = tmp_path / "private"
    outside.mkdir()
    if kind == "simulation":
        (outside / "state.json").write_text('{"status": "created"}')
        (storage[0] / "sim_link").symlink_to(outside, target_is_directory=True)
        load, identifier = SimulationManager().get_simulation, "sim_link"
    else:
        (outside / "project.json").write_text('{"project_id": "proj_link"}')
        (storage[1] / "proj_link").symlink_to(outside, target_is_directory=True)
        load, identifier = ProjectManager.get_project, "proj_link"
    with pytest.raises(ValueError):
        load(identifier)


@pytest.mark.parametrize("kind", ["simulation", "project"])
def test_service_loads_reject_symlinked_record_files(storage, tmp_path, kind):
    outside = tmp_path / "private.json"
    outside.write_text('{"project_id": "proj-1", "status": "created"}')
    if kind == "simulation":
        folder = storage[0] / "sim-1"
        folder.mkdir()
        (folder / "state.json").symlink_to(outside)
        load, identifier = SimulationManager().get_simulation, "sim-1"
    else:
        folder = storage[1] / "proj-1"
        folder.mkdir()
        (folder / "project.json").symlink_to(outside)
        load, identifier = ProjectManager.get_project, "proj-1"
    with pytest.raises(ValueError):
        load(identifier)


@pytest.mark.parametrize("endpoint, table", [("posts", "post"), ("comments", "comment")])
def test_database_endpoints_reject_absolute_platform_paths(storage, tmp_path, client, endpoint, table):
    outside = tmp_path / "private_simulation.db"
    with sqlite3.connect(outside) as conn:
        conn.execute(f"CREATE TABLE {table} (created_at TEXT, content TEXT)")
        conn.execute(f"INSERT INTO {table} VALUES ('2026-01-01', 'private sentinel')")
    response = client.get(f"/api/simulation/sim-1/{endpoint}", query_string={"platform": str(tmp_path / "private")})
    assert response.status_code == 400
    assert "private sentinel" not in response.get_data(as_text=True)


@pytest.mark.parametrize("endpoint, table", [("posts", "post"), ("comments", "comment")])
def test_database_endpoints_reject_symlinked_database(storage, tmp_path, client, endpoint, table):
    outside = tmp_path / "private.db"
    with sqlite3.connect(outside) as conn:
        conn.execute(f"CREATE TABLE {table} (created_at TEXT, content TEXT)")
        conn.execute(f"INSERT INTO {table} VALUES ('2026-01-01', 'private sentinel')")
    folder = storage[0] / "sim-1"
    folder.mkdir()
    (folder / "reddit_simulation.db").symlink_to(outside)
    response = client.get(f"/api/simulation/sim-1/{endpoint}?platform=reddit")
    assert response.status_code == 400
    assert "private sentinel" not in response.get_data(as_text=True)


@pytest.mark.parametrize("endpoint, table", [("posts", "post"), ("comments", "comment")])
def test_database_endpoints_read_valid_records_in_configured_storage(storage, client, endpoint, table):
    folder = storage[0] / "sim-1"
    folder.mkdir()
    with sqlite3.connect(folder / "reddit_simulation.db") as conn:
        conn.execute(f"CREATE TABLE {table} (created_at TEXT, content TEXT)")
        conn.execute(f"INSERT INTO {table} VALUES ('2026-01-01', 'public post')")
    response = client.get(f"/api/simulation/sim-1/{endpoint}?platform=reddit")
    assert response.status_code == 200
    assert response.json["data"][endpoint] == [{"created_at": "2026-01-01", "content": "public post"}]


@pytest.fixture
def prepared_inputs(storage, monkeypatch):
    project = ProjectManager.create_project("Security regression")
    project.simulation_requirement = "Describe the likely response"
    project.graph_id = "graph-test"
    project.status = ProjectStatus.GRAPH_COMPLETED
    ProjectManager.save_project(project)
    state = SimulationManager().create_simulation(project.project_id, "graph-test")
    monkeypatch.setattr(simulation_api, "assert_clean_simulation_source", lambda _: None)
    monkeypatch.setattr(simulation_api, "ZepEntityReader", lambda: SimpleNamespace(
        filter_defined_entities=lambda **kwargs: SimpleNamespace(filtered_count=1, entity_types=["Person"])
    ))
    return state.simulation_id


@pytest.fixture
def tasks(tmp_path, monkeypatch):
    manager = TaskManager(db_path=tmp_path / "jobs.sqlite3")
    monkeypatch.setattr(simulation_api, "TaskManager", lambda: manager)
    return manager


def test_overlapping_prepare_requests_do_not_start_duplicate_work(client, prepared_inputs, tasks, monkeypatch):
    calls = []
    def prepare(self, simulation_id, **kwargs):
        calls.append(kwargs)
        assert get_graph_readers("graph-test")
        state = self.get_simulation(simulation_id)
        state.status = SimulationStatus.READY
        self._save_simulation_state(state)
        return state

    monkeypatch.setattr(SimulationManager, "prepare_simulation", prepare)
    payload = {"simulation_id": prepared_inputs, "force_regenerate": True}
    first = client.post("/api/simulation/prepare", json=payload)
    assert first.status_code == 200
    assert calls == []
    first_id = first.json["data"]["task_id"]
    second = client.post("/api/simulation/prepare", json=payload)
    assert second.status_code == 200
    assert second.json["data"]["task_id"] == first_id
    assert get_graph_readers("graph-test") == []

    # A fresh queue handle can execute the saved request without a request closure.
    restarted = TaskManager(db_path=tasks.db_path)
    queued = restarted.get_task(first_id)
    assert queued.handler == "simulation_prepare"
    assert "document_text" not in queued.parameters
    assert queued.parameters["project_id"] == queued.metadata["project_id"]
    monkeypatch.setattr(ProjectManager, "get_extracted_text", lambda _project_id: "reloaded document")
    simulation_api.run_prepare_job(first_id, queued.parameters)
    assert restarted.get_task(first_id).status == TaskStatus.COMPLETED
    assert calls[0]["document_text"] == "reloaded document"
    assert get_graph_readers("graph-test") == []
    retried = client.post("/api/simulation/prepare", json=payload)
    assert retried.status_code == 200
    assert retried.json["data"]["task_id"] != first_id


def test_prepare_claim_released_after_enqueue_failure(client, prepared_inputs, tasks, monkeypatch):
    enqueue = tasks.enqueue
    def fail_enqueue(**_kwargs):
        raise RuntimeError("queue unavailable")
    monkeypatch.setattr(tasks, "enqueue", fail_enqueue)
    first = client.post("/api/simulation/prepare", json={"simulation_id": prepared_inputs})
    assert first.status_code == 500

    monkeypatch.setattr(tasks, "enqueue", enqueue)
    def fail_prepare(self, **kwargs):
        raise RuntimeError("provider failed")
    monkeypatch.setattr(SimulationManager, "prepare_simulation", fail_prepare)
    second = client.post("/api/simulation/prepare", json={"simulation_id": prepared_inputs})
    assert second.status_code == 200
    task_id = second.json["data"]["task_id"]
    simulation_api.run_prepare_job(task_id, tasks.get_task(task_id).parameters)
    assert tasks.get_task(task_id).status == TaskStatus.FAILED
    assert get_graph_readers("graph-test") == []
    third = client.post("/api/simulation/prepare", json={"simulation_id": prepared_inputs})
    assert third.status_code == 200
    assert second.json["data"]["task_id"] != third.json["data"]["task_id"]


def test_prepare_idempotency_reuses_completed_task(client, prepared_inputs, tasks):
    payload = {"simulation_id": prepared_inputs}
    headers = {"Idempotency-Key": "prepare-request-1"}
    first = client.post("/api/simulation/prepare", json=payload, headers=headers)
    assert first.status_code == 200
    task_id = first.json["data"]["task_id"]
    tasks.complete_task(task_id, {"simulation_id": prepared_inputs})
    second = client.post("/api/simulation/prepare", json=payload, headers=headers)
    assert second.status_code == 200
    assert second.json["data"]["task_id"] == task_id


def test_prepare_job_rejects_changed_graph_before_provider_call(client, prepared_inputs, tasks, monkeypatch):
    response = client.post("/api/simulation/prepare", json={"simulation_id": prepared_inputs})
    task_id = response.json["data"]["task_id"]
    queued = tasks.get_task(task_id)
    project = ProjectManager.get_project(queued.parameters["project_id"])
    project.graph_id = "new-graph"
    ProjectManager.save_project(project)
    def unexpected_prepare(*_args, **_kwargs):
        pytest.fail("Must not generate against a replaced graph")
    monkeypatch.setattr(SimulationManager, "prepare_simulation", unexpected_prepare)
    simulation_api.run_prepare_job(task_id, queued.parameters)
    assert tasks.get_task(task_id).status == TaskStatus.FAILED
    assert "graph changed" in tasks.get_task(task_id).error
    assert SimulationManager().get_simulation(prepared_inputs).status == SimulationStatus.CREATED
    assert get_graph_readers("graph-test") == []


def test_prepare_job_preserves_budget_failure(client, prepared_inputs, tasks, monkeypatch):
    response = client.post("/api/simulation/prepare", json={"simulation_id": prepared_inputs})
    task_id = response.json["data"]["task_id"]
    error = RuntimeError("project budget exhausted")
    error.code = "budget_exceeded"
    def fail_prepare(*_args, **_kwargs):
        raise error
    monkeypatch.setattr(SimulationManager, "prepare_simulation", fail_prepare)
    simulation_api.run_prepare_job(task_id, tasks.get_task(task_id).parameters)
    assert tasks.get_task(task_id).status == TaskStatus.BUDGET_EXCEEDED
    assert get_graph_readers("graph-test") == []


@pytest.mark.parametrize("count", [0, -1, 33, 2.5, True, "3", None])
def test_prepare_rejects_invalid_concurrency_before_background_work(client, prepared_inputs, count, monkeypatch):
    def no_external_prepare(self, **kwargs):
        raise RuntimeError("Unexpected worker for invalid input")
    monkeypatch.setattr(SimulationManager, "prepare_simulation", no_external_prepare)
    response = client.post("/api/simulation/prepare", json={"simulation_id": prepared_inputs, "parallel_profile_count": count})
    assert response.status_code == 400


@pytest.mark.parametrize("rounds", [True, 1.5, "10", 0, -1, 10001])
def test_start_rejects_invalid_round_limits_before_loading_simulation(client, storage, rounds):
    response = client.post("/api/simulation/start", json={"simulation_id": "sim_missing", "max_rounds": rounds})
    assert response.status_code == 400


def test_state_write_failure_preserves_previous_json(storage, monkeypatch):
    manager = SimulationManager()
    state = manager.create_simulation("proj-1", "graph-1")
    state.entities_count = object()  # Simulate a serialization failure during a state update.
    with pytest.raises(TypeError):
        manager._save_simulation_state(state)
    persisted = json.loads((storage[0] / state.simulation_id / "state.json").read_text())
    assert persisted["entities_count"] == 0
    assert list((storage[0] / state.simulation_id).iterdir()) == [storage[0] / state.simulation_id / "state.json"]


def test_listing_storage_skips_symlink_escapes_and_keeps_valid_records(storage, tmp_path):
    manager = SimulationManager()
    state = manager.create_simulation("proj-1", "graph-1")
    project = ProjectManager.create_project("Visible project")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "state.json").write_text('{"status":"created"}')
    (outside / "project.json").write_text('{"project_id":"proj_private"}')
    (storage[0] / "sim_link").symlink_to(outside, target_is_directory=True)
    (storage[1] / "proj_link").symlink_to(outside, target_is_directory=True)
    assert [item.simulation_id for item in manager.list_simulations()] == [state.simulation_id]
    assert [item.project_id for item in ProjectManager.list_projects()] == [project.project_id]


def test_prepare_claim_is_held_while_synchronous_preview_is_still_running(client, prepared_inputs, tasks, monkeypatch):
    preview_started, release_preview = threading.Event(), threading.Event()
    responses = []

    def preview(**kwargs):
        if not preview_started.is_set():
            preview_started.set()
            assert release_preview.wait(5)
        return SimpleNamespace(filtered_count=1, entity_types=["Person"])

    monkeypatch.setattr(simulation_api, "ZepEntityReader", lambda: SimpleNamespace(filter_defined_entities=preview))

    def first_request():
        with client.application.test_client() as another_client:
            responses.append(another_client.post("/api/simulation/prepare", json={"simulation_id": prepared_inputs}))

    request_thread = threading.Thread(target=first_request)
    request_thread.start()
    try:
        assert preview_started.wait(2)
        second = client.post("/api/simulation/prepare", json={"simulation_id": prepared_inputs, "force_regenerate": True})
        assert second.status_code == 409
    finally:
        release_preview.set()
        request_thread.join(timeout=5)
    assert not request_thread.is_alive()
    assert responses[0].status_code == 200


@pytest.mark.parametrize('status, error_code', [
    (TaskStatus.INTERRUPTED, 'worker_interrupted'),
    (TaskStatus.BUDGET_EXCEEDED, 'budget_exceeded'),
])
def test_prepare_idempotent_response_exposes_terminal_state(client, prepared_inputs, tasks, status, error_code):
    headers = {'Idempotency-Key': 'original-prepare'}
    payload = {'simulation_id': prepared_inputs}
    first = client.post('/api/simulation/prepare', json=payload, headers=headers)
    task_id = first.json['data']['task_id']
    tasks.update_task(task_id, status=status, error_code=error_code, message='Requires attention')
    repeated = client.post('/api/simulation/prepare', json=payload, headers=headers)
    assert repeated.status_code == 200
    assert repeated.json['data']['status'] == status.value
    assert repeated.json['data']['task_status'] == status.value
    assert repeated.json['data']['error_code'] == error_code
    assert repeated.json['data']['message'] == 'Requires attention'


def test_prepare_status_prioritizes_explicit_failed_task_over_cached_simulation(client, prepared_inputs, tasks, monkeypatch):
    task_id = tasks.enqueue('simulation_prepare', 'simulation_prepare', {})
    tasks.fail_task(task_id, RuntimeError('new preparation failed'))
    client.application.config['JOBS_DB_PATH'] = tasks.db_path
    def unexpected_cache(_simulation_id):
        pytest.fail('An explicit task_id must not be hidden by a cached preparation')
    monkeypatch.setattr(simulation_api, '_check_simulation_prepared', unexpected_cache)
    response = client.post('/api/simulation/prepare/status', json={'task_id': task_id, 'simulation_id': prepared_inputs})
    assert response.status_code == 200
    assert response.json['data']['status'] == 'failed'
    assert response.json['data']['error'] == 'new preparation failed'


def test_prepare_validates_force_and_detects_changed_idempotent_semantics(client, prepared_inputs, tasks):
    payload = {'simulation_id': prepared_inputs, 'force_regenerate': 'false'}
    assert client.post('/api/simulation/prepare', json=payload).status_code == 400
    payload['force_regenerate'] = False
    headers = {'Idempotency-Key': 'original-prepare'}
    first = client.post('/api/simulation/prepare', json=payload, headers=headers)
    assert first.status_code == 200
    payload['force_regenerate'] = True
    changed = client.post('/api/simulation/prepare', json=payload, headers=headers)
    assert changed.status_code == 400
    assert 'Idempotency-Key' in changed.json['error']
