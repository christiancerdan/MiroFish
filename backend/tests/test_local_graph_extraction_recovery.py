"""Model feedback regenerates complete validated graph payloads before writes."""
import copy
import hashlib
import json
import socket
from types import SimpleNamespace

import pytest

from app.services.local_graph import LocalGraphClient
from app.utils.budget import BudgetExceeded
from app.utils.llm_client import LLMClient


ONTOLOGY = {
    "entity_types": [{"name": "Reader", "description": "A named fictional reader", "attributes": []},
                     {"name": "Topic", "description": "A discussion topic", "attributes": []}],
    "edge_types": [{"name": "DISCUSSES_WITH", "description": "Two readers discuss a topic", "attributes": [],
                    "source_targets": [{"source": "Reader", "target": "Reader"}]}],
}
DOCUMENT = "Curious Reader discusses the headlines with Skeptical Reader."
VALID = {
    "entities": [
        {"name": "Curious Reader", "entity_type": "Reader", "summary": "Curious Reader discusses the headlines.", "attributes": {}},
        {"name": "Skeptical Reader", "entity_type": "Reader", "summary": "Skeptical Reader discusses the headlines.", "attributes": {}},
    ],
    "edges": [{"source": "Curious Reader", "target": "Skeptical Reader", "edge_type": "DISCUSSES_WITH",
               "fact": DOCUMENT, "attributes": {}}],
}


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Extraction recovery tests must not use network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def graph_with_responses(tmp_path, responses):
    calls = []
    llm = LLMClient.__new__(LLMClient)

    def complete(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        if len(calls) > len(responses):
            raise AssertionError("More model calls than the bounded recovery contract")
        response = responses[len(calls) - 1]
        if isinstance(response, BaseException):
            raise response
        content = response if isinstance(response, str) else json.dumps(response)
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=content))])

    llm._create_completion = complete
    client = LocalGraphClient(tmp_path / "memory.sqlite3", llm_client=llm)
    client.graph.create(graph_id="source", name="Source")
    client.graph.set_ontology_definition("source", ONTOLOGY)
    return client, calls


def invalid_payload(problem):
    value = copy.deepcopy(VALID)
    if problem == "generic_type":
        value["entities"][0]["entity_type"] = "Entity"
    elif problem == "empty_summary":
        value["entities"][0]["summary"] = ""
    elif problem == "empty_fact":
        value["edges"][0]["fact"] = "   "
    elif problem == "wrong_endpoint_type":
        value["entities"][1]["entity_type"] = "Topic"
    elif problem == "missing_endpoint":
        value["edges"][0]["target"] = "Unknown Reader"
    elif problem == "malformed_json":
        return '{"entities": [{"name": "Curious Reader"},'
    elif problem == "invalid_evidence":
        value["entities"][0]["evidence"] = {"quote": DOCUMENT}
    elif problem == "duplicate_entity":
        value["entities"].append({**value["entities"][0], "summary": "A contradictory invented summary."})
    return value


@pytest.mark.parametrize("problem", ["generic_type", "empty_summary", "empty_fact", "wrong_endpoint_type",
                                     "missing_endpoint", "malformed_json", "invalid_evidence", "duplicate_entity"])
def test_invalid_payload_regenerates_once_with_feedback_before_committing(tmp_path, problem):
    client, calls = graph_with_responses(tmp_path, [invalid_payload(problem), VALID])
    episode = client.graph.add(graph_id="source", data=DOCUMENT)
    assert len(calls) == 2
    assert calls[1]["max_tokens"] == calls[0]["max_tokens"] == 8192
    assert calls[1]["messages"] != calls[0]["messages"]
    assert calls[0]["messages"][0] == calls[1]["messages"][0]
    evidence = client.graph.get_evidence("source")
    assert len(evidence) == 1 and evidence[0]["source_id"] == episode.uuid_
    assert evidence[0]["text"] == DOCUMENT
    assert evidence[0]["source_hash"] == hashlib.sha256(DOCUMENT.encode()).hexdigest()
    nodes = client.graph.node.with_raw_response.get_by_graph_id(graph_id="source", limit=100).data
    assert {node.summary for node in nodes} == {entity["summary"] for entity in VALID["entities"]}
    assert client.graph.edge.with_raw_response.get_by_graph_id(graph_id="source", limit=100).data[0].fact == DOCUMENT


@pytest.mark.parametrize("problem", ["generic_type", "empty_summary", "empty_fact", "malformed_json"])
def test_exhausted_regeneration_preserves_no_partial_episode_or_graph_rows(tmp_path, problem):
    client, calls = graph_with_responses(tmp_path, [invalid_payload(problem)] * 2)
    with pytest.raises(ValueError):
        client.graph.add(graph_id="source", data=DOCUMENT)
    assert len(calls) == 2
    with client._connect() as db:
        for table in ("entities", "edges", "documents", "episodes", "entity_sources", "edge_sources"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_invalid_later_chunk_does_not_commit_earlier_valid_extraction(tmp_path):
    client, calls = graph_with_responses(tmp_path, [VALID, invalid_payload("empty_fact"), invalid_payload("generic_type")])
    batch = client.batch.create(metadata={"graph_id": "source", "mirofish_operation_id": "build", "chunk_count": 2})
    client.batch.add(batch_id=batch.batch_id, items=[{
        "type": "graph_episode", "data_type": "text", "graph_id": "source", "data": text,
        "metadata": {"chunk_index": index, "mirofish_operation_id": "build", "chunk_sha256": hashlib.sha256(text.encode()).hexdigest()},
    } for index, text in enumerate((DOCUMENT, DOCUMENT + " They continue discussing."))])
    with pytest.raises(ValueError):
        client.batch.process(batch_id=batch.batch_id)
    assert len(calls) == 3
    assert client.batch.get(batch_id=batch.batch_id).status == "failed"
    assert client.graph.get_evidence("source") == []
    assert client.graph.node.with_raw_response.get_by_graph_id(graph_id="source", limit=100).data == []


def test_exhausted_memory_extraction_preserves_existing_values_and_source_links(tmp_path):
    client, calls = graph_with_responses(tmp_path, [VALID, invalid_payload("empty_summary"), invalid_payload("empty_fact")])
    client.graph.add(graph_id="source", data=DOCUMENT)
    with client._connect() as db:
        before = list(db.iterdump())
    with pytest.raises(ValueError):
        client.graph.add(graph_id="source", data="Curious Reader posts a new simulated opinion.", metadata={"kind": "simulation"})
    assert len(calls) == 3
    with client._connect() as db:
        assert list(db.iterdump()) == before


def test_declared_ontology_without_edge_types_never_uses_generic_relation_fallback(tmp_path):
    invalid = copy.deepcopy(VALID)
    invalid["edges"][0]["edge_type"] = "RELATED_TO"
    repaired = {"entities": VALID["entities"], "edges": []}
    client, calls = graph_with_responses(tmp_path, [invalid, repaired])
    client.graph.set_ontology_definition("source", {**ONTOLOGY, "edge_types": []})
    client.graph.add(graph_id="source", data=DOCUMENT)
    assert len(calls) == 2
    prompt = json.loads(calls[0]["messages"][1]["content"])
    assert prompt["ontology"]["edge_types"] == []
    assert "edges must be empty" in json.dumps(calls[1]["messages"])
    assert client.graph.edge.with_raw_response.get_by_graph_id(graph_id="source", limit=100).data == []
    assert client.graph.get_evidence("source")[0]["text"] == DOCUMENT


def test_budget_exhaustion_during_feedback_is_terminal(tmp_path):
    client, calls = graph_with_responses(tmp_path, [invalid_payload("empty_summary"), BudgetExceeded(), VALID])
    with pytest.raises(BudgetExceeded):
        client.graph.add(graph_id="source", data=DOCUMENT)
    assert len(calls) == 2
    assert client.graph.get_evidence("source") == []


def test_existing_entity_type_hints_are_available_without_reusing_summaries(tmp_path):
    client, calls = graph_with_responses(tmp_path, [VALID, VALID])
    client.graph.add(graph_id="source", data=DOCUMENT)
    client.graph.add(graph_id="source", data=DOCUMENT + " The discussion is simulated.", metadata={"kind": "simulation"})
    prompt = json.loads(calls[1]["messages"][1]["content"])
    assert {tuple(sorted(item)) for item in prompt["existing_entities"]} == {("entity_type", "name")}
    assert {item["entity_type"] for item in prompt["existing_entities"]} == {"Reader"}
    assert "summary" not in prompt["ontology"]["entity_types"][0]
    assert "attributes" not in prompt["ontology"]["entity_types"][0]
    assert "summary" in calls[1]["messages"][0]["content"]
