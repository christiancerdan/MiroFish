#!/usr/bin/env python3
"""Freeze and run a resumable benchmark schedule without loading outcomes.

Only untouched slots may start. Trial results, frozen inputs, and start markers
are immutable; status.json and predictions.json are derived progress snapshots.
"""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading

BACKEND = Path(__file__).resolve().parents[1]
REPOSITORY = BACKEND.parent
PRIVATE_RUNTIMES = BACKEND / "uploads" / "benchmark-runtimes"
SCRIPTS = (Path(__file__), Path(__file__).with_name("audit_batch.py"))
REPORTING_PLAN = REPOSITORY / "docs" / "benchmark-holdout-data-2026-10-03.md"
MODEL = {"provider": "openai_compatible", "model": "gpt-oss:20b-cloud",
         "base_url": "http://127.0.0.1:11434/v1", "token_limit": 32768}


def now():
    return datetime.now(timezone.utc).isoformat()


def runner_module():
    spec = importlib.util.spec_from_file_location(
        "benchmark_batch_trial", BACKEND / "scripts" / "run_comparative_benchmark.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON value")))


def write_new(path, value):
    """Exclusive creation: immutable artifacts cannot replace prior evidence."""
    encoded = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with Path(path).open("x", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def source_sha256():
    return runner_module().implementation_source_sha256()


def orchestration_sha256():
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in SCRIPTS}


def slot_name(slot):
    name = f"{slot['case_id']}-{slot['method']}-{slot['repeat']}"
    if not all(char.isascii() and (char.isalnum() or char in "-_") for char in name):
        raise ValueError("Unsafe scheduled slot identifier")
    return name


def schedule_for(inputs, development=False):
    indexed = runner_module().benchmark_module().validate_inputs(inputs)
    if development:
        cases = ["upworthy-001", "upworthy-003"]
        if not set(cases) <= indexed.keys():
            raise ValueError("Development inputs must contain old pilot cases 001 and 003")
        return [{"case_id": case, "method": "mirofish", "repeat": 1} for case in cases]
    cases = [f"upworthy-holdout-{index:03d}" for index in range(1, 21)]
    if set(indexed) != set(cases):
        raise ValueError("Holdout requires exactly the 20 explicitly selected fresh cases")
    # Reverse method order on the second repeat to distribute order effects.
    return [{"case_id": case, "method": method, "repeat": repeat}
            for case in cases for repeat in (1, 2)
            for method in (("single_model", "mirofish") if repeat == 1 else ("mirofish", "single_model"))]


def private_runtime(path):
    root = Path(path).resolve()
    if root == PRIVATE_RUNTIMES.resolve() or not root.is_relative_to(PRIVATE_RUNTIMES.resolve()):
        raise ValueError("Runtime root must be a batch directory under backend/uploads/benchmark-runtimes")
    return root


def freeze(inputs_path, results_dir, runtime_root=None, *, development=False):
    runner = runner_module()
    contract = runner.benchmark_module()
    inputs = read_json(inputs_path)
    schedule = schedule_for(inputs, development)
    result = Path(results_dir).resolve()
    runtime = private_runtime(runtime_root or PRIVATE_RUNTIMES / result.name)
    if result.exists() or runtime.exists():
        raise FileExistsError("Freeze requires new results and runtime directories")
    if result == runtime or result.is_relative_to(runtime) or runtime.is_relative_to(result):
        raise ValueError("Public results and private runtime directories must be separate")
    purpose = "development_reliability_only" if development else "fresh_holdout_comparison"
    model = {**MODEL, "max_concurrent_trials": 2, "purpose": purpose, "frozen_at": now(),
             "timing_interpretation": "Observed wall time with up to two concurrent trials; not an isolated latency benchmark",
             "implementation_source_sha256": source_sha256()}
    model["git_head_at_freeze"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=BACKEND, text=True).strip()
    protocol = contract.build_protocol(
        inputs, case_ids=list(dict.fromkeys(slot["case_id"] for slot in schedule)),
        repeats=1 if development else 2, bootstrap_samples=5000, bootstrap_seed=20261003,
        model=model, configuration=runner.benchmark_configuration())
    batch = {"schema_version": 1, "purpose": purpose, "created_at": model["frozen_at"],
             "max_concurrent_trials": 2, "runtime_root": str(runtime.relative_to(REPOSITORY)),
             "inputs_sha256": contract.canonical_sha256(inputs),
             "protocol_sha256": contract.canonical_sha256(protocol),
             "schedule_sha256": contract.canonical_sha256(schedule),
             "implementation_source_sha256": model["implementation_source_sha256"],
             "orchestration_source_sha256": orchestration_sha256(),
             "reporting_plan_sha256": hashlib.sha256(REPORTING_PLAN.read_bytes()).hexdigest()}
    result.mkdir(parents=True)
    runtime.mkdir(parents=True, mode=0o700)
    runtime.chmod(0o700)
    (runtime / "markers").mkdir(mode=0o700)
    (runtime / "launcher-logs").mkdir(mode=0o700)
    (result / "trials").mkdir()
    for name, value in (("inputs", inputs), ("protocol", protocol), ("schedule", schedule), ("batch", batch)):
        write_new(result / f"{name}.json", value)
    snapshot = refresh(result)
    return compact(snapshot)


def load_batch(results_dir, *, check_source=True):
    result = Path(results_dir).resolve()
    batch, inputs, protocol, schedule = [read_json(result / f"{name}.json")
                                         for name in ("batch", "inputs", "protocol", "schedule")]
    runner = runner_module()
    contract = runner.benchmark_module()
    expected = contract.build_protocol(
        inputs, case_ids=protocol.get("case_ids"), repeats=protocol.get("repeats"),
        bootstrap_samples=protocol.get("bootstrap_samples"), bootstrap_seed=protocol.get("bootstrap_seed"),
        model=protocol.get("model"), configuration=protocol.get("configuration"))
    if protocol != expected or batch.get("schema_version") != 1 or batch.get("max_concurrent_trials") != 2:
        raise ValueError("Invalid frozen benchmark contract")
    if batch.get("purpose") not in {"development_reliability_only", "fresh_holdout_comparison"}:
        raise ValueError("Unknown batch purpose")
    if schedule != schedule_for(inputs, batch["purpose"] == "development_reliability_only"):
        raise ValueError("Schedule does not match its declared cohort")
    if (protocol["case_ids"] != list(dict.fromkeys(slot["case_id"] for slot in schedule))
            or protocol["repeats"] != max(slot["repeat"] for slot in schedule)):
        raise ValueError("Protocol and schedule cohorts disagree")
    if any(batch.get(name + "_sha256") != contract.canonical_sha256(value)
           for name, value in (("inputs", inputs), ("protocol", protocol), ("schedule", schedule))):
        raise ValueError("A frozen artifact changed")
    if any(protocol["model"].get(key) != value for key, value in MODEL.items()):
        raise ValueError("Frozen model is not the approved comparison model")
    if batch.get("implementation_source_sha256") != protocol["model"].get("implementation_source_sha256"):
        raise ValueError("Source hash bindings disagree")
    private_runtime(REPOSITORY / batch["runtime_root"])
    if check_source:
        assert_source(batch, protocol)
    return batch, inputs, protocol, schedule


def assert_source(batch, protocol):
    if (batch["implementation_source_sha256"] != source_sha256()
            or batch["orchestration_source_sha256"] != orchestration_sha256()
            or batch["reporting_plan_sha256"] != hashlib.sha256(REPORTING_PLAN.read_bytes()).hexdigest()
            or protocol["configuration"] != runner_module().benchmark_configuration()):
        raise ValueError("Implementation or orchestration changed after freeze; create a new batch")


def inspect_slots(results_dir, batch, protocol, schedule):
    result = Path(results_dir)
    runtime = REPOSITORY / batch["runtime_root"]
    contract = runner_module().benchmark_module()
    slots, records = [], []
    for slot in schedule:
        name = slot_name(slot)
        marker = runtime / "markers" / (name + ".started.json")
        output = result / "trials" / (name + ".json")
        proof = runtime / name / "proof.json"
        item = {**slot, "slot": name, "state": "unstarted"}
        if output.exists():
            try:
                envelope = read_json(output)
                valid = contract._validate_predictions(protocol, envelope)
                key = (slot["case_id"], slot["method"], slot["repeat"])
                if set(valid) != {key} or not marker.exists():
                    raise ValueError("Result is not a single started scheduled trial")
                saved = read_json(proof)
                record = valid[key]
                if (any(saved.get(field) != slot[field] for field in ("case_id", "method", "repeat"))
                        or saved.get("protocol_sha256") != batch["protocol_sha256"]
                        or saved.get("status") != record["status"] or not saved.get("finished_at")
                        or saved.get("implementation", {}).get("backend_python_source_sha256")
                        != batch["implementation_source_sha256"]
                        or saved.get("model") != MODEL):
                    raise ValueError("Terminal proof does not match frozen result")
                item["state"] = record["status"]
                # Preserve the runner's exact failure accounting and null costs.
                records.append(record)
            except (ValueError, KeyError, TypeError, OSError):
                item.update(state="invalid", error="InvalidTerminalArtifacts")
        elif marker.exists() or (runtime / name).exists():
            item.update(state="incomplete", error="StartedWithoutTerminalResult")
        slots.append(item)
    counts = {state: sum(item["state"] == state for item in slots)
              for state in ("ok", "failed", "invalid", "incomplete", "unstarted")}
    snapshot = {"schema_version": 1, "protocol_sha256": batch["protocol_sha256"],
                "schedule_sha256": batch["schedule_sha256"], "purpose": batch["purpose"],
                "updated_at": now(), "scheduled": len(slots), "counts": counts,
                "all_slots_terminal": counts["ok"] + counts["failed"] == len(slots),
                "freeze_violation": (runtime / "freeze-violation.json").exists(), "slots": slots}
    predictions = {"schema_version": 1, "protocol_sha256": batch["protocol_sha256"], "records": records}
    return snapshot, predictions


def refresh(results_dir):
    batch, _, protocol, schedule = load_batch(results_dir, check_source=False)
    snapshot, predictions = inspect_slots(results_dir, batch, protocol, schedule)
    runner = runner_module()
    # These two files are disposable views; immutable individual trials remain.
    runner.save_json(Path(results_dir) / "predictions.json", predictions)
    runner.save_json(Path(results_dir) / "status.json", snapshot)
    return snapshot


def compact(snapshot):
    return {key: snapshot[key] for key in
            ("purpose", "scheduled", "counts", "all_slots_terminal", "freeze_violation")}


@contextmanager
def batch_lock(runtime):
    with (runtime / "batch.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("A supervisor or its trial children still hold this batch lock") from error
        # Do not explicitly unlock: children inherit this file description, so
        # the lock survives a killed supervisor until its last child exits.
        yield lock.fileno()


def execute_slot(results_dir, batch, protocol, slot, python, lock_fd):
    assert_source(batch, protocol)
    result = Path(results_dir)
    runtime = REPOSITORY / batch["runtime_root"]
    name = slot_name(slot)
    marker = runtime / "markers" / (name + ".started.json")
    output = result / "trials" / (name + ".json")
    if marker.exists() or output.exists() or (runtime / name).exists():
        raise RuntimeError("A started or existing trial must never be relaunched")
    write_new(marker, {**slot, "started_at": now(), "protocol_sha256": batch["protocol_sha256"],
                       "implementation_source_sha256": batch["implementation_source_sha256"]})
    command = [str(python), str(BACKEND / "scripts" / "run_comparative_benchmark.py"),
               "--inputs", str(result / "inputs.json"), "--protocol", str(result / "protocol.json"),
               "--case-id", slot["case_id"], "--method", slot["method"], "--repeat", str(slot["repeat"]),
               "--output", str(output), "--runtime-dir", str(runtime / name),
               "--timeout", str(protocol["configuration"]["wall_timeout_seconds"]),
               "--max-calls", str(protocol["configuration"]["max_calls"])]
    try:
        with (runtime / "launcher-logs" / (name + ".log")).open("x", encoding="utf-8") as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                       cwd=REPOSITORY, pass_fds=(lock_fd,), start_new_session=True)
            try:
                code = process.wait(timeout=protocol["configuration"]["wall_timeout_seconds"] + 60)
            except subprocess.TimeoutExpired:
                terminate_trial(process)
                code = process.returncode
        write_new(runtime / "markers" / (name + ".exit.json"),
                  {"finished_at": now(), "returncode": code})
    except OSError as error:
        write_new(runtime / "markers" / (name + ".exit.json"),
                  {"finished_at": now(), "error": type(error).__name__, "returncode": None})


def terminate_trial(process):
    """Bound cleanup even when an OASIS child created a separate session."""
    rows = subprocess.check_output(["ps", "-axo", "pid=,ppid=,pgid="], text=True)
    tree = [tuple(map(int, row.split())) for row in rows.splitlines() if row.strip()]
    descendants = {process.pid}
    while True:
        expanded = descendants | {pid for pid, parent, _ in tree if parent in descendants}
        if expanded == descendants:
            break
        descendants = expanded
    groups = {group for pid, _, group in tree if pid in descendants and group in descendants}
    groups.add(process.pid)  # Popen(start_new_session=True) owns this group.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for group in groups:
            try:
                os.killpg(group, sig)
            except ProcessLookupError:
                pass
        if sig == signal.SIGTERM:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
    process.wait(timeout=10)


def run_batch(results_dir, python, *, execute=execute_slot):
    result = Path(results_dir).resolve()
    batch, _, protocol, schedule = load_batch(result)
    # Resolving a venv's python symlink would select the base interpreter and
    # discard the installed simulation dependencies.
    python = Path(os.path.abspath(python))
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("Simulation Python executable is missing or not executable")
    runtime = REPOSITORY / batch["runtime_root"]
    stop = threading.Event()
    previous = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, lambda *_: stop.set())
    try:
        with batch_lock(runtime) as lock_fd:
            if (runtime / "freeze-violation.json").exists():
                raise ValueError("This batch recorded a freeze violation; start a new batch")
            snapshot = refresh(result)
            untouched = iter(slot for slot in snapshot["slots"] if slot["state"] == "unstarted")
            with ThreadPoolExecutor(max_workers=2) as pool:
                active, exhausted = set(), False
                while active or not exhausted:
                    try:
                        assert_source(batch, protocol)
                    except (ValueError, OSError):
                        stop.set()
                        if not (runtime / "freeze-violation.json").exists():
                            write_new(runtime / "freeze-violation.json", {"detected_at": now()})
                    while not stop.is_set() and not exhausted and len(active) < 2:
                        slot = next(untouched, None)
                        if slot is None:
                            exhausted = True
                        else:
                            active.add(pool.submit(execute, result, batch, protocol, slot, python, lock_fd))
                    if stop.is_set():
                        exhausted = True
                    if active:
                        done, active = wait(active, timeout=1, return_when=FIRST_COMPLETED)
                        for future in done:
                            future.result()
                        if done:
                            print(json.dumps(compact(refresh(result))), flush=True)
                try:
                    assert_source(batch, protocol)
                except (ValueError, OSError):
                    if not (runtime / "freeze-violation.json").exists():
                        write_new(runtime / "freeze-violation.json", {"detected_at": now()})
                return compact(refresh(result))
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    freezing = commands.add_parser("freeze", help="Write a new frozen protocol; makes no provider calls")
    freezing.add_argument("--inputs", required=True)
    freezing.add_argument("--results-dir", required=True)
    freezing.add_argument("--runtime-root")
    freezing.add_argument("--development", action="store_true")
    running = commands.add_parser("run", help="Run only never-started slots; uses the configured provider")
    running.add_argument("--results-dir", required=True)
    running.add_argument("--python", default=str(BACKEND / ".venv-simulation" / "bin" / "python"))
    status = commands.add_parser("status", help="Read progress without changing artifacts")
    status.add_argument("--results-dir", required=True)
    args = parser.parse_args(argv)
    if args.command == "freeze":
        result = freeze(args.inputs, args.results_dir, args.runtime_root, development=args.development)
    elif args.command == "run":
        result = run_batch(args.results_dir, args.python)
    else:
        batch, _, protocol, schedule = load_batch(args.results_dir, check_source=False)
        result = compact(inspect_slots(args.results_dir, batch, protocol, schedule)[0])
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return int(args.command == "run" and (not result["all_slots_terminal"] or result["freeze_violation"]))


if __name__ == "__main__":
    raise SystemExit(main())
