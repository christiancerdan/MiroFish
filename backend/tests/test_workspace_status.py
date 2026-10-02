from app import create_app
from app.config import Config


class StatusConfig(Config):
    TESTING = True
    MIROFISH_ACCESS_KEY = 'x' * 48
    GRAPH_BACKEND = 'local'
    ZEP_API_KEY = ''
    LLM_PROVIDER = 'openai_compatible'
    LLM_API_KEY = 'private-provider-sentinel'
    LLM_BASE_URL = 'http://127.0.0.1:11434/v1'
    LLM_MODEL_NAME = 'gpt-oss:20b-cloud'


def test_workspace_status_distinguishes_local_storage_from_cloud_inference():
    client = create_app(StatusConfig).test_client()
    assert client.get('/api/system/status').status_code == 401
    response = client.get('/api/system/status', headers={'Authorization': 'Bearer ' + StatusConfig.MIROFISH_ACCESS_KEY})
    assert response.status_code == 200
    data = response.json['data']
    assert data['memory'] == {'backend': 'local', 'location': 'this computer', 'requires_zep_key': False}
    assert data['llm']['execution'] == 'cloud'
    assert data['llm']['model'] == 'gpt-oss:20b-cloud'
    assert 'private-provider-sentinel' not in response.get_data(as_text=True)


def test_local_model_status_is_explicit():
    class LocalConfig(StatusConfig):
        LLM_MODEL_NAME = 'local-model:8b'
    client = create_app(LocalConfig).test_client()
    response = client.get('/api/system/status', headers={'Authorization': 'Bearer ' + StatusConfig.MIROFISH_ACCESS_KEY})
    assert response.json['data']['llm']['execution'] == 'local'
