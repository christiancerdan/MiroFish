"""Provider contracts: real SDK requests, with only HTTP replaced locally."""

import json

import httpx
import pytest
from openai import OpenAI

from app.utils.llm_provider import resolve_llm_settings, settings_from_config
from app.utils.llm_client import LLMClient


def test_cloud_requires_explicit_model_and_uses_ollama_key_alias():
    env = {"LLM_PROVIDER": "ollama_cloud", "OLLAMA_API_KEY": "cloud-secret"}
    with pytest.raises(ValueError, match="LLM_MODEL_NAME"):
        resolve_llm_settings(env)
    env["LLM_MODEL_NAME"] = "gpt-oss:120b"
    settings = resolve_llm_settings(env)
    assert settings.api_key == "cloud-secret"
    assert settings.base_url == "https://ollama.com/v1"
    assert settings.model == "gpt-oss:120b"
    assert "cloud-secret" not in repr(settings)


def test_explicit_llm_key_wins_and_openai_compatible_defaults_remain():
    settings = resolve_llm_settings({
        "LLM_PROVIDER": "ollama_cloud", "OLLAMA_API_KEY": "alias-secret",
        "LLM_API_KEY": "explicit-secret", "LLM_MODEL_NAME": "arbitrary:model",
    })
    assert settings.api_key == "explicit-secret"
    legacy = resolve_llm_settings({"LLM_API_KEY": "legacy-secret"})
    assert (legacy.base_url, legacy.model) == ("https://api.openai.com/v1", "gpt-4o-mini")


@pytest.mark.parametrize("url", ["https://api.openai.com/v1", "http://ollama.com/v1", "https://ollama.com/api", "https://secret@ollama.com/v1", "https://ollama.com/v1?key=secret"])
def test_cloud_rejects_wrong_endpoint_without_leaking_its_value(url):
    with pytest.raises(ValueError) as error:
        resolve_llm_settings({"LLM_PROVIDER": "ollama_cloud", "OLLAMA_API_KEY": "private-key", "LLM_MODEL_NAME": "gpt-oss:120b", "LLM_BASE_URL": url})
    assert "secret" not in str(error.value)
    assert "private-key" not in str(error.value)


def test_copy_env_boost_placeholders_do_not_enable_second_provider():
    settings = resolve_llm_settings({
        "LLM_API_KEY": "primary-secret",
        "LLM_BOOST_API_KEY": "your_api_key_here",
        "LLM_BOOST_BASE_URL": "your_base_url_here",
        "LLM_BOOST_MODEL_NAME": "your_model_name_here",
    }, use_boost=True)
    assert settings.api_key == "primary-secret"
    assert settings.is_boost is False


def test_partial_real_boost_fails_without_disclosing_key():
    with pytest.raises(ValueError, match="LLM_BOOST_BASE_URL") as error:
        resolve_llm_settings({"LLM_API_KEY": "primary", "LLM_BOOST_API_KEY": "boost-secret"}, use_boost=True)
    assert "boost-secret" not in str(error.value)


def test_invalid_context_limit_is_rejected_before_network_access():
    for limit in ["0", "-4", "large"]:
        with pytest.raises(ValueError, match="LLM_TOKEN_LIMIT"):
            resolve_llm_settings({"LLM_API_KEY": "key", "LLM_TOKEN_LIMIT": limit})


def test_config_overrides_are_resolved_together_and_keep_cloud_alias():
    class Config:
        LLM_PROVIDER = "ollama_cloud"
        LLM_API_KEY = None
        OLLAMA_API_KEY = "cloud-secret"
        LLM_MODEL_NAME = "gpt-oss:120b"
        LLM_BASE_URL = "https://ollama.com/v1"
        LLM_TOKEN_LIMIT = 32768

    settings = settings_from_config(Config, model="different:cloud")
    assert settings.model == "different:cloud"
    assert settings.api_key == "cloud-secret"


def test_llm_client_cloud_json_request_uses_bearer_auth_and_arbitrary_model(monkeypatch):
    import app.utils.llm_client as module
    from app.config import Config

    for field, value in {"LLM_PROVIDER": "ollama_cloud", "LLM_API_KEY": "wire-secret", "LLM_BASE_URL": "https://ollama.com/v1", "LLM_MODEL_NAME": "gpt-oss:120b"}.items():
        monkeypatch.setattr(Config, field, value, raising=False)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "chatcmpl-test", "object": "chat.completion", "created": 1, "model": "gpt-oss:120b", "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": '{"ok": true}'}}]})

    transport_client = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(module, "OpenAI", lambda **kwargs: OpenAI(**kwargs, http_client=transport_client))
    client = LLMClient()
    try:
        assert client.chat_json([{"role": "user", "content": "Return JSON"}]) == {"ok": True}
        assert len(requests) == 1
        assert str(requests[0].url) == "https://ollama.com/v1/chat/completions"
        assert requests[0].headers["authorization"] == "Bearer wire-secret"
        payload = json.loads(requests[0].content)
        assert payload["model"] == "gpt-oss:120b"
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["max_tokens"] == 4096
    finally:
        client.client.close()
