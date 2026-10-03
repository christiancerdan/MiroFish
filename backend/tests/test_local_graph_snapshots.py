"""Execution copies preserve a verified document baseline without paid extraction."""
import hashlib
import json
import socket
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services.local_graph import LocalGraphClient


EXTRACTION = {
    "entities": [
        {"name": "Alice", "entity_type": "Entity", "summary": "Alice works at Acme.", "attributes": {"role": "engineer"}},
        {"name": "Acme", "entity_type": "Entity", "summary": "Acme employs Alice.", "attributes": {}},
    ],
    "edges": [{"source": "Alice", "target": "Acme", "edge_type": "RELATED_TO", "fact": "Alice works at Acme.", "attributes": {}}],
}


class Extractor:
    def __init__(self):
        self.calls = 0
        self.before_return = None

    def chat_json(self, **kwargs):
        self.calls += 1
        if self.before_return:
            self.before_return()
        return json.loads(json.dumps(EXTRACTION))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Snapshot tests must not connect to any network")
    monkeypatch.setattr(socket.socket, "connect", reject)


def source_graph(tmp_path, graph_id="source"):
    model = Extractor()
    client = LocalGraphClient(tmp_path / "graph.sqlite3", llm_client=model)
    client.graph.create(graph_id=graph_id, name="Source documents")
    text = "Alice works at Acme."
    operation = "build-" + graph_id
    batch = client.batch.create(metadata={"graph_id": graph_id, "mirofish_operation_id": operation, "chunk_count": 1})
    client.batch.add(batch_id=batch.batch_id, items=[{
        "type": "graph_episode", "data_type": "text", "graph_id": graph_id, "data": text,
        "metadata": {"mirofish_operation_id": operation, "chunk_index": 0, "chunk_sha256": hashlib.sha256(text.encode()).hexdigest()},
    }])
    client.batch.process(batch_id=batch.batch_id)
    return client, model


def clone(client, graph_id="run-graph", execution_id="execution-1", simulation_id="simulation-1"):
    return client.graph.clone_for_execution(source_graph_id="source", graph_id=graph_id,
                                            execution_id=execution_id, simulation_id=simulation_id)


def contents(client, graph_id):
    with client._connect() as db:
        return {table: [dict(r) for r in db.execute(f"SELECT * FROM {table} WHERE graph_id=? ORDER BY 1", (graph_id,))]
                for table in ("graphs", "documents", "episodes", "entities", "edges")}


def test_clone_preserves_source_values_and_lineage_without_extraction(tmp_path):
    client, model = source_graph(tmp_path)
    before = contents(client, "source")
    verified = client.graph.assert_clean_source("source")
    copied = clone(client)
    assert copied.source_graph_id == "source"
    assert copied.execution_id == "execution-1"
    assert copied.simulation_id == "simulation-1"
    assert copied.source_snapshot_sha256 == verified.source_snapshot_sha256
    assert len(copied.source_snapshot_sha256) == 64 and copied.snapshot_created_at
    assert model.calls == 1
    assert contents(client, "source") == before
    after = contents(client, "run-graph")
    for table in ("documents", "episodes", "entities", "edges"):
        assert len(after[table]) == len(before[table])
        assert {r["uuid"] for r in after[table]}.isdisjoint(r["uuid"] for r in before[table])
    assert after["entities"][0]["summary"] in {r["summary"] for r in before["entities"]}
    evidence = client.graph.get_evidence("run-graph")[0]
    assert evidence["metadata"]["source_graph_id"] == "source"
    assert evidence["metadata"]["source_episode_id"] == before["episodes"][0]["uuid"]
    assert evidence["metadata"]["source_document_id"] == before["documents"][0]["uuid"]
    assert evidence["source_hash"] == before["documents"][0]["source_hash"]
    with client._connect() as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT COUNT(*) FROM batches WHERE graph_id='run-graph'").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM entity_sources s JOIN entities e ON e.uuid=s.entity_uuid JOIN episodes p ON p.uuid=s.episode_uuid WHERE e.graph_id='run-graph' AND p.graph_id='run-graph'").fetchone()[0] == 2


def test_execution_writes_cannot_change_source_or_another_execution(tmp_path):
    client, model = source_graph(tmp_path)
    baseline = contents(client, "source")
    clone(client)
    clone(client, "second-graph", "execution-2")
    second = contents(client, "second-graph")
    client.graph.add(graph_id="run-graph", data="Alice posts a simulated claim.", metadata={"source": "mirofish_simulation", "simulation_id": "simulation-1", "execution_id": "execution-1"})
    assert contents(client, "source") == baseline
    assert contents(client, "second-graph") == second
    assert len(client.graph.get_evidence("run-graph")) == 2
    clone(client)  # Idempotent retry must not replace observations already written.
    assert len(client.graph.get_evidence("run-graph")) == 2
    assert model.calls == 2
    reopened = LocalGraphClient(client.db_path, llm_client=Extractor())
    assert clone(reopened).source_snapshot_sha256 == client.graph.get("run-graph").source_snapshot_sha256


@pytest.mark.parametrize("metadata", [
    {}, {"kind": "document"},
    {"source": "mirofish_simulation", "simulation_id": "simulation-1", "execution_id": "wrong"},
    {"source": "mirofish_simulation", "simulation_id": "wrong", "execution_id": "execution-1"},
])
def test_execution_rejects_unscoped_or_wrong_run_ingestion_without_model_calls(tmp_path, metadata):
    client, model = source_graph(tmp_path)
    clone(client)
    with pytest.raises(ValueError, match="matching execution_id"):
        client.graph.add(graph_id="run-graph", data="Wrong run observation", metadata=metadata)
    assert model.calls == 1
    assert len(client.graph.get_evidence("run-graph")) == 1


def test_concurrent_identical_clone_requests_share_one_complete_snapshot(tmp_path):
    client, model = source_graph(tmp_path)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: clone(client), range(3)))
    assert len({r.source_snapshot_sha256 for r in results}) == 1
    assert len({r.snapshot_created_at for r in results}) == 1
    assert len(client.graph.get_evidence("run-graph")) == 1
    assert model.calls == 1


@pytest.mark.parametrize("changes", [
    {"execution_id": "different"}, {"simulation_id": "different"},
    {"graph_id": "different"}, {"graph_id": "source"},
])
def test_clone_identity_conflicts_are_rejected(tmp_path, changes):
    client, _ = source_graph(tmp_path)
    clone(client)
    baseline = contents(client, "run-graph")
    with pytest.raises(ValueError):
        clone(client, **changes)
    assert contents(client, "run-graph") == baseline


def test_seal_blocks_ingestion_and_batch_changes_before_paid_work(tmp_path):
    client, model = source_graph(tmp_path)
    clone(client)
    with pytest.raises(ValueError, match="sealed"):
        client.graph.add(graph_id="source", data="New source data")
    with pytest.raises(ValueError, match="sealed"):
        client.batch.create(metadata={"graph_id": "source", "mirofish_operation_id": "new"})
    with pytest.raises(ValueError, match="sealed"):
        client.graph.set_ontology_definition("source", {"entity_types": [], "edge_types": []})
    assert model.calls == 1
    assert client.graph.assert_clean_source("source").source_snapshot_sha256


def test_sealed_baseline_corruption_is_not_silently_copied(tmp_path):
    client, _ = source_graph(tmp_path)
    clone(client)
    with client._connect(write=True) as db:
        db.execute("UPDATE entities SET summary='Changed after seal' WHERE graph_id='source'")
    with pytest.raises(ValueError, match="sealed baseline has changed"):
        clone(client, "second", "execution-2")


def test_ingestion_started_before_seal_is_rejected_at_commit(tmp_path):
    client, model = source_graph(tmp_path)
    baseline = contents(client, "source")
    model.before_return = lambda: clone(client)
    with pytest.raises(ValueError, match="sealed"):
        client.graph.add(graph_id="source", data="In-flight addition")
    assert contents(client, "source") == baseline
    assert len(client.graph.get_evidence("run-graph")) == 1


@pytest.mark.parametrize("mode", ["simulation", "unknown", "forged"])
def test_legacy_contamination_and_unknown_document_provenance_require_rebuild(tmp_path, mode):
    client, _ = source_graph(tmp_path)
    metadata = {"source": "mirofish_simulation"} if mode == "simulation" else {}
    if mode == "forged":
        metadata = {"mirofish_operation_id": "forged", "chunk_index": 0,
                    "chunk_sha256": hashlib.sha256(b"Untrusted addition").hexdigest()}
    client.graph.add(graph_id="source", data="Untrusted addition", metadata=metadata)
    with pytest.raises(ValueError, match="[Rr]ebuild"):
        client.graph.assert_clean_source("source")
    with pytest.raises(ValueError, match="[Rr]ebuild"):
        clone(client)
    assert contents(client, "run-graph")["graphs"] == []


@pytest.mark.parametrize("sql", [
    "UPDATE documents SET source_hash='wrong' WHERE graph_id='source'",
    "UPDATE episodes SET text='Changed source text' WHERE graph_id='source'",
    "DELETE FROM entity_sources WHERE entity_uuid=(SELECT uuid FROM entities WHERE graph_id='source' LIMIT 1)",
    "DELETE FROM edge_sources WHERE edge_uuid=(SELECT uuid FROM edges WHERE graph_id='source' LIMIT 1)",
    "UPDATE batches SET status='draft' WHERE graph_id='source'",
])
def test_invalid_source_integrity_leaves_no_clone_or_seal(tmp_path, sql):
    client, _ = source_graph(tmp_path)
    with client._connect(write=True) as db:
        db.execute(sql)
    with pytest.raises(ValueError, match="[Rr]ebuild"):
        clone(client)
    with client._connect() as db:
        assert not db.execute("SELECT 1 FROM graphs WHERE graph_id='run-graph'").fetchone()
        assert not db.execute("SELECT 1 FROM graph_source_seals WHERE graph_id='source'").fetchone()


@pytest.mark.parametrize("link", ["entity_source", "edge_source", "edge_endpoint", "document"])
def test_cross_graph_foreign_keys_are_rejected(tmp_path, link):
    client, _ = source_graph(tmp_path)
    source_graph(tmp_path, "other")
    with client._connect(write=True) as db:
        episode = db.execute("SELECT uuid FROM episodes WHERE graph_id='other'").fetchone()[0]
        node = db.execute("SELECT uuid FROM entities WHERE graph_id='other' LIMIT 1").fetchone()[0]
        if link == "entity_source":
            db.execute("UPDATE entity_sources SET episode_uuid=? WHERE entity_uuid IN (SELECT uuid FROM entities WHERE graph_id='source')", (episode,))
        elif link == "edge_source":
            db.execute("UPDATE edge_sources SET episode_uuid=? WHERE edge_uuid IN (SELECT uuid FROM edges WHERE graph_id='source')", (episode,))
        elif link == "edge_endpoint":
            db.execute("UPDATE edges SET target_node_uuid=? WHERE graph_id='source'", (node,))
        else:
            db.execute("UPDATE episodes SET document_uuid=? WHERE graph_id='source'", (episode,))
    with pytest.raises(ValueError, match="[Rr]ebuild"):
        clone(client)


def test_partial_copy_failure_rolls_back_target_and_source_seal(tmp_path):
    client, _ = source_graph(tmp_path)
    with client._connect(write=True) as db:
        db.execute("CREATE TRIGGER fail_copy BEFORE INSERT ON entities WHEN NEW.graph_id='run-graph' BEGIN SELECT RAISE(ABORT,'injected copy failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected copy"):
        clone(client)
    with client._connect() as db:
        assert not db.execute("SELECT 1 FROM graphs WHERE graph_id='run-graph'").fetchone()
        assert not db.execute("SELECT 1 FROM graph_source_seals WHERE graph_id='source'").fetchone()
    assert client.graph.assert_clean_source("source").source_snapshot_sha256


def test_execution_cannot_be_a_source_and_survives_source_delete(tmp_path):
    client, _ = source_graph(tmp_path)
    copied = clone(client)
    with pytest.raises(ValueError, match="[Rr]ebuild"):
        client.graph.assert_clean_source("run-graph")
    client.graph.delete("source")
    assert len(client.graph.get_evidence("run-graph")) == 1
    assert clone(client).source_snapshot_sha256 == copied.source_snapshot_sha256


def test_version_one_database_migrates_without_reextracting(tmp_path):
    client, _ = source_graph(tmp_path)
    baseline = contents(client, "source")
    with client._connect(write=True) as db:
        db.execute("DROP TABLE IF EXISTS graph_execution_snapshots")
        db.execute("DROP TABLE IF EXISTS graph_source_seals")
        db.execute("PRAGMA user_version=1")
    model = Extractor()
    reopened = LocalGraphClient(client.db_path, llm_client=model)
    clone(reopened)
    assert model.calls == 0
    assert contents(reopened, "source") == baseline
    with reopened._connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
