"""Storage selection must never infer cloud consent from a stale API key."""
from types import SimpleNamespace

import pytest

from app.config import Config
from app.utils import zep


def test_local_default_ignores_cloud_key_and_endpoint(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "GRAPH_BACKEND", "local", raising=False)
    monkeypatch.setattr(Config, "LOCAL_GRAPH_DB_PATH", str(tmp_path / "memory.sqlite3"), raising=False)
    monkeypatch.setattr(Config, "ZEP_API_KEY", "stale-cloud-key")
    monkeypatch.setenv("ZEP_API_URL", "https://cloud.invalid")
    monkeypatch.setattr(zep, "Zep", lambda **kwargs: pytest.fail("Cloud client constructed"))
    zep.clear_zep_client_cache()
    client = zep.get_zep_client("explicit-stale-key")
    assert client.backend == "local"
    assert client is zep.get_zep_client()
    client.graph.create(graph_id="local", name="Local")
    assert client.graph.get("local").graph_id == "local"
    zep.clear_zep_client_cache()


def test_explicit_cloud_backend_preserves_zep_contract(monkeypatch):
    monkeypatch.setattr(Config, "GRAPH_BACKEND", "zep", raising=False)
    monkeypatch.delenv("ZEP_API_URL", raising=False)
    calls = []
    def fake(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(backend="zep")
    monkeypatch.setattr(zep, "Zep", fake)
    zep.clear_zep_client_cache()
    assert zep.get_zep_client(" test-key ").backend == "zep"
    assert calls[0]["api_key"] == "test-key"
    assert calls[0]["base_url"] == zep.ZEP_CLOUD_BASE_URL
    zep.clear_zep_client_cache()


def test_unknown_backend_fails_closed(monkeypatch):
    monkeypatch.setattr(Config, "GRAPH_BACKEND", "typo", raising=False)
    with pytest.raises(ValueError, match="GRAPH_BACKEND"):
        zep.get_zep_client("cloud-key")
