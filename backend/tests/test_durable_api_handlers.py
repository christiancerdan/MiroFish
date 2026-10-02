"""Persist API submissions, then reconstruct their workers in a fresh process."""
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest
from flask import Flask

from app.api import report as report_api
from app.api import simulation as simulation_api
from app.config import Config
from app.models.project import ProjectManager, ProjectStatus
from app.models.task import TaskManager, TaskStatus
from app.services.report_agent import ReportManager
from app.services.simulation_manager import SimulationManager, SimulationStatus
from app.services.simulation_runner import SimulationRunner, SimulationRunState, RunnerStatus


RESTARTED_WORKER = textwrap.dedent('''
    import json, socket, sys, time
    from pathlib import Path
    def forbidden_network(*args, **kwargs):
        raise AssertionError("Network access is forbidden in restart tests")
    socket.create_connection = forbidden_network
    socket.socket.connect = forbidden_network

    from app.api import report as report_api
    from app.config import Config
    from app.models.project import ProjectManager
    from app.models.task import TaskManager, TaskStatus
    from app.services.job_dispatcher import JobDispatcher
    from app.services.report_agent import Report, ReportManager, ReportStatus
    from app.services.simulation_manager import SimulationManager, SimulationStatus
    from app.services.simulation_runner import SimulationRunner
    from app.utils.budget import current_budget
    from app.utils.locale import get_locale
    from app.utils.zep_lifecycle import get_graph_readers

    settings = json.loads(sys.argv[1])
    Config.BUDGET_DB_PATH = settings['budget_db']
    Config.JOBS_DB_PATH = settings['jobs_db']
    Config.OASIS_SIMULATION_DATA_DIR = settings['simulations']
    ProjectManager.PROJECTS_DIR = settings['projects']
    SimulationManager.SIMULATION_DATA_DIR = settings['simulations']
    SimulationRunner.RUN_STATE_DIR = settings['simulations']
    ReportManager.REPORTS_DIR = settings['reports']

    def record_provider_call(kind):
        assert current_budget().run_id == settings['project_id']
        assert get_locale() == 'en'
        with open(settings['calls'], 'a') as output:
            output.write(json.dumps({'kind': kind}) + '\\n')

    def prepare(self, simulation_id, **kwargs):
        readers = get_graph_readers('graph-restart')
        assert len(readers) == 1 and readers[0].startswith(settings['task_id'] + ':')
        assert kwargs['document_text'] == 'document reloaded after enqueue'
        assert kwargs['defined_entity_types'] == ['Person']
        assert kwargs['parallel_profile_count'] == 2
        assert kwargs['simulation_requirement'] == 'Analyze this scenario'
        record_provider_call('prepare')
        kwargs['progress_callback']('reading', 100, 'Read saved document')
        state = self.get_simulation(simulation_id)
        state.status = SimulationStatus.READY
        state.config_generated = True
        self._save_simulation_state(state)
        return state

    class Agent:
        def __init__(self, graph_id, simulation_id, simulation_requirement):
            assert graph_id == 'graph-restart'
            assert simulation_requirement == 'Analyze this scenario'
            self.simulation_id = simulation_id

        def generate_report(self, progress_callback, report_id):
            assert report_id == settings['report_id']
            readers = get_graph_readers('graph-restart')
            assert len(readers) == 1 and readers[0].startswith(report_id + ':')
            record_provider_call('report')
            progress_callback('analysis', 100, 'Finished report')
            return Report(report_id=report_id, simulation_id=self.simulation_id,
                          graph_id='graph-restart', simulation_requirement='Analyze this scenario',
                          status=ReportStatus.COMPLETED, markdown_content='Persisted report')

    SimulationManager.prepare_simulation = prepare
    report_api.ReportAgent = Agent
    manager = TaskManager(db_path=settings['jobs_db'])
    dispatcher = JobDispatcher(manager=manager, workers=1, poll_seconds=0.01)
    dispatcher.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            task = manager.get_task(settings['task_id'])
            if task.status not in {TaskStatus.PENDING, TaskStatus.PROCESSING}:
                break
            time.sleep(0.01)
        assert task.status == TaskStatus.COMPLETED, task.to_dict()
        assert get_graph_readers('graph-restart') == []
    finally:
        dispatcher.stop(wait=True)
''')


@pytest.mark.parametrize('kind', ['prepare', 'report'])
def test_named_handler_survives_process_restart(tmp_path, monkeypatch, kind):
    projects = tmp_path / 'projects'
    simulations = tmp_path / 'simulations'
    reports = tmp_path / 'reports'
    projects.mkdir()
    simulations.mkdir()
    monkeypatch.setattr(ProjectManager, 'PROJECTS_DIR', str(projects))
    monkeypatch.setattr(SimulationManager, 'SIMULATION_DATA_DIR', str(simulations))
    monkeypatch.setattr(SimulationRunner, 'RUN_STATE_DIR', str(simulations))
    monkeypatch.setattr(SimulationRunner, '_run_states', {})
    monkeypatch.setattr(ReportManager, 'REPORTS_DIR', str(reports))
    monkeypatch.setattr(Config, 'OASIS_SIMULATION_DATA_DIR', str(simulations))
    monkeypatch.setattr(simulation_api, 'ZepEntityReader', lambda: SimpleNamespace(
        filter_defined_entities=lambda **_kwargs: SimpleNamespace(filtered_count=1, entity_types=['Person']),
    ))
    project = ProjectManager.create_project('Restart test')
    project.graph_id = 'graph-restart'
    project.status = ProjectStatus.GRAPH_COMPLETED
    project.simulation_requirement = 'Analyze this scenario'
    ProjectManager.save_project(project)
    ProjectManager.save_extracted_text(project.project_id, 'original document')
    state = SimulationManager().create_simulation(project.project_id, project.graph_id)
    if kind == 'report':
        SimulationRunner._save_run_state(SimulationRunState(
            state.simulation_id, runner_status=RunnerStatus.COMPLETED,
            started_at='2026-01-01T00:00:00', completed_at='2026-01-01T00:01:00',
        ))
    jobs_path = str(tmp_path / 'jobs.sqlite3')
    app = Flask(__name__)
    app.config['JOBS_DB_PATH'] = jobs_path
    payload = {'simulation_id': state.simulation_id}
    if kind == 'prepare':
        payload.update(entity_types=['Person'], parallel_profile_count=2)
    route = simulation_api.prepare_simulation if kind == 'prepare' else report_api.generate_report
    # Locale is captured explicitly; worker has no request context to inherit.
    monkeypatch.setattr(simulation_api if kind == 'prepare' else report_api, 'get_locale', lambda: 'en')
    with app.test_request_context('/enqueue', method='POST', json=payload):
        response = route()
        assert not isinstance(response, tuple), response
        data = response.get_json()['data']
    task_id = data['task_id']
    assert TaskManager(db_path=jobs_path).get_task(task_id).status == TaskStatus.PENDING
    ProjectManager.save_extracted_text(project.project_id, 'document reloaded after enqueue')
    settings = {
        'jobs_db': jobs_path, 'budget_db': str(tmp_path / 'budgets.sqlite3'),
        'projects': str(projects), 'simulations': str(simulations), 'reports': str(reports),
        'project_id': project.project_id, 'task_id': task_id,
        'report_id': data.get('report_id'), 'calls': str(tmp_path / 'provider_calls.jsonl'),
    }
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    # The second restart must retain completion and execute zero extra provider calls.
    for _ in range(2):
        completed = subprocess.run([sys.executable, '-c', RESTARTED_WORKER, json.dumps(settings)],
                                   capture_output=True, text=True, env=env, timeout=15)
        assert completed.returncode == 0, completed.stdout + completed.stderr
    task = TaskManager(db_path=jobs_path).get_task(task_id)
    assert task.status == TaskStatus.COMPLETED
    assert task.attempts == 1
    assert Path(settings['calls']).read_text().splitlines() == [json.dumps({'kind': kind})]
    if kind == 'prepare':
        assert SimulationManager().get_simulation(state.simulation_id).status == SimulationStatus.READY
    else:
        assert task.result['report_id'] == data['report_id']
        assert ReportManager.get_report(data['report_id']).markdown_content == 'Persisted report'
