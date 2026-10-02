import importlib.util
import json
from pathlib import Path

import httpx

from app.utils.llm_provider import resolve_llm_settings


_SPEC = importlib.util.spec_from_file_location("provider_check", Path(__file__).resolve().parents[1] / "scripts" / "check_llm_provider.py")
probe = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(probe)


def test_probe_exercises_text_json_and_tools_over_actual_sdk(monkeypatch):
    requests = []

    def respond(self, request):
        requests.append(request)
        payload = json.loads(request.content)
        message = {"role": "assistant", "content": "OK"}
        reason = "stop"
        if payload.get("response_format"):
            message["content"] = '{"ok":true}'
        if payload.get("tools"):
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "call_probe", "type": "function", "function": {"name": "record_probe", "arguments": '{"code":"mirofish"}'}}]}
            reason = "tool_calls"
        return httpx.Response(200, json={"id": "chatcmpl-probe", "object": "chat.completion", "created": 1, "model": "gpt-oss:120b", "choices": [{"index": 0, "finish_reason": reason, "message": message}]})

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", respond)
    settings = resolve_llm_settings({"LLM_PROVIDER": "ollama_cloud", "OLLAMA_API_KEY": "test-secret", "LLM_MODEL_NAME": "gpt-oss:120b"})
    assert probe.run_checks(settings, include_camel=False) == ["text", "json", "tools"]
    assert len(requests) == 3
    assert all(request.headers["authorization"] == "Bearer test-secret" for request in requests)


def test_probe_failure_does_not_print_provider_response_secrets(monkeypatch, capsys):
    monkeypatch.setenv("LLM_PROVIDER", "ollama_cloud")
    monkeypatch.setenv("LLM_API_KEY", "private-api-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://ollama.com/v1")
    monkeypatch.setenv("LLM_MODEL_NAME", "gpt-oss:120b")

    def reject(self, request):
        return httpx.Response(401, json={"error": {"message": "private-api-key was rejected; secret-provider-body", "type": "authentication_error"}})

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", reject)
    assert probe.main(["--skip-camel"]) == 1
    output = capsys.readouterr().out
    assert "private-api-key" not in output
    assert "secret-provider-body" not in output
    assert json.loads(output) == {"ok": False, "error_type": "AuthenticationError", "http_status": 401}


def test_model_discovery_does_not_require_model_selection(monkeypatch, capsys):
    monkeypatch.setenv("LLM_PROVIDER", "ollama_cloud")
    monkeypatch.setenv("LLM_API_KEY", "private-api-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://ollama.com/v1")
    monkeypatch.delenv("LLM_MODEL_NAME", raising=False)

    def respond(self, request):
        assert str(request.url) == "https://ollama.com/v1/models"
        return httpx.Response(200, json={"object": "list", "data": [{"id": "cloud:model", "object": "model", "created": 0, "owned_by": "ollama"}]})

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", respond)
    assert probe.main(["--list-models"]) == 0
    assert json.loads(capsys.readouterr().out)["models"] == ["cloud:model"]
