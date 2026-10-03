# Reliability development — October 3, 2026

These checks reuse old pilot cases for diagnosis. They are not held-out accuracy
measurements and must not be pooled with either the original pilot or the fresh
twenty-case comparison.

## Attempt 1

Source commit `a78dadece2cb7cecb70ad73760f6307954c0dbf0`, protocol
`07de92dce33915f077702ddaa407012915883b62bb502e73e1ae77bbd7bdfb89`.
One MiroFish execution each on old cases `upworthy-001` and `upworthy-003`, using
the configured `gpt-oss:20b-cloud`, a 35-call budget, a 900-second deadline, and
two concurrent trials. No baseline or outcome scoring was run.

Both executions passed ontology, extraction, simulation, and memory ingestion.
Both original graphs matched their sealed fingerprints in a separate read-only
audit. Case 001 completed its report and final assessment; case 003 failed its
third report section because a malformed citation remained after correction.

The attempt also exposed a profile-generation defect: the benchmark's requested
`Reader` type was not in the hardcoded person list, so all three fictional people
were sent to an organization prompt. That prompt demanded long invented
institutional histories. Separate profile JSON handling also allowed truncated
or missing content to become replacement profiles. Consequently **neither run
satisfies the customer-response reliability gate**, even though one completed.

Usage: **40 model calls, 145,854 accounted tokens**, 712.6 summed wall seconds.
Monetary cost is unknown. Failed calls and conservative reservations are retained.

The next source revision addresses neutral handling of unknown actor types,
concise strict profile generation, and clearer exact citation tokens in the
report evidence prompt. It does not loosen evidence-reference validation.

[Machine-readable records and protocol](../backend/benchmarks/results/2026-10-03-reliability-development-v1/)
and [source-preservation audit](../backend/benchmarks/results/2026-10-03-reliability-development-v1/run-audit.json)
are retained permanently.

## Attempt 2 and diagnostic reproduction

Source commit `d6e8c1d` added strict profile generation and exact citation tokens.
Both old-case development trials failed: **8 calls and 10,329 tokens** in total,
51.9 summed wall seconds. Their public result records survived a session
interruption, but their temporary runtime logs did not. No stage-level diagnosis
or source-preservation audit is asserted for those lost runtime artifacts.

A fresh diagnostic run of case 001 on the same source then failed in profile
preparation. Both profile responses were valid JSON with `finish_reason=stop`,
but included extra identity keys such as `entity_name`, `entity_type`, and `role`.
The validator correctly rejected those keys. The prompt asked the model to
retain the entity's identity without clearly saying to put it inside `bio` and
`persona`, rather than adding fields. This reproduction identifies a concrete
prompt/schema mismatch; it does not recover the lost logs from attempt 2.

The diagnostic run used **4 calls and 6,780 tokens**, 47.4 wall seconds. Its raw
response-only diagnostics are private; [the result and frozen protocol](../backend/benchmarks/results/2026-10-03-reliability-diagnostic-v1/)
are public. New runtime logs use durable ignored project storage rather than
temporary directories. Neither attempt is a holdout result.

Separate isolated profile probes confirmed the same extra-key defect: two targets
failed before the prompt correction (four calls), and one target passed on the
first call afterward. All five probes used **7,044 tokens**, with unknown monetary
cost. The strict validator is unchanged. The revised prompt defines the exact
allowed keys, places identity inside profile text, distinguishes nullable
demographics from the topic array, and separates the target's traits from those
of related entities. [Compact probe diagnostics](../backend/benchmarks/results/2026-10-03-reliability-diagnostic-v1/profile-probes.json)
retain fields, finish reasons, and usage without raw response prose.

## Attempt 3

Source commit `e22a30875736f335bbaa68da8cd9206c1addf6de` corrected the profile
prompt/schema mismatch. Frozen protocol
`9bcec30a78d512223204a5697492cbd455c3bee31f2a64d7565fd890771e89f7`
again ran one MiroFish execution each on old cases `upworthy-001` and
`upworthy-003`, with the same model, per-trial limits, and two concurrent trials.
**Both completed ontology, graph extraction, profile preparation, simulation,
memory ingestion, report generation, and final assessment.** No baseline or
outcome scoring was performed.

A separate read-only audit verified each result against its saved assessment,
model/configuration proof, and frozen source commit. Both source graphs still
matched their sealed fingerprints; simulation evidence and completed reports
were bound to their respective executions. Saved report inputs and evidence
hashes matched. Each usage record agreed with both its proof and the SQLite
budget ledger. The audit read temporary copies of the stopped databases and WAL
files; all original runtime file contents and inventory remained unchanged.
These checks establish artifact consistency, not factual or predictive accuracy.
This attempt predates the new immutable batch/start markers; no missing metadata
was reconstructed. Its historical source hash covers application and runner
Python files, without attesting the then-installed vendor package.

Usage: **37 calls, 148,243 accounted tokens** (112,891 input and 35,352 output),
**295.685 summed wall seconds**. Monetary cost remains unknown. With two trials
running concurrently, summed wall time is not batch duration or isolated latency.

The successful executions exposed another issue at export: all six Reddit
profiles acquired unsupported defaults of age 30, gender `other`, MBTI `ISTJ`,
and country `中国`, despite the source providing no such demographics. Strict
profile generation now works, but the serializer introduced these assumptions
before OASIS built the agents' prompts. **This attempt therefore does not pass
the customer-response reliability gate.** Preserving unknown demographic values
through export and prompt construction, followed by a fresh full-pipeline gate,
remains necessary before the holdout comparison.

[Aggregated trial records](../backend/benchmarks/results/2026-10-03-reliability-development-v3/predictions.json)
and the [read-only integrity audit](../backend/benchmarks/results/2026-10-03-reliability-development-v3/run-audit.json)
are retained separately from earlier attempts and the holdout.
