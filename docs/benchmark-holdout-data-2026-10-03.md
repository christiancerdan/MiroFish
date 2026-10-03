# Fresh Upworthy holdout — 2026-10-03

This freezes **20 new historical experiments** for a comparison after the first pilot's reliability work. All 20 source test IDs from the earlier pilot **and its unrun reserve** are excluded. The new cases must remain separate from development results and both earlier pilot attempts. No new outcome file was created during this input freeze.

The dataset is `upworthy_headline_holdout_20261003_v1`. Its [inputs](../backend/benchmarks/data/holdout-2026-10-03/upworthy_inputs.json) contain only headline text, the fixed audience context, the unavailable-image disclosure, and opaque case/split identifiers. The [selection manifest](../backend/benchmarks/data/holdout-2026-10-03/upworthy_selection_manifest.json) records stimulus matching, source identities, exclusions, hashes, and the fixed seed `mirofish-upworthy-holdout-2026-10-03-v1`.

| Frozen artifact | SHA-256 |
|---|---|
| Input file bytes | `e89acb4b512a70c3562823ce4348f4d651c29da473ea4cdc9cc2153dcc21b666` |
| Canonical decoded inputs | `759de479a8e79fec9ad768e25404dc17318269b106a3bbc974e953c069eab2bd` |
| Official CSV snapshot | `8368313b060f4015a0c6fb34e6d788163cee29554144aba9390b14922eb9d8ce` |
| Excluded earlier selection manifest | `dd5f7d42ec69ceb586bb9e46f1b5b8806f511feb2c11e1c75e2470704fe1a249` |

Selection uses the same official source, correction dates, and exact matching rules described in [benchmark-data.md](benchmark-data.md), including its source attribution and **CC BY 4.0** license. The original `prepare_upworthy.py` and earlier artifacts are unchanged. The new helper loads an isolated copy of that selector with a new seed and projects only the original permitted metadata fields. Clicks, impressions, editorial winner flags, significance, and predictions do not affect eligibility, pair selection, ranking, or orientation.

Excluding the prior 20 tests removes 97 source rows. Of the remaining 4,853 source tests, 1,050 fall in the randomization-correction window and 1,945 lack an eligible matched pair. The resulting 1,858 eligible tests contain 15,471 matching pairs. The frozen selection takes one deterministically chosen pair from each of 20 deterministically ranked distinct tests. It does not choose cases by observed effect size or significance.

## Experiment and release boundary

The planned schedule is **20 cases × 2 repeats × 2 methods = 80 trial slots**. The method names remain `single_model` and `mirofish`. All cases use `split: "reserve"` for compatibility with the existing strict input schema; that label does not remove them from this holdout experiment. Build the new experiment protocol with an explicit `case_ids` list containing all 20 `upworthy-holdout-*` IDs. The protocol and deterministic run schedule must be frozen separately after the final source/model/configuration checks and before generation. The data manifest is not a model/configuration freeze.

Each scheduled trial must produce a final success or failure record. Preserve failures and their measured or conservative usage, and do not replace cases or restart only unfavourable trials. The outcome-release helper validates the frozen input/protocol identities, all 20 case IDs, two repeats, both methods, and all 80 final records before accessing labels. Missing slots block release. This is a reproducibility guard, not an authenticated access-control boundary.

To reproduce the **input-only** freeze, from the repository root:

```sh
backend/.venv/bin/python -S backend/benchmarks/data/prepare_upworthy_holdout.py \
  freeze /path/to/upworthy-archive-exploratory-packages-03.12.2020.csv
```

The source file must have the frozen CSV hash. Repeating the freeze is idempotent; it refuses to overwrite changed input or manifest artifacts. Source rows and the CSV remain outside the repository.

Only after generation has been declared complete, release labels with the separately saved protocol and all final prediction records:

```sh
backend/.venv/bin/python -S backend/benchmarks/data/prepare_upworthy_holdout.py \
  outcomes /path/to/upworthy-archive-exploratory-packages-03.12.2020.csv \
  --protocol /path/to/frozen-protocol.json \
  --completed-predictions /path/to/completed-predictions.json
```

The release step reuses the original observed-CTR and Wilson/Newcombe interval calculation, rechecks selection, and refuses to overwrite an outcome file. Scoring then uses the separate [comparison scorer](comparative-benchmark.md). Outcomes must not be fed back into generation or used to select a replacement configuration for this frozen run.

## What this holdout can establish

Twenty distinct source tests provide twenty experimental cases; the two model repeats do not create forty independent human experiments. Report completion rates, all scheduled usage, complete-pair scores, and failure sensitivity. A larger completed sample may offer a better comparison than the six-case pilot, but twenty selected cases still give imprecise estimates and potentially unstable bootstrap intervals. Article, audience, and time overlap can also weaken the independent-case assumption.

This is still **public historical replay**, with a configuration tuned using a different pilot from the same archive. Excluding prior test IDs prevents exact test reuse; it does not guarantee that article topics, audiences, wording, or model training data are independent. Real historical CTR orderings can be noisy; retain ties and uncertain labels under the predeclared scoring rules rather than filtering them after seeing results. Missing images and fictional personas limit ecological validity. Headline clicks measure a narrow message-response task and cannot establish purchase demand, general forecasting accuracy, calibrated probabilities, or usefulness for every MiroFish application.
