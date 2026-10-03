# Maintained fork: progress and next work

This fork keeps upstream AGPL-3.0 attribution and targets a private, single-owner workspace. Completed controls reduce demonstrated risks; they do not establish forecast accuracy or guarantee absence of vulnerabilities.

## Implemented

- **Owned memory:** SQLite document/entity/relationship/evidence storage, transactional batches, replay deduplication and bounded lexical search. Zep Cloud remains an explicit option. Cloud model prompts still leave the server. See [local memory](local-memory.md).
- **Modern dependencies:** separate API and simulation environments, reviewed OASIS packaging/source fork, safe pinned recommendation loaders and blocking vulnerability scans. See [dependency analysis](dependency-security.md).
- **Bounded simulation processes:** minimal credential environment, per-run working directories, CPU/wall limits, Linux address-space limits and a restricted non-root container. This is not a network sandbox or a separate security identity for each run. See [deployment](deployment.md).
- **Durable jobs:** SQLite claims, leases, saved handler parameters, automatic queued recovery and explicit interrupted retries. Provider effects are not exactly-once. See [recovery](durable-jobs.md).
- **Usage budgets:** shared project ledgers across preparation, simulations and reports; pre-request call/token/output/time reservations; optional explicitly priced cost caps. Unknown subscription costs remain unknown. See [budgets](run-budgets.md).
- **Auditable reports:** immutable source snapshots, validated paragraph/evidence-ID output with application-rendered citations, uncertainty labels and versioned run manifests. Reference integrity does not prove semantic support or truth.
- **Evaluation harness:** dated binary forecasts, leakage checks, proper scores, explicit baselines, calibration bins and repeated-run variability. Synthetic fixtures verify the harness, not prediction performance. See [evaluation](forecast-evaluation.md).
- **Workspace controls:** provider/storage disclosure, budget usage and limits, job recovery, and evidence inspection in reports.
- **Comparative measurement:** a frozen, audited 80-trial historical comparison with all failures retained found no established predictive advantage. The [prospective study kit](prospective-customer-study.md) supports local protocol preparation, manual forecast sealing and aggregate response scoring; real experiments remain to be conducted.

## Next priorities

1. **Measure usefulness with prospective customer experiments.** Select a real product, audience and response event; freeze exact variants, forecasts and stopping rules before launch. Compare against the same-model baseline and retain failures. The [historical comparison](benchmark-holdout-2026-10-03.md) is inconclusive and does not establish prospective accuracy; local timestamps and operator attestations are not external preregistration.
2. **Strengthen worker isolation.** Separate worker containers/users and outbound network policy, durable process supervision, and a platform-tested cancellation story. Multi-server deployment also needs transactional project/simulation lifecycle coordination.
3. **Improve retrieval and migration.** Evaluate local embeddings and temporal contradiction handling against lexical search; add explicit Zep export/import, complete project export and tested restore tooling. Keep data ownership and privacy visible.
4. **Improve model onboarding.** Add model discovery and text/JSON/tool capability checks to the UI, actionable configuration errors and a small synthetic guided run. Cache reusable work and expose estimates based on measured workloads.
5. **Compare runs and claims.** Add scenario comparison, source-linked exports and claim-to-passage support evaluation beyond ID/hash validation.
6. **Add accounts when needed.** OIDC/passkeys, per-project authorization, per-user budgets and audit history are prerequisites for multiple independent users or a public service.
