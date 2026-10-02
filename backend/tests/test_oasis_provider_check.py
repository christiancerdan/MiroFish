from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest


_SPEC = importlib.util.spec_from_file_location(
    "oasis_provider_check",
    Path(__file__).resolve().parents[1] / "scripts" / "check_oasis_provider.py",
)
probe = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(probe)


def _database(path, *, content=None, trace=False):
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(
            "CREATE TABLE user (user_id INTEGER, agent_id INTEGER);"
            "CREATE TABLE post (post_id INTEGER, user_id INTEGER, content TEXT);"
            "CREATE TABLE trace (user_id INTEGER, action TEXT, info TEXT);"
            "INSERT INTO user VALUES (0, 0);"
        )
        if content is not None:
            db.execute("INSERT INTO post VALUES (1, 0, ?)", (content,))
        if trace:
            db.execute(
                "INSERT INTO trace VALUES (0, 'create_post', ?)",
                (json.dumps({"content": content, "post_id": 1}),),
            )


@pytest.mark.parametrize(
    "content,trace",
    [(None, False), ("wrong content", True), ("MiroFish synthetic provider check.", False)],
)
def test_completed_round_without_expected_persisted_action_fails(tmp_path, content, trace):
    database = tmp_path / "oasis.db"
    _database(database, content=content, trace=trace)
    with pytest.raises(ValueError):
        probe.check_persisted_action(database)


def test_expected_post_and_matching_trace_pass(tmp_path):
    database = tmp_path / "oasis.db"
    _database(database, content=probe.PROBE_CONTENT, trace=True)
    assert probe.check_persisted_action(database) == {"posts": 1, "create_post_traces": 1}


def test_provider_failure_outputs_no_secret_body(monkeypatch, capsys):
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_API_KEY", "private-api-key")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("LLM_MODEL_NAME", "example:model")

    def reject(*args, **kwargs):
        raise RuntimeError("private-api-key secret-provider-body")

    monkeypatch.setattr(probe, "run_check", reject)
    assert probe.main([]) == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == {
        "ok": False, "error_type": "RuntimeError", "http_status": None,
    }
    assert "private-api-key" not in output.out + output.err
    assert "secret-provider-body" not in output.out + output.err
