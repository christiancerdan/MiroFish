"""The live runner must keep input isolation and prediction failures explicit."""
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_comparative_benchmark.py"
SPEC = importlib.util.spec_from_file_location("comparative_runner", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def case():
    return {"headline_a": "A new approach", "headline_b": "What changed?",
            "audience_context": "Readers of a general-interest publisher.",
            "shared_image_unavailable": True}


class FakeLLM:
    def __init__(self, text, finish_reason="stop"):
        self.text, self.finish_reason, self.calls = text, finish_reason, []

    def _create_completion(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason=self.finish_reason,
            message=SimpleNamespace(content=self.text))])


def test_source_contract_rejects_outcomes_and_identifiers():
    for field in ("winner", "clicks", "impressions", "case_id", "source_id", "outcomes"):
        with pytest.raises(ValueError):
            runner.build_source_document({**case(), field: "secret outcome"})


def test_both_methods_receive_identical_source_and_persona_assumptions():
    source = runner.build_source_document(case())
    single = FakeLLM('{"probability_a":0.6,"rationale":"A is clear."}')
    simulated = FakeLLM('{"probability_a":0.4,"rationale":"Assumed readers preferred B."}')
    runner.assess_once(single, source)
    runner.assess_once(simulated, source, report="A simulated report.")
    assert source == runner.build_source_document(case())
    assert single.calls[0]["messages"][0] == simulated.calls[0]["messages"][0]
    for provider in (single, simulated):
        assert len(provider.calls) == 1
        assert source in provider.calls[0]["messages"][1]["content"]
    assert "fictional" in source and "assumptions" in source
    assert "shared image" in source


@pytest.mark.parametrize("text", [
    '{"probability_a":NaN,"rationale":"x"}',
    '{"probability_a":Infinity,"rationale":"x"}',
    '{"probability_a":true,"rationale":"x"}',
    '{"probability_a":1.01,"rationale":"x"}',
    '{"probability_a":-0.1,"rationale":"x"}',
    '{"probability_a":"0.5","rationale":"x"}',
    '{"probability_a":0.5,"rationale":"x","winner":"A"}',
    '{"probability_a":0.5,"probability_a":0.6,"rationale":"x"}',
    '{"probability_a":0.5}',
    '```json\n{"probability_a":0.5,"rationale":"x"}\n```',
    '',
])
def test_invalid_prediction_never_repaired_or_reported_as_success(text):
    model = FakeLLM(text)
    result = runner.prediction_attempt(lambda: runner.assess_once(model, "source"))
    assert result["status"] == "failed" and result["probability_a"] is None
    assert result["error"]
    assert len(model.calls) == 1


def test_truncated_json_is_failed_even_when_valid_json_prefix():
    model = FakeLLM('{"probability_a":0.5,"rationale":"x"}', finish_reason="length")
    result = runner.prediction_attempt(lambda: runner.assess_once(model, "source"))
    assert result["status"] == "failed"


def test_valid_prediction_preserves_extreme_probability():
    model = FakeLLM('{"probability_a":0,"rationale":"B appears stronger."}')
    result = runner.prediction_attempt(lambda: runner.assess_once(model, "source"))
    assert result == {"status": "ok", "probability_a": 0.0, "error": None,
                      "rationale": "B appears stronger."}


def test_runtime_must_be_empty(tmp_path):
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "memory.sqlite3").write_text("prior run")
    with pytest.raises(ValueError, match="empty"):
        runner.make_runtime(occupied)
    new = tmp_path / "new"
    assert runner.make_runtime(new) == new.resolve()


def test_usage_maps_unknown_cost_without_inventing_zero():
    assert runner.usage_record({"calls": 2, "tokens": 9, "input_tokens": 5,
                               "output_tokens": 4, "estimated_cost_usd": None}) == {
        "calls": 2, "total_tokens": 9, "input_tokens": 5,
        "output_tokens": 4, "cost_usd": None}


def test_usage_includes_failed_ontology_project_before_response():
    budgets = [
        {"run_id": "unreturned_project", "usage": {"calls": 1, "tokens": 15,
         "input_tokens": 5, "output_tokens": 10, "estimated_cost_usd": None}},
        {"run_id": "other_project", "usage": {"calls": 2, "tokens": 12,
         "input_tokens": 7, "output_tokens": 5, "estimated_cost_usd": 0.01}},
    ]
    assert runner.total_usage(budgets) == {"calls": 3, "total_tokens": 27,
                                         "input_tokens": 12, "output_tokens": 15, "cost_usd": None}


def test_configuration_freezes_prompts_and_resource_bounds():
    config = runner.benchmark_configuration()
    assert config["max_calls"] == 35 and config["wall_timeout_seconds"] == 900
    assert config["assessor_system_sha256"] == runner.sha256_text(runner.ASSESSOR_SYSTEM)
    assert config["simulation_requirement_sha256"] == runner.sha256_text(runner.REQUIREMENT)


def test_output_writer_is_strict_and_atomic(tmp_path):
    target = tmp_path / "result.json"
    runner.save_json(target, {"probability_a": 0.7})
    assert json.loads(target.read_text()) == {"probability_a": 0.7}
    with pytest.raises(ValueError):
        runner.save_json(target, {"probability_a": float("nan")})
    assert json.loads(target.read_text()) == {"probability_a": 0.7}


def graph():
    return {'nodes': [
        {'uuid': 'curious', 'name': 'Curious Reader', 'labels': ['Entity', 'CuriousReader']},
        {'uuid': 'skeptical', 'name': 'Skeptical Reader', 'labels': ['Entity', 'SkepticalReader']},
        {'uuid': 'busy', 'name': 'Busy Reader', 'labels': ['Entity', 'BusyReader']},
    ]}


def test_valid_actual_subtypes_are_selected_without_invented_reader_label():
    assert runner.select_participant_types(graph()) == ['BusyReader', 'CuriousReader', 'SkepticalReader']


def test_shared_actual_type_is_supported():
    value = graph()
    for node in value['nodes']:
        node['labels'] = ['Entity', 'Person']
    assert runner.select_participant_types(value) == ['Person']


def test_unrelated_headline_entity_does_not_become_an_agent():
    value = graph()
    value['nodes'].append({'uuid': 'headline', 'name': 'A headline', 'labels': ['Entity', 'Headline']})
    assert runner.select_participant_types(value) == ['BusyReader', 'CuriousReader', 'SkepticalReader']


@pytest.mark.parametrize('failure', ['missing', 'duplicate', 'untyped', 'extra_selected', 'invented_person'])
def test_invalid_or_ambiguous_fixed_participants_fail_closed(failure):
    value = graph()
    if failure == 'missing':
        value['nodes'].pop()
    elif failure == 'duplicate':
        value['nodes'].append({**value['nodes'][0], 'uuid': 'duplicate'})
    elif failure == 'untyped':
        value['nodes'][0]['labels'] = ['Entity', 'Node']
    elif failure == 'extra_selected':
        value['nodes'].append({'uuid': 'fourth', 'name': 'Fourth Reader', 'labels': ['CuriousReader']})
    elif failure == 'invented_person':
        value['nodes'][0]['name'] = 'Alice Curious'
    with pytest.raises(RuntimeError):
        runner.select_participant_types(value)


def test_prepared_names_cannot_silently_replace_source_personas():
    runner.validate_participant_names([{'entity_name': persona['name']} for persona in runner.PERSONAS])
    with pytest.raises(RuntimeError):
        runner.validate_participant_names([{'entity_name': 'Alice'}, {'entity_name': 'Bob'}, {'entity_name': 'Chris'}])


def test_actual_fixture_names_match():
    # Only structural participant data copied from the failed v1 trace; no
    # headline, prediction, or outcome fixture enters this regression test.
    value = graph()
    value['nodes'][0]['uuid'] = 'c1728826-0e5b-5838-8d18-8e36ad546387'
    value['nodes'][1]['uuid'] = '654b1f22-1a1c-5dbe-8620-e72eb720b91d'
    value['nodes'][2]['uuid'] = '69d8c8f0-6401-5f96-92cc-a6d83a5ef931'
    assert len(runner.select_participant_types(value)) == 3


@pytest.mark.parametrize("method", ["single_model", "mirofish"])
def test_frozen_source_mismatch_blocks_model_and_pipeline_before_calls(tmp_path, monkeypatch, method):
    from app.utils import llm_client, llm_provider

    monkeypatch.setattr(llm_provider, "settings_from_config", lambda _: SimpleNamespace(
        provider="openai_compatible", model="test-model", base_url="http://localhost:11434/v1", token_limit=32768))
    args = SimpleNamespace(method=method, timeout=900, max_calls=35, case_id="test", repeat=1)
    trial = runner.Trial(args, runner.build_source_document(case()),
                         {"model": {"implementation_source_sha256": "0" * 64}}, tmp_path, io.StringIO())
    attempted = []

    def forbidden():
        attempted.append("paid_work")
        raise AssertionError("A source mismatch must stop before model or pipeline initialization")

    monkeypatch.setattr(llm_client, "LLMClient", forbidden)
    monkeypatch.setattr(trial, "mirofish_report", forbidden)
    monkeypatch.setattr(trial, "mark", lambda *_args, **_kwargs: None)
    with pytest.raises(ValueError, match="source.*frozen protocol"):
        trial.run()
    assert attempted == []


def test_frozen_token_limit_mismatch_stops_before_runtime_or_calls(tmp_path, monkeypatch):
    from app.utils import llm_provider
    monkeypatch.setattr(llm_provider, "settings_from_config", lambda _: SimpleNamespace(
        provider="openai_compatible", model="test", base_url="http://localhost/v1", token_limit=1024))
    args = SimpleNamespace(method="single_model", timeout=900, max_calls=35, case_id="test", repeat=1)
    trial = runner.Trial(args, runner.build_source_document(case()),
                         {"model": {"token_limit": 32768}}, tmp_path, io.StringIO())
    with pytest.raises(ValueError, match="Configured model"):
        trial.model_and_implementation()


@pytest.mark.parametrize("mismatch", [None, "version", "inventory", "content"])
def test_installed_simulation_runtime_must_match_frozen_vendor(tmp_path, monkeypatch, mismatch):
    vendor = tmp_path / "vendor" / "camel-oasis"
    (vendor / "oasis").mkdir(parents=True)
    (vendor / "pyproject.toml").write_text('[project]\nversion = "1.0+patched"\n')
    (vendor / "oasis" / "user.py").write_text('demographics = None\n')
    installed = tmp_path / "installed"
    (installed / "oasis").mkdir(parents=True)
    (installed / "oasis" / "user.py").write_text(
        'demographics = 30\n' if mismatch == "content" else 'demographics = None\n')
    distribution = SimpleNamespace(
        version="0.9" if mismatch == "version" else "1.0+patched",
        files=[] if mismatch == "inventory" else [Path("oasis/user.py")],
        locate_file=lambda path: installed / path)
    monkeypatch.setattr(runner, "BACKEND", tmp_path)
    monkeypatch.setattr(runner.importlib.metadata, "distribution", lambda _: distribution)
    if mismatch:
        with pytest.raises(ValueError, match="Installed simulation runtime"):
            runner.verify_vendored_runtime()
    else:
        assert runner.verify_vendored_runtime()["files_verified"] == 1


def test_source_freeze_covers_vendor_assets_lock_and_locales(tmp_path, monkeypatch):
    backend = tmp_path / "backend"
    names = ["app/example.py", "scripts/runner.py", "vendor/camel-oasis/oasis/user.py",
             "vendor/camel-oasis/oasis/schema.sql", "vendor/camel-oasis/pyproject.toml",
             "vendor/camel-oasis/UPSTREAM.json", "pyproject.toml", "uv.lock"]
    for name in names:
        path = backend / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original")
    locale = tmp_path / "locales" / "en.json"
    locale.parent.mkdir()
    locale.write_text("original")
    monkeypatch.setattr(runner, "BACKEND", backend)
    original = runner.implementation_source_sha256()
    for path in [backend / name for name in names] + [locale]:
        path.write_text("changed")
        assert runner.implementation_source_sha256() != original
        path.write_text("original")
    assert runner.implementation_source_sha256() == original
