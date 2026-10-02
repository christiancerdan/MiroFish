"""Bounded execution of persisted, named jobs.

Queued work survives shutdown; expired running jobs become interrupted rather
than replaying external calls. SQLite arbitrates claims across app processes.
"""
import atexit
import logging
import threading
import uuid
from contextlib import nullcontext

from ..models.task import JobLeaseLost, TaskManager, TaskStatus

logger = logging.getLogger(__name__)
_dispatchers = []


def default_handlers():
    from ..api.graph import run_graph_build_job
    from ..api.simulation import run_prepare_job
    from ..api.report import run_report_job
    return {'graph_build': run_graph_build_job,
            'simulation_prepare': run_prepare_job,
            'report_generate': run_report_job}


class JobDispatcher:
    def __init__(self, manager=None, handlers=None, workers=2, lease_seconds=60,
                 poll_seconds=0.5, app=None):
        if not 1 <= int(workers) <= 32:
            raise ValueError('JOB_WORKERS must be between 1 and 32')
        if lease_seconds < 3:
            raise ValueError('JOB_LEASE_SECONDS must be at least 3 seconds')
        self.manager = manager or TaskManager()
        self.handlers = default_handlers() if handlers is None else handlers
        self.workers = int(workers)
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds
        self.app = app
        self._stop = threading.Event()
        self._threads = []
        self._active = {}
        self._guard = threading.Lock()

    def start(self):
        if self._threads:
            return self
        self.manager.recover_expired()
        for _ in range(self.workers):
            thread = threading.Thread(target=self._worker, name='mirofish-job', daemon=True)
            self._threads.append(thread)
            thread.start()
        heartbeat = threading.Thread(target=self._heartbeat, name='mirofish-job-leases', daemon=True)
        self._threads.append(heartbeat)
        heartbeat.start()
        return self

    def _worker(self):
        owner = str(uuid.uuid4())
        while not self._stop.is_set():
            try:
                task = self.manager.claim_next(owner, self.lease_seconds, handlers=self.handlers)
                if task is None:
                    self._stop.wait(self.poll_seconds)
                    continue
                with self._guard:
                    self._active[task.task_id] = owner
                try:
                    with self.manager.execution(task.task_id, owner):
                        context = self.app.app_context() if self.app else nullcontext()
                        with context:
                            project_id = task.metadata.get('project_id') or task.parameters.get('project_id')
                            budget_context = nullcontext()
                            if project_id:
                                from ..utils.budget import BudgetContext
                                budget_context = BudgetContext(project_id)
                            with budget_context:
                                result = self.handlers[task.handler](task.task_id, task.parameters)
                                if project_id:
                                    from ..utils.budget import BudgetStore
                                    BudgetStore().check(project_id)
                            latest = self.manager.get_task(task.task_id)
                            if latest and latest.status == TaskStatus.PROCESSING:
                                if isinstance(result, dict):
                                    self.manager.complete_task(task.task_id, result)
                                else:
                                    self.manager.fail_task(task.task_id, RuntimeError('Job handler returned without recording a result'))
                except JobLeaseLost:
                    logger.warning('Job %s lost its lease; leaving it interrupted', task.task_id)
                except Exception as exc:
                    logger.exception('Job %s failed', task.task_id)
                    try:
                        with self.manager.execution(task.task_id, owner):
                            self.manager.fail_task(task.task_id, exc)
                    except JobLeaseLost:
                        logger.warning('Stale worker cannot publish failure for %s', task.task_id)
                finally:
                    with self._guard:
                        self._active.pop(task.task_id, None)
            except Exception:
                logger.exception('Durable job dispatch failed; retrying after poll interval')
                self._stop.wait(self.poll_seconds)

    def _heartbeat(self):
        while True:
            with self._guard:
                active = dict(self._active)
            if self._stop.is_set() and not active:
                return
            try:
                for task_id, owner in active.items():
                    self.manager.heartbeat(task_id, owner, self.lease_seconds)
                self.manager.recover_expired()
            except Exception:
                logger.exception('Unable to refresh durable job leases')
            if self._stop.is_set():
                threading.Event().wait(min(self.lease_seconds / 3, 1))
            else:
                self._stop.wait(min(self.lease_seconds / 3, 1))

    def stop(self, wait=False):
        self._stop.set()
        if wait:
            for thread in self._threads:
                thread.join(timeout=max(self.lease_seconds, 5))


def start_job_dispatcher(app):
    """Explicit server startup hook, after blueprint registration.

    App factories may gate this call on JOBS_AUTOSTART; server entrypoints call
    it explicitly. TESTING suppresses it to keep test imports free of workers.
    """
    existing = app.extensions.get('job_dispatcher')
    if existing:
        return existing
    if app.config.get('TESTING', False):
        return None
    with app.app_context():
        manager = TaskManager()
    dispatcher = JobDispatcher(manager=manager, app=app,
        workers=app.config.get('JOB_WORKERS', 2),
        lease_seconds=app.config.get('JOB_LEASE_SECONDS', 60))
    app.extensions['job_dispatcher'] = dispatcher
    _dispatchers.append(dispatcher)
    dispatcher.start()
    return dispatcher


def stop_job_dispatcher(app=None, wait=False):
    for dispatcher in ([app.extensions.get('job_dispatcher')] if app is not None else list(_dispatchers)):
        if dispatcher:
            dispatcher.stop(wait=wait)


atexit.register(stop_job_dispatcher)
