"""Only application-rendered evidence paragraphs reach report publication."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Config
from app.models.task import JobCancelled, JobLeaseLost
from app.services.report_agent import ReportAgent, ReportManager, ReportOutline, ReportSection, ReportStatus
from app.services.report_provenance import (
    CitationError, SECTION_OUTPUT_RULES, SECTION_RENDERER_VERSION, SECTION_SCHEMA_VERSION, sha256_text,
)
from app.utils.budget import BudgetExceeded


def envelope(cid, text="Source evidence: Customers preferred the simpler description."):
    return json.dumps({"paragraphs": [{"text": text, "source_ids": [cid]}]})


@pytest.fixture
def report_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(tmp_path / "reports"))
    agent = ReportAgent("graph-fixture", "sim-fixture", "An explicit scenario assumption.",
                        llm_client=SimpleNamespace(model="fixture"),
                        zep_tools=SimpleNamespace(get_evidence=lambda _: [{
                            "source_id": "source-fixture", "kind": "document",
                            "text": "Customers preferred the simpler description.",
                        }]))
    monkeypatch.setattr(agent, "plan_outline", lambda **kwargs: ReportOutline("Report", "Summary", [ReportSection("Findings")]))
    return agent


def source_id(agent):
    return next(item["citation_id"] for item in agent.evidence_registry.sources if item["kind"] == "source_fact")


def events(report_id):
    return [json.loads(line) for line in (
        Path(Config.UPLOAD_FOLDER) / "reports" / report_id / "agent_log.jsonl"
    ).read_text().splitlines()]


@pytest.mark.parametrize("prefix", ["", "Final Answer: "])
def test_valid_envelope_renders_without_paid_repair_and_only_publishes_markdown(report_agent, prefix):
    calls = []
    def chat(**kwargs):
        calls.append(deepcopy(kwargs))
        return prefix + envelope(source_id(report_agent))
    report_agent.llm.chat = chat
    report = report_agent.generate_report(report_id="report-envelope")
    assert report.status == ReportStatus.COMPLETED
    assert len(calls) == 1
    assert report.manifest["metrics"]["citation_repair_calls"] == 0
    assert report.manifest["section_output"] == {
        "schema_version": SECTION_SCHEMA_VERSION, "renderer_version": SECTION_RENDERER_VERSION,
        "format": "paragraphs_with_source_ids",
    }
    assert report.manifest["input_hashes"]["section_output_rules"] == sha256_text(SECTION_OUTPUT_RULES)
    published = [item for item in events(report.report_id) if item["action"] == "section_content"]
    assert len(published) == 1
    content = published[0]["details"]["content"]
    assert content == report.outline.sections[0].content
    assert "Customers preferred" in content
    assert f"[{source_id(report_agent)}](#source-{source_id(report_agent)})" in content
    assert '"paragraphs"' not in content
    assert "[[source:" not in content
    system = calls[0]["messages"][0]["content"]
    assert SECTION_OUTPUT_RULES in system
    assert '"citation_token"' not in system
    assert "Copy citation_token exactly" not in system


def test_one_schema_repair_publishes_only_corrected_rendered_section(report_agent):
    calls = []
    def chat(**kwargs):
        calls.append(deepcopy(kwargs))
        if len(calls) == 1:
            return json.dumps({"paragraphs": [{"text": "Unsupported draft", "source_ids": ["e-000000000000000000000000"]}]})
        return envelope(source_id(report_agent))
    report_agent.llm.chat = chat
    report = report_agent.generate_report(report_id="report-corrected")
    assert report.status == ReportStatus.COMPLETED
    assert len(calls) == 2
    assert calls[1]["response_format"] == {"type": "json_object"}
    assert SECTION_OUTPUT_RULES in calls[1]["messages"][0]["content"]
    assert '"citation_token"' not in calls[1]["messages"][1]["content"]
    assert report.manifest["metrics"]["citation_repair_calls"] == 1
    published = [item for item in events(report.report_id) if item["action"] == "section_content"]
    assert len(published) == 1
    assert "Unsupported draft" not in published[0]["details"]["content"]
    assert report.citation_validation["valid"] is True


def test_even_correct_legacy_markdown_citations_cannot_bypass_new_section_contract(report_agent):
    calls = []
    def chat(**kwargs):
        calls.append(kwargs)
        return f"Source evidence [[source:{source_id(report_agent)}]]."
    report_agent.llm.chat = chat
    report = report_agent.generate_report(report_id="report-raw-markdown")
    assert report.status == ReportStatus.FAILED
    assert len(calls) == 2
    assert not ReportManager.get_generated_sections(report.report_id)
    assert not any(item["action"] == "section_content" for item in events(report.report_id))
    assert report.citation_validation["valid"] is False


@pytest.mark.parametrize("terminal", [JobCancelled, JobLeaseLost, BudgetExceeded])
def test_one_repair_propagates_terminal_job_and_budget_errors(report_agent, terminal):
    report_agent.evidence_registry.add_assumption("A supplied assumption")
    calls = []
    failure = terminal("stop")
    def chat(**kwargs):
        calls.append(kwargs)
        raise failure
    report_agent.llm.chat = chat
    with pytest.raises(terminal) as raised:
        report_agent._validate_or_repair_section("", "Findings", 1)
    assert raised.value is failure
    assert len(calls) == 1
    assert report_agent._report_metrics["citation_repair_calls"] == 1
    assert not report_agent.evidence_registry.verified


def test_one_repair_provider_failure_remains_failed_without_hidden_retry(report_agent):
    report_agent.evidence_registry.add_assumption("A supplied assumption")
    calls = []
    failure = RuntimeError("provider unavailable")
    def chat(**kwargs):
        calls.append(kwargs)
        raise failure
    report_agent.llm.chat = chat
    with pytest.raises(CitationError) as raised:
        report_agent._validate_or_repair_section("", "Findings", 1)
    assert raised.value.__cause__ is failure
    assert len(calls) == 1


def test_tool_observation_exposes_exact_id_without_teaching_section_citation_syntax(report_agent, monkeypatch):
    monkeypatch.setattr(report_agent, "_execute_tool_raw", lambda *args: "A simulated observation")
    result = report_agent._execute_tool("quick_search", {"query": "evidence"}, structured_section=True)
    payload = json.loads(result)
    assert payload["citation_id"] == report_agent.evidence_registry.sources[-1]["citation_id"]
    assert payload["kind"] == "simulation_observation"
    assert payload["derived"] is True
    assert "[[source:" not in result
    assert report_agent._report_metrics["tool_calls"] == 1
    # Legacy report chat still receives the original source-token contract.
    legacy = report_agent._execute_tool("quick_search", {"query": "evidence"})
    assert "[[source:" in legacy


def test_final_answer_words_inside_paragraph_do_not_split_json(report_agent):
    source = report_agent.evidence_registry.add_assumption("Explain a literal label")
    raw = envelope(source["citation_id"], "Assumption: The visible label is Final Answer: followed by a choice.")
    assert report_agent._section_payload(raw) == raw
    assert report_agent._parse_tool_calls(raw) == []
    assert "Final Answer:" in report_agent._validate_or_repair_section(raw, "Findings", 1)


def test_outline_keeps_its_own_schema_instead_of_requesting_section_paragraphs():
    calls = []
    def chat_json(**kwargs):
        calls.append(kwargs)
        return {"title": "Report", "summary": "Summary", "sections": [{"title": "One"}, {"title": "Two"}]}
    agent = ReportAgent("graph-fixture", "sim-fixture", "Scenario", llm_client=SimpleNamespace(chat_json=chat_json),
                        zep_tools=SimpleNamespace(get_simulation_context=lambda **kwargs: {}))
    assert len(agent.plan_outline().sections) == 2
    system = calls[0]["messages"][0]["content"]
    assert SECTION_OUTPUT_RULES not in system
    assert '"sections"' in system


def test_envelope_with_tool_keys_is_schema_error_and_never_dispatched(report_agent, monkeypatch):
    calls = []
    monkeypatch.setattr(report_agent, "_execute_tool", lambda *args, **kwargs: pytest.fail("Final envelope must not dispatch a tool"))
    def chat(**kwargs):
        calls.append(kwargs)
        result = json.loads(envelope(source_id(report_agent)))
        if len(calls) == 1:
            result.update(name="quick_search", parameters={"query": "evidence"})
        return json.dumps(result)
    report_agent.llm.chat = chat
    report = report_agent.generate_report(report_id="report-extra-tool-keys")
    assert report.status == ReportStatus.COMPLETED
    assert len(calls) == 2
    assert report.manifest["metrics"]["tool_calls"] == 0
    assert report.manifest["metrics"]["citation_repair_calls"] == 1
    assert "invalid-section-envelope" in report.manifest["citation_repairs"][0]["initial_error"]
