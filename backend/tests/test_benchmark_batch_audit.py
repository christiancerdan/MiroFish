"""Audits detect corrupt bindings without inference, outcomes, or database writes."""
import importlib.util
import importlib.metadata
import json
from pathlib import Path
import socket
import sqlite3

import pytest


BACKEND = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("batch_audit_test", BACKEND / "benchmarks/audit_batch.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def batch(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    backend = repository / "backend"
    backend.mkdir(parents=True)
    for name in ("app", "scripts", "vendor"):
        (backend / name).symlink_to(BACKEND / name, target_is_directory=True)
    (backend / "benchmarks").mkdir()
    (backend / "benchmarks/audit_batch.py").symlink_to(BACKEND / "benchmarks/audit_batch.py")
    (backend / "benchmarks/run_batch.py").write_text("# frozen orchestration fixture\n")
    monkeypatch.setattr(audit, "REPOSITORY", repository)
    monkeypatch.setattr(audit, "BACKEND", backend)
    runner = audit._runner()
    contract = runner.benchmark_module()
    inputs = {"schema_version": 1, "dataset_id": "audit-fixture", "cases": [{
        "case_id": "case-1", "split": "reserve", "headline_a": "Headline A",
        "headline_b": "Headline B", "audience_context": "A fictional readership.",
        "shared_image_unavailable": True}]}
    model = {"provider": "openai_compatible", "model": "fixture", "base_url": "http://localhost:9999/v1",
             "implementation_source_sha256": audit._implementation_sha(), "token_limit": 32768}
    protocol = contract.build_protocol(inputs, case_ids=["case-1"], repeats=1, model=model, configuration=runner.benchmark_configuration())
    schedule = [{"case_id": "case-1", "method": method, "repeat": 1} for method in protocol["methods"]]
    relative_root = "backend/uploads/benchmark-runtimes/audit-fixture"
    results = tmp_path / "results"
    runtime_root = repository / relative_root
    plan = repository / "docs/benchmark-holdout-data-2026-10-03.md"
    plan.parent.mkdir()
    plan.write_text("Frozen fixture reporting plan.\n")
    metadata = {"schema_version": 1, "runtime_root": relative_root, "inputs_sha256": contract.canonical_sha256(inputs),
                "protocol_sha256": contract.canonical_sha256(protocol), "schedule_sha256": contract.canonical_sha256(schedule),
                "implementation_source_sha256": model["implementation_source_sha256"],
                "reporting_plan_sha256": audit._file_sha(plan),
                "orchestration_source_sha256": {name: audit._file_sha(backend / "benchmarks" / name)
                                                for name in ("run_batch.py", "audit_batch.py")}}
    for name, value in (("batch", metadata), ("inputs", inputs), ("protocol", protocol), ("schedule", schedule)):
        save(results / (name + ".json"), value)
    source = runner.build_source_document(contract.prompt_case(inputs, "case-1"))
    proofs = {}
    records = []
    for slot in schedule:
        name = f"case-1-{slot['method']}-1"
        runtime = runtime_root / name
        status = "ok" if slot["method"] == "single_model" else "failed"
        proof = {**slot, "protocol_sha256": contract.canonical_sha256(protocol), "status": status, "finished_at": "2026-10-03T20:00:00+00:00",
                 "source_document_sha256": audit._sha(source), "configuration": protocol["configuration"],
                 "limits": {"max_calls": 35, "timeout_seconds": 900}, "model": {key: model[key]
                     for key in ("provider", "model", "base_url", "token_limit")},
                 "implementation": {"backend_python_source_sha256": model["implementation_source_sha256"],
                     "source_scope": runner.SOURCE_SCOPE, "vendored_runtime": audit._expected_vendored_runtime(),
                     "runner_source_sha256": audit._file_sha(BACKEND / "scripts/run_comparative_benchmark.py")},
                 "budgets": [{"run_id": "budget", "usage": {"calls": 1, "tokens": 12, "input_tokens": 8,
                                  "output_tokens": 4, "estimated_cost_usd": None}}],
                 "prompt_contract": {"assessor_system": runner.ASSESSOR_SYSTEM, "simulation_requirement": runner.REQUIREMENT}}
        if status == "ok":
            proof["assessment"] = {"probability_a": 0.6, "rationale": "Fixture assumption."}
        save(runtime / "proof.json", proof)
        save(runtime / "source.json", {"source_document": source})
        save(runtime_root / "markers" / (name + ".started.json"), {**slot, "started_at": "2026-10-03T19:00:00+00:00",
             "protocol_sha256": metadata["protocol_sha256"], "implementation_source_sha256": model["implementation_source_sha256"]})
        (runtime / "data").mkdir()
        with sqlite3.connect(runtime / "data/budgets.sqlite3") as connection:
            connection.execute("CREATE TABLE run_budgets(run_id TEXT,calls INTEGER,input_tokens INTEGER,output_tokens INTEGER,unpriced_calls INTEGER,cost_nano INTEGER,pricing_json TEXT)")
            connection.execute("INSERT INTO run_budgets VALUES('budget',1,8,4,1,0,'{}')")
        record = {**slot, "status": status, "error": None if status == "ok" else "FixtureFailure",
                  "elapsed_seconds": 1.0, "probability_a": 0.6 if status == "ok" else None,
                  "usage": runner.total_usage(proof["budgets"])}
        records.append(record)
        save(results / "trials" / (name + ".json"), {"schema_version": 1,
             "protocol_sha256": contract.canonical_sha256(protocol), "records": [record]})
        proofs[slot["method"]] = proof
    save(results / "predictions.json", {"schema_version": 1, "protocol_sha256": metadata["protocol_sha256"], "records": records})
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **kw: pytest.fail("Audit attempted network access"))
    return {"results": results, "runtime_root": runtime_root, "proofs": proofs, "source": source,
            "contract": contract, "protocol": protocol, "metadata": metadata}


def trial(result, method="mirofish"):
    return next(item for item in result["trials"] if item["method"] == method)


def complete_graph(batch):
    class Model:
        def chat_json(self, **kwargs):
            return {"entities": [{"name": "Curious Reader", "entity_type": "Entity",
                                   "summary": "Curious Reader is fictional.", "attributes": {}}], "edges": []}

    runtime = batch["runtime_root"] / "case-1-mirofish-1"
    graph = audit._graph_module().LocalGraphClient(runtime / "data/memory.sqlite3", llm_client=Model())
    graph.graph.create(graph_id="source", name="Fixture")
    document = audit._expected_document(batch["source"])
    created = graph.batch.create(metadata={"graph_id": "source", "mirofish_operation_id": "build", "chunk_count": 1})
    graph.batch.add(batch_id=created.batch_id, items=[{
        "type": "graph_episode", "data_type": "text", "graph_id": "source", "data": document,
        "metadata": {"mirofish_operation_id": "build", "chunk_index": 0, "chunk_sha256": audit._sha(document)}}])
    graph.batch.process(batch_id=created.batch_id)
    snapshot = graph.graph.clone_for_execution(source_graph_id="source", graph_id="execution-graph",
                                               execution_id="execution-1", simulation_id="simulation-1")
    graph.graph.add(graph_id="execution-graph", data="Curious Reader reacts to a headline.",
                    metadata={"kind": "simulation", "source": "mirofish_simulation",
                              "execution_id": "execution-1", "simulation_id": "simulation-1"})
    binding = {"simulation_id": "simulation-1", "execution_id": "execution-1", "source_graph_id": "source",
               "execution_graph_id": "execution-graph", "source_snapshot_sha256": snapshot.source_snapshot_sha256}
    run_path = runtime / "data/simulations/simulation-1/run_state.json"
    save(run_path, binding)
    evidence = {"sources": [{"kind": "source_fact"}, {"kind": "simulation_observation"}]}
    manifest = {**binding, "graph_id": "execution-graph", "input_hashes": {
        "run_state.json": audit._file_sha(run_path), "evidence_snapshot": batch["contract"].canonical_sha256(evidence)}}
    report = {"report_id": "report-1", "status": "completed", "manifest": manifest,
              "evidence": evidence, "markdown_content": "A fixture report."}
    save(runtime / "report.json", report)
    proof = batch["proofs"]["mirofish"]
    proof.update(binding, status="ok", source_graph_unchanged=True, report_id="report-1",
                 report={"manifest": manifest, "markdown_sha256": audit._sha(report["markdown_content"])},
                 assessment={"probability_a": 0.6, "rationale": "Fixture simulation."},
                 source_graph_evidence_sha256=batch["contract"].canonical_sha256(graph.graph.get_evidence("source")))
    save(runtime / "proof.json", proof)
    path = batch["results"] / "trials/case-1-mirofish-1.json"
    result = audit._read(path)
    result["records"][0].update(status="ok", probability_a=0.6, error=None)
    save(path, result)
    predictions = audit._read(batch["results"] / "predictions.json")
    predictions["records"] = [result["records"][0] if item["method"] == "mirofish" else item for item in predictions["records"]]
    save(batch["results"] / "predictions.json", predictions)
    return graph, runtime


def test_failed_early_stage_is_explicitly_not_applicable(batch):
    result = audit.audit_batch(batch["results"])
    assert result["status"] == "pass"
    assert result["counts"] == {"scheduled": 2, "pass": 2, "fail": 0, "incomplete": 0}
    assert trial(result)["trial_status"] == "failed"
    for name in ("source_graph", "execution", "report"):
        assert trial(result)["checks"][name]["status"] == "not_applicable"
    assert trial(result, "single_model")["checks"]["execution"]["reason"] == "single_model_method"


def test_full_execution_report_binding_is_audited_read_only(batch):
    graph, runtime = complete_graph(batch)
    before = {path: path.read_bytes() for path in runtime.rglob("*") if path.is_file()}
    result = audit.audit_batch(batch["results"])
    assert result["status"] == "pass", result
    assert trial(result)["checks"]["report_input_hashes"]["status"] == "pass"
    assert {path: path.read_bytes() for path in runtime.rglob("*") if path.is_file()} == before
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        with audit._ReadOnlyGraphClient(Path(graph.db_path))._connect() as connection:
            connection.execute("DELETE FROM graphs")


@pytest.mark.parametrize("field,value,check", [
    ("model", {"provider": "other"}, "model"),
    ("implementation", {"backend_python_source_sha256": "b" * 64}, "implementation"),
    ("configuration", {}, "configuration"),
    ("protocol_sha256", "b" * 64, "proof"),
])
def test_frozen_proof_mismatch_fails(batch, field, value, check):
    proof = batch["proofs"]["single_model"]
    proof[field] = value
    save(batch["runtime_root"] / "case-1-single_model-1/proof.json", proof)
    result = audit.audit_batch(batch["results"])
    assert result["status"] == "fail"
    assert trial(result, "single_model")["checks"][check]["status"] == "fail"


def test_private_source_substitution_is_detected_without_exposure(batch):
    private = "Private canary that must not appear in audit output"
    save(batch["runtime_root"] / "case-1-single_model-1/source.json", {"source_document": private})
    result = audit.audit_batch(batch["results"])
    assert result["status"] == "fail"
    assert private not in json.dumps(result)
    assert str(batch["runtime_root"]) not in json.dumps(result)


@pytest.mark.parametrize("change", ["source", "execution", "report", "evidence", "inputs"])
def test_mutated_source_execution_or_report_is_rejected(batch, change):
    graph, runtime = complete_graph(batch)
    if change == "source":
        with sqlite3.connect(graph.db_path) as connection:
            connection.execute("UPDATE entities SET summary='tampered' WHERE graph_id='source'")
    elif change == "execution":
        path = runtime / "data/simulations/simulation-1/run_state.json"
        value = audit._read(path)
        value["execution_id"] = "other-execution"
        save(path, value)
    else:
        path = runtime / "report.json"
        value = audit._read(path)
        if change == "report":
            value["manifest"]["execution_id"] = "other-execution"
        elif change == "evidence":
            value["evidence"]["sources"].pop()
        else:
            value["manifest"]["input_hashes"]["run_state.json"] = "b" * 64
        save(path, value)
    result = audit.audit_batch(batch["results"])
    assert result["status"] == "fail", result


def test_missing_started_proof_is_unavailable_not_success(batch):
    (batch["runtime_root"] / "case-1-single_model-1/proof.json").unlink()
    result = audit.audit_batch(batch["results"])
    assert result["status"] == "incomplete"
    assert trial(result, "single_model")["checks"]["proof"] == {
        "status": "unavailable", "reason": "proof_unavailable"}


def test_missing_unstarted_trial_is_explicit(batch):
    runtime = batch["runtime_root"] / "case-1-single_model-1"
    (runtime / "data/budgets.sqlite3").unlink()
    (runtime / "data").rmdir()
    for path in runtime.iterdir():
        path.unlink()
    runtime.rmdir()
    (batch["results"] / "trials/case-1-single_model-1.json").unlink()
    (batch["runtime_root"] / "markers/case-1-single_model-1.started.json").unlink()
    predictions = audit._read(batch["results"] / "predictions.json")
    predictions["records"] = [item for item in predictions["records"] if item["method"] != "single_model"]
    save(batch["results"] / "predictions.json", predictions)
    result = audit.audit_batch(batch["results"])
    assert result["status"] == "incomplete"
    assert trial(result, "single_model")["checks"]["proof"]["reason"] == "trial_not_started"


def test_schedule_cannot_duplicate_or_omit_a_trial(batch):
    path = batch["results"] / "schedule.json"
    schedule = audit._read(path)
    save(path, [schedule[0], schedule[0]])
    with pytest.raises(ValueError, match="exactly once"):
        audit.audit_batch(batch["results"])


def test_unexpected_runtime_is_not_silently_ignored(batch):
    (batch["runtime_root"] / "unscheduled-trial").mkdir()
    result = audit.audit_batch(batch["results"])
    assert result["checks"]["runtime_inventory"]["status"] == "fail"


def test_cli_does_not_overwrite_existing_output(batch, tmp_path):
    output = tmp_path / "audit.json"
    args = ["--results-dir", str(batch["results"]), "--output", str(output)]
    assert audit.main(args) == 0
    original = output.read_bytes()
    with pytest.raises(SystemExit):
        audit.main(args)
    assert output.read_bytes() == original


@pytest.mark.parametrize("change,check", [("record", "usage"), ("ledger", "usage_ledger")])
def test_usage_cannot_be_replaced_by_invented_zeroes(batch, change, check):
    runtime = batch["runtime_root"] / "case-1-single_model-1"
    if change == "record":
        path = batch["results"] / "trials/case-1-single_model-1.json"
        record = audit._read(path)
        record["records"][0]["usage"]["calls"] = 0
        save(path, record)
    else:
        with sqlite3.connect(runtime / "data/budgets.sqlite3") as connection:
            connection.execute("UPDATE run_budgets SET calls=0")
    result = audit.audit_batch(batch["results"])
    assert trial(result, "single_model")["checks"][check]["status"] == "fail"


def test_changed_aggregate_cannot_score_differently_from_audited_trials(batch):
    path = batch["results"] / "predictions.json"
    value = audit._read(path)
    value["records"][0]["probability_a"] = 0.99
    save(path, value)
    result = audit.audit_batch(batch["results"])
    assert result["checks"]["aggregate_predictions"]["status"] == "fail"


@pytest.mark.parametrize("method,probability", [("single_model", True), ("mirofish", 0.5)])
def test_invalid_trial_probabilities_fail_strict_schema(batch, method, probability):
    path = batch["results"] / f"trials/case-1-{method}-1.json"
    value = audit._read(path)
    value["records"][0]["probability_a"] = probability
    save(path, value)
    result = audit.audit_batch(batch["results"])
    assert trial(result, method)["checks"]["trial_result"]["status"] == "fail"


@pytest.mark.parametrize("mode", ["missing", "changed"])
def test_start_marker_is_required_and_bound_to_frozen_source(batch, mode):
    path = batch["runtime_root"] / "markers/case-1-single_model-1.started.json"
    if mode == "missing":
        path.unlink()
    else:
        value = audit._read(path)
        value["implementation_source_sha256"] = "b" * 64
        save(path, value)
    result = audit.audit_batch(batch["results"])
    assert trial(result, "single_model")["checks"]["start_marker"]["status"] == (
        "unavailable" if mode == "missing" else "fail")


def test_recorded_freeze_violation_persists_after_files_restored(batch):
    save(batch["runtime_root"] / "freeze-violation.json", {"detected_at": "earlier"})
    result = audit.audit_batch(batch["results"])
    assert result["checks"]["freeze_integrity"]["status"] == "fail"


def test_known_launcher_logs_directory_is_permitted(batch):
    (batch["runtime_root"] / "launcher-logs").mkdir()
    assert audit.audit_batch(batch["results"])["status"] == "pass"


def test_missing_completed_report_is_unavailable_not_validated(batch):
    _, runtime = complete_graph(batch)
    (runtime / "report.json").unlink()
    result = audit.audit_batch(batch["results"])
    assert trial(result)["checks"]["report"]["status"] == "unavailable"


def test_nonempty_wal_is_read_without_creating_source_sidecars(tmp_path):
    database = tmp_path / "active.sqlite3"
    writer = sqlite3.connect(database)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE evidence(value TEXT)")
        writer.execute("INSERT INTO evidence VALUES('committed in WAL')")
        writer.commit()
        before = {path: path.read_bytes() for path in tmp_path.iterdir()}
        with audit._ReadOnlyGraphClient(database)._connect() as connection:
            assert connection.execute("SELECT value FROM evidence").fetchone()[0] == "committed in WAL"
        assert {path: path.read_bytes() for path in tmp_path.iterdir()} == before
    finally:
        writer.close()


@pytest.mark.parametrize("scope", [None, "backend-app-scripts-v1", ""])
def test_missing_or_old_source_scope_is_rejected(batch, scope):
    proof = batch["proofs"]["single_model"]
    if scope is None:
        proof["implementation"].pop("source_scope")
    else:
        proof["implementation"]["source_scope"] = scope
    save(batch["runtime_root"] / "case-1-single_model-1/proof.json", proof)
    result = audit.audit_batch(batch["results"])
    assert trial(result, "single_model")["checks"]["implementation_scope"]["status"] == "fail"


@pytest.mark.parametrize("field,value", [
    (None, None), ("distribution", "other-runtime"), ("version", "0.0.0"),
    ("files_verified", 0), ("source_sha256", "b" * 64), ("unknown", "unfrozen"),
])
def test_missing_or_mismatched_vendor_inventory_is_rejected(batch, field, value):
    proof = batch["proofs"]["single_model"]
    if field is None:
        proof["implementation"].pop("vendored_runtime")
    else:
        proof["implementation"]["vendored_runtime"][field] = value
    save(batch["runtime_root"] / "case-1-single_model-1/proof.json", proof)
    result = audit.audit_batch(batch["results"])
    assert trial(result, "single_model")["checks"]["vendored_runtime"]["status"] == "fail"


def test_audit_does_not_require_installed_simulation_distribution(batch, monkeypatch):
    monkeypatch.setattr(importlib.metadata, "distribution", lambda *a, **kw: pytest.fail(
        "Audit must use vendored source; installed OASIS is not required"))
    assert audit.audit_batch(batch["results"])["status"] == "pass"


def test_expected_vendor_proof_hashes_only_runtime_python_and_sql(tmp_path, monkeypatch):
    backend = tmp_path / "backend"
    vendor = backend / "vendor/camel-oasis"
    (vendor / "oasis").mkdir(parents=True)
    (vendor / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n')
    (vendor / "oasis/agent.py").write_text("# source\n")
    (vendor / "oasis/schema.sql").write_text("CREATE TABLE fixture(id);\n")
    (vendor / "oasis/cache.pyc").write_bytes(b"ignored compiled file")
    monkeypatch.setattr(audit, "BACKEND", backend)
    expected_inventory = "\n".join(f"oasis/{name}:{audit._file_sha(vendor / 'oasis' / name)}"
                                   for name in ("agent.py", "schema.sql"))
    assert audit._expected_vendored_runtime() == {
        "distribution": "camel-oasis", "version": "1.2.3", "files_verified": 2,
        "source_sha256": audit._sha(expected_inventory)}
