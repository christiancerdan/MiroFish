"""A stale preparation worker cannot overwrite a replacement worker's state."""
import pytest

from app.models.task import JobLeaseLost
from app.services.simulation_manager import SimulationManager, SimulationState, SimulationStatus


def test_lost_lease_does_not_write_terminal_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(SimulationManager, "SIMULATION_DATA_DIR", str(tmp_path))
    manager = SimulationManager()
    state = SimulationState("sim_lease", "project", "graph", status=SimulationStatus.CREATED)
    manager._save_simulation_state(state)
    writes = []
    real_save = manager._save_simulation_state
    def save(value):
        writes.append(value.status)
        real_save(value)
    monkeypatch.setattr(manager, "_save_simulation_state", save)
    def lost(*args, **kwargs):
        raise JobLeaseLost("replaced owner")
    with pytest.raises(JobLeaseLost):
        manager.prepare_simulation("sim_lease", "requirement", "document", progress_callback=lost)
    assert writes == [SimulationStatus.PREPARING]


def _claimed_job(tmp_path):
    from app.models.task import TaskManager
    jobs = TaskManager(str(tmp_path / 'jobs.sqlite3'))
    task_id = jobs.enqueue('prepare', 'prepare', {})
    jobs.claim_next('old-owner', 60)
    return jobs, task_id


def _replace_worker(jobs, task_id):
    import sqlite3
    with sqlite3.connect(jobs.db_path) as db:
        db.execute('UPDATE tasks SET lease_until=0 WHERE task_id=?', (task_id,))
    jobs.recover_expired()
    jobs.retry_task(task_id, 'retry-request', acknowledge_effects=True)
    assert jobs.claim_next('new-owner', 60).task_id == task_id


def test_stale_state_write_preserves_replacement_artifact(tmp_path, monkeypatch):
    import json
    jobs, task_id = _claimed_job(tmp_path)
    monkeypatch.setattr(SimulationManager, 'SIMULATION_DATA_DIR', str(tmp_path / 'simulations'))
    old = SimulationManager()
    stale = SimulationState('simulation', 'project', 'graph')
    old._save_simulation_state(stale)
    _replace_worker(jobs, task_id)
    replacement = SimulationState('simulation', 'project', 'graph', status=SimulationStatus.READY, config_reasoning='replacement output')
    with jobs.execution(task_id, 'new-owner'):
        SimulationManager()._save_simulation_state(replacement)
    stale.status = SimulationStatus.FAILED
    with jobs.execution(task_id, 'old-owner'), pytest.raises(JobLeaseLost):
        old._save_simulation_state(stale)
    saved = json.loads((tmp_path / 'simulations/simulation/state.json').read_text())
    assert saved['status'] == 'ready'
    assert saved['config_reasoning'] == 'replacement output'


def test_expired_config_response_cannot_overwrite_replacement_file(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from app.services import simulation_manager as module
    from app.services.zep_entity_reader import EntityNode, FilteredEntities
    jobs, task_id = _claimed_job(tmp_path)
    monkeypatch.setattr(SimulationManager, 'SIMULATION_DATA_DIR', str(tmp_path / 'simulations'))
    manager = SimulationManager()
    manager._save_simulation_state(SimulationState('simulation', 'project', 'graph', enable_twitter=False, enable_reddit=False))
    entity = EntityNode('alice', 'Alice', ['Person'], 'Summary', {})
    monkeypatch.setattr(module, 'ZepEntityReader', lambda: SimpleNamespace(filter_defined_entities=lambda **kwargs: FilteredEntities([entity], {'Person'}, 1, 1)))
    monkeypatch.setattr(module, 'OasisProfileGenerator', lambda **kwargs: SimpleNamespace(generate_profiles_from_entities=lambda **kwargs: [object()]))
    config_path = tmp_path / 'simulations/simulation/simulation_config.json'
    def generate_config(**kwargs):
        # Response returns after a replacement worker has published its config.
        _replace_worker(jobs, task_id)
        config_path.write_text(json.dumps({'owner': 'new-owner'}))
        return SimpleNamespace(to_json=lambda: json.dumps({'owner': 'old-owner'}), generation_reasoning='stale')
    monkeypatch.setattr(module, 'SimulationConfigGenerator', lambda: SimpleNamespace(generate_config=generate_config))
    with jobs.execution(task_id, 'old-owner'), pytest.raises(JobLeaseLost):
        manager.prepare_simulation('simulation', 'requirement', 'document')
    assert json.loads(config_path.read_text()) == {'owner': 'new-owner'}


def test_cancelled_prepare_does_not_publish_terminal_failure(tmp_path, monkeypatch):
    from app.models.task import JobCancelled
    jobs, task_id = _claimed_job(tmp_path)
    monkeypatch.setattr(SimulationManager, 'SIMULATION_DATA_DIR', str(tmp_path / 'simulations'))
    manager = SimulationManager()
    manager._save_simulation_state(SimulationState('simulation', 'project', 'graph'))
    writes = []
    real_save = manager._save_simulation_state
    def save(state):
        writes.append(state.status)
        return real_save(state)
    monkeypatch.setattr(manager, '_save_simulation_state', save)
    def checkpoint(*args, **kwargs):
        jobs.cancel_task(task_id)
        jobs.assert_current_execution()
    with jobs.execution(task_id, 'old-owner'), pytest.raises(JobCancelled):
        manager.prepare_simulation('simulation', 'requirement', 'document', progress_callback=checkpoint)
    assert writes == [SimulationStatus.PREPARING]
