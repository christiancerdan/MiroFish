# MiroFish audience-response pilot — 2026-10-02

**The current configuration did not demonstrate useful prediction performance.** MiroFish completed none of its 12 trials. The single-model baseline completed all 12, but its mean predictions identified only two of six observed winners and scored worse than a fixed 50/50 prediction. These results justify improving reliability and running a larger held-out evaluation; they do not establish that either method can predict customer demand.

## What was tested

Six real Upworthy headline A/B experiments, two repeats per method, using `gpt-oss:20b-cloud` through the configured local Ollama daemon. Both methods received identical headline material and three explicitly fictional reader personas. MiroFish additionally performed ontology generation, graph extraction, agent preparation, one Reddit round, memory ingestion, and—if preceding stages succeeded—report generation and final assessment. Graph memory stayed local.

Inputs and prompts were frozen before generation. The corrected protocol and all 24 prediction records are retained in [the result directory](../backend/benchmarks/results/2026-10-02-upworthy-pilot-v2/). Its tested source is commit `3e3edb8`; all trial source hashes and model identities match. The data, correction exclusions, CC BY 4.0 attribution, and selection procedure are described in [benchmark-data.md](benchmark-data.md). Generation ran on October 2 Pacific time / October 3 UTC.

## Recorded results

| Measure | Single model | MiroFish |
|---|---:|---:|
| Valid final predictions | 12 / 12 | 0 / 12 |
| Cases with both repeats complete | 6 / 6 | 0 / 6 |
| Correct observed winner, after averaging repeats | 2 / 6 | Unavailable |
| Brier score, lower is better | 0.2968 | Unavailable |
| Log loss, lower is better | 0.7930 | Unavailable |
| Model calls, including failures | 12 | 62 |
| Accounted tokens, including failures | 11,019 | 276,574 |
| Mean elapsed time per trial | 4.1 seconds | 112.1 seconds |
| Monetary cost | Unknown | Unknown |

The fixed 50/50 reference has Brier score **0.25** and log loss **0.6931** on these cases. There are **zero complete paired cases**, so a measured MiroFish-versus-baseline accuracy difference or paired confidence interval is unavailable. The scorer also exports a worst-case failure penalty; that is an operational sensitivity calculation, not a MiroFish forecast score.

Times include failures and were observed with up to two concurrent trials; they are not controlled latency measurements. Token accounting can include conservative reservations for unsuccessful provider calls. No model pricing was configured, so no dollar saving or cost claim is made.

| Case | Observed higher CTR | Baseline mean P(A) | Baseline direction correct? | CTR difference interval includes zero? |
|---|---|---:|---|---|
| upworthy-001 | A | 0.475 | No | Yes |
| upworthy-002 | B | 0.675 | No | Yes |
| upworthy-003 | B | 0.525 | No | No |
| upworthy-004 | A | 0.575 | Yes | Yes |
| upworthy-005 | A | 0.740 | Yes | No |
| upworthy-006 | B | 0.725 | No | No |

All MiroFish predictions are unavailable; no failed trial was replaced with an invented probability or silently dropped.

## Failure diagnosis and isolation verification

The 12 MiroFish failures comprise **five ontology-generation failures, four document-extraction failures, and three simulation-memory validation failures**. Recorded examples include truncated or malformed model JSON, entity types outside the generated ontology, and empty entity summaries or relationship facts. Invalid writes failed closed; those trials never reached final assessment.

Three trials created execution snapshots and ran the simulation stage. A separate read-only audit recomputed their original graph fingerprints, including entities, edges, source episodes, and ingestion receipts. **All three original graphs still matched their sealed snapshots.** This verifies source preservation in those executions; the regression suite separately covers repeated starts, concurrent commands, report binding, and stale requests.

The first attempted protocol was stopped after discovering a harness defect: a hardcoded `Reader` filter excluded correctly generated `CuriousReader`, `SkepticalReader`, and `BusyReader` participants. The correction selects the actual labels for the exact three source personas. It changed no source text or model prompt. Outcomes were not scored or consulted to make that correction, and every trial—including the baseline—was rerun under the new protocol.

The [aborted attempt](../backend/benchmarks/results/2026-10-02-upworthy-pilot/attempt-status.json) retains 18 records, including two interrupted trials reconstructed from their conservative budget ledgers, plus six unstarted slots. It consumed 28 accounted calls and 133,699 tokens. Its performance comparison is invalid and is not pooled with the corrected pilot. Across both attempts, the recorded total is **102 calls and 421,292 tokens**, with unknown monetary cost.

## Product decision and next work

Use this fork as an experimental research tool while its predictive value remains unproven. The baseline is operationally simpler, but this pilot does not support using its probabilities for product go/no-go decisions either.

The next engineering priority is structured-output reliability: reduce the fixed ontology complexity for small inputs, validate model output against the requested schema, and use bounded feedback/repair with visible failures and budgets. Those are proposed follow-up changes, not changes made after seeing these pilot outcomes. Keep the current invalid-write protections.

After the workflow is reliable, freeze a new model/configuration comparison and a larger holdout before inspecting outcomes. Product-demand validation will also need real purchase, signup, or retention evidence; headline clicks are a narrow message-response proxy. Three of these six observed CTR differences have 95% intervals spanning zero, the images are unavailable, and public historical data may have appeared in model training. Two repeats do not create additional independent human experiments. The pilot cannot establish general superiority, infer calibration, or prove that MiroFish is useless in every setting.

Machine-readable [scores](../backend/benchmarks/results/2026-10-02-upworthy-pilot-v2/scores.json), [predictions](../backend/benchmarks/results/2026-10-02-upworthy-pilot-v2/predictions.json), and [run audit](../backend/benchmarks/results/2026-10-02-upworthy-pilot-v2/run-audit.json) accompany this report. [Reproduction and scoring instructions](comparative-benchmark.md) describe the frozen contract.
