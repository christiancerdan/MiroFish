"""Every launch has an independent graph and empty platform state."""
import json
from types import SimpleNamespace

import pytest

from app.config import Config
from app.services import simulation_runner as runner_module
from app.services.simulation_runner import SimulationRunner, RunnerStatus


@pytest.fixture
def launch(tmp_path, monkeypatch):
    runs = tmp_path / 'runs'
    folder = runs / 'sim-isolated'
    scripts = tmp_path / 'scripts'
    folder.mkdir(parents=True)
    scripts.mkdir()
    (scripts / 'run_parallel_simulation.py').write_text('pass\n')
    (folder / 'simulation_config.json').write_text(json.dumps({
        'time_config': {'total_simulation_hours': 1, 'minutes_per_round': 60},
    }))
    monkeypatch.setattr(Config, 'GRAPH_BACKEND', 'local')
    monkeypatch.setattr(SimulationRunner, 'RUN_STATE_DIR', str(runs))
    monkeypatch.setattr(SimulationRunner, 'SCRIPTS_DIR', str(scripts))
    for name in ('_run_states', '_processes', '_monitor_threads', '_stdout_files',
                 '_stderr_files', '_action_queues', '_graph_memory_enabled'):
        monkeypatch.setattr(SimulationRunner, name, {})
    monkeypatch.setattr(SimulationRunner, '_sync_simulation_status', classmethod(lambda cls, *args: None))
    monkeypatch.setattr(runner_module.ZepGraphMemoryManager, 'get_updater', lambda _: None)
    calls, writes, processes = [], [], []
    def clone(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(source_snapshot_sha256='source-hash')
    graph = SimpleNamespace(clone_for_execution=clone)
    monkeypatch.setattr(runner_module, 'get_zep_client', lambda: SimpleNamespace(graph=graph), raising=False)
    monkeypatch.setattr(runner_module.ZepGraphMemoryManager, 'create_updater',
                        lambda *args, **kwargs: writes.append((args, kwargs)))
    monkeypatch.setattr(runner_module.threading, 'Thread', lambda **kwargs: SimpleNamespace(start=lambda: None))
    def popen(*args, **kwargs):
        processes.append(kwargs)
        return SimpleNamespace(pid=12345, poll=lambda: 0)
    monkeypatch.setattr(runner_module.subprocess, 'Popen', popen)
    yield SimpleNamespace(folder=folder, calls=calls, writes=writes, processes=processes, graph=graph)
    for stream in SimulationRunner._stdout_files.values():
        stream.close()


@pytest.mark.parametrize('memory', [True, False])
def test_launch_persists_unique_graph_binding_and_only_updates_execution_graph(launch, memory):
    state = SimulationRunner.start_simulation('sim-isolated', platform='reddit',
        graph_id='source-graph', enable_graph_memory_update=memory)
    assert state.execution_id
    assert state.source_graph_id == 'source-graph'
    assert state.execution_graph_id != 'source-graph'
    assert state.source_snapshot_sha256 == 'source-hash'
    assert launch.calls == [{
        'source_graph_id': 'source-graph', 'graph_id': state.execution_graph_id,
        'execution_id': state.execution_id, 'simulation_id': 'sim-isolated',
    }]
    if memory:
        assert launch.writes == [(('sim-isolated', state.execution_graph_id), {'execution_id': state.execution_id})]
    else:
        assert launch.writes == []
    recovered = SimulationRunner._load_run_state('sim-isolated')
    assert recovered.to_dict()['execution_id'] == state.execution_id
    assert recovered.to_dict()['source_graph_id'] == 'source-graph'
    assert recovered.to_dict()['execution_graph_id'] == state.execution_graph_id
    assert recovered.to_dict()['source_snapshot_sha256'] == 'source-hash'


def test_rerun_clears_both_platforms_and_ipc_even_without_force(launch):
    first = SimulationRunner.start_simulation('sim-isolated', graph_id='source-graph')
    first.runner_status = RunnerStatus.COMPLETED
    SimulationRunner._save_run_state(first)
    for name in ('twitter_simulation.db', 'reddit_simulation.db', 'reddit_simulation.db-wal', 'env_status.json', 'actions.jsonl'):
        (launch.folder / name).write_text('previous execution')
    for name in ('twitter', 'reddit', 'ipc_commands', 'ipc_responses'):
        (launch.folder / name).mkdir(exist_ok=True)
        (launch.folder / name / ('actions.jsonl' if name in ('twitter', 'reddit') else 'stale.json')).write_text('old')
    second = SimulationRunner.start_simulation('sim-isolated', platform='reddit', graph_id='source-graph')
    assert second.execution_id != first.execution_id
    assert second.execution_graph_id != first.execution_graph_id
    assert len(launch.calls) == 2
    for name in ('twitter_simulation.db', 'reddit_simulation.db', 'reddit_simulation.db-wal', 'env_status.json', 'actions.jsonl',
                 'twitter/actions.jsonl', 'reddit/actions.jsonl', 'ipc_commands/stale.json', 'ipc_responses/stale.json'):
        assert not (launch.folder / name).exists(), name
    assert (launch.folder / 'simulation_config.json').exists()
    archived = json.loads((launch.folder / 'executions' / first.execution_id / 'run_state.json').read_text())
    assert archived['execution_graph_id'] == first.execution_graph_id


def test_cloud_fails_before_claim_or_process_launch(launch, monkeypatch):
    monkeypatch.setattr(Config, 'GRAPH_BACKEND', 'zep')
    with pytest.raises(ValueError, match='local'):
        SimulationRunner.start_simulation('sim-isolated', graph_id='cloud-graph')
    assert launch.processes == []
    assert launch.calls == []
    assert SimulationRunner.get_run_state('sim-isolated') is None


def test_invalid_source_does_not_create_active_run_or_erase_previous_logs(launch):
    (launch.folder / 'reddit_simulation.db').write_text('previous run')
    def fail(**kwargs):
        raise ValueError('Rebuild source graph: previous simulation observations')
    launch.graph.clone_for_execution = fail
    with pytest.raises(ValueError, match='Rebuild'):
        SimulationRunner.start_simulation('sim-isolated', graph_id='contaminated-graph')
    assert SimulationRunner.get_run_state('sim-isolated') is None
    assert launch.processes == []
    assert (launch.folder / 'reddit_simulation.db').read_text() == 'previous run'


def test_prepare_rejects_contaminated_source_even_when_previously_prepared(monkeypatch):
    from flask import Flask
    from app.api import simulation as api
    from app.models.project import ProjectStatus
    from app.services.simulation_runner import SimulationIsolationError
    state = SimpleNamespace(project_id='project-1', graph_id='legacy-graph')
    project = SimpleNamespace(graph_id='legacy-graph', status=ProjectStatus.GRAPH_COMPLETED)
    monkeypatch.setattr(api, 'SimulationManager', lambda: SimpleNamespace(get_simulation=lambda _: state))
    monkeypatch.setattr(api.ProjectManager, 'get_project', lambda _: project)
    monkeypatch.setattr(api, '_check_simulation_prepared', lambda _: (True, {}))
    def reject(_):
        raise SimulationIsolationError('Rebuild the source graph from original documents')
    monkeypatch.setattr(api, 'assert_clean_simulation_source', reject, raising=False)
    with Flask(__name__).test_request_context('/prepare', method='POST', json={'simulation_id': 'sim-clean-check'}):
        response = api.prepare_simulation()
    body, status = response if isinstance(response, tuple) else (response, 200)
    assert status == 409
    assert body.get_json()['error_code'] == 'execution_isolation_unavailable'
    assert 'Rebuild' in body.get_json()['error']


def test_start_passes_source_graph_when_memory_disabled(monkeypatch):
    from flask import Flask
    from app.api import simulation as api
    from app.models.project import ProjectStatus
    from app.services.simulation_manager import SimulationStatus
    from app.services.simulation_runner import SimulationRunState
    state = SimpleNamespace(project_id='project-1', graph_id='source-graph', status=SimulationStatus.READY)
    project = SimpleNamespace(graph_id='source-graph', status=ProjectStatus.GRAPH_COMPLETED)
    monkeypatch.setattr(api, 'SimulationManager', lambda: SimpleNamespace(get_simulation=lambda _: state))
    monkeypatch.setattr(api.ProjectManager, 'get_project', lambda _: project)
    monkeypatch.setattr(api, 'assert_clean_simulation_source', lambda _: None, raising=False)
    launches = []
    def start(**kwargs):
        launches.append(kwargs)
        return SimulationRunState('sim-api', execution_id='exec-api', source_graph_id='source-graph', execution_graph_id='isolated-graph')
    monkeypatch.setattr(api.SimulationRunner, 'start_simulation', start)
    with Flask(__name__).test_request_context('/start', method='POST', json={'simulation_id': 'sim-api'}):
        response = api.start_simulation()
    body, status = response if isinstance(response, tuple) else (response, 200)
    assert status == 200, body.get_json()
    assert launches[0]['graph_id'] == 'source-graph'
    assert body.get_json()['data']['graph_id'] == 'isolated-graph'
    assert body.get_json()['data']['execution_id'] == 'exec-api'


def test_monitor_releases_resources_before_allowing_new_start(launch, monkeypatch):
    from contextlib import contextmanager
    lock_state = {'held': False}
    @contextmanager
    def lock():
        lock_state['held'] = True
        try:
            yield
        finally:
            lock_state['held'] = False
    monkeypatch.setattr(SimulationRunner, '_finalization_lock', classmethod(lambda cls, _: lock()))
    state = SimulationRunner.start_simulation('sim-isolated', graph_id='source-graph')
    state.runner_status = RunnerStatus.RUNNING
    process = SimpleNamespace(poll=lambda: 0, returncode=0)
    SimulationRunner._processes['sim-isolated'] = process
    SimulationRunner._stdout_files['sim-isolated'].close()
    closed = []
    SimulationRunner._stdout_files['sim-isolated'] = SimpleNamespace(close=lambda: closed.append(lock_state['held']))
    SimulationRunner._monitor_simulation('sim-isolated')
    assert closed == [True], 'Old monitor must close handles while it still owns the finalization lock'
    assert 'sim-isolated' not in SimulationRunner._processes


@pytest.mark.parametrize('batch', [False, True])
def test_interview_checks_and_sends_while_execution_lock_is_held(launch, monkeypatch, batch):
    from contextlib import contextmanager
    state = SimulationRunner.start_simulation('sim-isolated', graph_id='source-graph')
    held = {'value': False}
    @contextmanager
    def lock():
        held['value'] = True
        try:
            yield
        finally:
            held['value'] = False
    monkeypatch.setattr(SimulationRunner, '_finalization_lock', classmethod(lambda cls, _: lock()))
    checks = []
    def alive():
        checks.append(held['value'])
        return True
    def send(**kwargs):
        checks.append(held['value'])
        return SimpleNamespace(status=SimpleNamespace(value='completed'), result={}, timestamp='now')
    monkeypatch.setattr(runner_module, 'SimulationIPCClient', lambda _: SimpleNamespace(
        check_env_alive=alive, send_interview=send, send_batch_interview=send,
    ))
    if batch:
        result = SimulationRunner.interview_agents_batch('sim-isolated', [{'agent_id': 0, 'prompt': 'hello'}])
    else:
        result = SimulationRunner.interview_agent('sim-isolated', 0, 'hello')
    assert result['success']
    assert checks == [True, True]
    assert state.execution_id == SimulationRunner.get_run_state('sim-isolated').execution_id


def test_interview_rejects_execution_changed_while_waiting_for_lock(launch, monkeypatch):
    from contextlib import contextmanager
    state = SimulationRunner.start_simulation('sim-isolated', graph_id='source-graph')
    @contextmanager
    def changed_execution():
        state.execution_id = 'exec-replacement'
        yield
    monkeypatch.setattr(SimulationRunner, '_finalization_lock', classmethod(lambda cls, _: changed_execution()))
    with pytest.raises(ValueError, match='execution changed'):
        SimulationRunner.interview_agent('sim-isolated', 0, 'must not reach replacement')


def test_preparation_archives_and_invalidates_old_execution_binding(launch):
    state = SimulationRunner.start_simulation('sim-isolated', graph_id='source-graph')
    state.runner_status = RunnerStatus.COMPLETED
    SimulationRunner._save_run_state(state)
    SimulationRunner.invalidate_prepared_run('sim-isolated')
    assert SimulationRunner.get_run_state('sim-isolated') is None
    assert not (launch.folder / 'run_state.json').exists()
    archived = json.loads((launch.folder / 'executions' / state.execution_id / 'run_state.json').read_text())
    assert archived['execution_graph_id'] == state.execution_graph_id


def test_preparation_cannot_invalidate_active_execution(launch):
    state = SimulationRunner.start_simulation('sim-isolated', graph_id='source-graph')
    with pytest.raises(ValueError, match='active execution'):
        SimulationRunner.invalidate_prepared_run('sim-isolated')
    assert SimulationRunner.get_run_state('sim-isolated').execution_id == state.execution_id


def test_prepare_worker_rejects_report_reader_before_changing_artifacts(launch, monkeypatch):
    from app.api import simulation as api
    from app.models.project import ProjectStatus
    from app.services.simulation_manager import SimulationStatus
    state = SimulationRunner.start_simulation('sim-isolated', graph_id='source-graph')
    state.runner_status = RunnerStatus.COMPLETED
    SimulationRunner._save_run_state(state)
    simulation = SimpleNamespace(project_id='project-1', graph_id='source-graph', status=SimulationStatus.COMPLETED)
    project = SimpleNamespace(graph_id='source-graph', status=ProjectStatus.GRAPH_COMPLETED, simulation_requirement='test')
    failures = []
    monkeypatch.setattr(api, 'SimulationManager', lambda: SimpleNamespace(get_simulation=lambda _: simulation))
    monkeypatch.setattr(api.ProjectManager, 'get_project', lambda _: project)
    monkeypatch.setattr(api, 'get_graph_readers', lambda _: ['report-reader'])
    monkeypatch.setattr(api, 'TaskManager', lambda: SimpleNamespace(fail_task=lambda task, error: failures.append(str(error))))
    api.run_prepare_job('prepare-task', {
        'simulation_id': 'sim-isolated', 'project_id': 'project-1',
        'graph_id': 'source-graph', 'simulation_requirement': 'test',
    })
    assert failures and 'reading this graph' in failures[0]
    assert SimulationRunner.get_run_state('sim-isolated').execution_id == state.execution_id
