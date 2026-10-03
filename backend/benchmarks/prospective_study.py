#!/usr/bin/env python3
"""Offline preparation and recording for manually conducted prospective studies.

No model calls, assignment, customer contact, or event collection happens here.
Exclusive files and hashes detect accidental drift, not dishonest operators or a
changed local clock. Preserve a read-only external copy if independent timing
evidence is needed. All measurements remain operator-attested.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import statistics

METHODS = ("single_model", "mirofish")
ATTESTATIONS = ("assignment_as_declared", "unique_visitors_disjoint_arms",
                "variants_and_event_as_declared", "exclusions_as_declared",
                "full_window_no_early_stopping", "counts_from_event_system")


class StudyError(ValueError):
    """The study contract or artifact evidence is invalid."""


def now():
    return datetime.now(timezone.utc)


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def utc(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value):
        raise StudyError(f"{field} must be an ISO 8601 UTC timestamp ending in Z")
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise StudyError(f"Invalid {field} timestamp") from exc
    return result


def obj(value, fields, field):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise StudyError(f"{field} requires exactly these fields: {', '.join(sorted(fields))}")


def text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise StudyError(f"{field} must be nonempty text")
    if re.search(r"\b(TODO|TBD|PLACEHOLDER|REPLACE_ME)\b|<[^>]+>", value, re.I):
        raise StudyError(f"{field} contains an unfilled placeholder")
    return value


def ident(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value):
        raise StudyError(f"{field} must be a safe identifier of at most 80 characters")
    return value


def integer(value, field, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise StudyError(f"{field} must be an integer in the declared range")
    return value


def number(value, field, maximum=None):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid or (maximum is not None and value > maximum):
        raise StudyError(f"{field} must be a finite number in the declared range")
    return value


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise StudyError("Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(StudyError("Nonfinite JSON")))


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_new(path, data):
    """Exclusive creation, private contents, and no replacement by this CLI."""
    data = data if isinstance(data, bytes) else encoded(data)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def read_evidence(path):
    with Path(path).open("rb") as handle:
        data = handle.read(16 * 1024 * 1024 + 1)
    if not data or len(data) > 16 * 1024 * 1024:
        raise StudyError("Evidence must contain 1 byte to 16 MiB")
    return data


def validate_manifest(study, *, for_freeze=False):
    obj(study, {"schema_version", "study_id", "example_only", "product_context", "model",
                "method_configuration", "fictional_persona_assumptions", "repeats",
                "forecast_deadline", "launch_at", "observation_ends_at", "randomization",
                "stopping_rule", "cases"}, "study")
    if type(study["schema_version"]) is not int or study["schema_version"] != 1:
        raise StudyError("Only schema_version 1 is supported")
    ident(study["study_id"], "study_id")
    if type(study["example_only"]) is not bool or (for_freeze and study["example_only"]):
        raise StudyError("An example cannot be frozen; supply a real study")
    text(study["product_context"], "product_context")
    if for_freeze and (study["study_id"].lower().startswith(("example-", "demo-", "synthetic-"))
                       or "fictional example" in study["product_context"].lower()):
        raise StudyError("Replace the fictional example with an actual study before freezing")
    obj(study["model"], {"provider", "name"}, "model")
    for key, value in study["model"].items():
        text(value, "model." + key)
    obj(study["method_configuration"], METHODS, "method_configuration")
    for method in METHODS:
        text(study["method_configuration"][method], method + " configuration")
    assumptions = study["fictional_persona_assumptions"]
    if not isinstance(assumptions, list) or not assumptions:
        raise StudyError("Declare at least one fictional persona assumption")
    for assumption in assumptions:
        text(assumption, "fictional_persona_assumption")
    integer(study["repeats"], "repeats", 1, 10)
    deadline, launch, end = [utc(study[key], key) for key in
                              ("forecast_deadline", "launch_at", "observation_ends_at")]
    if not deadline < launch < end:
        raise StudyError("forecast_deadline must precede launch_at and observation_ends_at")
    if study["randomization"] != {"unit": "unique_visitor", "allocation_a": 0.5,
                                  "allocation_b": 0.5, "assignment": "persistent"}:
        raise StudyError("Randomization must be persistent 50/50 assignment of unique visitors")
    if study["stopping_rule"] != "fixed_window_no_early_stopping":
        raise StudyError("Use the fixed_window_no_early_stopping rule")
    if not isinstance(study["cases"], list) or not study["cases"]:
        raise StudyError("cases must be a nonempty list")
    seen_cases, seen_experiments = set(), set()
    for case in study["cases"]:
        obj(case, {"case_id", "experiment_id", "audience", "channel", "eligibility",
                   "exclusions", "variant_a", "variant_b", "response",
                   "minimum_unique_visitors_per_arm", "sample_size_rationale"}, "case")
        for key, seen in (("case_id", seen_cases), ("experiment_id", seen_experiments)):
            ident(case[key], key)
            if case[key] in seen:
                raise StudyError("Duplicate " + key)
            seen.add(case[key])
        for key in ("audience", "channel", "eligibility", "sample_size_rationale"):
            text(case[key], key)
        if not isinstance(case["exclusions"], list):
            raise StudyError("exclusions must be a list; use [] for no exclusions")
        for exclusion in case["exclusions"]:
            text(exclusion, "exclusion")
        for side in ("a", "b"):
            variant = case["variant_" + side]
            obj(variant, {"id", "text"}, "variant")
            ident(variant["id"], "variant.id")
            text(variant["text"], "variant.text")
        if case["variant_a"]["id"] == case["variant_b"]["id"] or case["variant_a"]["text"] == case["variant_b"]["text"]:
            raise StudyError("Variants must have distinct IDs and distinct text")
        obj(case["response"], {"event_id", "definition"}, "response")
        ident(case["response"]["event_id"], "response.event_id")
        text(case["response"]["definition"], "response.definition")
        integer(case["minimum_unique_visitors_per_arm"], "minimum_unique_visitors_per_arm", 1)
    return study


def packets_for(study):
    """Identical future-facing material for both methods; no historical-runner adaptation."""
    return {case["case_id"]: {
        "purpose": "Prospective manual forecast; customer observations have not started.",
        "product_context": study["product_context"], "audience": case["audience"],
        "channel": case["channel"], "eligibility": case["eligibility"],
        "exclusions": case["exclusions"], "variant_a": case["variant_a"],
        "variant_b": case["variant_b"], "response": case["response"],
        "randomization": study["randomization"], "launch_at": study["launch_at"],
        "observation_ends_at": study["observation_ends_at"],
        "fictional_persona_assumptions": study["fictional_persona_assumptions"],
        "assumption_notice": "Personas are fictional assumptions, not sampled or interviewed customers. Do not invent demographic facts.",
        "question": "Before launch, estimate probability_a: the probability that A will have a higher observed fraction of eligible unique visitors taking the defined binary response than B over the complete fixed window. This is not a conversion-rate estimate. Exact observed ties are excluded from winner scoring.",
    } for case in study["cases"]}


def slots_for(study):
    return [(case["case_id"], method, repeat) for case in study["cases"]
            for method in METHODS for repeat in range(1, study["repeats"] + 1)]


def slot_name(slot):
    return "--".join(map(str, slot))


@contextmanager
def locked(root):
    with (Path(root) / ".lock").open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def freeze(manifest_path, directory):
    study = validate_manifest(read_json(manifest_path), for_freeze=True)
    created = now()
    if created >= utc(study["forecast_deadline"], "forecast_deadline"):
        raise StudyError("Cannot freeze after the forecast deadline")
    packets = packets_for(study)
    root = Path(directory)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    (root / "forecasts").mkdir(mode=0o700)
    write_new(root / "study.json", study)
    write_new(root / "packets.json", packets)
    frozen = {"schema_version": 1, "frozen_at": stamp(created),
              "study_sha256": sha(encoded(study)), "packets_sha256": sha(encoded(packets)),
              "tool_sha256": sha(Path(__file__).read_bytes()),
              "timing_authority": "local_clock_only"}
    write_new(root / "freeze.json", frozen)
    return frozen


def load_frozen(root):
    root = Path(root)
    study = validate_manifest(read_json(root / "study.json"), for_freeze=True)
    frozen = read_json(root / "freeze.json")
    obj(frozen, {"schema_version", "frozen_at", "study_sha256", "packets_sha256",
                 "tool_sha256", "timing_authority"}, "freeze")
    if type(frozen["schema_version"]) is not int or frozen["schema_version"] != 1 or frozen["timing_authority"] != "local_clock_only":
        raise StudyError("Invalid freeze metadata")
    for name in ("study", "packets"):
        if sha((root / (name + ".json")).read_bytes()) != frozen[name + "_sha256"]:
            raise StudyError("Frozen " + name + " file changed")
    if sha(Path(__file__).read_bytes()) != frozen["tool_sha256"]:
        raise StudyError("The recording tool changed; use the frozen revision")
    if read_json(root / "packets.json") != packets_for(study):
        raise StudyError("Common packets do not match the frozen study")
    if utc(frozen["frozen_at"], "frozen_at") >= utc(study["forecast_deadline"], "forecast_deadline"):
        raise StudyError("Freeze was after the deadline")
    return study, frozen


def validate_forecast(value, study, frozen, recorded_at):
    obj(value, {"case_id", "method", "repeat", "status", "probability_a", "error", "model",
                "study_sha256", "packet_sha256", "generation_started_at", "generation_completed_at",
                "generated_for_this_study_before_observations", "usage", "elapsed_seconds"}, "forecast")
    slot = (value["case_id"], value["method"], value["repeat"])
    integer(value["repeat"], "repeat", 1, study["repeats"])
    if slot not in slots_for(study):
        raise StudyError("Forecast is not a declared slot")
    if value["study_sha256"] != frozen["study_sha256"]:
        raise StudyError("Forecast study hash mismatch")
    if value["packet_sha256"] != sha(encoded(packets_for(study)[value["case_id"]])):
        raise StudyError("Forecast packet hash mismatch")
    if value["model"] != study["model"]:
        raise StudyError("Forecast model differs from the declared shared model")
    if value["generated_for_this_study_before_observations"] is not True:
        raise StudyError("Operator must attest this is a newly generated prospective forecast")
    started = utc(value["generation_started_at"], "generation_started_at")
    ended = utc(value["generation_completed_at"], "generation_completed_at")
    if not utc(frozen["frozen_at"], "frozen_at") <= started <= ended <= recorded_at:
        raise StudyError("Generation timestamps must fall between freeze and recording")
    if recorded_at >= utc(study["forecast_deadline"], "forecast_deadline"):
        raise StudyError("Forecast recording is late")
    if value["status"] == "ok":
        number(value["probability_a"], "probability_a", 1)
        if value["error"] is not None:
            raise StudyError("Successful forecast must have null error")
    elif value["status"] == "failed":
        if value["probability_a"] is not None:
            raise StudyError("Failed forecast must have null probability_a")
        text(value["error"], "error")
    else:
        raise StudyError("Every forecast must be terminal: ok or failed")
    number(value["elapsed_seconds"], "elapsed_seconds")
    usage = value["usage"]
    obj(usage, {"calls", "input_tokens", "output_tokens", "total_tokens", "cost_usd"}, "usage")
    for key in ("calls", "input_tokens", "output_tokens", "total_tokens"):
        if usage[key] is not None:
            integer(usage[key], "usage." + key)
    if all(usage[key] is not None for key in ("input_tokens", "output_tokens", "total_tokens")):
        if usage["input_tokens"] + usage["output_tokens"] != usage["total_tokens"]:
            raise StudyError("Usage token counts disagree")
    if usage["cost_usd"] is not None:
        number(usage["cost_usd"], "usage.cost_usd")
    return slot


def record(directory, forecast_path, evidence_path):
    root = Path(directory)
    with locked(root):
        if (root / "seal.json").exists():
            raise StudyError("Study forecasts are already sealed")
        study, frozen = load_frozen(root)
        value = read_json(forecast_path)
        evidence = read_evidence(evidence_path)
        slot = validate_forecast(value, study, frozen, now())
        target = root / "forecasts" / slot_name(slot)
        # A failed/interrupted import keeps its occupied slot; no automatic replay.
        target.mkdir(mode=0o700, exist_ok=False)
        write_new(target / "evidence.bin", evidence)
        # Capture after potentially slow evidence I/O; an import crossing the
        # deadline leaves an incomplete occupied slot, never an accepted record.
        recorded = now()
        validate_forecast(value, study, frozen, recorded)
        result = {"forecast": value, "recorded_at": stamp(recorded),
                  "evidence_sha256": sha(evidence)}
        write_new(target / "record.json", result)
        return result


def records_for(root, study, frozen):
    expected = {slot_name(slot) for slot in slots_for(study)}
    directory = Path(root) / "forecasts"
    if {path.name for path in directory.iterdir()} != expected:
        raise StudyError("Forecast slots are missing or extra")
    records, hashes = [], {}
    for slot in slots_for(study):
        name = slot_name(slot)
        target = directory / name
        if {path.name for path in target.iterdir()} != {"record.json", "evidence.bin"}:
            raise StudyError("Incomplete or extra forecast artifacts")
        result = read_json(target / "record.json")
        obj(result, {"forecast", "recorded_at", "evidence_sha256"}, "record")
        if validate_forecast(result["forecast"], study, frozen,
                             utc(result["recorded_at"], "recorded_at")) != slot:
            raise StudyError("Forecast stored in the wrong slot")
        evidence_hash = sha(read_evidence(target / "evidence.bin"))
        if evidence_hash != result["evidence_sha256"]:
            raise StudyError("Forecast evidence changed")
        hashes[name] = {"record.json": sha((target / "record.json").read_bytes()), "evidence.bin": evidence_hash}
        records.append(result)
    return records, hashes


def seal(directory):
    root = Path(directory)
    with locked(root):
        study, frozen = load_frozen(root)
        records, hashes = records_for(root, study, frozen)
        freeze_sha = sha((root / "freeze.json").read_bytes())
        # All record/evidence reads and hashing finish before this timestamp.
        sealed = now()
        if sealed >= utc(study["forecast_deadline"], "forecast_deadline"):
            raise StudyError("Cannot seal after the forecast deadline")
        if any(utc(record["recorded_at"], "recorded_at") > sealed for record in records):
            raise StudyError("The local clock moved backwards before sealing")
        result = {"schema_version": 1, "sealed_at": stamp(sealed),
                  "freeze_sha256": freeze_sha,
                  "forecast_files": hashes, "terminal_records": len(records)}
        write_new(root / "seal.json", result)
        return result


def validate_observations(value, study, frozen, sealed, collected_at):
    obj(value, {"schema_version", "study_sha256", "seal_sha256", "operator", "attestations", "cases"}, "observations")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise StudyError("Invalid observations version")
    if value["study_sha256"] != frozen["study_sha256"] or value["seal_sha256"] != sha(encoded(sealed)):
        raise StudyError("Observation study/seal hash mismatch")
    text(value["operator"], "operator")
    obj(value["attestations"], ATTESTATIONS, "attestations")
    if any(value["attestations"][key] is not True for key in ATTESTATIONS):
        raise StudyError("Operator must attest all collection requirements")
    if collected_at < utc(study["observation_ends_at"], "observation_ends_at"):
        raise StudyError("The observation window is incomplete")
    if not isinstance(value["cases"], list):
        raise StudyError("Observation cases must be a list")
    indexed, seen_experiments = {}, set()
    cases = {case["case_id"]: case for case in study["cases"]}
    for observed in value["cases"]:
        obj(observed, {"case_id", "experiment_id", "variant_a_id", "variant_b_id", "event_id",
                       "window_started_at", "window_ended_at", "unique_visitors_a", "unique_visitors_b",
                       "responders_a", "responders_b"}, "observation case")
        case_id = observed["case_id"]
        if case_id not in cases or case_id in indexed:
            raise StudyError("Unknown or duplicate observation case")
        case = cases[case_id]
        if observed["experiment_id"] in seen_experiments:
            raise StudyError("Duplicate observed experiment_id")
        seen_experiments.add(observed["experiment_id"])
        for key, expected in (("experiment_id", case["experiment_id"]),
                              ("variant_a_id", case["variant_a"]["id"]),
                              ("variant_b_id", case["variant_b"]["id"]),
                              ("event_id", case["response"]["event_id"]),
                              ("window_started_at", study["launch_at"]),
                              ("window_ended_at", study["observation_ends_at"])):
            if observed[key] != expected:
                raise StudyError("Observed " + key + " differs from the frozen study")
        for side in ("a", "b"):
            n = integer(observed["unique_visitors_" + side], "unique_visitors_" + side, 0)
            integer(observed["responders_" + side], "responders_" + side, 0, n)
            if n < case["minimum_unique_visitors_per_arm"]:
                raise StudyError("The declared minimum sample was not reached; preserve observations without changing the frozen study")
        indexed[case_id] = observed
    if set(indexed) != set(cases):
        raise StudyError("Missing observation cases")
    return indexed


def evaluate(directory, observations_path):
    root = Path(directory)
    with locked(root):
        study, frozen = load_frozen(root)
        sealed = read_json(root / "seal.json")
        obj(sealed, {"schema_version", "sealed_at", "freeze_sha256", "forecast_files", "terminal_records"}, "seal")
        records, hashes = records_for(root, study, frozen)
        seal_time = utc(sealed["sealed_at"], "sealed_at")
        if (type(sealed["schema_version"]) is not int or sealed["schema_version"] != 1
                or type(sealed["terminal_records"]) is not int or sealed["freeze_sha256"] != sha((root / "freeze.json").read_bytes())
                or hashes != sealed["forecast_files"] or len(records) != sealed["terminal_records"]
                or seal_time >= utc(study["forecast_deadline"], "forecast_deadline")
                or any(utc(record["recorded_at"], "recorded_at") > seal_time for record in records)):
            raise StudyError("Sealed artifacts or timing changed")
        collected = now()
        observations = read_json(observations_path)
        indexed = validate_observations(observations, study, frozen, sealed, collected)
        forecasts = [record["forecast"] for record in records]
        details, complete, adjusted = [], {method: [] for method in METHODS}, {method: [] for method in METHODS}
        for case in study["cases"]:
            observed = indexed[case["case_id"]]
            na, nb, ra, rb = [observed[key] for key in ("unique_visitors_a", "unique_visitors_b", "responders_a", "responders_b")]
            enough = min(na, nb) >= case["minimum_unique_visitors_per_arm"]
            difference = ra * nb - rb * na
            winner = ("A" if difference > 0 else "B" if difference < 0 else "tie") if min(na, nb) else None
            row = {"case_id": case["case_id"], "observed_response_rate_a": ra / na if na else None,
                   "observed_response_rate_b": rb / nb if nb else None, "observed_winner": winner,
                   "minimum_sample_met": enough, "mean_probability_a": {}, "failed_repeats": {}}
            runs = {method: [r for r in forecasts if r["case_id"] == case["case_id"] and r["method"] == method] for method in METHODS}
            for method in METHODS:
                row["failed_repeats"][method] = sum(r["status"] == "failed" for r in runs[method])
                row["mean_probability_a"][method] = statistics.mean(r["probability_a"] for r in runs[method]) if not row["failed_repeats"][method] else None
            if enough and winner != "tie":
                label = int(winner == "A")
                for method in METHODS:
                    worst = statistics.mean(r["probability_a"] if r["status"] == "ok" else 1 - label for r in runs[method])
                    adjusted[method].append((worst, label))
                    if all(row["mean_probability_a"][m] is not None for m in METHODS):
                        complete[method].append((row["mean_probability_a"][method], label))
            details.append(row)
        def metrics(pairs):
            return {"scored_cases": len(pairs),
                    "brier_score": statistics.mean((p - y) ** 2 for p, y in pairs) if pairs else None,
                    "directional_accuracy": statistics.mean(0.5 if p == 0.5 else int((p > 0.5) == bool(y)) for p, y in pairs) if pairs else None}
        def accounted(method, field):
            values = [r["usage"][field] for r in forecasts if r["method"] == method]
            known = [value for value in values if value is not None]
            return {"total": sum(known) if len(known) == len(values) else None,
                    "known_subtotal": sum(known), "unknown_records": len(values) - len(known)}
        result = {"schema_version": 1, "study_id": study["study_id"], "evaluated_at": stamp(collected),
                  "design": "prospective_manual_operator_attested", "timing_authority": "local_clock_only",
                  "general_forecast_accuracy_claim": False, "seal_sha256": sha((root / "seal.json").read_bytes()),
                  "observations_sha256": sha(encoded(observations)), "cases": details,
                  "completion": {m: {"valid": sum(r["status"] == "ok" and r["method"] == m for r in forecasts),
                                       "scheduled": sum(r["method"] == m for r in forecasts)} for m in METHODS},
                  "usage": {m: {field: accounted(m, field) for field in
                                 ("calls", "input_tokens", "output_tokens", "total_tokens", "cost_usd")} for m in METHODS},
                  "complete_paired_metrics": {m: metrics(complete[m]) for m in METHODS},
                  "failure_adjusted_metrics": {m: metrics(adjusted[m]) for m in METHODS},
                  "limitations": ["Timing uses the local clock and generation/collection are operator-attested; hashes are not external preregistration.",
                                  "Observed winners are noisy. Exact ties are excluded from winner scoring and retained above. All included arms meet the declared minimum; this does not establish statistical power.",
                                  "Repeated model runs are not independent customer experiments. One case cannot establish general predictive accuracy.",
                                  "Failure sensitivity substitutes probabilities entirely against the observed winner before repeat averaging; these are not replacement predictions.",
                                  "No power or calibrated-probability claim, significance test, or generalization interval is produced."]}
        # Persist the attested observations alongside their result; neither may overwrite a prior evaluation.
        if (root / "observations.json").exists() or (root / "evaluation.json").exists():
            raise StudyError("Evaluation artifacts already exist")
        write_new(root / "observations.json", observations)
        write_new(root / "evaluation.json", result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("manifest")
    prepare = commands.add_parser("freeze")
    prepare.add_argument("manifest")
    prepare.add_argument("directory")
    import_forecast = commands.add_parser("record")
    import_forecast.add_argument("directory")
    import_forecast.add_argument("forecast")
    import_forecast.add_argument("evidence")
    close = commands.add_parser("seal")
    close.add_argument("directory")
    score = commands.add_parser("evaluate")
    score.add_argument("directory")
    score.add_argument("observations")
    args = parser.parse_args()
    try:
        if args.command == "validate":
            study = validate_manifest(read_json(args.manifest))
            result = {"valid": True, "example_only": study["example_only"], "scheduled_forecasts": len(slots_for(study))}
        elif args.command == "freeze":
            result = freeze(args.manifest, args.directory)
        elif args.command == "record":
            result = record(args.directory, args.forecast, args.evidence)
        elif args.command == "seal":
            result = seal(args.directory)
        else:
            result = evaluate(args.directory, args.observations)
    except (StudyError, OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(2, f"Study rejected: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
