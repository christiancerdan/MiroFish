# Preparing a prospective customer-response study

The offline [study kit](../backend/benchmarks/prospective_study.py) prepares a
future-facing comparison of MiroFish and a single-model baseline against actual
customer behavior. It freezes a study, records **manually executed forecasts**,
seals every forecast before launch, and later scores operator-attested aggregate
observations. It does not call a provider, run MiroFish, recruit customers,
randomize traffic, launch an experiment, or collect customer events.

No real study has been launched with this kit. The actual product, audience,
channel, variants, response event, dates, and sample rationale must be supplied
before it can be used. The [example manifest](../backend/benchmarks/templates/prospective-study.example.json)
describes an invented product and deliberately cannot be frozen. The example's
sample count is an illustration, not a power calculation.

## The question being measured

For each separately identified A/B experiment, both methods estimate
`probability_a`: the probability that variant A will have a **higher observed
response fraction** than variant B over the complete fixed window. A response is
binary per eligible unique visitor: zero or one, even if the visitor performs the
event repeatedly. This probability is about the observed winner, not the
visitor's conversion probability, market demand, revenue, or a causal effect size.

The methods receive identical product/audience/variant/event packets and the same
explicitly fictional persona assumptions. Their processing differs according to
the frozen `method_configuration`. Both use the same declared provider and model;
record exact method settings, code revision, simulation rounds, budgets, and
assessment instructions in those configuration strings before freezing. Keep
synthetic assumptions visibly separate from facts about the intended audience.

Each repeat is a fresh forecast using the same packet and settings. Repeats are
averaged within an experiment; they are not independent customer experiments.
Decide the number of repeats in advance. Failed runs must be recorded as failed
slots, including the available usage, rather than retried until successful.

## Design the real study first

Copy the example outside versioned fixtures, preferably under ignored
`backend/uploads/prospective/`, and replace it with the actual design:

- A real `study_id`, `example_only: false`, product context, exact audience,
  channel, eligibility rule, and exclusions. `[]` means no exclusions.
- Unique `case_id` and actual event-system `experiment_id` for every independent
  experiment; exact distinct text variants and their deployed IDs.
- One response event ID and an operational definition, including attribution
  timing and deduplication. Hold other content and exposure conditions fixed.
- Persistent 50/50 assignment by **unique visitor**, with one arm per visitor.
  The event system must enforce assignment; the kit does not do it.
- A UTC forecast deadline strictly before launch, and an observation end after
  launch. Use full `YYYY-MM-DDTHH:MM:SSZ` timestamps. The stopping rule is a fixed
  window with no early stopping or extension based on results.
- A minimum number of eligible unique visitors in each arm and an honest
  sample-size rationale. Minimum counts alone do not establish statistical
  power. Specify a relevant effect size and expected baseline response when
  planning sample adequacy with a qualified analyst; no sample calculator or
  power claim is included here.

The operator is responsible for the customer-facing implementation, lawful data
collection, variant fidelity, and eligibility filtering. Arrange for the person
collecting outcomes to remain blind to method forecasts until collection ends.
Do not change the sample requirement, exclusions, window, or variants after
seeing forecasts or responses. If the experiment deviates, preserve its evidence
and treat it as a protocol deviation; do not make its inputs appear compliant.

## Freeze and generate forecasts manually

Commands below run from the repository root using Python's standard library;
`-S` demonstrates that no application or provider dependencies are loaded.

```sh
python3 -S backend/benchmarks/prospective_study.py validate backend/uploads/prospective/actual-study.json
python3 -S backend/benchmarks/prospective_study.py freeze backend/uploads/prospective/actual-study.json backend/uploads/prospective/run-001
```

`freeze` requires a new directory and creates `study.json`, `packets.json`, and
`freeze.json`. The freeze binds the exact file bytes and recording-tool revision.
Use that tool revision for the entire study. Newline or whitespace changes to a
frozen file are changes and will be rejected. The study directory and copied raw
evidence are private local files; no upload or publishing happens automatically.

Give each method the matching case packet from `packets.json` plus its frozen
method instructions. Execute the methods manually using fresh isolated runs and
record output, settings, failures, and usage. The existing Upworthy runner is a
historical-replay runner: **do not use it to execute this prospective study or
relabel its old results as prospective forecasts**. The kit adds no automated
MiroFish execution path.

For each case/method/repeat, save an import JSON with exactly these fields:

```json
{
  "case_id": "your-case-id",
  "method": "single_model",
  "repeat": 1,
  "status": "ok",
  "probability_a": 0.6,
  "error": null,
  "model": {"provider": "your-declared-provider", "name": "your-declared-model"},
  "study_sha256": "copy the study_sha256 from freeze.json",
  "packet_sha256": "calculate the digest of this case packet as described below",
  "generation_started_at": "2030-01-01T09:00:00Z",
  "generation_completed_at": "2030-01-01T09:01:00Z",
  "generated_for_this_study_before_observations": true,
  "usage": {"calls": 1, "input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "cost_usd": null},
  "elapsed_seconds": 60
}
```

The example above is illustrative, not an actual forecast. Use the real declared
model and timestamps. A failed run uses `"status": "failed"`,
`"probability_a": null`, and a nonempty `error`. Unknown usage fields or monetary
cost must be `null`, not invented zeroes. Record costs from all failed and
successful executions. An unstarted slot may be recorded as failed with an
explanation and zero known calls if that is what actually happened; seal all
slots before the deadline. Do not replace failed slots with later successes.

The packet digest hashes a case packet using the tool's exact JSON encoding. To
print the study and packet digests without changing any artifacts:

```sh
python3 -S - <<'PY'
import json, runpy
from pathlib import Path
kit = runpy.run_path("backend/benchmarks/prospective_study.py")
root = Path("backend/uploads/prospective/run-001")
print("study_sha256:", json.loads((root / "freeze.json").read_text())["study_sha256"])
for case_id, packet in json.loads((root / "packets.json").read_text()).items():
    print(case_id, kit["sha"](kit["encoded"](packet)))
PY
```

Import the forecast together with a raw output or failure-evidence file:

```sh
python3 -S backend/benchmarks/prospective_study.py record backend/uploads/prospective/run-001 backend/uploads/prospective/forecast.json backend/uploads/prospective/raw-output.txt
```

The evidence must be nonempty and at most 16 MiB. Avoid putting API keys or
customer identifiers in it. The tool copies and hashes it, stamps `recorded_at`
from the current local clock, and exclusively creates that slot. Imported
generation timestamps must fall after freeze and before recording; recording
must finish its evidence I/O before the forecast deadline. The import does not
accept a caller-supplied `recorded_at`. If an import is interrupted and leaves an
incomplete occupied slot, sealing fails; the CLI will not overwrite or replay it.
Preserve the failed run and start a new study if necessary before customer launch.

The raw evidence's contents, model/provider settings, method execution, generation
timestamps, and usage are **supplied and attested by the operator**. They are not
automatically verified against a provider runtime. Exact schema and hash checks
do not prove that a model used the supplied packet or that reported usage is true.

## Seal before the forecast deadline, then launch separately

```sh
python3 -S backend/benchmarks/prospective_study.py seal backend/uploads/prospective/run-001
```

Sealing requires every scheduled slot, successful or failed, and rejects extra
slots, missing evidence, modified artifacts, and late recording. The local seal
binds the freeze and every forecast/evidence file. It prevents further imports
through the CLI. Keep an independent read-only copy of the sealed directory if
you need an external review trail. A local clock plus editable files is **not
external preregistration or trusted timestamping**; the owner can alter their
machine. No claim of independently established timing follows from a hash.

Only after sealing, and at the declared launch time, should the operator enable
the actual customer experiment in their own event/assignment system. The kit
does not perform this action. Wait for the full observation window even if an
apparent winner or the minimum sample appears earlier.

## Import complete customer observations

After the full window, prepare one JSON object containing all experiment cases.
`study_sha256` is from `freeze.json`; `seal_sha256` is the SHA256 of the exact
`seal.json` file bytes. Counts must be unique eligible visitors and unique
responders after applying the frozen exclusions. Do not send individual customer
records to the kit.

```json
{
  "schema_version": 1,
  "study_sha256": "copy the frozen study digest",
  "seal_sha256": "SHA256 of the seal.json file bytes",
  "operator": "person responsible for the collection",
  "attestations": {
    "assignment_as_declared": true,
    "unique_visitors_disjoint_arms": true,
    "variants_and_event_as_declared": true,
    "exclusions_as_declared": true,
    "full_window_no_early_stopping": true,
    "counts_from_event_system": true
  },
  "cases": [{
    "case_id": "your-case-id",
    "experiment_id": "the-deployed-experiment-id",
    "variant_a_id": "the-deployed-a-id",
    "variant_b_id": "the-deployed-b-id",
    "event_id": "the-frozen-event-id",
    "window_started_at": "2030-01-02T12:00:00Z",
    "window_ended_at": "2030-01-09T12:00:00Z",
    "unique_visitors_a": 100,
    "unique_visitors_b": 100,
    "responders_a": 20,
    "responders_b": 10
  }]
}
```

Those counts are synthetic examples. Actual experiment IDs, variant IDs, event
IDs, and exact window timestamps must match the frozen design. Duplicate or
missing cases, impossible counts, partial windows, missing attestations, or an
arm below its declared minimum cause rejection. If a minimum was not reached,
retain the aggregate observations as insufficient evidence; do not extend the
window or reduce the frozen minimum after the fact.

```sh
python3 -S backend/benchmarks/prospective_study.py evaluate backend/uploads/prospective/run-001 backend/uploads/prospective/actual-observations.json
```

Evaluation rechecks all frozen and sealed bindings and writes new
`observations.json` and `evaluation.json` files. Existing evaluations cannot be
overwritten. Collected counts and correct experiment execution remain
operator-attested; the tool cannot detect bots, cross-device duplicates,
incorrect assignment, a different deployed page, or an early experiment that
the operator falsely reports as compliant.

## Interpret the output conservatively

The output shows response fractions, observed winners, completion, and accounted
usage including failures. Unknown costs stay null with a known subtotal shown
separately. Brier score and directional accuracy use case-mean probabilities on
the **same complete paired cases**. Exact observed rate ties are retained in the
case table and excluded from winner scoring; a model probability of exactly 0.5
earns half directional credit.

The all-case failure sensitivity substitutes a probability entirely against the
observed winner for each failed repeat, then averages repeats. It is a sensitivity
calculation, not an observed replacement prediction or a bound on relative
advantage. Exact observed ties are excluded there too.

These are descriptive measurements against noisy observed winners. The kit
produces no significance test, confidence interval, or inferential win, and always
sets `general_forecast_accuracy_claim` to false. One customer experiment cannot
establish general predictive accuracy; even multiple cases may share audiences
or products. Use independent, prespecified future cases and an appropriate
analysis plan before making a broader claim. Review whether simulation outputs
produce useful, source-supported questions and objections alongside their extra
cost, rather than treating synthetic personas as actual customer research.
