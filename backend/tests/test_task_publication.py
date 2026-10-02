"""A replaced worker must not publish stale domain files or queued calls."""
import copy
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest

from app.models.task import JobCancelled, JobLeaseLost, TaskManager, TaskStatus, copy_task_context


def _expire(manager, task_id):
    with sqlite3.connect(manager.db_path) as db:
        db.execute('UPDATE tasks SET lease_until=0 WHERE task_id=?', (task_id,))


def test_publication_checks_expiry_even_before_recovery_and_cannot_revive_lease(tmp_path):
    manager = TaskManager(db_path=str(tmp_path / 'jobs.sqlite3'))
    task_id = manager.enqueue('x', 'x', {})
    manager.claim_next('old')
    _expire(manager, task_id)
    artifact = tmp_path / 'artifact.txt'
    artifact.write_text('replacement')
    with manager.execution(task_id, 'old'), pytest.raises(JobLeaseLost):
        with manager.publication_guard():
            artifact.write_text('stale')
    assert artifact.read_text() == 'replacement'
    assert manager.heartbeat(task_id, 'old') is False
    assert manager.recover_expired() == 1


def test_publication_guard_serializes_recovery_and_allows_nested_helpers(tmp_path):
    manager = TaskManager(db_path=str(tmp_path / 'jobs.sqlite3'))
    recovery = TaskManager(db_path=manager.db_path)
    task_id = manager.enqueue('x', 'x', {})
    manager.claim_next('worker')
    attempting, finished = threading.Event(), threading.Event()
    def recover():
        attempting.set()
        recovery.recover_expired()
        finished.set()
    thread = threading.Thread(target=recover)
    with manager.execution(task_id, 'worker'), manager.publication_guard():
        with manager.publication_guard():
            manager.update_task(task_id, progress=25)
        # Simulate expiry during a short publication. Recovery must not grant a
        # replacement ownership while the original publication is in progress.
        with manager._connection(write=True) as db:
            db.execute('UPDATE tasks SET lease_until=0 WHERE task_id=?', (task_id,))
        thread.start()
        assert attempting.wait(1)
        assert not finished.wait(0.05)
        (tmp_path / 'artifact.txt').write_text('authorized before expiry')
    thread.join(2)
    assert finished.is_set()
    assert recovery.get_task(task_id).status == TaskStatus.INTERRUPTED


def test_cancelled_job_cannot_publish_or_enter_a_queued_worker(tmp_path):
    manager = TaskManager(db_path=str(tmp_path / 'jobs.sqlite3'))
    task_id = manager.enqueue('x', 'x', {})
    manager.claim_next('worker')
    calls = []
    with manager.execution(task_id, 'worker'):
        bound = copy_task_context(lambda: calls.append('paid call'))
        manager.cancel_task(task_id)
        with pytest.raises(JobCancelled), manager.publication_guard():
            calls.append('artifact')
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(JobCancelled):
            pool.submit(bound).result(timeout=2)
    assert calls == []


@pytest.mark.parametrize('late_stage', ['graph', 'batch', 'error', 'complete'])
def test_stale_graph_worker_cannot_overwrite_replacement_project(monkeypatch, tmp_path, late_stage):
    from app.api import graph as graph_api
    from app.models.project import Project, ProjectStatus
    from app.services.graph_builder import BatchSubmission
    now = datetime.now().isoformat()
    original = Project(project_id='proj_fence', name='Fence', status=ProjectStatus.ONTOLOGY_GENERATED,
                       created_at=now, updated_at=now, ontology={'entity_types': [], 'edge_types': []})
    stored = {'project': original}
    writes = []
    def save(project):
        writes.append(copy.deepcopy(project))
        stored['project'] = copy.deepcopy(project)
    monkeypatch.setattr(graph_api.ProjectManager, 'get_project', lambda _id: copy.deepcopy(stored['project']))
    monkeypatch.setattr(graph_api.ProjectManager, 'get_extracted_text', lambda _id: 'source')
    monkeypatch.setattr(graph_api.ProjectManager, 'save_project', save)
    manager = TaskManager(db_path=str(tmp_path / 'jobs.sqlite3'))
    parameters = {'project_id': original.project_id, 'graph_name': original.name,
                  'chunk_size': 500, 'chunk_overlap': 50,
                  'input_digest': graph_api._graph_input_digest('source', original.ontology)}
    task_id = manager.enqueue('graph_build', 'graph_build', parameters)
    manager.claim_next('old')
    replacement_writes = []
    def supersede():
        _expire(manager, task_id)
        replacement = TaskManager(db_path=manager.db_path)
        replacement.recover_expired()
        replacement.retry_task(task_id, 'retry-once', acknowledge_effects=True)
        replacement.claim_next('new')
        fresh = copy.deepcopy(original)
        fresh.graph_build_task_id = task_id
        fresh.graph_id = 'replacement_graph'
        fresh.zep_batch_id = 'replacement_batch'
        fresh.status = ProjectStatus.GRAPH_COMPLETED
        with replacement.execution(task_id, 'new'), replacement.publication_guard():
            save(fresh)
        replacement_writes.append(len(writes))
    class Builder:
        def __init__(self, **_):
            pass
        def validate_batch_chunks(self, *_args, **_kwargs):
            pass
        def create_graph(self, name, graph_id_callback):
            if late_stage == 'graph':
                supersede()
            graph_id_callback('old_graph')
            return 'old_graph'
        def set_ontology(self, *_):
            if late_stage == 'error':
                supersede()
                raise RuntimeError('old external request failed after retry')
        def add_text_batches(self, *_, batch_created_callback, **_kwargs):
            if late_stage == 'batch':
                supersede()
            batch_created_callback('old_batch', 'old_operation')
            return BatchSubmission(batch_id='old_batch', operation_id='old_operation', episode_uuids=[], item_count=1)
        def _wait_for_batch(self, *_):
            pass
        def get_graph_data(self, *_):
            if late_stage == 'complete':
                supersede()
            return {'node_count': 2, 'edge_count': 1}
    monkeypatch.setattr(graph_api, 'GraphBuilderService', Builder)
    with manager.execution(task_id, 'old'), pytest.raises(JobLeaseLost):
        graph_api.run_graph_build_job(task_id, parameters)
    assert stored['project'].graph_id == 'replacement_graph'
    assert stored['project'].zep_batch_id == 'replacement_batch'
    assert stored['project'].status == ProjectStatus.GRAPH_COMPLETED
    assert stored['project'].error is None
    assert len(writes) == replacement_writes[0]
    assert manager.get_task(task_id).status == TaskStatus.PROCESSING
