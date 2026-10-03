#!/usr/bin/env python3
"""Read-only integrity audit of a frozen comparative batch, without outcomes.

The public result contains checks and digests, never prompts, reports, database
contents, provider errors, or private runtime paths. This establishes artifact
consistency, not model quality or independent historical preregistration.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
import tomllib
from types import ModuleType

BACKEND = Path(__file__).resolve().parents[1]
REPOSITORY = BACKEND.parent
SAFE_ID = re.compile(r"[A-Za-z0-9_-]+\Z")


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _runner():
    return _load_module("_mirofish_batch_audit_runner", BACKEND / "scripts/run_comparative_benchmark.py")


def _graph_module():
    # services/__init__.py imports operational services and file loggers. Load
    # precisely the storage module with its unused inference dependency disabled.
    prefix = "_mirofish_readonly_graph_audit"
    for suffix in ("", ".services", ".utils"):
        name = prefix + suffix
        if name not in sys.modules:
            package = ModuleType(name)
            package.__path__ = []
            sys.modules[name] = package
    dependency = ModuleType(prefix + ".utils.llm_client")

    def no_inference(*args, **kwargs):
        raise RuntimeError("Inference is forbidden during a batch audit")

    dependency.LLMClient = no_inference
    sys.modules[dependency.__name__] = dependency
    return _load_module(prefix + ".services.local_graph", BACKEND / "app/services/local_graph.py")


def _read(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate artifact key")
            value[key] = item
        return value

    def invalid_constant(value):
        raise ValueError("Nonfinite artifact value")

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique,
                      parse_constant=invalid_constant)


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _implementation_sha():
    return _runner().implementation_source_sha256()


def _expected_vendored_runtime():
    """Derive the trial's expected wheel proof from source, without loading OASIS.

    The runner verifies its installed simulation wheel before inference. The
    audit may run in the API-only environment, so it compares that saved proof
    to the frozen vendored files instead of inspecting installed distributions.
    """
    vendor = BACKEND / "vendor/camel-oasis"
    version = tomllib.loads((vendor / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    paths = sorted(path.relative_to(vendor) for path in (vendor / "oasis").rglob("*")
                   if path.is_file() and path.suffix in {".py", ".sql"})
    if not paths or not isinstance(version, str) or not version:
        raise ValueError("Vendored runtime source is incomplete")
    inventory = "\n".join(f"{path}:{_file_sha(vendor / path)}" for path in paths)
    return {"distribution": "camel-oasis", "version": version,
            "files_verified": len(paths), "source_sha256": _sha(inventory)}


def _check(checks, name, condition, *, reason="artifact_mismatch"):
    checks[name] = {"status": "pass" if condition else "fail"}
    if not condition:
        checks[name]["reason"] = reason


def _absent(checks, name, required, reason):
    checks[name] = {"status": "unavailable" if required else "not_applicable", "reason": reason}


def _status(checks):
    statuses = {item["status"] for item in checks.values()}
    return "fail" if "fail" in statuses else "incomplete" if "unavailable" in statuses else "pass"


class _ReadOnlyGraphClient:
    def __init__(self, database):
        self.database = database

    @contextmanager
    def _connect(self, *, write=False):
        if write:
            raise RuntimeError("Writes are forbidden during a batch audit")
        # Even SQLite mode=ro may create WAL-index sidecars next to its database.
        # Copy the stopped trial's database plus any uncheckpointed WAL into a
        # private temporary directory and read that copy. Never copy SHM: SQLite
        # reconstructs the temporary index from the preserved WAL itself.
        originals = [self.database, Path(str(self.database) + "-wal")]

        def signatures():
            return [(path.stat().st_size, path.stat().st_mtime_ns, path.stat().st_ino)
                    if path.exists() else None for path in originals]

        before = signatures()
        with tempfile.TemporaryDirectory(prefix="mirofish-readonly-audit-") as directory:
            copied = Path(directory) / self.database.name
            for path in originals:
                if path.exists():
                    shutil.copyfile(path, Path(directory) / path.name)
            if signatures() != before:
                raise ValueError("Database changed while taking an audit snapshot")
            connection = sqlite3.connect(copied.as_uri() + "?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            try:
                yield connection
            finally:
                connection.close()

    @staticmethod
    def _graph(connection, graph_id):
        row = connection.execute("SELECT * FROM graphs WHERE graph_id=?", (graph_id,)).fetchone()
        if row is None:
            raise ValueError("Expected graph is missing")
        return row


def _expected_document(source):
    # /graph/ontology/generate preprocesses each upload then adds its filename.
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    source = re.sub(r"\n{3,}", "\n\n", source)
    source = "\n".join(line.strip() for line in source.split("\n")).strip()
    return "\n\n=== source.txt ===\n" + source


def _usage_audit(runtime, proof, record, checks, runner):
    budgets = proof.get("budgets")
    if not isinstance(budgets, list):
        _absent(checks, "usage", True, "budget_proof_unavailable")
        return
    expected = runner.total_usage(budgets)
    _check(checks, "usage", record is not None and record.get("usage") == expected)
    database = runtime / "data/budgets.sqlite3"
    if not database.is_file():
        _absent(checks, "usage_ledger", True, "budget_ledger_unavailable")
        return
    with _ReadOnlyGraphClient(database)._connect() as connection:
        rows = connection.execute("SELECT * FROM run_budgets ORDER BY run_id").fetchall()
    actual = []
    for row in rows:
        actual.append({"run_id": row["run_id"], "usage": {
            "calls": row["calls"], "tokens": row["input_tokens"] + row["output_tokens"],
            "input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"],
            "estimated_cost_usd": None if row["unpriced_calls"] or not json.loads(row["pricing_json"])
            else row["cost_nano"] / 1e9}})
    _check(checks, "usage_ledger", runner.total_usage(actual) == expected
           and sorted(item["run_id"] for item in budgets) == [item["run_id"] for item in actual])


def _graph_audit(runtime, proof, source, checks, canonical):
    _absent(checks, "report", bool(proof.get("report")) or proof.get("status") == "ok", "report_not_reached")
    database = runtime / "data/memory.sqlite3"
    required = bool(proof.get("source_graph_id") or proof.get("execution_id"))
    if not database.is_file():
        _absent(checks, "source_graph", required, "graph_database_unavailable")
        _absent(checks, "execution", required, "execution_not_reached")
        return
    graph = _graph_module()._Graph(_ReadOnlyGraphClient(database))
    try:
        with graph.client._connect() as connection:
            snapshots = [dict(row) for row in connection.execute("SELECT * FROM graph_execution_snapshots")]
            source_ids = [row[0] for row in connection.execute(
                "SELECT graph_id FROM graphs WHERE graph_id NOT IN (SELECT graph_id FROM graph_execution_snapshots)")]
            seals = [row[0] for row in connection.execute("SELECT graph_id FROM graph_source_seals")]
        source_id = proof.get("source_graph_id")
        if not source_id and not snapshots and not seals:
            _absent(checks, "source_graph", False, "source_build_not_completed")
            _absent(checks, "execution", False, "execution_not_reached")
            return
        _check(checks, "source_graph_identity", len(source_ids) == 1 and source_ids == [source_id])
        clean = graph.assert_clean_source(source_id)
        fingerprint = clean.source_snapshot_sha256
        evidence = graph.get_evidence(source_id)
        _check(checks, "source_graph", True)
        _check(checks, "source_graph_evidence", bool(proof.get("source_graph_evidence_sha256"))
               and canonical(evidence) == proof["source_graph_evidence_sha256"])
        # Frozen trials use one upload and a 10,000-character chunk size.
        _check(checks, "source_graph_document", len(evidence) == 1
               and evidence[0]["text"] == _expected_document(source))
        checks["source_graph"]["source_snapshot_sha256"] = fingerprint
        if not snapshots:
            _absent(checks, "execution", bool(proof.get("execution_id")), "execution_not_reached")
            return
        _check(checks, "execution_count", len(snapshots) == 1)
        if len(snapshots) != 1:
            return
        snapshot = snapshots[0]
        simulation_id = snapshot["simulation_id"]
        if not isinstance(simulation_id, str) or not SAFE_ID.fullmatch(simulation_id):
            _check(checks, "execution", False, reason="invalid_simulation_identity")
            return
        run_path = runtime / "data/simulations" / simulation_id / "run_state.json"
        if not run_path.is_file():
            _absent(checks, "execution", True, "run_state_unavailable")
            return
        run = _read(run_path)
        binding = {"simulation_id": simulation_id, "execution_id": snapshot["execution_id"],
                   "source_graph_id": source_id, "execution_graph_id": snapshot["graph_id"],
                   "source_snapshot_sha256": fingerprint}
        _check(checks, "execution", snapshot["source_graph_id"] == source_id
               and snapshot["source_snapshot_sha256"] == fingerprint
               and snapshot["graph_id"] != source_id
               and all(run.get(key) == value for key, value in binding.items())
               and proof.get("simulation_id") == simulation_id)
        if proof.get("execution_id"):
            _check(checks, "execution_proof", all(proof.get(key) == binding[key]
                   for key in ("execution_id", "execution_graph_id", "source_graph_id", "simulation_id"))
                   and proof.get("source_graph_unchanged") is True)
        run_evidence = graph.get_evidence(snapshot["graph_id"])
        simulated = [item for item in run_evidence if item["kind"] == "simulation"]
        _check(checks, "simulation_evidence_scope", all(
            item["metadata"].get("execution_id") == binding["execution_id"]
            and item["metadata"].get("simulation_id") == simulation_id for item in simulated))
        if proof.get("execution_id"):
            _check(checks, "simulation_evidence_present", bool(simulated))
        report_path = runtime / "report.json"
        if not report_path.is_file():
            _absent(checks, "report", bool(proof.get("report")) or proof.get("status") == "ok",
                    "completed_report_unavailable")
            return
        report = _read(report_path)
        manifest = report.get("manifest", {})
        _check(checks, "report", report.get("status") == "completed"
               and all(manifest.get(key) == value for key, value in binding.items())
               and manifest.get("graph_id") == binding["execution_graph_id"]
               and report.get("report_id") == proof.get("report_id")
               and proof.get("report", {}).get("manifest") == manifest)
        _check(checks, "report_content", isinstance(report.get("markdown_content"), str)
               and bool(report["markdown_content"].strip())
               and _sha(report["markdown_content"]) == proof.get("report", {}).get("markdown_sha256"))
        _check(checks, "report_evidence", canonical(report.get("evidence"))
               == manifest.get("input_hashes", {}).get("evidence_snapshot")
               and {"source_fact", "simulation_observation"}.issubset(
                   item.get("kind") for item in report.get("evidence", {}).get("sources", [])))
        input_checks = []
        for name, digest in manifest.get("input_hashes", {}).items():
            if name in {"simulation_config.json", "run_state.json", "twitter_profiles.csv", "reddit_profiles.json",
                        "twitter/actions.jsonl", "reddit/actions.jsonl"}:
                path = run_path.parent / name
                input_checks.append(path.is_file() and _file_sha(path) == digest)
        _check(checks, "report_input_hashes", bool(input_checks) and all(input_checks))
    except Exception:
        # SourceGraphNotCleanError and parser messages can contain private data.
        _check(checks, "graph_integrity", False, reason="graph_or_binding_validation_failed")


def _audit_trial(results, runtime, slot, protocol, source, runner, contract):
    canonical = contract.canonical_sha256
    checks = {}
    identity = {key: slot[key] for key in ("case_id", "method", "repeat")}
    name = f"{slot['case_id']}-{slot['method']}-{slot['repeat']}"
    output = {**identity, "checks": checks}
    record_path = results / "trials" / (name + ".json")
    record = None
    if record_path.is_file():
        try:
            envelope = _read(record_path)
            records = contract._validate_predictions(protocol, envelope)
            key = (slot["case_id"], slot["method"], slot["repeat"])
            record = records.get(key)
            _check(checks, "trial_result", set(records) == {key})
        except Exception:
            _check(checks, "trial_result", False, reason="invalid_trial_artifact")
    else:
        _absent(checks, "trial_result", True, "trial_result_unavailable")
    marker_path = runtime.parent / "markers" / (name + ".started.json")
    if marker_path.is_file():
        try:
            marker = _read(marker_path)
            _check(checks, "start_marker", all(marker.get(key) == value for key, value in identity.items())
                   and marker.get("protocol_sha256") == canonical(protocol)
                   and marker.get("implementation_source_sha256") == protocol["model"]["implementation_source_sha256"]
                   and bool(marker.get("started_at")))
        except Exception:
            _check(checks, "start_marker", False, reason="invalid_start_marker")
    else:
        _absent(checks, "start_marker", runtime.exists() or record_path.exists(), "start_marker_unavailable")
    proof_path = runtime / "proof.json"
    if not proof_path.is_file():
        _absent(checks, "proof", True, "trial_not_started" if not runtime.exists()
                and not marker_path.exists() else "proof_unavailable")
        output.update(status=_status(checks), trial_status=record.get("status") if record else "unavailable")
        return output
    try:
        proof = _read(proof_path)
        _check(checks, "proof", all(proof.get(key) == value for key, value in identity.items())
               and proof.get("protocol_sha256") == canonical(protocol)
               and bool(proof.get("finished_at"))
               and record is not None and proof.get("status") == record.get("status"))
        _check(checks, "source", _read(runtime / "source.json") == {"source_document": source}
               and proof.get("source_document_sha256") == _sha(source))
        output["source_document_sha256"] = _sha(source)
        model = protocol.get("model", {})
        _check(checks, "model", all(proof.get("model", {}).get(key) == model.get(key)
               for key in ("provider", "model", "base_url", "token_limit") if key in model))
        _check(checks, "implementation", isinstance(model.get("implementation_source_sha256"), str)
               and proof.get("implementation", {}).get("backend_python_source_sha256")
               == model.get("implementation_source_sha256")
               and proof.get("implementation", {}).get("runner_source_sha256") == _file_sha(
                   BACKEND / "scripts/run_comparative_benchmark.py"))
        _check(checks, "implementation_scope", proof.get("implementation", {}).get("source_scope") == runner.SOURCE_SCOPE)
        _check(checks, "vendored_runtime", canonical(proof.get("implementation", {}).get("vendored_runtime"))
               == canonical(_expected_vendored_runtime()))
        configuration = protocol["configuration"]
        _check(checks, "configuration", proof.get("configuration") == configuration
               and proof.get("limits") == {"max_calls": configuration["max_calls"],
                                          "timeout_seconds": configuration["wall_timeout_seconds"]})
        prompt = proof.get("prompt_contract", {})
        _check(checks, "prompt_contract", prompt == {"assessor_system": runner.ASSESSOR_SYSTEM,
               "simulation_requirement": runner.REQUIREMENT})
        _usage_audit(runtime, proof, record, checks, runner)
        if proof.get("status") == "ok":
            assessment = proof.get("assessment", {})
            validated = runner.parse_prediction(json.dumps(assessment, allow_nan=False))
            _check(checks, "assessment", record is not None
                   and validated["probability_a"] == record.get("probability_a"))
        if slot["method"] == "mirofish":
            _graph_audit(runtime, proof, source, checks, canonical)
        else:
            for name in ("source_graph", "execution", "report"):
                _absent(checks, name, False, "single_model_method")
    except Exception:
        _check(checks, "proof_integrity", False, reason="invalid_or_missing_proof_artifact")
    output.update(status=_status(checks), trial_status=record.get("status") if record else "unavailable")
    return output


def audit_batch(results_dir):
    """Check every scheduled/started trial without mutating runtime artifacts."""
    results = Path(results_dir).resolve()
    runner = _runner()
    contract = runner.benchmark_module()
    canonical = contract.canonical_sha256
    batch, inputs, protocol, schedule = (_read(results / name) for name in
                                         ("batch.json", "inputs.json", "protocol.json", "schedule.json"))
    contract.validate_inputs(inputs)
    expected_protocol = contract.build_protocol(
        inputs, case_ids=protocol.get("case_ids"), repeats=protocol.get("repeats"),
        bootstrap_samples=protocol.get("bootstrap_samples"), bootstrap_seed=protocol.get("bootstrap_seed"),
        model=protocol.get("model"), configuration=protocol.get("configuration"))
    if protocol != expected_protocol:
        raise ValueError("Invalid frozen protocol")
    methods = ("mirofish",) if batch.get("purpose") == "development_reliability_only" else protocol["methods"]
    expected = {(case_id, method, repeat) for case_id in protocol["case_ids"]
                for method in methods for repeat in range(1, protocol["repeats"] + 1)}
    identities = []
    for item in schedule:
        if (not isinstance(item, dict) or set(item) != {"case_id", "method", "repeat"}
                or not isinstance(item["case_id"], str) or not SAFE_ID.fullmatch(item["case_id"])
                or item["method"] not in {"single_model", "mirofish"} or type(item["repeat"]) is not int):
            raise ValueError("Invalid batch schedule")
        identities.append((item["case_id"], item["method"], item["repeat"]))
    if len(set(identities)) != len(identities) or set(identities) != expected:
        raise ValueError("Schedule must contain every protocol trial exactly once")
    relative_root = Path(batch["runtime_root"])
    runtime_root = (REPOSITORY / relative_root).resolve()
    allowed_root = (REPOSITORY / "backend/uploads/benchmark-runtimes").resolve()
    if relative_root.is_absolute() or not runtime_root.is_relative_to(allowed_root) or runtime_root == allowed_root:
        raise ValueError("Runtime root is outside the private benchmark runtime directory")
    checks = {}
    _check(checks, "frozen_artifacts", batch.get("schema_version") == 1
           and batch.get("inputs_sha256") == canonical(inputs)
           and batch.get("protocol_sha256") == canonical(protocol)
           and batch.get("schedule_sha256") == canonical(schedule)
           and protocol.get("inputs_sha256") == canonical(inputs)
           and batch.get("implementation_source_sha256") == protocol.get("model", {}).get("implementation_source_sha256"))
    scripts = batch.get("orchestration_source_sha256", {})
    _check(checks, "orchestration_source", set(scripts) == {"run_batch.py", "audit_batch.py"}
           and all((BACKEND / "benchmarks" / name).is_file()
                   and _file_sha(BACKEND / "benchmarks" / name) == digest for name, digest in scripts.items()))
    _check(checks, "implementation_source", batch.get("implementation_source_sha256") == _implementation_sha()
           and protocol.get("configuration") == runner.benchmark_configuration())
    reporting_plan = REPOSITORY / "docs/benchmark-holdout-data-2026-10-03.md"
    _check(checks, "reporting_plan", reporting_plan.is_file()
           and batch.get("reporting_plan_sha256") == _file_sha(reporting_plan))
    _check(checks, "freeze_integrity", not (runtime_root / "freeze-violation.json").exists(),
           reason="freeze_violation_was_recorded")
    expected_dirs = {f"{case_id}-{method}-{repeat}" for case_id, method, repeat in identities}
    extra = [path for path in runtime_root.iterdir() if path.is_dir() and path.name not in expected_dirs | {"markers", "launcher-logs"}] if runtime_root.is_dir() else []
    _check(checks, "runtime_inventory", not extra, reason="unexpected_trial_runtime")
    unexpected_results = [path for path in (results / "trials").glob("*.json")
                          if path.stem not in expected_dirs]
    _check(checks, "trial_inventory", not unexpected_results, reason="unexpected_trial_result")
    trials = []
    for slot in schedule:
        name = f"{slot['case_id']}-{slot['method']}-{slot['repeat']}"
        source = runner.build_source_document(contract.prompt_case(inputs, slot["case_id"]))
        trials.append(_audit_trial(results, runtime_root / name, slot, protocol, source, runner, contract))
    aggregate_path = results / "predictions.json"
    if not aggregate_path.is_file():
        _absent(checks, "aggregate_predictions", True, "aggregate_predictions_unavailable")
    else:
        try:
            aggregate = contract._validate_predictions(protocol, _read(aggregate_path))
            immutable = {}
            for slot in schedule:
                path = results / "trials" / (f"{slot['case_id']}-{slot['method']}-{slot['repeat']}.json")
                if path.is_file():
                    immutable.update(contract._validate_predictions(protocol, _read(path)))
            _check(checks, "aggregate_predictions", aggregate == immutable)
        except Exception:
            _check(checks, "aggregate_predictions", False, reason="invalid_prediction_artifact")
    for index, trial in enumerate(trials):
        checks[f"trial_{index + 1}"] = {"status": "unavailable" if trial["status"] == "incomplete" else trial["status"]}
    status = _status(checks)
    checks = {key: value for key, value in checks.items() if not key.startswith("trial_")}
    return {"schema_version": 1, "status": status, "protocol_sha256": canonical(protocol),
            "checks": checks, "counts": {"scheduled": len(trials),
                **{value: sum(trial["status"] == value for trial in trials) for value in ("pass", "fail", "incomplete")}},
            "trials": trials}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    output = Path(args.output)
    if output.exists():
        parser.error("Refusing to overwrite an existing audit output")
    audit = audit_batch(args.results_dir)
    with output.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(audit, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return 0 if audit["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
