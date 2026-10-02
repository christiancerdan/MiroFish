"""Verify the production entrypoint without starting a network listener."""

import runpy
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def server(monkeypatch):
    for name in ("FLASK_HOST", "FLASK_PORT", "WAITRESS_THREADS"):
        monkeypatch.delenv(name, raising=False)

    calls = []
    application = SimpleNamespace(debug=True)
    config = SimpleNamespace(validate=lambda: [])
    app_module = ModuleType("app")
    app_module.create_app = lambda: calls.append("create_app") or application
    config_module = ModuleType("app.config")
    config_module.Config = config
    waitress_module = ModuleType("waitress")
    waitress_module.serve = lambda app, **kwargs: calls.append((app, kwargs))
    monkeypatch.setitem(sys.modules, "app", app_module)
    monkeypatch.setitem(sys.modules, "app.config", config_module)
    monkeypatch.setitem(sys.modules, "waitress", waitress_module)

    namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / "serve.py"))
    return SimpleNamespace(
        main=namespace["main"], application=application, config=config, calls=calls
    )


def test_production_defaults_bind_loopback_once_without_debug(server):
    server.main()

    assert server.calls == [
        "create_app",
        (server.application, {"host": "127.0.0.1", "port": 5001, "threads": 8}),
    ]
    assert server.application.debug is False


def test_production_honors_explicit_listener_and_thread_settings(server, monkeypatch):
    monkeypatch.setenv("FLASK_HOST", "0.0.0.0")
    monkeypatch.setenv("FLASK_PORT", "8080")
    monkeypatch.setenv("WAITRESS_THREADS", "16")

    server.main()

    assert server.calls[-1] == (
        server.application,
        {"host": "0.0.0.0", "port": 8080, "threads": 16},
    )


def test_invalid_configuration_never_creates_or_serves_application(server, capsys):
    server.config.validate = lambda: ["Required configuration is missing"]

    with pytest.raises(SystemExit) as error:
        server.main()

    assert error.value.code == 1
    assert server.calls == []
    assert "Required configuration is missing" in capsys.readouterr().err
