"""Transactional per-project model admission, shared with simulation processes.

Reserve every request before sending it. Unknown/failing/cancelled requests keep
all reservations because a remote provider may already have billed them. Money
is an estimate from explicitly configured exact-model rates, never subscription
pricing. Text token admission uses a conservative UTF-8 byte envelope; provider
usage is authoritative when returned (see docs/run-budgets.md for limitations).
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future, TimeoutError as FutureTimeout
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_CEILING
from functools import wraps
import json
import math
import os
from pathlib import Path
import sqlite3
import time
import threading
import uuid

from .storage import validate_storage_id


_request_failure: ContextVar[dict | None] = ContextVar('budget_request_failure', default=None)


class BudgetExceeded(RuntimeError):
    code = 'budget_exceeded'

    def __init__(self, reason: str = 'limit_reached'):
        self.reason = reason
        observer = _request_failure.get()
        if observer is not None:
            observer['exceeded'] = True
        super().__init__('Run budget exceeded; no further model requests are allowed.')


@dataclass(frozen=True)
class BudgetLimits:
    max_calls: int = 1000
    max_tokens: int = 1000000
    max_output_tokens: int = 250000
    max_wall_seconds: int = 3600
    max_cost_usd: float | None = None


def _config(name, default=None):
    from ..config import Config
    try:
        from flask import current_app, has_app_context
        if has_app_context():
            return current_app.config.get(name, getattr(Config, name, default))
    except ImportError:
        pass
    return getattr(Config, name, default)


def _limits(values: dict) -> BudgetLimits:
    if not isinstance(values, dict) or set(values) - set(asdict(BudgetLimits())):
        raise ValueError('Unknown budget limit')
    result = asdict(BudgetLimits())
    result.update(values)
    for key, value in result.items():
        if key == 'max_cost_usd':
            if value is not None and (isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0 or value > 1e9):
                raise ValueError('max_cost_usd must be a positive finite number or null')
        elif type(value) is not int or not 0 < value <= 10**12:
            raise ValueError(key + ' must be a positive integer')
    return BudgetLimits(**result)


def _pricing(values: dict) -> dict:
    if not isinstance(values, dict):
        raise ValueError('BUDGET_MODEL_PRICING_JSON must be an object')
    result = {}
    for model, rates in values.items():
        if not isinstance(model, str) or not isinstance(rates, dict) or set(rates) != {'input_per_million', 'output_per_million'}:
            raise ValueError('Each model price requires input_per_million and output_per_million')
        for value in rates.values():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError('Model prices must be finite nonnegative numbers')
        result[model] = dict(rates)
    return result


def _cost_nano(rates, input_tokens, output_tokens):
    if rates is None:
        return 0
    # Integer nanodollars avoid floating point admission races; round upward.
    amount = (Decimal(str(rates['input_per_million'])) * input_tokens + Decimal(str(rates['output_per_million'])) * output_tokens) * 1000
    return int(amount.to_integral_value(rounding=ROUND_CEILING))


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    run_id: str
    db_path: str
    deadline_at: float


class BudgetStore:
    def __init__(self, db_path=None, *, defaults: BudgetLimits | None = None, pricing: dict | None = None):
        fallback = str(Path(__file__).resolve().parents[2] / 'uploads' / 'budgets.sqlite3')
        self.db_path = os.path.abspath(db_path or os.environ.get('MIROFISH_BUDGET_DB_PATH') or _config('BUDGET_DB_PATH', fallback))
        self.defaults = defaults or _limits({key: _config('BUDGET_' + key.upper(), value) for key, value in asdict(BudgetLimits()).items()})
        if pricing is None:
            pricing = _config('BUDGET_MODEL_PRICING_JSON', '{}')
            if isinstance(pricing, str):
                try:
                    pricing = json.loads(pricing)
                except (ValueError, TypeError):
                    raise ValueError('BUDGET_MODEL_PRICING_JSON must contain valid JSON') from None
        self.pricing = _pricing(pricing)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS run_budgets (
                    run_id TEXT PRIMARY KEY, limits_json TEXT NOT NULL, pricing_json TEXT NOT NULL,
                    started_at REAL, active_since REAL, wall_elapsed REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'active', reason TEXT,
                    calls INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0, cost_nano INTEGER NOT NULL DEFAULT 0,
                    unpriced_calls INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS budget_reservations (
                    reservation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, model TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
                    cost_nano INTEGER NOT NULL, rates_json TEXT, state TEXT NOT NULL DEFAULT 'reserved'
                );
                CREATE INDEX IF NOT EXISTS budget_reservation_run ON budget_reservations(run_id);
            ''')

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=30000')
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self):
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _ensure(self, db, run_id):
        validate_storage_id(run_id, 'run_id')
        db.execute('INSERT OR IGNORE INTO run_budgets(run_id,limits_json,pricing_json) VALUES (?,?,?)', (run_id, json.dumps(asdict(self.defaults)), json.dumps(self.pricing)))
        return db.execute('SELECT * FROM run_budgets WHERE run_id=?', (run_id,)).fetchone()

    def _expiry(self, db, row):
        reason = row['reason'] if row['status'] == 'budget_exceeded' else None
        if reason is None and self._elapsed(row) >= json.loads(row['limits_json'])['max_wall_seconds']:
            reason = 'wall_time'
            db.execute("UPDATE run_budgets SET status='budget_exceeded', reason=? WHERE run_id=?", (reason, row['run_id']))
        return reason

    @staticmethod
    def _elapsed(row):
        return row['wall_elapsed'] + (max(0, time.time() - row['active_since']) if row['active_since'] is not None else 0)

    def configure(self, run_id, limits: dict):
        with self._transaction() as db:
            row = self._ensure(db, run_id)
            previous = json.loads(row['limits_json'])
            updated = dict(previous)
            if not isinstance(limits, dict):
                raise ValueError('Budget limits must be an object')
            updated.update(limits)
            validated = _limits(updated)
            if row['started_at'] is not None:
                for key, value in updated.items():
                    old = previous[key]
                    if key == 'max_cost_usd':
                        if old is None and value is not None or old is not None and value is not None and value < old:
                            raise ValueError('Started run limits may only be increased')
                    elif value < old:
                        raise ValueError('Started run limits may only be increased')
            reopening = updated != previous and row['status'] == 'budget_exceeded'
            db.execute('UPDATE run_budgets SET limits_json=?,status=?,reason=? WHERE run_id=?',
                       (json.dumps(asdict(validated)), 'active' if reopening else row['status'], None if reopening else row['reason'], run_id))
        return self.get(run_id)

    def start(self, run_id):
        with self._transaction() as db:
            row = self._ensure(db, run_id)
            reason = self._expiry(db, row)
            if not reason and row['started_at'] is None:
                db.execute('UPDATE run_budgets SET started_at=? WHERE run_id=?', (time.time(), run_id))
        if reason:
            raise BudgetExceeded(reason)
        return self.get(run_id)

    def check(self, run_id):
        with self._transaction() as db:
            row = self._ensure(db, run_id)
            reason = self._expiry(db, row)
        if reason:
            raise BudgetExceeded(reason)
        return self.get(run_id)

    def get(self, run_id):
        with self._transaction() as db:
            row = self._ensure(db, run_id)
            self._expiry(db, row)
            row = db.execute('SELECT * FROM run_budgets WHERE run_id=?', (run_id,)).fetchone()
            limits = json.loads(row['limits_json'])
            return {
                'run_id': run_id, 'status': row['status'], 'reason': row['reason'], 'limits': limits,
                'started_at': row['started_at'],
                'deadline_at': time.time() + max(0, limits['max_wall_seconds'] - self._elapsed(row)) if row['active_since'] is not None else None,
                'wall_time_semantics': 'cumulative_active_model_time',
                'usage': {'calls': row['calls'], 'tokens': row['input_tokens'] + row['output_tokens'],
                          'input_tokens': row['input_tokens'], 'output_tokens': row['output_tokens'], 'wall_seconds': self._elapsed(row),
                          'estimated_cost_usd': None if row['unpriced_calls'] or not json.loads(row['pricing_json']) else row['cost_nano'] / 1e9},
                'pricing_configured_models': sorted(json.loads(row['pricing_json'])),
            }

    def reserve(self, run_id, model, input_tokens, output_tokens):
        if type(input_tokens) is not int or type(output_tokens) is not int or input_tokens < 0 or output_tokens < 1:
            raise ValueError('Token reservations must use nonnegative input and positive output integers')
        with self._transaction() as db:
            row = self._ensure(db, run_id)
            limits = json.loads(row['limits_json'])
            reason = self._expiry(db, row)
            rates = json.loads(row['pricing_json']).get(str(model))
            cost = _cost_nano(rates, input_tokens, output_tokens)
            if not reason:
                checks = [(row['calls'] + 1 > limits['max_calls'], 'calls'),
                          (row['input_tokens'] + row['output_tokens'] + input_tokens + output_tokens > limits['max_tokens'], 'tokens'),
                          (row['output_tokens'] + output_tokens > limits['max_output_tokens'], 'output_tokens'),
                          (limits['max_cost_usd'] is not None and rates is None, 'pricing_unavailable'),
                          (limits['max_cost_usd'] is not None and row['cost_nano'] + cost > Decimal(str(limits['max_cost_usd'])) * 10**9, 'cost')]
                reason = next((why for hit, why in checks if hit), None)
            if reason:
                db.execute("UPDATE run_budgets SET status='budget_exceeded',reason=? WHERE run_id=?", (reason, run_id))
            else:
                started = row['started_at'] if row['started_at'] is not None else time.time()
                reservation = Reservation(uuid.uuid4().hex, run_id, self.db_path, time.time() + limits['max_wall_seconds'] - self._elapsed(row))
                db.execute('UPDATE run_budgets SET started_at=?,active_since=COALESCE(active_since,?),calls=calls+1,input_tokens=input_tokens+?,output_tokens=output_tokens+?,cost_nano=cost_nano+?,unpriced_calls=unpriced_calls+? WHERE run_id=?', (started, time.time(), input_tokens, output_tokens, cost, int(rates is None), run_id))
                db.execute('INSERT INTO budget_reservations(reservation_id,run_id,model,input_tokens,output_tokens,cost_nano,rates_json) VALUES (?,?,?,?,?,?,?)', (reservation.reservation_id, run_id, str(model), input_tokens, output_tokens, cost, json.dumps(rates) if rates is not None else None))
        if reason:
            raise BudgetExceeded(reason)
        return reservation

    def settle(self, reservation, response=None, *, failed=False):
        usage = getattr(response, 'usage', None) if not isinstance(response, dict) else response.get('usage')
        value = lambda key: usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
        actual_input, actual_output = value('prompt_tokens'), value('completion_tokens')
        known = not failed and type(actual_input) is int and type(actual_output) is int and actual_input >= 0 and actual_output >= 0
        exceeded = None
        with self._transaction() as db:
            row = db.execute('SELECT * FROM budget_reservations WHERE reservation_id=?', (reservation.reservation_id,)).fetchone()
            if row is None or row['state'] != 'reserved':
                return
            if known:
                rates = json.loads(row['rates_json']) if row['rates_json'] else None
                cost = _cost_nano(rates, actual_input, actual_output)
                db.execute('UPDATE run_budgets SET input_tokens=input_tokens+?,output_tokens=output_tokens+?,cost_nano=cost_nano+? WHERE run_id=?', (actual_input-row['input_tokens'], actual_output-row['output_tokens'], cost-row['cost_nano'], row['run_id']))
                if actual_input > row['input_tokens'] or actual_output > row['output_tokens']:
                    exceeded = 'provider_reservation_overrun'
                    db.execute("UPDATE run_budgets SET status='budget_exceeded',reason=? WHERE run_id=?", (exceeded, row['run_id']))
            db.execute('UPDATE budget_reservations SET state=? WHERE reservation_id=?', ('settled' if known else 'conservative', reservation.reservation_id))
            budget = db.execute('SELECT * FROM run_budgets WHERE run_id=?', (row['run_id'],)).fetchone()
            exceeded = exceeded or self._expiry(db, budget)
            pending = db.execute("SELECT 1 FROM budget_reservations WHERE run_id=? AND state='reserved' LIMIT 1", (row['run_id'],)).fetchone()
            if pending is None:
                db.execute('UPDATE run_budgets SET wall_elapsed=?,active_since=NULL WHERE run_id=?', (self._elapsed(budget), row['run_id']))
        if exceeded:
            raise BudgetExceeded(exceeded)


@dataclass(frozen=True)
class BudgetBinding:
    run_id: str
    db_path: str
    failure_observer: dict | None = field(default=None, compare=False, repr=False)

    def snapshot(self):
        return BudgetStore(self.db_path).get(self.run_id)


_active_budget: ContextVar[BudgetBinding | None] = ContextVar('mirofish_budget', default=None)


def current_budget():
    current = _active_budget.get()
    if current is not None:
        return current
    run_id = os.environ.get('MIROFISH_BUDGET_RUN_ID')
    if run_id:
        validate_storage_id(run_id, 'run_id')
        return BudgetBinding(run_id, BudgetStore().db_path)
    return None


class BudgetContext:
    def __init__(self, run_id, db_path=None):
        self.store = BudgetStore(db_path)
        self.binding = BudgetBinding(validate_storage_id(run_id, 'run_id'), self.store.db_path, _request_failure.get())

    def __enter__(self):
        self.store.start(self.binding.run_id)
        self.token = _active_budget.set(self.binding)
        return self.binding

    def __exit__(self, *exc):
        _active_budget.reset(self.token)


def budget_environment(run_id=None):
    binding = current_budget()
    if run_id is None and binding is None:
        return {}
    run_id = validate_storage_id(run_id or binding.run_id, 'run_id')
    return {'MIROFISH_BUDGET_RUN_ID': run_id, 'MIROFISH_BUDGET_DB_PATH': binding.db_path if binding else BudgetStore().db_path}


def copy_budget_context(fn):
    context = copy_context()
    @wraps(fn)
    def run(*args, **kwargs):
        return context.copy().run(fn, *args, **kwargs)
    return run


def bind_budget_client(client):
    client._mirofish_budget = current_budget()
    return client


def _request_reservation(client, kwargs):
    binding = current_budget() or getattr(client, '_mirofish_budget', None)
    if binding is None:
        return None, None, dict(kwargs)
    kwargs = dict(kwargs)
    if kwargs.get('stream'):
        raise ValueError('Streaming model requests are not supported for budgeted runs')
    if kwargs.get('n', 1) != 1:
        raise ValueError('Budgeted requests require exactly one completion')
    overrides = kwargs.get('extra_body') or {}
    if not isinstance(overrides, dict) or set(overrides).intersection({'model', 'messages', 'tools', 'max_tokens', 'max_completion_tokens', 'n', 'stream'}):
        raise ValueError('Extra request fields cannot override budgeted parameters')
    for message in kwargs.get('messages', []):
        content = message.get('content') if isinstance(message, dict) else None
        if isinstance(content, list) and any(isinstance(part, dict) and part.get('type') not in {'text', 'input_text'} for part in content):
            raise ValueError('Only text model inputs are supported for budgeted runs')
    # Keep the request finite even when a JSON regeneration omitted its cap.
    cap_key = 'max_completion_tokens' if str(kwargs['model']).lower().startswith('gpt-5') else 'max_tokens'
    if kwargs.get('max_completion_tokens') is not None:
        cap_key = 'max_completion_tokens'
    cap = kwargs.get(cap_key)
    if cap is None:
        cap = 4096
    if type(cap) is not int or cap < 1:
        raise ValueError('Output token limit must be a positive integer')
    kwargs[cap_key] = cap
    # JSON includes tool schema, names, all role metadata and escaped characters.
    # Additional framing allowance avoids pretending character/4 is a hard bound.
    prompt = {key: value for key, value in kwargs.items() if key in {'messages', 'tools', 'response_format', 'tool_choice'}}
    prompt_bytes = json.dumps(prompt, ensure_ascii=True, default=lambda value: value.model_json_schema() if hasattr(value, 'model_json_schema') else str(value)).encode('utf-8')
    input_tokens = len(prompt_bytes) + 256 + 64 * len(kwargs.get('messages', []))
    store = BudgetStore(binding.db_path)
    return store, store.reserve(binding.run_id, str(kwargs['model']), input_tokens, cap), kwargs


def _bounded_client(client, reservation):
    if reservation is None or not hasattr(client, 'with_options'):
        return client
    remaining = max(0.001, reservation.deadline_at - time.time())
    return client.with_options(max_retries=0, timeout=min(180, remaining))


def _mark_client_failure(client):
    binding = current_budget() or getattr(client, '_mirofish_budget', None)
    if binding and binding.failure_observer is not None:
        binding.failure_observer['exceeded'] = True


@contextmanager
def observe_budget_failures():
    observer = {'exceeded': False}
    token = _request_failure.set(observer)
    try:
        yield observer
    finally:
        _request_failure.reset(token)


def _observe_sync_failure(fn):
    @wraps(fn)
    def call(client, **kwargs):
        try:
            return fn(client, **kwargs)
        except BudgetExceeded:
            _mark_client_failure(client)
            raise
    return call


def _observe_async_failure(fn):
    @wraps(fn)
    async def call(client, **kwargs):
        try:
            return await fn(client, **kwargs)
        except BudgetExceeded:
            _mark_client_failure(client)
            raise
    return call


@_observe_sync_failure
def budgeted_chat_completion(client, *, sdk_operation='create', **kwargs):
    store, reservation, kwargs = _request_reservation(client, kwargs)
    try:
        bounded = _bounded_client(client, reservation)
        operation = bounded.beta.chat.completions.parse if sdk_operation == 'parse' else bounded.chat.completions.create
        if reservation:
            # SDK timeouts bound socket operations, not total wall time. A daemon
            # transport thread lets the job return at its absolute deadline.
            # Remote work can still finish; its full reservation stays charged.
            future = Future()
            context = copy_context()
            def send():
                try:
                    future.set_result(context.run(operation, **kwargs))
                except BaseException as error:
                    future.set_exception(error)
            threading.Thread(target=send, name='budgeted-model-request', daemon=True).start()
            try:
                response = future.result(timeout=max(0.001, reservation.deadline_at-time.time()))
            except FutureTimeout:
                if not future.done():
                    store.check(reservation.run_id)
                raise
        else:
            response = operation(**kwargs)
    except BaseException:
        if reservation:
            store.settle(reservation, failed=True)
        raise
    if reservation:
        store.settle(reservation, response)
        store.check(reservation.run_id)
    return response


@_observe_async_failure
async def budgeted_async_chat_completion(client, *, sdk_operation='create', **kwargs):
    store, reservation, kwargs = _request_reservation(client, kwargs)
    try:
        bounded = _bounded_client(client, reservation)
        operation = bounded.beta.chat.completions.parse if sdk_operation == 'parse' else bounded.chat.completions.create
        request = operation(**kwargs)
        response = await asyncio.wait_for(request, timeout=max(0.001, reservation.deadline_at-time.time())) if reservation else await request
    except asyncio.CancelledError:
        if reservation:
            try:
                store.settle(reservation, failed=True)
            except BudgetExceeded:
                pass
        raise
    except BaseException:
        if reservation:
            store.settle(reservation, failed=True)
            # Preserve cancellation. An actual deadline becomes the safe terminal error.
            if time.time() >= reservation.deadline_at:
                store.check(reservation.run_id)
        raise
    if reservation:
        store.settle(reservation, response)
        store.check(reservation.run_id)
    return response
