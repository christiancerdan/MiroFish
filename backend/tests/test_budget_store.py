"""Budget admission is shared by threads/processes and never refunds failures."""
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
from types import SimpleNamespace
import time

import pytest
from app.utils.budget import BudgetContext, BudgetExceeded, BudgetStore, current_budget


def _attempt(args):
    path, run_id = args
    try:
        BudgetStore(path).reserve(run_id, 'model', 10, 5)
        return True
    except BudgetExceeded:
        return False


def configured(tmp_path, **limits):
    store = BudgetStore(str(tmp_path / 'budget.sqlite3'))
    store.configure('project_a', {'max_calls': 4, 'max_tokens': 1000, 'max_output_tokens': 500, 'max_wall_seconds': 60, **limits})
    return store


def test_thread_admission_never_overspends(tmp_path):
    store = configured(tmp_path)
    with ThreadPoolExecutor(max_workers=12) as pool:
        accepted = list(pool.map(_attempt, [(store.db_path, 'project_a')] * 30))
    assert sum(accepted) == 4
    snapshot = store.get('project_a')
    assert snapshot['usage']['calls'] == 4
    assert snapshot['usage']['tokens'] == 60
    assert snapshot['status'] == 'budget_exceeded'


def test_process_admission_never_overspends(tmp_path):
    store = configured(tmp_path, max_calls=2)
    with multiprocessing.get_context('spawn').Pool(3) as pool:
        accepted = pool.map(_attempt, [(store.db_path, 'project_a')] * 9)
    assert sum(accepted) == 2
    assert store.get('project_a')['usage']['calls'] == 2


def test_failure_and_missing_usage_keep_reservations(tmp_path):
    store = configured(tmp_path)
    reservation = store.reserve('project_a', 'model', 10, 5)
    store.settle(reservation, failed=True)
    store.settle(reservation, response=SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1)))
    second = store.reserve('project_a', 'model', 20, 10)
    store.settle(second, response=SimpleNamespace())
    assert store.get('project_a')['usage']['tokens'] == 45
    assert store.get('project_a')['usage']['estimated_cost_usd'] is None


def test_actual_usage_refunds_only_successful_known_usage(tmp_path):
    store = configured(tmp_path)
    reservation = store.reserve('project_a', 'model', 100, 50)
    store.settle(reservation, response=SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5)))
    usage = store.get('project_a')['usage']
    assert (usage['calls'], usage['tokens'], usage['output_tokens']) == (1, 15, 5)


@pytest.mark.parametrize('limit, value', [('max_tokens', 14), ('max_output_tokens', 4)])
def test_token_caps_reject_before_admission(tmp_path, limit, value):
    store = configured(tmp_path, **{limit: value})
    with pytest.raises(BudgetExceeded):
        store.reserve('project_a', 'model', 10, 5)
    assert store.get('project_a')['usage']['calls'] == 0


def test_pricing_is_explicit_and_monetary_cap_requires_it(tmp_path):
    store = configured(tmp_path, max_cost_usd=0.01)
    with pytest.raises(BudgetExceeded):
        store.reserve('project_a', 'unpriced-model', 10, 5)
    assert store.get('project_a')['reason'] == 'pricing_unavailable'
    priced = BudgetStore(str(tmp_path / 'priced.sqlite3'), pricing={'model': {'input_per_million': 2, 'output_per_million': 4}})
    priced.configure('priced', {'max_cost_usd': 0.0001})
    item = priced.reserve('priced', 'model', 10, 5)
    priced.settle(item, failed=True)
    assert priced.get('priced')['usage']['estimated_cost_usd'] == pytest.approx(0.00004)
    with pytest.raises(BudgetExceeded):
        priced.reserve('priced', 'model', 100, 50)


def test_wall_deadline_and_context_reset(tmp_path, monkeypatch):
    store = configured(tmp_path, max_wall_seconds=1)
    with BudgetContext('project_a', db_path=store.db_path):
        assert current_budget().run_id == 'project_a'
        store.reserve('project_a', 'model', 10, 5)
        started = store.get('project_a')['deadline_at']
        monkeypatch.setattr(time, 'time', lambda: started + 1)
        with pytest.raises(BudgetExceeded):
            store.check('project_a')
    assert current_budget() is None
    assert store.get('project_a')['status'] == 'budget_exceeded'


def test_can_raise_but_not_reset_or_lower_started_run(tmp_path):
    store = configured(tmp_path)
    store.reserve('project_a', 'model', 10, 5)
    with pytest.raises(ValueError):
        store.configure('project_a', {'max_calls': 1})
    store.configure('project_a', {'max_calls': 1000})
    assert store.get('project_a')['usage']['calls'] == 1


def test_idle_stage_gaps_do_not_consume_wall_budget(tmp_path, monkeypatch):
    store = configured(tmp_path, max_wall_seconds=2)
    now = time.time()
    monkeypatch.setattr(time, 'time', lambda: now)
    reservation = store.reserve('project_a', 'model', 10, 5)
    store.settle(reservation, failed=True)
    monkeypatch.setattr(time, 'time', lambda: now + 86400)
    assert store.check('project_a')['status'] == 'active'
    assert store.get('project_a')['deadline_at'] is None


def test_increase_reopens_exhausted_run_without_erasing_usage(tmp_path):
    store = configured(tmp_path, max_calls=1)
    reservation = store.reserve('project_a', 'model', 10, 5)
    store.settle(reservation, failed=True)
    with pytest.raises(BudgetExceeded):
        store.reserve('project_a', 'model', 10, 5)
    snapshot = store.configure('project_a', {'max_calls': 2})
    assert snapshot['usage']['calls'] == 1
    assert snapshot['status'] == 'active'
    store.reserve('project_a', 'model', 10, 5)
