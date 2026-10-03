from copy import deepcopy
import json
import math
from pathlib import Path
import subprocess
import sys

import pytest

from app.services.comparative_benchmark import (
    BenchmarkError, build_protocol, canonical_sha256, evaluate_benchmark, prompt_case,
)


@pytest.fixture
def bundle():
    inputs = {"schema_version": 1, "dataset_id": "test-fixture", "cases": [
        {"case_id": case_id, "split": "pilot", "headline_a": "First headline",
         "headline_b": "Second headline", "audience_context": "Historical reader sample",
         "shared_image_unavailable": True} for case_id in ("c1", "c2")
    ]}
    outcomes = {"schema_version": 1, "dataset_id": "test-fixture", "cases": [
        {"case_id": "c1", "impressions_a": 100, "clicks_a": 20,
         "impressions_b": 100, "clicks_b": 10, "observed_winner": "A"},
        {"case_id": "c2", "impressions_a": 100, "clicks_a": 10,
         "impressions_b": 100, "clicks_b": 20, "observed_winner": "B"},
    ]}
    protocol = build_protocol(inputs, repeats=2, bootstrap_samples=100, bootstrap_seed=17)
    records = []
    for case_id in ("c1", "c2"):
        for method, values in (("single_model", [0.5, 0.5]), ("mirofish", [0.9, 0.7])):
            for repeat, value in enumerate(values, 1):
                records.append({"case_id": case_id, "method": method, "repeat": repeat,
                                "status": "ok", "probability_a": value if case_id == "c1" else 1-value,
                                "error": None, "elapsed_seconds": 2.0,
                                "usage": {"calls": 1, "input_tokens": 10, "output_tokens": 5,
                                          "total_tokens": 15, "cost_usd": None}})
    predictions = {"schema_version": 1, "protocol_sha256": canonical_sha256(protocol), "records": records}
    return inputs, outcomes, protocol, predictions


def test_hand_calculated_metrics_and_case_level_repetition(bundle):
    result = evaluate_benchmark(*bundle)
    metrics = result["complete_paired_metrics"]
    assert metrics["mirofish"]["brier_score"] == pytest.approx(0.04)
    assert metrics["mirofish"]["log_loss"] == pytest.approx(-math.log(0.8))
    assert metrics["mirofish"]["directional_accuracy"] == 1
    assert metrics["single_model"]["brier_score"] == 0.25
    assert metrics["single_model"]["directional_accuracy"] == 0.5
    assert result["chance"]["scored_cases"] == 2
    assert result["chance"]["brier_score"] == 0.25
    assert result["counts"]["scheduled_records"] == 8
    assert result["counts"]["complete_paired_cases"] == 2
    assert result["paired_comparison"]["improvement"]["brier_score"] == pytest.approx(0.21)
    assert result["paired_comparison"]["uncertainty"]["unit"] == "case"
    assert result["usage"]["mirofish"]["total_tokens"]["total"] == 60
    assert result["usage"]["mirofish"]["cost_usd"]["total"] is None
    assert result["retrospective_replay"] is True
    assert result["forecast_accuracy_claim"] is False
    assert result == evaluate_benchmark(*bundle)


def test_missing_and_failed_runs_are_counted_and_penalized(bundle):
    inputs, outcomes, protocol, predictions = bundle
    missing = predictions["records"].pop(3)  # c1 MiroFish repeat 2
    failed = predictions["records"][-1]
    failed.update(status="failed", probability_a=None, error="Malformed response", elapsed_seconds=3)
    result = evaluate_benchmark(*bundle)
    assert missing["method"] == "mirofish"
    assert result["counts"]["missing_records"] == 1
    assert result["counts"]["failed_records"] == 1
    assert result["counts"]["complete_paired_cases"] == 0
    assert result["complete_paired_metrics"]["mirofish"]["brier_score"] is None
    # Each observed MiroFish probability was 0.9 toward the winner; the failed/missing
    # repeat receives worst-case 0.0 toward that winner, giving a mean 0.45.
    assert result["failure_adjusted_metrics"]["mirofish"]["brier_score"] == pytest.approx(0.55**2)
    assert result["failure_adjusted_metrics"]["mirofish"]["scored_cases"] == 2
    assert result["usage"]["mirofish"]["elapsed_seconds"]["total"] is None
    assert result["usage"]["mirofish"]["elapsed_seconds"]["known_subtotal"] == 7
    assert result["usage"]["mirofish"]["calls"]["known_subtotal"] == 3


def test_identical_methods_have_zero_delta_and_zero_interval(bundle):
    for record in bundle[3]["records"]:
        record["probability_a"] = 0.7
    result = evaluate_benchmark(*bundle)
    for value in result["paired_comparison"]["improvement"].values():
        assert value == 0
    for interval in result["paired_comparison"]["uncertainty"]["intervals"].values():
        assert interval == {"lower": 0, "upper": 0}


def test_one_case_has_no_bootstrap_interval(bundle):
    inputs, outcomes, _, predictions = bundle
    protocol = build_protocol(inputs, case_ids=["c1"], bootstrap_samples=100)
    predictions["protocol_sha256"] = canonical_sha256(protocol)
    predictions["records"] = [r for r in predictions["records"] if r["case_id"] == "c1"]
    result = evaluate_benchmark(inputs, outcomes, protocol, predictions)
    assert result["paired_comparison"]["uncertainty"]["intervals"] is None
    assert "two" in result["paired_comparison"]["uncertainty"]["reason"]


def test_swapping_labels_does_not_change_scores(bundle):
    before = evaluate_benchmark(*bundle)
    inputs, outcomes, protocol, predictions = deepcopy(bundle)
    for case in inputs["cases"]:
        case["headline_a"], case["headline_b"] = case["headline_b"], case["headline_a"]
    for case in outcomes["cases"]:
        for count in ("impressions", "clicks"):
            case[count + "_a"], case[count + "_b"] = case[count + "_b"], case[count + "_a"]
        case["observed_winner"] = "B" if case["observed_winner"] == "A" else "A"
    protocol["inputs_sha256"] = canonical_sha256(inputs)
    predictions["protocol_sha256"] = canonical_sha256(protocol)
    for record in predictions["records"]:
        record["probability_a"] = 1-record["probability_a"]
    after = evaluate_benchmark(inputs, outcomes, protocol, predictions)
    for method in ("single_model", "mirofish"):
        for metric in ("brier_score", "log_loss", "directional_accuracy"):
            assert after["complete_paired_metrics"][method][metric] == pytest.approx(before["complete_paired_metrics"][method][metric])


@pytest.mark.parametrize("extra", ["observed_winner", "clicks_a", "ctr_a", "winner", "rationale"])
def test_unknown_prompt_fields_are_rejected_before_generation(bundle, extra):
    inputs = deepcopy(bundle[0])
    inputs["cases"][0][extra] = "outcome information"
    with pytest.raises(BenchmarkError, match="Unknown"):
        build_protocol(inputs)
    with pytest.raises(BenchmarkError, match="Unknown"):
        prompt_case(inputs, "c1")


def test_prompt_payload_contains_only_allowed_case_material(bundle):
    payload = prompt_case(bundle[0], "c1")
    assert set(payload) == {"headline_a", "headline_b", "audience_context", "shared_image_unavailable"}
    assert "case_id" not in payload  # Opaque identity is not needed by the model.


@pytest.mark.parametrize("probability", [True, False, -0.01, 1.01, float("nan"), float("inf"), "0.5", None])
def test_invalid_probabilities_rejected(bundle, probability):
    bundle[3]["records"][0]["probability_a"] = probability
    with pytest.raises(BenchmarkError, match="probability_a"):
        evaluate_benchmark(*bundle)


@pytest.mark.parametrize("mutation", ["duplicate", "unknown_case", "unknown_method", "unknown_repeat", "unknown_field", "failed_with_probability", "hash", "bool_repeat", "bool_usage"])
def test_prediction_contract_rejected(bundle, mutation):
    record = bundle[3]["records"][0]
    if mutation == "duplicate":
        bundle[3]["records"].append(deepcopy(record))
    elif mutation == "unknown_case": record["case_id"] = "unknown"
    elif mutation == "unknown_method": record["method"] = "other"
    elif mutation == "unknown_repeat": record["repeat"] = 3
    elif mutation == "unknown_field": record["surprise"] = True
    elif mutation == "failed_with_probability": record.update(status="failed", error="error")
    elif mutation == "hash": bundle[3]["protocol_sha256"] = "0"*64
    elif mutation == "bool_repeat": record["repeat"] = True
    elif mutation == "bool_usage": record["usage"]["calls"] = True
    with pytest.raises(BenchmarkError):
        evaluate_benchmark(*bundle)


def test_outcomes_must_match_counts_and_all_inputs(bundle):
    bundle[1]["cases"][0]["observed_winner"] = "B"
    with pytest.raises(BenchmarkError, match="winner"):
        evaluate_benchmark(*bundle)
    bundle[1]["cases"].pop()
    with pytest.raises(BenchmarkError):
        evaluate_benchmark(*bundle)


def test_exact_ties_excluded_but_scheduled_usage_and_completion_remain(bundle):
    bundle[1]["cases"][0].update(clicks_a=10, observed_winner="tie")
    result = evaluate_benchmark(*bundle)
    assert result["counts"]["scheduled_cases"] == 2
    assert result["counts"]["excluded_ties"] == 1
    assert result["counts"]["complete_paired_cases"] == 1
    assert result["counts"]["scheduled_records"] == 8
    assert result["failure_adjusted_metrics"]["mirofish"]["scored_cases"] == 1


def test_changed_inputs_and_protocol_rejected(bundle):
    bundle[0]["cases"][0]["headline_a"] = "Changed later"
    with pytest.raises(BenchmarkError, match="hash"):
        evaluate_benchmark(*bundle)


def test_byte_and_canonical_input_hashes_are_distinct_and_validated(bundle):
    import hashlib
    inputs, outcomes, protocol, predictions = bundle
    raw = json.dumps(inputs, indent=2).encode()
    raw_hash = hashlib.sha256(raw).hexdigest()
    outcomes["inputs_sha256"] = raw_hash
    outcomes["inputs_canonical_sha256"] = canonical_sha256(inputs)
    assert raw_hash != outcomes["inputs_canonical_sha256"]
    result = evaluate_benchmark(*bundle, inputs_file_sha256=raw_hash)
    assert result["inputs_file_sha256"] == raw_hash
    with pytest.raises(BenchmarkError, match="raw-file"):
        evaluate_benchmark(*bundle, inputs_file_sha256="0"*64)
    outcomes["inputs_canonical_sha256"] = "0"*64
    with pytest.raises(BenchmarkError, match="canonical"):
        evaluate_benchmark(*bundle)


def test_repeated_source_experiment_cannot_inflate_independent_cases(bundle):
    for case in bundle[1]["cases"]:
        case["source_test_id"] = "same-experiment"
    with pytest.raises(BenchmarkError, match="dependent"):
        evaluate_benchmark(*bundle)


def test_all_ties_and_zero_predictions_remain_a_valid_empty_quality_result(bundle):
    for case in bundle[1]["cases"]:
        case.update(clicks_a=10, clicks_b=10, observed_winner="tie")
    bundle[3]["records"] = []
    result = evaluate_benchmark(*bundle)
    assert result["counts"]["excluded_ties"] == 2
    assert result["counts"]["missing_records"] == 8
    assert result["chance"]["brier_score"] is None
    assert result["failure_adjusted_comparison"]["improvement"]["brier_score"] is None
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("field,value", [("schema_version", True), ("repeats", True), ("repeats", 0),
                                         ("bootstrap_samples", -1), ("bootstrap_seed", True),
                                         ("extra", "unknown"), ("tie_policy", "ignore"),
                                         ("failure_policy", "drop")])
def test_invalid_protocol_fields_cannot_change_comparison(bundle, field, value):
    bundle[2][field] = value
    bundle[3]["protocol_sha256"] = canonical_sha256(bundle[2])
    with pytest.raises(BenchmarkError):
        evaluate_benchmark(*bundle)


def test_protocol_metadata_must_be_finite_json_and_case_ids_explicit_list(bundle):
    for configuration in ({"temperature": float("nan")}, {"set": {1}}, {"huge": 10**1000}):
        with pytest.raises(BenchmarkError):
            build_protocol(bundle[0], configuration=configuration)
    with pytest.raises(BenchmarkError, match="explicit list"):
        build_protocol(bundle[0], case_ids="c1")


def test_known_zero_cost_is_kept_and_failed_usage_not_dropped(bundle):
    for record in bundle[3]["records"]:
        record["usage"]["cost_usd"] = 0.0
    bundle[3]["records"][0].update(status="failed", error="provider failed after billing", probability_a=None)
    result = evaluate_benchmark(*bundle)
    assert result["usage"]["single_model"]["cost_usd"]["total"] == 0
    assert result["usage"]["single_model"]["calls"]["total"] == 4
    assert result["usage"]["single_model"]["elapsed_seconds"]["total"] == 8


def test_cli_runs_without_flask_and_rejects_duplicate_json_keys(bundle, tmp_path):
    paths = []
    for name, value in zip(("inputs", "outcomes", "protocol", "predictions"), bundle):
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(value))
        paths.append(str(path))
    script = Path(__file__).resolve().parents[1] / "scripts" / "score_comparative_benchmark.py"
    result = subprocess.run([sys.executable, "-S", str(script), *paths], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["counts"]["complete_paired_cases"] == 2
    Path(paths[0]).write_text('{"schema_version": 1, "schema_version": 1}')
    result = subprocess.run([sys.executable, "-S", str(script), *paths], capture_output=True, text=True)
    assert result.returncode == 2
    assert "Duplicate JSON field" in json.loads(result.stderr)["error"]
