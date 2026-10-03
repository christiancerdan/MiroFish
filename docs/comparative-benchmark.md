# Comparing MiroFish with a single-model analysis

This benchmark asks whether the additional MiroFish pipeline improves an audience-response prediction enough to justify its extra work. It uses real historical headline A/B outcomes and keeps the observed results separate from model inputs. See [the dataset documentation](benchmark-data.md) for selection, attribution, correction exclusions, and sampling uncertainty.

This is a retrospective replay. Model training may include the public headlines or their results. A correct prediction of observed CTR ordering does not establish the true population preference, calibration, product demand, or prospective forecasting ability.

The [first recorded pilot](benchmark-pilot-2026-10-02.md) found reliability failures in all 12 MiroFish trials and a baseline Brier score worse than the fixed 50/50 reference. It includes the aborted harness attempt, the corrected protocol, every trial, and a separate source-preservation audit.

## Methods

Both methods receive the same headlines, audience description, unavailable-image disclosure, and three explicitly fictional reader personas. They use the same configured base model; the runner disables the application's optional boost model.

- `single_model`: one structured prediction request using the shared source material.
- `mirofish`: ontology generation, document graph extraction, agent preparation, one Reddit round, execution-memory ingestion, report generation, and one structured prediction request using the source plus the report.

The three personas are assumptions, not a representative audience sample. The MiroFish trial fixes their first-round activity and seed posts so the bounded simulation actually exercises agent decisions. Every override is saved. Simulation adds generated reasoning and discussion, not additional observed customer data.

The final assessor uses the same prompt, temperature, output limit, and strict schema in both methods. Invalid or incomplete predictions fail; the runner does not repair or retry them. Internal application calls and report repairs count toward the MiroFish trial's usage.

## Freeze before generation

`backend/app/services/comparative_benchmark.py` is a standard-library-only module. Its `build_protocol` function validates inputs and freezes the case list, repeat count, aggregation, tie and failure policies, bootstrap seed, model metadata, and runner configuration. Import `backend/scripts/run_comparative_benchmark.py` with `importlib.util` to obtain `benchmark_configuration()`; its prompt hashes and limits must match the protocol exactly.

The committed pilot protocol is in `backend/benchmarks/results/2026-10-02-upworthy-pilot-v2/protocol.json`. Reproducing it requires its specified model and runner configuration. A different model, prompt, source, or resource limit requires a new protocol; do not overwrite an existing result.

The example below assumes the recorded source checkout, commit `3e3edb8`, with its dependencies installed. Later revisions also reject a mismatched `model.implementation_source_sha256` before making provider calls. To benchmark the current revision, build a fresh protocol with its source inventory hash; do not reuse the historical protocol against changed code. Scoring saved predictions works independently of the generation checkout.

Each live invocation runs one trial in a fresh Python process and empty data directory:

```sh
backend/.venv-simulation/bin/python backend/scripts/run_comparative_benchmark.py \
  --inputs backend/benchmarks/data/upworthy_inputs.json \
  --protocol backend/benchmarks/results/2026-10-02-upworthy-pilot-v2/protocol.json \
  --case-id upworthy-001 --method single_model --repeat 1 \
  --output /tmp/my-benchmark/single-001-1.json \
  --runtime-dir /tmp/my-benchmark/runtime-single-001-1
```

Use `--method mirofish` for the corresponding pipeline trial. These are opt-in provider calls and can consume an account allowance. The frozen configuration caps each trial at 35 calls and 900 seconds. Each trial uses local graph memory and its own budgets, jobs, projects, profiles, and simulation files. The generation runner has no outcomes-file argument and only admits approved source fields into prompts.

Runtime artifacts include the exact source document, prompts, source-code hashes, model identity, activity overrides, report, actions, execution identity, logs, and usage. Keep them for investigation; published predictions omit credentials and private runtime paths. The result records elapsed wall time and the budget ledger's accounted tokens, including conservative reservations for unsuccessful provider calls. Monetary cost stays null when model pricing is unknown.

## Score after all scheduled trials

Combine each trial's `records` into one envelope with the same schema version and protocol hash. Missing scheduled records count as failures. Score locally, without loading Flask or contacting a provider:

```sh
python -S backend/scripts/score_comparative_benchmark.py \
  backend/benchmarks/data/upworthy_inputs.json \
  backend/benchmarks/data/upworthy_outcomes.json \
  backend/benchmarks/results/2026-10-02-upworthy-pilot-v2/protocol.json \
  /tmp/my-benchmark/predictions.json \
  --output /tmp/my-benchmark/scores.json
```

The scorer averages repeat probabilities within each case and weights cases equally. It reports Brier score, log loss, directional accuracy, a fixed 0.5 comparator, and paired differences with a case-level bootstrap. Positive paired loss improvement favors MiroFish. Exact observed CTR ties are excluded under the frozen policy.

Complete-pair metrics describe only cases with every required repeat for both methods. The full scheduled cohort also gets a conservative failure sensitivity that assigns worst-case losses to missing or failed repeats; it is an operational penalty, not a model probability estimate. Always inspect both views alongside completion rates. Per-method successful subsets may contain different cases and should not be compared as if they were paired.

Six cases support a pilot diagnosis, not a general validity claim. Two repeats are still six experimental cases, not twelve independent human experiments. The pilot's observed wall times were collected with up to two concurrent trials and are not controlled latency measurements. Any larger test should freeze a new protocol before examining its outcomes; tuning on these cases makes subsequent results development results.
