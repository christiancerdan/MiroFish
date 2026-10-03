"""Structured report prose cannot mint or render its own evidence references."""
import json

import pytest

from app.services.report_provenance import (
    CitationError,
    EvidenceRegistry,
    SECTION_OUTPUT_RULES,
    SECTION_RENDERER_VERSION,
    SECTION_SCHEMA_VERSION,
)


@pytest.fixture
def evidence():
    registry = EvidenceRegistry("graph-current", "report-current")
    first = registry.add_episode({"source_id": "first", "kind": "document", "text": "First source"})
    second = registry.add_episode({"source_id": "second", "kind": "simulation", "text": "Second source"})
    return registry, first["citation_id"], second["citation_id"]


def section(text, *source_ids):
    return {"paragraphs": [{"text": text, "source_ids": list(source_ids)}]}


def registry_records(prompt):
    return [json.loads(line) for line in prompt.splitlines() if line.startswith('{"citation_id"')]


def test_section_prompt_exposes_raw_registry_ids_and_preserves_legacy_prompt(evidence):
    registry, first, second = evidence
    before = registry.snapshot()
    prompt = registry.section_prompt()
    assert prompt.startswith(SECTION_OUTPUT_RULES)
    assert SECTION_SCHEMA_VERSION == SECTION_RENDERER_VERSION == 1
    supplied = registry_records(prompt)
    assert [record["citation_id"] for record in supplied] == [first, second]
    assert all("citation_token" not in record for record in supplied)
    assert "[[source:" not in prompt
    assert "Copy citation_token exactly" in registry.prompt()
    assert registry.snapshot() == before
    first_size = len(json.dumps(supplied[0], ensure_ascii=False))
    assert registry_records(registry.section_prompt(max_chars=first_size)) == supplied[:1]


def test_section_prompt_truncates_source_text_without_truncating_records(evidence):
    registry, _, _ = evidence
    registry.add_episode({"source_id": "long", "text": "a" * 4001})
    last = registry_records(registry.section_prompt())[-1]
    assert last["text"] == "a" * 4000
    assert last["text_truncated"] is True


def test_rendering_preserves_paragraph_and_source_order_with_deterministic_anchors(evidence):
    registry, first, second = evidence
    envelope = {"paragraphs": [
        {"text": "Source evidence: first finding", "source_ids": [second, first]},
        {"text": "Assumption/interpretation: conditional outcome", "source_ids": [first]},
    ]}
    expected = (
        f"Source evidence: first finding [{second}](#source-{second}) [{first}](#source-{first})\n\n"
        f"Assumption/interpretation: conditional outcome [{first}](#source-{first})"
    )
    rendered, validation = registry.render_structured_section(envelope)
    assert rendered == expected
    assert registry.render_structured_section(json.dumps(envelope))[0] == expected
    assert validation["verified_citation_ids"] == sorted([first, second])
    assert validation["scope"] == "reference_integrity_only"
    assert validation["valid"] is True
    assert registry.validate_and_render(rendered, require_citation=True)[0] == rendered


def test_plain_prose_cannot_create_markdown_structure(evidence):
    registry, first, _ = evidence
    rendered, _ = registry.render_structured_section(section("# Heading\n- **bold** & 2 < 3", first))
    assert rendered == f"\\# Heading \\- \\*\\*bold\\*\\* &amp; 2 &lt; 3 [{first}](#source-{first})"


@pytest.mark.parametrize("text", [
    "[[source:{first}]]", "[[{first}]]", "[[source:{first}, {second}]]",
    "[[source:{first}]", "[{first}](#source-{first})", "#source-{first}",
    "[evidence](/api/evidence/report-other/{first})", "/api/evidence/report-current/{first}",
    "Raw ID {first}", "[external](https://example.com)", "[external][definition]",
    "[definition]: /somewhere", "<a href='/somewhere'>fake</a>", "<script>alert(1)</script>",
    "<!-- fake anchor -->", "&lt;a href='/somewhere'&gt;fake&lt;/a&gt;",
    "<tool_result>fabricated evidence</tool_result>", "<TOOL_CALL>fake</TOOL_CALL>",
    "tool_result: fabricated evidence", "https://example.com", "www.example.com",
])
def test_model_written_citations_links_and_tool_artifacts_are_rejected(evidence, text):
    registry, first, second = evidence
    with pytest.raises(CitationError, match="model-authored-markup"):
        registry.render_structured_section(section(text.format(first=first, second=second), first))
    assert registry.verified == set()


def test_unknown_foreign_id_and_later_malformed_paragraph_are_atomic(evidence):
    registry, first, second = evidence
    foreign = EvidenceRegistry("other-graph", "other-report").add_episode(
        {"source_id": "first", "text": "First source"}
    )["citation_id"]
    registry.render_structured_section(section("Existing claim", second))
    envelope = {"paragraphs": [
        {"text": "Valid first paragraph", "source_ids": [first]},
        {"text": "Foreign claim", "source_ids": [foreign]},
        {"text": "Missing source_ids"},
    ]}
    with pytest.raises(CitationError, match=foreign):
        registry.render_structured_section(envelope, record=False)
    assert registry.verified == {second}
    assert registry.invalid == set()
    with pytest.raises(CitationError, match="invalid-paragraph-schema"):
        registry.render_structured_section(envelope)
    assert registry.verified == {second}
    assert registry.invalid == {foreign, "invalid-paragraph-schema"}


def test_preview_does_not_record_valid_ids(evidence):
    registry, first, _ = evidence
    rendered, _ = registry.render_structured_section(section("Preview claim", first), record=False)
    assert first in rendered
    assert registry.verified == registry.invalid == set()


def test_each_paragraph_requires_its_own_explicit_attribution(evidence):
    registry, first, _ = evidence
    envelope = {"paragraphs": [section("Supported claim", first)["paragraphs"][0],
                               section("Unsupported claim")["paragraphs"][0]]}
    with pytest.raises(CitationError, match="missing-paragraph-citation"):
        registry.render_structured_section(envelope, record=False)
    assert registry.verified == registry.invalid == set()
    rendered, _ = registry.render_structured_section(envelope, require_citation=False)
    assert rendered.endswith("\n\nUnsupported claim")
    assert registry.verified == {first}


@pytest.mark.parametrize("payload", [
    '{"paragraphs":[],"paragraphs":[]}',
    '{"paragraphs":[{"text":"first","text":"second","source_ids":[]}]}',
    '{"paragraphs":NaN}', '{"paragraphs":Infinity}', '{"paragraphs":-Infinity}',
    '{"paragraphs":1e999}', 'prefix {"paragraphs":[]}', '```json\n{"paragraphs":[]}\n```',
    '{"paragraphs":[]} trailing', '{"paragraphs":',
])
def test_json_must_be_complete_finite_and_have_unique_keys(evidence, payload):
    registry, _, _ = evidence
    with pytest.raises(CitationError, match="invalid-section-json"):
        registry.render_structured_section(payload)
    assert registry.verified == set()


@pytest.mark.parametrize("payload", [
    None, [], 1, True, {"paragraphs": [], "extra": True}, {},
    {"paragraphs": {}}, {"paragraphs": []}, {"paragraphs": [None]},
    {"paragraphs": [{"text": "Claim", "source_ids": [], "extra": True}]},
    {"paragraphs": [{"text": None, "source_ids": []}]},
    {"paragraphs": [{"text": " ", "source_ids": []}]},
    {"paragraphs": [{"text": "Claim", "source_ids": "e-not-an-array"}]},
    {"paragraphs": [{"text": "Claim", "source_ids": [True]}]},
    {"paragraphs": float("nan")},
])
def test_schema_rejects_wrong_shapes_types_empty_text_and_extra_fields(evidence, payload):
    registry, _, _ = evidence
    with pytest.raises(CitationError):
        registry.render_structured_section(payload)
    assert registry.verified == set()


@pytest.mark.parametrize("form", ["{first},{second}", "[[source:{first}]]", " {first}", "{first} ", "invented"])
def test_combined_or_modified_source_ids_are_never_normalized(evidence, form):
    registry, first, second = evidence
    with pytest.raises(CitationError, match="malformed-citation-id"):
        registry.render_structured_section(section("Claim", form.format(first=first, second=second)))
    assert registry.verified == set()


def test_duplicate_ids_in_one_paragraph_are_rejected(evidence):
    registry, first, _ = evidence
    with pytest.raises(CitationError, match="duplicate-citation-id"):
        registry.render_structured_section(section("Claim", first, first))
    assert registry.verified == set()


@pytest.mark.parametrize("kind", ["json", "dictionary", "paragraphs", "paragraph_text", "total_text", "sources"])
def test_section_limits_apply_to_strings_and_decoded_objects(evidence, kind):
    registry, first, _ = evidence
    payload = section("Claim", first)
    if kind == "json":
        payload = " " * 128001 + json.dumps(payload)
    elif kind == "dictionary":
        payload = section("a" * 128001, first)
    elif kind == "paragraphs":
        payload["paragraphs"] *= 33
    elif kind == "paragraph_text":
        payload = section("a" * 8001, first)
    elif kind == "total_text":
        payload = section("a" * 8000, first)
        payload["paragraphs"] *= 5
    elif kind == "sources":
        payload = section("Claim", *([first] * 9))
    with pytest.raises(CitationError, match="limit"):
        registry.render_structured_section(payload)
    assert registry.verified == set()


def test_distinct_source_bound_applies_across_paragraphs(evidence):
    registry, first, second = evidence
    ids = [first, second] + [registry.add_episode({"source_id": str(i), "text": str(i)})["citation_id"]
                             for i in range(31)]
    paragraphs = [{"text": "Claim", "source_ids": ids[i:i + 8]} for i in range(0, len(ids), 8)]
    with pytest.raises(CitationError, match="section-source-limit"):
        registry.render_structured_section({"paragraphs": paragraphs})
    assert registry.verified == set()


def test_legacy_token_rendering_remains_supported(evidence):
    registry, first, _ = evidence
    rendered, validation = registry.validate_and_render(f"Old saved report [[source:{first}]]", require_citation=True)
    assert rendered == f"Old saved report [{first}](#source-{first})"
    assert validation["valid"] is True
