# Maintained fork: improvement plan

This fork keeps the upstream AGPL-3.0 license and attribution. Its initial target is a private workspace for one owner. The 2026-10-02 hardening pass addresses demonstrated application vulnerabilities; it is not a claim that the full dependency stack is free of vulnerabilities.

## Next: finish the security baseline

1. **Modernize or replace the pinned OASIS/CAMEL dependency tree.** Six packages retain advisories after compatible updates. Upgrade in a separate branch with real Twitter and Reddit action tests, safe model loading, and a fresh Linux container inventory. Avoid suppressing scanner results to make CI appear clean. See [dependency analysis](dependency-security.md).
2. **Isolate simulations from the API.** Run each simulation in a restricted worker with its own working directory, bounded CPU/memory/time, limited provider credentials, and network access limited to required services. Keep storage directories writable only by the application owner.
3. **Persist jobs.** Add a durable queue and transactional SQLite/Postgres job records. Atomically claim jobs in the database, recover interrupted work on restart, and support cancellation. Current duplicate prevention coordinates one process only.

## Make simulations useful and measurable

4. **Evaluate predictive claims before relying on them.** Build a dated holdout dataset, prohibit future information in inputs, compare against simple baselines, and measure calibration and outcome error. Repeat across models and random seeds; report uncertainty and failures. Generated scenarios are hypotheses until validated.
5. **Preserve evidence provenance.** Link report statements to source passages and observed simulation events. Separate source facts, modeled assumptions, and generated behavior. Save input hashes, model IDs, prompts, simulation parameters, and timestamps with every run.
6. **Add cost and resource budgets.** Show an estimate before starting, meter token calls and elapsed time, and stop at an explicit per-run cap. Cache reusable extraction/profile work, use smaller models for routine tasks, and reserve a stronger model for synthesis. The current round/concurrency bounds are safety limits, not spend guarantees.

## Improve ownership and everyday use

7. **Replace mandatory Zep Cloud with a graph adapter.** Preserve the existing integration and add a local graph/vector implementation. This enables private or offline deployments and gives users a clear retention/export/delete policy. Ollama support alone does not make document processing local.
8. **Expose provider checks in onboarding.** Add a guided configuration screen with model discovery, text/JSON/tool checks, clear cloud-versus-local labels, and actionable missing-key messages. Start with an end-to-end synthetic example before uploading real documents.
9. **Improve report trust and workflow.** Show progress based on durable job state, make failures recoverable, add comparison views across runs, and export source-linked reports. Label outputs as simulation results rather than measured future probabilities.
10. **Add accounts only when collaboration is needed.** Replace the shared owner key with OIDC/passkeys, per-project authorization, per-user budgets, and audit history before supporting separate users or a public service.

Suggested first milestone: one reproducible, low-cost Ollama Cloud example that survives a server restart and produces an evidence-linked report, with the full simulation dependency inventory reviewed and the Zep/local-storage choice explicit.
