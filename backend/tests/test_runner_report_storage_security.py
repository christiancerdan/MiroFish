import json

import pytest

from app.config import Config
from app.services.report_agent import (
    Report,
    ReportConsoleLogger,
    ReportLogger,
    ReportManager,
    ReportSection,
    ReportStatus,
)
from app.services.simulation_runner import SimulationRunner, SimulationRunState


@pytest.fixture
def storage(tmp_path, monkeypatch):
    reports = tmp_path / "uploads" / "reports"
    simulations = tmp_path / "simulations"
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path / "uploads"))
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(reports))
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(simulations))
    monkeypatch.setattr(SimulationRunner, "_run_states", {})
    return reports, simulations


@pytest.mark.parametrize("identifier", ["../outside", "/absolute", "a/b", r"a\b", ".", "", None])
@pytest.mark.parametrize("operation", ["read", "delete", "progress", "agent_log", "console_log"])
def test_report_storage_rejects_malformed_identifiers(storage, identifier, operation):
    if identifier == "/absolute":
        identifier = str(storage[0].parent.parent / "absolute_outside")
    actions = {
        "read": lambda: ReportManager.get_report(identifier),
        "delete": lambda: ReportManager.delete_report(identifier),
        "progress": lambda: ReportManager.update_progress(identifier, "pending", 0, "start"),
        "agent_log": lambda: ReportLogger(identifier),
        "console_log": lambda: ReportConsoleLogger(identifier),
    }
    with pytest.raises(ValueError):
        actions[operation]()
    assert not storage[0].exists()


@pytest.mark.parametrize("identifier", ["../outside", "/absolute", "a/b", r"a\b", ".", "", None])
@pytest.mark.parametrize("operation", ["read", "write", "actions", "cleanup", "environment", "history"])
def test_runner_storage_rejects_malformed_identifiers(storage, identifier, operation):
    if identifier == "/absolute":
        identifier = str(storage[0].parent.parent / "absolute_outside")
    actions = {
        "read": lambda: SimulationRunner.get_run_state(identifier),
        "write": lambda: SimulationRunner._save_run_state(SimulationRunState(identifier)),
        "actions": lambda: SimulationRunner.get_all_actions(identifier),
        "cleanup": lambda: SimulationRunner.cleanup_simulation_logs(identifier),
        "environment": lambda: SimulationRunner.get_env_status_detail(identifier),
        "history": lambda: SimulationRunner.get_interview_history(identifier),
    }
    with pytest.raises(ValueError):
        actions[operation]()
    assert not storage[1].exists()


def test_runner_cache_cannot_bypass_identifier_validation(storage):
    SimulationRunner._run_states["../outside"] = SimulationRunState("../outside")
    with pytest.raises(ValueError):
        SimulationRunner.get_run_state("../outside")


@pytest.mark.parametrize("service", ["report", "runner"])
def test_storage_rejects_identifier_directory_symlink(storage, tmp_path, service):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = storage[0 if service == "report" else 1]
    root.mkdir(parents=True)
    (root / "safe_id").symlink_to(outside, target_is_directory=True)
    action = ReportManager.get_report if service == "report" else SimulationRunner.get_run_state
    with pytest.raises(ValueError):
        action("safe_id")


@pytest.mark.parametrize("filename,operation", [
    ("progress.json", lambda: ReportManager.get_progress("report_1")),
    ("section_01.md", lambda: ReportManager.get_generated_sections("report_1")),
    ("section_01.md", lambda: ReportManager.save_section("report_1", 1, ReportSection("Title", "new"))),
    ("agent_log.jsonl", lambda: ReportLogger("report_1")),
    ("console_log.txt", lambda: ReportConsoleLogger("report_1")),
])
def test_report_file_symlink_cannot_read_or_overwrite_outside_file(storage, tmp_path, filename, operation):
    folder = storage[0] / "report_1"
    folder.mkdir(parents=True)
    outside = tmp_path / "private.txt"
    outside.write_text("{}", encoding="utf-8")
    (folder / filename).symlink_to(outside)
    with pytest.raises(ValueError):
        operation()
    assert outside.read_text(encoding="utf-8") == "{}"


@pytest.mark.parametrize("filename,operation", [
    ("run_state.json", lambda: SimulationRunner.get_run_state("sim_1")),
    ("run_state.json", lambda: SimulationRunner._save_run_state(SimulationRunState("sim_1"))),
    ("env_status.json", lambda: SimulationRunner.get_env_status_detail("sim_1")),
    ("twitter_simulation.db", lambda: SimulationRunner.get_interview_history("sim_1")),
])
def test_runner_file_symlink_cannot_access_outside_file(storage, tmp_path, filename, operation):
    folder = storage[1] / "sim_1"
    folder.mkdir(parents=True)
    outside = tmp_path / "private.txt"
    outside.write_text("{}", encoding="utf-8")
    (folder / filename).symlink_to(outside)
    with pytest.raises(ValueError):
        operation()
    assert outside.read_text(encoding="utf-8") == "{}"


@pytest.mark.parametrize("operation", [SimulationRunner.get_all_actions, SimulationRunner.cleanup_simulation_logs])
def test_runner_platform_symlink_cannot_read_or_delete_outside_actions(storage, tmp_path, operation):
    folder = storage[1] / "sim_1"
    folder.mkdir(parents=True)
    outside = tmp_path / "private"
    outside.mkdir()
    actions = outside / "actions.jsonl"
    actions.write_text('{"agent_id": 99, "action_type": "private"}\n', encoding="utf-8")
    (folder / "twitter").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        operation("sim_1")
    assert actions.read_text(encoding="utf-8") == '{"agent_id": 99, "action_type": "private"}\n'


@pytest.mark.parametrize("operation", [ReportManager.get_report, ReportManager.delete_report])
def test_legacy_report_symlink_cannot_access_outside_file(storage, tmp_path, operation):
    storage[0].mkdir(parents=True)
    outside = tmp_path / "private.json"
    outside.write_text("{}", encoding="utf-8")
    (storage[0] / "report_1.json").symlink_to(outside)
    with pytest.raises(ValueError):
        operation("report_1")
    assert outside.read_text(encoding="utf-8") == "{}"


def test_missing_storage_reads_do_not_create_directories(storage):
    assert ReportManager.get_report("report_missing") is None
    assert ReportManager.list_reports() == []
    assert ReportManager.get_report_by_simulation("sim_missing") is None
    assert SimulationRunner.get_run_state("sim_missing") is None
    assert SimulationRunner.get_all_actions("sim_missing") == []
    assert not storage[0].exists()
    assert not storage[1].exists()


def test_valid_report_and_simulation_storage_round_trip(storage):
    report = Report("report_1", "sim_1", "graph_1", "Question", ReportStatus.COMPLETED, markdown_content="# Answer")
    ReportManager.save_report(report)
    ReportManager.update_progress("report_1", "completed", 100, "Done")
    assert ReportManager.get_report("report_1").markdown_content == "# Answer"
    assert ReportManager.get_progress("report_1")["progress"] == 100
    SimulationRunner._save_run_state(SimulationRunState("sim_1", current_round=3))
    SimulationRunner._run_states.clear()
    assert SimulationRunner.get_run_state("sim_1").current_round == 3
    assert json.loads((storage[1] / "sim_1" / "run_state.json").read_text())["current_round"] == 3
