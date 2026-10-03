from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from app.services.ontology_generator import OntologyGenerator
from app.utils.llm_client import LLMClient, LLMResponseError


def _ontology():
    return {
        "entity_types": [{
            "name": "Person",
            "description": "A fictional reader explicitly assumed in the source.",
            "attributes": [],
            "examples": [],
        }],
        "edge_types": [],
        "analysis_summary": "The three assumed readers share one actor type; no relationships are stated.",
    }


def _edge():
    return {
        "name": "KNOWS",
        "description": "An explicit acquaintance in the source.",
        "attributes": [],
        "source_targets": [{"source": "Person", "target": "Person"}],
    }


class RecordingLLMClient:
    def __init__(self):
        self.calls = []

    def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs["validator"](_ontology())


def _generator_for_test() -> OntologyGenerator:
    generator = OntologyGenerator(llm_client=object())
    generator.MAX_TEXT_LENGTH_FOR_LLM = 2000
    generator.LONG_TEXT_CHUNK_SIZE = 500
    generator.LONG_TEXT_CHUNK_OVERLAP = 0
    generator.MAX_LONG_TEXT_CHUNKS = 3
    generator.MIN_LONG_TEXT_EXCERPT = 120
    return generator


def test_short_ontology_context_keeps_original_text():
    generator = _generator_for_test()

    context = generator._build_document_context(["short document body"])

    assert context == "short document body"
    assert "长文本自动分块摘要" not in context


def test_long_ontology_context_samples_across_document():
    generator = _generator_for_test()
    long_text = "BEGIN" + ("a" * 1050) + "MIDDLE" + ("b" * 1050) + "END"

    context = generator._build_document_context([long_text])

    assert len(context) <= generator.MAX_TEXT_LENGTH_FOR_LLM
    assert "长文本自动分块摘要" in context
    assert "BEGIN" in context
    assert "MIDDLE" in context
    assert "END" in context
    assert "分块 1/" in context
    assert "分块 3/" in context
    assert "分块 5/" in context


def test_very_long_ontology_context_selects_representative_chunks():
    generator = _generator_for_test()
    chunks = ["BEGIN"] + [
        f"CHUNK{i:02d}-" + (str(i) * 490)
        for i in range(12)
    ] + ["FINALEND"]
    long_text = "".join(chunks)

    context = generator._build_document_context([long_text])

    assert len(context) <= generator.MAX_TEXT_LENGTH_FOR_LLM
    assert "BEGIN" in context
    assert "FINALEND" in context
    assert context.count("--- 文档 1 / 分块") == generator.MAX_LONG_TEXT_CHUNKS


def test_small_source_uses_one_actor_type_without_invented_fallbacks_or_edges():
    llm = RecordingLLMClient()
    generator = OntologyGenerator(llm_client=llm)
    source = "Fictional reader assumptions: Ana, Bo, and Cy. No relationships are stated."

    result = generator.generate([source], "Compare message responses.")

    assert result == _ontology()
    assert [entity["name"] for entity in result["entity_types"]] == ["Person"]
    assert result["edge_types"] == []
    assert llm.calls[0]["max_tokens"] == 8192
    assert llm.calls[0]["retry_max_tokens"] == 16384
    assert llm.calls[0]["max_attempts"] == 2
    messages = llm.calls[0]["messages"]
    assert source in messages[1]["content"]
    assert "smallest useful" in messages[0]["content"]
    assert "assumed/fictional status" in messages[0]["content"]
    assert "正好" not in messages[0]["content"] + messages[1]["content"]


def test_generator_respects_smaller_configured_model_output_limit():
    llm = RecordingLLMClient()
    llm.token_limit = 6000
    OntologyGenerator(llm_client=llm).generate(["One person."], "Simulate discussion.")
    assert llm.calls[0]["max_tokens"] == 6000
    assert llm.calls[0]["retry_max_tokens"] == 6000


def test_valid_nonfallback_taxonomy_and_prose_are_preserved_without_mutation():
    value = _ontology()
    value["entity_types"][0].update(name="Student", description="假设的学生参与者。", examples=["Ana"])
    value["edge_types"] = [_edge()]
    value["edge_types"][0]["source_targets"] = [{"source": "Student", "target": "Student"}]
    original = deepcopy(value)

    result = _generator_for_test()._validate_and_process(value)

    assert result == original
    assert value == original
    assert result is not value
    assert len(result["entity_types"]) == 1


def test_full_ten_type_taxonomy_is_valid_without_removing_types_for_fallbacks():
    value = _ontology()
    value["entity_types"] = [dict(value["entity_types"][0], name=f"Actor{index}") for index in range(10)]
    result = _generator_for_test()._validate_and_process(value)
    assert result == value


def test_legacy_entity_wildcard_remains_a_valid_endpoint():
    value = _ontology()
    value["edge_types"] = [_edge()]
    value["edge_types"][0]["source_targets"] = [{"source": "Entity", "target": "Person"}]
    assert _generator_for_test()._validate_and_process(value) == value


@pytest.mark.parametrize("value", [None, [], {}, {"entity_types": [], "edge_types": [], "analysis_summary": "ok"}])
def test_incomplete_response_is_rejected_instead_of_inventing_fallbacks(value):
    with pytest.raises(ValueError):
        _generator_for_test()._validate_and_process(value)


@pytest.mark.parametrize("field,bad_value", [
    ("name", "person"),
    ("name", "123Person"),
    ("name", "Entity"),
    ("name", "Node"),
    ("description", ""),
    ("description", "x" * 101),
    ("attributes", None),
    ("attributes", ["role"]),
    ("attributes", [{"name": "summary", "type": "text", "description": "Reserved."}]),
    ("attributes", [{"name": "role", "type": "number", "description": "Wrong type."}]),
    ("attributes", [{"name": "role", "type": "text", "description": "Role."}] * 2),
    ("examples", "Ana"),
    ("examples", [None]),
])
def test_malformed_entity_fields_are_rejected(field, bad_value):
    value = _ontology()
    value["entity_types"][0][field] = bad_value
    with pytest.raises(ValueError, match="entity_types"):
        _generator_for_test()._validate_and_process(value)


@pytest.mark.parametrize("field,bad_value", [
    ("name", "knows"),
    ("source_targets", []),
    ("source_targets", [None]),
    ("source_targets", [{"source": "Person", "target": "Missing"}]),
    ("source_targets", [{"source": "Person", "target": "Person"}] * 2),
    ("source_targets", [{"source": "Person"}]),
    ("attributes", {}),
])
def test_invalid_edges_are_rejected_instead_of_dropped_or_made_unrestricted(field, bad_value):
    value = _ontology()
    edge = _edge()
    edge[field] = bad_value
    value["edge_types"] = [edge]
    with pytest.raises(ValueError, match="edge_types"):
        _generator_for_test()._validate_and_process(value)


@pytest.mark.parametrize("field", ["entity_types", "edge_types"])
def test_duplicate_and_excess_types_are_rejected(field):
    value = _ontology()
    item = value["entity_types"][0] if field == "entity_types" else _edge()
    for count in (2, 11):
        value[field] = [deepcopy(item) for _ in range(count)]
        with pytest.raises(ValueError, match=field):
            _generator_for_test()._validate_and_process(value)


def test_unrecognized_fields_are_rejected_without_echoing_model_content():
    value = _ontology()
    value["SENSITIVE_SOURCE_SENTINEL"] = "SENSITIVE_SOURCE_SENTINEL"
    with pytest.raises(ValueError) as error:
        _generator_for_test()._validate_and_process(value)
    assert "SENSITIVE_SOURCE_SENTINEL" not in str(error.value)


class CompletionSequence:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _response(content, finish_reason="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(
        finish_reason=finish_reason,
        message=SimpleNamespace(content=content),
    )])


def _generator_with_responses(*responses):
    sequence = CompletionSequence(*responses)
    client = object.__new__(LLMClient)
    client.model = "offline-test-model"
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=sequence))
    return OntologyGenerator(llm_client=client), sequence


@pytest.mark.parametrize("first_response", [
    _response('{"entity_types":[', "length"),
    _response('{"entity_types": [}'),
    _response(json.dumps(dict(_ontology(), entity_types=[]))),
])
def test_truncation_json_and_schema_errors_regenerate_with_shared_attempt_budget(first_response):
    generator, sequence = _generator_with_responses(first_response, _response(json.dumps(_ontology())))

    assert generator.generate(["Three explicitly fictional readers."], "Compare messages.") == _ontology()
    assert len(sequence.calls) == 2
    assert [call["max_tokens"] for call in sequence.calls] == [8192, 16384]
    assert len(sequence.calls[0]["messages"]) == 2
    feedback = sequence.calls[1]["messages"][-1]["content"]
    assert "validation_error" in feedback
    assert "entity_types" in feedback
    assert "smallest source-grounded taxonomy" in feedback


def test_mixed_parse_and_schema_errors_fail_after_two_calls_without_synthetic_result():
    invalid = dict(_ontology(), entity_types=[])
    generator, sequence = _generator_with_responses(
        _response('{"entity_types":[', "length"),
        _response(json.dumps(invalid)),
        _response(json.dumps(_ontology())),
    )

    with pytest.raises(LLMResponseError, match="schema validation"):
        generator.generate(["Three explicitly fictional readers."], "Compare messages.")
    assert len(sequence.calls) == 2
    assert len(sequence.responses) == 1


def test_schema_feedback_identifies_invalid_endpoint_without_source_echo():
    invalid = _ontology()
    invalid["edge_types"] = [_edge()]
    invalid["edge_types"][0]["source_targets"][0]["target"] = "PRIVATE_SENTINEL"
    generator, sequence = _generator_with_responses(
        _response(json.dumps(invalid)), _response(json.dumps(_ontology())),
    )

    assert generator.generate(["One person."], "Simulate discussion.") == _ontology()
    feedback = sequence.calls[1]["messages"][-1]["content"]
    assert "source_targets[0]" in feedback
    assert "PRIVATE_SENTINEL" not in feedback
