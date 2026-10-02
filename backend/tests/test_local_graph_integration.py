"""Application services work together without Zep or cloud networking."""
import copy
import socket

import pytest

from app.config import Config
from app.services import local_graph
from app.services.graph_builder import GraphBuilderService
from app.services.zep_entity_reader import ZepEntityReader
from app.services.zep_tools import ZepToolsService
from app.services.zep_graph_memory_updater import AgentActivity, ZepGraphMemoryUpdater
from app.utils.zep import clear_zep_client_cache


ONTOLOGY = {
    "entity_types": [{"name": "Person"}, {"name": "Company"}],
    "edge_types": [{"name": "WORKS_AT", "source_targets": [{"source": "Person", "target": "Company"}]}],
}
EXTRACTION = {
    "entities": [
        {"name": "Alice", "entity_type": "Person", "summary": "Alice works at Acme.", "attributes": {}},
        {"name": "Acme", "entity_type": "Company", "summary": "Acme employs Alice.", "attributes": {}},
    ],
    "edges": [{"source": "Alice", "target": "Acme", "edge_type": "WORKS_AT", "fact": "Alice works at Acme.", "attributes": {}}],
}


@pytest.fixture
def offline_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "GRAPH_BACKEND", "local", raising=False)
    monkeypatch.setattr(Config, "LOCAL_GRAPH_DB_PATH", str(tmp_path / "memory.sqlite3"), raising=False)
    monkeypatch.setattr(Config, "ZEP_API_KEY", "")
    monkeypatch.setattr(socket.socket, "connect", lambda *_args, **_kwargs: pytest.fail("Unexpected network access"))
    class LLM:
        def chat_json(self, **_kwargs):
            return copy.deepcopy(EXTRACTION)
    monkeypatch.setattr(local_graph, "LLMClient", LLM)
    clear_zep_client_cache()
    yield
    clear_zep_client_cache()


def test_complete_document_profile_search_memory_and_delete_path(offline_backend):
    builder = GraphBuilderService()
    graph_id = builder.create_graph("Local run")
    builder.set_ontology(graph_id, ONTOLOGY)
    submitted = builder.add_text_batches(graph_id, ["Alice works at Acme."], batch_size=1)
    source_ids = builder._wait_for_batch(submitted, timeout=1)
    assert len(source_ids) == 1
    assert builder.get_graph_data(graph_id)["node_count"] == 2
    # Same operation may be resumed/replayed without duplicating observations.
    replay = builder.add_text_batches(graph_id, ["Alice works at Acme."], batch_size=1)
    assert replay.batch_id == submitted.batch_id
    assert builder._wait_for_batch(replay, timeout=1) == source_ids

    reader = ZepEntityReader()
    entities = reader.filter_defined_entities(graph_id, ["Person"], enrich_with_edges=True)
    assert entities.filtered_count == 1
    person = entities.entities[0]
    assert person.name == "Alice"
    assert person.source_episode_ids == source_ids
    assert person.related_nodes[0]["name"] == "Acme"
    assert reader.get_entity_with_context(graph_id, person.uuid).source_episode_ids == source_ids

    tools = ZepToolsService()
    result = tools.search_graph(graph_id, "Alice employment Acme", scope="edges")
    assert result.edges[0]["source_episode_ids"] == source_ids
    assert tools.get_all_edges(graph_id)[0].source_episode_ids == source_ids
    evidence = tools.get_evidence(graph_id, result.edges[0]["source_episode_ids"])
    assert evidence[0]["text"] == "Alice works at Acme."
    assert evidence[0]["kind"] == "document"

    updater = ZepGraphMemoryUpdater(graph_id, simulation_id="simulation-local")
    updater.SEND_INTERVAL = 0
    updater.start()
    updater.add_activity(AgentActivity(
        platform="twitter", agent_id=1, agent_name="Alice", action_type="CREATE_POST",
        action_args={"content": "I work at Acme."}, round_num=1,
        timestamp="2026-10-02T01:00:00Z",
    ))
    updater.stop()
    assert updater.get_stats()["items_sent"] == 1
    evidence = tools.get_evidence(graph_id)
    assert {source["kind"] for source in evidence} == {"document", "simulation"}
    assert next(s for s in evidence if s["kind"] == "simulation")["metadata"]["simulation_id"] == "simulation-local"
    assert len(tools.get_all_edges(graph_id)) == 1
    assert len(tools.get_all_edges(graph_id)[0].source_episode_ids) == 2
    builder.delete_graph(graph_id)
    from zep_cloud import NotFoundError
    with pytest.raises(NotFoundError):
        tools.get_evidence(graph_id)


@pytest.mark.parametrize("source_targets", ["bad", ["bad"], [{"source": "Unknown", "target": "Person"}]])
def test_malformed_ontology_rejected_before_ingestion(offline_backend, source_targets):
    builder = GraphBuilderService()
    graph_id = builder.create_graph("Malformed ontology")
    ontology = copy.deepcopy(ONTOLOGY)
    ontology["edge_types"][0]["source_targets"] = source_targets
    with pytest.raises(ValueError, match="source_targets|endpoint"):
        builder.set_ontology(graph_id, ontology)
    assert builder.client.graph.get(graph_id).ontology["edge_types"] == []


def test_graph_ingestion_preserves_terminal_budget_stop(offline_backend, monkeypatch):
    from app.utils.budget import BudgetExceeded
    class StoppedLLM:
        def chat_json(self, **_kwargs):
            raise BudgetExceeded("max_calls")
    monkeypatch.setattr(local_graph, "LLMClient", StoppedLLM)
    builder = GraphBuilderService()
    graph_id = builder.create_graph("Capped run")
    builder.set_ontology(graph_id, ONTOLOGY)
    with pytest.raises(BudgetExceeded):
        builder.add_text_batches(graph_id, ["Alice works at Acme."])
    assert builder.client.graph.get_evidence(graph_id) == []


def test_background_memory_update_retains_its_budget_context(offline_backend, monkeypatch):
    from app.utils.budget import BudgetContext, current_budget
    seen = []
    class ObservedLLM:
        def chat_json(self, **_kwargs):
            seen.append(current_budget())
            return copy.deepcopy(EXTRACTION)
    monkeypatch.setattr(local_graph, "LLMClient", ObservedLLM)
    builder = GraphBuilderService()
    graph_id = builder.create_graph("Context test")
    builder.set_ontology(graph_id, ONTOLOGY)
    with BudgetContext("project-context"):
        updater = ZepGraphMemoryUpdater(graph_id, simulation_id="sim-context")
        captured = current_budget()
    updater.BATCH_SIZE = 1
    updater.SEND_INTERVAL = 0
    updater.start()
    updater.add_activity(AgentActivity("twitter", 1, "Alice", "CREATE_POST", {"content": "Acme"}, 1, "2026-10-02T01:00:00Z"))
    updater.stop()
    assert seen == [captured]
    assert captured is not None
