import sqlite3
import threading
import time

import pytest

from app.models.task import TaskManager, TaskStatus


def test_pending_parameters_and_terminal_results_survive_restart(tmp_path):
    path = str(tmp_path / 'jobs.sqlite3')
    first = TaskManager(db_path=path)
    task_id = first.enqueue('graph_build', 'graph_build', {'project_id': 'proj_test'}, dedupe_key='graph:proj_test')
    restarted = TaskManager(db_path=path)
    task = restarted.get_task(task_id)
    assert task.parameters == {'project_id': 'proj_test'}
    assert task.handler == 'graph_build'
    restarted.complete_task(task_id, {'graph_id': 'graph_test'})
    assert TaskManager(db_path=path).get_task(task_id).result == {'graph_id': 'graph_test'}


def test_atomic_dedupe_and_claim_across_independent_managers(tmp_path):
    path = str(tmp_path / 'jobs.sqlite3')
    managers = [TaskManager(db_path=path) for _ in range(8)]
    barrier = threading.Barrier(len(managers))
    ids, claims = [], []
    def attempt(index):
        barrier.wait()
        ids.append(managers[index].enqueue('x', 'x', {'n': 1}, dedupe_key='one'))
        task = managers[index].claim_next(str(index), lease_seconds=60)
        if task:
            claims.append(task.task_id)
    workers = [threading.Thread(target=attempt, args=(i,)) for i in range(len(managers))]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert len(set(ids)) == 1
    assert claims == [ids[0]]


def test_expired_started_job_is_interrupted_and_never_automatically_reclaimed(tmp_path):
    path = str(tmp_path / 'jobs.sqlite3')
    first = TaskManager(db_path=path)
    task_id = first.enqueue('external', 'external', {'effect': 'may have occurred'})
    first.claim_next('crashed', lease_seconds=60)
    with sqlite3.connect(path) as db:
        db.execute('UPDATE tasks SET lease_until = 0 WHERE task_id = ?', (task_id,))
    restarted = TaskManager(db_path=path)
    assert restarted.recover_expired() == 1
    task = restarted.get_task(task_id)
    assert task.status == TaskStatus.INTERRUPTED
    assert task.error_code == 'worker_interrupted'
    assert restarted.claim_next('new') is None
    with pytest.raises(ValueError, match='acknowledge'):
        restarted.retry_task(task_id, idempotency_key='retry-1')
    restarted.retry_task(task_id, idempotency_key='retry-1', acknowledge_effects=True)
    claimed = restarted.claim_next('new')
    assert claimed.task_id == task_id
    assert claimed.attempts == 2
    restarted.retry_task(task_id, idempotency_key='retry-1', acknowledge_effects=True)
    assert restarted.get_task(task_id).status == TaskStatus.PROCESSING


def test_idempotency_key_reuses_completed_job_and_rejects_different_request(tmp_path):
    manager = TaskManager(db_path=str(tmp_path / 'jobs.sqlite3'))
    task_id = manager.enqueue('x', 'x', {'n': 1}, idempotency_key='key')
    manager.complete_task(task_id, {'ok': True})
    assert manager.enqueue('x', 'x', {'n': 1}, idempotency_key='key') == task_id
    with pytest.raises(ValueError, match='Idempotency'):
        manager.enqueue('x', 'x', {'n': 2}, idempotency_key='key')


def test_cancel_pending_job_cannot_be_claimed(tmp_path):
    manager = TaskManager(db_path=str(tmp_path / 'jobs.sqlite3'))
    task_id = manager.enqueue('x', 'x', {})
    manager.cancel_task(task_id)
    assert manager.get_task(task_id).status == TaskStatus.CANCELLED
    assert manager.claim_next('worker') is None


def test_dispatcher_recovers_queued_work_with_bounded_execution(tmp_path):
    from app.services.job_dispatcher import JobDispatcher
    manager = TaskManager(db_path=str(tmp_path / 'jobs.sqlite3'))
    task_ids = [manager.enqueue('work', 'work', {'number': i}) for i in range(5)]
    active = 0
    maximum = 0
    guard = threading.Lock()
    def execute(task_id, parameters):
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.02)
        manager.complete_task(task_id, parameters)
        with guard:
            active -= 1
    dispatcher = JobDispatcher(manager=TaskManager(db_path=manager.db_path), handlers={'work': execute}, workers=2, poll_seconds=0.01)
    dispatcher.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and any(manager.get_task(i).status != TaskStatus.COMPLETED for i in task_ids):
            time.sleep(0.01)
        assert all(manager.get_task(i).status == TaskStatus.COMPLETED for i in task_ids)
        assert maximum == 2
    finally:
        dispatcher.stop(wait=True)


def test_expired_worker_is_fenced_from_publishing_after_recovery(tmp_path):
    from app.models.task import JobLeaseLost
    path = str(tmp_path / 'jobs.sqlite3')
    manager = TaskManager(db_path=path)
    task_id = manager.enqueue('external', 'external', {})
    manager.claim_next('stale')
    with sqlite3.connect(path) as db:
        db.execute('UPDATE tasks SET lease_until=0 WHERE task_id=?', (task_id,))
    TaskManager(db_path=path).recover_expired()
    with manager.execution(task_id, 'stale'), pytest.raises(JobLeaseLost):
        manager.complete_task(task_id, {'success': 'too late'})
    assert manager.get_task(task_id).status == TaskStatus.INTERRUPTED


def test_dispatcher_never_replays_ambiguous_external_effects(tmp_path):
    from app.services.job_dispatcher import JobDispatcher
    path = str(tmp_path / 'jobs.sqlite3')
    first = TaskManager(db_path=path)
    task_id = first.enqueue('external', 'external', {})
    first.claim_next('crashed-after-effect')
    effects = ['already performed']
    with sqlite3.connect(path) as db:
        db.execute('UPDATE tasks SET lease_until=0 WHERE task_id=?', (task_id,))
    restarted = TaskManager(db_path=path)
    dispatcher = JobDispatcher(manager=restarted, handlers={'external': lambda *_: effects.append('duplicate')}, poll_seconds=0.01)
    dispatcher.start()
    try:
        time.sleep(0.05)
        assert restarted.get_task(task_id).status == TaskStatus.INTERRUPTED
        assert effects == ['already performed']
    finally:
        dispatcher.stop(wait=True)


def test_retry_conflicts_with_newer_active_job_without_losing_either(tmp_path):
    manager = TaskManager(db_path=str(tmp_path / 'jobs.sqlite3'))
    first = manager.enqueue('x', 'x', {}, dedupe_key='same')
    manager.fail_task(first, 'failed')
    second = manager.enqueue('x', 'x', {}, dedupe_key='same')
    with pytest.raises(ValueError, match='newer job'):
        manager.retry_task(first, 'explicit-retry', acknowledge_effects=True)
    assert manager.get_task(first).status == TaskStatus.FAILED
    assert manager.get_task(second).status == TaskStatus.PENDING


def test_retry_and_cancel_http_contract_and_project_filter(tmp_path):
    from flask import Flask
    from app.api import graph_bp
    manager = TaskManager(db_path=str(tmp_path / 'jobs.sqlite3'))
    app = Flask(__name__)
    app.config['JOBS_DB_PATH'] = manager.db_path
    app.register_blueprint(graph_bp, url_prefix='/api/graph')
    one = manager.enqueue('x', 'x', {}, metadata={'project_id': 'one'})
    manager.enqueue('x', 'x', {}, metadata={'project_id': 'two'})
    client = app.test_client()
    jobs = client.get('/api/graph/tasks?project_id=one').json['data']
    assert [task['task_id'] for task in jobs] == [one]
    manager.fail_task(one, 'failed')
    url = f'/api/graph/task/{one}/retry'
    assert client.post(url, json={'acknowledge_effects': True}).status_code == 409
    response = client.post(url, headers={'Idempotency-Key': 'retry-once'}, json={'acknowledge_effects': True})
    assert response.status_code == 202
    assert response.json['data']['status'] == 'pending'
    assert client.post(f'/api/graph/task/{one}/cancel').json['data']['status'] == 'cancelled'


@pytest.mark.parametrize("forced", [False, True])
def test_graph_worker_restarts_from_queue_and_builds_once(monkeypatch, forced):
    from datetime import datetime
    from types import SimpleNamespace
    from app.api import graph as graph_api
    from app.models.project import Project, ProjectStatus
    from app.services.job_dispatcher import JobDispatcher
    from app.services.graph_builder import BatchSubmission
    project = Project(project_id='proj_restart', name='Restart', status=ProjectStatus.ONTOLOGY_GENERATED,
                      created_at=datetime.now().isoformat(), updated_at=datetime.now().isoformat(),
                      ontology={'entity_types': [], 'edge_types': []})
    if forced:
        project.status = ProjectStatus.GRAPH_COMPLETED
        project.graph_id = 'old_graph'
        project.graph_build_task_id = 'old_task'
    monkeypatch.setattr(graph_api.ProjectManager, 'get_project', lambda _id: project)
    monkeypatch.setattr(graph_api.ProjectManager, 'get_extracted_text', lambda _id: 'source document')
    monkeypatch.setattr(graph_api.ProjectManager, 'save_project', lambda _project: None)
    mutations = []
    monkeypatch.setattr(graph_api, '_delete_cloud_graph_if_present', lambda _id: mutations.append('delete'))
    class Builder:
        def __init__(self, **_):
            pass
        def validate_batch_chunks(self, *_args, **_kwargs):
            pass
        def create_graph(self, name, graph_id_callback):
            mutations.append('create')
            graph_id_callback('graph_restart')
            return 'graph_restart'
        def set_ontology(self, *_):
            mutations.append('ontology')
        def add_text_batches(self, *_, batch_created_callback, **_kwargs):
            mutations.append('ingest')
            batch_created_callback('batch_restart', 'operation_restart')
            return BatchSubmission(batch_id='batch_restart', operation_id='operation_restart', episode_uuids=[], item_count=1)
        def _wait_for_batch(self, *_):
            pass
        def get_graph_data(self, *_):
            return {'node_count': 2, 'edge_count': 1}
    monkeypatch.setattr(graph_api, 'GraphBuilderService', Builder)
    manager = TaskManager()
    parameters = {'project_id': project.project_id, 'graph_name': project.name,
                  'chunk_size': 500, 'chunk_overlap': 50,
                  'input_digest': graph_api._graph_input_digest('source document', project.ontology),
                  'force': forced}
    task_id = manager.enqueue('graph_build', 'graph_build', parameters, metadata={'rebuild_graph_id': 'old_graph' if forced else None})
    # No closure, object instance, or in-memory project/task claim is passed to
    # the restarted dispatcher; it reconstructs the operation from SQLite.
    restarted = TaskManager(db_path=manager.db_path)
    dispatcher = JobDispatcher(manager=restarted, handlers={'graph_build': graph_api.run_graph_build_job}, poll_seconds=0.01)
    dispatcher.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and restarted.get_task(task_id).status in {TaskStatus.PENDING, TaskStatus.PROCESSING}:
            time.sleep(0.01)
        task = restarted.get_task(task_id)
        assert task.status == TaskStatus.COMPLETED, task.error
        assert task.result['graph_id'] == 'graph_restart'
        assert mutations == (['delete'] if forced else []) + ['create', 'ontology', 'ingest']
        assert project.status == ProjectStatus.GRAPH_COMPLETED
    finally:
        dispatcher.stop(wait=True)


def test_idempotency_alias_created_by_dedupe_survives_completion_and_cleanup(tmp_path):
    manager = TaskManager(db_path=str(tmp_path / 'jobs.sqlite3'))
    first = manager.enqueue('x', 'x', {'n': 1}, dedupe_key='same')
    assert manager.enqueue('x', 'x', {'n': 1}, dedupe_key='same', idempotency_key='joined') == first
    manager.complete_task(first, {'done': True})
    manager.cleanup_old_tasks(max_age_hours=-1)
    restarted = TaskManager(db_path=manager.db_path)
    assert restarted.enqueue('x', 'x', {'n': 1}, idempotency_key='joined') == first
