# Persistent, frozen benchmark batches

`backend/benchmarks/run_batch.py` freezes the existing single-trial runner into a
fixed schedule and starts at most two fresh simulation Python processes at once.
It never accepts or opens an outcomes file. The application and trial-runner
source inventory, both orchestration scripts, reporting plan, inputs, schedule,
model identity, context token limit, prompts, and resource limits are frozen
before generation. The shared inventory includes vendored OASIS Python and SQL,
its package metadata, backend dependency declarations and lock, and locale JSON.
Frozen-source mismatches prevent subsequent trials from starting. Every trial
also verifies the installed OASIS distribution against the vendor's version and
exact Python/SQL files; a mismatch fails that trial before inference.

The development schedule uses old pilot cases `upworthy-001` and `upworthy-003`,
one repeat each, MiroFish only. It is a reliability check, not a new comparison.
The holdout schedule explicitly includes all 20 fresh cases, both methods, and
two repeats: 80 trials. Method order reverses on the second repeat. The protocol
retains the common two-method scoring contract even for development batches;
do not score development-only runs as complete comparisons.

Freeze a **new** development batch after all source changes are finished:

```sh
backend/.venv/bin/python backend/benchmarks/run_batch.py freeze \
  --inputs backend/benchmarks/data/upworthy_inputs.json \
  --results-dir backend/benchmarks/results/NEW-development-batch \
  --development
```

Freeze a **new** holdout batch only after the reporting plan and implementation
are ready, before releasing its observed outcomes:

```sh
backend/.venv/bin/python backend/benchmarks/run_batch.py freeze \
  --inputs backend/benchmarks/data/holdout-2026-10-03/upworthy_inputs.json \
  --results-dir backend/benchmarks/results/NEW-holdout-batch
```

The default private runtime is
`backend/uploads/benchmark-runtimes/<results-directory-name>`. It persists
across terminal sessions, is ignored by Git, and has directory mode `0700`.
`--runtime-root` can select another new batch directory under that same private
parent. Existing results or runtime directories cannot be reused by `freeze`.

Generation makes provider calls and consumes the configured account allowance:

```sh
backend/.venv/bin/python backend/benchmarks/run_batch.py run \
  --results-dir backend/benchmarks/results/NEW-holdout-batch \
  --python backend/.venv-simulation/bin/python
```

The supplied virtual-environment Python path is preserved without following its
executable symlink. Each child uses the single-trial runner's isolated graph,
jobs, budgets, simulations, and reports. The existing 35-call and 900-second
limits still apply; the supervisor allows another 60 seconds for cleanup before
terminating its trial process and descendant process groups.

Read compact progress without changing any files:

```sh
backend/.venv/bin/python backend/benchmarks/run_batch.py status \
  --results-dir backend/benchmarks/results/NEW-holdout-batch
```

Running the same `run` command after interruption starts **only untouched
slots**. A permanent private start marker is written before launching a child.
Completed, failed, malformed, and started-but-incomplete trials are never
replayed or replaced. A runtime directory without a start marker also counts as
already started. The supervisor passes its filesystem lock to its children so
a new supervisor cannot overlap trials surviving an interrupted session.

`SIGINT` or `SIGTERM` stops further dispatch and lets active trials finish within
their bounded cleanup allowance. If code, helper scripts, or the reporting plan
change during generation, the supervisor stops dispatch and records a permanent
freeze violation. That batch cannot resume; use a new batch and retain the old
evidence. Changes made and reverted between checks cannot be detected, so keep
the frozen checkout unchanged while a batch runs.

Frozen JSON and individual trial records are immutable. `status.json` and
`predictions.json` are explicitly derived progress snapshots and are atomically
updated. Only validated terminal trial records enter the combined predictions.
Failed records retain their original accounted calls and tokens, with unknown
pricing left null. An incomplete or invalid terminal record is omitted from
predictions and reported in status; its usage is unknown, never filled with
zero. Its private ledger remains available for investigation. The scorer's
missing-record failure sensitivity still applies.

Audit before releasing outcomes or scoring:

```sh
backend/.venv/bin/python backend/benchmarks/audit_batch.py \
  --results-dir backend/benchmarks/results/NEW-holdout-batch \
  --output backend/benchmarks/results/NEW-holdout-batch/run-audit.json
```

The audit reads saved proofs and SQLite data without initializing an application
client or contacting the provider. Review its unavailable and failed checks;
terminal counts alone do not establish a valid comparison. Keep the entire
private runtime locally. Publish only compact audit/results artifacts; do not
copy runtime databases, logs, source documents, generated reports, or private
paths into the public results directory.

After all scheduled trials are terminal and the audit has been reviewed, release
the separately retained outcomes with the dataset preparation helper and follow
the scoring instructions in [comparative-benchmark.md](comparative-benchmark.md).
Frozen `inputs.json` is a JSON serialization of the same canonical input data;
for scoring, use the original dataset input file when its exact byte hash is
bound to the outcomes manifest.
