"""Explicit provider settings shared by the API and simulation subprocesses.

This module never changes process environment variables. In particular, each
CAMEL model receives its own credentials so parallel platforms cannot overwrite
one another's provider configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit


OLLAMA_CLOUD_BASE_URL = "https://ollama.com/v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"
_PLACEHOLDERS = {"your_api_key_here", "your_base_url_here", "your_model_name_here", "your_ollama_api_key_here"}


def _value(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    return "" if text.lower() in _PLACEHOLDERS else text


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    api_key: str = field(repr=False)
    base_url: str
    model: str
    token_limit: int = 32768
    is_boost: bool = False


def resolve_llm_settings(
    environ: Optional[Mapping[str, Any]] = None,
    *,
    use_boost: bool = False,
    fallback_model: Optional[str] = None,
    validate: bool = True,
) -> LLMSettings:
    """Resolve a complete provider; copied boost placeholders mean disabled.

    Cloud model names must be selected explicitly from the direct cloud API.
    ``validate=False`` permits an absent key/model for configuration inspection;
    malformed endpoints, providers, limits and partial boost settings still fail.
    Errors contain field names only, never credential or URL values.
    """
    env = os.environ if environ is None else environ
    provider = _value(env.get("LLM_PROVIDER")) or "openai"
    prefix = "LLM_"
    is_boost = False
    if use_boost:
        boost_fields = ("API_KEY", "BASE_URL", "MODEL_NAME")
        boost_values = {name: _value(env.get("LLM_BOOST_" + name)) for name in boost_fields}
        if any(boost_values.values()):
            missing = ["LLM_BOOST_" + name for name, value in boost_values.items() if not value]
            if missing:
                raise ValueError("Incomplete boost configuration: set " + ", ".join(missing))
            prefix = "LLM_BOOST_"
            provider = _value(env.get("LLM_BOOST_PROVIDER")) or provider
            is_boost = True

    provider = provider.lower()
    if provider not in {"openai", "openai_compatible", "ollama_cloud"}:
        raise ValueError("LLM_PROVIDER must be openai, openai_compatible, or ollama_cloud")
    cloud = provider == "ollama_cloud"
    api_key = _value(env.get(prefix + "API_KEY"))
    if not api_key and cloud and not is_boost:
        api_key = _value(env.get("OLLAMA_API_KEY"))
    base_url = _value(env.get(prefix + "BASE_URL")) or (OLLAMA_CLOUD_BASE_URL if cloud else OPENAI_BASE_URL)
    model = _value(env.get(prefix + "MODEL_NAME"))
    if not model and not cloud:
        model = _value(fallback_model) or "gpt-4o-mini"

    # A direct cloud credential must never be silently sent to a local daemon or
    # an endpoint left over from a copied OpenAI configuration.
    try:
        parsed = urlsplit(base_url)
        invalid_url = (
            parsed.scheme not in {"https", "http"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or bool(parsed.query) or bool(parsed.fragment)
        )
    except ValueError:
        invalid_url = True
    if invalid_url:
        raise ValueError(prefix + "BASE_URL must be an HTTP(S) API URL without credentials, query, or fragment")
    base_url = base_url.rstrip("/")
    if cloud and base_url != OLLAMA_CLOUD_BASE_URL:
        raise ValueError(prefix + "BASE_URL for ollama_cloud must be https://ollama.com/v1")

    token_field = prefix + "TOKEN_LIMIT"
    raw_limit = _value(env.get(token_field)) or _value(env.get("LLM_TOKEN_LIMIT")) or "32768"
    try:
        token_limit = int(raw_limit)
        if token_limit < 1:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError(token_field + " must be a positive integer") from None

    if validate:
        missing = []
        if not api_key:
            missing.append(prefix + "API_KEY" + (" (or OLLAMA_API_KEY)" if cloud and not is_boost else ""))
        if not model:
            missing.append(prefix + "MODEL_NAME (select a model from the cloud model list)")
        if missing:
            raise ValueError("Missing LLM configuration: " + ", ".join(missing))
    return LLMSettings(provider, api_key, base_url, model, token_limit, is_boost)


def settings_from_config(
    config: Any,
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> LLMSettings:
    """Honor Config and caller overrides through the same validation path."""
    names = ("LLM_PROVIDER", "LLM_API_KEY", "OLLAMA_API_KEY", "LLM_BASE_URL", "LLM_MODEL_NAME", "LLM_TOKEN_LIMIT")
    values = {name: getattr(config, name, None) for name in names}
    for name, value in (("LLM_API_KEY", api_key), ("LLM_BASE_URL", base_url), ("LLM_MODEL_NAME", model)):
        if value is not None:
            values[name] = value
    return resolve_llm_settings(values)


def create_camel_model(
    config: Optional[Mapping[str, Any]] = None,
    *,
    use_boost: bool = False,
    settings: Optional[LLMSettings] = None,
):
    """Build an OASIS backend with explicit per-instance provider credentials."""
    from .camel_llm_model import BoundedCompatibleModel
    from .openai_chat_compat import is_gpt5_family

    settings = settings or resolve_llm_settings(
        use_boost=use_boost,
        fallback_model=(config or {}).get("llm_model"),
    )
    # Tool calls drive simulation actions. Do not force response_format here;
    # profiles/configuration use JSON via the separate application LLM client.
    model_config: dict[str, Any] = {"stream": False}
    if is_gpt5_family(settings.model):
        model_config["max_completion_tokens"] = 4096
    else:
        model_config["max_tokens"] = 4096
    return BoundedCompatibleModel(
        model_type=settings.model,
        api_key=settings.api_key,
        url=settings.base_url,
        context_token_limit=settings.token_limit,
        model_config_dict=model_config,
        timeout=180,
        max_retries=0,
    )
