"""Eval runner: build an isolated, in-memory database from a dataset's events.

The agent normally answers questions against ``chuckle.db``. For evals we want a
disposable database seeded with known mock data so answers are reproducible. This
reuses the production schema and insert path from ``src/db.py`` so the eval DB is
identical in shape to the real one.

Run with ``src`` on ``PYTHONPATH`` (matching the pytest config), e.g.
``PYTHONPATH=src uv run python evals/runner.py``.
"""

import hashlib
import json
import os
import sqlite3
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain.agents import AgentExecutor

import db
from agent import build_agent
from prompts import build_system_prompt

PROJECT_ROOT = Path(__file__).parent.parent
DATASET_PATH = Path(__file__).parent / "datasets" / "knowledge_base_v1.json"
_NOW_FORMAT = "%Y-%m-%d %H:%M:%S"


def load_eval_env() -> None:
    """Load the eval env file, overriding any prod ``.env`` or shell vars.

    The file is chosen by ``CHUCKLE_ENV_FILE`` (default ``.env.eval``), resolved
    relative to the project root. ``override=True`` ensures eval credentials win
    over a real ``.env`` so a prod key can't leak into an eval run.
    """
    env_file = os.environ.get("CHUCKLE_ENV_FILE", ".env.eval")
    load_dotenv(PROJECT_ROOT / env_file, override=True)


@dataclass
class EvalRunMetadata:
    run_id: str  # uuid4
    timestamp: str  # ISO 8601 UTC
    git_commit: str  # agent code version under test
    agent_model: str
    agent_temperature: float
    agent_max_tokens: int | None
    dataset_version: str
    system_prompt: str  # exact rendered system prompt sent to the model
    system_prompt_hash: str  # sha256[:12] of system_prompt, for quick comparison
    judge_model: str | None  # model used for LLM-as-judge scorer, if any


def load_dataset(dataset_path: Path = DATASET_PATH) -> dict[str, Any]:
    """Return a dataset file's ``data`` block (its ``now`` and ``events``)."""
    with dataset_path.open() as f:
        return json.load(f)["data"]


def load_events(dataset_path: Path = DATASET_PATH) -> list[dict[str, Any]]:
    """Return the list of event records held in a dataset's ``data.events``."""
    return load_dataset(dataset_path)["events"]


def init_eval_db(
    events: list[dict[str, Any]] | None = None,
    dataset_path: Path = DATASET_PATH,
) -> sqlite3.Connection:
    """Create an in-memory eval database seeded with the dataset's events.

    Pass ``events`` directly, or omit them to load from ``dataset_path``.
    """
    if events is None:
        events = load_events(dataset_path)
    conn = db.init_db(":memory:")
    db.replace_events(conn, events)
    return conn


def build_eval_agent(
    dataset_path: Path = DATASET_PATH,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int | None = None,
) -> AgentExecutor:
    """Build an agent bound to a fresh in-memory eval DB instead of ``chuckle.db``.

    ``now`` is taken from the dataset's ``data.now`` (a fixed timestamp) so the
    system prompt's temporal context and few-shot examples are reproducible.
    Model parameters are passed through to ``build_agent`` so eval runs can
    configure and record them.
    """
    data = load_dataset(dataset_path)
    now = datetime.strptime(data["now"], _NOW_FORMAT)
    conn = init_eval_db(data["events"])
    return build_agent(now, model=model, temperature=temperature, max_tokens=max_tokens, conn=conn)


def render_system_prompt(dataset_path: Path = DATASET_PATH) -> str:
    """Render the exact system prompt the eval agent will use.

    ``build_system_prompt`` is a pure function of ``(now, schema_context)``; both
    are fixed for a dataset (fixed ``data.now``, fixed seeded events in fixed
    order), so this reproduces the precise text ``build_agent`` sends to the model.
    """
    data = load_dataset(dataset_path)
    now = datetime.strptime(data["now"], _NOW_FORMAT)
    conn = init_eval_db(data["events"])
    return build_system_prompt(now, db.get_schema_context(conn))


def create_run_metadata(config: dict) -> EvalRunMetadata:
    system_prompt = config["system_prompt"]

    try:
        git_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        git_commit = "unknown"

    return EvalRunMetadata(
        run_id=str(uuid.uuid4()),
        timestamp=datetime.now(UTC).isoformat(),
        git_commit=git_commit,
        agent_model=config["model"],
        agent_temperature=config.get("temperature", 0.0),
        agent_max_tokens=config.get("max_tokens"),
        dataset_version=config.get("dataset_version", "v1"),
        system_prompt=system_prompt,
        system_prompt_hash=hashlib.sha256(system_prompt.encode()).hexdigest()[:12],
        judge_model=config.get("judge_model"),
    )


def main():
    load_eval_env()
    conn = init_eval_db()
    total, by_type = db.get_event_summary(conn)
    print(f"eval db seeded: {total} events")
    for event_type, count in sorted(by_type.items()):
        print(f"  {event_type}: {count}")


if __name__ == "__main__":
    main()
