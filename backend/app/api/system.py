"""Authenticated workspace status; never return configured credentials."""
from urllib.parse import urlsplit

from flask import Blueprint, current_app

from ..utils.llm_provider import resolve_llm_settings

system_bp = Blueprint('system', __name__)


@system_bp.get('/status')
def workspace_status():
    config = current_app.config
    backend = config.get('GRAPH_BACKEND', 'local')
    llm = {'configured': False, 'provider': config.get('LLM_PROVIDER', 'openai'),
           'model': config.get('LLM_MODEL_NAME'), 'execution': 'unknown'}
    try:
        settings = resolve_llm_settings(config)
        host = urlsplit(settings.base_url).hostname
        if settings.provider == 'ollama_cloud' or '-cloud' in settings.model or ':cloud' in settings.model:
            execution = 'cloud'
        elif host in {'localhost', '127.0.0.1', '::1'}:
            execution = 'local'
        else:
            execution = 'remote'
        llm.update(configured=True, model=settings.model, provider=settings.provider, execution=execution)
    except ValueError:
        pass
    return {'success': True, 'data': {
        'memory': {'backend': backend,
                   'location': 'this computer' if backend == 'local' else 'Zep Cloud',
                   'requires_zep_key': backend == 'zep'},
        'llm': llm,
        'jobs': {'storage': 'sqlite', 'queued_recovery': 'automatic', 'interrupted_recovery': 'explicit_retry'},
        'budgets': {'scope': 'project', 'monetary_cost_requires_configured_prices': True},
        'privacy': 'Local graph storage does not make a cloud model private or offline.',
    }}
