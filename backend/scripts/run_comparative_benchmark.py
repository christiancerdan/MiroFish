#!/usr/bin/env python3
"""Run ONE opted-in live comparative trial; never open an outcomes file.

Use the simulation Python environment. Each invocation gets new application data
and makes paid calls to the configured provider. Inputs and a frozen protocol are
required. A failed trial is written explicitly and is never retried or resumed.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import secrets
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib

BACKEND = Path(__file__).resolve().parents[1]
SOURCE_SCOPE = "backend-app-scripts-vendor-runtime-lock-locales-v2"


def implementation_source_sha256():
    """Bind executed source, local runtime assets, and dependency declarations."""
    paths = [path for directory in ("app", "scripts")
             for path in (BACKEND / directory).rglob("*.py")]
    vendor = BACKEND / "vendor" / "camel-oasis"
    paths += [path for path in (vendor / "oasis").rglob("*")
              if path.is_file() and path.suffix in {".py", ".sql"}]
    paths += [vendor / "pyproject.toml", vendor / "UPSTREAM.json",
              BACKEND / "pyproject.toml", BACKEND / "uv.lock"]
    paths += list((BACKEND.parent / "locales").glob("*.json"))
    inventory = "\n".join(str(path.relative_to(BACKEND.parent)) + ":" +
                          hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths))
    return sha256_text(SOURCE_SCOPE + "\n" + inventory)


def verify_vendored_runtime():
    """The simulation wheel must contain the exact frozen vendored runtime."""
    vendor = BACKEND / "vendor" / "camel-oasis"
    expected_version = tomllib.loads((vendor / "pyproject.toml").read_text())["project"]["version"]
    installed = importlib.metadata.distribution("camel-oasis")
    if installed.version != expected_version:
        raise ValueError("Installed simulation runtime version differs from vendored source")
    source_files = {path.relative_to(vendor) for path in (vendor / "oasis").rglob("*")
                    if path.is_file() and path.suffix in {".py", ".sql"}}
    installed_files = {Path(str(path)) for path in installed.files or []
                       if str(path).startswith("oasis/") and Path(str(path)).suffix in {".py", ".sql"}}
    if installed_files != source_files:
        raise ValueError("Installed simulation runtime inventory differs from vendored source")
    inventory = []
    for path in sorted(source_files):
        expected = hashlib.sha256((vendor / path).read_bytes()).hexdigest()
        if hashlib.sha256(Path(installed.locate_file(path)).read_bytes()).hexdigest() != expected:
            raise ValueError("Installed simulation runtime content differs from vendored source")
        inventory.append(f"{path}:{expected}")
    return {"distribution": "camel-oasis", "version": installed.version,
            "files_verified": len(inventory), "source_sha256": sha256_text("\n".join(inventory))}


PERSONAS = (
    {"name": "Curious Reader", "description": "A fictional curiosity-led reader who likes surprising, understandable stories."},
    {"name": "Skeptical Reader", "description": "A fictional evidence-oriented reader who distrusts exaggerated or vague claims."},
    {"name": "Busy Reader", "description": "A fictional time-poor generalist who values clear relevance and quickly understood benefits."},
)
ASSESSOR_SYSTEM = (
    "You are assessing two headlines shown with the same image in a randomized publisher test. "
    "Use only the provided source and, when supplied, the explicitly simulated report. "
    "Do not search, recall historical test results, or invent observed click counts. "
    "Estimate the probability that headline A had the higher observed click-through rate. "
    "This probability is an uncalibrated model estimate, not an empirical survey result. "
    "Fictional reader personas and simulation events are assumptions, not real audience evidence. "
    "Return exactly one JSON object with exactly two keys: probability_a (a finite number from 0 to 1) "
    "and rationale (a short nonempty string). Do not use Markdown fences or additional fields."
)
REQUIREMENT = (
    "Compare which of headline A and headline B would attract more clicks in the stated publisher audience. "
    "Use exactly the three named fictional readers from the source as individual Reader entities; "
    "headlines, audience groups, topics, and publishers are not simulation participants. "
    "Each reader is an explicitly declared assumption, not a sampled real person. "
    "Explore the choice on Reddit for one round with all three readers active at hour 0. "
    "Do not search or recall historical results or invent click counts. "
    "Produce a compact three-section report separating source facts, simulated observations, and assumptions. "
    "Cite stored evidence, discuss limitations and the unavailable shared image, and do not claim calibrated probabilities."
)


def benchmark_configuration():
    return {
        "simulation_rounds": 1, "simulation_platform": "reddit",
        "assumed_personas": [dict(persona) for persona in PERSONAS],
        "assessor_temperature": 0.3, "assessor_max_tokens": 4096,
        "assessor_attempts": 1, "assessor_response_format": "json_object",
        "report_max_tool_calls": 2, "report_max_reflection_rounds": 1,
        "max_calls": 35, "wall_timeout_seconds": 900,
        "participant_selection": "exact_source_persona_names_actual_graph_labels_v2",
        "assessor_system_sha256": sha256_text(ASSESSOR_SYSTEM),
        "simulation_requirement_sha256": sha256_text(REQUIREMENT),
        "source_prompt_template_sha256": sha256_text(build_source_document({
            "headline_a": "<A>", "headline_b": "<B>", "audience_context": "<AUDIENCE>",
            "shared_image_unavailable": True})),
        "source_format_version": 1,
    }


def build_source_document(case):
    allowed = {"headline_a", "headline_b", "audience_context", "shared_image_unavailable"}
    if not isinstance(case, dict) or set(case) != allowed:
        raise ValueError("Prompt case must contain exactly the approved source fields")
    if case["shared_image_unavailable"] is not True:
        raise ValueError("This protocol requires an unavailable shared image")
    if any(not isinstance(case[key], str) or not case[key].strip()
           for key in ("headline_a", "headline_b", "audience_context")):
        raise ValueError("Headline and audience source fields must be nonempty text")
    audience = json.dumps(case["audience_context"], ensure_ascii=False)
    headlines = json.dumps({"headline_a": case["headline_a"], "headline_b": case["headline_b"]},
                           ensure_ascii=False, indent=2)
    personas = "\n".join(f"- {person['name']}: {person['description']}" for person in PERSONAS)
    return (
        "SOURCE MATERIAL FOR A RETROSPECTIVE HEADLINE COMPARISON\n"
        f"Audience context: {audience}\nHeadlines (quoted source text):\n{headlines}\n"
        "Both headline variants used the same image. The shared image is unavailable to both methods. "
        "No observed clicks, impressions, dates, source identifiers, or historical winner are supplied.\n\n"
        "DECLARED ASSUMPTIONS\n"
        "The following three fictional readers are fixed assumptions for this exercise. "
        "They are not recovered audience demographics or a representative population sample. "
        "They can encounter one another in the same fictional Reddit discussion.\n"
        f"{personas}\n\n"
        "DECISION TASK\nEstimate which headline had the higher observed click-through rate, "
        "using only this material. Simulation may suggest hypotheses but does not add real audience evidence."
    )


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON keys are invalid")
        result[key] = value
    return result


def select_participant_types(graph):
    """Preserve fixed people while accepting MiroFish's generated type names.

    The production ontology prompt defines multiple entity types. A declared
    source person may therefore be a CuriousReader, Person, or another label;
    the benchmark must not require an invented literal Reader label. Names are
    exact source identities, not fuzzy matches or inferred replacement people.
    """
    expected = {persona["name"] for persona in PERSONAS}
    nodes = graph.get("nodes", [])
    selected = [node for node in nodes if node.get("name") in expected]
    if len(selected) != len(expected) or {node.get("name") for node in selected} != expected:
        raise RuntimeError("Graph does not contain exactly one node per declared source persona")
    types = set()
    for node in selected:
        labels = [label for label in node.get("labels", []) if label not in {"Entity", "Node"}]
        if not labels or any(not isinstance(label, str) or not label for label in labels):
            raise RuntimeError("A declared source persona has no usable generated entity type")
        types.update(labels)
    admitted = [node for node in nodes if types.intersection(node.get("labels", []))]
    if len(admitted) != len(expected) or {node.get("name") for node in admitted} != expected:
        raise RuntimeError("Generated entity types would admit participants outside the declared source personas")
    return sorted(types)


def validate_participant_names(agents):
    expected = {persona["name"] for persona in PERSONAS}
    if len(agents) != len(expected) or {agent.get("entity_name") for agent in agents} != expected:
        raise RuntimeError("Prepared participants do not match the three declared source personas")


def parse_prediction(content):
    if not isinstance(content, str):
        raise ValueError("Prediction must be JSON text")
    value = json.loads(content, object_pairs_hook=_unique_object)
    if not isinstance(value, dict) or set(value) != {"probability_a", "rationale"}:
        raise ValueError("Prediction fields do not match the frozen schema")
    probability = value["probability_a"]
    if type(probability) not in {int, float} or not math.isfinite(probability) or not 0 <= probability <= 1:
        raise ValueError("Prediction probability must be a finite number in [0,1]")
    if not isinstance(value["rationale"], str) or not value["rationale"].strip():
        raise ValueError("Prediction rationale must be nonempty text")
    return {"probability_a": float(probability), "rationale": value["rationale"].strip()}


def assess_once(llm, source_document, *, report=None):
    user_content = "BEGIN IDENTICAL SOURCE MATERIAL\n" + source_document + "\nEND SOURCE MATERIAL"
    if report is not None:
        user_content += (
            "\n\nBEGIN SIMULATED REPORT\nThis report contains generated assumptions and observations "
            "from fictional agents. It contains no additional observed audience outcomes.\n"
            + report + "\nEND SIMULATED REPORT"
        )
    response = llm._create_completion(
        messages=[{"role": "system", "content": ASSESSOR_SYSTEM}, {"role": "user", "content": user_content}],
        temperature=0.3, max_tokens=4096, response_format={"type": "json_object"},
    )
    choices = getattr(response, "choices", None) or []
    if len(choices) != 1 or getattr(choices[0], "finish_reason", None) != "stop":
        raise ValueError("Assessor response did not complete normally")
    return parse_prediction(getattr(getattr(choices[0], "message", None), "content", None))


def prediction_attempt(callback):
    try:
        prediction = callback()
        return {"status": "ok", **prediction, "error": None}
    except Exception as error:
        # Provider errors can include request details. Public results retain only
        # an error class; the private runtime log supplies diagnostic context.
        return {"status": "failed", "probability_a": None, "error": type(error).__name__}


def save_json(path, value):
    encoded = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)


def make_runtime(path=None):
    if path is None:
        path = Path(tempfile.mkdtemp(prefix="mirofish-comparative-"))
    path = Path(path).resolve()
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError("A trial requires a new or empty runtime directory")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def usage_record(usage):
    return {"calls": usage.get("calls", 0), "total_tokens": usage.get("tokens", 0),
            "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0),
            "cost_usd": usage.get("estimated_cost_usd")}


def total_usage(budgets):
    """Account for a project created by an API call that failed before its reply."""
    keys = ("calls", "tokens", "input_tokens", "output_tokens")
    usage = {key: sum(budget["usage"][key] for budget in budgets) for key in keys}
    costs = [budget["usage"].get("estimated_cost_usd") for budget in budgets]
    usage["estimated_cost_usd"] = sum(costs) if costs and all(cost is not None for cost in costs) else None
    return usage_record(usage)


def benchmark_module():
    # Importing app.services normally imports Config, before trial paths can be
    # installed. The scoring module is deliberately pure standard-library code.
    spec = importlib.util.spec_from_file_location(
        "mirofish_comparative_contract", BACKEND / "app" / "services" / "comparative_benchmark.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sha256_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class Trial:
    def __init__(self, args, source, protocol, runtime, output):
        self.args, self.source, self.protocol = args, source, protocol
        self.runtime, self.output = runtime, output
        self.deadline = time.monotonic() + args.timeout
        self.started = time.monotonic()
        self.client = self.dispatcher = None
        self.project_id = self.simulation_id = self.graph_id = None
        self.budget_id = "bench_" + secrets.token_hex(10)
        self.owner_key = secrets.token_hex(32)
        self.proof = {"started_at": utc_now(), "runtime_dir": str(runtime), "stages": [],
                      "source_document_sha256": sha256_text(source),
                      "case_id": args.case_id, "method": args.method, "repeat": args.repeat,
                      "configuration": benchmark_configuration(),
                      "limits": {"max_calls": args.max_calls, "timeout_seconds": args.timeout}}
        self.proof["prompt_contract"] = {"assessor_system": ASSESSOR_SYSTEM,
                                         "simulation_requirement": REQUIREMENT}

    def configure(self):
        data = self.runtime / "data"
        settings = {
            "MIROFISH_DATA_DIR": str(data), "LOCAL_GRAPH_DB_PATH": str(data / "memory.sqlite3"),
            "JOBS_DB_PATH": str(data / "jobs.sqlite3"), "BUDGET_DB_PATH": str(data / "budgets.sqlite3"),
            "MIROFISH_BUDGET_DB_PATH": str(data / "budgets.sqlite3"), "MIROFISH_BUDGET_RUN_ID": "",
            "GRAPH_BACKEND": "local", "ZEP_API_KEY": "", "MIROFISH_ACCESS_KEY": self.owner_key,
            "SECRET_KEY": secrets.token_hex(32), "FLASK_DEBUG": "false", "JOBS_AUTOSTART": "false",
            "JOB_WORKERS": "1", "SIMULATION_PYTHON": sys.executable,
            "SIMULATION_MAX_WALL_SECONDS": str(min(self.args.timeout, 480)), "SIMULATION_MAX_CPU_SECONDS": "240",
            "BUDGET_MAX_CALLS": str(self.args.max_calls), "BUDGET_MAX_TOKENS": "600000",
            "BUDGET_MAX_OUTPUT_TOKENS": "180000", "BUDGET_MAX_WALL_SECONDS": str(self.args.timeout),
            "REPORT_AGENT_MAX_TOOL_CALLS": "2", "REPORT_AGENT_MAX_REFLECTION_ROUNDS": "1",
        }
        # A boost model would violate the same-model comparison even when the
        # operator's normal application .env enables one.
        settings.update({"LLM_BOOST_" + key: "" for key in
                         ("PROVIDER", "API_KEY", "BASE_URL", "MODEL_NAME", "TOKEN_LIMIT")})
        os.environ.update(settings)
        sys.path.insert(0, str(BACKEND))

    def mark(self, stage, **values):
        self.proof["stage"] = stage
        event = {"stage": stage, "at": utc_now(), **values}
        self.proof["stages"].append(event)
        save_json(self.runtime / "proof.json", self.proof)
        print(json.dumps({"case_id": self.args.case_id, "method": self.args.method,
                          "repeat": self.args.repeat, **event}, ensure_ascii=False), file=self.output, flush=True)

    def request(self, method, path, **kwargs):
        if time.monotonic() >= self.deadline:
            raise TimeoutError("Trial exceeded its deadline")
        response = getattr(self.client, method)(path, **kwargs)
        payload = response.get_json(silent=True) or {}
        if response.status_code >= 400 or not payload.get("success", False):
            self.proof["last_http_failure"] = {"path": path, "status": response.status_code,
                                               "error_code": payload.get("error_code")}
            raise RuntimeError(f"Trial HTTP request failed: {response.status_code}")
        return payload.get("data", {})

    def wait_job(self, task_id, stage):
        last = None
        while time.monotonic() < self.deadline:
            task = self.request("get", f"/api/graph/task/{task_id}")
            status = task.get("status")
            if status != last:
                self.mark(stage, task_id=task_id, status=status)
                last = status
            if status == "completed":
                self.proof.setdefault("jobs", []).append({key: task.get(key) for key in
                    ("task_id", "status", "handler", "attempts", "created_at", "updated_at")})
                return task.get("result") or {}
            if status in {"failed", "cancelled", "canceled", "interrupted", "budget_exceeded"}:
                self.proof["failed_job"] = {key: task.get(key) for key in ("task_id", "status", "error_code")}
                raise RuntimeError(f"Durable {stage} job failed")
            time.sleep(1)
        raise TimeoutError(f"Timed out waiting for {stage}")

    def model_and_implementation(self):
        from app.config import Config
        from app.utils.llm_provider import settings_from_config
        model = settings_from_config(Config)
        actual = {"provider": model.provider, "model": model.model, "base_url": model.base_url,
                  "token_limit": model.token_limit}
        expected = self.protocol.get("model", {})
        for key in ("provider", "model", "base_url", "token_limit"):
            if key in expected and expected[key] != actual[key]:
                raise ValueError("Configured model does not match the frozen protocol")
        self.proof["model"] = actual
        self.proof["implementation"] = {"python": sys.version.split()[0],
                                         "source_scope": SOURCE_SCOPE,
                                         "backend_python_source_sha256": implementation_source_sha256(),
                                         "runner_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
        if ("implementation_source_sha256" in expected
                and expected["implementation_source_sha256"] != self.proof["implementation"]["backend_python_source_sha256"]):
            raise ValueError("Implementation source does not match the frozen protocol")
        self.proof["implementation"]["vendored_runtime"] = verify_vendored_runtime()
        try:
            self.proof["implementation"]["git_head"] = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=BACKEND, text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            pass

    def run(self):
        self.model_and_implementation()
        from app.utils.budget import BudgetContext
        from app.utils.llm_client import LLMClient
        report = self.mirofish_report() if self.args.method == "mirofish" else None
        self.mark("assessor_started")
        with BudgetContext(self.budget_id):
            result = assess_once(LLMClient(), self.source, report=report)
        self.proof["assessment"] = result
        self.mark("assessor_completed")
        return result

    def mirofish_report(self):
        from app import create_app
        from app.services.job_dispatcher import start_job_dispatcher
        from app.services.simulation_manager import SimulationManager
        from app.services.simulation_runner import SimulationRunner
        from app.services.zep_graph_memory_updater import ZepGraphMemoryManager
        from app.services.zep_tools import ZepToolsService
        contract = benchmark_module()
        expected_simulations = (self.runtime / "data" / "simulations").resolve()
        if (Path(SimulationManager.SIMULATION_DATA_DIR).resolve() != expected_simulations
                or Path(SimulationRunner.RUN_STATE_DIR).resolve() != expected_simulations):
            raise RuntimeError("Simulation services did not use the isolated data directory")
        app = create_app()
        self.client = app.test_client()
        self.client.environ_base.update(HTTP_AUTHORIZATION="Bearer " + self.owner_key, HTTP_ACCEPT_LANGUAGE="en")
        self.dispatcher = start_job_dispatcher(app)
        self.mark("ontology_started")
        created = self.request("post", "/api/graph/ontology/generate", data={
            "project_name": "Blinded headline comparison", "simulation_requirement": REQUIREMENT,
            "additional_context": "The only people are Curious Reader, Skeptical Reader, and Busy Reader. "
                "Define exactly one entity type Reader. Do not create headline, story, publisher, or audience entities. "
                "The fictional readers participate in the same discussion; a DISCUSSES_WITH relationship may connect Readers.",
            "files": (io.BytesIO(self.source.encode("utf-8")), "source.txt"),
        }, content_type="multipart/form-data")
        self.project_id = self.budget_id = created["project_id"]
        self.proof["project_id"] = self.project_id
        self.mark("ontology_completed")
        queued = self.request("post", "/api/graph/build", json={
            "project_id": self.project_id, "chunk_size": 10000, "chunk_overlap": 0})
        result = self.wait_job(queued["task_id"], "graph_build")
        self.graph_id = result.get("graph_id") or self.request("get", f"/api/graph/project/{self.project_id}")["graph_id"]
        self.proof["source_graph_id"] = self.graph_id
        original_sources = ZepToolsService().get_evidence(self.graph_id)
        source_hash = contract.canonical_sha256(original_sources)
        if not original_sources or any(source.get("kind") == "simulation" for source in original_sources):
            raise RuntimeError("Source graph is missing or already contains simulation evidence")
        self.proof["source_graph_evidence_sha256"] = source_hash
        graph = self.request("get", f"/api/graph/data/{self.graph_id}")
        participant_types = select_participant_types(graph)
        self.proof["participant_selection"] = {
            "entity_types": participant_types,
            "source_persona_names": [persona["name"] for persona in PERSONAS],
            "policy": "exact_source_persona_names_actual_graph_labels_v2",
        }
        self.mark("graph_completed", nodes=graph["node_count"], edges=graph["edge_count"])
        created = self.request("post", "/api/simulation/create", json={
            "project_id": self.project_id, "enable_twitter": False, "enable_reddit": True})
        self.simulation_id = created["simulation_id"]
        self.proof["simulation_id"] = self.simulation_id
        queued = self.request("post", "/api/simulation/prepare", json={
            "simulation_id": self.simulation_id, "parallel_profile_count": 1, "entity_types": participant_types})
        self.wait_job(queued["task_id"], "simulation_prepare")
        config = self.request("get", f"/api/simulation/{self.simulation_id}/config")
        self.configure_scenario(config, expected_simulations)
        self.mark("simulation_started")
        self.request("post", "/api/simulation/start", json={
            "simulation_id": self.simulation_id, "platform": "reddit", "max_rounds": 1,
            "enable_graph_memory_update": True})
        while time.monotonic() < self.deadline:
            state = self.request("get", f"/api/simulation/{self.simulation_id}/run-status")
            if state.get("runner_status") in {"failed", "interrupted"}:
                raise RuntimeError("Simulation worker failed")
            if state.get("reddit_completed"):
                break
            time.sleep(1)
        else:
            raise TimeoutError("Simulation did not complete its round")
        self.request("post", "/api/simulation/close-env", json={"simulation_id": self.simulation_id, "timeout": 30})
        while time.monotonic() < self.deadline:
            state = self.request("get", f"/api/simulation/{self.simulation_id}/run-status")
            if (state.get("runner_status") in {"completed", "stopped"}
                    and ZepGraphMemoryManager.get_updater(self.simulation_id) is None):
                break
            if state.get("runner_status") in {"failed", "interrupted"}:
                raise RuntimeError("Simulation memory drain failed")
            time.sleep(1)
        else:
            raise TimeoutError("Simulation memory did not drain")
        execution_graph = state.get("execution_graph_id")
        if not state.get("execution_id") or not execution_graph or execution_graph == self.graph_id:
            raise RuntimeError("Simulation did not provide an independent execution graph")
        if contract.canonical_sha256(ZepToolsService().get_evidence(self.graph_id)) != source_hash:
            raise RuntimeError("Simulation changed the original source graph")
        self.proof["source_graph_unchanged"] = True
        self.proof["execution_id"] = state["execution_id"]
        self.proof["execution_graph_id"] = execution_graph
        memory = ZepToolsService().get_evidence(execution_graph)
        if not any(source.get("kind") == "simulation" for source in memory):
            raise RuntimeError("No simulated observations reached execution memory")
        actions_path = expected_simulations / self.simulation_id / "reddit" / "actions.jsonl"
        actions = [json.loads(line) for line in actions_path.read_text().splitlines() if line.strip()]
        real_actions = [action for action in actions if not action.get("event_type") and action.get("round", 0) >= 1
                        and action.get("action_type") != "DO_NOTHING" and action.get("success") is not False]
        if not real_actions:
            raise RuntimeError("No successful non-idle model-driven actions were observed")
        self.proof["actions"] = actions
        self.proof["simulation_run_state"] = state
        self.mark("simulation_completed", autonomous_actions=len(real_actions))
        queued = self.request("post", "/api/report/generate", json={"simulation_id": self.simulation_id})
        self.proof["report_id"] = queued["report_id"]
        self.wait_job(queued["task_id"], "report_generate")
        report = self.request("get", f"/api/report/{queued['report_id']}")
        if report.get("status") != "completed" or not report.get("markdown_content", "").strip():
            raise RuntimeError("Report was not completed")
        if report.get("manifest", {}).get("execution_id") != state["execution_id"]:
            raise RuntimeError("Report is not bound to this execution")
        evidence_kinds = {item.get("kind") for item in report.get("evidence", {}).get("sources", [])}
        if not {"source_fact", "simulation_observation"} <= evidence_kinds:
            raise RuntimeError("Report does not include both source and simulation evidence")
        save_json(self.runtime / "report.json", report)
        self.proof["report"] = {"manifest": report["manifest"], "uncertainty": report.get("uncertainty"),
                                 "markdown_sha256": sha256_text(report["markdown_content"])}
        self.mark("report_completed")
        return report["markdown_content"]

    def configure_scenario(self, config, simulations):
        agents = config.get("agent_configs", [])
        if len(agents) != len(PERSONAS):
            raise RuntimeError("Preparation did not produce exactly three assumed readers")
        validate_participant_names(agents)
        before = json.loads(json.dumps(config))
        schedule = config["time_config"]
        schedule.update(minutes_per_round=3, agents_per_hour_min=3, agents_per_hour_max=3,
                        peak_activity_multiplier=1, off_peak_activity_multiplier=1,
                        morning_activity_multiplier=1, work_activity_multiplier=1)
        for agent in agents:
            agent["active_hours"] = [0]
            agent["activity_level"] = 1.0
        initial_posts = []
        for index, agent in enumerate(agents):
            initial_posts.append({"poster_agent_id": agent.get("agent_id", index),
                                  "content": "Which of the following headline options would you click, and why? "
                                             "No winner is known.\n" + self.source})
        config.setdefault("event_config", {})["initial_posts"] = initial_posts
        self.proof["fixture_overrides"] = {
            "generated_time_config": before.get("time_config"), "tested_time_config": schedule,
            "all_agents_active_hours": [0], "all_agents_activity_level": 1.0,
            "neutral_seed_posts": initial_posts,
        }
        save_json(simulations / self.simulation_id / "simulation_config.json", config)

    def cleanup(self):
        if self.simulation_id:
            try:
                from app.services.simulation_runner import SimulationRunner
                state = SimulationRunner.get_run_state(self.simulation_id)
                if state and state.runner_status.value not in {"completed", "stopped", "failed", "interrupted"}:
                    SimulationRunner.stop_simulation(self.simulation_id, timeout=15)
            except Exception as error:
                self.proof["cleanup_error"] = type(error).__name__
        if self.dispatcher:
            self.dispatcher.stop(wait=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--method", required=True, choices=("single_model", "mirofish"))
    parser.add_argument("--repeat", type=int, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--runtime-dir")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--max-calls", type=int, default=35)
    args = parser.parse_args(argv)
    if not 60 <= args.timeout <= 3600 or not 1 <= args.max_calls <= 150:
        parser.error("timeout must be 60..3600 and max-calls 1..150")
    if "app.config" in sys.modules:
        parser.error("Run one trial per fresh Python process before importing application services")
    contract = benchmark_module()
    inputs = json.loads(Path(args.inputs).read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    protocol = json.loads(Path(args.protocol).read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    # Only whitelisted fields cross into either model prompt. The runner has no
    # argument or code path that reads an outcomes file.
    source = build_source_document(contract.prompt_case(inputs, args.case_id))
    expected_protocol = contract.build_protocol(
        inputs, case_ids=protocol.get("case_ids"), repeats=protocol.get("repeats"),
        bootstrap_samples=protocol.get("bootstrap_samples"), bootstrap_seed=protocol.get("bootstrap_seed"),
        model=protocol.get("model"), configuration=protocol.get("configuration"))
    if (protocol != expected_protocol or protocol.get("schema_version") != 1
            or protocol.get("inputs_sha256") != contract.canonical_sha256(inputs)
            or protocol.get("dataset_id") != inputs.get("dataset_id")
            or args.case_id not in protocol.get("case_ids", [])
            or args.method not in protocol.get("methods", [])
            or not 1 <= args.repeat <= protocol.get("repeats", 0)
            or protocol.get("configuration") != benchmark_configuration()
            or args.max_calls != benchmark_configuration()["max_calls"]
            or args.timeout != benchmark_configuration()["wall_timeout_seconds"]):
        parser.error("Trial does not match the frozen protocol and runner configuration")
    output_path = Path(args.output).resolve()
    if output_path.exists():
        parser.error("Refusing to overwrite an existing trial result")
    runtime = make_runtime(args.runtime_dir)
    trial = Trial(args, source, protocol, runtime, sys.stdout)
    trial.configure()
    save_json(runtime / "source.json", {"source_document": source})
    result = {"status": "failed", "probability_a": None, "error": "TrialIncomplete"}
    previous_alarm = None
    if hasattr(signal, "SIGALRM"):
        def expired(_signum, _frame):
            raise TimeoutError("Trial exceeded wall-clock timeout")
        previous_alarm = signal.signal(signal.SIGALRM, expired)
        signal.alarm(args.timeout)
    with (runtime / "trial.log").open("w", encoding="utf-8") as log, redirect_stdout(log), redirect_stderr(log):
        try:
            result = prediction_attempt(trial.run)
        finally:
            if previous_alarm is not None:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, previous_alarm)
            trial.cleanup()
            # Capture the ledger even on failed generation; unknown pricing is
            # deliberately null. Conservatively reserved failed calls count.
            try:
                from app.utils.budget import BudgetStore
                store = BudgetStore(str(runtime / "data" / "budgets.sqlite3"))
                with sqlite3.connect(store.db_path) as database:
                    budget_ids = [row[0] for row in database.execute("SELECT run_id FROM run_budgets ORDER BY run_id")]
                budgets = [store.get(budget_id) for budget_id in budget_ids]
                usage = total_usage(budgets)
                trial.proof["budgets"] = budgets
            except Exception as error:
                usage = {"calls": None, "total_tokens": None, "cost_usd": None}
                trial.proof["usage_error"] = type(error).__name__
            record = {"case_id": args.case_id, "method": args.method, "repeat": args.repeat,
                      "status": result["status"], "probability_a": result["probability_a"],
                      "error": result.get("error"), "elapsed_seconds": time.monotonic() - trial.started,
                      "usage": usage, "run_id": trial.budget_id}
            trial.proof.update(status=result["status"], error=result.get("error"), finished_at=utc_now())
            trial.proof["protocol_sha256"] = contract.canonical_sha256(protocol)
            save_json(runtime / "proof.json", trial.proof)
            save_json(output_path, {"schema_version": 1, "protocol_sha256": contract.canonical_sha256(protocol),
                                    "records": [record]})
    print(json.dumps({"status": result["status"], "output": str(output_path), "proof": str(runtime / "proof.json")}),
          file=trial.output, flush=True)
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
