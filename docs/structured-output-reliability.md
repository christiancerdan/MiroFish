# Structured output reliability

The maintained fork validates generated data before publishing it or writing
graph memory. Provider JSON mode is a convenience, not a schema guarantee.
In particular, [Ollama Cloud currently does not support structured outputs](https://docs.ollama.com/capabilities/structured-outputs)
(documentation checked October 3, 2026). Cloud inference remains supported with
application validation and bounded regeneration.

## Generation and budgets

`LLMClient.chat_json` defaults to one content attempt. Callers may opt into up to
three attempts and supply a pure validator. Syntax and schema failures share
that same attempt count. A retry receives a bounded, quoted validation diagnostic
and the original source request; it does not invent replacement values. Provider
and budget exceptions propagate immediately. One explicit provider rejection of
JSON mode may add one compatibility request, also charged to the budget.

All JSON requests have finite output caps. An omitted cap becomes at most 4,096
tokens; an explicit retry cap may increase it. Caps are at most 32,768 and clamp
to the configured model token limit. Invalid JSON, duplicate keys, non-finite
numbers, truncated output, and exhausted validation retries remain failures.

Ontology generation uses at most two attempts, requesting 8,192 then 16,384
output tokens. It requests a small source-grounded taxonomy instead of exactly
ten actor types. One type and no relationships are valid. Explicitly fictional
personas retain their assumed status. Invalid entries are rejected rather than
silently removed or replaced with invented defaults.

Graph extraction uses at most two attempts, each capped at 8,192 output tokens.
It receives allowed labels/endpoints and existing names/types, plus the current
source episode. Validation covers required summaries/facts, ontology membership,
endpoint references, duplicate entities, and supplied evidence excerpts. Writes
remain atomic: all repair happens before ingestion, and a failed batch does not
publish partial graph changes. A declared ontology with no relationships stays
edgeless; generic defaults apply only to an unconfigured legacy graph.

Profile generation uses the same two-attempt validation contract with 8,192 then
16,384 token caps. Unknown actor types receive neutral source-based instructions;
only recognized group types receive institution-specific instructions. Profiles
are concise, preserve fictional assumptions, and leave unknown demographics
unset. Invalid or truncated output cannot silently become a replacement rule
profile in LLM mode. Explicit rule-based generation remains a separate option.
Default social counts are simulation parameters, not observed audience facts.

## Reports

Reports honor `REPORT_AGENT_MAX_TOOL_CALLS` per section. They may use the evidence
registry directly when it is sufficient; a minimum tool-call quota is no longer
required. XML-wrapped and plain JSON tool requests share normalization and
bounded malformed-request recovery. Every generated section still passes the
existing evidence/citation checks and bounded section repair before publication.
Tool-shaped model prose is sanitized on normal and forced final-answer paths.

These controls improve reliability and provenance. They do not establish that
model-generated facts are true or that simulated audiences predict real demand.
The [original pilot](benchmark-pilot-2026-10-02.md) remains frozen; subsequent
development checks and fresh holdout results must be reported separately.
