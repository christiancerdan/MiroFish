from copy import deepcopy
import json
import math
from pathlib import Path
import subprocess
import sys

import pytest

from app.services.forecast_evaluation import EvaluationError, evaluate_forecasts


@pytest.fixture
def dataset():
    return {
        "version": 1, "synthetic": True, "evaluated_at": "2025-04-01T00:00:00Z",
        "baseline": {"probability": 0.5, "declared_at": "2025-01-01T00:00:00Z", "label": "Fixed 50% comparator"},
        "evidence": [{"id": "source-1", "published_at": "2025-01-02T00:00:00Z", "available_at": "2025-01-03T00:00:00Z", "text": "Synthetic source"}],
        "forecasts": [
            {"forecast_id": "f1", "case_id": "c1", "run_id": "r1", "issued_at": "2025-02-02T00:00:00Z", "as_of": "2025-02-01T00:00:00Z", "probability": 0.8, "evidence_ids": ["source-1"]},
            {"forecast_id": "f2", "case_id": "c2", "run_id": "r1", "issued_at": "2025-02-02T00:00:00Z", "as_of": "2025-02-01T00:00:00Z", "probability": 0.2, "evidence_ids": ["source-1"]},
        ],
        "outcomes": [
            {"case_id": "c1", "outcome": 1, "resolved_at": "2025-03-01T00:00:00Z"},
            {"case_id": "c2", "outcome": 0, "resolved_at": "2025-03-01T00:00:00Z"},
        ],
    }


def test_scores_and_fixed_baseline_are_correct(dataset):
    result = evaluate_forecasts(dataset, bootstrap_samples=100)
    assert result["metrics"]["brier_score"] == pytest.approx(0.04)
    assert result["metrics"]["log_loss"] == pytest.approx(-math.log(0.8))
    assert result["baseline"]["brier_score"] == 0.25
    assert result["baseline"]["log_loss"] == pytest.approx(math.log(2))
    assert sum(b["count"] for b in result["calibration_bins"]) == 2
    assert result["synthetic"] is True
    assert result["forecast_accuracy_claim"] is False
    assert result["dataset_sha256"]


@pytest.mark.parametrize("probability", [-0.01, 1.01, float("nan"), float("inf"), True, "0.6", None])
def test_invalid_probabilities_fail(dataset, probability):
    dataset["forecasts"][0]["probability"] = probability
    with pytest.raises(EvaluationError, match="probability"):
        evaluate_forecasts(dataset)


@pytest.mark.parametrize("field", ["issued_at", "as_of"])
def test_missing_forecast_dates_fail(dataset, field):
    del dataset["forecasts"][0][field]
    with pytest.raises(EvaluationError, match=field):
        evaluate_forecasts(dataset)


@pytest.mark.parametrize("field", ["published_at", "available_at"])
def test_evidence_without_dates_cannot_pass_asof_audit(dataset, field):
    del dataset["evidence"][0][field]
    with pytest.raises(EvaluationError, match=field):
        evaluate_forecasts(dataset)


@pytest.mark.parametrize("field", ["published_at", "available_at"])
def test_future_evidence_is_rejected(dataset, field):
    dataset["evidence"][0][field] = "2025-02-01T00:00:01Z"
    with pytest.raises(EvaluationError, match="future evidence|availability"):
        evaluate_forecasts(dataset)


def test_dates_require_timezone_and_real_calendar_date(dataset):
    for bad in ["2025-02-01", "2025-02-01T00:00:00", "2025-02-31T00:00:00Z", "not-date"]:
        dataset["forecasts"][0]["as_of"] = bad
        with pytest.raises(EvaluationError, match="as_of"):
            evaluate_forecasts(dataset)


def test_resolution_must_follow_forecast_and_be_known_at_evaluation(dataset):
    for bad in ["2025-02-02T00:00:00Z", "2026-01-01T00:00:00Z"]:
        dataset["outcomes"][0]["resolved_at"] = bad
        with pytest.raises(EvaluationError):
            evaluate_forecasts(dataset)


def test_outcomes_must_be_binary_and_complete(dataset):
    for bad in [True, 2, "1", None]:
        dataset["outcomes"][0]["outcome"] = bad
        with pytest.raises(EvaluationError, match="outcome"):
            evaluate_forecasts(dataset)
    dataset["outcomes"] = []
    with pytest.raises(EvaluationError, match="outcome"):
        evaluate_forecasts(dataset)


def test_missing_duplicate_and_cross_run_ids_are_rejected(dataset):
    variants = []
    case = deepcopy(dataset); case["forecasts"][0]["evidence_ids"] = ["made-up"]; variants.append(case)
    case = deepcopy(dataset); case["forecasts"].append(deepcopy(case["forecasts"][0])); variants.append(case)
    case = deepcopy(dataset); extra = deepcopy(case["forecasts"][0]); extra["forecast_id"] = "new"; case["forecasts"].append(extra); variants.append(case)
    case = deepcopy(dataset); case["evidence"].append(deepcopy(case["evidence"][0])); variants.append(case)
    case = deepcopy(dataset); case["outcomes"].append(deepcopy(case["outcomes"][0])); variants.append(case)
    for variant in variants:
        with pytest.raises(EvaluationError):
            evaluate_forecasts(variant)


def test_hindsight_baseline_and_changing_repeated_run_cutoffs_rejected(dataset):
    dataset["baseline"]["declared_at"] = "2025-02-02T00:00:00Z"
    with pytest.raises(EvaluationError, match="baseline"):
        evaluate_forecasts(dataset)
    dataset["baseline"]["declared_at"] = "2025-01-01T00:00:00Z"
    second = deepcopy(dataset["forecasts"][0])
    second.update(forecast_id="repeat", run_id="r2", as_of="2025-02-01T01:00:00Z")
    dataset["forecasts"].append(second)
    with pytest.raises(EvaluationError, match="as_of"):
        evaluate_forecasts(dataset)


def test_repeated_runs_do_not_inflate_independent_case_count(dataset):
    second = deepcopy(dataset["forecasts"][0]); second.update(forecast_id="repeat", run_id="r2", probability=0.6)
    dataset["forecasts"].append(second)
    result = evaluate_forecasts(dataset, bootstrap_samples=100, seed=17)
    assert result["counts"] == {"forecasts": 3, "independent_cases": 2, "runs": 2}
    assert result["cases"][0]["mean_probability"] == pytest.approx(0.7)
    assert result["cases"][0]["probability_stddev"] == pytest.approx(0.1)
    assert result["cases"][0]["run_count"] == 2
    assert result == evaluate_forecasts(dataset, bootstrap_samples=100, seed=17)


def test_probabilities_zero_and_one_are_json_safe_and_clipping_is_declared(dataset):
    dataset["forecasts"][0]["probability"] = 0.0
    dataset["forecasts"][1]["probability"] = 1.0
    result = evaluate_forecasts(dataset, bootstrap_samples=0)
    assert math.isfinite(result["metrics"]["log_loss"])
    assert result["method"]["log_loss_clip_epsilon"] == 1e-15
    json.dumps(result, allow_nan=False)


def test_stdlib_cli_writes_result_without_importing_flask(dataset, tmp_path):
    source = tmp_path / "forecast.json"; output = tmp_path / "result.json"
    source.write_text(json.dumps(dataset))
    script = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_forecasts.py"
    completed = subprocess.run([sys.executable, "-S", str(script), str(source), "--output", str(output), "--bootstrap-samples", "10"], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(output.read_text())["counts"]["independent_cases"] == 2
    dataset["forecasts"][0]["probability"] = -1
    source.write_text(json.dumps(dataset))
    completed = subprocess.run([sys.executable, "-S", str(script), str(source)], capture_output=True, text=True)
    assert completed.returncode == 2
    assert "probability" in json.loads(completed.stderr)["error"]
