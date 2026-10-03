# Fresh product/customer-response holdout — October 3, 2026

The repaired MiroFish pipeline completed **38 of 40 simulation trials**, while the
single-model baseline completed **40 of 40**. This historical headline replay
**does not demonstrate a predictive advantage for MiroFish**. Its primary paired
Brier improvement was 0.0336, with a 95% bootstrap interval from **−0.0729 to
+0.1372**. The interval includes both improvement and harm. MiroFish used **60.4×
as many accounted tokens** as the baseline. Neither method beat the neutral
0.5-probability reference on Brier score or log loss in the complete paired cohort.

## Design and integrity

Twenty fresh historical Upworthy headline experiments were selected
using metadata before their outcomes were released. All twenty earlier pilot and
reserve experiments were excluded. Each method ran twice on each case: **80
scheduled trials**, with at most two running concurrently. Repeats were averaged
within cases; the uncertainty calculation uses cases, not 80 executions, as
its resampling units. Both methods received the same headline/source packet and
three explicitly fictional reader assumptions. Images were unavailable to both.

Both used `gpt-oss:20b-cloud` through the signed-in local Ollama daemon's OpenAI
compatible endpoint, with context token limit 32,768. Each trial had a 35-call
budget and 900-second deadline. The MiroFish configuration used one Reddit round
and requested a compact three-section report. Of the 38 completed reports, 30
had three sections and eight had four; the requested section count was not
enforced exactly. This tests this configuration, not every
possible model, audience, or simulation design.

Source was frozen at commit `4ee691b` (same inference source as `3643e12`), with
implementation hash `1ae39db576905cac8e5e1c2dc56ce71c7c16ed27675f6304076b64e68759141b`.
Protocol hash:
`b9e3d81ae2a9cea569d0b8000dc4d05148f60e7a177e7c80aff251d3c4cba2a0`.
The [reporting plan](benchmark-holdout-data-2026-10-03.md), source, dependency assets,
installed OASIS runtime, helpers, input data, model settings, and trial schedule
were bound before generation. No implementation tuning or replay occurred during
the batch. Outcomes were released only after all 80 terminal records existed and
the independent artifact audit passed.

Generation ran from 19:42 to 20:41 UTC, about 59 minutes including freeze/launch
setup. All 80 scheduled records are present: 78 valid predictions and two explicit
failures, with no missing or invalid artifacts and no freeze violation. The
[audit](../backend/benchmarks/results/2026-10-03-upworthy-holdout-v1/run-audit.json)
passed all 80 record checks, including usage ledgers and the source/execution/report
bindings applicable to each trial. All 40 original simulation source graphs
remained unchanged.

## Quality results

Primary quality metrics below use the **same 18 complete paired cases**. Completion
and compute totals in the next table include all 40 scheduled trials per method.
Lower Brier score and log loss are better. Directional accuracy gives half credit
when an averaged probability is exactly 0.5.

| Method | Brier score | Log loss | Directional accuracy |
| --- | ---: | ---: | ---: |
| Single model | 0.3404 | 0.8851 | 30.6% |
| MiroFish | 0.3068 | 0.8298 | 50.0% |
| Neutral probability 0.5 | 0.2500 | 0.6931 | 50.0% |

The primary difference is baseline loss minus MiroFish loss, so positive favors
MiroFish. Its mean was **+0.0336 Brier**, 95% case-bootstrap interval
**[−0.0729, +0.1372]**, using the frozen 5,000 resamples and seed 20261003. The
observed gain is inconclusive. The directional-accuracy improvement interval also
crossed zero (−2.8 to +41.7 percentage points).

Complete-pair results omit the two cases with a failed repeat. The predeclared
full-cohort sensitivity replaces each failed repeat with a probability entirely
against its observed label before averaging the two repeats. On all **20 cases**,
this gives:

| Method | Failure-adjusted Brier | Failure-adjusted log loss | Directional accuracy |
| --- | ---: | ---: | ---: |
| Single model | 0.3150 | 0.8309 | 37.5% |
| MiroFish | 0.3344 | 0.8918 | 45.0% |

The failure-adjusted Brier difference is **−0.0194**, interval **[−0.1405, +0.0968]**.
These are worst-case missing-repeat imputations, not observed replacement
predictions or a bound on relative advantage. They demonstrate why selective
completion cannot be ignored. The baseline's all-20-case metrics must not be
compared directly with MiroFish's 18-case complete metrics.

## Resource use and failures

| Method | Valid / scheduled | Model calls | Accounted tokens | Summed trial wall time |
| --- | ---: | ---: | ---: | ---: |
| Single model | 40 / 40 | 40 | 39,611 | 232.8 s |
| MiroFish | 38 / 40 | 748 | 2,392,977 | 6,824.5 s |

MiroFish used **18.7× the calls and 60.4× the tokens**. Summed trial wall time was
29.3× higher; concurrent execution means these sums are not batch duration or
isolated latency measurements. Monetary pricing was unavailable, so cost remains
null. The complete holdout consumed **788 calls and 2,432,588 tokens**, including
both failures. [Development costs](reliability-development-2026-10-03.md) are
reported separately and are not pooled with these results.

Both failures occurred on the second repeat, in report section 3:

- Case 004: malformed grouping of otherwise registered citation IDs; the one
  repair attempt also failed citation validation. Usage: 19 calls, 61,205 tokens.
- Case 019: malformed grouping of registered citation IDs; the one repair attempt
  returned invalid JSON. Usage: 18 calls, 53,305 tokens.

Earlier stages, including simulation and memory ingestion, completed in both.
The guard rejected the report, withheld the final assessor call, and retained a
null prediction. These are report-formatting failures, not call-budget overruns.
Citation syntax checks establish reference integrity, not semantic truth.

The [profile export audit](../backend/benchmarks/results/2026-10-03-upworthy-holdout-v1/profile-audit.json)
found the expected three identities in all 40 exports; all **480 demographic
fields across 120 profiles** were present and null. All profiles had nonempty bio
and persona text and a fictional marker in at least one of those fields. These
structural/keyword checks do not establish semantic faithfulness or absence of
all invented traits.

## Product decision

Use this fork as an **exploratory research workflow with explicitly synthetic
participants**. Keep the inexpensive single-model method as a comparison control.
This study does not support marketing either method as a validated customer-demand
forecaster or treating model probabilities as calibrated purchase estimates.

The next priorities are:

1. Render citations deterministically from validated evidence IDs, reducing the
   remaining dependence on model-written citation syntax and enforcing the requested
   report structure, while keeping rejection
   of unknown or unsupported references.
2. Evaluate whether simulations produce useful, source-supported questions or
   objections for human researchers, alongside their extra compute cost.
3. Run a prospective, blinded product/customer-response study against actual user
   behavior and the simple baseline, with a frozen decision rule and adequate
   sample size before making predictive claims.

The 20 historical cases are a small sample; **12 observed CTR-difference intervals
include zero**, although the frozen scoring rule retains them. Public historical
material may have been memorized during model training. Missing images, noisy
observed winners, fixed synthetic personas, and the headline proxy all limit
transfer to a new product or audience. Related stories or audiences can also
make cases dependent. No prospective forecasting claim follows
from this replay.

## Reproducible evidence

- [Frozen protocol and batch artifacts](../backend/benchmarks/results/2026-10-03-upworthy-holdout-v1/)
- [Predictions, including failed trials](../backend/benchmarks/results/2026-10-03-upworthy-holdout-v1/predictions.json)
- [Full metrics and uncertainty](../backend/benchmarks/results/2026-10-03-upworthy-holdout-v1/scores.json)
- [Independent arithmetic audit](../backend/benchmarks/results/2026-10-03-upworthy-holdout-v1/independent-score-audit.json)
- [Released outcomes and original input/selection artifacts](../backend/benchmarks/data/holdout-2026-10-03/)
- [Persistent runner and audit instructions](benchmark-batch-runner.md)

Implementation validation before generation: **950 backend tests passed**, two
optional-runtime skips locally; **30 root tests passed**; all five CI jobs passed,
including full simulation environments on Python 3.11 and 3.12. The separately
implemented arithmetic checker verifies aggregation, bootstrap intervals, failure
sensitivity, usage, and artifact bindings; it does not import the primary scorer.
