# Historical headline-response benchmark data

This fixture compares MiroFish and a single-model baseline on a narrow, observed
human response: which of two headlines received a higher historical click-through
rate. It is a customer/message-response **proxy**, not a test of purchases,
customer satisfaction, policy outcomes, or general forecasting ability. Six cases
are a pilot for the evaluation pipeline; the additional 14 cases are a frozen
reserve pool, not an independent confirmatory sample if used after pilot tuning.

## Primary source, correction, and attribution

The source is the [Upworthy Research Archive](https://upworthy.natematias.com/),
created by J. Nathan Matias, Kevin Munger, Marianne Aubin Le Quere, and Charles
Ebersole, with data donated by Good/Upworthy and published by Cornell University.
Cite their [2021 data descriptor](https://doi.org/10.1038/s41597-021-00934-7) and
[2024 author correction](https://doi.org/10.1038/s41597-024-03600-w). The archive
contains 32,487 experiments; this fixture uses only its exploratory partition of
4,873 tests and 22,666 package rows.

The source data and these adapted headline/count fixtures are licensed under
[Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/).
Retain this attribution, source links, and disclosure of changes when sharing the
fixture. Our changes are deterministic subsetting, A/B relabeling, exclusion of
the corrected date window, separation of inputs from outcomes, and derived CTRs
and intervals. The archive authors and Upworthy do not endorse this benchmark.
The archive's [data documentation](https://upworthy.natematias.com/about-the-archive.html)
states the license and describes the fields.

Downloaded official source:

- [OSF exploratory CSV](https://osf.io/download/3vqmp/), within the
  [official archive](https://osf.io/jd64p/).
- Filename: `upworthy-archive-exploratory-packages-03.12.2020.csv`.
- OSF file ID `608ff67f5533b40296e21e5a`, version 1, modified
  `2021-05-03T13:11:27.711690`; 14,260,949 bytes.
- SHA-256: `8368313b060f4015a0c6fb34e6d788163cee29554144aba9390b14922eb9d8ce`.

The downloaded snapshot has **no `problem` column**. We apply the published
correction ourselves, excluding a whole test if any package's `created_at` date
falls between **2013-06-25 and 2014-01-10, inclusive**. The archive's
[June 2024 explanation](https://upworthy.natematias.com/2024-06-upworthy-archive-update.html)
describes likely caching-related assignment failures in that interval. This is
not a claim that the 2021 CSV was itself corrected. Source timestamp timezone is
unknown; we use its literal date. The source hash is pinned, so a different
release needs an explicitly reviewed new fixture rather than silent replacement.

## Selection fixed before inspecting outcome counts

`backend/benchmarks/data/prepare_upworthy.py` has separate `freeze` and `outcomes`
stages. The selector projects rows to only identity, date, headline, and matching
fields; clicks, impressions, editorial `winner`, `first_place`, `significance`,
and `updated_at` cannot enter selection. No count threshold, CTR difference,
statistical significance, topic judgment, or model prediction chooses a case.

The frozen rule is:

1. Exclude the entire corrected date window as above.
2. Form pairs within a single `clickability_test_id`. Require a nonempty shared
   `eyecatcher_id` and exact equality of `excerpt`, `lede`, `share_text`, and
   `square`, including equal empty values. Require two nonempty headlines that
   differ after case-folding and whitespace normalization. Preserve original
   headline text in inputs.
3. Set the seed to `mirofish-upworthy-pilot-v1`. Hash compact, sorted-key UTF-8 JSON
   arrays with SHA-256. For each eligible pair, hash
   `[seed,"pair",test_id,low_row_id,high_row_id]`, where row IDs are ordered
   lexicographically. Retain the lowest-hash pair per test.
4. Rank tests by the hash of `[seed,"test",test_id]` and retain the first 20.
   Orient A/B by the parity of the hash of
   `[seed,"orientation",test_id,low_row_id,high_row_id]`. The first six ranked
   tests form the pilot; the remaining 14 are the reserve. Source row IDs are
   the CSV's unique unnamed index column, not invented package IDs.
5. Freeze input bytes, selected identities, and matching-field hashes before
   attaching counts. Selected counts must be integer and satisfy
   `0 <= clicks <= impressions`, with positive impressions. Invalid selected
   data fails preparation; it does not trigger replacement cases.

This yields **1,878 eligible tests / 15,632 eligible pairs**, after excluding
1,050 tests by date and 1,945 with no matching, distinct-headline pair. An initial
metadata-only check also tried matching `slug`; that left zero eligible pairs,
so slug matching was dropped before freezing or inspecting counts. Slugs are
internal URL names, not the shared-image condition. No other selection rule was
changed after the first outcome inspection.

The paper and archive describe headline/image packages. Their current field
documentation says images are unavailable and identifies the other matched text
as material not believed to be shown during these tests. Matching those fields
is an extra conservative control; it does not reconstruct all presentation or
audience conditions. Identical image IDs hold the recorded image constant but
cannot test image/headline interaction when neither model receives the image.
Neither model receives excerpt, lede, sharing text, URL, source IDs, or dates.

## Inputs and labels remain separate

- `upworthy_inputs.json`: `schema_version`, `dataset_id`, and `cases`; each case
  contains only `case_id`, `split`, `headline_a`, `headline_b`, `audience_context`,
  and `shared_image_unavailable`.
- `upworthy_outcomes.json`: separate raw clicks/impressions, observed CTRs,
  `observed_winner` (`A`, `B`, or `tie`), uncertainty intervals, and source row/test
  IDs. These are scorer data and must never be included in model prompts,
  generated personas, simulation memory, or a retrieved document collection.
- `upworthy_selection_manifest.json`: source provenance, exact algorithm seed,
  counts, matching checks, selected identities, and hashes. It is audit metadata,
  not model context.

The input-file SHA-256 is
`5d48910287e7c14ea3304dba6eaef37ab3eb425929fbcd32290ae7cbb9c9f406`.
`inputs_file_sha256` in the manifest and `inputs_sha256` in outcomes hash those
exact file bytes. `inputs_canonical_sha256` in both files instead hashes
`json.dumps(inputs, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
allow_nan=False).encode('utf-8')`; it supports scorers receiving decoded JSON.
These hashes are intentionally distinct. Adding the canonical-hash metadata did
not change any frozen input byte or selected case.

## Observed outcomes and uncertainty

The label uses `clicks / impressions` for each arm; an exact integer
cross-product comparison determines its observed ordering. The archive describes
assignments, including people who may never have scrolled to the preview, so the
metric is an intention-to-treat click rate rather than clicks per verified view.

Each arm includes a 95% Wilson score interval without continuity correction.
The interval for `CTR_A - CTR_B` combines these using Newcombe's independent
proportions method. For rates `pA,pB` and Wilson bounds `[LA,UA]`, `[LB,UB]`, use:

```text
d = pA - pB
lower = d - sqrt((pA - LA)^2 + (UB - pB)^2)
upper = d + sqrt((UA - pA)^2 + (pB - LB)^2)
```

See Newcombe's original papers on
[single proportions](https://doi.org/10.1002/(SICI)1097-0258(19980430)17:8%3C857::AID-SIM777%3E3.0.CO;2-E)
and [independent differences](https://doi.org/10.1002/(SICI)1097-0258(19980430)17:8%3C873::AID-SIM779%3E3.0.CO;2-I).
These are per-case intervals under an independent-binomial model, not simultaneous
intervals across the benchmark. They do not account for unobserved repeated-user
dependence, clustering, adaptive stopping, or residual platform measurement error.

**Three of six pilot cases and 13 of 20 pool cases have difference intervals
that include zero. Every case is retained.** Do not call an observed ordering a
known population winner, exclude uncertain pairs after seeing results, or count
the two arms as independent benchmark cases. Report raw ordering accuracy with
its small sample size and label uncertainty alongside any richer scores.

This historical public dataset may overlap model training data. Neither hiding
source IDs nor separating labels removes that possibility. Results assess a
retrospective, potentially contaminated proxy. They cannot establish prospective
performance or superiority across real customer decisions. Six cases can reveal
pipeline failures and costs, but cannot support a broad effectiveness claim.

## Reproduce without changing the frozen fixture

Use a separate output directory and compare generated artifacts. The script
verifies the complete official CSV hash before either stage. It uses Python's
standard library and makes no model or network calls.

```sh
curl -fL https://osf.io/download/3vqmp/ -o /tmp/upworthy-exploratory.csv
python3 backend/benchmarks/data/prepare_upworthy.py freeze \
  /tmp/upworthy-exploratory.csv --output /tmp/upworthy-fixture
python3 backend/benchmarks/data/prepare_upworthy.py outcomes \
  /tmp/upworthy-exploratory.csv --output /tmp/upworthy-fixture
```

Do not overwrite or tune this sample after observing model predictions. A later
domain-specific or prospective evaluation should receive a new dataset ID,
protocol, and disclosure of how its cases were chosen.
