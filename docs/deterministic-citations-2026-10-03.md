# Deterministic report citations — October 3, 2026

The historical holdout exposed two reports whose otherwise registered evidence
IDs were combined into malformed model-written citation syntax. New report
sections now return paragraphs with plain text and separate evidence IDs.
Application code checks the entire envelope before rendering source links.
Unknown IDs, invalid schemas and embedded citation markup fail closed, with at
most one model correction request per section. No source is guessed or attached
automatically. Legacy saved reports and report chat retain their existing
citation validation.

Report manifests record the paragraph schema and renderer versions, both 1,
and the output-instruction hash. Only validated Markdown reaches the section
publication event. These checks establish reference integrity; they do not
establish semantic support, factual truth or forecast accuracy. See the
[provenance contract](forecast-evaluation.md#report-provenance).

## Bounded live check

Source `adba5ce5e9a0aab9a78f939b4d8f5c90ef464ce3` was frozen before two
old-pilot MiroFish executions, cases `upworthy-001` and `upworthy-003`, using
`gpt-oss:20b-cloud` through the signed-in local Ollama gateway. Each trial has a
35-call budget and a 900-second deadline; at most two run concurrently.

The [frozen development artifacts](../backend/benchmarks/results/2026-10-03-deterministic-citations-development-v1/)
are separate from the historical holdout. No baseline, outcome scoring or new
accuracy estimate is part of this check. Failed attempts remain in the results.

**Both executions completed.** Each report used schema 1 / renderer 1, produced
three nonempty sections and passed citation validation with **zero citation
repairs**. The [artifact audit](../backend/benchmarks/results/2026-10-03-deterministic-citations-development-v1/run-audit.json)
passed both trials, including source-graph preservation, execution/report
bindings and usage-ledger agreement. The
[structured-report audit](../backend/benchmarks/results/2026-10-03-deterministic-citations-development-v1/citation-audit.json)
records the renderer and citation checks. Case 001's outline response was invalid
JSON and used the existing deterministic three-section fallback; case 003's
outline was valid. The section citation change does not eliminate every source
of model-formatting failure.

Usage was **36 calls and 108,608 accounted tokens** (79,714 input and 28,894 output),
with **367.787 summed trial seconds** and unknown monetary cost. Trials ran
concurrently, so summed trial time is not batch duration or isolated latency.
Protocol SHA256: `548d675ef646ef36a4a5cd3f7f02d7bfdf8ef2f26587508d53ac308401eca60b`.

The combined implementation passed **1,100 backend tests**, with two optional
simulation tests skipped in the API environment, plus **30 root tests**. Targeted
regressions cover strict schema and ID handling, atomic validation, tool/final
dispatch, bounded repair and termination, rendered publication events, and the
prospective kit's deadlines, frozen artifacts and scoring. These two successful
development cases do not establish a general report failure rate or predictive
benefit.

## Prospective work

The [customer-study kit](prospective-customer-study.md) prepares a future text
A/B study, accepts manually supplied forecasts before a fixed launch, seals all
scheduled successes and failures, and evaluates operator-attested aggregate
responses after the complete observation window. No customer experiment has
been launched by this change. Product details, exact variants, audience, event
definition and a justified sample/stopping plan are still required.

The earlier [80-trial holdout](benchmark-holdout-2026-10-03.md) remains unchanged
and its predictive-benefit result remains inconclusive.
