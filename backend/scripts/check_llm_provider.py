#!/usr/bin/env python3
"""Explicit, small synthetic provider probe. Never sends project documents."""

import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openai import OpenAI

from app.utils.llm_provider import create_camel_model, resolve_llm_settings
from app.utils.openai_chat_compat import create_chat_completion, extract_chat_completion_text


PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "record_probe",
        "description": "Record the synthetic provider probe code.",
        "parameters": {
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
            "additionalProperties": False,
        },
    },
}
TOOL_MESSAGES = [{"role": "user", "content": "Call record_probe exactly once with code equal to mirofish. Do not answer in prose."}]


def _check_tool_result(result):
    choices = getattr(result, "choices", None) or []
    calls = getattr(choices[0].message, "tool_calls", None) if choices else None
    if not calls or len(calls) != 1:
        raise ValueError("Expected one tool call")
    if calls[0].function.name != "record_probe" or json.loads(calls[0].function.arguments) != {"code": "mirofish"}:
        raise ValueError("Unexpected tool call")


def run_checks(settings, *, include_camel=True):
    """Exercise the actual SDK and simulation backend with synthetic prompts."""
    passed = []
    with OpenAI(api_key=settings.api_key, base_url=settings.base_url, timeout=90, max_retries=0) as client:
        text = create_chat_completion(client, model=settings.model, messages=[{"role": "user", "content": "Reply with the single word OK."}], max_tokens=2048)
        if not extract_chat_completion_text(text).strip():
            raise ValueError("Empty text response")
        passed.append("text")
        result = create_chat_completion(client, model=settings.model, messages=[{"role": "user", "content": 'Return only this JSON object: {"ok": true}'}], max_tokens=2048, response_format={"type": "json_object"})
        if json.loads(extract_chat_completion_text(result)) != {"ok": True}:
            raise ValueError("Unexpected JSON response")
        passed.append("json")
        _check_tool_result(create_chat_completion(client, model=settings.model, messages=TOOL_MESSAGES, max_tokens=2048, tools=[PROBE_TOOL]))
        passed.append("tools")
    if include_camel:
        model = create_camel_model(settings=settings)
        try:
            # OASIS executes the async CAMEL path, so check that exact path.
            async def check_camel():
                try:
                    _check_tool_result(await model.arun(TOOL_MESSAGES, tools=[PROBE_TOOL]))
                finally:
                    await model._async_client.close()
            asyncio.run(check_camel())
            passed.append("camel_tools")
        finally:
            model._client.close()
    return passed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list-models", action="store_true", help="List model IDs from the configured OpenAI-compatible API; no generation")
    parser.add_argument("--boost", action="store_true", help="Check the optional boost provider")
    parser.add_argument("--skip-camel", action="store_true", help="Check only the application SDK; does not verify simulation compatibility")
    args = parser.parse_args(argv)
    try:
        settings = resolve_llm_settings(use_boost=args.boost, validate=not args.list_models)
        if not settings.api_key:
            raise ValueError("Set LLM_API_KEY (or OLLAMA_API_KEY for ollama_cloud)")
    except ValueError as error:
        print(json.dumps({"ok": False, "configuration_error": str(error)}))
        return 2
    try:
        if args.list_models:
            with OpenAI(api_key=settings.api_key, base_url=settings.base_url, timeout=30, max_retries=0) as client:
                model_ids = sorted(model.id for model in client.models.list())
            print(json.dumps({"ok": True, "provider": settings.provider, "models": model_ids}))
        else:
            checks = run_checks(settings, include_camel=not args.skip_camel)
            print(json.dumps({"ok": True, "provider": settings.provider, "model": settings.model, "checks": checks}))
        return 0
    except Exception as error:
        # Provider exception messages/bodies can echo auth, URLs, or prompts.
        # Emit only the error class and HTTP status; never raw errors or keys.
        print(json.dumps({"ok": False, "error_type": type(error).__name__, "http_status": getattr(error, "status_code", None)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
