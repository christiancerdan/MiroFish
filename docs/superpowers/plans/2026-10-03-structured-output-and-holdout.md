# Structured output reliability and fresh holdout

User-approved follow-up to the October 2 audience-response pilot. The previous
pilot remains frozen and separate; its six cases are development evidence.

## Diagnosis

The prior configuration failed all twelve MiroFish trials: five ontology,
four source extraction, and three simulation-memory failures. The ontology
prompt demanded ten types even for three fictional people. Extraction advertised
generic fallback types that were invalid under a populated ontology, and schema
validation happened outside the model's repair loop. Empty facts and summaries
correctly prevented publication but did not receive corrective feedback.

Removing an output cap on retry also did not increase available output: the
budget layer reapplied its 4,096-token default. A separate report review found
that the three-tool minimum conflicted with a configured two-tool maximum,
repeating otherwise usable final answers, and XML-wrapped tool payloads bypassed
normalization applied to plain JSON payloads.

Ollama's [current documentation](https://docs.ollama.com/capabilities/structured-outputs)
states that Cloud does not support structured outputs. We therefore retain
application validation and bounded regeneration instead of assuming provider
JSON mode enforces the application's schema.

## Implementation

1. Add opt-in validation feedback to the shared JSON client, with finite content
   attempts and token caps. Account for every request; propagate budget/provider
   failures. Never manufacture missing values or publish invalid responses.
2. Generate a concise ontology appropriate to the source. Preserve downstream
   compatibility and validate before returning it.
3. Give extraction explicit allowed types, endpoints, and required field rules.
   Regenerate only before atomic ingestion; keep failed writes visible.
4. Honor report tool limits without redundant final-answer calls and normalize
   supported tool-call wrappers consistently, keeping citation validation.
5. Test malformed, truncated, schema-invalid, exhausted, and valid responses,
   including failed-repair atomicity. Run real development trials on the old
   pilot cases and investigate any remaining end-to-end failure.

## Evaluation contract

Prepare twenty fresh historical Upworthy tests, excluding all twenty tests in
the original pilot/reserve fixture. Use the same corrected-date exclusions and
matched non-headline stimuli. Freeze selection using identity/stimulus metadata
only; do not release new outcome labels until generation is complete.

After development checks, freeze source hash, model, prompts, limits, and a
two-repeat comparison between the single-model baseline and full MiroFish.
Both methods receive identical source material. Continue using configured
`gpt-oss:20b-cloud`; retain every failed trial and all usage. No retries or
replacement trials after the holdout freeze. Use existing case-level paired
scoring and failure reporting. Unknown monetary costs remain unknown.

The reliability gate is at least two complete development executions on
different old pilot cases, including source-preservation and report evidence
checks. If a systemic provider outage prevents calls, preserve completed work
and report the blocked experiment accurately.

This is historical headline-response replay, not confirmed product-demand
forecasting. Public-model memorization and noisy observed CTR order remain
limitations. Twenty independent experiments, not eighty independent people,
underlie the comparison.

## Delivery

Review the patch, run affected and required checks, publish a pull request on
the maintained fork, wait for CI, and merge after successful validation. Preserve
the exact experiment source commits and machine-readable results.
