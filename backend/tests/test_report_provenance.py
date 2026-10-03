import json
from types import SimpleNamespace

import pytest

from app.services.report_provenance import EvidenceRegistry, CitationError, uncertainty_metadata
from app.services.report_agent import Report, ReportAgent, ReportManager, ReportOutline, ReportSection, ReportStatus
from app.config import Config


def section_json(text, *source_ids):
    return json.dumps({"paragraphs": [{"text": text, "source_ids": list(source_ids)}]})


def test_source_identity_is_stable_and_content_is_snapshotted():
    records = [{"source_id": "episode-1", "kind": "document", "text": "Document says X.", "reference_time": None}]
    first = EvidenceRegistry("graph-1", "report-1")
    second = EvidenceRegistry("graph-1", "report-2")
    one = first.add_episode(records[0])
    two = second.add_episode(records[0])
    assert one["citation_id"] == two["citation_id"]
    assert one["kind"] == "source_fact"
    assert one["reference_time"] is None
    assert one["content_sha256"]
    assert one["url"].startswith("/api/evidence/report-1/")
    records[0]["text"] = "changed"
    assert one["text"] == "Document says X."


def test_generated_material_cannot_become_source_fact():
    registry = EvidenceRegistry("graph-1", "report-1")
    simulated = registry.add_episode({"source_id": "sim-1", "kind": "simulation", "text": "Agent believes X."})
    unknown = registry.add_episode({"source_id": "other", "kind": "unknown", "text": "Unknown origin."})
    assert simulated["kind"] == "simulation_observation"
    assert unknown["kind"] == "unclassified"


def test_invented_citation_is_rejected_and_valid_citation_becomes_link():
    registry = EvidenceRegistry("graph-1", "report-1")
    source = registry.add_episode({"source_id": "episode-1", "kind": "document", "text": "X"})
    citation_id = source["citation_id"]
    content, check = registry.validate_and_render(f"Source evidence: X [[source:{citation_id}]].")
    assert f"](#source-{citation_id})" in content
    assert check["verified_citation_ids"] == [citation_id]
    assert check["scope"] == "reference_integrity_only"
    for forged in ["[[source:made-up]]", "[made-up](#source-made-up)", "[source](/api/evidence/report-1/made-up)"]:
        with pytest.raises(CitationError):
            registry.validate_and_render(forged)


def test_source_html_is_escaped_in_markdown_appendix():
    registry = EvidenceRegistry("graph-1", "report-1")
    registry.add_episode({"source_id": "x", "kind": "document", "text": '<script>alert(1)</script>\n## Fake heading'})
    appendix = registry.markdown_appendix()
    assert "<script>" not in appendix
    assert "&lt;script&gt;" in appendix


def test_confidence_does_not_assert_calibration():
    uncertainty = uncertainty_metadata()
    assert uncertainty["calibrated"] is False
    assert uncertainty["confidence"] == "unvalidated"
    assert "probabilit" in " ".join(uncertainty["limitations"]).lower()


@pytest.fixture
def report_storage(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(reports))
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path))
    return reports


def test_report_roundtrip_preserves_snapshot_and_manifest(report_storage):
    report = Report("report-1", "sim-1", "graph-1", "Test", ReportStatus.COMPLETED,
                    evidence={"version": 1, "sources": [{"citation_id": "e-1", "text": "Original"}]},
                    manifest={"input_hashes": {"requirement": "abc"}}, uncertainty=uncertainty_metadata())
    ReportManager.save_report(report)
    loaded = ReportManager.get_report("report-1")
    assert loaded.evidence == report.evidence
    assert loaded.manifest == report.manifest
    assert json.loads((report_storage / "report-1" / "evidence.json").read_text()) == report.evidence
    assert json.loads((report_storage / "report-1" / "manifest.json").read_text()) == report.manifest


def test_generation_fails_closed_on_invented_source(report_storage, monkeypatch):
    tools = SimpleNamespace(get_evidence=lambda graph_id: [{"source_id": "original", "kind": "document", "text": "Raw source"}])
    agent = ReportAgent("graph-1", "sim-1", "Test", llm_client=SimpleNamespace(model="test-model"), zep_tools=tools)
    monkeypatch.setattr(agent, "plan_outline", lambda **kwargs: ReportOutline("Test", "Summary", [ReportSection("Analysis")]))
    monkeypatch.setattr(agent, "_generate_section_react", lambda **kwargs: section_json("Invented claim.", "not-real"))
    result = agent.generate_report(report_id="report-bad")
    assert result.status == ReportStatus.FAILED
    assert "citation" in result.error.lower()
    assert not (report_storage / "report-bad" / "section_01.md").exists()
    assert result.evidence["sources"]


def test_successful_report_persists_limits_and_source_links(report_storage, monkeypatch):
    tools = SimpleNamespace(get_evidence=lambda graph_id: [{"source_id": "original", "kind": "document", "text": "Raw source"}])
    agent = ReportAgent("graph-1", "sim-1", "Test", llm_client=SimpleNamespace(model="test-model"), zep_tools=tools)
    monkeypatch.setattr(agent, "plan_outline", lambda **kwargs: ReportOutline("Test", "Summary", [ReportSection("Analysis")]))
    def generate(**kwargs):
        cid = next(s["citation_id"] for s in agent.evidence_registry.sources if s["kind"] == "source_fact")
        return section_json("Source evidence: Raw source.", cid)
    monkeypatch.setattr(agent, "_generate_section_react", generate)
    report = agent.generate_report(report_id="report-ok")
    assert report.status == ReportStatus.COMPLETED
    assert "not calibrated probabilities" in report.markdown_content
    assert "#source-" in report.markdown_content
    assert report.manifest["model"] == "test-model"
    assert report.manifest["input_hashes"]["simulation_requirement"]
    assert report.citation_validation["valid"] is True
    assert report.uncertainty["calibrated"] is False


def test_snapshot_endpoint_is_report_scoped_and_checks_content_hash(report_storage):
    from flask import Flask
    from app.api.evidence import evidence_bp
    registry = EvidenceRegistry("graph-1", "report-1")
    source = registry.add_episode({"source_id": "original", "kind": "document", "text": "Raw source"})
    report = Report("report-1", "sim-1", "graph-1", "Test", ReportStatus.COMPLETED, evidence=registry.snapshot())
    ReportManager.save_report(report)
    app = Flask(__name__)
    app.register_blueprint(evidence_bp, url_prefix="/api/evidence")
    client = app.test_client()
    response = client.get(source["url"])
    assert response.status_code == 200
    assert response.json["data"]["text"] == "Raw source"
    assert client.get("/api/evidence/report-other/" + source["citation_id"]).status_code == 404
    assert client.get("/api/evidence/report-1/invented").status_code == 404
    report.evidence["sources"][0]["text"] = "Tampered source"
    ReportManager.save_report(report)
    assert client.get(source["url"]).status_code == 409


def test_citations_must_resolve_in_this_report_and_sections_require_a_source():
    registry = EvidenceRegistry("graph-1", "report-1")
    source = registry.add_episode({"source_id": "original", "kind": "document", "text": "Raw source"})
    with pytest.raises(CitationError, match="wrong-report"):
        registry.validate_and_render(f"[source](/api/evidence/report-2/{source['citation_id']})")
    with pytest.raises(CitationError, match="missing-section"):
        registry.validate_and_render("Unsupported prose", require_citation=True)


def test_budget_failure_saves_report_before_propagating(report_storage, monkeypatch):
    from app.utils.budget import BudgetExceeded
    tools = SimpleNamespace(get_evidence=lambda graph_id: [])
    agent = ReportAgent("graph-1", "sim-1", "Test", llm_client=SimpleNamespace(model="test-model"), zep_tools=tools)
    def exceed(**kwargs):
        raise BudgetExceeded("max_calls")
    monkeypatch.setattr(agent, "plan_outline", exceed)
    with pytest.raises(BudgetExceeded):
        agent.generate_report(report_id="report-budget")
    assert ReportManager.get_report("report-budget").status == ReportStatus.FAILED
    assert agent.console_logger is None


def test_manifest_captures_available_inputs_and_simulation_settings(report_storage, monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "GRAPH_BACKEND", "local")
    from app.services.simulation_runner import SimulationRunner
    root = tmp_path / "simulation-inputs"
    folder = root / "sim-1"
    folder.mkdir(parents=True)
    config = {"time_config": {"total_simulation_hours": 24}, "llm_model": "simulation-model", "agent_configs": [{"agent_id": 1}]}
    (folder / "simulation_config.json").write_text(json.dumps(config))
    (folder / "run_state.json").write_text(json.dumps({"current_round": 3, "twitter_actions_count": 12}))
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(root))
    agent = ReportAgent("graph-1", "sim-1", "Test", llm_client=SimpleNamespace(model="report-model"), zep_tools=SimpleNamespace(get_evidence=lambda graph_id: []))
    report = Report("report-1", "sim-1", "graph-1", "Test", ReportStatus.PENDING)
    agent._prepare_evidence(report)
    assert report.manifest["settings"]["memory_backend"] == "local"
    assert report.manifest["simulation_settings"]["llm_model"] == "simulation-model"
    assert report.manifest["simulation_settings"]["seed"] is None
    assert report.manifest["simulation_metrics"]["current_round"] == 3
    assert report.manifest["input_hashes"]["simulation_config.json"]
    assert report.manifest["input_hashes"]["report_prompts"]
    assert "simulation_config.json" not in report.manifest["missing_inputs"]


def test_model_supplied_citation_id_cannot_enter_registry():
    registry = EvidenceRegistry("graph-1", "report-1")
    source = registry.add_episode({"source_id": "original", "citation_id": "trusted-id", "kind": "document", "text": "Raw"})
    assert source["citation_id"] != "trusted-id"
    with pytest.raises(CitationError):
        registry.validate_and_render("Claim [[source:trusted-id]]")


def test_lost_worker_cannot_overwrite_replacement_report(report_storage, monkeypatch):
    from app.models.task import JobLeaseLost
    tools = SimpleNamespace(get_evidence=lambda _: [])
    agent = ReportAgent("graph-1", "sim-1", "Old attempt", llm_client=SimpleNamespace(model="test"), zep_tools=tools)
    replacement = Report("report-retry", "sim-1", "graph-1", "New owner", ReportStatus.COMPLETED, markdown_content="Replacement")
    def lose_lease(**kwargs):
        ReportManager.save_report(replacement)
        raise JobLeaseLost("replacement owns job")
    monkeypatch.setattr(agent, "plan_outline", lose_lease)
    with pytest.raises(JobLeaseLost):
        agent.generate_report(report_id="report-retry")
    assert ReportManager.get_report("report-retry").simulation_requirement == "New owner"
    assert agent.console_logger is None


@pytest.mark.parametrize("write", ["report", "section", "outline", "progress", "agent_log"])
def test_expired_execution_cannot_publish_report_files(report_storage, tmp_path, write):
    from app.models.task import JobLeaseLost, TaskManager
    from app.services.report_agent import ReportLogger
    manager = TaskManager(tmp_path / "jobs.sqlite3")
    task_id = manager.enqueue("report_generate", "report_generate", {})
    manager.claim_next("old-owner")
    original = Report("report-lease", "sim-1", "graph-1", "Original", ReportStatus.COMPLETED)
    ReportManager.save_report(original)
    with manager._connection(write=True) as db:
        db.execute("UPDATE tasks SET lease_until=0 WHERE task_id=?", (task_id,))
    operations = {
        "report": lambda: ReportManager.save_report(Report("report-lease", "sim-1", "graph-1", "Stale", ReportStatus.FAILED)),
        "section": lambda: ReportManager.save_section("report-lease", 1, ReportSection("Stale", "Stale")),
        "outline": lambda: ReportManager.save_outline("report-lease", ReportOutline("Stale", "Stale", [])),
        "progress": lambda: ReportManager.update_progress("report-lease", "failed", -1, "Stale"),
        "agent_log": lambda: ReportLogger("report-lease").log("stale", "failed", {}),
    }
    with manager.execution(task_id, "old-owner"), pytest.raises(JobLeaseLost):
        operations[write]()
    assert ReportManager.get_report("report-lease").simulation_requirement == "Original"
    assert not (report_storage / "report-lease" / "section_01.md").exists()


def test_retry_archives_old_sections_and_validates_current_assembled_citations(report_storage, monkeypatch):
    previous = Report("report-retry", "sim-1", "graph-1", "Old", ReportStatus.FAILED)
    ReportManager.save_report(previous)
    for index in range(1, 4):
        ReportManager.save_section("report-retry", index, ReportSection(f"Old {index}", "STALE [[source:not-in-current-registry]]"))
    tools = SimpleNamespace(get_evidence=lambda _: [])
    agent = ReportAgent("graph-1", "sim-1", "Current assumptions", llm_client=SimpleNamespace(model="test"), zep_tools=tools)
    monkeypatch.setattr(agent, "plan_outline", lambda **kwargs: ReportOutline("Current", "Summary", [ReportSection("One"), ReportSection("Two")]))
    monkeypatch.setattr(agent, "_generate_section_react", lambda **kwargs: section_json("Assumption.", agent.evidence_registry.sources[0]["citation_id"]))
    report = agent.generate_report(report_id="report-retry")
    assert report.status == ReportStatus.COMPLETED
    assert "STALE" not in report.markdown_content
    assert len(ReportManager.get_generated_sections("report-retry")) == 2
    assert report.citation_validation["valid"] is True
    archived = list((report_storage / "report-retry" / "attempt_history").glob("*/section_03.md"))
    assert len(archived) == 1
    assert "STALE" in archived[0].read_text()


def test_assembly_ignores_files_beyond_current_outline(report_storage):
    outline = ReportOutline("Current", "Summary", [ReportSection("One")])
    ReportManager.save_section("report-assembly", 1, ReportSection("One", "Current"))
    ReportManager.save_section("report-assembly", 2, ReportSection("Stale", "STALE"))
    assert "STALE" not in ReportManager.assemble_full_report("report-assembly", outline)


def test_actual_assembled_report_is_validated_before_publication(report_storage, monkeypatch):
    agent = ReportAgent("graph-1", "sim-1", "Assumption", llm_client=SimpleNamespace(model="test"), zep_tools=SimpleNamespace(get_evidence=lambda _: []))
    monkeypatch.setattr(agent, "plan_outline", lambda **kwargs: ReportOutline("Current", "Summary", [ReportSection("One")]))
    monkeypatch.setattr(agent, "_generate_section_react", lambda **kwargs: section_json("Assumption.", agent.evidence_registry.sources[0]["citation_id"]))
    monkeypatch.setattr(ReportManager, "assemble_full_report", classmethod(lambda cls, *args: "Unregistered [[source:from-stale-file]]"))
    result = agent.generate_report(report_id="report-validation")
    assert result.status == ReportStatus.FAILED
    assert result.citation_validation["valid"] is False
    assert "from-stale-file" in result.citation_validation["invalid_citation_ids"]
    assert not (report_storage / "report-validation" / "full_report.md").exists()


@pytest.mark.parametrize("terminal_name", ["JobCancelled", "JobLeaseLost"])
def test_outline_fallback_cannot_swallow_terminal_job_signal(monkeypatch, terminal_name):
    from app.models import task as task_module
    terminal = getattr(task_module, terminal_name)
    agent = ReportAgent("graph-1", "sim-1", "Scenario", llm_client=SimpleNamespace(
        model="test", chat_json=lambda **kwargs: {"title": "Test", "summary": "Summary", "sections": [{"title": "One"}]}),
        zep_tools=SimpleNamespace(get_simulation_context=lambda **kwargs: {}))
    def checkpoint(stage, progress, message):
        if progress == 80:
            raise terminal("stop at outline parse checkpoint")
    with pytest.raises(terminal):
        agent.plan_outline(progress_callback=checkpoint)


def test_one_empty_generation_uses_one_bounded_structured_repair(report_storage, monkeypatch):
    calls = []
    agent = None
    def chat(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return ""
        cid = next(source["citation_id"] for source in agent.evidence_registry.sources if source["kind"] == "source_fact")
        return section_json("Source evidence: Raw source.", cid)
    agent = ReportAgent("graph-1", "sim-1", "Scenario", llm_client=SimpleNamespace(model="test", chat=chat),
                        zep_tools=SimpleNamespace(get_evidence=lambda _: [{"source_id": "raw", "kind": "document", "text": "Raw source"}]))
    monkeypatch.setattr(agent, "plan_outline", lambda **kwargs: ReportOutline("Current", "Summary", [ReportSection("One")]))
    result = agent.generate_report(report_id="report-repair")
    assert result.status == ReportStatus.COMPLETED
    assert len(calls) == 2
    assert calls[1]["response_format"] == {"type": "json_object"}
    assert "Raw source" in calls[1]["messages"][1]["content"]
    assert result.citation_validation["valid"] is True
    assert result.citation_validation["verified_citation_ids"]
    assert result.manifest["metrics"]["citation_repair_calls"] == 1
    assert result.manifest["citation_repairs"][0]["repaired"] is True
    assert "invalid-section-json" in result.manifest["citation_repairs"][0]["initial_error"]
    assert "Source evidence: Raw source" in result.markdown_content


@pytest.mark.parametrize("repair_text", [
    section_json("Unsupported without citations"), section_json("Wrong", "still-made-up"), "",
])
def test_one_repair_still_fails_closed_for_persistent_invalid_citations(report_storage, monkeypatch, repair_text):
    calls = []
    def chat(**kwargs):
        calls.append(kwargs)
        return repair_text
    agent = ReportAgent("graph-1", "sim-1", "Scenario", llm_client=SimpleNamespace(model="test", chat=chat), zep_tools=SimpleNamespace(get_evidence=lambda _: []))
    monkeypatch.setattr(agent, "plan_outline", lambda **kwargs: ReportOutline("Current", "Summary", [ReportSection("One")]))
    monkeypatch.setattr(agent, "_generate_section_react", lambda **kwargs: section_json("Wrong", "made-up"))
    result = agent.generate_report(report_id="report-repair-failed")
    assert result.status == ReportStatus.FAILED
    assert len(calls) == 1
    assert result.citation_validation["valid"] is False
    assert result.manifest["citation_repairs"][0]["repaired"] is False
    assert not (report_storage / "report-repair-failed" / "section_01.md").exists()


def test_repair_budget_denial_is_terminal_without_another_call(report_storage, monkeypatch):
    from app.utils.budget import BudgetExceeded
    calls = []
    def chat(**kwargs):
        calls.append(kwargs)
        raise BudgetExceeded("calls")
    agent = ReportAgent("graph-1", "sim-1", "Scenario", llm_client=SimpleNamespace(model="test", chat=chat), zep_tools=SimpleNamespace(get_evidence=lambda _: []))
    monkeypatch.setattr(agent, "plan_outline", lambda **kwargs: ReportOutline("Current", "Summary", [ReportSection("One")]))
    monkeypatch.setattr(agent, "_generate_section_react", lambda **kwargs: "")
    with pytest.raises(BudgetExceeded):
        agent.generate_report(report_id="report-repair-budget")
    assert len(calls) == 1
    assert not (report_storage / "report-repair-budget" / "section_01.md").exists()


@pytest.mark.parametrize("sections", [
    [], [{"title": "4. Recommendations"}], [{"title": str(i)} for i in range(6)],
    None, "not-a-list", [{"title": ""}, {"title": "Valid"}],
    [{"title": "Repeated"}, {"title": " repeated "}], [{"title": 1}, {"title": "Valid"}],
])
def test_invalid_model_outline_uses_validated_three_section_fallback_without_another_call(sections):
    calls = []
    def outline_response(**kwargs):
        calls.append(kwargs)
        return {"title": "Test", "summary": "Summary", "sections": sections}
    agent = ReportAgent("graph-1", "sim-1", "Scenario", llm_client=SimpleNamespace(model="test", chat_json=outline_response),
                        zep_tools=SimpleNamespace(get_simulation_context=lambda **kwargs: {}))
    outline = agent.plan_outline()
    assert len(outline.sections) == 3
    assert len({section.title for section in outline.sections}) == 3
    assert all(section.title.strip() for section in outline.sections)
    assert len(calls) == 1
    assert agent._outline_validation["fallback_used"] is True
    assert agent._outline_validation["model_response_valid"] is False


@pytest.mark.parametrize("count", [2, 3, 5])
def test_valid_model_outline_preserves_every_section(count):
    sections = [{"title": f"Section {i}"} for i in range(count)]
    agent = ReportAgent("graph-1", "sim-1", "Scenario", llm_client=SimpleNamespace(model="test", chat_json=lambda **kwargs: {"title": "Test", "summary": "Summary", "sections": sections}),
                        zep_tools=SimpleNamespace(get_simulation_context=lambda **kwargs: {}))
    outline = agent.plan_outline()
    assert [section.title for section in outline.sections] == [item["title"] for item in sections]
    assert agent._outline_validation == {"model_response_valid": True, "fallback_used": False, "section_count": count}


def test_invalid_live_style_outline_generates_three_sections_and_records_fallback(report_storage, monkeypatch):
    response = {"title": "Conditional analysis", "summary": "An unvalidated scenario", "sections": [{"title": "4. Recommendations for Future Simulations"}]}
    agent = ReportAgent("graph-1", "sim-1", "Fictional scenario assumption", llm_client=SimpleNamespace(model="test", chat_json=lambda **kwargs: response),
        zep_tools=SimpleNamespace(get_evidence=lambda _: [], get_simulation_context=lambda **kwargs: {}))
    monkeypatch.setattr(agent, "_generate_section_react", lambda **kwargs: section_json("Assumption.", agent.evidence_registry.sources[0]["citation_id"]))
    report = agent.generate_report(report_id="report-outline-fallback")
    assert report.status == ReportStatus.COMPLETED
    assert len(report.outline.sections) == 3
    assert len(ReportManager.get_generated_sections(report.report_id)) == 3
    assert report.manifest["outline_validation"]["fallback_used"] is True
    assert report.manifest["outline_validation"]["section_count"] == 3
    events = [json.loads(line) for line in (report_storage / report.report_id / "agent_log.jsonl").read_text().splitlines()]
    assert next(event for event in events if event["action"] == "planning_response")["details"]["response"] == response
