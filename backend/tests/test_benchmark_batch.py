"""Batch persistence contracts without provider calls or outcome data."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import threading

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "benchmarks" / "run_batch.py"
spec = importlib.util.spec_from_file_location("benchmark_batch_test", SCRIPT)
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "REPOSITORY", tmp_path)
    monkeypatch.setattr(batch, "PRIVATE_RUNTIMES", tmp_path / "backend/uploads/benchmark-runtimes")
    helper = tmp_path / "audit_batch.py"
    helper.write_text("# frozen helper\n")
    monkeypatch.setattr(batch, "SCRIPTS", (SCRIPT, helper))
    reporting = tmp_path / "reporting-plan.md"
    reporting.write_text("Predeclared evaluation plan.\n")
    monkeypatch.setattr(batch, "REPORTING_PLAN", reporting)
    inputs = batch.BACKEND / "benchmarks/data/upworthy_inputs.json"
    result = tmp_path / "results"
    batch.freeze(inputs, result, development=True)
    return result


def terminal(result, slot, *, status="ok", probability=0.6):
    meta, _, protocol, _ = batch.load_batch(result)
    runtime = batch.REPOSITORY / meta["runtime_root"]
    name = batch.slot_name(slot)
    batch.write_new(runtime / "markers" / (name + ".started.json"), {"started_at": batch.now()})
    trial = runtime / name
    trial.mkdir()
    record = {**{key: slot[key] for key in ("case_id", "method", "repeat")}, "status": status,
              "probability_a": probability if status == "ok" else None,
              "error": None if status == "ok" else "RuntimeError", "elapsed_seconds": 9.0,
              "usage": {"calls": 3, "total_tokens": 11, "input_tokens": 5,
                        "output_tokens": 6, "cost_usd": None}}
    batch.write_new(trial / "proof.json", {
        **{key: slot[key] for key in ("case_id", "method", "repeat")},
        "protocol_sha256": meta["protocol_sha256"], "status": status, "finished_at": batch.now(),
        "implementation": {"backend_python_source_sha256": meta["implementation_source_sha256"]},
        "model": batch.MODEL})
    batch.write_new(result / "trials" / (name + ".json"),
                    {"schema_version": 1, "protocol_sha256": meta["protocol_sha256"], "records": [record]})
    return record


def test_freeze_is_new_only_and_private(frozen):
    original = {path.name: path.read_bytes() for path in frozen.glob("*.json")}
    meta, _, _, _ = batch.load_batch(frozen)
    runtime = batch.REPOSITORY / meta["runtime_root"]
    assert runtime.stat().st_mode & 0o777 == 0o700
    with pytest.raises(FileExistsError):
        batch.freeze(frozen / "inputs.json", frozen, development=True)
    assert original == {path.name: path.read_bytes() for path in frozen.glob("*.json")}
    with pytest.raises(FileExistsError):
        batch.write_new(frozen / "protocol.json", {})


def test_holdout_schedule_is_full_explicit_and_method_order_balanced():
    inputs = batch.read_json(batch.BACKEND / "benchmarks/data/holdout-2026-10-03/upworthy_inputs.json")
    schedule = batch.schedule_for(inputs)
    assert len(schedule) == 80
    assert len({batch.slot_name(slot) for slot in schedule}) == 80
    assert {slot["case_id"] for slot in schedule} == {case["case_id"] for case in inputs["cases"]}
    assert [slot["method"] for slot in schedule[:4]] == ["single_model", "mirofish", "mirofish", "single_model"]
    with pytest.raises(ValueError, match="20 explicitly"):
        batch.schedule_for({**inputs, "cases": inputs["cases"][:6]})


def test_batch_uses_the_runner_source_inventory_without_a_parallel_definition(monkeypatch):
    monkeypatch.setattr(batch, "runner_module", lambda: SimpleNamespace(
        implementation_source_sha256=lambda: "central-inventory"))
    assert batch.source_sha256() == "central-inventory"


@pytest.mark.parametrize("kind", ["backend", "helper", "reporting_plan"])
def test_changed_frozen_code_or_plan_blocks_before_any_start(frozen, monkeypatch, kind):
    if kind == "backend":
        monkeypatch.setattr(batch, "source_sha256", lambda: "changed")
    elif kind == "helper":
        batch.SCRIPTS[1].write_text("# modified helper")
    else:
        batch.REPORTING_PLAN.write_text("Changed primary outcome")
    launched = []
    with pytest.raises(ValueError, match="changed after freeze"):
        batch.run_batch(frozen, sys.executable, execute=lambda *args: launched.append(args))
    assert launched == []


def test_frozen_input_tampering_blocks(frozen):
    value = batch.read_json(frozen / "inputs.json")
    value["cases"][0]["headline_a"] = "Changed after freeze"
    (frozen / "inputs.json").write_text(json.dumps(value))
    with pytest.raises(ValueError):
        batch.load_batch(frozen)


def test_disappearing_helper_during_run_permanently_records_violation(frozen):
    ready = threading.Barrier(2)
    helper = batch.SCRIPTS[1]
    original = helper.read_text()
    def execute(*_):
        leader = ready.wait()
        if leader == 0:
            helper.unlink()
        # Ensure both children finish only after the helper disappeared.
        ready.wait()
    status = batch.run_batch(frozen, sys.executable, execute=execute)
    assert status["freeze_violation"]
    helper.write_text(original)
    with pytest.raises(ValueError, match="recorded a freeze violation"):
        batch.run_batch(frozen, sys.executable, execute=lambda *_: pytest.fail("must remain blocked"))


def test_resume_runs_only_unstarted_and_never_incomplete(frozen):
    meta, _, _, schedule = batch.load_batch(frozen)
    runtime = batch.REPOSITORY / meta["runtime_root"]
    interrupted = batch.slot_name(schedule[0])
    batch.write_new(runtime / "markers" / (interrupted + ".started.json"), {"started_at": batch.now()})
    launched = []
    def execute(result, _meta, _protocol, slot, _python, _fd):
        launched.append(batch.slot_name(slot))
        terminal(result, slot, status="failed")
    status = batch.run_batch(frozen, sys.executable, execute=execute)
    assert launched == [batch.slot_name(schedule[1])]
    assert status["counts"] == {"ok": 0, "failed": 1, "invalid": 0, "incomplete": 1, "unstarted": 0}
    assert not status["all_slots_terminal"]
    predictions = batch.read_json(frozen / "predictions.json")
    assert predictions["records"][0]["usage"]["total_tokens"] == 11
    assert predictions["records"][0]["usage"]["cost_usd"] is None
    # Incomplete accounting stays absent/unknown; no fabricated zero record.
    assert len(predictions["records"]) == 1
    batch.run_batch(frozen, sys.executable, execute=execute)
    assert len(launched) == 1


def test_existing_runtime_without_marker_is_never_replayed(frozen):
    meta, _, protocol, schedule = batch.load_batch(frozen)
    runtime = batch.REPOSITORY / meta["runtime_root"]
    (runtime / batch.slot_name(schedule[0])).mkdir()
    snapshot, _ = batch.inspect_slots(frozen, meta, protocol, schedule)
    assert snapshot["slots"][0]["state"] == "incomplete"
    with batch.batch_lock(runtime) as fd, pytest.raises(RuntimeError, match="never"):
        batch.execute_slot(frozen, meta, protocol, schedule[0], sys.executable, fd)


@pytest.mark.parametrize("failure", ["malformed", "missing_proof", "wrong_model", "wrong_slot", "no_finish"])
def test_invalid_terminal_artifacts_never_become_success(frozen, failure):
    meta, _, protocol, schedule = batch.load_batch(frozen)
    terminal(frozen, schedule[0])
    name = batch.slot_name(schedule[0])
    output = frozen / "trials" / (name + ".json")
    proof = batch.REPOSITORY / meta["runtime_root"] / name / "proof.json"
    if failure == "malformed":
        output.write_text("{unfinished")
    elif failure == "missing_proof":
        proof.unlink()
    else:
        value = batch.read_json(proof)
        if failure == "wrong_model":
            value["model"]["model"] = "different"
        elif failure == "wrong_slot":
            value["case_id"] = "upworthy-003"
        else:
            value.pop("finished_at")
        proof.write_text(json.dumps(value))
    snapshot, predictions = batch.inspect_slots(frozen, meta, protocol, schedule)
    assert snapshot["counts"]["invalid"] == 1 and snapshot["counts"]["ok"] == 0
    assert predictions["records"] == []


def test_running_lock_prevents_concurrent_supervisors(frozen):
    meta, _, _, _ = batch.load_batch(frozen)
    runtime = batch.REPOSITORY / meta["runtime_root"]
    with batch.batch_lock(runtime):
        with pytest.raises(RuntimeError, match="still hold"):
            batch.run_batch(frozen, sys.executable, execute=lambda *_: pytest.fail("must not start"))


def test_child_retains_lock_after_supervisor_descriptor_closes(frozen):
    meta, _, _, _ = batch.load_batch(frozen)
    runtime = batch.REPOSITORY / meta["runtime_root"]
    with batch.batch_lock(runtime) as fd:
        child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"],
                                 stdin=subprocess.PIPE, pass_fds=(fd,))
    try:
        with pytest.raises(RuntimeError, match="still hold"):
            with batch.batch_lock(runtime):
                pytest.fail("child must keep the supervisor lock")
    finally:
        child.communicate(timeout=10)
    with batch.batch_lock(runtime):
        pass


def test_cleanup_timeout_targets_trial_and_descendant_sessions_only(monkeypatch):
    monkeypatch.setattr(batch.subprocess, "check_output", lambda *args, **kwargs:
                        "100 1 100\n101 100 101\n102 101 101\n200 1 200\n")
    signals = []
    monkeypatch.setattr(batch.os, "killpg", lambda group, sig: signals.append((group, sig)))
    waited = []
    process = SimpleNamespace(pid=100, wait=lambda **kwargs: waited.append(kwargs))
    batch.terminate_trial(process)
    assert {group for group, _ in signals} == {100, 101}
    assert len(signals) == 4
    assert waited == [{"timeout": 10}, {"timeout": 10}]


def test_python_venv_symlink_is_preserved(frozen, tmp_path):
    venv_python = tmp_path / "simulation-venv/bin/python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(sys.executable)
    seen = []
    def execute(result, _meta, _protocol, slot, python, _fd):
        seen.append(python)
        terminal(result, slot)
    status = batch.run_batch(frozen, venv_python, execute=execute)
    assert status["all_slots_terminal"]
    assert seen == [venv_python, venv_python]
    assert venv_python.resolve() != venv_python


def test_read_only_status_does_not_rewrite_artifacts(frozen, capsys):
    before = {path: path.read_bytes() for path in frozen.rglob("*") if path.is_file()}
    assert batch.main(["status", "--results-dir", str(frozen)]) == 0
    assert "unstarted" in capsys.readouterr().out
    assert before == {path: path.read_bytes() for path in frozen.rglob("*") if path.is_file()}


def test_no_outcomes_argument():
    with pytest.raises(SystemExit):
        batch.main(["freeze", "--outcomes", "unavailable.json"])
