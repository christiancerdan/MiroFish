from types import SimpleNamespace

from flask import Flask
import pytest

from app.security import install_security
from app.utils.budget import BudgetStore, BudgetExceeded, current_budget
from app.utils.budget_requests import install_request_budget


@pytest.fixture
def api(tmp_path, monkeypatch):
    from app.services.simulation_manager import SimulationManager
    from app.services.report_agent import ReportManager
    from app.models.project import ProjectManager
    monkeypatch.setattr(SimulationManager, 'get_simulation', lambda self, value: SimpleNamespace(project_id='project', simulation_id=value) if value == 'simulation' else None)
    monkeypatch.setattr(ReportManager, 'get_report', lambda value: SimpleNamespace(simulation_id='simulation') if value == 'report' else None)
    monkeypatch.setattr(ProjectManager, 'get_project', lambda value: SimpleNamespace(project_id=value, graph_id='graph') if value in {'project', 'other-project'} else None)
    monkeypatch.setattr(ProjectManager, 'list_projects', lambda limit=None: [SimpleNamespace(project_id='project', graph_id='graph')])
    app = Flask(__name__)
    app.config.update(TESTING=True, MIROFISH_ACCESS_KEY='owner-' * 8, BUDGET_DB_PATH=str(tmp_path / 'requests.sqlite3'))
    install_security(app)
    install_request_budget(app)
    @app.post('/api/report/chat')
    @app.post('/api/report/tools/search')
    def billed():
        binding = current_budget()
        if app.config.get('DECLINE'):
            try:
                store = BudgetStore(binding.db_path)
                store.reserve(binding.run_id, 'model', 100000000, 10)
            except BudgetExceeded:
                return {'success': True, 'data': 'pretend fallback worked'}
        if app.config.get('CONCURRENT_DECLINE'):
            # This request sees another request's terminal ledger but did not
            # have an admission denial itself.
            store = BudgetStore(binding.db_path)
            with store._transaction() as db:
                db.execute("UPDATE run_budgets SET status='budget_exceeded',reason='calls' WHERE run_id=?", (binding.run_id,))
        return {'success': True, 'run_id': binding.run_id if binding else None}
    @app.post('/api/report/generate/status')
    def status():
        return {'success': True, 'bound': current_budget() is not None}
    return app


def headers(api):
    return {'Authorization': 'Bearer ' + api.config['MIROFISH_ACCESS_KEY']}


@pytest.mark.parametrize('identity', [{'simulation_id': 'simulation'}, {'report_id': 'report'}, {'project_id': 'project'}, {'graph_id': 'graph'}])
def test_request_identity_binds_and_resets(api, identity):
    result = api.test_client().post('/api/report/chat', json=identity, headers=headers(api))
    assert result.json['run_id'] == 'project'
    assert current_budget() is None


def test_denial_is_safe_even_when_endpoint_swallows_it(api):
    api.config['DECLINE'] = True
    result = api.test_client().post('/api/report/chat', json={'simulation_id': 'simulation'}, headers=headers(api))
    assert result.status_code == 429
    assert result.json['code'] == 'budget_exceeded'
    assert result.json['run_id'] == 'project'
    assert current_budget() is None


def test_unrelated_concurrent_exhaustion_does_not_rewrite_response(api):
    api.config['CONCURRENT_DECLINE'] = True
    result = api.test_client().post('/api/report/chat', json={'simulation_id': 'simulation'}, headers=headers(api))
    assert result.status_code == 200
    assert result.json['success'] is True


def test_exhausted_project_does_not_block_status_reads(api):
    with api.app_context():
        store = BudgetStore()
        with pytest.raises(BudgetExceeded):
            store.reserve('project', 'model', 100000000, 1)
    result = api.test_client().post('/api/report/generate/status', json={'project_id': 'project'}, headers=headers(api))
    assert result.status_code == 200
    assert result.json['bound'] is False


def test_budget_cannot_be_bypassed_with_unrelated_project_id(api):
    result = api.test_client().post('/api/report/chat', json={'simulation_id': 'simulation', 'project_id': 'other-project'}, headers=headers(api))
    assert result.status_code == 400


def test_authentication_happens_before_binding(api):
    assert api.test_client().post('/api/report/chat', json={'simulation_id': 'simulation'}).status_code == 401
    assert current_budget() is None
