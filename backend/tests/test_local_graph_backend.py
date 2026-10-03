"""Offline behavioral contracts for the durable local graph backend."""

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
import socket
import threading

import pytest
from zep_cloud import BatchAddItem, NotFoundError
from zep_cloud.external_clients.ontology import EntityModel

from app.services.local_graph import LocalGraphClient


ONTOLOGY = {
    "entity_types": [
        {"name": "Person", "description": "A person", "attributes": []},
        {"name": "Company", "description": "A company", "attributes": []},
    ],
    "edge_types": [{
        "name": "WORKS_AT",
        "description": "Employment",
        "source_targets": [{"source": "Person", "target": "Company"}],
        "attributes": [],
    }],
}


def extraction(person="Alice", company="Acme", *, fact=None):
    return {
        "entities": [
            {"name": person, "entity_type": "Person", "summary": f"{person} is an engineer.", "attributes": {}},
            {"name": company, "entity_type": "Company", "summary": f"{company} makes tools.", "attributes": {}},
        ],
        "edges": [{
            "source": person,
            "target": company,
            "edge_type": "WORKS_AT",
            "fact": fact or f"{person} works at {company}",
            "attributes": {},
        }],
    }


class FakeLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def chat_json(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if not self.responses:
            raise AssertionError("Unexpected additional extraction call")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return copy.deepcopy(result)


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def forbidden_connect(*_args, **_kwargs):
        raise AssertionError("Local graph tests must never connect to the network")

    monkeypatch.setattr(socket.socket, "connect", forbidden_connect)


def make_client(tmp_path, *responses, graph_id="graph-a"):
    llm = FakeLLM(*responses)
    client = LocalGraphClient(str(tmp_path / "memory.sqlite3"), llm_client=llm)
    client.graph.create(graph_id=graph_id, name="Offline graph", description="Test graph")
    client.graph.set_ontology_definition(graph_id, copy.deepcopy(ONTOLOGY))
    return client, llm


def nodes(client, graph_id="graph-a"):
    return client.graph.node.with_raw_response.get_by_graph_id(graph_id=graph_id, limit=100).data


def edges(client, graph_id="graph-a"):
    return client.graph.edge.with_raw_response.get_by_graph_id(graph_id=graph_id, limit=100).data


def item(text, index, graph_id="graph-a"):
    return BatchAddItem(
        type="graph_episode",
        graph_id=graph_id,
        data=text,
        data_type="text",
        source_description="MiroFish source document chunk",
        metadata={
            "chunk_index": index,
            "payload_sha256": hashlib.sha256(text.encode()).hexdigest(),
        },
    )


def test_document_extraction_is_durable_and_keeps_source_evidence(tmp_path):
    client, llm = make_client(tmp_path, extraction())
    episode = client.graph.add(
        graph_id="graph-a", type="text", data="Alice works at Acme.",
        metadata={"filename": "people.txt"},
    )
    assert client.graph.episode.get(uuid_=episode.uuid_).processed is True
    graph_nodes = nodes(client)
    graph_edges = edges(client)
    assert {node.name for node in graph_nodes} == {"Alice", "Acme"}
    assert all("Entity" in node.labels for node in graph_nodes)
    assert set(next(node for node in graph_nodes if node.name == "Alice").labels) == {"Entity", "Person"}
    assert len(graph_edges) == 1
    assert graph_edges[0].fact == "Alice works at Acme"
    assert episode.uuid_ in graph_edges[0].source_episode_ids
    assert all(episode.uuid_ in node.source_episode_ids for node in graph_nodes)

    # The extractor receives a structured, reviewable input, including ontology.
    messages, _kwargs = llm.calls[0]
    payload = json.loads(next(message["content"] for message in messages if message["role"] == "user"))
    assert payload["document"] == "Alice works at Acme."
    assert {item["name"] for item in payload["ontology"]["entity_types"]} == {"Person", "Company"}
    assert payload["ontology"]["edge_types"][0]["source_targets"] == ONTOLOGY["edge_types"][0]["source_targets"]
    assert all("attributes" not in item for item in payload["ontology"]["entity_types"])
    assert payload["existing_entities"] == []

    reopened = LocalGraphClient(str(tmp_path / "memory.sqlite3"), llm_client=FakeLLM())
    assert reopened.graph.get("graph-a").name == "Offline graph"
    assert {node.uuid_ for node in nodes(reopened)} == {node.uuid_ for node in graph_nodes}
    assert [edge.uuid_ for edge in edges(reopened)] == [graph_edges[0].uuid_]
    evidence = reopened.graph.get_evidence("graph-a")
    assert len(evidence) == 1
    assert evidence[0]["source_id"] == episode.uuid_
    assert evidence[0]["kind"] == "document"
    assert evidence[0]["text"] == "Alice works at Acme."
    assert evidence[0]["metadata"]["filename"] == "people.txt"
    assert evidence[0]["graph_id"] == "graph-a"
    assert evidence[0]["id"]
    assert evidence[0]["created_at"]


def test_add_replay_reuses_episode_without_another_llm_call(tmp_path):
    client, llm = make_client(tmp_path, extraction())
    args = {"graph_id": "graph-a", "type": "text", "data": "Alice works at Acme.", "metadata": {"round": 1}}
    first = client.graph.add(**args)
    second = client.graph.add(**args)
    reopened = LocalGraphClient(str(tmp_path / "memory.sqlite3"), llm_client=FakeLLM())
    third = reopened.graph.add(**args)
    assert first.uuid_ == second.uuid_ == third.uuid_
    assert len(llm.calls) == 1
    assert len(nodes(reopened)) == 2
    assert len(edges(reopened)) == 1
    assert len(reopened.graph.get_evidence("graph-a")) == 1


def test_repeated_facts_merge_sources_and_metadata_distinguishes_episodes(tmp_path):
    client, llm = make_client(tmp_path, extraction(), extraction())
    first = client.graph.add(graph_id="graph-a", type="text", data="Alice works at Acme.", metadata={"round": 1})
    second = client.graph.add(graph_id="graph-a", type="text", data="Alice works at Acme.", metadata={"round": 2})
    assert first.uuid_ != second.uuid_
    assert len(nodes(client)) == 2
    assert len(edges(client)) == 1
    assert set(edges(client)[0].source_episode_ids) == {first.uuid_, second.uuid_}
    assert len(client.graph.get_evidence("graph-a")) == 2
    payload = json.loads(next(m["content"] for m in llm.calls[1][0] if m["role"] == "user"))
    assert {entity["name"] for entity in payload["existing_entities"]} == {"Alice", "Acme"}


@pytest.mark.parametrize("invalid", ["unknown_entity", "unknown_edge", "missing_endpoint", "wrong_endpoint_type"])
def test_invalid_extraction_cannot_partially_write_graph(tmp_path, invalid):
    response = extraction()
    if invalid == "unknown_entity":
        response["entities"][1]["entity_type"] = "InventedCompanyType"
    elif invalid == "unknown_edge":
        response["edges"][0]["edge_type"] = "INVENTED_RELATION"
    elif invalid == "missing_endpoint":
        response["edges"][0]["target"] = "Missing Company"
    else:
        response["edges"][0].update(source="Acme", target="Alice")
    client, _llm = make_client(tmp_path, response)
    with pytest.raises(ValueError):
        client.graph.add(graph_id="graph-a", type="text", data="Invalid extraction")
    assert nodes(client) == []
    assert edges(client) == []
    assert client.graph.get_evidence("graph-a") == []


def test_llm_failure_keeps_existing_graph_and_evidence_intact(tmp_path):
    client, _llm = make_client(tmp_path, extraction(), RuntimeError("model unavailable"))
    episode = client.graph.add(graph_id="graph-a", type="text", data="Alice works at Acme.")
    prior_node_ids = {node.uuid_ for node in nodes(client)}
    with pytest.raises(RuntimeError, match="model unavailable"):
        client.graph.add(graph_id="graph-a", type="text", data="A second document")
    assert {node.uuid_ for node in nodes(client)} == prior_node_ids
    assert len(edges(client)) == 1
    assert [entry["source_id"] for entry in client.graph.get_evidence("graph-a")] == [episode.uuid_]


def test_sdk_entity_models_are_accepted_as_an_ontology(tmp_path):
    class Person(EntityModel):
        """A person appearing in a document."""

    llm = FakeLLM({"entities": [extraction()["entities"][0]], "edges": []})
    client = LocalGraphClient(str(tmp_path / "sdk.sqlite3"), llm_client=llm)
    client.graph.create(graph_id="sdk", name="SDK ontology")
    client.graph.set_ontology(graph_ids=["sdk"], entities={"Person": Person})
    client.graph.add(graph_id="sdk", type="text", data="Alice is an engineer.")
    assert set(nodes(client, "sdk")[0].labels) == {"Entity", "Person"}


def test_graph_isolation_and_delete_remove_only_owned_records(tmp_path):
    client, _llm = make_client(tmp_path, extraction(), extraction())
    client.graph.create(graph_id="graph-b", name="Other graph")
    client.graph.set_ontology_definition("graph-b", copy.deepcopy(ONTOLOGY))
    first = client.graph.add(graph_id="graph-a", type="text", data="Alice works at Acme.")
    second = client.graph.add(graph_id="graph-b", type="text", data="Alice works at Acme.")
    a_ids = {node.uuid_ for node in nodes(client, "graph-a")}
    b_ids = {node.uuid_ for node in nodes(client, "graph-b")}
    assert a_ids.isdisjoint(b_ids)
    assert first.uuid_ != second.uuid_
    assert client.graph.get_evidence("graph-b", source_ids=[first.uuid_]) == []
    client.graph.delete(graph_id="graph-a")
    with pytest.raises(NotFoundError):
        client.graph.get("graph-a")
    with pytest.raises(NotFoundError):
        client.graph.node.get(uuid_=next(iter(a_ids)))
    with pytest.raises(NotFoundError):
        client.graph.episode.get(uuid_=first.uuid_)
    assert {node.uuid_ for node in nodes(client, "graph-b")} == b_ids
    assert len(edges(client, "graph-b")) == 1
    assert len(client.graph.get_evidence("graph-b")) == 1


def test_node_and_edge_pagination_visits_each_record_once(tmp_path):
    client, _llm = make_client(tmp_path, extraction(), extraction("Bob", "Beta"), extraction("Cara", "Cedar"))
    for name in ("Alice", "Bob", "Cara"):
        client.graph.add(graph_id="graph-a", type="text", data=f"Document about {name}")
    for api, expected_count in ((client.graph.node, 6), (client.graph.edge, 3)):
        cursor = None
        seen_ids = []
        seen_cursors = set()
        for _page in range(expected_count + 1):
            response = api.with_raw_response.get_by_graph_id(graph_id="graph-a", limit=2, cursor=cursor)
            assert len(response.data) <= 2
            seen_ids.extend(record.uuid_ for record in response.data)
            cursor = response.headers.get("zep-next-cursor")
            if cursor is None:
                break
            assert cursor not in seen_cursors
            seen_cursors.add(cursor)
        else:
            pytest.fail("Pagination did not terminate")
        assert len(seen_ids) == len(set(seen_ids)) == expected_count


def test_node_get_and_incident_edges_include_both_directions(tmp_path):
    client, _llm = make_client(tmp_path, extraction())
    client.graph.add(graph_id="graph-a", type="text", data="Alice works at Acme.")
    for node in nodes(client):
        assert client.graph.node.get(uuid_=node.uuid_).name == node.name
        incident = client.graph.node.get_edges(node_uuid=node.uuid_)
        assert [edge.uuid_ for edge in incident] == [edges(client)[0].uuid_]


def test_lexical_search_excludes_unrelated_results_and_honors_scope_and_limit(tmp_path):
    client, _llm = make_client(tmp_path, extraction(), extraction("Bob", "Beta"))
    client.graph.add(graph_id="graph-a", type="text", data="Alice works at Acme.")
    client.graph.add(graph_id="graph-a", type="text", data="Bob works at Beta.")
    result = client.graph.search(graph_id="graph-a", query="Alice", scope="edges", limit=1, reranker="cross_encoder")
    assert len(result.edges) == 1
    assert result.edges[0].fact == "Alice works at Acme"
    result = client.graph.search(graph_id="graph-a", query="works", scope="edges", limit=1)
    assert len(result.edges) == 1
    result = client.graph.search(graph_id="graph-a", query="Bob", scope="nodes", limit=1)
    assert [node.name for node in result.nodes] == ["Bob"]
    result = client.graph.search(graph_id="graph-a", query="unrelatedxyz", scope="edges", limit=10)
    assert result.edges == []


def test_simulation_evidence_preserves_metadata_and_reference_time(tmp_path):
    client, _llm = make_client(tmp_path, extraction(), extraction())
    document = client.graph.add(graph_id="graph-a", type="text", data="Alice works at Acme.")
    reference_time = "2026-10-01T12:00:00Z"
    simulation = client.graph.add(
        graph_id="graph-a", type="text", data="Alice posted about work at Acme.",
        created_at=reference_time, source_description="MiroFish simulation activity batch",
        metadata={"source": "mirofish_simulation", "simulation_id": "sim-1", "platform": "twitter", "first_round": 2},
    )
    evidence = client.graph.get_evidence("graph-a", source_ids=[simulation.uuid_])
    assert len(evidence) == 1
    assert evidence[0]["kind"] == "simulation"
    assert evidence[0]["source_id"] == simulation.uuid_
    assert datetime.fromisoformat(evidence[0]["reference_time"]) == datetime.fromisoformat(reference_time.replace("Z", "+00:00"))
    assert evidence[0]["metadata"]["simulation_id"] == "sim-1"
    assert evidence[0]["metadata"]["first_round"] == 2
    assert client.graph.get_evidence("graph-a", source_ids=["missing"]) == []
    assert {entry["source_id"] for entry in client.graph.get_evidence("graph-a")} == {document.uuid_, simulation.uuid_}


def test_batch_operation_and_item_replays_are_durable_and_idempotent(tmp_path):
    client, llm = make_client(tmp_path, extraction(), extraction("Bob", "Beta"))
    metadata = {"graph_id": "graph-a", "mirofish_operation_id": "build-1", "chunk_count": 2}
    batch = client.batch.create(metadata=metadata)
    assert client.batch.create(metadata=metadata).batch_id == batch.batch_id
    items = [item("Alice works at Acme.", 0), item("Bob works at Beta.", 1)]
    accepted = client.batch.add(batch_id=batch.batch_id, items=items)
    replayed = client.batch.add(batch_id=batch.batch_id, items=items)
    assert [entry.episode_uuid for entry in accepted] == [entry.episode_uuid for entry in replayed]
    assert nodes(client) == []
    assert client.graph.get_evidence("graph-a") == []
    client.batch.process(batch_id=batch.batch_id)
    assert client.batch.get(batch_id=batch.batch_id).status == "succeeded"
    assert len(llm.calls) == 2
    assert len(nodes(client)) == 4
    assert len(edges(client)) == 2
    reopened = LocalGraphClient(str(tmp_path / "memory.sqlite3"), llm_client=FakeLLM())
    assert reopened.batch.create(metadata=metadata).batch_id == batch.batch_id
    reopened.batch.process(batch_id=batch.batch_id)
    assert len(nodes(reopened)) == 4
    assert len(reopened.graph.get_evidence("graph-a")) == 2
    listed = reopened.batch.list(limit=100, cursor=None)
    assert batch.batch_id in [entry.batch_id for entry in listed.batches]
    page = reopened.batch.list_items(batch_id=batch.batch_id, limit=1, cursor=None)
    assert len(page.items) == 1
    assert page.items[0].status == "succeeded"
    assert page.next_cursor is not None
    second = reopened.batch.list_items(batch_id=batch.batch_id, limit=1, cursor=page.next_cursor)
    assert len(second.items) == 1
    assert second.items[0].episode_uuid != page.items[0].episode_uuid
    assert second.next_cursor is None


def test_failed_batch_does_not_commit_earlier_valid_item(tmp_path):
    invalid = extraction("Bob", "Beta")
    invalid["edges"][0]["target"] = "Nonexistent endpoint"
    client, _llm = make_client(tmp_path, extraction(), invalid)
    batch = client.batch.create(metadata={"graph_id": "graph-a", "mirofish_operation_id": "bad-build"})
    accepted = client.batch.add(batch_id=batch.batch_id, items=[item("Valid first chunk", 0), item("Invalid second chunk", 1)])
    try:
        client.batch.process(batch_id=batch.batch_id)
    except (ValueError, RuntimeError):
        pass
    assert client.batch.get(batch_id=batch.batch_id).status == "failed"
    assert nodes(client) == []
    assert edges(client) == []
    assert client.graph.get_evidence("graph-a") == []
    for entry in accepted:
        with pytest.raises(NotFoundError):
            client.graph.episode.get(uuid_=entry.episode_uuid)


def test_failed_extraction_batch_status_survives_restart(tmp_path):
    client, _llm = make_client(tmp_path, RuntimeError("extraction unavailable"))
    batch = client.batch.create(metadata={"graph_id": "graph-a", "mirofish_operation_id": "failed-build"})
    client.batch.add(batch_id=batch.batch_id, items=[item("A source document", 0)])
    try:
        client.batch.process(batch_id=batch.batch_id)
    except RuntimeError:
        pass
    reopened = LocalGraphClient(str(tmp_path / "memory.sqlite3"), llm_client=FakeLLM())
    assert reopened.batch.get(batch_id=batch.batch_id).status == "failed"
    assert nodes(reopened) == []
    assert reopened.graph.get_evidence("graph-a") == []


class ConcurrentLLM:
    """Let both extractors finish before either transaction can commit."""

    def __init__(self):
        self.barrier = threading.Barrier(2, timeout=10)

    def chat_json(self, messages, **kwargs):
        self.barrier.wait()
        return extraction()


def test_concurrent_direct_replay_commits_one_episode(tmp_path):
    client, _llm = make_client(tmp_path)
    llm = ConcurrentLLM()
    first = LocalGraphClient(client.db_path, llm_client=llm)
    second = LocalGraphClient(client.db_path, llm_client=llm)

    def add(local):
        return local.graph.add(graph_id="graph-a", type="text", data="Alice works at Acme.").uuid_

    with ThreadPoolExecutor(max_workers=2) as executor:
        episode_ids = list(executor.map(add, [first, second]))
    assert episode_ids[0] == episode_ids[1]
    assert len(nodes(client)) == 2
    assert len(edges(client)) == 1
    assert [entry["source_id"] for entry in client.graph.get_evidence("graph-a")] == [episode_ids[0]]


def test_concurrent_batch_processing_commits_one_set_of_sources(tmp_path):
    client, _llm = make_client(tmp_path)
    batch = client.batch.create(metadata={"graph_id": "graph-a", "mirofish_operation_id": "concurrent-build", "chunk_count": 1})
    client.batch.add(batch_id=batch.batch_id, items=[item("Alice works at Acme.", 0)])
    llm = ConcurrentLLM()
    first = LocalGraphClient(client.db_path, llm_client=llm)
    second = LocalGraphClient(client.db_path, llm_client=llm)

    def process(local):
        return local.batch.process(batch_id=batch.batch_id).status

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(process, [first, second]))
    assert statuses == ["succeeded", "succeeded"]
    assert len(nodes(client)) == 2
    assert len(edges(client)) == 1
    assert len(client.graph.get_evidence("graph-a")) == 1
    assert client.batch.get(batch_id=batch.batch_id).progress.succeeded_items == 1


def test_batch_operation_identity_rejects_different_metadata(tmp_path):
    client, _llm = make_client(tmp_path)
    metadata = {"graph_id": "graph-a", "mirofish_operation_id": "build-1", "chunk_count": 1}
    batch = client.batch.create(metadata=metadata)
    with pytest.raises(ValueError, match="different metadata"):
        client.batch.create(metadata={**metadata, "chunk_count": 2})
    assert client.batch.get(batch_id=batch.batch_id).metadata == metadata
    assert len(client.batch.list(limit=100).batches) == 1


def test_batch_item_conflict_rolls_back_new_items_in_the_same_add(tmp_path):
    client, _llm = make_client(tmp_path)
    batch = client.batch.create(metadata={"graph_id": "graph-a", "mirofish_operation_id": "build-1"})
    original = client.batch.add(batch_id=batch.batch_id, items=[item("Original source", 0)])
    with pytest.raises(ValueError, match="replay differs"):
        client.batch.add(batch_id=batch.batch_id, items=[item("New source", 1), item("Changed source", 0)])
    retained = client.batch.list_items(batch_id=batch.batch_id, limit=100).items
    assert [entry.episode_uuid for entry in retained] == [original[0].episode_uuid]
    assert client.batch.get(batch_id=batch.batch_id).status == "draft"
    assert client.graph.get_evidence("graph-a") == []


def test_batch_rejects_foreign_graph_items_and_appends_after_processing(tmp_path):
    client, _llm = make_client(tmp_path, extraction())
    batch = client.batch.create(metadata={"graph_id": "graph-a", "mirofish_operation_id": "build-1"})
    with pytest.raises(ValueError, match="graph_id"):
        client.batch.add(batch_id=batch.batch_id, items=[item("Wrong graph", 0, graph_id="graph-b")])
    assert client.batch.list_items(batch_id=batch.batch_id).items == []
    client.batch.add(batch_id=batch.batch_id, items=[item("Alice works at Acme.", 0)])
    client.batch.process(batch_id=batch.batch_id)
    with pytest.raises(ValueError, match="processed batch"):
        client.batch.add(batch_id=batch.batch_id, items=[item("Late item", 1)])
    assert len(client.batch.list_items(batch_id=batch.batch_id).items) == 1
    assert len(client.graph.get_evidence("graph-a")) == 1


def test_changed_ontology_during_extraction_prevents_stale_commit(tmp_path):
    client, _llm = make_client(tmp_path)

    class OntologyChangingLLM:
        def chat_json(self, messages, **kwargs):
            replacement = copy.deepcopy(ONTOLOGY)
            replacement["entity_types"][0]["description"] = "A revised person type"
            client.graph.set_ontology_definition("graph-a", replacement)
            return extraction()

    concurrent_client = LocalGraphClient(client.db_path, llm_client=OntologyChangingLLM())
    with pytest.raises(ValueError, match="ontology changed"):
        concurrent_client.graph.add(graph_id="graph-a", type="text", data="Alice works at Acme.")
    assert nodes(client) == []
    assert edges(client) == []
    assert client.graph.get_evidence("graph-a") == []
