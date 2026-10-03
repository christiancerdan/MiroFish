from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from flask import Flask

from app.api import graph as graph_api
from app.api import report as report_api
from app.api import simulation as simulation_api
from app.models.project import ProjectStatus
from app.models.task import TaskManager, TaskStatus
from app.services.simulation_manager import SimulationStatus
from app.services.simulation_runner import RunnerStatus
from app.utils.zep_lifecycle import (
    get_graph_readers,
    unregister_graph_reader,
)


def _json_result(result):
    if isinstance(result, tuple):
        response, status = result
    else:
        response, status = result, result.status_code
    return response.get_json(), status


def test_report_generation_waits_for_zep_ingestion(monkeypatch):
    simulation = SimpleNamespace(project_id="proj-1", graph_id="graph-1")
    monkeypatch.setattr(
        report_api,
        "SimulationManager",
        lambda: SimpleNamespace(
            get_simulation=lambda _simulation_id: simulation
        ),
    )
    monkeypatch.setattr(
        report_api.ReportManager,
        "get_report_by_simulation",
        classmethod(lambda _cls, _simulation_id: None),
    )
    monkeypatch.setattr(
        report_api.SimulationRunner,
        "get_run_state",
        classmethod(
            lambda _cls, _simulation_id: SimpleNamespace(
                runner_status=RunnerStatus.STOPPING
            )
        ),
    )
    monkeypatch.setattr(
        report_api.ZepGraphMemoryManager,
        "get_updater",
        classmethod(lambda _cls, _simulation_id: object()),
    )

    app = Flask(__name__)
    with app.test_request_context(
        "/api/report/generate",
        method="POST",
        json={"simulation_id": "sim-1"},
    ):
        body, status = _json_result(report_api.generate_report())

    assert status == 409
    assert body["ingestion_pending"] is True


def test_active_rerun_does_not_return_a_stale_completed_report(monkeypatch):
    simulation = SimpleNamespace(project_id="proj-1", graph_id="graph-1")
    monkeypatch.setattr(
        report_api,
        "SimulationManager",
        lambda: SimpleNamespace(
            get_simulation=lambda _simulation_id: simulation
        ),
    )
    monkeypatch.setattr(
        report_api.ReportManager,
        "get_report_by_simulation",
        classmethod(
            lambda _cls, _simulation_id: SimpleNamespace(
                report_id="old-report",
                status=report_api.ReportStatus.COMPLETED,
            )
        ),
    )
    monkeypatch.setattr(
        report_api.SimulationRunner,
        "get_run_state",
        classmethod(
            lambda _cls, _simulation_id: SimpleNamespace(
                runner_status=RunnerStatus.STOPPING
            )
        ),
    )
    monkeypatch.setattr(
        report_api.ZepGraphMemoryManager,
        "get_updater",
        classmethod(lambda _cls, _simulation_id: object()),
    )

    app = Flask(__name__)
    with app.test_request_context(
        "/api/report/generate",
        method="POST",
        json={"simulation_id": "sim-1"},
    ):
        body, status = _json_result(report_api.generate_report())

    assert status == 409
    assert body["ingestion_pending"] is True


def test_failed_ingestion_cannot_generate_a_report_after_restart(monkeypatch):
    simulation = SimpleNamespace(project_id="proj-1", graph_id="graph-1")
    monkeypatch.setattr(
        report_api,
        "SimulationManager",
        lambda: SimpleNamespace(
            get_simulation=lambda _simulation_id: simulation
        ),
    )
    monkeypatch.setattr(
        report_api.SimulationRunner,
        "get_run_state",
        classmethod(
            lambda _cls, _simulation_id: SimpleNamespace(
                runner_status=RunnerStatus.FAILED
            )
        ),
    )
    monkeypatch.setattr(
        report_api.ZepGraphMemoryManager,
        "get_updater",
        classmethod(lambda _cls, _simulation_id: None),
    )

    app = Flask(__name__)
    with app.test_request_context(
        "/api/report/generate",
        method="POST",
        json={"simulation_id": "sim-1"},
    ):
        body, status = _json_result(report_api.generate_report())

    assert status == 409
    assert "successfully completed" in body["error"]


@pytest.mark.parametrize("enable_graph_memory_update", [True, False])
@pytest.mark.parametrize("force", [True, False])
def test_report_reader_lease_blocks_graph_start_and_delete(monkeypatch, enable_graph_memory_update, force):
    simulation = SimpleNamespace(
        simulation_id="sim-1",
        project_id="proj-1",
        graph_id="graph-1",
        status=SimulationStatus.COMPLETED if force else SimulationStatus.READY,
    )
    project = SimpleNamespace(
        project_id="proj-1",
        graph_id="graph-1",
        status=ProjectStatus.GRAPH_COMPLETED,
        simulation_requirement="mock requirement",
    )
    run_state = SimpleNamespace(runner_status=RunnerStatus.COMPLETED, execution_id="execution-1",
                                source_graph_id="graph-1", execution_graph_id="execution-graph-1")
    queued_jobs = []
    runner_calls = []
    failed_tasks = []

    class Tasks:
        def enqueue(self, **kwargs):
            if not queued_jobs:
                queued_jobs.append(kwargs)
            return "task-1"

        def get_task(self, _task_id):
            return SimpleNamespace(metadata=queued_jobs[0]["metadata"], status=TaskStatus.PENDING, error_code=None, message="")

        def publication_guard(self):
            return nullcontext()

        def update_task(self, *_args, **_kwargs):
            pass

        def complete_task(self, *_args, **_kwargs):
            pass

        def fail_task(self, *_args, **_kwargs):
            failed_tasks.append(_args)

    class Agent:
        def __init__(self, **_kwargs):
            pass

        def generate_report(self, *, progress_callback, report_id):
            readers = get_graph_readers("graph-1")
            assert len(readers) == 1 and readers[0].startswith(report_id + ":")
            with app.test_request_context(
                "/api/simulation/start", method="POST",
                json={"simulation_id": "sim-1", "enable_graph_memory_update": enable_graph_memory_update, "force": force},
            ):
                start_body, start_status = _json_result(simulation_api.start_simulation())
            assert start_status == 409
            assert start_body["active_reports"] == readers
            assert runner_calls == []
            with pytest.raises(graph_api.GraphInUseError, match=f"report:{report_id}"):
                graph_api._delete_cloud_graph_if_present("graph-1")
            progress_callback("mock", 100, "done")
            return SimpleNamespace(
                report_id=report_id,
                status=report_api.ReportStatus.COMPLETED,
                error=None,
            )

    monkeypatch.setattr(
        report_api,
        "SimulationManager",
        lambda: SimpleNamespace(
            get_simulation=lambda _simulation_id: simulation
        ),
    )
    monkeypatch.setattr(
        simulation_api,
        "SimulationManager",
        lambda: SimpleNamespace(
            get_simulation=lambda _simulation_id: simulation
        ),
    )
    monkeypatch.setattr(
        report_api.ProjectManager,
        "get_project",
        classmethod(lambda _cls, _project_id: project),
    )
    monkeypatch.setattr(
        report_api.SimulationRunner,
        "get_run_state",
        classmethod(lambda _cls, _simulation_id: run_state),
    )
    monkeypatch.setattr(
        report_api.ZepGraphMemoryManager,
        "get_updater",
        classmethod(lambda _cls, _simulation_id: None),
    )
    monkeypatch.setattr(
        report_api.ReportManager,
        "get_report_by_simulation",
        classmethod(lambda _cls, _simulation_id: None),
    )
    monkeypatch.setattr(
        report_api.ReportManager,
        "save_report",
        classmethod(lambda _cls, _report: None),
    )
    monkeypatch.setattr(report_api, "TaskManager", Tasks)
    monkeypatch.setattr(report_api, "ReportAgent", Agent)
    monkeypatch.setattr(
        simulation_api.SimulationRunner,
        "start_simulation",
        classmethod(
            lambda _cls, **_kwargs: runner_calls.append(True)
        ),
    )
    monkeypatch.setattr(
        graph_api.ZepGraphMemoryManager,
        "get_simulation_ids_for_graph",
        classmethod(lambda _cls, _graph_id: []),
    )
    monkeypatch.setattr(
        graph_api,
        "SimulationManager",
        lambda: SimpleNamespace(list_simulations=lambda: []),
    )

    app = Flask(__name__)
    report_id = None
    try:
        with app.test_request_context(
            "/api/report/generate",
            method="POST",
            json={"simulation_id": "sim-1"},
        ):
            body, status = _json_result(report_api.generate_report())
        assert status == 200
        report_id = body["data"]["report_id"]
        assert get_graph_readers("graph-1") == []
        assert len(queued_jobs) == 1
        queued_job = queued_jobs[0]
        assert queued_job["handler"] == "report_generate"
        assert queued_job["parameters"]["project_id"] == "proj-1"
        assert queued_job["dedupe_key"] == "report_generate:sim-1:execution-1"
        with app.test_request_context(
            "/api/report/generate", method="POST", json={"simulation_id": "sim-1"},
        ):
            duplicate, duplicate_status = _json_result(report_api.generate_report())
        assert duplicate_status == 200
        assert duplicate["data"]["report_id"] == report_id
        assert duplicate["data"]["task_id"] == "task-1"

        report_api.run_report_job("task-1", queued_job["parameters"])
        assert failed_tasks == []
        assert get_graph_readers("graph-1") == []
    finally:
        if report_id:
            unregister_graph_reader("graph-1", report_id)


@pytest.mark.parametrize("change", ["graph", "requirement", "active_run", "new_run", "ingestion"])
def test_report_job_revalidates_queued_inputs_before_provider_call(monkeypatch, change):
    state = SimpleNamespace(project_id="proj-1", graph_id="graph-1")
    project = SimpleNamespace(
        graph_id="graph-1", status=ProjectStatus.GRAPH_COMPLETED,
        simulation_requirement="original requirement",
    )
    run = SimpleNamespace(
        runner_status=RunnerStatus.COMPLETED, started_at="first-run", completed_at="finished",
        execution_id="execution-1", source_graph_id="graph-1", execution_graph_id="execution-graph-1",
    )
    parameters = {
        "simulation_id": "sim-1", "project_id": "proj-1", "graph_id": "execution-graph-1",
        "execution_id": "execution-1", "source_graph_id": "graph-1", "execution_graph_id": "execution-graph-1",
        "report_id": "report-1", "simulation_requirement": "original requirement",
        "run_started_at": "first-run", "run_completed_at": "finished", "locale": "en",
    }
    if change == "graph":
        project.graph_id = "graph-rebuilt"
    elif change == "requirement":
        project.simulation_requirement = "new requirement"
    elif change == "active_run":
        run.runner_status = RunnerStatus.RUNNING
    elif change == "new_run":
        run.started_at = "second-run"
    failures = []
    monkeypatch.setattr(report_api, "TaskManager", lambda: SimpleNamespace(
        fail_task=lambda task_id, error: failures.append((task_id, error)),
        get_task=lambda _task_id: SimpleNamespace(metadata={"report_id": "report-1"}),
    ))
    monkeypatch.setattr(report_api, "SimulationManager", lambda: SimpleNamespace(
        get_simulation=lambda _simulation_id: state,
    ))
    monkeypatch.setattr(report_api.ProjectManager, "get_project", lambda _project_id: project)
    monkeypatch.setattr(report_api.SimulationRunner, "get_run_state", lambda _simulation_id: run)
    monkeypatch.setattr(report_api.ZepGraphMemoryManager, "get_updater", lambda _simulation_id: object() if change == "ingestion" else None)
    def unexpected_provider(**_kwargs):
        pytest.fail("Provider must not run against invalid queued inputs")
    monkeypatch.setattr(report_api, "ReportAgent", unexpected_provider)
    report_api.run_report_job("task-1", parameters)
    assert len(failures) == 1
    assert isinstance(failures[0][1], ValueError)
    assert get_graph_readers("graph-1") == []


def test_report_job_preserves_failure_type_and_releases_reader(monkeypatch):
    error = RuntimeError("provider quota reached")
    error.code = "budget_exceeded"
    failures = []
    parameters = {
        "simulation_id": "sim-1", "project_id": "proj-1", "graph_id": "execution-graph-1",
        "execution_id": "execution-1", "source_graph_id": "graph-1", "execution_graph_id": "execution-graph-1",
        "report_id": "report-1", "simulation_requirement": "requirement",
    }
    monkeypatch.setattr(report_api, "TaskManager", lambda: SimpleNamespace(
        update_task=lambda *_args, **_kwargs: None,
        fail_task=lambda task_id, failure: failures.append(failure),
        get_task=lambda _task_id: SimpleNamespace(metadata={"report_id": "report-1"}),
    ))
    monkeypatch.setattr(report_api, "SimulationManager", lambda: SimpleNamespace(
        get_simulation=lambda _simulation_id: SimpleNamespace(project_id="proj-1", graph_id="graph-1"),
    ))
    monkeypatch.setattr(report_api.ProjectManager, "get_project", lambda _project_id: SimpleNamespace(
        graph_id="graph-1", status=ProjectStatus.GRAPH_COMPLETED, simulation_requirement="requirement",
    ))
    monkeypatch.setattr(report_api.SimulationRunner, "get_run_state", lambda _simulation_id: SimpleNamespace(
        runner_status=RunnerStatus.COMPLETED, execution_id="execution-1", source_graph_id="graph-1",
        execution_graph_id="execution-graph-1"))
    monkeypatch.setattr(report_api.ZepGraphMemoryManager, "get_updater", lambda _simulation_id: None)
    def fail_provider(**_kwargs):
        readers = get_graph_readers("graph-1")
        assert len(readers) == 1 and readers[0].startswith("report-1:")
        raise error
    monkeypatch.setattr(report_api, "ReportAgent", fail_provider)
    report_api.run_report_job("task-1", parameters)
    assert failures == [error]
    assert get_graph_readers("graph-1") == []


@pytest.mark.parametrize("terminal_status", [
    TaskStatus.PENDING, TaskStatus.COMPLETED, TaskStatus.INTERRUPTED, TaskStatus.BUDGET_EXCEEDED,
])
def test_report_enqueue_idempotency_preserves_report_id_after_restart(monkeypatch, tmp_path, terminal_status):
    tasks = TaskManager(db_path=tmp_path / "jobs.sqlite3")
    monkeypatch.setattr(report_api, "TaskManager", lambda: tasks)
    monkeypatch.setattr(report_api, "SimulationManager", lambda: SimpleNamespace(
        get_simulation=lambda _simulation_id: SimpleNamespace(project_id="proj-1", graph_id="graph-1"),
    ))
    monkeypatch.setattr(report_api.ProjectManager, "get_project", lambda _project_id: SimpleNamespace(
        project_id="proj-1", graph_id="graph-1", status=ProjectStatus.GRAPH_COMPLETED,
        simulation_requirement="requirement",
    ))
    monkeypatch.setattr(report_api.SimulationRunner, "get_run_state", lambda _simulation_id: SimpleNamespace(
        runner_status=RunnerStatus.COMPLETED, started_at="first-run", completed_at="finished",
        execution_id="execution-1", source_graph_id="graph-1", execution_graph_id="execution-graph-1",
    ))
    monkeypatch.setattr(report_api.ZepGraphMemoryManager, "get_updater", lambda _simulation_id: None)
    monkeypatch.setattr(report_api.ReportManager, "get_report_by_simulation", lambda _simulation_id: None)
    app = Flask(__name__)
    def submit():
        with app.test_request_context(
            "/api/report/generate", method="POST", json={"simulation_id": "sim-1"},
            headers={"Idempotency-Key": "report-request-1"},
        ):
            return _json_result(report_api.generate_report())
    first, first_status = submit()
    assert first_status == 200
    first_data = first["data"]
    error_code = 'budget_exceeded' if terminal_status == TaskStatus.BUDGET_EXCEEDED else None
    if terminal_status != TaskStatus.PENDING:
        tasks.update_task(first_data['task_id'], status=terminal_status, error_code=error_code)
    tasks = TaskManager(db_path=tasks.db_path)
    second, second_status = submit()
    assert second_status == 200
    assert second["data"]["task_id"] == first_data["task_id"]
    assert second["data"]["report_id"] == first_data["report_id"]
    assert get_graph_readers("graph-1") == []
    queued = tasks.get_task(first_data["task_id"])
    assert queued.metadata["project_id"] == queued.parameters["project_id"]
    assert "report_id" not in queued.parameters
    assert queued.status == terminal_status
    expected_status = 'generating' if terminal_status == TaskStatus.PENDING else terminal_status.value
    assert second['data']['status'] == expected_status
    assert second['data']['task_status'] == terminal_status.value
    assert second['data']['error_code'] == error_code
    with app.test_request_context(
        '/api/report/generate', method='POST',
        json={'simulation_id': 'sim-1', 'force_regenerate': True},
        headers={'Idempotency-Key': 'report-request-1'},
    ):
        conflict, conflict_status = _json_result(report_api.generate_report())
    assert conflict_status == 400
    assert 'Idempotency-Key' in conflict['error']


def test_report_status_prioritizes_explicit_failed_task_over_cached_report(monkeypatch, tmp_path):
    tasks = TaskManager(db_path=tmp_path / 'jobs.sqlite3')
    task_id = tasks.enqueue('report_generate', 'report_generate', {})
    error = RuntimeError('report budget exhausted')
    error.code = 'budget_exceeded'
    tasks.fail_task(task_id, error)
    monkeypatch.setattr(report_api, 'TaskManager', lambda: tasks)
    def unexpected_cache(_simulation_id):
        pytest.fail('An explicit task_id must not be hidden by a cached report')
    monkeypatch.setattr(report_api.ReportManager, 'get_report_by_simulation', unexpected_cache)
    app = Flask(__name__)
    with app.test_request_context('/api/report/generate/status', method='POST',
                                  json={'task_id': task_id, 'simulation_id': 'sim-1'}):
        body, status = _json_result(report_api.get_generate_status())
    assert status == 200
    assert body['data']['status'] == 'budget_exceeded'
    assert body['data']['error_code'] == 'budget_exceeded'
