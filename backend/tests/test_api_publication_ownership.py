"""Stale API workers cannot replace artifacts published by a retry."""
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from app.api import report as report_api
from app.api import simulation as simulation_api
from app.config import Config
from app.models.project import ProjectManager, ProjectStatus
from app.models.task import JobCancelled, JobLeaseLost, TaskManager, TaskStatus
from app.services.report_agent import Report, ReportManager, ReportStatus
from app.services.simulation_manager import SimulationManager, SimulationStatus
from app.services.simulation_runner import SimulationRunner, SimulationRunState, RunnerStatus
from app.utils.zep_lifecycle import get_graph_readers


@pytest.fixture
def job(tmp_path, monkeypatch):
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
    project = ProjectManager.create_project('Publication ownership')
    project.graph_id = 'graph-owned'
    project.status = ProjectStatus.GRAPH_COMPLETED
    project.simulation_requirement = 'Analyze the saved run'
    ProjectManager.save_project(project)
    ProjectManager.save_extracted_text(project.project_id, 'saved document')
    state = SimulationManager().create_simulation(project.project_id, project.graph_id)
    SimulationRunner._save_run_state(SimulationRunState(
        state.simulation_id, runner_status=RunnerStatus.COMPLETED,
        started_at='2026-01-01T00:00:00', completed_at='2026-01-01T00:01:00',
    ))
    parameters = {
        'project_id': project.project_id, 'simulation_id': state.simulation_id,
        'graph_id': project.graph_id, 'simulation_requirement': project.simulation_requirement,
        'run_started_at': '2026-01-01T00:00:00', 'run_completed_at': '2026-01-01T00:01:00',
        'locale': 'en',
    }
    manager = TaskManager(db_path=tmp_path / 'jobs.sqlite3')
    return SimpleNamespace(
        manager=manager, project=project, simulation=state, parameters=parameters,
        reports=reports, report_id='report_owned',
    )


def _claim(job, kind):
    task_id = job.manager.enqueue(kind, kind, job.parameters, metadata={'report_id': job.report_id})
    assert job.manager.claim_next('old-owner').task_id == task_id
    return task_id


def _replace(job, task_id):
    with sqlite3.connect(job.manager.db_path) as db:
        db.execute('UPDATE tasks SET lease_until=0 WHERE task_id=?', (task_id,))
    replacement = TaskManager(db_path=job.manager.db_path)
    assert replacement.recover_expired() == 1
    replacement.retry_task(task_id, 'replace-once', acknowledge_effects=True)
    assert replacement.claim_next('new-owner').task_id == task_id
    return replacement


def _report(job, content):
    return Report(
        report_id=job.report_id, simulation_id=job.simulation.simulation_id,
        graph_id=job.project.graph_id, simulation_requirement=job.project.simulation_requirement,
        status=ReportStatus.COMPLETED, markdown_content=content,
    )


def _report_files(job):
    return {path.name: path.read_bytes() for path in (job.reports / job.report_id).iterdir() if path.is_file()}


@pytest.mark.parametrize('outcome', ['return', 'raise'])
def test_stale_report_api_preserves_replacement_artifact(job, monkeypatch, outcome):
    task_id = _claim(job, 'report_generate')
    replacement_files = {}

    class Agent:
        def __init__(self, **_kwargs):
            pass

        def generate_report(self, **_kwargs):
            replacement = _replace(job, task_id)
            with replacement.execution(task_id, 'new-owner'):
                with replacement.publication_guard():
                    ReportManager.save_report(_report(job, 'Replacement report'))
                replacement.complete_task(task_id, {'owner': 'replacement'})
            replacement_files.update(_report_files(job))
            if outcome == 'raise':
                raise RuntimeError('old provider failed after replacement finished')
            return _report(job, 'Stale report')

    monkeypatch.setattr(report_api, 'ReportAgent', Agent)
    with job.manager.execution(task_id, 'old-owner'), pytest.raises(JobLeaseLost):
        report_api.run_report_job(task_id, job.parameters)
    assert _report_files(job) == replacement_files
    assert job.manager.get_task(task_id).result == {'owner': 'replacement'}
    assert job.manager.get_task(task_id).status == TaskStatus.COMPLETED
    assert get_graph_readers(job.project.graph_id) == []


@pytest.mark.parametrize('outcome', ['return', 'raise'])
def test_stale_prepare_api_preserves_replacement_state(job, monkeypatch, outcome):
    task_id = _claim(job, 'simulation_prepare')
    replacement_state = {}

    def prepare(self, simulation_id, **_kwargs):
        replacement = _replace(job, task_id)
        with replacement.execution(task_id, 'new-owner'):
            with replacement.publication_guard():
                state = self.get_simulation(simulation_id)
                state.status = SimulationStatus.READY
                state.error = 'Replacement state sentinel'
                self._save_simulation_state(state)
            replacement.complete_task(task_id, {'owner': 'replacement'})
        replacement_state.update(state.to_dict())
        if outcome == 'raise':
            raise RuntimeError('old provider failed after replacement finished')
        state.status = SimulationStatus.READY
        return state

    monkeypatch.setattr(SimulationManager, 'prepare_simulation', prepare)
    with job.manager.execution(task_id, 'old-owner'), pytest.raises(JobLeaseLost):
        simulation_api.run_prepare_job(task_id, job.parameters)
    assert SimulationManager().get_simulation(job.simulation.simulation_id).to_dict() == replacement_state
    assert job.manager.get_task(task_id).result == {'owner': 'replacement'}
    assert job.manager.get_task(task_id).status == TaskStatus.COMPLETED
    assert get_graph_readers(job.project.graph_id) == []


def test_stale_report_cleanup_preserves_replacement_reader_lease(job, monkeypatch):
    task_id = _claim(job, 'report_generate')
    replacement_started = threading.Event()
    finish_replacement = threading.Event()
    threads = []
    errors = []
    calls = []
    replacement_readers = []

    class Agent:
        def __init__(self, **_kwargs):
            pass

        def generate_report(self, **_kwargs):
            calls.append(True)
            if len(calls) == 1:
                old_readers = set(get_graph_readers(job.project.graph_id))
                replacement = _replace(job, task_id)
                def run_replacement():
                    try:
                        with replacement.execution(task_id, 'new-owner'):
                            report_api.run_report_job(task_id, job.parameters)
                    except BaseException as error:
                        errors.append(error)
                thread = threading.Thread(target=run_replacement)
                threads.append(thread)
                thread.start()
                assert replacement_started.wait(5)
                replacement_readers.extend(set(get_graph_readers(job.project.graph_id)) - old_readers)
                assert len(replacement_readers) == 1
                return _report(job, 'Stale report')
            replacement_started.set()
            assert finish_replacement.wait(5)
            return _report(job, 'Replacement report')

    monkeypatch.setattr(report_api, 'ReportAgent', Agent)
    try:
        with job.manager.execution(task_id, 'old-owner'), pytest.raises(JobLeaseLost):
            report_api.run_report_job(task_id, job.parameters)
        assert get_graph_readers(job.project.graph_id) == replacement_readers
    finally:
        finish_replacement.set()
        for thread in threads:
            thread.join(timeout=5)
            assert not thread.is_alive()
    assert errors == []
    assert get_graph_readers(job.project.graph_id) == []
    assert ReportManager.get_report(job.report_id).markdown_content == 'Replacement report'
    assert job.manager.get_task(task_id).status == TaskStatus.COMPLETED


@pytest.mark.parametrize('kind', ['prepare', 'report'])
def test_api_handler_propagates_job_cancellation_before_publication(job, monkeypatch, kind):
    task_id = _claim(job, kind)
    state_before = SimulationManager().get_simulation(job.simulation.simulation_id).to_dict()
    error = JobCancelled('cancel this attempt')
    def fail_provider(*_args, **_kwargs):
        raise error
    if kind == 'prepare':
        monkeypatch.setattr(SimulationManager, 'prepare_simulation', fail_provider)
        handler = simulation_api.run_prepare_job
    else:
        monkeypatch.setattr(report_api, 'ReportAgent', fail_provider)
        handler = report_api.run_report_job
    with job.manager.execution(task_id, 'old-owner'), pytest.raises(JobCancelled) as raised:
        handler(task_id, job.parameters)
    assert raised.value is error
    assert SimulationManager().get_simulation(job.simulation.simulation_id).to_dict() == state_before
    assert not (job.reports / job.report_id).exists()
    assert job.manager.get_task(task_id).status == TaskStatus.PROCESSING
    assert get_graph_readers(job.project.graph_id) == []
