"""Contract tests against the pinned CAMEL backend, without external requests."""

import asyncio
import importlib
import json
import os

import httpx
import pytest

if importlib.util.find_spec("camel") is None:
    pytest.skip("Full simulation dependencies are not installed", allow_module_level=True)

from app.utils.llm_provider import create_camel_model, resolve_llm_settings


TOOL = {"type": "function", "function": {"name": "record_choice", "description": "Record a choice", "parameters": {"type": "object", "properties": {"choice": {"type": "string"}}, "required": ["choice"]}}}


@pytest.mark.parametrize("asynchronous", [False, True])
def test_camel_arbitrary_models_send_tools_with_independent_auth(monkeypatch, asynchronous):
    requests = []

    def response(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "chatcmpl-test", "object": "chat.completion", "created": 1, "model": "gpt-oss:120b", "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "record_choice", "arguments": '{"choice":"yes"}'}}]}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda self, request: response(request))

    async def async_response(self, request):
        return response(request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", async_response)
    monkeypatch.setenv("OPENAI_API_KEY", "global-sentinel")
    monkeypatch.setenv("OPENAI_API_BASE_URL", "https://sentinel.invalid/v1")
    before = dict(os.environ)
    env = {"LLM_PROVIDER": "ollama_cloud", "OLLAMA_API_KEY": "cloud-secret", "LLM_MODEL_NAME": "gpt-oss:120b", "LLM_BOOST_PROVIDER": "openai_compatible", "LLM_BOOST_API_KEY": "boost-secret", "LLM_BOOST_BASE_URL": "https://second.invalid/v1", "LLM_BOOST_MODEL_NAME": "arbitrary:second"}
    primary = create_camel_model(settings=resolve_llm_settings(env))
    boost = create_camel_model(settings=resolve_llm_settings(env, use_boost=True))
    messages = [{"role": "user", "content": "Record yes"}]
    try:
        for model in (primary, boost, primary):
            result = asyncio.run(model.arun(messages, tools=[TOOL])) if asynchronous else model.run(messages, tools=[TOOL])
            assert result.choices[0].message.tool_calls[0].function.name == "record_choice"
            assert model.token_limit == 32768
        assert dict(os.environ) == before
        assert [request.headers["authorization"] for request in requests] == ["Bearer cloud-secret", "Bearer boost-secret", "Bearer cloud-secret"]
        assert [str(request.url) for request in requests] == ["https://ollama.com/v1/chat/completions", "https://second.invalid/v1/chat/completions", "https://ollama.com/v1/chat/completions"]
        assert [json.loads(request.content)["model"] for request in requests] == ["gpt-oss:120b", "arbitrary:second", "gpt-oss:120b"]
        for request in requests:
            payload = json.loads(request.content)
            assert payload["tools"] == [TOOL]
            assert payload["max_tokens"] == 4096
            assert "response_format" not in payload
    finally:
        for model in (primary, boost):
            model._client.close()
            asyncio.run(model._async_client.close())


def test_camel_gpt5_keeps_bounded_memory_with_completion_token_cap():
    model = create_camel_model(settings=resolve_llm_settings({"LLM_API_KEY": "test", "LLM_MODEL_NAME": "gpt-5", "LLM_TOKEN_LIMIT": "16000"}))
    try:
        assert model.token_limit == 16000
        assert model.model_config_dict["max_completion_tokens"] == 4096
        assert "max_tokens" not in model.model_config_dict
        assert "temperature" not in model.model_config_dict
    finally:
        model._client.close()
        asyncio.run(model._async_client.close())


@pytest.mark.parametrize(("module_name", "runner_name"), [
    ("scripts.run_parallel_simulation", None),
    ("scripts.run_twitter_simulation", "TwitterSimulationRunner"),
    ("scripts.run_reddit_simulation", "RedditSimulationRunner"),
])
def test_each_simulation_entrypoint_sends_local_cloud_alias_to_configured_endpoint(monkeypatch, module_name, runner_name):
    if importlib.util.find_spec("oasis") is None:
        pytest.skip("OASIS is not installed")
    module = importlib.import_module(module_name)
    for key, value in {"LLM_PROVIDER": "openai_compatible", "LLM_API_KEY": "ollama", "LLM_BASE_URL": "http://127.0.0.1:11434/v1", "LLM_MODEL_NAME": "gpt-oss:20b-cloud", "LLM_BOOST_API_KEY": "your_api_key_here", "LLM_BOOST_BASE_URL": "your_base_url_here", "LLM_BOOST_MODEL_NAME": "your_model_name_here"}.items():
        monkeypatch.setenv(key, value)
    requests = []

    def respond(self, request):
        requests.append(request)
        return httpx.Response(200, json={"id": "chatcmpl-test", "object": "chat.completion", "created": 1, "model": "gpt-oss:20b-cloud", "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "OK"}}]})

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", respond)
    if runner_name:
        runner = object.__new__(getattr(module, runner_name))
        runner.config = {}
        model = runner._create_model()
    else:
        model = module.create_model({}, use_boost=True)
    try:
        assert model.run([{"role": "user", "content": "OK"}]).choices[0].message.content == "OK"
        assert requests[0].headers["authorization"] == "Bearer ollama"
        assert str(requests[0].url) == "http://127.0.0.1:11434/v1/chat/completions"
        assert json.loads(requests[0].content)["model"] == "gpt-oss:20b-cloud"
    finally:
        model._client.close()
        asyncio.run(model._async_client.close())
