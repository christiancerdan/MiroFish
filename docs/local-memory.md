# Local graph memory

MiroFish can store graph memory in its own SQLite database without a Zep account. Local memory is the default (`GRAPH_BACKEND=local`). It supports the graph, entity, relationship, source episode, batch, and search operations used by the application.

## Configuration and data ownership

Set the backend in the project-root `.env`:

```dotenv
GRAPH_BACKEND=local
# Optional: an absolute path on persistent storage.
# LOCAL_GRAPH_DB_PATH=/path/to/mirofish/local_graph.sqlite3
```

When `LOCAL_GRAPH_DB_PATH` is omitted, the application places `memory.sqlite3` under its configured uploads directory (`backend/uploads` by default). Keep that directory on persistent storage in deployments. Back up the database with SQLite's backup API or with the application stopped; copying only the main database file while a WAL-mode database is active can omit committed changes. Restore the application files and graph database together so project graph IDs still resolve.

Local mode does not require a Zep API key. Entity and relationship extraction still uses the configured model provider. If that provider is Ollama Cloud or another hosted service, source text, the ontology, and existing graph entities can leave the machine in model prompts. Choosing local graph storage does not make hosted model inference local. Use the existing provider configuration to select the desired inference endpoint.

For the existing managed backend, set `GRAPH_BACKEND=zep` and provide `ZEP_API_KEY`. Switching the configuration selects a different store; it does not export or migrate existing graphs between stores. Existing projects retain their graph IDs, so select the backend containing a project's graph when reopening it. There is no automatic download of Zep data.

## Extraction, persistence, and replay

Each graph stores its ontology. Extraction receives the source document, that ontology, and existing entities in structured input. The adapter validates extracted entity types, relationship types, endpoint existence, and allowed source/target type pairs before committing graph changes. Invalid model output fails the operation rather than quietly inventing an ontology type or dropping the invalid relationship.

Nodes and relationships have stable IDs and retain the source episode IDs that support them. Repeated entities and facts merge source references. Replaying the same direct addition with the same text and metadata reuses its episode; changed metadata can represent a distinct source event even when its text is identical.

Document ingestion retains batch operation identity and submitted items in SQLite. Replayed creation with the same operation identity, replayed items, and repeated processing of a successful batch do not create duplicate graph records. Batch processing is synchronous in this adapter. A batch commits its graph changes together: if extraction or validation fails on any item, the batch is marked failed and earlier items do not leave partially committed entities, relationships, or source episodes. Batch state remains available after an application restart. Concurrent attempts or a crash before committing can still cause repeated model requests; the store deduplicates committed graph effects, not provider usage.

Graph deletion removes that graph's stored memory. Entities with the same names in other graphs belong to separate graphs and remain available. Project and simulation lifecycle checks still control when the application permits deletion.

## Retrieval and source evidence

Local search provides bounded lexical retrieval over persisted graph content. Query terms are matched against stored text, with case-insensitive substring matches contributing to ranking; results respect the requested scope and limit, up to 50 results per scope. Queries are limited to 400 characters. This is useful for finding explicit entity names and terms already present in facts. Paraphrases, multilingual equivalents, and conceptually related passages can require different wording or more than one query.

The API accepts the existing application's search arguments, including `reranker`, for compatibility. Local retrieval does not run a Zep cross-encoder, vector search, semantic reranker, or temporal reasoning engine. It does not claim semantic or temporal equivalence with Zep. Timestamps record evidence context; they do not automatically resolve contradictory facts or establish when a fact stopped being true.

`client.graph.get_evidence(graph_id, source_ids=None)` returns persisted source records. Each record includes an evidence ID, source episode ID, graph ID, source kind (`document` or `simulation`), original text, creation timestamp, reference time, and metadata. Supplying source IDs filters within the requested graph. Simulation evidence keeps available simulation ID, platform, round, and activity metadata alongside its original text.

Use source IDs to inspect the text underlying a graph fact or report citation. A source link proves that stored text exists and can be revisited; it does not prove a model's extraction, simulation, or forecast is correct. Local memory is an auditable input store, not a validation of predictions.

## Offline verification

From `backend`, with development dependencies installed:

```sh
python -m pytest tests/test_local_graph_backend.py -q
```

These tests use a deterministic fake model and real temporary SQLite files. They cover restart durability, ontology rejection, atomic batch failures, replay deduplication, graph isolation and deletion, pagination, lexical search, and document/simulation source evidence. A socket guard rejects network connections. They establish the adapter's persistence and API contracts; they do not measure extraction quality from a live model or establish parity with Zep Cloud.
