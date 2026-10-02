"""Cancellation checkpoints stop unstarted profiles and do not undo paid work."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from types import SimpleNamespace

import pytest

from app.models.task import JobCancelled, JobLeaseLost
from app.services.oasis_profile_generator import OasisAgentProfile, OasisProfileGenerator
from app.services.zep_entity_reader import EntityNode
from app.utils.budget import BudgetExceeded


def entities(count=4):
    return [EntityNode(f'id_{index}', f'Person {index}', ['Person'], 'Summary', {}) for index in range(count)]


def profile(entity, user_id, **kwargs):
    return OasisAgentProfile(user_id=user_id, user_name=entity.name, name=entity.name, bio='Bio', persona='Persona')


def generator(monkeypatch):
    result = object.__new__(OasisProfileGenerator)
    monkeypatch.setattr(result, '_print_generated_profile', lambda *args: None)
    return result


def test_cancel_before_first_profile_admits_no_paid_work(monkeypatch):
    instance = generator(monkeypatch)
    calls = []
    monkeypatch.setattr(instance, 'generate_profile_from_entity', lambda **kwargs: calls.append(kwargs) or profile(**kwargs))
    def cancelled(*args):
        raise JobCancelled('cancel requested')
    with pytest.raises(JobCancelled):
        instance.generate_profiles_from_entities(entities(), parallel_count=1, progress_callback=cancelled)
    assert calls == []


@pytest.mark.parametrize('stop_error', [JobCancelled, JobLeaseLost, BudgetExceeded])
def test_completion_checkpoint_stops_backlog_before_save(monkeypatch, tmp_path, stop_error):
    instance = generator(monkeypatch)
    calls = []
    monkeypatch.setattr(instance, 'generate_profile_from_entity', lambda **kwargs: calls.append(kwargs['user_id']) or profile(**kwargs))
    target = tmp_path / 'profiles.json'
    def stop_after_first(current, total, message):
        if current == 1:
            raise stop_error('stop requested')
    with pytest.raises(stop_error):
        instance.generate_profiles_from_entities(entities(), parallel_count=1, progress_callback=stop_after_first, realtime_output_path=str(target))
    assert calls == [0]
    assert not target.exists()


def test_running_profile_is_joined_but_backlog_is_never_submitted(monkeypatch):
    instance = generator(monkeypatch)
    second_started, release_second, stopped, finished_second = Event(), Event(), Event(), Event()
    calls, call_lock = [], Lock()
    def generate(**kwargs):
        index = kwargs['user_id']
        with call_lock:
            calls.append(index)
        if index == 0:
            assert second_started.wait(2)
        if index == 1:
            second_started.set()
            assert release_second.wait(2)
            finished_second.set()
        return profile(**kwargs)
    monkeypatch.setattr(instance, 'generate_profile_from_entity', generate)
    def checkpoint(current, total, message):
        if current == 1:
            stopped.set()
            raise JobCancelled('cancel requested')
    with ThreadPoolExecutor(max_workers=1) as control:
        result = control.submit(instance.generate_profiles_from_entities, entities(), parallel_count=2, progress_callback=checkpoint)
        try:
            assert stopped.wait(2)
            assert not result.done()  # In-flight work is joined, not claimed undone.
            assert sorted(calls) == [0, 1]
        finally:
            release_second.set()
        with pytest.raises(JobCancelled):
            result.result(timeout=2)
    assert finished_second.is_set()
    assert sorted(calls) == [0, 1]


def test_inflight_failure_does_not_retry_after_batch_cancel(monkeypatch):
    from app.services import oasis_profile_generator as module
    instance = generator(monkeypatch)
    instance.client, instance.model_name = SimpleNamespace(), 'model'
    paid_started, release_paid, stopped = Event(), Event(), Event()
    paid_calls = []
    def paid(**kwargs):
        paid_calls.append(True)
        paid_started.set()
        assert release_paid.wait(3)
        raise RuntimeError('temporary provider failure')
    monkeypatch.setattr(module, 'create_chat_completion', lambda client, **kwargs: paid(**kwargs))
    def generate(**kwargs):
        if kwargs['user_id'] == 0:
            assert paid_started.wait(3)
            return profile(**kwargs)
        instance._generate_profile_with_llm('Person', 'Person', 'Summary', {}, 'Context')
        return profile(**kwargs)
    monkeypatch.setattr(instance, 'generate_profile_from_entity', generate)
    def checkpoint(current, total, message):
        if current == 1:
            stopped.set()
            raise JobCancelled('stop before retry')
    with ThreadPoolExecutor(max_workers=1) as control:
        result = control.submit(instance.generate_profiles_from_entities, entities(), parallel_count=2, progress_callback=checkpoint)
        try:
            assert stopped.wait(3)
        finally:
            release_paid.set()
        with pytest.raises(JobCancelled):
            result.result(timeout=3)
    assert len(paid_calls) == 1


def test_worker_context_checks_persisted_cancellation_before_paid_retry(monkeypatch, tmp_path):
    from app.models.task import TaskManager
    from app.services import oasis_profile_generator as module
    jobs = TaskManager(str(tmp_path / 'jobs.sqlite3'))
    task_id = jobs.enqueue('prepare', 'prepare', {})
    jobs.claim_next('owner', 60)
    instance = generator(monkeypatch)
    instance.client, instance.model_name = SimpleNamespace(), 'model'
    paid_calls = []
    def paid(client, **kwargs):
        assert TaskManager() is jobs  # Thread worker inherited durable identity.
        paid_calls.append(True)
        # Independent writer succeeds: no SQLite publication transaction spans HTTP.
        TaskManager(jobs.db_path).cancel_task(task_id)
        raise RuntimeError('temporary provider failure')
    monkeypatch.setattr(module, 'create_chat_completion', paid)
    def generate(**kwargs):
        instance._generate_profile_with_llm('Person', 'Person', 'Summary', {}, 'Context')
        return profile(**kwargs)
    monkeypatch.setattr(instance, 'generate_profile_from_entity', generate)
    with jobs.execution(task_id, 'owner'), pytest.raises(JobCancelled):
        instance.generate_profiles_from_entities(entities(), parallel_count=1)
    assert len(paid_calls) == 1
