"""Process-boundary tests use real children; no provider or model downloads."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.services.simulation_runtime import build_worker_environment


BOOTSTRAP = Path(__file__).parents[1] / 'scripts' / 'simulation_worker.py'


def test_worker_environment_does_not_inherit_server_or_machine_secrets(tmp_path):
    source = {
        'SECRET_KEY': 'server-secret', 'MIROFISH_ACCESS_KEY': 'workspace-key',
        'ZEP_API_KEY': 'graph-key', 'AWS_SECRET_ACCESS_KEY': 'aws-key',
        'HF_TOKEN': 'model-key', 'PYTHONPATH': '/untrusted/code',
        'LD_PRELOAD': '/untrusted/library', 'LLM_API_KEY': 'provider-key',
        'LLM_MODEL_NAME': 'configured-model', 'LLM_BASE_URL': 'http://127.0.0.1:11434/v1',
        'LLM_BOOST_API_KEY': 'boost-key', 'OLLAMA_API_KEY': 'cloud-key',
    }
    env = build_worker_environment(tmp_path, source=source, budget={
        'MIROFISH_BUDGET_RUN_ID': 'project-1', 'MIROFISH_BUDGET_DB_PATH': '/budget.sqlite3',
    })
    assert env['LLM_API_KEY'] == 'provider-key'
    assert env['LLM_BOOST_API_KEY'] == 'boost-key'
    assert 'OLLAMA_API_KEY' not in env  # This run already selects another provider key.
    assert env['MIROFISH_BUDGET_RUN_ID'] == 'project-1'
    assert env['MIROFISH_SIMULATION_WORKER'] == '1'
    for name in ('SECRET_KEY', 'MIROFISH_ACCESS_KEY', 'ZEP_API_KEY', 'AWS_SECRET_ACCESS_KEY', 'HF_TOKEN', 'PYTHONPATH', 'LD_PRELOAD'):
        assert name not in env
    for name in ('HOME', 'TMPDIR', 'HF_HOME', 'XDG_CACHE_HOME'):
        assert Path(env[name]).is_relative_to(tmp_path)
        assert Path(env[name]).is_dir()
    other = build_worker_environment(tmp_path, source={})
    assert env['HOME'] != other['HOME']


def test_dotenv_cannot_reintroduce_server_secrets_in_worker(tmp_path):
    backend = str(Path(__file__).parents[1])
    env = build_worker_environment(tmp_path, source={})
    script = "import sys;sys.path.insert(0,sys.argv[1]);import app.config;import os;print('SECRET_KEY' in os.environ, 'ZEP_API_KEY' in os.environ)"
    result = subprocess.run([sys.executable, '-I', '-c', script, backend], env=env, cwd=tmp_path, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == 'False False'


def test_worker_bootstrap_enforces_deadline_and_records_safe_failure(tmp_path):
    target = tmp_path / 'wait_forever.py'
    target.write_text('import time\ntime.sleep(30)\n')
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    result = subprocess.run([
        sys.executable, '-I', str(BOOTSTRAP), '--script', str(target),
        '--runtime-dir', str(runtime), '--parent-pid', str(os.getpid()),
        '--wall-seconds', '0.2', '--cpu-seconds', '10', '--memory-mb', '512',
    ], cwd=tmp_path, capture_output=True, text=True, start_new_session=True, timeout=8)
    assert result.returncode != 0
    failure = json.loads((runtime / 'failure.json').read_text())
    assert failure['code'] == 'simulation_timeout'
    limits = json.loads((runtime / 'isolation.json').read_text())
    assert limits['wall_seconds'] == 0.2
    assert limits['memory_limit_enforced'] == sys.platform.startswith('linux')


def test_worker_bootstrap_applies_cpu_limit_before_target_import(tmp_path):
    if os.name != 'posix':
        pytest.skip('POSIX resource limits')
    target = tmp_path / 'read_limits.py'
    target.write_text('import resource,json\nprint(json.dumps(resource.getrlimit(resource.RLIMIT_CPU)))\n')
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    result = subprocess.run([
        sys.executable, '-I', str(BOOTSTRAP), '--script', str(target),
        '--runtime-dir', str(runtime), '--parent-pid', str(os.getpid()),
        '--wall-seconds', '8', '--cpu-seconds', '7', '--memory-mb', '512',
    ], capture_output=True, text=True, start_new_session=True, check=True)
    assert json.loads(result.stdout) == [7, 7]


def test_restart_marks_saved_active_simulation_interrupted_without_signaling_pid(tmp_path, monkeypatch):
    from app.services.simulation_runner import SimulationRunner, SimulationRunState, RunnerStatus
    monkeypatch.setattr(SimulationRunner, 'RUN_STATE_DIR', str(tmp_path))
    monkeypatch.setattr(SimulationRunner, '_run_states', {})
    monkeypatch.setattr(SimulationRunner, '_processes', {})
    monkeypatch.setattr(SimulationRunner, '_sync_simulation_status', classmethod(lambda cls, *args: None))
    state = SimulationRunState('interrupted-run', runner_status=RunnerStatus.RUNNING, process_pid=os.getpid(), twitter_running=True)
    SimulationRunner._save_run_state(state)
    SimulationRunner._run_states.clear()
    recovered = SimulationRunner.get_run_state('interrupted-run')
    assert recovered.runner_status == RunnerStatus.FAILED
    assert recovered.error_code == 'interrupted'
    assert recovered.process_pid is None
    assert recovered.twitter_running is False
    assert json.loads((tmp_path / 'interrupted-run' / 'run_state.json').read_text())['error_code'] == 'interrupted'


@pytest.mark.parametrize('platform,expected_flag', [('twitter', '--twitter-only'), ('reddit', '--reddit-only'), ('parallel', None)])
def test_runner_launch_uses_filtered_environment_and_selected_interpreter(tmp_path, monkeypatch, platform, expected_flag):
    from app.config import Config
    from app.services.simulation_runner import SimulationRunner
    import app.services.simulation_runner as runner_module
    runs, scripts = tmp_path / 'runs', tmp_path / 'scripts'
    directory = runs / 'launch-run'
    directory.mkdir(parents=True)
    scripts.mkdir()
    (scripts / 'run_parallel_simulation.py').write_text('pass\n')
    (directory / 'simulation_config.json').write_text(json.dumps({'time_config': {'total_simulation_hours': 1, 'minutes_per_round': 60}}))
    monkeypatch.setattr(SimulationRunner, 'RUN_STATE_DIR', str(runs))
    monkeypatch.setattr(SimulationRunner, 'SCRIPTS_DIR', str(scripts))
    for name in ('_run_states', '_processes', '_monitor_threads', '_stdout_files', '_stderr_files', '_action_queues', '_graph_memory_enabled'):
        monkeypatch.setattr(SimulationRunner, name, {})
    monkeypatch.setattr(SimulationRunner, '_sync_simulation_status', classmethod(lambda cls, *args: None))
    monkeypatch.setattr(Config, 'SIMULATION_PYTHON', '/selected/simulation/python')
    monkeypatch.setenv('MIROFISH_ACCESS_KEY', 'server-secret')
    monkeypatch.setenv('LLM_API_KEY', 'model-key')
    captured = {}

    class Process:
        pid = 12345
        def poll(self):
            return 0

    class Thread:
        def __init__(self, **kwargs):
            pass
        def start(self):
            pass

    def popen(command, **kwargs):
        captured.update(command=command, **kwargs)
        return Process()

    monkeypatch.setattr(runner_module.subprocess, 'Popen', popen)
    monkeypatch.setattr(runner_module.threading, 'Thread', Thread)
    SimulationRunner.start_simulation('launch-run', platform=platform, max_rounds=1)
    assert captured['command'][0:2] == ['/selected/simulation/python', '-I']
    assert Path(captured['command'][2]).name == 'simulation_worker.py'
    if expected_flag:
        assert expected_flag in captured['command']
    else:
        assert '--twitter-only' not in captured['command'] and '--reddit-only' not in captured['command']
    assert 'run_parallel_simulation.py' in captured['command'][captured['command'].index('--script') + 1]
    assert captured['start_new_session'] is True
    assert captured['env']['LLM_API_KEY'] == 'model-key'
    assert 'MIROFISH_ACCESS_KEY' not in captured['env']
    assert captured['command'][-2:] == ['--max-rounds', '1']
    for stream in SimulationRunner._stdout_files.values():
        stream.close()


def test_monitor_stops_child_when_shared_budget_is_exceeded(tmp_path, monkeypatch):
    from app.services.simulation_runner import SimulationRunner, SimulationRunState, RunnerStatus
    from app.utils.budget import BudgetExceeded, BudgetStore
    monkeypatch.setattr(SimulationRunner, 'RUN_STATE_DIR', str(tmp_path))
    monkeypatch.setattr(SimulationRunner, '_run_states', {})
    monkeypatch.setattr(SimulationRunner, '_processes', {})
    monkeypatch.setattr(SimulationRunner, '_sync_simulation_status', classmethod(lambda cls, *args: None))
    state = SimulationRunState('budget-run', runner_status=RunnerStatus.RUNNING, budget_run_id='project-1')
    SimulationRunner._save_run_state(state)
    stopped = []

    class Process:
        def poll(self):
            return None

    def check(self, run_id):
        raise BudgetExceeded('request_limit')

    monkeypatch.setattr(BudgetStore, 'check', check)
    monkeypatch.setattr(SimulationRunner, '_terminate_process', classmethod(lambda cls, *args: stopped.append(True)))
    SimulationRunner._processes['budget-run'] = Process()
    SimulationRunner._monitor_simulation('budget-run')
    assert stopped == [True]
    assert state.runner_status == RunnerStatus.FAILED
    assert state.error_code == 'budget_exceeded'


@pytest.mark.parametrize('batch', [False, True])
def test_interview_cannot_report_success_after_child_exhausts_budget(tmp_path, monkeypatch, batch):
    from types import SimpleNamespace
    from app.services.simulation_runner import SimulationRunner, SimulationRunState, RunnerStatus
    import app.services.simulation_runner as runner_module
    from app.utils.budget import BudgetExceeded, BudgetStore
    (tmp_path / 'interview-run').mkdir()
    monkeypatch.setattr(SimulationRunner, 'RUN_STATE_DIR', str(tmp_path))
    monkeypatch.setattr(SimulationRunner, '_run_states', {
        'interview-run': SimulationRunState('interview-run', runner_status=RunnerStatus.RUNNING, budget_run_id='project-1'),
    })
    checks = []
    def check(self, run_id):
        checks.append(run_id)
        if len(checks) > 1:
            raise BudgetExceeded('request_limit')
    monkeypatch.setattr(BudgetStore, 'check', check)
    response = SimpleNamespace(status=SimpleNamespace(value='completed'), result={}, timestamp='now')
    monkeypatch.setattr(runner_module, 'SimulationIPCClient', lambda directory: SimpleNamespace(
        check_env_alive=lambda: True,
        send_interview=lambda **kwargs: response,
        send_batch_interview=lambda **kwargs: response,
    ))
    with pytest.raises(BudgetExceeded):
        if batch:
            SimulationRunner.interview_agents_batch('interview-run', [{'agent_id': 0, 'prompt': 'hello'}])
        else:
            SimulationRunner.interview_agent('interview-run', 0, 'hello')
    assert checks == ['project-1', 'project-1']


def test_worker_watchdog_stops_orphan_when_api_parent_disappears(tmp_path):
    import time
    if os.name != 'posix':
        pytest.skip('POSIX process-group watchdog')
    target = tmp_path / 'long_work.py'
    target.write_text('import time\ntime.sleep(30)\n')
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    parent = '''
import os, pathlib, subprocess, sys, time
runtime = pathlib.Path(sys.argv[1])
subprocess.Popen([sys.executable, '-I', sys.argv[3], '--script', sys.argv[2],
    '--runtime-dir', str(runtime), '--parent-pid', str(os.getpid()),
    '--wall-seconds', '10', '--cpu-seconds', '5', '--memory-mb', '512'],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
for attempt in range(100):
    if (runtime / 'isolation.json').exists():
        break
    time.sleep(.02)
else:
    raise RuntimeError('worker did not initialize')
'''
    subprocess.run([sys.executable, '-c', parent, str(runtime), str(target), str(BOOTSTRAP)], check=True, timeout=5)
    for attempt in range(100):
        if (runtime / 'failure.json').exists():
            break
        time.sleep(.02)
    assert json.loads((runtime / 'failure.json').read_text())['code'] == 'interrupted'


def test_worker_resolves_only_selected_ollama_fallback_key(tmp_path):
    env = build_worker_environment(tmp_path, source={
        'LLM_PROVIDER': 'ollama_cloud', 'LLM_API_KEY': 'your_api_key_here',
        'OLLAMA_API_KEY': 'selected-cloud-key', 'LLM_MODEL_NAME': 'configured-cloud-model',
    })
    assert env['LLM_API_KEY'] == 'selected-cloud-key'
    assert 'OLLAMA_API_KEY' not in env


def test_simulation_storage_follows_configured_workspace_data_directory(tmp_path):
    backend = str(Path(__file__).parents[1])
    code = """
import sys,json
sys.path.insert(0,sys.argv[1])
from app.config import Config
from app.services.simulation_manager import SimulationManager
from app.services.simulation_runner import SimulationRunner
print(json.dumps([Config.OASIS_SIMULATION_DATA_DIR,SimulationManager.SIMULATION_DATA_DIR,SimulationRunner.RUN_STATE_DIR]))
"""
    result = subprocess.run([sys.executable, '-I', '-c', code, backend], env={
        'MIROFISH_SIMULATION_WORKER': '1', 'MIROFISH_DATA_DIR': str(tmp_path),
    }, check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == [str(tmp_path / 'simulations')] * 3
