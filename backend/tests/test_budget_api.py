from flask import Flask
import pytest

from app.api.budget import budget_bp
from app.security import install_security
from app.utils.budget import BudgetStore


@pytest.fixture
def api(tmp_path):
    app = Flask(__name__)
    app.config.update(TESTING=True, MIROFISH_ACCESS_KEY='owner-' * 8, BUDGET_DB_PATH=str(tmp_path / 'api.sqlite3'))
    install_security(app)
    app.register_blueprint(budget_bp, url_prefix='/api/budget')
    return app


def test_budget_routes_require_authentication(api):
    client = api.test_client()
    assert client.get('/api/budget/project').status_code == 401
    assert client.put('/api/budget/project', json={'limits': {'max_calls': 3}}).status_code == 401


def test_budget_route_updates_and_exposes_usage(api):
    client = api.test_client()
    headers = {'Authorization': 'Bearer ' + api.config['MIROFISH_ACCESS_KEY']}
    result = client.put('/api/budget/project', json={'limits': {'max_calls': 3}}, headers=headers)
    assert result.status_code == 200
    assert result.json['data']['limits']['max_calls'] == 3
    with api.app_context():
        store = BudgetStore()
        store.reserve('project', 'model', 10, 5)
    result = client.get('/api/budget/project', headers=headers)
    assert result.json['data']['usage']['calls'] == 1
    assert result.json['data']['usage']['estimated_cost_usd'] is None
    assert 'pricing_json' not in result.json['data']
    result = client.put('/api/budget/project', json={'limits': {'max_calls': 8}}, headers=headers)
    assert result.json['data']['limits']['max_calls'] == 8
    assert result.json['data']['usage']['calls'] == 1


@pytest.mark.parametrize('body', [None, [], {}, {'limits': []}, {'limits': {'max_calls': True}}, {'limits': {'max_calls': 0}}, {'limits': {'arbitrary': 1}}, {'limits': {'max_cost_usd': -1}}])
def test_budget_route_rejects_invalid_settings(api, body):
    response = api.test_client().put('/api/budget/project', json=body, headers={'Authorization': 'Bearer ' + api.config['MIROFISH_ACCESS_KEY']})
    assert response.status_code == 400
