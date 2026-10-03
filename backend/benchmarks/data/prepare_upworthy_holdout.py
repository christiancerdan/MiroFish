"""Freeze a fresh, outcome-blind Upworthy holdout without altering the pilot.

`freeze` accesses only the original selector's explicit stimulus projection.
`outcomes` is a separate release step requiring all 80 scheduled trial records.
Do not invoke that release step until the experiment owner closes generation.
The official CSV stays outside the repository. Python standard library only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
from pathlib import Path


DATASET_ID = "upworthy_headline_holdout_20261003_v1"
SEED = "mirofish-upworthy-holdout-2026-10-03-v1"
POOL_SIZE = 20
PRIOR_POOL_SIZE = 20
EXPECTED_PRIOR_MANIFEST_SHA256 = "dd5f7d42ec69ceb586bb9e46f1b5b8806f511feb2c11e1c75e2470704fe1a249"
SOURCE_SHA256 = "8368313b060f4015a0c6fb34e6d788163cee29554144aba9390b14922eb9d8ce"
DATA_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = DATA_DIR / "holdout-2026-10-03"
PRIOR_MANIFEST = DATA_DIR / "upworthy_selection_manifest.json"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def legacy():
    """An isolated instance allows a new seed without mutating the old module."""
    return _load(DATA_DIR / "prepare_upworthy.py", "upworthy_original_selector")


def _file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json(path):
    def invalid_constant(value):
        raise ValueError(f"Nonfinite JSON constant: {value}")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_json_object, parse_constant=invalid_constant)


def read_exclusions(path=PRIOR_MANIFEST):
    if _file_hash(path) != EXPECTED_PRIOR_MANIFEST_SHA256:
        raise ValueError("Prior manifest differs from the frozen 20-case pilot/reserve pool")
    manifest = _read_json(path)
    ids = {item["source_test_id"] for item in manifest["selected_cases"]}
    if manifest["dataset_id"] != "upworthy_headline_pairs_v1" or len(ids) != PRIOR_POOL_SIZE:
        raise ValueError("All 20 distinct prior source tests must be excluded")
    return ids


def read_selection_source(path):
    """Hash the source, then retain only permitted metadata; never access labels."""
    if _file_hash(path) != SOURCE_SHA256:
        raise ValueError("CSV is not the frozen official OSF snapshot")
    selector = legacy()
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        if fields is None or not set(selector.SELECTION_FIELDS).issubset(fields):
            raise ValueError("Source lacks required stimulus metadata")
        rows = [{key: row[key] for key in selector.SELECTION_FIELDS} for row in reader]
    if len(rows) != 22666 or len({row[""] for row in rows}) != 22666:
        raise ValueError("Unexpected source row identity/count")
    if len({row["clickability_test_id"] for row in rows}) != 4873:
        raise ValueError("Unexpected source test count")
    return rows, fields


def select_holdout(rows, excluded_ids):
    """Apply the original eligibility rules with a new fixed seed and exclusions."""
    if len(excluded_ids) != PRIOR_POOL_SIZE:
        raise ValueError("Exactly 20 distinct previous source tests must be excluded")
    selector = legacy()
    projected = [{key: row[key] for key in selector.SELECTION_FIELDS} for row in rows]
    source_ids = {row["clickability_test_id"] for row in projected}
    if not excluded_ids.issubset(source_ids):
        raise ValueError("Prior source tests are missing from the source snapshot")
    remaining = [row for row in projected if row["clickability_test_id"] not in excluded_ids]
    selector.SEED = SEED
    selector.POOL_SIZE = POOL_SIZE
    selector.PILOT_SIZE = 0  # Existing strict benchmark schema uses pilot/reserve.
    cases, selected, counts = selector.select_cases(remaining)
    for index, (case, selection) in enumerate(zip(cases, selected), 1):
        case_id = f"upworthy-holdout-{index:03d}"
        case["case_id"] = selection["case_id"] = case_id
    counts.update({"source_tests_before_prior_exclusion": len(source_ids), "source_rows_before_prior_exclusion": len(rows),
                   "excluded_prior_tests": len(excluded_ids), "excluded_prior_rows": len(projected)-len(remaining)})
    return cases, selected, counts


def _save_immutable(output, artifacts):
    rendered = {name: (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n").encode("utf-8")
                for name, value in artifacts.items()}
    # Check every existing artifact before changing any, including its input mate.
    for name, content in rendered.items():
        target = output / name
        if target.exists() and target.read_bytes() != content:
            raise ValueError(f"Refusing to overwrite frozen artifact: {target.name}")
    output.mkdir(parents=True, exist_ok=True)
    for name, content in rendered.items():
        target = output / name
        if not target.exists():
            with target.open("xb") as handle:
                handle.write(content)


def freeze(source, output=DEFAULT_OUTPUT, *, prior_manifest=PRIOR_MANIFEST):
    excluded_ids = read_exclusions(prior_manifest)
    rows, fields = read_selection_source(source)
    cases, selected, counts = select_holdout(rows, excluded_ids)
    selector = legacy()
    inputs = {"schema_version": 1, "dataset_id": DATASET_ID, "cases": cases}
    input_bytes = (json.dumps(inputs, ensure_ascii=False, indent=2, allow_nan=False)+"\n").encode("utf-8")
    manifest = {
        "schema_version": 1, "dataset_id": DATASET_ID, "selection_phase": "fresh_holdout_after_pilot_reliability_work",
        "selection_seed": SEED, "pool_size": POOL_SIZE, "pilot_size": 0,
        "planned_repeats_per_method": 2, "planned_methods": ["single_model", "mirofish"], "planned_trial_count": 80,
        "source": {"url": selector.SOURCE_URL, "filename": "upworthy-archive-exploratory-packages-03.12.2020.csv",
                   "sha256": SOURCE_SHA256, "bytes": source.stat().st_size, "columns": fields,
                   "license": "CC-BY-4.0", "problem_column_present": "problem" in fields},
        "randomization_correction": {"doi": "10.1038/s41597-024-03600-w",
            "excluded_created_at_dates_inclusive": [selector.BAD_START.isoformat(), selector.BAD_END.isoformat()],
            "applied_locally": True, "whole_test_exclusion_if_any_arm_in_window": True,
            "source_timestamps_timezone": "unknown"},
        "prior_pool_exclusion": {"dataset_id": "upworthy_headline_pairs_v1",
            "manifest_file_sha256": _file_hash(prior_manifest), "excluded_source_test_ids": sorted(excluded_ids),
            "includes_unrun_reserve_cases": True},
        "selection_fields": list(selector.SELECTION_FIELDS), "matched_fields": list(selector.MATCH_FIELDS),
        "selection_uses_clicks_or_impressions": False, "selection_uses_editorial_winner_or_significance": False,
        "selection_counts": counts, "selected_cases": selected,
        "inputs_file_sha256": hashlib.sha256(input_bytes).hexdigest(), "inputs_canonical_sha256": selector.digest(inputs),
        "selection_script_sha256": _file_hash(Path(__file__)),
        "original_selector_sha256": _file_hash(DATA_DIR / "prepare_upworthy.py"),
        "outcome_release_rule": "Do not invoke outcomes until generation is declared complete; require all 80 final trial records."
    }
    _save_immutable(output, {"upworthy_inputs.json": inputs, "upworthy_selection_manifest.json": manifest})
    print(json.dumps({"frozen": len(cases), "labels_materialized": False,
                      "inputs_file_sha256": manifest["inputs_file_sha256"],
                      "inputs_canonical_sha256": manifest["inputs_canonical_sha256"], **counts}))
    return manifest


def release_outcomes(source, output, protocol_path, predictions_path, *, prior_manifest=PRIOR_MANIFEST):
    """A separate, explicit post-generation step; tests use no real holdout labels."""
    # Read/validate experiment artifacts before reading any source labels.
    inputs = _read_json(output / "upworthy_inputs.json")
    manifest = _read_json(output / "upworthy_selection_manifest.json")
    protocol, predictions = _read_json(protocol_path), _read_json(predictions_path)
    scorer = _load(DATA_DIR.parents[1] / "app/services/comparative_benchmark.py", "holdout_contract")
    indexed = scorer.validate_inputs(inputs)
    scorer._validate_protocol(inputs, protocol, indexed)
    records = scorer._validate_predictions(protocol, predictions)
    if (inputs["dataset_id"] != DATASET_ID or len(indexed) != POOL_SIZE or set(protocol["case_ids"]) != set(indexed)
            or protocol["repeats"] != 2 or len(records) != 80):
        raise ValueError("Outcome release requires all 80 final records for all 20 frozen holdout cases")
    expected_hashes = {"inputs_file_sha256": _file_hash(output / "upworthy_inputs.json"),
                       "inputs_canonical_sha256": scorer.canonical_sha256(inputs),
                       "selection_script_sha256": _file_hash(Path(__file__)),
                       "original_selector_sha256": _file_hash(DATA_DIR / "prepare_upworthy.py")}
    if any(manifest.get(key) != value for key, value in expected_hashes.items()):
        raise ValueError("Frozen input/helper identity changed before outcome release")
    if manifest["dataset_id"] != DATASET_ID or manifest["selection_seed"] != SEED:
        raise ValueError("Unexpected frozen holdout identity")
    excluded_ids = read_exclusions(prior_manifest)
    if (manifest["prior_pool_exclusion"]["manifest_file_sha256"] != _file_hash(prior_manifest)
            or manifest["prior_pool_exclusion"]["excluded_source_test_ids"] != sorted(excluded_ids)):
        raise ValueError("Prior-pool exclusion changed before outcome release")
    if (output / "upworthy_outcomes.json").exists():
        raise ValueError("Refusing to overwrite released outcomes")
    # Reuse the original label calculation and its frozen-selection verification.
    selector = legacy()
    selector.DATASET_ID = DATASET_ID
    selector.PILOT_SIZE = 0
    selector.select_cases = lambda rows: select_holdout(rows, excluded_ids)
    selector.outcomes(source, output)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("freeze", "outcomes"))
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--completed-predictions", type=Path)
    args = parser.parse_args(argv)
    if args.stage == "freeze":
        freeze(args.source, args.output)
    else:
        if args.protocol is None or args.completed_predictions is None:
            parser.error("outcomes requires --protocol and --completed-predictions after generation finishes")
        release_outcomes(args.source, args.output, args.protocol, args.completed_predictions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
