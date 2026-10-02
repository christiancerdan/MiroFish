# Owned memory and reliable runs implementation plan

**Goal:** Run MiroFish without a Zep account, protect model usage with persistent budgets, recover background work, isolate a modernized simulation runtime, and produce auditable reports with a forecast evaluation harness.

**Authorization:** The user approved the four improvements in the preceding delivery and explicitly asked whether we can own the Zep replacement. That approval covers execution. Design and review happen within this work; no additional approval pause is needed for the reversible implementation.

**Architecture:** Keep the existing Flask/Vue workflow and provider abstraction. SQLite stores local graph evidence, durable jobs, and usage reservations. A separate Python environment runs simulation subprocesses with a minimal environment and resource bounds. Existing Zep Cloud support remains opt-in. Ollama Cloud still receives prompts even when graph storage is local.

**Alternatives considered:** Self-hosted Graphiti offers richer temporal and semantic retrieval but introduces a graph database and more operational dependencies. Retaining mandatory Zep preserves its managed features but fails the ownership requirement. A focused local SQLite adapter implements the application's actual graph operations and preserves evidence; it does not claim full Zep feature parity.

## Contracts and constraints

- Preserve AGPL and all vendored upstream licenses/provenance.
- `GRAPH_BACKEND=local` by default; `zep` explicitly selects cloud. Local mode never needs or sends a Zep key.
- `LOCAL_GRAPH_DB_PATH`, `JOBS_DB_PATH`, `BUDGET_DB_PATH` live under the configured uploads directory by default.
- `BudgetContext(project_id)` spans graph, preparation, simulation, and reports. Reservations are transactional across processes. Unknown provider prices must not be represented as a known monetary cost.
- Queued durable jobs resume on startup. Ambiguous interrupted side effects require explicit retry and remain visible; do not claim exactly-once behavior.
- Simulations receive only required model/budget settings. Worker imports must not reload the private root `.env`.
- Source IDs and hashes verify references and reproducibility, not the truth of model-generated claims. Reports remain unvalidated forecasts unless supported by an actual evaluation.
- Only synthetic inputs are used for live integration tests. No cloud documents or production projects are migrated implicitly.

## Implementation lanes

- [x] Local memory: implement compatible graph/batch operations, transactional extraction and deduplication, source episodes, retrieval, and deletion in local graph modules; route the client factory and key guards. Verify real SQLite persistence, rejected malformed extraction, isolation between graphs, retry/idempotency, source retrieval, and no Zep network calls.
- [x] Durable jobs: persist task state, serialized handler inputs, claims, leases, dedupe keys, attempts, and cancellation; replace graph/prepare/report thread closures with named handlers. Verify restart reconstruction, concurrent claims, queued recovery, interrupted status, explicit retry, and cancellation.
- [x] Usage budgets: reserve calls/tokens/output/time before requests; settle actual usage conservatively, including failed calls and concurrency. Bind SDK and CAMEL clients and subprocess contexts. Expose authenticated configuration/usage. Verify caps before network access, thread/process races, missing usage, retries, unknown pricing, and exceeded states.
- [x] Simulation runtime: remove obsolete runtime dependency pins using a reviewed downstream OASIS package if upstream remains constrained; retain code and license provenance. Install a separate optional environment. Restrict child environment and working directories; apply supported CPU/address-space/wall limits. Verify imports, real persisted tool actions, minimal credentials, deadline termination, and interruption handling.
- [x] Report quality: snapshot evidence with stable IDs and hashes, validate citation targets, persist model/input/run metadata, and display uncertainty. Add binary forecast evaluation with dated evidence cutoffs, Brier/log scores, calibration bins, baselines, and synthetic examples. Reject invalid probabilities and evidence from after the forecast cutoff.
- [x] Integration/UI: add backend/provider status, budget controls and usage, interrupted-job retry/cancel, source evidence links, and report limitations. Preserve authentication and sanitized rendering. Verify UI behavior, static production build, and authenticated API contracts.

## Completion checks

- [x] Run the complete backend/root and frontend suites, including the separate simulation environment tests.
- [x] Audit the exact core and simulation resolutions without globally ignored advisories; record remaining platform-specific limitations accurately.
- [x] Run a small synthetic local graph → profiles → simulation → report path with Ollama Cloud and no Zep key. Validate persisted graph sources and action records, budget usage, report evidence, and restart behavior.
- [x] Update setup, privacy, isolation, evaluation, and migration documentation. Existing Zep graphs remain available through explicit Zep configuration; there is no automatic remote-data export claim.
- [ ] Review changes, publish a pull request in the user's fork, pass CI, merge, and sync local main as in the prior approved delivery.

Release tracking: [pull request #2](https://github.com/christiancerdan/MiroFish/pull/2). Live verification passed with local memory and Ollama Cloud; final publication/merge status is recorded on that pull request.
