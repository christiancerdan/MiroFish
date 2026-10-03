# Independent executions and comparative benchmark

The user approved testing whether MiroFish itself adds value over a single-model analysis. This plan implements that decision, including the run isolation required for a meaningful comparison.

## Design

Each execution receives a unique ID and its own local graph snapshot. The project graph contains the original documents; simulation observations go only into the execution graph. Snapshot creation is transactional, remaps IDs and provenance, requires a clean document source, and makes no model calls. A source is sealed after its first snapshot. Contaminated or unverifiable legacy graphs require rebuilding from uploaded documents. The current Zep adapter cannot offer equivalent snapshots, so new runs fail explicitly rather than share mutable memory.

Preparation verifies the source is clean. Reports pin the completed execution and its snapshot, including durable task parameters and saved manifests. Published report conversations use the saved report and evidence. Earlier report snapshots remain readable after a new execution.

The first benchmark is a historical replay of real headline A/B tests from the Upworthy Research Archive. It is a narrow proxy for message response, not a test of product demand or general forecasting. Public historical data may have appeared in model training. Outcomes are stored separately and are never loaded by the generation runner.

## Frozen pilot protocol

- Select one comparable same-image headline pair per experiment without using clicks, impressions, winner labels, or statistical significance. Apply the archive's documented randomization-problem exclusions before selection. Freeze a deterministic pool of 20 cases, using the first six for this pilot.
- Run two independent repeats per case and method, using the same configured base model and the same source document. The document includes three explicitly fictional reader personas as assumptions, available to both methods.
- The single-model method makes one structured prediction call. MiroFish builds its graph, prepares agents, runs one Reddit round with all three agents active, drains execution memory, generates a report, and makes one structured prediction call using that report.
- Bound each trial to 35 model calls and a wall-clock limit. Preserve failures and all available usage. Do not silently repair or replace invalid predictions.
- Freeze input and protocol hashes before model calls. Record model identity, prompts/source hashes, timing, calls, tokens, unknown monetary costs, execution identity, and artifacts.
- Score mean probabilities across repeats with equal case weight. Compare Brier score, log loss, directional accuracy, and a fixed 0.5 baseline. Bootstrap paired differences by case, never treating repeats as additional independent cases. Exclude exact observed CTR ties using a declared rule. Show complete-pair and failure-adjusted results together.

## Work and verification

1. Add transactional graph snapshots and clean-source validation. Test independence, provenance remapping, sealing, retry behavior, and rollback on invalid input.
2. Bind runner state, graph updates, preparation, and restarts to unique executions. Test memory-disabled runs, startup failures, stale state, and unsupported cloud isolation.
3. Bind report tasks, cached reports, manifests, and conversations to the selected execution. Test rerun races and historical report readback.
4. Add reproducible, attributed dataset fixtures and separate outcomes; implement a strict scorer and an opt-in live runner with unit coverage for leakage and invalid records.
5. Run the frozen pilot, preserve every scheduled outcome and failure, and publish an honest comparison with limitations. Expand the sample only under a separately frozen protocol.
6. Run relevant backend and frontend checks; review changes, deliver through the maintained fork's pull request workflow, and verify CI.

## Decision rule

Six cases cannot establish general predictive validity. The pilot can reveal integration failures, excessive compute, obvious deterioration, or enough promise to justify a larger preregistered holdout. An inconclusive or unfavorable result is a valid result. Do not tune prompts on the pilot outcomes and then present the same cases as independent validation.

## Deferred work

Semantic citation entailment, backup/restore, tenant isolation, cloud graph snapshot support, and prospective product-demand validation are separate work. The current single-owner security boundary remains relevant.
