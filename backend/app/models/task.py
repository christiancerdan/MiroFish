"""Durable task state and atomic job claims.

Queued handlers may be recovered automatically. An expired running lease is
ambiguous: its external effects might already exist, so it requires an explicit
retry and never silently returns to the queue.
"""
import json
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import MutableMapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from functools import wraps
from typing import Any, Dict, Optional

from ..utils.locale import t


class TaskStatus(str, Enum):
    PENDING = 'pending'
    PROCESSING = 'processing'
    COMPLETED = 'completed'
    FAILED = 'failed'
    INTERRUPTED = 'interrupted'
    CANCELLED = 'cancelled'
    BUDGET_EXCEEDED = 'budget_exceeded'


class JobCancelled(RuntimeError):
    code = 'job_cancelled'


class JobLeaseLost(RuntimeError):
    code = 'worker_interrupted'


@dataclass
class Task:
    task_id: str
    task_type: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    progress: int = 0
    message: str = ''
    result: Optional[Dict] = None
    error: Optional[str] = None
    metadata: Dict = field(default_factory=dict)
    progress_detail: Dict = field(default_factory=dict)
    handler: Optional[str] = None
    parameters: Dict = field(default_factory=dict)
    attempts: int = 0
    error_code: Optional[str] = None
    cancel_requested: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            'task_id': self.task_id, 'task_type': self.task_type,
            'status': self.status.value, 'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(), 'progress': self.progress,
            'message': self.message, 'progress_detail': self.progress_detail,
            'result': self.result, 'error': self.error, 'metadata': self.metadata,
            'handler': self.handler, 'attempts': self.attempts,
            'error_code': self.error_code, 'cancel_requested': self.cancel_requested,
            'retryable': self.handler is not None and self.status in {
                TaskStatus.INTERRUPTED, TaskStatus.FAILED, TaskStatus.BUDGET_EXCEEDED,
            },
        }


class _TaskMapping(MutableMapping):
    """Legacy `_tasks` access delegates to the durable store, without a cache."""
    def __init__(self, manager):
        self.manager = manager

    def __getitem__(self, key):
        value = self.manager.get_task(key)
        if value is None:
            raise KeyError(key)
        return value

    def __setitem__(self, key, value):
        with self.manager._connection(write=True) as db:
            self.manager._save(db, value)

    def __delitem__(self, key):
        with self.manager._connection(write=True) as db:
            db.execute('DELETE FROM tasks WHERE task_id=?', (key,))

    def __iter__(self):
        return iter(task['task_id'] for task in self.manager.list_tasks())

    def __len__(self):
        return len(self.manager.list_tasks())

    def clear(self):
        with self.manager._connection(write=True) as db:
            db.execute('DELETE FROM retry_requests')
            db.execute('DELETE FROM job_requests')
            db.execute('DELETE FROM tasks')


class TaskManager:
    _instance = None
    _lock = threading.RLock()
    _execution = threading.local()

    def __new__(cls, db_path=None):
        if db_path is not None:
            instance = super().__new__(cls)
            instance._initialize(os.fspath(db_path))
            return instance
        bound = getattr(cls._execution, 'manager', None)
        if bound is not None:
            return bound
        from flask import current_app, has_app_context
        from ..config import Config
        path = (current_app.config.get('JOBS_DB_PATH') if has_app_context() else None)
        path = path or getattr(Config, 'JOBS_DB_PATH', os.path.join(Config.UPLOAD_FOLDER, 'jobs.sqlite3'))
        with cls._lock:
            if cls._instance is None or cls._instance.db_path != os.path.abspath(path):
                cls._instance = super().__new__(cls)
                cls._instance._initialize(path)
        return cls._instance

    def _initialize(self, path):
        self.db_path = os.path.abspath(path)
        self._task_lock = threading.RLock()
        self._tasks = _TaskMapping(self)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with self._connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY, task_type TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, payload TEXT NOT NULL,
                    handler TEXT, parameters TEXT NOT NULL DEFAULT '{}',
                    dedupe_key TEXT, idempotency_key TEXT,
                    owner TEXT, lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0
                );
                CREATE UNIQUE INDEX IF NOT EXISTS job_active_dedupe
                    ON tasks(dedupe_key) WHERE dedupe_key IS NOT NULL
                    AND status IN ('pending','processing','interrupted');
                CREATE UNIQUE INDEX IF NOT EXISTS job_request_idempotency
                    ON tasks(handler,idempotency_key) WHERE idempotency_key IS NOT NULL;
                CREATE INDEX IF NOT EXISTS job_queue ON tasks(status,created_at);
                CREATE TABLE IF NOT EXISTS job_requests (
                    handler TEXT NOT NULL, request_key TEXT NOT NULL,
                    task_id TEXT NOT NULL, parameters TEXT NOT NULL,
                    PRIMARY KEY(handler,request_key)
                );
                INSERT OR IGNORE INTO job_requests(handler,request_key,task_id,parameters)
                    SELECT handler,idempotency_key,task_id,parameters FROM tasks
                    WHERE handler IS NOT NULL AND idempotency_key IS NOT NULL;
                CREATE TABLE IF NOT EXISTS retry_requests (
                    task_id TEXT NOT NULL, request_key TEXT NOT NULL,
                    PRIMARY KEY(task_id,request_key)
                );
            ''')

    @contextmanager
    def _connection(self, write=False):
        # Short nested publication helpers share their writer transaction.
        # Opening a second writer here would deadlock against our own guard.
        publication = getattr(self._execution, 'publication', None)
        if publication and publication[0] == self.db_path:
            yield publication[1]
            return
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _decode(row):
        if row is None:
            return None
        data = json.loads(row['payload'])
        data.pop('retryable', None)
        data['created_at'] = datetime.fromisoformat(row['created_at'])
        data['updated_at'] = datetime.fromisoformat(row['updated_at'])
        data['status'] = TaskStatus(row['status'])
        data['parameters'] = json.loads(row['parameters'])
        data['attempts'] = row['attempts']
        data['cancel_requested'] = bool(row['cancel_requested'])
        return Task(**data)

    def _save(self, db, task):
        db.execute('''UPDATE tasks SET status=?,updated_at=?,payload=?,cancel_requested=?
                      WHERE task_id=?''', (task.status.value, task.updated_at.isoformat(),
                      json.dumps(task.to_dict(), ensure_ascii=False), int(task.cancel_requested), task.task_id))

    def create_task(self, task_type: str, metadata: Optional[Dict] = None) -> str:
        return self.enqueue(task_type, None, {}, metadata=metadata)

    def enqueue(self, task_type, handler, parameters, metadata=None,
                dedupe_key=None, idempotency_key=None):
        serialized = json.dumps(parameters, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if idempotency_key is not None and (not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200):
            raise ValueError('Idempotency-Key must contain between 1 and 200 characters')
        with self._connection(write=True) as db:
            if idempotency_key:
                row = db.execute('SELECT * FROM job_requests WHERE handler=? AND request_key=?',
                                 (handler, idempotency_key)).fetchone()
                if row:
                    if row['parameters'] != serialized:
                        raise ValueError('Idempotency-Key was already used with different parameters')
                    return row['task_id']
            if dedupe_key:
                row = db.execute("SELECT task_id,parameters FROM tasks WHERE dedupe_key=? AND status IN ('pending','processing','interrupted')", (dedupe_key,)).fetchone()
                if row:
                    if row['parameters'] != serialized:
                        raise ValueError('An active job for this operation has different parameters')
                    if idempotency_key:
                        db.execute('INSERT INTO job_requests VALUES(?,?,?,?)',
                                   (handler, idempotency_key, row['task_id'], serialized))
                    return row['task_id']
            now = datetime.now()
            task = Task(str(uuid.uuid4()), task_type, TaskStatus.PENDING, now, now,
                        metadata=metadata or {}, handler=handler, parameters=parameters)
            db.execute('''INSERT INTO tasks(task_id,task_type,status,created_at,updated_at,
                       payload,handler,parameters,dedupe_key,idempotency_key)
                       VALUES(?,?,?,?,?,?,?,?,?,?)''',
                       (task.task_id, task_type, task.status.value, now.isoformat(), now.isoformat(),
                        json.dumps(task.to_dict(), ensure_ascii=False), handler, serialized, dedupe_key, idempotency_key))
            if idempotency_key:
                db.execute('INSERT INTO job_requests VALUES(?,?,?,?)',
                           (handler, idempotency_key, task.task_id, serialized))
            return task.task_id

    def get_task(self, task_id):
        with self._connection() as db:
            return self._decode(db.execute('SELECT * FROM tasks WHERE task_id=?', (task_id,)).fetchone())

    def find_active(self, dedupe_key):
        with self._connection() as db:
            return self._decode(db.execute(
                "SELECT * FROM tasks WHERE dedupe_key=? AND status IN ('pending','processing','interrupted')",
                (dedupe_key,),
            ).fetchone())

    def update_task(self, task_id, status=None, progress=None, message=None, result=None,
                    error=None, progress_detail=None, error_code=None):
        with self._connection(write=True) as db:
            row = db.execute('SELECT * FROM tasks WHERE task_id=?', (task_id,)).fetchone()
            task = self._decode(row)
            if task is None:
                return
            owner = getattr(self._execution, 'owner', None)
            if owner and getattr(self._execution, 'task_id', None) == task_id:
                if (row['owner'] != owner or task.status == TaskStatus.INTERRUPTED
                        or row['lease_until'] is None or row['lease_until'] <= time.time()):
                    raise JobLeaseLost('Worker lease was lost; inspect persisted output before retrying')
                if task.cancel_requested and status not in {TaskStatus.CANCELLED, TaskStatus.FAILED}:
                    raise JobCancelled('Cancellation requested; any completed external effects are retained')
            if task.status == TaskStatus.CANCELLED and status != TaskStatus.CANCELLED:
                return
            task.updated_at = datetime.now()
            for name, value in [('status', status), ('progress', progress), ('message', message),
                                ('result', result), ('error', error), ('progress_detail', progress_detail), ('error_code', error_code)]:
                if value is not None:
                    setattr(task, name, TaskStatus(value) if name == 'status' else value)
            self._save(db, task)

    def complete_task(self, task_id, result):
        self.update_task(task_id, status=TaskStatus.COMPLETED, progress=100,
                         message=t('progress.taskComplete'), result=result)

    def fail_task(self, task_id, error):
        code = getattr(error, 'code', None)
        status = TaskStatus.BUDGET_EXCEEDED if code == 'budget_exceeded' else TaskStatus.FAILED
        task = self.get_task(task_id)
        if isinstance(error, JobLeaseLost):
            return
        if isinstance(error, JobCancelled) or (task and task.cancel_requested):
            status, code = TaskStatus.CANCELLED, 'job_cancelled'
        self.update_task(task_id, status=status, message=t('progress.taskFailed'), error=str(error), error_code=code)

    def list_tasks(self, task_type=None):
        with self._connection() as db:
            sql = 'SELECT * FROM tasks'
            args = ()
            if task_type:
                sql += ' WHERE task_type=?'
                args = (task_type,)
            return [self._decode(row).to_dict() for row in db.execute(sql + ' ORDER BY created_at DESC', args)]

    def cleanup_old_tasks(self, max_age_hours=24):
        cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
        with self._connection(write=True) as db:
            # Idempotency records must outlive ordinary cleanup to avoid replay.
            db.execute("DELETE FROM tasks WHERE created_at<? AND status IN ('completed','failed','cancelled') AND idempotency_key IS NULL AND NOT EXISTS (SELECT 1 FROM job_requests WHERE job_requests.task_id=tasks.task_id)", (cutoff,))

    def claim_next(self, owner, lease_seconds=60, handlers=None):
        with self._connection(write=True) as db:
            sql = "SELECT * FROM tasks WHERE status='pending' AND handler IS NOT NULL"
            args = []
            if handlers is not None:
                if not handlers:
                    return None
                sql += ' AND handler IN (' + ','.join('?' for _ in handlers) + ')'
                args.extend(handlers)
            row = db.execute(sql + ' ORDER BY created_at LIMIT 1', args).fetchone()
            task = self._decode(row)
            if task is None:
                return None
            task.status, task.updated_at = TaskStatus.PROCESSING, datetime.now()
            task.attempts += 1
            self._save(db, task)
            db.execute('UPDATE tasks SET owner=?,lease_until=?,attempts=? WHERE task_id=?',
                       (owner, time.time() + lease_seconds, task.attempts, task.task_id))
            return task

    def heartbeat(self, task_id, owner, lease_seconds=60):
        with self._connection(write=True) as db:
            now = time.time()
            updated = db.execute("UPDATE tasks SET lease_until=? WHERE task_id=? AND owner=? AND status='processing' AND lease_until>?",
                                 (now + lease_seconds, task_id, owner, now))
            return updated.rowcount == 1

    def recover_expired(self):
        with self._connection(write=True) as db:
            rows = db.execute("SELECT * FROM tasks WHERE status='processing' AND lease_until IS NOT NULL AND lease_until<?", (time.time(),)).fetchall()
            for row in rows:
                task = self._decode(row)
                task.status, task.updated_at = TaskStatus.INTERRUPTED, datetime.now()
                task.error_code = 'worker_interrupted'
                task.error = 'Worker stopped or lost its lease. Inspect saved output and retry explicitly; external effects may already exist.'
                task.message = task.error
                self._save(db, task)
            return len(rows)

    def retry_task(self, task_id, idempotency_key, acknowledge_effects=False):
        if not idempotency_key or not isinstance(idempotency_key, str) or len(idempotency_key) > 200:
            raise ValueError('A retry Idempotency-Key is required (maximum 200 characters)')
        with self._connection(write=True) as db:
            row = db.execute('SELECT * FROM tasks WHERE task_id=?', (task_id,)).fetchone()
            task = self._decode(row)
            if task is None:
                raise KeyError(task_id)
            if db.execute('SELECT 1 FROM retry_requests WHERE task_id=? AND request_key=?', (task_id, idempotency_key)).fetchone():
                return task
            if not task.handler or task.status not in {TaskStatus.INTERRUPTED, TaskStatus.FAILED, TaskStatus.BUDGET_EXCEEDED}:
                raise ValueError('Only failed, interrupted or budget-exceeded durable jobs can be retried')
            if not acknowledge_effects:
                raise ValueError('Set acknowledge_effects=true after inspecting saved output; retry may repeat external effects')
            if row['dedupe_key'] and db.execute(
                "SELECT 1 FROM tasks WHERE dedupe_key=? AND task_id<>? AND status IN ('pending','processing','interrupted')",
                (row['dedupe_key'], task_id),
            ).fetchone():
                raise ValueError('A newer job already owns this operation; inspect or cancel it before retrying')
            task.status, task.updated_at = TaskStatus.PENDING, datetime.now()
            task.error, task.error_code, task.cancel_requested = None, None, False
            task.message = 'Queued for an explicitly requested retry'
            self._save(db, task)
            db.execute('UPDATE tasks SET owner=NULL,lease_until=NULL WHERE task_id=?', (task_id,))
            db.execute('INSERT INTO retry_requests VALUES(?,?)', (task_id, idempotency_key))
            return task

    def cancel_task(self, task_id):
        with self._connection(write=True) as db:
            task = self._decode(db.execute('SELECT * FROM tasks WHERE task_id=?', (task_id,)).fetchone())
            if task is None:
                raise KeyError(task_id)
            if task.status in {TaskStatus.PENDING, TaskStatus.INTERRUPTED}:
                task.status, task.error_code = TaskStatus.CANCELLED, 'job_cancelled'
            if task.status in {TaskStatus.PROCESSING, TaskStatus.CANCELLED}:
                task.cancel_requested = True
                task.message = 'Cancellation requested; external effects already completed are retained'
                task.updated_at = datetime.now()
                self._save(db, task)
            return task

    @contextmanager
    def publication_guard(self):
        """Fence a short artifact write against lease recovery and retry.

        This is a job ownership barrier, not an atomic filesystem/SQLite commit.
        Callers still use atomic file replacement and may recover partial output
        after a process crash. Never hold this transaction over a provider call.
        Synchronous code outside a bound job keeps its existing behavior.
        """
        task_id = getattr(self._execution, 'task_id', None)
        owner = getattr(self._execution, 'owner', None)
        if not task_id or not owner:
            yield
            return
        with self._connection(write=True) as db:
            row = db.execute('SELECT * FROM tasks WHERE task_id=?', (task_id,)).fetchone()
            if (row is None or row['owner'] != owner
                    or row['status'] != TaskStatus.PROCESSING.value
                    or row['lease_until'] is None or row['lease_until'] <= time.time()):
                raise JobLeaseLost('Worker no longer owns this job; saved artifacts were left unchanged')
            if row['cancel_requested']:
                raise JobCancelled('Cancellation requested; saved artifacts were left unchanged')
            previous = getattr(self._execution, 'publication', None)
            self._execution.publication = (self.db_path, db)
            try:
                yield
            finally:
                if previous is None:
                    self._execution.__dict__.pop('publication', None)
                else:
                    self._execution.publication = previous

    def assert_current_execution(self):
        """Check the bound job before starting an operation or queueing work."""
        with self.publication_guard():
            pass

    @contextmanager
    def execution(self, task_id, owner):
        previous = self._execution.__dict__.copy()
        self._execution.manager, self._execution.task_id, self._execution.owner = self, task_id, owner
        try:
            yield
        finally:
            self._execution.__dict__.clear()
            self._execution.__dict__.update(previous)


def copy_task_context(callback):
    """Carry only the execution identity into a thread, never a DB connection."""
    manager = getattr(TaskManager._execution, 'manager', None)
    task_id = getattr(TaskManager._execution, 'task_id', None)
    owner = getattr(TaskManager._execution, 'owner', None)
    if manager is None or not task_id or not owner:
        return callback

    @wraps(callback)
    def bound(*args, **kwargs):
        with manager.execution(task_id, owner):
            manager.assert_current_execution()
            return callback(*args, **kwargs)
    return bound
