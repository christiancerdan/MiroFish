"""Strict, standard-library scoring of a frozen retrospective paired experiment.

This is a replay of public historical material, not an out-of-time forecast.
Inputs and outcomes deliberately have separate schemas and prompt_case emits only
allowed stimulus fields. Hashes detect changed artifacts; they do not establish
preregistration, prevent model memorization, or authenticate historical outcomes.
"""
import hashlib
import json
import math
import random
import statistics


METHODS = ("single_model", "mirofish")
METRICS = ("brier_score", "log_loss", "directional_accuracy")
EPSILON = 1e-15
_INPUT_CASE_FIELDS = {"case_id", "split", "headline_a", "headline_b", "audience_context", "shared_image_unavailable"}
_OUTCOME_REQUIRED = {"case_id", "impressions_a", "impressions_b", "clicks_a", "clicks_b", "observed_winner"}
_OUTCOME_OPTIONAL = {"split", "source_test_id", "source_row_a", "source_row_b", "ctr_a", "ctr_b",
                     "ctr_difference_a_minus_b", "newcombe95_difference", "binomial_intervals",
                     "difference_interval_includes_zero"}
_USAGE_FIELDS = {"calls", "input_tokens", "output_tokens", "total_tokens", "cost_usd"}


class BenchmarkError(ValueError):
    """A benchmark artifact violates the declared comparison contract."""


def _object(value, required, optional=(), *, field):
    if not isinstance(value, dict):
        raise BenchmarkError(f"{field} must be an object")
    unknown = set(value) - set(required) - set(optional)
    missing = set(required) - set(value)
    if unknown:
        raise BenchmarkError(f"Unknown {field} fields: {', '.join(sorted(map(str, unknown)))}")
    if missing:
        raise BenchmarkError(f"Missing {field} fields: {', '.join(sorted(missing))}")
    return value


def _string(value, field):
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkError(f"{field} must be a nonempty string")
    return value


def _integer(value, field, lower=0, upper=None):
    if type(value) is not int or value < lower or (upper is not None and value > upper):
        raise BenchmarkError(f"{field} must be an integer from {lower}" + (f" to {upper}" if upper is not None else ""))
    return value


def _number(value, field, lower=0, upper=None):
    try:
        finite = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise BenchmarkError(f"{field} must be a finite number")
    if value < lower or (upper is not None and value > upper):
        raise BenchmarkError(f"{field} must be between {lower} and {upper}")
    return float(value)


def _json_value(value, field):
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, (int, float)):
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise BenchmarkError(f"{field} contains a nonfinite number")
        return
    if isinstance(value, list):
        for item in value:
            _json_value(item, field)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _json_value(item, field)
        return
    raise BenchmarkError(f"{field} contains a non-JSON value")


def canonical_sha256(value):
    """Hash decoded JSON canonically; this differs from a source file byte hash."""
    _json_value(value, "hashed artifact")
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _digest(value, field):
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise BenchmarkError(f"{field} must be a lowercase SHA256 digest")
    return value


def _version(value, field):
    if type(value) is not int or value != 1:
        raise BenchmarkError(f"{field}.schema_version must be integer 1")


def validate_inputs(inputs):
    """Validate and index the entire input pool before a runner exposes a case."""
    _object(inputs, {"schema_version", "dataset_id", "cases"}, field="inputs")
    _version(inputs["schema_version"], "inputs")
    _string(inputs["dataset_id"], "inputs.dataset_id")
    if not isinstance(inputs["cases"], list) or not inputs["cases"]:
        raise BenchmarkError("inputs.cases must be a nonempty list")
    indexed = {}
    for case in inputs["cases"]:
        _object(case, _INPUT_CASE_FIELDS, field="input case")
        case_id = _string(case["case_id"], "input case.case_id")
        if case_id in indexed:
            raise BenchmarkError(f"Duplicate input case_id: {case_id}")
        if case["split"] not in ("pilot", "reserve"):
            raise BenchmarkError("input case.split must be pilot or reserve")
        for field in ("headline_a", "headline_b", "audience_context"):
            _string(case[field], f"input case.{field}")
        if case["shared_image_unavailable"] is not True:
            raise BenchmarkError("input case.shared_image_unavailable must be true")
        indexed[case_id] = case
    return indexed


def prompt_case(inputs, case_id):
    """Return only stimulus text; no case ID, split, outcome, or provenance."""
    indexed = validate_inputs(inputs)
    _string(case_id, "case_id")
    if case_id not in indexed:
        raise BenchmarkError(f"Unknown input case_id: {case_id}")
    return {field: indexed[case_id][field] for field in
            ("headline_a", "headline_b", "audience_context", "shared_image_unavailable")}


def build_protocol(inputs, *, case_ids=None, repeats=2, bootstrap_samples=2000,
                   bootstrap_seed=0, model=None, configuration=None):
    """Construct a protocol to save before any model calls or outcome scoring."""
    indexed = validate_inputs(inputs)
    if case_ids is not None and not isinstance(case_ids, list):
        raise BenchmarkError("case_ids must be an explicit list")
    protocol = {
        "schema_version": 1, "dataset_id": inputs["dataset_id"], "inputs_sha256": canonical_sha256(inputs),
        "case_ids": ([case_id for case_id, case in indexed.items() if case["split"] == "pilot"]
                     if case_ids is None else list(case_ids)),
        "methods": list(METHODS), "repeats": repeats, "tie_policy": "exclude_exact_ties",
        "failure_policy": "worst_case_missing_repeat", "bootstrap_samples": bootstrap_samples,
        "bootstrap_seed": bootstrap_seed, "model": {} if model is None else model,
        "configuration": {} if configuration is None else configuration,
    }
    _validate_protocol(inputs, protocol, indexed)
    # Detach mutable caller-owned configuration before the caller persists it.
    return json.loads(json.dumps(protocol, ensure_ascii=False, allow_nan=False))


def _validate_protocol(inputs, protocol, indexed):
    required = {"schema_version", "dataset_id", "inputs_sha256", "case_ids", "methods", "repeats", "tie_policy",
                "failure_policy", "bootstrap_samples", "bootstrap_seed", "model", "configuration"}
    _object(protocol, required, field="protocol")
    _version(protocol["schema_version"], "protocol")
    if protocol["dataset_id"] != inputs["dataset_id"]:
        raise BenchmarkError("protocol dataset_id does not match inputs")
    if _digest(protocol["inputs_sha256"], "protocol.inputs_sha256") != canonical_sha256(inputs):
        raise BenchmarkError("Frozen protocol input hash does not match inputs")
    case_ids = protocol["case_ids"]
    if not isinstance(case_ids, list) or not case_ids:
        raise BenchmarkError("protocol.case_ids must be a nonempty list")
    for case_id in case_ids:
        _string(case_id, "protocol.case_ids item")
        if case_id not in indexed:
            raise BenchmarkError(f"Unknown protocol case_id: {case_id}")
    if len(set(case_ids)) != len(case_ids):
        raise BenchmarkError("Duplicate protocol case_ids")
    if protocol["methods"] != list(METHODS):
        raise BenchmarkError("protocol.methods must be ['single_model', 'mirofish']")
    _integer(protocol["repeats"], "protocol.repeats", 1, 100)
    if protocol["tie_policy"] != "exclude_exact_ties":
        raise BenchmarkError("Unknown protocol.tie_policy")
    if protocol["failure_policy"] != "worst_case_missing_repeat":
        raise BenchmarkError("Unknown protocol.failure_policy")
    _integer(protocol["bootstrap_samples"], "protocol.bootstrap_samples", 0, 100000)
    _integer(protocol["bootstrap_seed"], "protocol.bootstrap_seed", 0)
    for field in ("model", "configuration"):
        if not isinstance(protocol[field], dict):
            raise BenchmarkError(f"protocol.{field} must be an object")
        _json_value(protocol[field], f"protocol.{field}")


def _interval(value, field, lower, upper):
    if not isinstance(value, list) or len(value) != 2:
        raise BenchmarkError(f"{field} must be [lower, upper]")
    lo, hi = (_number(item, field, lower, upper) for item in value)
    if lo > hi:
        raise BenchmarkError(f"{field} lower exceeds upper")
    return lo, hi


def _validate_outcomes(inputs, outcomes, indexed, inputs_file_sha256):
    _object(outcomes, {"schema_version", "dataset_id", "cases"},
            {"inputs_sha256", "inputs_canonical_sha256", "selection_manifest_sha256", "label_semantics",
             "uncertainty_assumption", "difference_interval_method"}, field="outcomes")
    _version(outcomes["schema_version"], "outcomes")
    if outcomes["dataset_id"] != inputs["dataset_id"]:
        raise BenchmarkError("outcomes dataset_id does not match inputs")
    for field in ("inputs_sha256", "inputs_canonical_sha256", "selection_manifest_sha256"):
        if field in outcomes:
            _digest(outcomes[field], "outcomes." + field)
    if "inputs_canonical_sha256" in outcomes and outcomes["inputs_canonical_sha256"] != canonical_sha256(inputs):
        raise BenchmarkError("Outcome canonical input hash does not match inputs")
    if inputs_file_sha256 is not None:
        _digest(inputs_file_sha256, "inputs_file_sha256")
        if "inputs_sha256" in outcomes and outcomes["inputs_sha256"] != inputs_file_sha256:
            raise BenchmarkError("Outcome raw-file input hash does not match input file bytes")
    for field in ("label_semantics", "uncertainty_assumption", "difference_interval_method"):
        if field in outcomes:
            _string(outcomes[field], "outcomes." + field)
    if not isinstance(outcomes["cases"], list):
        raise BenchmarkError("outcomes.cases must be a list")
    result = {}
    source_tests = set()
    for case in outcomes["cases"]:
        _object(case, _OUTCOME_REQUIRED, _OUTCOME_OPTIONAL, field="outcome case")
        case_id = _string(case["case_id"], "outcome case.case_id")
        if case_id not in indexed:
            raise BenchmarkError(f"Unknown outcome case_id: {case_id}")
        if case_id in result:
            raise BenchmarkError(f"Duplicate outcome case_id: {case_id}")
        for side in ("a", "b"):
            impressions = _integer(case["impressions_" + side], "impressions_" + side, 1)
            clicks = _integer(case["clicks_" + side], "clicks_" + side, 0, impressions)
            if "ctr_" + side in case:
                ctr = _number(case["ctr_" + side], "ctr_" + side, 0, 1)
                if not math.isclose(ctr, clicks / impressions, rel_tol=1e-12, abs_tol=1e-15):
                    raise BenchmarkError("Outcome CTR does not match counts")
        # Integer cross multiplication preserves exact ties without floating error.
        difference = case["clicks_a"] * case["impressions_b"] - case["clicks_b"] * case["impressions_a"]
        winner = "A" if difference > 0 else "B" if difference < 0 else "tie"
        if case["observed_winner"] != winner:
            raise BenchmarkError(f"Outcome observed_winner disagrees with raw counts for {case_id}")
        if "split" in case and case["split"] != indexed[case_id]["split"]:
            raise BenchmarkError("Outcome split does not match inputs")
        for field in ("source_test_id", "source_row_a", "source_row_b"):
            if field in case:
                _string(case[field], "outcome." + field)
        if "source_test_id" in case:
            if case["source_test_id"] in source_tests:
                raise BenchmarkError("Repeated source_test_id would count dependent pairs as independent cases")
            source_tests.add(case["source_test_id"])
        if "ctr_difference_a_minus_b" in case:
            observed_difference = _number(case["ctr_difference_a_minus_b"], "ctr_difference_a_minus_b", -1, 1)
            expected = case["clicks_a"] / case["impressions_a"] - case["clicks_b"] / case["impressions_b"]
            if not math.isclose(observed_difference, expected, rel_tol=1e-12, abs_tol=1e-15):
                raise BenchmarkError("Outcome CTR difference does not match counts")
        if "newcombe95_difference" in case:
            lo, hi = _interval(case["newcombe95_difference"], "newcombe95_difference", -1, 1)
            if "difference_interval_includes_zero" in case and case["difference_interval_includes_zero"] is not (lo <= 0 <= hi):
                raise BenchmarkError("Outcome interval zero flag is inconsistent")
        if "difference_interval_includes_zero" in case and type(case["difference_interval_includes_zero"]) is not bool:
            raise BenchmarkError("difference_interval_includes_zero must be boolean")
        if "binomial_intervals" in case:
            intervals = _object(case["binomial_intervals"], {"method", "confidence_level", "ctr_a", "ctr_b"}, field="binomial_intervals")
            _string(intervals["method"], "binomial_intervals.method")
            confidence = _number(intervals["confidence_level"], "binomial_intervals.confidence_level", 0, 1)
            if confidence in (0, 1):
                raise BenchmarkError("binomial confidence_level must be strictly between 0 and 1")
            for side in ("a", "b"):
                _interval(intervals["ctr_" + side], "binomial_intervals.ctr_" + side, 0, 1)
        result[case_id] = case
    if set(result) != set(indexed):
        raise BenchmarkError("Outcome case IDs must exactly match the complete input pool")
    return result


def _validate_predictions(protocol, predictions):
    _object(predictions, {"schema_version", "protocol_sha256", "records"}, field="predictions")
    _version(predictions["schema_version"], "predictions")
    if _digest(predictions["protocol_sha256"], "predictions.protocol_sha256") != canonical_sha256(protocol):
        raise BenchmarkError("Prediction protocol hash does not match frozen protocol")
    if not isinstance(predictions["records"], list):
        raise BenchmarkError("predictions.records must be a list")
    indexed = {}
    for record in predictions["records"]:
        _object(record, {"case_id", "method", "repeat", "status", "probability_a", "error", "elapsed_seconds", "usage"},
                {"run_id"}, field="prediction record")
        case_id = _string(record["case_id"], "prediction.case_id")
        method = _string(record["method"], "prediction.method")
        if case_id not in protocol["case_ids"]:
            raise BenchmarkError(f"Unknown or unscheduled prediction case_id: {case_id}")
        if method not in METHODS:
            raise BenchmarkError(f"Unknown prediction method: {method}")
        repeat = _integer(record["repeat"], "prediction.repeat", 1, protocol["repeats"])
        key = (case_id, method, repeat)
        if key in indexed:
            raise BenchmarkError(f"Duplicate scheduled prediction: {case_id}/{method}/{repeat}")
        if record["status"] == "ok":
            _number(record["probability_a"], "prediction.probability_a", 0, 1)
            if record["error"] is not None:
                raise BenchmarkError("Successful prediction.error must be null")
        elif record["status"] == "failed":
            if record["probability_a"] is not None:
                raise BenchmarkError("Failed prediction.probability_a must be null")
            _string(record["error"], "failed prediction.error")
        else:
            raise BenchmarkError("prediction.status must be ok or failed")
        if record["elapsed_seconds"] is not None:
            _number(record["elapsed_seconds"], "prediction.elapsed_seconds")
        usage = _object(record["usage"], set(), _USAGE_FIELDS, field="prediction.usage")
        for field, value in usage.items():
            if value is not None:
                if field == "cost_usd":
                    _number(value, "usage.cost_usd")
                else:
                    _integer(value, "usage." + field)
        if all(usage.get(field) is not None for field in ("input_tokens", "output_tokens", "total_tokens")):
            if usage["input_tokens"] + usage["output_tokens"] != usage["total_tokens"]:
                raise BenchmarkError("usage.total_tokens disagrees with input_tokens + output_tokens")
        if "run_id" in record:
            _string(record["run_id"], "prediction.run_id")
        indexed[key] = record
    return indexed


def _loss(probability, outcome):
    correct_probability = probability if outcome == 1 else 1-probability
    return {"brier_score": (probability-outcome)**2,
            "log_loss": -math.log(min(1-EPSILON, max(EPSILON, correct_probability))),
            "directional_accuracy": 0.5 if probability == 0.5 else float((probability > 0.5) == (outcome == 1))}


def _metrics(rows):
    losses = [_loss(probability, outcome) for probability, outcome in rows]
    return {"scored_cases": len(rows), **{field: statistics.fmean(row[field] for row in losses) if losses else None for field in METRICS}}


def _percentile(values, fraction):
    values = sorted(values)
    position = (len(values)-1) * fraction
    lo, hi = math.floor(position), math.ceil(position)
    return values[lo] + (values[hi]-values[lo]) * (position-lo)


def _comparison(rows, samples, seed):
    # Each row is one independent case with (single_model, mirofish, outcome).
    deltas = []
    for single, simulation, outcome in rows:
        single_loss, simulation_loss = _loss(single, outcome), _loss(simulation, outcome)
        deltas.append({metric: (simulation_loss[metric]-single_loss[metric] if metric == "directional_accuracy"
                               else single_loss[metric]-simulation_loss[metric]) for metric in METRICS})
    improvement = {metric: statistics.fmean(row[metric] for row in deltas) if deltas else None for metric in METRICS}
    uncertainty = {"method": "paired_case_bootstrap_percentile", "unit": "case", "level": 0.95,
                   "resamples": samples, "seed": seed, "intervals": None}
    if len(deltas) >= 2 and samples:
        rng = random.Random(seed)
        draws = {metric: [] for metric in METRICS}
        for _ in range(samples):
            selected = rng.choices(deltas, k=len(deltas))
            for metric in METRICS:
                draws[metric].append(statistics.fmean(row[metric] for row in selected))
        uncertainty["intervals"] = {metric: {"lower": _percentile(values, 0.025), "upper": _percentile(values, 0.975)}
                                    for metric, values in draws.items()}
    else:
        uncertainty["reason"] = "At least two paired cases and nonzero bootstrap resamples are required."
    return {"paired_cases": len(rows), "positive_improvement_favors": "mirofish", "improvement": improvement,
            "uncertainty": uncertainty}


def _aggregate_usage(records, scheduled):
    output = {"scheduled_records": scheduled, "observed_records": len(records),
              "successful_records": sum(record["status"] == "ok" for record in records),
              "failed_records": sum(record["status"] == "failed" for record in records),
              "missing_records": scheduled-len(records)}
    for field in sorted(_USAGE_FIELDS | {"elapsed_seconds"}):
        values = [record.get(field) if field == "elapsed_seconds" else record["usage"].get(field) for record in records]
        known = [value for value in values if value is not None]
        subtotal = math.fsum(known) if field in ("cost_usd", "elapsed_seconds") else sum(known)
        output[field] = {"total": subtotal if len(known) == scheduled else None,
                         "known_subtotal": subtotal if known else None,
                         "known_records": len(known), "unknown_records": scheduled-len(known)}
    return output


def evaluate_benchmark(inputs, outcomes, protocol, predictions, *, inputs_file_sha256=None):
    """Score all scheduled cases, retaining missing/failed runs in a sensitivity view.

    Complete-paired metrics include a case only when both methods returned every
    scheduled repeat. Failure-adjusted metrics replace each failed/missing repeat
    with a probability entirely against its observed label before case averaging.
    That is a worst-case loss bound, not an estimate of the missing prediction.
    """
    indexed_inputs = validate_inputs(inputs)
    _validate_protocol(inputs, protocol, indexed_inputs)
    indexed_outcomes = _validate_outcomes(inputs, outcomes, indexed_inputs, inputs_file_sha256)
    records = _validate_predictions(protocol, predictions)
    repeated = protocol["repeats"]
    case_results, paired_rows, adjusted_rows, failures = [], [], [], []
    complete_rows = {method: [] for method in METHODS}
    adjusted_by_method = {method: [] for method in METHODS}
    method_complete_rows = {method: [] for method in METHODS}
    ties = 0
    uncertain_labels = 0
    for case_id in protocol["case_ids"]:
        label = indexed_outcomes[case_id]
        is_tie = label["observed_winner"] == "tie"
        outcome = None if is_tie else int(label["observed_winner"] == "A")
        ties += int(is_tie)
        uncertain_labels += int(label.get("difference_interval_includes_zero") is True)
        case = {"case_id": case_id, "observed_winner": label["observed_winner"], "excluded_tie": is_tie,
                "difference_interval_includes_zero": label.get("difference_interval_includes_zero"), "methods": {}}
        for method in METHODS:
            successful = []
            failed = missing = 0
            for repeat in range(1, repeated+1):
                record = records.get((case_id, method, repeat))
                if record is None:
                    missing += 1
                    failures.append({"case_id": case_id, "method": method, "repeat": repeat, "status": "missing", "error": "No scheduled record supplied"})
                elif record["status"] == "failed":
                    failed += 1
                    failures.append({"case_id": case_id, "method": method, "repeat": repeat, "status": "failed", "error": record["error"]})
                else:
                    successful.append(record["probability_a"])
            complete = len(successful) == repeated
            mean = statistics.fmean(successful) if successful else None
            adjusted = ((math.fsum(successful)+(failed+missing)*(1-outcome))/repeated if outcome is not None else None)
            case["methods"][method] = {"complete": complete, "successful_repeats": len(successful), "failed_repeats": failed,
                                       "missing_repeats": missing, "mean_probability_a": mean if complete else None,
                                       "observed_repeats_mean_probability_a": mean,
                                       "observed_repeats_probability_stddev": statistics.pstdev(successful) if successful else None,
                                       "failure_adjusted_probability_a": adjusted}
            if not is_tie:
                adjusted_by_method[method].append((adjusted, outcome))
                if complete:
                    method_complete_rows[method].append((mean, outcome))
        if not is_tie:
            adjusted_rows.append((*[case["methods"][method]["failure_adjusted_probability_a"] for method in METHODS], outcome))
            if all(case["methods"][method]["complete"] for method in METHODS):
                paired_rows.append((*[case["methods"][method]["mean_probability_a"] for method in METHODS], outcome))
                for method in METHODS:
                    complete_rows[method].append((case["methods"][method]["mean_probability_a"], outcome))
        case_results.append(case)
    scheduled_records = len(protocol["case_ids"])*repeated*len(METHODS)
    counts = {"scheduled_cases": len(protocol["case_ids"]), "scored_cases": len(protocol["case_ids"])-ties,
              "excluded_ties": ties, "observed_orderings_with_difference_interval_including_zero": uncertain_labels,
              "scheduled_records": scheduled_records, "observed_records": len(records),
              "successful_records": sum(record["status"] == "ok" for record in records.values()),
              "failed_records": sum(record["status"] == "failed" for record in records.values()),
              "missing_records": scheduled_records-len(records), "complete_paired_cases": len(paired_rows)}
    return {
        "schema_version": 1, "dataset_id": inputs["dataset_id"], "retrospective_replay": True, "forecast_accuracy_claim": False,
        "protocol_sha256": canonical_sha256(protocol), "inputs_canonical_sha256": canonical_sha256(inputs),
        "inputs_file_sha256": inputs_file_sha256, "outcomes_canonical_sha256": canonical_sha256(outcomes),
        "predictions_canonical_sha256": canonical_sha256(predictions), "counts": counts,
        "complete_paired_metrics": {method: _metrics(complete_rows[method]) for method in METHODS},
        "method_complete_metrics": {method: _metrics(method_complete_rows[method]) for method in METHODS},
        "failure_adjusted_metrics": {method: _metrics(adjusted_by_method[method]) for method in METHODS},
        "chance": {"probability_a": 0.5, **_metrics([(0.5, row[2]) for row in adjusted_rows])},
        "paired_comparison": _comparison(paired_rows, protocol["bootstrap_samples"], protocol["bootstrap_seed"]),
        "failure_adjusted_comparison": _comparison(adjusted_rows, protocol["bootstrap_samples"], protocol["bootstrap_seed"]),
        "usage": {method: _aggregate_usage([record for record in records.values() if record["method"] == method],
                                           len(protocol["case_ids"])*repeated) for method in METHODS},
        "cases": case_results, "failures": failures,
        "method": {"aggregation": "equal_weight_cases_using_mean_probability_across_scheduled_repeats",
                   "label": "observed_historical_ctr_ordering", "probability_tie_accuracy_credit": 0.5,
                   "outcome_ties": "exclude_exact_ties_from_all_quality_metrics_but_include_run_counts_and_usage",
                   "log_loss_clip_epsilon": EPSILON,
                   "failure_adjustment": "replace_each_missing_or_failed_repeat_with_probability_entirely_against_observed_label_before_averaging",
                   "hashes": "canonical_json_except_explicit_inputs_file_sha256",
                   "elapsed_seconds": "sum_of_record_wall_times_including_failures; not_parallel_benchmark_makespan"},
        "limitations": [
            "This is public historical replay; model memorization and training-data contamination cannot be ruled out.",
            "Observed CTR ordering is a noisy sample label, not a known population preference or future product outcome.",
            "Small selected samples and case dependence can make bootstrap intervals unstable; they do not establish general usefulness or calibration.",
            "Repeated runs are aggregated within cases and never counted as independent observations.",
            "Complete-paired metrics can be biased by selective failures; always inspect failure counts and the full-cohort failure-adjusted sensitivity metrics.",
            "Failure-adjusted losses are worst-case imputations for each method, not predicted behavior or a bound on their relative advantage.",
            "Method-complete metrics may use different case sets and must not be compared directly when failures differ.",
            "Methods can use different compute budgets; usage and elapsed time must be considered alongside quality.",
            "Missing images and fixed synthetic personas restrict what this configuration can say about historical readers.",
            "Hashes establish artifact identity, not prospective preregistration, label authenticity, or absence of hidden prompt leakage.",
        ],
    }
