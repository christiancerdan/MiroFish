"""Budget identities for synchronous billed API operations, after authentication."""
from flask import g, request

from ..security import public_error
from .budget import BudgetContext, BudgetExceeded, observe_budget_failures
from .storage import validate_storage_id

# Reads, job submission/status, cleanup, and budget changes never acquire a
# context here. Durable workers bind their own project context at execution.
_BILLED_PATHS = {
    '/api/report/chat', '/api/report/tools/search',
    '/api/simulation/generate-profiles', '/api/simulation/interview',
    '/api/simulation/interview/batch', '/api/simulation/interview/all',
}


def _project_for_request(data):
    from ..models.project import ProjectManager
    from ..services.simulation_manager import SimulationManager
    from ..services.report_agent import ReportManager

    identifiers = {}
    for source in (request.view_args or {}, request.args, data):
        for key in ('project_id', 'simulation_id', 'report_id', 'graph_id'):
            value = source.get(key)
            if value:
                identifiers[key] = validate_storage_id(value, key)
    projects = set()
    if identifiers.get('project_id'):
        project = ProjectManager.get_project(identifiers['project_id'])
        if project:
            projects.add(project.project_id)
        else:
            raise LookupError
    simulation_id = identifiers.get('simulation_id')
    if identifiers.get('report_id'):
        report = ReportManager.get_report(identifiers['report_id'])
        if not report or simulation_id and report.simulation_id != simulation_id:
            raise LookupError
        simulation_id = report.simulation_id
    if simulation_id:
        state = SimulationManager().get_simulation(simulation_id)
        if state is None:
            raise LookupError
        projects.add(validate_storage_id(state.project_id, 'project_id'))
    if identifiers.get('graph_id'):
        graph_projects = {project.project_id for project in ProjectManager.list_projects(limit=None) if project.graph_id == identifiers['graph_id']}
        if not graph_projects:
            raise LookupError
        if projects:
            if not projects.issubset(graph_projects):
                raise ValueError('The requested records belong to different projects')
        elif len(graph_projects) == 1:
            projects.update(graph_projects)
        else:
            raise ValueError('A project_id is required for a shared graph')
    if len(projects) > 1:
        raise ValueError('The requested records belong to different projects')
    return next(iter(projects), None)


def _budget_response(run_id):
    return public_error({'success': False, 'error': 'Run budget exceeded; increase its limits before retrying.',
                         'code': 'budget_exceeded', 'run_id': run_id}, 429)


def install_request_budget(app):
    @app.before_request
    def bind_request_budget():
        if request.method != 'POST' or request.path not in _BILLED_PATHS:
            return None
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return None  # Global security/route validation handles malformed input.
        if request.path == '/api/simulation/generate-profiles' and data.get('use_llm') is False:
            return None
        try:
            run_id = _project_for_request(data)
        except LookupError:
            return public_error({'success': False, 'error': 'Project for this model request was not found'}, 404)
        except ValueError as error:
            return public_error({'success': False, 'error': str(error)}, 400)
        if run_id is None:
            return None  # Required identifiers are validated by the endpoint.
        g.budget_run_id = run_id
        observation = observe_budget_failures()
        g.budget_failure_observation = observation
        g.budget_failure = observation.__enter__()
        context = BudgetContext(run_id)
        try:
            context.__enter__()
        except BudgetExceeded:
            return _budget_response(run_id)
        g.budget_context = context

    @app.after_request
    def show_budget_failure(response):
        # A caught exception/fallback cannot disguise the request's own denial.
        # Concurrent exhaustion on another request never changes this response.
        if getattr(g, 'budget_failure', {}).get('exceeded'):
            return _budget_response(g.budget_run_id)
        return response

    @app.teardown_request
    def release_request_budget(error):
        context = g.pop('budget_context', None)
        if context is not None:
            context.__exit__(None, None, None)
        observation = g.pop('budget_failure_observation', None)
        if observation is not None:
            observation.__exit__(None, None, None)
