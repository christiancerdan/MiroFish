"""Narrow simulation process boundary (resource isolation, not an OS sandbox)."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
from typing import Mapping

from ..utils.llm_provider import resolve_llm_settings


# Never forward the complete API environment. In particular, Zep, workspace,
# cloud-storage, SSH, Python injection, and model-hub credentials stay outside.
_PROVIDER_KEYS = (
    'LLM_PROVIDER', 'LLM_API_KEY', 'LLM_BASE_URL', 'LLM_MODEL_NAME', 'LLM_TOKEN_LIMIT',
    'LLM_BOOST_PROVIDER', 'LLM_BOOST_API_KEY', 'LLM_BOOST_BASE_URL',
    'LLM_BOOST_MODEL_NAME', 'LLM_BOOST_TOKEN_LIMIT',
)
_BUDGET_KEYS = ('MIROFISH_BUDGET_RUN_ID', 'MIROFISH_BUDGET_DB_PATH')


def build_worker_environment(
    simulation_dir: str | Path, *, source: Mapping[str, str] | None = None,
    budget: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if source is None else source
    runtime = Path(tempfile.mkdtemp(prefix='.worker-', dir=simulation_dir)).resolve()
    runtime.chmod(0o700)
    home, temporary, cache = (runtime / part for part in ('home', 'tmp', 'cache'))
    for directory in (home, temporary, cache, cache / 'huggingface'):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    env = {key: str(source[key]) for key in _PROVIDER_KEYS if source.get(key)}
    # Resolve the selected credential once, including Ollama's fallback key;
    # don't forward an unused second provider credential.
    selected = resolve_llm_settings(source, validate=False)
    if selected.api_key:
        env['LLM_API_KEY'] = selected.api_key
    for key in ('SYSTEMROOT', 'WINDIR', 'COMSPEC'):
        if source.get(key):
            env[key] = str(source[key])
    env.update({
        'PATH': os.defpath,
        'HOME': str(home), 'USERPROFILE': str(home),
        'TMPDIR': str(temporary), 'TMP': str(temporary), 'TEMP': str(temporary),
        'XDG_CACHE_HOME': str(cache), 'HF_HOME': str(cache / 'huggingface'),
        'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8',
        'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'MIROFISH_SIMULATION_WORKER': '1', 'MIROFISH_WORKER_RUNTIME_DIR': str(runtime),
        'CAMEL_MODEL_LOG_ENABLED': 'false', 'TOKENIZERS_PARALLELISM': 'false',
        'OMP_NUM_THREADS': '2', 'OPENBLAS_NUM_THREADS': '2', 'MKL_NUM_THREADS': '2',
    })
    env.update({key: str(budget[key]) for key in _BUDGET_KEYS if budget and budget.get(key)})
    return env


def worker_command(script: str, arguments: list[str], env: Mapping[str, str], config) -> list[str]:
    """Bootstrap in the selected interpreter; never use a shell or preexec_fn."""
    python = getattr(config, 'SIMULATION_PYTHON', None) or sys.executable
    bootstrap = Path(__file__).resolve().parents[2] / 'scripts' / 'simulation_worker.py'
    return [
        str(python), '-I', str(bootstrap), '--script', str(Path(script).resolve()),
        '--runtime-dir', env['MIROFISH_WORKER_RUNTIME_DIR'], '--parent-pid', str(os.getpid()),
        '--wall-seconds', str(getattr(config, 'SIMULATION_MAX_WALL_SECONDS', 3600)),
        '--cpu-seconds', str(getattr(config, 'SIMULATION_MAX_CPU_SECONDS', 1800)),
        '--memory-mb', str(getattr(config, 'SIMULATION_MAX_MEMORY_MB', 8192)),
        '--', *arguments,
    ]
