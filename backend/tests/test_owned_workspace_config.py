import os
from pathlib import Path
import subprocess
import sys

from app.config import Config


def configured(backend):
    class WorkspaceConfig(Config):
        GRAPH_BACKEND = backend
        ZEP_API_KEY = ''
        LLM_PROVIDER = 'openai_compatible'
        LLM_API_KEY = 'test-key'
        LLM_BASE_URL = 'http://127.0.0.1:11434/v1'
        LLM_MODEL_NAME = 'test-model'
        LLM_BOOST_API_KEY = None
        LLM_BOOST_BASE_URL = None
        LLM_BOOST_MODEL_NAME = None
        MIROFISH_ACCESS_KEY = 'x' * 48
    return WorkspaceConfig


def test_local_memory_needs_no_zep_key(monkeypatch):
    monkeypatch.delenv('ZEP_API_URL', raising=False)
    assert configured('local').validate() == []


def test_zep_remains_explicit_and_requires_a_key(monkeypatch):
    monkeypatch.delenv('ZEP_API_URL', raising=False)
    assert any('ZEP_API_KEY' in error for error in configured('zep').validate())
    assert any('GRAPH_BACKEND' in error for error in configured('other').validate())


def test_simulation_worker_does_not_reload_workspace_secrets():
    env = {name: value for name, value in os.environ.items() if name not in {
        'MIROFISH_ACCESS_KEY', 'SECRET_KEY', 'ZEP_API_KEY', 'LLM_API_KEY', 'OLLAMA_API_KEY',
    }}
    env['MIROFISH_SIMULATION_WORKER'] = '1'
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
    script = "from app.config import Config; assert not Config.MIROFISH_ACCESS_KEY; assert not Config.ZEP_API_KEY; assert not Config.LLM_API_KEY"
    result = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
