"""Exercise admission at the real SDK boundary without external traffic."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI, AsyncOpenAI, APIStatusError

from app.utils.budget import (
    BudgetContext, BudgetExceeded, BudgetStore, bind_budget_client,
    budgeted_chat_completion, budgeted_async_chat_completion,
)
from app.utils.openai_chat_compat import create_chat_completion


def store_at(tmp_path, max_calls=2):
    store = BudgetStore(str(tmp_path / 'calls.sqlite3'))
    store.configure('project', {'max_calls': max_calls, 'max_tokens': 100000, 'max_output_tokens': 20000})
    return store


def completion():
    return {'id': 'test', 'object': 'chat.completion', 'created': 1, 'model': 'model', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'ok'}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12}}


def test_generic_client_capture_propagates_to_threads(tmp_path):
    store = store_at(tmp_path, max_calls=1)
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=completion())
    with OpenAI(api_key='test', http_client=httpx.Client(transport=httpx.MockTransport(respond))) as client:
        with BudgetContext('project', db_path=store.db_path):
            bind_budget_client(client)
        def call():
            return create_chat_completion(client, model='model', messages=[{'role': 'user', 'content': 'hello'}])
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(call).result().choices[0].message.content == 'ok'
            with pytest.raises(BudgetExceeded):
                executor.submit(call).result()
    assert len(requests) == 1
    assert json.loads(requests[0].content)['max_tokens'] == 4096
    assert store.get('project')['usage']['tokens'] == 12


def test_sdk_retries_disabled_and_failure_retains_reservation(tmp_path):
    store = store_at(tmp_path)
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(500, json={'error': {'message': 'synthetic'}})
    with OpenAI(api_key='test', max_retries=4, http_client=httpx.Client(transport=httpx.MockTransport(respond))) as client:
        with BudgetContext('project', db_path=store.db_path):
            with pytest.raises(APIStatusError):
                create_chat_completion(client, model='model', messages=[], max_tokens=20)
    assert len(requests) == 1
    usage = store.get('project')['usage']
    assert usage['calls'] == 1
    assert usage['output_tokens'] == 20
    assert usage['tokens'] > 20


def test_async_cancellation_keeps_reservation_and_stops_active_timer(tmp_path):
    store = store_at(tmp_path)
    started = asyncio.Event()
    async def run():
        async def respond(request):
            started.set()
            await asyncio.sleep(100)
        async with AsyncOpenAI(api_key='test', http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))) as client:
            with BudgetContext('project', db_path=store.db_path):
                task = asyncio.create_task(budgeted_async_chat_completion(client, model='model', messages=[], max_tokens=20))
                await started.wait()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
    asyncio.run(run())
    snapshot = store.get('project')
    assert snapshot['usage']['calls'] == 1
    assert snapshot['usage']['output_tokens'] == 20
    assert snapshot['deadline_at'] is None


def test_async_wall_deadline_is_terminal_budget_error(tmp_path):
    store = store_at(tmp_path)
    store.configure('project', {'max_wall_seconds': 1})
    async def run():
        async def respond(request):
            await asyncio.sleep(10)
        async with AsyncOpenAI(api_key='test', http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))) as client:
            with BudgetContext('project', db_path=store.db_path):
                with pytest.raises(BudgetExceeded):
                    await budgeted_async_chat_completion(client, model='model', messages=[], max_tokens=20)
    asyncio.run(run())
    assert store.get('project')['reason'] == 'wall_time'


def test_provider_usage_overrun_closes_run_and_records_truth(tmp_path):
    store = store_at(tmp_path)
    reservation = store.reserve('project', 'model', 1, 1)
    with pytest.raises(BudgetExceeded):
        store.settle(reservation, completion())
    assert store.get('project')['usage']['tokens'] == 12
    assert store.get('project')['reason'] == 'provider_reservation_overrun'


def test_sync_wall_deadline_returns_even_if_transport_ignores_socket_timeout(tmp_path):
    import threading
    import time
    store = store_at(tmp_path)
    store.configure('project', {'max_wall_seconds': 1})
    release = threading.Event()
    def respond(request):
        release.wait(5)
        return httpx.Response(200, json=completion())
    try:
        with OpenAI(api_key='test', http_client=httpx.Client(transport=httpx.MockTransport(respond))) as client:
            with BudgetContext('project', db_path=store.db_path):
                began = time.monotonic()
                with pytest.raises(BudgetExceeded):
                    budgeted_chat_completion(client, model='model', messages=[], max_tokens=20)
                assert time.monotonic() - began < 2
        assert store.get('project')['reason'] == 'wall_time'
        assert store.get('project')['usage']['output_tokens'] == 20
    finally:
        release.set()


@pytest.mark.parametrize('extra', [{'stream': True}, {'n': 2}, {'extra_body': {'max_tokens': 999999}}, {'messages': [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'https://example.invalid/image'}}]}]}])
def test_unsupported_shapes_cannot_bypass_reservations(tmp_path, extra):
    store = store_at(tmp_path)
    requests = []
    with OpenAI(api_key='test', http_client=httpx.Client(transport=httpx.MockTransport(lambda request: requests.append(request)))) as client:
        with BudgetContext('project', db_path=store.db_path):
            with pytest.raises(ValueError):
                budgeted_chat_completion(client, **{'model': 'model', 'messages': [], 'max_tokens': 10, **extra})
    assert not requests
    assert store.get('project')['usage']['calls'] == 0
