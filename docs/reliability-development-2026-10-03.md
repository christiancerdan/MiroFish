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
