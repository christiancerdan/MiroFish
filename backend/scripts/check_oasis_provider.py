#!/usr/bin/env python3
"""Create one synthetic OASIS post in a temporary Reddit database.

Uses the configured provider through the same CAMEL backend as simulations.
Only the synthetic prompt is sent. The temporary database and library logs are
removed; this checks tool execution and persistence, not a complete simulation.
"""

import argparse
import asyncio
from contextlib import closing, contextmanager, redirect_stderr, redirect_stdout, suppress
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.utils.llm_provider import create_camel_model, resolve_llm_settings


PROBE_CONTENT = "MiroFish synthetic provider check."


class VerificationError(ValueError):
    """A local verification failure with a safe, fixed check identifier."""

    def __init__(self, check):
        self.check = check
        super().__init__(check)


def check_persisted_action(database_path):
    """Reject silent OASIS failures, prose-only responses, and partial writes."""
    with closing(sqlite3.connect(Path(database_path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        users = db.execute("SELECT user_id, agent_id FROM user").fetchall()
        posts = db.execute("SELECT post_id, user_id, content FROM post").fetchall()
        traces = db.execute(
            "SELECT user_id, info FROM trace WHERE action = 'create_post'"
        ).fetchall()
    if users != [(0, 0)]:
        raise VerificationError("agent_registration")
    if len(posts) != 1 or posts[0][1:] != (0, PROBE_CONTENT):
        raise VerificationError("persisted_post")
    if len(traces) != 1 or traces[0][0] != 0:
        raise VerificationError("create_post_trace")
    if json.loads(traces[0][1]) != {"content": PROBE_CONTENT, "post_id": posts[0][0]}:
        raise VerificationError("matching_post_trace")
    return {"posts": 1, "create_post_traces": 1}


def _loggers():
    return [logging.getLogger()] + [
        logger for logger in logging.Logger.manager.loggerDict.values()
        if isinstance(logger, logging.Logger)
    ]


@contextmanager
def _isolated_libraries(directory, database_path):
    # OASIS creates ./log on import and uses OASIS_DB_PATH separately from the
    # environment database_path when building observations. Keep both temporary.
    previous_cwd = Path.cwd()
    env_values = {name: os.environ.get(name) for name in ("OASIS_DB_PATH", "CAMEL_MODEL_LOG_ENABLED")}
    previous_disable = logging.Logger.manager.disable
    previous_handlers = {handler for logger in _loggers() for handler in logger.handlers}
    try:
        os.chdir(directory)
        os.environ["OASIS_DB_PATH"] = str(database_path)
        os.environ["CAMEL_MODEL_LOG_ENABLED"] = "false"
        logging.disable(logging.CRITICAL)
        # Libraries may print or log raw provider errors. Only main's sanitized
        # JSON result is user-visible; no exception bodies are retained.
        with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
            yield
    finally:
        for logger in _loggers():
            for handler in list(logger.handlers):
                if handler not in previous_handlers:
                    logger.removeHandler(handler)
                    handler.close()
        logging.disable(previous_disable)
        os.chdir(previous_cwd)
        for name, value in env_values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


async def _run_oasis(settings, database_path):
    # Import inside isolation: OASIS opens log files during module import.
    import oasis
    from camel.prompts import TextPrompt

    model = create_camel_model(settings=settings)
    env = None
    try:
        user = oasis.UserInfo(
            user_name="provider_check", name="Provider check",
            description="Synthetic connectivity check", profile={},
            recsys_type="reddit",
        )
        agent = oasis.SocialAgent(
            agent_id=0, user_info=user,
            user_info_template=TextPrompt(
                "You are a synthetic connectivity check. On your next turn, "
                "call create_post exactly once. Set its content argument to the "
                "exact string shown in JSON quotes: " + json.dumps(PROBE_CONTENT)
                + " Preserve all punctuation inside the quotes. Do not include "
                "the quotes or add any text. Use the tool, not prose."
            ),
            model=model, available_actions=[oasis.ActionType.CREATE_POST],
            max_iteration=1,
        )
        graph = oasis.AgentGraph()
        graph.add_agent(agent)
        env = oasis.make(
            agent_graph=graph, platform=oasis.DefaultPlatformType.REDDIT,
            database_path=str(database_path), semaphore=1,
        )
        await env.reset()
        await env.step({agent: oasis.LLMAction()})
        return check_persisted_action(database_path)
    finally:
        if env is not None:
            task = getattr(env, "platform_task", None)
            if task is not None:
                if not task.done():
                    with suppress(Exception):
                        await asyncio.wait_for(env.close(), timeout=5)
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            # OASIS closes SQLite only when its platform loop receives EXIT.
            # Also close explicitly when reset/step failed or the task crashed.
            with suppress(Exception):
                env.platform.db_cursor.close()
            with suppress(Exception):
                env.platform.db.close()
        with suppress(Exception):
            await model._async_client.close()
        with suppress(Exception):
            model._client.close()


def run_check(settings, *, timeout=120):
    with tempfile.TemporaryDirectory(prefix="mirofish-oasis-provider-") as directory:
        database_path = Path(directory).resolve() / "probe.db"
        with _isolated_libraries(directory, database_path):
            async def bounded_check():
                return await asyncio.wait_for(_run_oasis(settings, database_path), timeout=timeout)
            return asyncio.run(bounded_check())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boost", action="store_true", help="Check the optional boost provider")
    parser.add_argument("--timeout", type=float, default=120, help="Maximum check time in seconds (default: 120)")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        settings = resolve_llm_settings(use_boost=args.boost)
    except ValueError as error:
        print(json.dumps({"ok": False, "configuration_error": str(error)}))
        return 2
    try:
        evidence = run_check(settings, timeout=args.timeout)
        print(json.dumps({
            "ok": True, "provider": settings.provider, "model": settings.model,
            "platform": "reddit", "agents": 1, "steps": 1,
            "checks": ["oasis_create_post", "sqlite_post_and_trace"], **evidence,
        }))
        return 0
    except Exception as error:
        status = getattr(error, "status_code", None)
        result = {
            "ok": False, "error_type": type(error).__name__,
            "http_status": status if isinstance(status, int) else None,
        }
        if isinstance(error, VerificationError):
            result["failed_check"] = error.check
        print(json.dumps(result))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
