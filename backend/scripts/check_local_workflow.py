#!/usr/bin/env python3
"""Run one paid, synthetic local-memory workflow against the configured provider.

This is an opt-in live check, not a unit test. It uses temporary application data,
one Reddit round, bounded model usage, and saves a secret-free proof JSON. The
configured model may be hosted even though graph memory is local. Run with the
simulation Python environment and --evidence-path /path/to/proof.json.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import time

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

DOCUMENT = (
    "Synthetic scenario. Alice is a Reddit user who favors a Saturday street cleanup. "
    "Bob is a Reddit user who prefers Sunday because he works Saturdays. "
    "Alice and Bob know each other and discuss neighborhood events online. "
    "They are the only participants. This scenario is fictional."
)
REQUIREMENT = (
    "Explore how Alice and Bob discuss whether their fictional street cleanup should happen "
    "Saturday or Sunday. Simulate exactly these two participants on Reddit for one round. "
    "Both are active at hour 0, have activity_level 1, and contribute a short post immediately. "
    "Use two initial posts, one from each participant stating the preferences in the source. "
    "Create a compact three-section report distinguishing source evidence, simulated observations, "
    "and assumptions. Cite stored evidence, state limitations, and do not claim calibrated probabilities."
)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-path", required=True)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--max-calls", type=int, default=60)
    args = parser.parse_args()
    if not 60 <= args.timeout <= 3600 or not 1 <= args.max_calls <= 150:
        parser.error("timeout must be 60..3600 and max-calls 1..150")

    runtime = Path(tempfile.mkdtemp(prefix="mirofish-live-workflow-"))
    evidence_path = Path(args.evidence_path).resolve()
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    owner_key = secrets.token_hex(32)
    # Environment must be set before importing Config or any data-owning service.
    settings = {
        "MIROFISH_DATA_DIR": str(runtime / "data"),
        "LOCAL_GRAPH_DB_PATH": str(runtime / "data" / "memory.sqlite3"),
        "JOBS_DB_PATH": str(runtime / "data" / "jobs.sqlite3"),
        "BUDGET_DB_PATH": str(runtime / "data" / "budgets.sqlite3"),
        "GRAPH_BACKEND": "local", "ZEP_API_KEY": "", "MIROFISH_ACCESS_KEY": owner_key,
        "SECRET_KEY": secrets.token_hex(32), "FLASK_DEBUG": "false", "JOBS_AUTOSTART": "false",
        "JOB_WORKERS": "1", "SIMULATION_PYTHON": sys.executable,
        "SIMULATION_MAX_WALL_SECONDS": "480", "SIMULATION_MAX_CPU_SECONDS": "240",
        "BUDGET_MAX_CALLS": str(args.max_calls), "BUDGET_MAX_TOKENS": "600000",
        "BUDGET_MAX_OUTPUT_TOKENS": "180000", "BUDGET_MAX_WALL_SECONDS": str(args.timeout),
        "REPORT_AGENT_MAX_TOOL_CALLS": "2", "REPORT_AGENT_MAX_REFLECTION_ROUNDS": "1",
    }
    os.environ.update(settings)
    proof = {"started_at": utc_now(), "status": "running", "runtime_dir": str(runtime),
             "synthetic_document_sha256": digest(DOCUMENT), "stages": [],
             "limits": {"max_calls": args.max_calls, "max_wall_seconds": args.timeout,
                        "simulation_rounds": 1, "platform": "reddit"}}
    deadline = time.monotonic() + args.timeout
    output = sys.stdout
    log_path = runtime / "workflow.log"
    app = client = dispatcher = None
    project_id = simulation_id = graph_id = report_id = None

    def save():
        proof["updated_at"] = utc_now()
        temporary = evidence_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(proof, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(evidence_path)

    def mark(stage, **values):
        proof["stage"] = stage
        record = {"stage": stage, "at": utc_now(), **values}
        proof["stages"].append(record)
        save()
        print(json.dumps(record, ensure_ascii=False), file=output, flush=True)

    def request(method, path, **kwargs):
        if time.monotonic() > deadline:
            raise TimeoutError("Workflow exceeded its deadline")
        response = getattr(client, method)(path, **kwargs)
        payload = response.get_json(silent=True) or {}
        if response.status_code >= 400 or not payload.get("success", False):
            proof["last_http_failure"] = {"path": path, "status": response.status_code,
                                          "error_code": payload.get("error_code"),
                                          "error": payload.get("error")}
            raise RuntimeError(f"Workflow HTTP request failed at {path}: {response.status_code}")
        return payload.get("data", {})

    def budget():
        return request("get", f"/api/budget/{project_id}") if project_id else {}

    def wait_job(task_id, stage):
        last = None
        while time.monotonic() < deadline:
            task = request("get", f"/api/graph/task/{task_id}")
            status = task.get("status")
            summary = (status, task.get("progress"))
            if summary != last:
                mark(stage, task_id=task_id, task_status=status, progress=task.get("progress"))
                last = summary
            if status == "completed":
                proof.setdefault("jobs", []).append({key: task.get(key) for key in
                    ("task_id", "status", "handler", "attempts", "created_at", "updated_at")})
                return task.get("result") or {}
            if status in {"failed", "cancelled", "canceled", "interrupted", "budget_exceeded"}:
                proof["failed_job"] = {key: task.get(key) for key in ("task_id", "status", "error", "error_code")}
                raise RuntimeError(f"Durable {stage} job ended as {status}")
            time.sleep(2)
        raise TimeoutError(f"Timed out waiting for {stage}")

    with log_path.open("w", encoding="utf-8") as logs, redirect_stdout(logs), redirect_stderr(logs):
        try:
            from app import create_app
            from app.config import Config
            from app.services.job_dispatcher import start_job_dispatcher
            from app.services.simulation_manager import SimulationManager
            from app.services.simulation_runner import SimulationRunner
            from app.services.zep_graph_memory_updater import ZepGraphMemoryManager
            from app.services.zep_tools import ZepToolsService
            from app.utils.zep import get_zep_client
            from app.utils.llm_provider import settings_from_config
            from app.utils.budget import BudgetStore

            expected_simulations = (runtime / "data" / "simulations").resolve()
            assert Path(SimulationManager.SIMULATION_DATA_DIR).resolve() == expected_simulations, "SimulationManager ignores isolated data directory"
            assert Path(SimulationRunner.RUN_STATE_DIR).resolve() == expected_simulations, "SimulationRunner ignores isolated data directory"
            model = settings_from_config(Config)
            proof["provider"] = {"provider": model.provider, "model": model.model,
                                 "base_url": model.base_url, "graph_backend": Config.GRAPH_BACKEND}
            assert model.base_url.startswith(("http://127.0.0.1:", "http://localhost:")), "This proof expects the authorized local Ollama route"
            assert Config.ZEP_API_KEY == ""
            app = create_app()
            app.config.update(TESTING=False)
            client = app.test_client()
            client.environ_base["HTTP_AUTHORIZATION"] = "Bearer " + owner_key
            client.environ_base["HTTP_ACCEPT_LANGUAGE"] = "en"
            dispatcher = start_job_dispatcher(app)
            mark("ontology_started")
            created = request("post", "/api/graph/ontology/generate", data={
                "project_name": "Synthetic local workflow proof",
                "simulation_requirement": REQUIREMENT,
                "additional_context": "The source has exactly two fictional individual participants, Alice and Bob, both RedditUser entities. Include KNOWS with source_targets RedditUser to RedditUser so their explicit acquaintance relationship is represented. Do not invent extra participants.",
                "files": (io.BytesIO(DOCUMENT.encode()), "synthetic-neighborhood.txt"),
            }, content_type="multipart/form-data")
            project_id = created["project_id"]
            proof["project_id"] = project_id
            mark("ontology_completed", entity_types=len(created["ontology"]["entity_types"]), budget=budget()["usage"])
            queued = request("post", "/api/graph/build", json={"project_id": project_id, "chunk_size": 1000, "chunk_overlap": 0})
            result = wait_job(queued["task_id"], "graph_build")
            graph_id = result.get("graph_id") or request("get", f"/api/graph/project/{project_id}")["graph_id"]
            proof["graph_id"] = graph_id
            graph_data = request("get", f"/api/graph/data/{graph_id}")
            assert graph_data["node_count"] == 2 and graph_data["edge_count"] >= 1, "Extraction did not produce the two-person graph"
            mark("graph_completed", nodes=graph_data["node_count"], edges=graph_data["edge_count"], budget=budget()["usage"])
            created_sim = request("post", "/api/simulation/create", json={"project_id": project_id, "enable_twitter": False, "enable_reddit": True})
            simulation_id = created_sim["simulation_id"]
            proof["simulation_id"] = simulation_id
            queued = request("post", "/api/simulation/prepare", json={"simulation_id": simulation_id, "parallel_profile_count": 1})
            wait_job(queued["task_id"], "simulation_prepare")
            config = request("get", f"/api/simulation/{simulation_id}/config")
            mark("prepare_completed", budget=budget()["usage"])
            request("post", "/api/simulation/start", json={"simulation_id": simulation_id, "platform": "reddit", "max_rounds": 1, "enable_graph_memory_update": True})
            mark("simulation_started")
            last = None
            while time.monotonic() < deadline:
                state = request("get", f"/api/simulation/{simulation_id}/run-status")
                observed = (state.get("runner_status"), state.get("reddit_completed"), state.get("total_actions_count"))
                if observed != last:
                    mark("simulation_running", runner_status=observed[0], platform_complete=observed[1], actions=observed[2])
                    last = observed
                if state.get("runner_status") in {"failed", "interrupted"}:
                    proof["failed_run"] = state
                    raise RuntimeError("Simulation worker failed")
                if state.get("reddit_completed"):
                    break
                time.sleep(2)
            else:
                raise TimeoutError("Simulation did not complete its Reddit round")
            request("post", "/api/simulation/close-env", json={"simulation_id": simulation_id, "timeout": 30})
            while time.monotonic() < deadline:
                state = request("get", f"/api/simulation/{simulation_id}/run-status")
                updater = ZepGraphMemoryManager.get_updater(simulation_id)
                if state.get("runner_status") in {"completed", "stopped"} and updater is None:
                    break
                if state.get("runner_status") in {"failed", "interrupted"}:
                    proof["failed_run"] = state
                    raise RuntimeError("Simulation or memory drain failed")
                time.sleep(2)
            else:
                raise TimeoutError("Simulation memory did not drain")
            sources = ZepToolsService().get_evidence(graph_id)
            simulated_sources = [s for s in sources if s["kind"] == "simulation"]
            assert state.get("total_actions_count", 0) > 0, "Simulation emitted no actions"
            assert simulated_sources, "No simulation observations reached local memory"
            proof["simulation"] = {"status": state["runner_status"], "actions": state["total_actions_count"],
                                   "completed_round": state["reddit_current_round"], "source_episodes": len(simulated_sources)}
            mark("simulation_completed", **proof["simulation"], budget=budget()["usage"])
            queued = request("post", "/api/report/generate", json={"simulation_id": simulation_id})
            report_id = queued["report_id"]
            proof["report_id"] = report_id
            wait_job(queued["task_id"], "report_generate")
            report = request("get", f"/api/report/{report_id}")
            assert report["status"] == "completed"
            records = report["evidence"]["sources"]
            assert {"source_fact", "simulation_observation"}.issubset({s["kind"] for s in records})
            for source in records:
                assert source["content_sha256"] == digest(source["text"]), "Source snapshot hash mismatch"
                fetched = request("get", source["url"])
                assert fetched["content_sha256"] == source["content_sha256"], "Evidence endpoint differs from report snapshot"
            uncertainty = report["uncertainty"]
            assert uncertainty["calibrated"] is False and uncertainty["limitations"]
            manifest = report["manifest"]
            evidence_hash = digest(json.dumps(report["evidence"], ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            assert manifest["input_hashes"]["evidence_snapshot"] == evidence_hash
            markdown = report["markdown_content"]
            known = {s["citation_id"] for s in records}
            cited = set(re.findall(r"\]\(#source-([^)]+)\)", markdown))
            assert cited and cited <= known, "Report citations do not resolve to stored evidence"
            final_budget = budget()
            persisted_budget = BudgetStore(str(runtime / "data" / "budgets.sqlite3")).get(project_id)
            assert final_budget["usage"] == persisted_budget["usage"]
            assert 0 < final_budget["usage"]["calls"] <= args.max_calls
            proof["budget"] = final_budget
            proof["report"] = {"status": report["status"], "source_count": len(records), "cited_source_count": len(cited),
                               "markdown_sha256": digest(markdown), "evidence_snapshot_sha256": evidence_hash,
                               "uncertainty": uncertainty, "manifest": manifest}
            (runtime / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            proof["status"] = "passed"
            mark("workflow_completed", source_count=len(records), cited_source_count=len(cited), model_calls=final_budget["usage"]["calls"])
        except BaseException as error:
            proof["status"] = "failed"
            proof["failure"] = {"type": type(error).__name__, "message": str(error), "stage": proof.get("stage")}
            if project_id and client:
                try:
                    proof["budget"] = budget()
                except Exception:
                    pass
            mark("workflow_failed", failure_type=type(error).__name__, message=str(error))
            import traceback
            traceback.print_exc()
        finally:
            if simulation_id:
                try:
                    from app.services.simulation_runner import SimulationRunner
                    state = SimulationRunner.get_run_state(simulation_id)
                    if state and state.runner_status.value not in {"completed", "stopped", "failed", "interrupted"}:
                        SimulationRunner.stop_simulation(simulation_id, timeout=30)
                except Exception as cleanup_error:
                    proof["cleanup_error_type"] = type(cleanup_error).__name__
            if dispatcher:
                dispatcher.stop(wait=False)
            proof["finished_at"] = utc_now()
            proof["log_path"] = str(log_path)
            proof["limitations"] = [
                "Uses a local Ollama daemon route to the configured model; a -cloud model performs hosted inference.",
                "Proves workflow and citation/hash integrity on a synthetic fixture, not forecast accuracy or semantic citation entailment.",
                "Only Reddit and one simulation round are exercised; Twitter and large datasets are outside this proof.",
            ]
            save()
    print(json.dumps({"status": proof["status"], "proof": str(evidence_path), "runtime_dir": str(runtime)}), file=output, flush=True)
    return 0 if proof["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
