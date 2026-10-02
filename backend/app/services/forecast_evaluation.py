"""Strict, standard-library-only historical binary forecast scoring.

This scores supplied forecasts, not MiroFish's uncalibrated generated scenarios.
Timestamp checks verify the supplied record contract, not upstream source authenticity.
"""
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import random
import statistics


class EvaluationError(ValueError):
    """A dataset cannot be evaluated without weakening the audit contract."""


def _date(value, field):
    if not isinstance(value, str) or "T" not in value:
        raise EvaluationError(f"{field} requires an ISO 8601 timestamp with timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise EvaluationError(f"Invalid {field} timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvaluationError(f"{field} requires a timezone")
    return parsed.astimezone(timezone.utc)


def _probability(value, field="probability"):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise EvaluationError(f"{field} must be a finite number between 0 and 1")
    return float(value)


def _identifier(value, field):
    if not isinstance(value, str) or not value.strip():
        raise EvaluationError(f"{field} must be a nonempty string")
    return value


def _records(dataset, name, key):
    values = dataset.get(name)
    if not isinstance(values, list):
        raise EvaluationError(f"{name} must be a list")
    indexed = {}
    for record in values:
        if not isinstance(record, dict):
            raise EvaluationError(f"{name} records must be objects")
        identifier = _identifier(record.get(key), f"{name}.{key}")
        if identifier in indexed:
            raise EvaluationError(f"Duplicate {name} {key}: {identifier}")
        indexed[identifier] = record
    return indexed


def _loss(probability, outcome, epsilon=1e-15):
    clipped = min(1 - epsilon, max(epsilon, probability))
    return {"brier_score": (probability - outcome) ** 2,
            "log_loss": -math.log(clipped if outcome == 1 else 1 - clipped)}


def _metrics(rows):
    losses = [_loss(p, y) for p, y in rows]
    return {key: statistics.fmean(row[key] for row in losses) for key in ("brier_score", "log_loss")}


def _bins(rows, count):
    bins = []
    for index in range(count):
        selected = [(p, y) for p, y in rows if min(int(p * count), count - 1) == index]
        bins.append({"lower": index / count, "upper": (index + 1) / count,
                     "upper_inclusive": index == count - 1, "count": len(selected),
                     "mean_probability": statistics.fmean(p for p, _ in selected) if selected else None,
                     "observed_frequency": statistics.fmean(y for _, y in selected) if selected else None})
    return bins


def _percentile(values, fraction):
    ordered = sorted(values)
    location = (len(ordered) - 1) * fraction
    left = math.floor(location); right = math.ceil(location)
    return ordered[left] + (ordered[right] - ordered[left]) * (location - left)


def evaluate_forecasts(dataset, *, bins=10, bootstrap_samples=1000, seed=0):
    """Validate dates/IDs first, then score the mean probability for each case.

    Repeated runs must use the same case cutoff. Cases receive equal weight.
    Baseline is an explicitly supplied fixed probability declared before all cutoffs.
    """
    if not isinstance(dataset, dict) or type(dataset.get("version")) is not int or dataset.get("version") != 1:
        raise EvaluationError("Dataset must be an object with version 1")
    if not isinstance(dataset.get("synthetic"), bool):
        raise EvaluationError("synthetic must explicitly be true or false")
    if isinstance(bins, bool) or not isinstance(bins, int) or not 1 <= bins <= 100:
        raise EvaluationError("bins must be an integer from 1 to 100")
    if isinstance(bootstrap_samples, bool) or not isinstance(bootstrap_samples, int) or not 0 <= bootstrap_samples <= 100000:
        raise EvaluationError("bootstrap_samples must be an integer from 0 to 100000")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise EvaluationError("seed must be an integer")
    evaluated_at = _date(dataset.get("evaluated_at"), "evaluated_at")
    evidence = _records(dataset, "evidence", "id")
    forecasts = _records(dataset, "forecasts", "forecast_id")
    outcomes = _records(dataset, "outcomes", "case_id")
    if not forecasts:
        raise EvaluationError("forecasts must contain at least one record")
    baseline = dataset.get("baseline")
    if not isinstance(baseline, dict):
        raise EvaluationError("baseline must declare a fixed probability and declared_at")
    baseline_probability = _probability(baseline.get("probability"), "baseline probability")
    baseline_date = _date(baseline.get("declared_at"), "baseline.declared_at")
    baseline_label = _identifier(baseline.get("label"), "baseline.label")

    evidence_dates = {}
    for eid, record in evidence.items():
        published = _date(record.get("published_at"), f"evidence[{eid}].published_at")
        available = _date(record.get("available_at"), f"evidence[{eid}].available_at")
        if available < published:
            raise EvaluationError(f"Evidence {eid} availability precedes publication")
        evidence_dates[eid] = (published, available)
    outcome_dates = {}
    for case_id, record in outcomes.items():
        if type(record.get("outcome")) is not int or record["outcome"] not in (0, 1):
            raise EvaluationError(f"outcome for {case_id} must be integer 0 or 1")
        resolved = _date(record.get("resolved_at"), f"outcome[{case_id}].resolved_at")
        if resolved > evaluated_at:
            raise EvaluationError(f"outcome for {case_id} is unresolved at evaluated_at")
        outcome_dates[case_id] = resolved

    grouped = defaultdict(list)
    run_cases = set()
    case_cutoffs = {}
    run_ids = set()
    for fid, forecast in forecasts.items():
        case_id = _identifier(forecast.get("case_id"), f"forecast[{fid}].case_id")
        run_id = _identifier(forecast.get("run_id"), f"forecast[{fid}].run_id")
        probability = _probability(forecast.get("probability"), f"forecast[{fid}].probability")
        issued = _date(forecast.get("issued_at"), f"forecast[{fid}].issued_at")
        cutoff = _date(forecast.get("as_of"), f"forecast[{fid}].as_of")
        if cutoff > issued:
            raise EvaluationError(f"forecast {fid} as_of is later than issued_at")
        if case_id not in outcomes:
            raise EvaluationError(f"Missing outcome for {case_id}")
        if issued >= outcome_dates[case_id]:
            raise EvaluationError(f"forecast {fid} must be issued before the outcome resolves")
        if baseline_date > cutoff:
            raise EvaluationError(f"baseline must be declared no later than forecast {fid} as_of")
        if (case_id, run_id) in run_cases:
            raise EvaluationError(f"Duplicate case/run pair: {case_id}/{run_id}")
        run_cases.add((case_id, run_id)); run_ids.add(run_id)
        if case_id in case_cutoffs and case_cutoffs[case_id] != cutoff:
            raise EvaluationError(f"Repeated runs for {case_id} must share the same as_of")
        case_cutoffs[case_id] = cutoff
        cited = forecast.get("evidence_ids")
        if not isinstance(cited, list) or any(not isinstance(item, str) or not item for item in cited):
            raise EvaluationError(f"forecast {fid} evidence_ids must be an explicit list of IDs")
        if len(cited) != len(set(cited)):
            raise EvaluationError(f"forecast {fid} contains duplicate evidence IDs")
        for eid in cited:
            if eid not in evidence_dates:
                raise EvaluationError(f"forecast {fid} cites missing evidence {eid}")
            if any(date > cutoff for date in evidence_dates[eid]):
                raise EvaluationError(f"forecast {fid} uses future evidence {eid} after its as_of cutoff")
        grouped[case_id].append(probability)

    unused_outcomes = set(outcomes) - set(grouped)
    if unused_outcomes:
        raise EvaluationError("Outcomes without forecasts: " + ", ".join(sorted(unused_outcomes)))
    cases = []
    rows = []
    for case_id in sorted(grouped):
        probabilities = grouped[case_id]
        mean = statistics.fmean(probabilities)
        outcome = outcomes[case_id]["outcome"]
        rows.append((mean, outcome))
        cases.append({"case_id": case_id, "as_of": case_cutoffs[case_id].isoformat(), "outcome": outcome,
                      "run_count": len(probabilities), "mean_probability": mean,
                      "probability_stddev": statistics.pstdev(probabilities),
                      "minimum_probability": min(probabilities), "maximum_probability": max(probabilities),
                      "metrics": _loss(mean, outcome)})
    comparator_rows = [(baseline_probability, y) for _, y in rows]
    metrics = _metrics(rows)
    baseline_metrics = _metrics(comparator_rows)
    uncertainty = {"method": "case_bootstrap_percentile", "level": 0.95, "resamples": bootstrap_samples,
                   "seed": seed, "unit": "independent_case", "intervals": None,
                   "interpretation": "Sampling variability under an independent-case assumption; not forecast confidence or proof of calibration."}
    if len(rows) > 1 and bootstrap_samples:
        rng = random.Random(seed)
        draws = [_metrics(rng.choices(rows, k=len(rows))) for _ in range(bootstrap_samples)]
        uncertainty["intervals"] = {key: {"lower": _percentile([draw[key] for draw in draws], 0.025),
                                          "upper": _percentile([draw[key] for draw in draws], 0.975)}
                                    for key in metrics}
    else:
        uncertainty["reason"] = "At least two independent cases and nonzero resamples are required."
    canonical = json.dumps(dataset, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {
        "version": 1, "synthetic": dataset["synthetic"], "evaluated_at": evaluated_at.isoformat(),
        "forecast_accuracy_claim": False,
        "dataset_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "counts": {"forecasts": len(forecasts), "independent_cases": len(cases), "runs": len(run_ids)},
        "metrics": metrics,
        "baseline": {"label": baseline_label, "probability": baseline_probability, "declared_at": baseline_date.isoformat(), **baseline_metrics},
        "improvement_over_baseline": {key: baseline_metrics[key] - metrics[key] for key in metrics},
        "calibration_bins": _bins(rows, bins), "baseline_calibration_bins": _bins(comparator_rows, bins),
        "cases": cases, "uncertainty": uncertainty,
        "method": {"aggregation": "equal_weight_cases_using_mean_probability_across_repeated_runs",
                   "log_loss_clip_epsilon": 1e-15, "calibration_bins": bins,
                   "date_validation": "published_at_and_available_at_no_later_than_as_of; as_of<=issued_at<resolved_at<=evaluated_at"},
        "limitations": [
            "Synthetic results are a software demonstration and provide no empirical forecast accuracy evidence." if dataset["synthetic"] else
            "Scores describe only these submitted cases; generalization requires a representative, preregistered holdout evaluation.",
            "Timestamps and outcomes are supplied by the dataset; upstream authenticity is not independently verified.",
            "This harness cannot detect outcome information memorized by a model or undeclared evidence.",
            "Repeated-run spread measures simulation variability, not calibrated event probabilities.",
            "Calibration bins are descriptive; sparse or selected cases cannot establish calibration.",
            "The fixed baseline is an explicit comparator, not an estimated real-world event prior.",
        ],
    }
