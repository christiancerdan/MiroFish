"""Reports and report conversations must retain the selected execution identity."""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from flask import Flask

from app.api import report as api
from app.models.project import ProjectStatus
from app.models.task import TaskStatus
from app.services.report_agent import Report, ReportAgent, ReportManager, ReportStatus
from app.services.report_provenance import EvidenceRegistry
from app.services.simulation_runner import RunnerStatus
from app.utils.zep_lifecycle import get_graph_readers


@pytest.fixture
def reporting(monkeypatch):
    run = SimpleNamespace(runner_status=RunnerStatus.COMPLETED, execution_id="execution-1",
                          source_graph_id="source-graph", execution_graph_id="execution-graph-1",
                          source_snapshot_sha256="snapshot-1", started_at="start", completed_at="end")
    project = SimpleNamespace(project_id="project-1", graph_id="source-graph",
                              status=ProjectStatus.GRAPH_COMPLETED, simulation_requirement="Scenario")
    queued, failures, constructed = [], [], []
    monkeypatch.setattr(api, "SimulationManager", lambda: SimpleNamespace(get_simulation=lambda _: SimpleNamespace(
        project_id="project-1", graph_id="source-graph")))
    monkeypatch.setattr(api.ProjectManager, "get_project", lambda _: project)
    monkeypatch.setattr(api.SimulationRunner, "get_run_state", lambda _: run)
    monkeypatch.setattr(api.ZepGraphMemoryManager, "get_updater", lambda _: None)
    monkeypatch.setattr(api.ReportManager, "get_report_by_simulation", lambda _: None)
    monkeypatch.setattr(api.ReportManager, "save_report", lambda _: None)
    class Tasks:
        def enqueue(self, **kwargs):
            queued.append(kwargs)
            return "task-1"
        def get_task(self, _):
            return SimpleNamespace(metadata=queued[-1]["metadata"], status=TaskStatus.PENDING, error_code=None, message="")
        def update_task(self, *args, **kwargs): pass
        def publication_guard(self): return nullcontext()
        def complete_task(self, *args, **kwargs): pass
        def fail_task(self, _, error): failures.append(error)
    monkeypatch.setattr(api, "TaskManager", Tasks)
    class Agent:
        def __init__(self, **kwargs):
            constructed.append(kwargs)
            assert get_graph_readers("source-graph")
            assert get_graph_readers(kwargs["graph_id"])
        def generate_report(self, **kwargs):
            return SimpleNamespace(report_id=kwargs["report_id"], status=ReportStatus.COMPLETED)
    monkeypatch.setattr(api, "ReportAgent", Agent)
    app = Flask(__name__)
    def submit():
        with app.test_request_context("/api/report/generate", method="POST", json={"simulation_id": "simulation-1"}):
            result = api.generate_report()
        response, status = result if isinstance(result, tuple) else (result, result.status_code)
        return response.get_json(), status
    return SimpleNamespace(run=run, project=project, queued=queued, failures=failures,
                           constructed=constructed, submit=submit, app=app)


def test_queue_and_worker_use_execution_graph_and_source_reader(reporting):
    body, status = reporting.submit()
    assert status == 200
    parameters = reporting.queued[0]["parameters"]
    assert parameters["graph_id"] == "execution-graph-1"
    assert parameters["execution_graph_id"] == "execution-graph-1"
    assert parameters["source_graph_id"] == "source-graph"
    assert parameters["execution_id"] == "execution-1"
    api.run_report_job("task-1", parameters)
    assert reporting.failures == []
    assert reporting.constructed[0]["graph_id"] == "execution-graph-1"
    assert reporting.constructed[0]["execution_id"] == "execution-1"
    assert reporting.project.graph_id == "source-graph"
    assert get_graph_readers("source-graph") == []
    assert get_graph_readers("execution-graph-1") == []


@pytest.mark.parametrize("field", ["execution_id", "source_graph_id", "execution_graph_id"])
def test_legacy_unbound_run_requires_rerun(reporting, field):
    setattr(reporting.run, field, None)
    body, status = reporting.submit()
    assert status == 409
    assert "rerun" in body["error"].lower()
    assert reporting.queued == []


def test_completed_rerun_with_same_timestamps_invalidates_queued_report(reporting):
    assert reporting.submit()[1] == 200
    reporting.run.execution_id = "execution-2"
    reporting.run.execution_graph_id = "execution-graph-2"
    api.run_report_job("task-1", reporting.queued[0]["parameters"])
    assert len(reporting.failures) == 1
    assert "run changed" in str(reporting.failures[0]).lower()
    assert reporting.constructed == []
    assert get_graph_readers("source-graph") == []


def test_cached_report_from_previous_execution_is_not_reused(reporting, monkeypatch):
    monkeypatch.setattr(api.ReportManager, "get_report_by_simulation", lambda _: SimpleNamespace(
        status=ReportStatus.COMPLETED, report_id="old-report", graph_id="old-execution-graph",
        manifest={"execution_id": "old-execution", "source_graph_id": "source-graph",
                  "execution_graph_id": "old-execution-graph"}))
    body, status = reporting.submit()
    assert status == 200
    assert body["data"]["report_id"] != "old-report"
    assert len(reporting.queued) == 1


def test_report_job_dedupe_is_scoped_to_execution(reporting):
    first, first_status = reporting.submit()
    reporting.run.execution_id = "execution-2"
    reporting.run.execution_graph_id = "execution-graph-2"
    second, second_status = reporting.submit()
    assert first_status == second_status == 200
    assert first["data"]["report_id"] != second["data"]["report_id"]
    assert reporting.queued[0]["dedupe_key"] != reporting.queued[1]["dedupe_key"]


def test_manifest_records_execution_identity_and_only_reads_its_graph(tmp_path, monkeypatch):
    monkeypatch.setattr(api.SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    reads = []
    def evidence(graph_id):
        reads.append(graph_id)
        return [{"source_id": "observation-2", "kind": "simulation", "text": "Current run observation"}]
    agent = ReportAgent("execution-graph-2", "simulation-1", "Scenario",
                        llm_client=SimpleNamespace(model="test"), zep_tools=SimpleNamespace(get_evidence=evidence),
                        execution_id="execution-2", source_graph_id="source-graph", source_snapshot_sha256="snapshot")
    report = Report("report-1", "simulation-1", "execution-graph-2", "Scenario", ReportStatus.PLANNING)
    agent._prepare_evidence(report)
    assert reads == ["execution-graph-2"]
    assert report.manifest["execution_id"] == "execution-2"
    assert report.manifest["execution_graph_id"] == "execution-graph-2"
    assert report.manifest["source_graph_id"] == "source-graph"
    assert report.manifest["source_snapshot_sha256"] == "snapshot"


def test_report_chat_uses_saved_snapshot_without_live_graph_access(monkeypatch):
    registry = EvidenceRegistry("old-execution-graph", "report-old")
    source = registry.add_episode({"source_id": "observation", "kind": "simulation", "text": "Old run only"})
    report = Report("report-old", "simulation-1", "old-execution-graph", "Old assumptions", ReportStatus.COMPLETED,
                    markdown_content="Saved report", evidence=registry.snapshot(), manifest={"execution_id": "old-execution"})
    prompts = []
    def chat(**kwargs):
        prompts.append(kwargs["messages"])
        return "Simulation observation: old run only [[source:" + source["citation_id"] + "]]."
    agent = ReportAgent(report.graph_id, report.simulation_id, report.simulation_requirement,
                        llm_client=SimpleNamespace(chat=chat), zep_tools=SimpleNamespace())
    monkeypatch.setattr(agent, "_execute_tool", lambda *args: pytest.fail("Snapshot chat cannot query live tools"))
    result = agent.chat_from_report(report, "What happened?")
    assert "Old run only" in str(prompts)
    assert result["report_id"] == "report-old"
    assert result["execution_id"] == "old-execution"
    assert result["tool_calls"] == []
    assert "#source-" in result["response"]


def test_chat_api_selects_explicit_report_without_latest_run_graph(reporting, monkeypatch):
    report = Report("report-old", "simulation-1", "old-execution-graph", "Old assumptions", ReportStatus.COMPLETED,
                    manifest={"execution_id": "old-execution", "source_graph_id": "source-graph"})
    monkeypatch.setattr(api.ReportManager, "get_report", lambda _: report)
    calls = []
    class SnapshotAgent:
        def __init__(self, **kwargs): calls.append(kwargs)
        def chat_from_report(self, selected_report, **kwargs):
            assert selected_report is report
            return {"response": "saved", "report_id": selected_report.report_id}
    monkeypatch.setattr(api, "ReportAgent", SnapshotAgent)
    with reporting.app.test_request_context("/api/report/chat", method="POST", json={
        "simulation_id": "simulation-1", "report_id": "report-old", "message": "Explain"}):
        result = api.chat_with_report_agent()
    assert result.status_code == 200
    assert calls[0]["graph_id"] == "old-execution-graph"
    assert calls[0]["simulation_requirement"] == "Old assumptions"


def test_latest_report_selection_uses_creation_time_not_directory_order(tmp_path, monkeypatch):
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(tmp_path))
    older = Report("report-old", "simulation-1", "old-graph", "Old", ReportStatus.COMPLETED,
                   created_at="2026-01-01T00:00:00")
    newer = Report("report-new", "simulation-1", "new-graph", "New", ReportStatus.COMPLETED,
                   created_at="2026-01-02T00:00:00")
    ReportManager.save_report(older)
    ReportManager.save_report(newer)
    monkeypatch.setattr("app.services.report_agent.os.listdir", lambda _: ["report-old", "report-new"])
    assert ReportManager.get_report_by_simulation("simulation-1").report_id == "report-new"


def test_status_and_by_simulation_do_not_claim_old_report_matches_new_run(reporting, monkeypatch):
    old_report = Report("report-old", "simulation-1", "old-graph", "Old", ReportStatus.COMPLETED,
                        manifest={"execution_id": "old-execution", "source_graph_id": "source-graph",
                                  "execution_graph_id": "old-graph"})
    monkeypatch.setattr(api.ReportManager, "get_report_by_simulation", lambda _: old_report)
    with reporting.app.test_request_context("/api/report/generate/status", method="POST", json={"simulation_id": "simulation-1"}):
        response, status = api.get_generate_status()
    assert status == 400
    assert "data" not in response.get_json()
    with reporting.app.test_request_context("/api/report/by-simulation/simulation-1"):
        response, status = api.get_report_by_simulation("simulation-1")
    assert status == 404
    with reporting.app.test_request_context("/api/report/check/simulation-1"):
        response = api.check_report_status("simulation-1")
    assert response.get_json()["data"]["interview_unlocked"] is False


def test_chat_rejects_report_from_another_simulation(reporting, monkeypatch):
    report = Report("report-other", "simulation-other", "other-graph", "Other", ReportStatus.COMPLETED)
    monkeypatch.setattr(api.ReportManager, "get_report", lambda _: report)
    with reporting.app.test_request_context("/api/report/chat", method="POST", json={
        "simulation_id": "simulation-1", "report_id": "report-other", "message": "Explain"}):
        response, status = api.chat_with_report_agent()
    assert status == 404
    assert reporting.constructed == []


def test_snapshot_chat_does_not_initialize_live_graph_provider(monkeypatch):
    monkeypatch.setattr("app.services.report_agent.ZepToolsService", lambda: pytest.fail("No live provider needed"))
    agent = ReportAgent("old-graph", "simulation-1", "Old", llm_client=SimpleNamespace(), snapshot_only=True)
    assert agent.zep_tools is None
