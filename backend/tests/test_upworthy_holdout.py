"""Selection tests use invented metadata only; no new historical labels are read."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


HELPER_PATH = Path(__file__).resolve().parents[1] / "benchmarks/data/prepare_upworthy_holdout.py"
spec = importlib.util.spec_from_file_location("prepare_upworthy_holdout", HELPER_PATH)
holdout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(holdout)


@pytest.fixture
def source_rows():
    rows = []
    for index in range(55):
        for arm in range(2):
            rows.append({"": str(index*2+arm), "clickability_test_id": f"test-{index:03}",
                         "created_at": "2015-01-01 12:00:00", "headline": f"Headline {index} arm {arm}",
                         "eyecatcher_id": f"image-{index}", "excerpt": "same excerpt", "lede": "",
                         "share_text": "", "square": "false"})
    return rows


@pytest.fixture
def excluded_ids():
    return {f"test-{index:03}" for index in range(20)}


def test_selection_is_reproducible_disjoint_and_fully_scheduled(source_rows, excluded_ids):
    cases, selected, counts = holdout.select_holdout(source_rows, excluded_ids)
    assert len(cases) == len(selected) == 20
    assert len({item["source_test_id"] for item in selected}) == 20
    assert not ({item["source_test_id"] for item in selected} & excluded_ids)
    assert all(case["split"] == "reserve" for case in cases)
    assert cases[0]["case_id"] == "upworthy-holdout-001"
    assert counts["excluded_prior_tests"] == 20
    assert counts["eligible_tests"] == 35
    assert (cases, selected, counts) == holdout.select_holdout(list(reversed(source_rows)), excluded_ids)


def test_selection_cannot_access_outcome_fields(source_rows, excluded_ids):
    class StimulusOnly(dict):
        def __getitem__(self, key):
            assert key in holdout.legacy().SELECTION_FIELDS, f"Outcome access attempted: {key}"
            return super().__getitem__(key)
    safe = [StimulusOnly(row, clicks=object(), impressions=object(), winner=object(), significance=object())
            for row in source_rows]
    assert holdout.select_holdout(safe, excluded_ids) == holdout.select_holdout(source_rows, excluded_ids)


def test_whole_test_date_and_exact_stimulus_matching_are_preserved(source_rows, excluded_ids):
    source_rows[40]["created_at"] = "2013-06-25 00:00:00"  # Only one arm in correction window.
    source_rows[42]["excerpt"] = "Different treatment"
    source_rows[44]["headline"] = source_rows[45]["headline"].upper()
    _, selected, counts = holdout.select_holdout(source_rows, excluded_ids)
    assert counts["exclusions"]["date_window_tests"] == 1
    assert counts["exclusions"]["no_matched_distinct_headline_pair_tests"] == 2
    assert not ({"test-020", "test-021", "test-022"} & {row["source_test_id"] for row in selected})


def test_insufficient_cases_fail_without_relaxing_rules(source_rows, excluded_ids):
    with pytest.raises(ValueError, match="Insufficient"):
        holdout.select_holdout(source_rows[:60], excluded_ids)


def test_old_selector_module_and_files_are_not_mutated(source_rows, excluded_ids):
    previous = holdout.legacy()
    old_seed = previous.SEED
    path = HELPER_PATH.with_name("prepare_upworthy.py")
    before = path.read_bytes()
    holdout.select_holdout(source_rows, excluded_ids)
    assert previous.SEED == old_seed == "mirofish-upworthy-pilot-v1"
    assert path.read_bytes() == before


def test_csv_hash_must_match_before_reading_fields(tmp_path):
    source = tmp_path / "wrong.csv"
    source.write_text("clicks,impressions\n999,9999\n")
    with pytest.raises(ValueError, match="frozen official"):
        holdout.read_selection_source(source)


def test_file_freeze_is_immutable_and_does_not_materialize_outcomes(source_rows, excluded_ids, tmp_path, monkeypatch):
    source = tmp_path / "source.csv"
    source.write_text("placeholder")
    monkeypatch.setattr(holdout, "read_selection_source", lambda path: (source_rows, list(source_rows[0])))
    monkeypatch.setattr(holdout, "read_exclusions", lambda path: excluded_ids)
    old_manifest = tmp_path / "prior.json"
    old_manifest.write_text("{}")
    output = tmp_path / "holdout"
    holdout.freeze(source, output, prior_manifest=old_manifest)
    originals = {path.name: path.read_bytes() for path in output.iterdir()}
    assert set(originals) == {"upworthy_inputs.json", "upworthy_selection_manifest.json"}
    holdout.freeze(source, output, prior_manifest=old_manifest)
    assert originals == {path.name: path.read_bytes() for path in output.iterdir()}
    source_rows[50]["headline"] += " edited"
    with pytest.raises(ValueError, match="overwrite"):
        holdout.freeze(source, output, prior_manifest=old_manifest)


def test_outcome_release_refuses_incomplete_generation_before_csv_read(tmp_path, monkeypatch):
    reached_source = False
    def read_source(path):
        nonlocal reached_source
        reached_source = True
        raise AssertionError("Must not reach source labels")
    monkeypatch.setattr(holdout, "read_selection_source", read_source)
    with pytest.raises((ValueError, FileNotFoundError)):
        holdout.release_outcomes(tmp_path / "source.csv", tmp_path / "holdout",
                                 tmp_path / "protocol.json", tmp_path / "predictions.json")
    assert not reached_source


def test_release_requires_all_80_final_records_and_same_frozen_configuration(source_rows, excluded_ids, tmp_path, monkeypatch):
    source = tmp_path / "source.csv"
    source.write_text("synthetic metadata only")
    monkeypatch.setattr(holdout, "read_selection_source", lambda path: (source_rows, list(source_rows[0])))
    monkeypatch.setattr(holdout, "read_exclusions", lambda path: excluded_ids)
    prior = tmp_path / "prior.json"
    prior.write_text("{}")
    output = tmp_path / "holdout"
    holdout.freeze(source, output, prior_manifest=prior)
    inputs = json.loads((output / "upworthy_inputs.json").read_text())
    scorer = holdout._load(HELPER_PATH.parents[2] / "app/services/comparative_benchmark.py", "test_contract")
    protocol = scorer.build_protocol(inputs, case_ids=[case["case_id"] for case in inputs["cases"]], repeats=2)
    records = [{"case_id": case["case_id"], "method": method, "repeat": repeat, "status": "failed",
                "probability_a": None, "error": "Synthetic test failure", "elapsed_seconds": None, "usage": {}}
               for case in inputs["cases"] for method in ("single_model", "mirofish") for repeat in (1, 2)]
    predictions = {"schema_version": 1, "protocol_sha256": scorer.canonical_sha256(protocol), "records": records[:-1]}
    protocol_path, prediction_path = tmp_path / "protocol.json", tmp_path / "predictions.json"
    protocol_path.write_text(json.dumps(protocol))
    prediction_path.write_text(json.dumps(predictions))
    release_calls = []
    old_legacy = holdout.legacy
    def no_label_legacy():
        selector = old_legacy()
        selector.outcomes = lambda source_path, output_path: release_calls.append((source_path, output_path))
        return selector
    monkeypatch.setattr(holdout, "legacy", no_label_legacy)
    with pytest.raises(ValueError, match="80 final records"):
        holdout.release_outcomes(source, output, protocol_path, prediction_path, prior_manifest=prior)
    assert release_calls == []
    predictions["records"] = records
    prediction_path.write_text(json.dumps(predictions))
    holdout.release_outcomes(source, output, protocol_path, prediction_path, prior_manifest=prior)
    assert release_calls == [(source, output)]
    assert not (output / "upworthy_outcomes.json").exists()  # No real labels used in this test.


def test_incorrect_prior_manifest_is_rejected(tmp_path):
    manifest = tmp_path / "prior.json"
    manifest.write_text('{"dataset_id":"different"}')
    with pytest.raises(ValueError, match="frozen 20-case"):
        holdout.read_exclusions(manifest)


def test_cli_help_runs_without_flask():
    result = subprocess.run([sys.executable, "-S", str(HELPER_PATH), "--help"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "freeze" in result.stdout
    assert "outcomes" in result.stdout
