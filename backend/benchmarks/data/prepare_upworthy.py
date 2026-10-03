"""Reproduce the frozen Upworthy fixture; never reads model predictions.

First run `freeze` to select cases using only stimulus/identity/date fields.
Only then run `outcomes` to attach the historical aggregate count labels.
The official CSV stays outside the repository. Python standard library only.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date
import hashlib
import itertools
import json
import math
from pathlib import Path
from statistics import NormalDist


DATASET_ID = "upworthy_headline_pairs_v1"
SEED = "mirofish-upworthy-pilot-v1"
SOURCE_SHA256 = "8368313b060f4015a0c6fb34e6d788163cee29554144aba9390b14922eb9d8ce"
SOURCE_URL = "https://osf.io/download/3vqmp/"
POOL_SIZE = 20
PILOT_SIZE = 6
MATCH_FIELDS = ("eyecatcher_id", "excerpt", "lede", "share_text", "square")
SELECTION_FIELDS = ("", "clickability_test_id", "created_at", "headline", *MATCH_FIELDS)
BAD_START, BAD_END = date(2013, 6, 25), date(2014, 1, 10)
CONTEXT = (
    "Historical Upworthy.com readers in 2013–2015, assigned an article preview "
    "on the Upworthy website. Predict which of these two headlines received "
    "the higher click-through rate (clicks per assignment). Both variants used "
    "the same image; the image is unavailable. This is a headline comparison, "
    "not a measure of purchases, satisfaction, or broader customer behavior."
)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_source(path):
    if file_hash(path) != SOURCE_SHA256:
        raise ValueError("CSV is not the frozen official OSF snapshot")
    with path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        rows = list(reader)
        fields = reader.fieldnames
    assert len(rows) == 22666
    assert len({row[""] for row in rows}) == len(rows)
    assert len({row["clickability_test_id"] for row in rows}) == 4873
    return rows, fields


def select_cases(rows):
    # Explicit projection keeps clicks, impressions, winner, first_place,
    # significance and updated_at unavailable to eligibility/ranking logic.
    projected = [{key: row[key] for key in SELECTION_FIELDS} for row in rows]
    tests = {}
    for row in projected:
        tests.setdefault(row["clickability_test_id"], []).append(row)
    candidates = []
    exclusions = {"date_window_tests": 0, "no_matched_distinct_headline_pair_tests": 0}
    eligible_pair_count = 0
    for test_id, arms in tests.items():
        # Exclude a whole test if ANY package date enters the correction window.
        if any(BAD_START <= date.fromisoformat(row["created_at"][:10]) <= BAD_END for row in arms):
            exclusions["date_window_tests"] += 1
            continue
        pairs = []
        for left, right in itertools.combinations(arms, 2):
            if not left["eyecatcher_id"].strip():
                continue
            if not all(left[key] == right[key] for key in MATCH_FIELDS):
                continue
            if not left["headline"].strip() or not right["headline"].strip():
                continue
            if " ".join(left["headline"].casefold().split()) == " ".join(right["headline"].casefold().split()):
                continue
            low, high = sorted((left, right), key=lambda row: row[""])
            pair_key = digest([SEED, "pair", test_id, low[""], high[""]])
            pairs.append((pair_key, low, high))
        if not pairs:
            exclusions["no_matched_distinct_headline_pair_tests"] += 1
            continue
        eligible_pair_count += len(pairs)
        pair_key, left, right = min(pairs, key=lambda pair: pair[0])
        candidates.append((digest([SEED, "test", test_id]), test_id, pair_key, left, right))
    selected = sorted(candidates, key=lambda item: (item[0], item[1]))[:POOL_SIZE]
    if len(selected) != POOL_SIZE:
        raise ValueError("Insufficient eligible tests; do not alter the rule after viewing labels")
    inputs, selections = [], []
    for index, (rank_hash, test_id, pair_key, left, right) in enumerate(selected, 1):
        orientation_hash = digest([SEED, "orientation", test_id, left[""], right[""]])
        if int(orientation_hash, 16) % 2:
            left, right = right, left
        case_id = f"upworthy-{index:03d}"
        split = "pilot" if index <= PILOT_SIZE else "reserve"
        inputs.append({"case_id": case_id, "split": split,
                       "headline_a": left["headline"], "headline_b": right["headline"],
                       "audience_context": CONTEXT, "shared_image_unavailable": True})
        selections.append({"case_id": case_id, "split": split, "source_test_id": test_id,
                           "source_row_a": left[""], "source_row_b": right[""],
                           "created_at_a": left["created_at"], "created_at_b": right["created_at"],
                           "test_rank_sha256": rank_hash, "pair_rank_sha256": pair_key,
                           "orientation_sha256": orientation_hash,
                           "matched_fields": {key: {"equal": True, "value_sha256": digest(left[key]),
                                                    "empty": left[key] == ""} for key in MATCH_FIELDS}})
    return inputs, selections, {"source_tests": len(tests), "source_rows": len(rows),
                               "eligible_tests": len(candidates), "eligible_pairs": eligible_pair_count,
                               "exclusions": exclusions}


def freeze(source, output):
    rows, fields = read_source(source)
    cases, selections, counts = select_cases(rows)
    output.mkdir(parents=True, exist_ok=True)
    inputs_path = output / "upworthy_inputs.json"
    inputs = {"schema_version": 1, "dataset_id": DATASET_ID, "cases": cases}
    save(inputs_path, inputs)
    manifest = {
        "schema_version": 1, "dataset_id": DATASET_ID, "selection_seed": SEED,
        "pool_size": POOL_SIZE, "pilot_size": PILOT_SIZE,
        "source": {"url": SOURCE_URL, "filename": "upworthy-archive-exploratory-packages-03.12.2020.csv",
                   "sha256": SOURCE_SHA256, "bytes": source.stat().st_size,
                   "osf_file_id": "608ff67f5533b40296e21e5a", "osf_version": 1,
                   "osf_date_modified": "2021-05-03T13:11:27.711690", "columns": fields,
                   "license": "CC-BY-4.0", "problem_column_present": "problem" in fields},
        "randomization_correction": {
            "doi": "10.1038/s41597-024-03600-w",
            "excluded_created_at_dates_inclusive": [BAD_START.isoformat(), BAD_END.isoformat()],
            "applied_locally": True, "whole_test_exclusion_if_any_arm_in_window": True,
            "source_timestamps_timezone": "unknown"},
        "selection_fields": list(SELECTION_FIELDS), "matched_fields": list(MATCH_FIELDS),
        "selection_uses_clicks_or_impressions": False, "selection_uses_editorial_winner_or_significance": False,
        "selection_counts": counts, "selected_cases": selections,
        "inputs_file_sha256": file_hash(inputs_path), "inputs_canonical_sha256": digest(inputs),
        "selection_script_sha256": file_hash(Path(__file__)),
    }
    save(output / "upworthy_selection_manifest.json", manifest)
    print(json.dumps({"frozen": len(cases), "inputs_file_sha256": manifest["inputs_file_sha256"], **counts}))


def wilson(clicks, impressions):
    p = clicks / impressions
    z = NormalDist().inv_cdf(0.975)
    denominator = 1 + z * z / impressions
    center = (p + z * z / (2 * impressions)) / denominator
    radius = z * math.sqrt(p * (1 - p) / impressions + z * z / (4 * impressions * impressions)) / denominator
    return [max(0.0, center - radius), min(1.0, center + radius)]


def outcomes(source, output):
    manifest = json.loads((output / "upworthy_selection_manifest.json").read_text())
    if file_hash(output / "upworthy_inputs.json") != manifest["inputs_file_sha256"]:
        raise ValueError("Input fixture changed after selection freeze")
    rows, _ = read_source(source)
    _, selected, counts = select_cases(rows)
    if selected != manifest["selected_cases"] or counts != manifest["selection_counts"]:
        raise ValueError("Selection changed; do not replace cases after reading labels")
    row_map = {row[""]: row for row in rows}
    labels = []
    for item in selected:
        a, b = row_map[item["source_row_a"]], row_map[item["source_row_b"]]
        ca, cb, na, nb = (int(a["clicks"]), int(b["clicks"]), int(a["impressions"]), int(b["impressions"]))
        if not (0 <= ca <= na and 0 <= cb <= nb and na > 0 and nb > 0):
            raise ValueError(f"Invalid counts in frozen case {item['case_id']}; report instead of resampling")
        pa, pb = ca / na, cb / nb
        ia, ib = wilson(ca, na), wilson(cb, nb)
        difference = pa - pb
        interval = [difference - math.sqrt((pa - ia[0]) ** 2 + (ib[1] - pb) ** 2),
                    difference + math.sqrt((ia[1] - pa) ** 2 + (pb - ib[0]) ** 2)]
        labels.append({**{key: item[key] for key in ("case_id", "split", "source_test_id", "source_row_a", "source_row_b")},
                       "clicks_a": ca, "clicks_b": cb, "impressions_a": na, "impressions_b": nb,
                       "ctr_a": pa, "ctr_b": pb,
                       "observed_winner": "A" if ca * nb > cb * na else "B" if ca * nb < cb * na else "tie",
                       "ctr_difference_a_minus_b": difference, "newcombe95_difference": interval,
                       "binomial_intervals": {"method": "Wilson score, no continuity correction", "confidence_level": 0.95,
                                              "ctr_a": ia, "ctr_b": ib},
                       "difference_interval_includes_zero": interval[0] <= 0 <= interval[1]})
    save(output / "upworthy_outcomes.json", {
        "schema_version": 1, "dataset_id": DATASET_ID, "inputs_sha256": manifest["inputs_file_sha256"],
        "inputs_canonical_sha256": manifest["inputs_canonical_sha256"],
        "selection_manifest_sha256": file_hash(output / "upworthy_selection_manifest.json"),
        "label_semantics": "Observed historical CTR ordering, not a known population winner.",
        "uncertainty_assumption": "Independent binomial assignments; intervals omit clustering, repeated-user dependence and adaptive stopping.",
        "difference_interval_method": "Newcombe hybrid score for independent proportions, no continuity correction; per-case 95%, not simultaneous.",
        "cases": labels})
    print(json.dumps({"labelled": len(labels), "pilot": PILOT_SIZE,
                      "interval_includes_zero": sum(row["difference_interval_includes_zero"] for row in labels),
                      "pilot_interval_includes_zero": sum(row["difference_interval_includes_zero"] for row in labels[:PILOT_SIZE])}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("freeze", "outcomes"))
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    {"freeze": freeze, "outcomes": outcomes}[args.stage](args.source, args.output)
