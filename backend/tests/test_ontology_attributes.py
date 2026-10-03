import pytest

from app.services.ontology_generator import OntologyGenerator
from app.services.graph_builder import GraphBuilderService
from app.utils.ontology import (
    MAX_ONTOLOGY_ATTRIBUTES,
    MAX_ONTOLOGY_SOURCE_TARGETS,
    normalize_ontology_attribute,
    normalize_ontology_attributes,
)
from zep_cloud.external_clients.ontology import (
    edge_model_to_api_schema,
    entity_model_to_api_schema,
)


def test_normalize_string_attribute():
    assert normalize_ontology_attribute("role") == {
        "name": "role",
        "type": "text",
        "description": "role",
    }


def test_preserve_valid_dictionary_attribute():
    original = {"name": "role", "type": "text", "description": "Public role"}
    assert normalize_ontology_attribute(original) == original
    assert normalize_ontology_attribute(original) is not original


def test_reject_unusable_attribute_shapes():
    for value in (None, 7, [], {}, {"name": None}, {"name": ""}, "   "):
        assert normalize_ontology_attribute(value) is None


def test_attribute_list_is_non_empty_and_capped_for_zep():
    assert normalize_ontology_attributes(None) == [{
        "name": "details",
        "type": "text",
        "description": "Additional details about this ontology type.",
    }]

    attributes = [None] + [f"field_{index}" for index in range(12)]
    normalized = normalize_ontology_attributes(attributes)

    assert len(normalized) == MAX_ONTOLOGY_ATTRIBUTES
    assert [attribute["name"] for attribute in normalized] == [
        f"field_{index}" for index in range(MAX_ONTOLOGY_ATTRIBUTES)
    ]


def _complete_generator_ontology():
    return {
        "entity_types": [{
            "name": "Speaker", "description": "A source participant.", "attributes": [], "examples": [],
        }],
        "edge_types": [{
            "name": "QUOTES", "description": "One participant quotes another.", "attributes": [],
            "source_targets": [{"source": "Speaker", "target": "Speaker"}],
        }],
        "analysis_summary": "The source describes speakers quoting one another.",
    }


def test_generator_preserves_valid_entity_and_edge_attributes():
    value = _complete_generator_ontology()
    value["entity_types"][0]["attributes"] = [
        {"name": "role", "type": "text", "description": "Public role"},
    ]
    value["edge_types"][0]["attributes"] = [
        {"name": "source_url", "type": "text", "description": "Source URL"},
    ]
    result = OntologyGenerator(llm_client=object())._validate_and_process(value)

    assert result == value


def test_generator_preserves_empty_attributes_for_graph_backend_normalization():
    result = OntologyGenerator(llm_client=object())._validate_and_process(_complete_generator_ontology())

    assert result["entity_types"][0]["attributes"] == []
    assert result["edge_types"][0]["attributes"] == []


def test_graph_builder_safety_net_accepts_strings_and_skips_invalid_values():
    captured = {}

    class GraphApi:
        def set_ontology(self, **kwargs):
            captured.update(kwargs)

    class Client:
        graph = GraphApi()

    builder = object.__new__(GraphBuilderService)
    builder.client = Client()
    builder.set_ontology("graph-id", {
        "entity_types": [{
            "name": "Speaker",
            "attributes": ["role", None, {"name": "summary"}],
        }],
        "edge_types": [],
    })

    speaker = captured["entities"]["Speaker"]
    assert set(speaker.__annotations__) == {"role", "entity_summary"}


def test_graph_builder_emits_a_pinned_zep_sdk_compatible_schema():
    captured = {}

    class GraphApi:
        def set_ontology(self, **kwargs):
            captured.update(kwargs)

    class Client:
        graph = GraphApi()

    builder = object.__new__(GraphBuilderService)
    builder.client = Client()
    builder.set_ontology("graph-id", {
        "entity_types": [{
            "name": "Speaker",
            "attributes": ["graph_id"] + [
                f"field_{index}" for index in range(10)
            ],
        }],
        "edge_types": [{
            "name": "MENTIONS",
            "attributes": [],
            "source_targets": [{"source": "Speaker", "target": "Speaker"}],
        }],
    })

    assert captured["graph_ids"] == ["graph-id"]

    speaker = captured["entities"]["Speaker"]
    entity_schema = entity_model_to_api_schema(speaker, "Speaker")
    assert len(entity_schema["properties"]) == MAX_ONTOLOGY_ATTRIBUTES
    assert entity_schema["properties"][0] == {
        "name": "entity_graph_id",
        "type": "Text",
        "description": "graph_id",
    }

    mentions, source_targets = captured["edges"]["MENTIONS"]
    edge_schema = edge_model_to_api_schema(mentions, "MENTIONS")
    assert edge_schema["properties"] == [{
        "name": "details",
        "type": "Text",
        "description": "Additional details about this ontology type.",
    }]
    assert source_targets[0].source == "Speaker"
    assert source_targets[0].target == "Speaker"


def test_graph_builder_passes_an_empty_entity_mapping_for_edge_only_ontology():
    captured = {}

    class GraphApi:
        def set_ontology(self, **kwargs):
            captured.update(kwargs)

    class Client:
        graph = GraphApi()

    builder = object.__new__(GraphBuilderService)
    builder.client = Client()
    builder.set_ontology("graph-id", {
        "entity_types": [],
        "edge_types": [{
            "name": "RELATED_TO",
            "attributes": ["reason"],
            "source_targets": [{"source": "Entity", "target": "Entity"}],
        }],
    })

    assert captured["entities"] == {}


def test_graph_builder_deduplicates_and_caps_edge_source_targets_for_zep():
    captured = {}

    class GraphApi:
        def set_ontology(self, **kwargs):
            captured.update(kwargs)

    class Client:
        graph = GraphApi()

    source_targets = [
        {"source": f"Source{index}", "target": f"Target{index}"}
        for index in range(MAX_ONTOLOGY_SOURCE_TARGETS + 2)
    ]
    source_targets.insert(1, dict(source_targets[0]))

    builder = object.__new__(GraphBuilderService)
    builder.client = Client()
    builder.set_ontology("graph-id", {
        "entity_types": [],
        "edge_types": [{
            "name": "RELATED_TO",
            "attributes": ["reason"],
            "source_targets": source_targets,
        }],
    })

    _, normalized_targets = captured["edges"]["RELATED_TO"]
    assert len(normalized_targets) == MAX_ONTOLOGY_SOURCE_TARGETS
    assert [(item.source, item.target) for item in normalized_targets] == [
        (f"Source{index}", f"Target{index}")
        for index in range(MAX_ONTOLOGY_SOURCE_TARGETS)
    ]


def test_generator_rejects_invalid_entries_instead_of_silently_dropping_them():
    value = _complete_generator_ontology()
    value["entity_types"].append(None)
    with pytest.raises(ValueError, match="entity_types"):
        OntologyGenerator(llm_client=object())._validate_and_process(value)


def test_generator_rejects_unknown_endpoints_instead_of_silently_discarding_them():
    value = _complete_generator_ontology()
    value["edge_types"][0]["source_targets"].insert(0, {"source": "Removed", "target": "Speaker"})
    with pytest.raises(ValueError, match="declared types"):
        OntologyGenerator(llm_client=object())._validate_and_process(value)
