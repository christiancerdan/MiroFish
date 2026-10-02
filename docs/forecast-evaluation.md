# Historical forecast evaluation

Generated MiroFish scenarios are **unvalidated scenario analyses**, not calibrated probabilities. The scorer evaluates an explicitly supplied dataset of dated binary forecasts and outcomes. The included sample is entirely synthetic; its scores demonstrate software behavior and provide no empirical accuracy claim.

From the repository root, using only Python's standard library:

```sh
python3 backend/scripts/evaluate_forecasts.py docs/examples/synthetic-forecasts.json --output /tmp/mirofish-synthetic-evaluation.json --seed 17
```

The CLI emits JSON to stdout, or to `--output`. Invalid data emits a JSON error to stderr and exits with code 2. Options are `--bins` (1–100, default 10), `--bootstrap-samples` (0–100000, default 1000), and `--seed` (default 0). Run the sample with `python3 -S` to verify it needs no Flask, model provider, or installed packages.

## Dataset contract

See [the synthetic example](examples/synthetic-forecasts.json) for a complete input. Version 1 requires:

| Field | Required contents |
| --- | --- |
| `version`, `synthetic`, `evaluated_at` | `1`, explicit boolean, evaluation timestamp |
| `baseline` | Fixed `probability`, human-readable `label`, and `declared_at` timestamp |
| `evidence` | Unique `id`, `published_at`, and `available_at` for every record; include text/URI/hash for auditing |
| `forecasts` | Unique `forecast_id`, `case_id`, `run_id`, `issued_at`, `as_of`, `probability`, explicit `evidence_ids` list |
| `outcomes` | Unique `case_id`, integer `outcome` 0 or 1, and `resolved_at`; retain the prespecified binary question/resolution rule with the record |

All timestamps must include time and timezone, for example `2025-02-01T00:00:00Z`. Missing and date-only values are rejected; the scorer never guesses a date. Probabilities must be finite numeric values in `[0,1]`; boolean/string values are rejected.

The audit checks every cited source against the forecast's cutoff:

```
published_at <= available_at <= as_of <= issued_at < resolved_at <= evaluated_at
baseline.declared_at <= as_of
```

A document published earlier but first available later is excluded at the earlier cutoff. A missing source/outcome, duplicate ID or case/run pair, invalid outcome, future source, or forecast issued after resolution fails the whole evaluation. Every outcome must have a forecast. Repeated runs of a case must share one `as_of` cutoff, so measurements from different information sets are not blended as repeats. An explicitly empty `evidence_ids` list is permitted and visible in the input; no evidence is silently inferred.

These checks validate the submitted record contract. They cannot independently authenticate dates, detect undeclared sources, detect outcome information memorized by a model, or establish that the outcomes were adjudicated fairly. For real backtesting, preserve immutable input snapshots, use held-out cases with fixed resolution rules, freeze the baseline and prompts before outcomes, and record all attempted cases, seeds, and model versions. Dataset hashes in results support auditing the exact submitted records; they do not establish authenticity.

## Scores and uncertainty

Each independent case receives equal weight. For a case with repeated runs, the scorer first averages its submitted probabilities. It scores that mean as an ensemble forecast; adding repeated runs does not increase the reported number of independent cases. Within each case, `run_count`, mean, population standard deviation, minimum, and maximum describe run variability.

The Brier score is mean `(probability - outcome)^2`. Log loss is mean negative log probability assigned to the observed outcome; probabilities are clipped to `[1e-15, 1-1e-15]` for a finite, JSON-safe result, and the clipping constant is recorded. Lower scores are better. The fixed, explicitly supplied baseline receives the same cases and scoring rules. `improvement_over_baseline` is baseline loss minus submitted loss. A fixed 50% baseline is a comparator, not an estimated event prior.

Calibration bins report case count, mean probability and observed event frequency. Empty bins have null means. Bins include the lower boundary and exclude the upper boundary except the last, which includes 1. Sparse calibration bins do not establish calibration.

The optional deterministic bootstrap resamples independent cases, not individual repeated runs, and returns 95% percentile intervals for mean Brier score and log loss. With fewer than two cases or zero resamples, intervals are null. The independent-case assumption may be inappropriate for correlated events; these descriptive intervals are not confidence in an individual future outcome. Run variability and sampling variability are both distinct from model calibration.

Every result records its input hash, scoring method, counts, baseline, limitations, and `forecast_accuracy_claim: false`. This harness does not automatically relabel reports as calibrated and does not convert scenario frequencies into event probabilities.

## Report provenance

New report metadata includes `evidence`, `citation_validation`, `uncertainty`, and `manifest`. Reports persist `evidence.json` and `manifest.json` beside `meta.json` and Markdown. Evidence snapshots preserve text, source ID, graph ID, SHA-256, source timestamps when available, metadata, and a report-scoped read URL. Citation IDs are assigned in application code from graph/source identity and content. The model cannot register its own sources. A changed source text produces a different citation ID.

Model-planned outlines must have two to five distinct, nonempty section titles and nonempty report title/summary. Invalid structures use a fixed three-section outline covering assumptions/source evidence, observed simulation behavior, and uncertainty, without another planning call. The parsed planning response is logged, and `manifest.outline_validation` records whether a fallback was used.

Report evidence kinds are:

- `source_fact`: **Source evidence**—what an input document states, not a guarantee of external truth.
- `simulation_observation`: Stored simulation output or a tool observation; tool-derived summaries explicitly carry `metadata.derived: true`.
- `assumption`: User-provided scenario assumptions.
- `unclassified`: Source origin was not established; it is not promoted to a fact.

The model receives allowed IDs and emits `[[source:ID]]`. Unknown/malformed IDs and uncited sections receive at most one structured correction request with stored source excerpts and application-issued IDs; persistent failures stop before section persistence. An empty generation response enters this same bounded repair immediately. The application never assigns a citation to an unsupported claim. Repair attempts and initial errors are recorded in the manifest and logs, and the corrected text passes the original validator. Valid tokens render as Markdown source-register links. The registry retains sources separately from derived text. The API `GET /api/evidence/<report_id>/<citation_id>` returns the stored source only if it belongs to that report and its text hash matches. Links survive later graph changes or deletion; deleting the report removes its snapshot.

On an explicit retry, prior report files are archived under `attempt_history/<attempt-id>` before the new attempt begins. Current APIs and source URLs refer to the current attempt; archived files remain available for local audit. Assembly reads only the current outline’s section indexes and validates the assembled references before publishing the final Markdown. Durable report writes hold the job ownership barrier, so an expired or replaced worker cannot overwrite the current attempt.

`citation_validation.scope` is `reference_integrity_only`: it verifies a citation identifies a stored source. It does **not** verify that a claim is entailed, that a quote is verbatim, or that an external claim is true. Human review or a separate semantic evaluation remains necessary. The report's uncertainty block states these limits in metadata and exported Markdown.

The manifest records model, report generation settings, input file hashes, source timestamps, report LLM/tool call counts, elapsed time, and the bound cumulative run budget snapshot when available. Missing inputs are listed; absent source timestamps remain null. Legacy reports load with an unvalidated uncertainty label and no invented provenance. Providers that cannot expose source episodes report evidence as unavailable; tool snapshots may still be retained as derived observations.
