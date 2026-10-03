"""Synthetic-only tests: no provider, customer, or network calls."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "benchmarks" / "prospective_study.py"
spec = importlib.util.spec_from_file_location("prospective_study", SCRIPT)
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


def at(hour):
    return datetime(2030, 1, 1, hour, tzinfo=timezone.utc)


def save(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.fixture
def manifest():
    # Test-only fictional inputs; time is supplied by a monkeypatched clock.
    return {"schema_version": 1, "study_id": "unit-fixture", "example_only": False,
            "product_context": "A fictional scheduling tool used only in unit tests.",
            "model": {"provider": "test-provider", "name": "test-model"},
            "method_configuration": {"single_model": "One prompt with the shared packet.",
                                     "mirofish": "One simulated round with the shared packet."},
            "fictional_persona_assumptions": ["A fictional visitor comparing scheduling tools."],
            "repeats": 2, "forecast_deadline": study.stamp(at(3)),
            "launch_at": study.stamp(at(4)), "observation_ends_at": study.stamp(at(8)),
            "randomization": {"unit": "unique_visitor", "allocation_a": 0.5,
                              "allocation_b": 0.5, "assignment": "persistent"},
            "stopping_rule": "fixed_window_no_early_stopping",
            "cases": [{"case_id": "case-1", "experiment_id": "experiment-1",
                       "audience": "Eligible new test visitors", "channel": "A test landing page",
                       "eligibility": "First eligible visit in the fixed window", "exclusions": ["Internal staff"],
                       "variant_a": {"id": "copy-a", "text": "Arrange your next meeting"},
                       "variant_b": {"id": "copy-b", "text": "Find a meeting time together"},
                       "response": {"event_id": "trial-start", "definition": "Visitor starts one trial in the same session; count at most once."},
                       "minimum_unique_visitors_per_arm": 100,
                       "sample_size_rationale": "Test fixture only; no power claim."}]}


@pytest.fixture
def frozen(tmp_path, monkeypatch, manifest):
    monkeypatch.setattr(study, "now", lambda: at(0))
    root = tmp_path / "study"
    metadata = study.freeze(save(tmp_path / "manifest.json", manifest), root)
    return root, manifest, metadata


def forecast(manifest, frozen, *, method="single_model", repeat=1):
    return {"case_id": "case-1", "method": method, "repeat": repeat, "status": "ok",
            "probability_a": 0.8 if method == "mirofish" else 0.5, "error": None,
            "model": manifest["model"], "study_sha256": frozen["study_sha256"],
            "packet_sha256": study.sha(study.encoded(study.packets_for(manifest)["case-1"])),
            "generation_started_at": study.stamp(at(1)), "generation_completed_at": study.stamp(at(1)),
            "generated_for_this_study_before_observations": True,
            "usage": {"calls": 1, "input_tokens": 10, "output_tokens": 2, "total_tokens": 12, "cost_usd": None},
            "elapsed_seconds": 1.5}


def fill(frozen, tmp_path, monkeypatch, *, failed=False):
    root, manifest, metadata = frozen
    monkeypatch.setattr(study, "now", lambda: at(2))
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("SYNTHETIC MODEL OUTPUT", encoding="utf-8")
    for method in study.METHODS:
        for repeat in (1, 2):
            value = forecast(manifest, metadata, method=method, repeat=repeat)
            if failed and method == "mirofish" and repeat == 2:
                value.update(status="failed", probability_a=None, error="Synthetic failure")
            study.record(root, save(tmp_path / "import.json", value), evidence)
    return study.seal(root)


def observations(frozen, sealed):
    _, manifest, metadata = frozen
    return {"schema_version": 1, "study_sha256": metadata["study_sha256"],
            "seal_sha256": study.sha(study.encoded(sealed)), "operator": "Synthetic test operator",
            "attestations": {key: True for key in study.ATTESTATIONS},
            "cases": [{"case_id": "case-1", "experiment_id": "experiment-1",
                       "variant_a_id": "copy-a", "variant_b_id": "copy-b", "event_id": "trial-start",
                       "window_started_at": manifest["launch_at"], "window_ended_at": manifest["observation_ends_at"],
                       "unique_visitors_a": 100, "unique_visitors_b": 100,
                       "responders_a": 20, "responders_b": 10}]}


def test_manual_workflow_scores_one_case_without_accuracy_claim(frozen, tmp_path, monkeypatch):
    sealed = fill(frozen, tmp_path, monkeypatch)
    monkeypatch.setattr(study, "now", lambda: at(9))
    result = study.evaluate(frozen[0], save(tmp_path / "observed.json", observations(frozen, sealed)))
    assert result["design"] == "prospective_manual_operator_attested"
    assert result["general_forecast_accuracy_claim"] is False
    assert result["timing_authority"] == "local_clock_only"
    assert result["complete_paired_metrics"]["mirofish"]["brier_score"] == pytest.approx(0.04)
    assert result["complete_paired_metrics"]["single_model"]["brier_score"] == 0.25
    assert result["complete_paired_metrics"]["single_model"]["directional_accuracy"] == 0.5
    assert result["usage"]["mirofish"]["total_tokens"]["total"] == 24
    assert result["usage"]["mirofish"]["cost_usd"]["total"] is None
    assert result["usage"]["mirofish"]["cost_usd"]["unknown_records"] == 2
    assert "interval" not in result
    assert json.loads((frozen[0] / "evaluation.json").read_text()) == result


def test_failed_repeat_retained_complete_pair_excluded_and_sensitivity(frozen, tmp_path, monkeypatch):
    sealed = fill(frozen, tmp_path, monkeypatch, failed=True)
    monkeypatch.setattr(study, "now", lambda: at(9))
    result = study.evaluate(frozen[0], save(tmp_path / "observed.json", observations(frozen, sealed)))
    assert result["completion"]["mirofish"] == {"valid": 1, "scheduled": 2}
    assert result["complete_paired_metrics"]["single_model"]["scored_cases"] == 0
    assert result["complete_paired_metrics"]["mirofish"]["brier_score"] is None
    assert result["failure_adjusted_metrics"]["mirofish"]["brier_score"] == pytest.approx(0.36)
    assert result["failure_adjusted_metrics"]["single_model"]["brier_score"] == 0.25


def test_exact_rate_tie_excluded_but_case_retained(frozen, tmp_path, monkeypatch):
    sealed = fill(frozen, tmp_path, monkeypatch)
    value = observations(frozen, sealed)
    value["cases"][0].update(unique_visitors_b=200, responders_b=40)
    monkeypatch.setattr(study, "now", lambda: at(9))
    result = study.evaluate(frozen[0], save(tmp_path / "observed.json", value))
    assert result["cases"][0]["observed_winner"] == "tie"
    assert result["complete_paired_metrics"]["mirofish"]["scored_cases"] == 0


@pytest.mark.parametrize("field,value", [("example_only", True), ("product_context", "TODO"),
    ("study_id", "../../unsafe"), ("study_id", "example-demo"), ("schema_version", True),
    ("repeats", True), ("repeats", 0), ("forecast_deadline", "2030-01-01"),
    ("forecast_deadline", "2030-01-01Z"), ("forecast_deadline", "2030-01-01T05:00:00Z"),
    ("stopping_rule", "stop_when_significant")])
def test_manifest_rejects_unfilled_examples_invalid_design(manifest, field, value):
    manifest[field] = value
    with pytest.raises(study.StudyError):
        study.validate_manifest(manifest, for_freeze=True)


@pytest.mark.parametrize("mutation", ["extra", "duplicate_case", "duplicate_experiment", "same_variant", "same_text",
    "invalid_minimum", "no_event", "wrong_allocation", "no_assumptions"])
def test_manifest_strict_details(manifest, mutation):
    if mutation == "extra": manifest["winner"] = "A"
    elif mutation == "duplicate_case": manifest["cases"].append(deepcopy(manifest["cases"][0]))
    elif mutation == "duplicate_experiment":
        case = deepcopy(manifest["cases"][0]); case["case_id"] = "case-2"; manifest["cases"].append(case)
    elif mutation == "same_variant": manifest["cases"][0]["variant_b"]["id"] = "copy-a"
    elif mutation == "same_text": manifest["cases"][0]["variant_b"]["text"] = manifest["cases"][0]["variant_a"]["text"]
    elif mutation == "invalid_minimum": manifest["cases"][0]["minimum_unique_visitors_per_arm"] = 0
    elif mutation == "no_event": manifest["cases"][0]["response"]["definition"] = ""
    elif mutation == "wrong_allocation": manifest["randomization"]["allocation_a"] = 0.9
    else: manifest["fictional_persona_assumptions"] = []
    with pytest.raises(study.StudyError): study.validate_manifest(manifest)


def test_common_packet_future_facing_and_fictional(manifest):
    packet = study.packets_for(manifest)["case-1"]
    assert "Before launch" in packet["question"]
    assert "fictional" in packet["assumption_notice"]
    assert "publisher" not in json.dumps(packet)
    assert "historical" not in json.dumps(packet)
    assert "shared_image_unavailable" not in packet
    assert "method" not in packet


def test_freeze_uses_current_clock_and_cannot_overwrite(frozen, tmp_path, monkeypatch):
    assert frozen[2]["frozen_at"] == study.stamp(at(0))
    with pytest.raises(FileExistsError): study.freeze(tmp_path / "manifest.json", frozen[0])
    monkeypatch.setattr(study, "now", lambda: at(3))
    with pytest.raises(study.StudyError, match="deadline"):
        study.freeze(tmp_path / "manifest.json", tmp_path / "late")


@pytest.mark.parametrize("mutation", ["wrong_study", "wrong_packet", "unknown_case", "wrong_method", "bool_repeat", "wrong_model",
    "historical", "future", "unattested", "nan", "bool_probability", "failed_probability", "success_error", "usage_mismatch", "extra"])
def test_forecast_strict_binding_and_generation_time(frozen, mutation):
    value = forecast(frozen[1], frozen[2])
    if mutation == "wrong_study": value["study_sha256"] = "0" * 64
    elif mutation == "wrong_packet": value["packet_sha256"] = "0" * 64
    elif mutation == "unknown_case": value["case_id"] = "different"
    elif mutation == "wrong_method": value["method"] = "historical"
    elif mutation == "bool_repeat": value["repeat"] = True
    elif mutation == "wrong_model": value["model"] = {"provider": "another", "name": "another"}
    elif mutation == "historical": value["generation_started_at"] = "2029-01-01T00:00:00Z"
    elif mutation == "future": value["generation_completed_at"] = study.stamp(at(3))
    elif mutation == "unattested": value["generated_for_this_study_before_observations"] = False
    elif mutation == "nan": value["probability_a"] = float("nan")
    elif mutation == "bool_probability": value["probability_a"] = True
    elif mutation == "failed_probability": value.update(status="failed", error="Failure")
    elif mutation == "success_error": value["error"] = "Failure"
    elif mutation == "usage_mismatch": value["usage"]["total_tokens"] = 0
    else: value["recorded_at"] = study.stamp(at(1))
    with pytest.raises(study.StudyError): study.validate_forecast(value, frozen[1], frozen[2], at(2))


def test_record_clock_is_actual_and_slot_cannot_replace(frozen, tmp_path, monkeypatch):
    root, manifest, metadata = frozen
    monkeypatch.setattr(study, "now", lambda: at(2))
    evidence = tmp_path / "evidence.txt"; evidence.write_text("synthetic response")
    path = save(tmp_path / "import.json", forecast(manifest, metadata))
    result = study.record(root, path, evidence)
    assert result["recorded_at"] == study.stamp(at(2))
    with pytest.raises(FileExistsError): study.record(root, path, evidence)
    monkeypatch.setattr(study, "now", lambda: at(3))
    with pytest.raises(study.StudyError, match="late"): study.record(root, path, evidence)


def test_seal_requires_every_terminal_slot(frozen, monkeypatch):
    monkeypatch.setattr(study, "now", lambda: at(2))
    with pytest.raises(study.StudyError, match="missing or extra"): study.seal(frozen[0])


def test_import_crossing_deadline_during_evidence_write_fails_closed(frozen, tmp_path, monkeypatch):
    root, manifest, metadata = frozen
    evidence = tmp_path / "evidence.txt"; evidence.write_text("synthetic response")
    path = save(tmp_path / "import.json", forecast(manifest, metadata))
    monkeypatch.setattr(study, "now", lambda: at(2))
    original = study.write_new
    def slow_write(path, value):
        original(path, value)
        if path.name == "evidence.bin":
            monkeypatch.setattr(study, "now", lambda: at(3))
    monkeypatch.setattr(study, "write_new", slow_write)
    with pytest.raises(study.StudyError, match="late"): study.record(root, path, evidence)
    assert not (root / "forecasts" / "case-1--single_model--1" / "record.json").exists()


def test_sealing_crossing_deadline_during_hashing_fails_closed(frozen, tmp_path, monkeypatch):
    fill(frozen, tmp_path, monkeypatch)
    (frozen[0] / "seal.json").unlink()
    original = study.records_for
    def slow_read(*args):
        result = original(*args)
        monkeypatch.setattr(study, "now", lambda: at(3))
        return result
    monkeypatch.setattr(study, "records_for", slow_read)
    with pytest.raises(study.StudyError, match="deadline"): study.seal(frozen[0])
    assert not (frozen[0] / "seal.json").exists()


@pytest.mark.parametrize("size", [0, 16 * 1024 * 1024 + 1])
def test_evidence_size_bounded(tmp_path, size):
    path = tmp_path / "evidence.bin"
    with path.open("wb") as handle: handle.truncate(size)
    with pytest.raises(study.StudyError, match="16 MiB"): study.read_evidence(path)


@pytest.mark.parametrize("change", ["study", "packets", "extra_slot", "record", "evidence", "seal"])
def test_modified_artifacts_rejected_on_evaluation(frozen, tmp_path, monkeypatch, change):
    sealed = fill(frozen, tmp_path, monkeypatch)
    root = frozen[0]
    if change in ("study", "packets"):
        with (root / (change + ".json")).open("a") as handle: handle.write(" ")
    elif change == "extra_slot": (root / "forecasts" / "extra").mkdir()
    elif change in ("record", "evidence"):
        path = root / "forecasts" / "case-1--single_model--1" / ("record.json" if change == "record" else "evidence.bin")
        with path.open("a") as handle: handle.write(" ")
    else:
        value = study.read_json(root / "seal.json"); value["terminal_records"] = 3; save(root / "seal.json", value)
    monkeypatch.setattr(study, "now", lambda: at(9))
    with pytest.raises(study.StudyError): study.evaluate(root, save(tmp_path / "observed.json", observations(frozen, sealed)))


@pytest.mark.parametrize("change", ["partial_window", "wrong_experiment", "wrong_a", "wrong_b", "wrong_event", "impossible_counts",
    "bool_counts", "negative", "missing_case", "duplicate_case", "low_sample", "unattested", "wrong_seal", "extra"])
def test_observations_reject_wrong_contract(frozen, tmp_path, monkeypatch, change):
    sealed = fill(frozen, tmp_path, monkeypatch)
    value = observations(frozen, sealed)
    case = value["cases"][0]
    if change == "partial_window": case["window_ended_at"] = study.stamp(at(7))
    elif change == "wrong_experiment": case["experiment_id"] = "other"
    elif change == "wrong_a": case["variant_a_id"] = "copy-b"
    elif change == "wrong_b": case["variant_b_id"] = "copy-a"
    elif change == "wrong_event": case["event_id"] = "page-view"
    elif change == "impossible_counts": case["responders_a"] = 101
    elif change == "bool_counts": case["responders_a"] = True
    elif change == "negative": case["responders_a"] = -1
    elif change == "missing_case": value["cases"] = []
    elif change == "duplicate_case": value["cases"].append(deepcopy(case))
    elif change == "low_sample": case["unique_visitors_b"] = 99
    elif change == "unattested": value["attestations"]["counts_from_event_system"] = False
    elif change == "wrong_seal": value["seal_sha256"] = "0" * 64
    else: case["conversion_revenue"] = 1
    monkeypatch.setattr(study, "now", lambda: at(9))
    with pytest.raises(study.StudyError): study.evaluate(frozen[0], save(tmp_path / "observed.json", value))
    assert not (frozen[0] / "evaluation.json").exists()


def test_early_evaluation_rejected_and_result_is_exclusive(frozen, tmp_path, monkeypatch):
    sealed = fill(frozen, tmp_path, monkeypatch)
    path = save(tmp_path / "observed.json", observations(frozen, sealed))
    monkeypatch.setattr(study, "now", lambda: at(7))
    with pytest.raises(study.StudyError, match="incomplete"): study.evaluate(frozen[0], path)
    monkeypatch.setattr(study, "now", lambda: at(9))
    study.evaluate(frozen[0], path)
    with pytest.raises(study.StudyError, match="already exist"): study.evaluate(frozen[0], path)


def test_sealed_study_rejects_later_record_or_seal(frozen, tmp_path, monkeypatch):
    fill(frozen, tmp_path, monkeypatch)
    with pytest.raises(study.StudyError, match="already sealed"):
        study.record(frozen[0], tmp_path / "import.json", tmp_path / "evidence.txt")
    with pytest.raises(FileExistsError): study.seal(frozen[0])


def test_late_seal_and_clock_rollback_rejected(frozen, tmp_path, monkeypatch):
    fill(frozen, tmp_path, monkeypatch)
    # Remove only in this synthetic test to exercise the timestamp gates.
    (frozen[0] / "seal.json").unlink()
    monkeypatch.setattr(study, "now", lambda: at(3))
    with pytest.raises(study.StudyError, match="deadline"): study.seal(frozen[0])
    monkeypatch.setattr(study, "now", lambda: at(1))
    with pytest.raises(study.StudyError, match="backwards"): study.seal(frozen[0])


def test_json_duplicate_and_nonfinite_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"x":1,"x":2}')
    with pytest.raises(study.StudyError, match="Duplicate"): study.read_json(path)
    path.write_text('{"x":NaN}')
    with pytest.raises(study.StudyError, match="Nonfinite"): study.read_json(path)


def test_stdlib_cli_validates_but_refuses_demo(tmp_path):
    example = SCRIPT.parent / "templates" / "prospective-study.example.json"
    run = subprocess.run([sys.executable, "-S", str(SCRIPT), "validate", str(example)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["example_only"] is True
    run = subprocess.run([sys.executable, "-S", str(SCRIPT), "freeze", str(example), str(tmp_path / "demo")], capture_output=True, text=True)
    assert run.returncode == 2
    assert "example" in run.stderr
    assert not (tmp_path / "demo").exists()
