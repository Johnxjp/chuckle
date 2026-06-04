"""Eval runner: build an isolated, in-memory database from a dataset's events.

The agent normally answers questions against ``chuckle.db``. For evals we want a
disposable database seeded with known mock data so answers are reproducible. This
reuses the production schema and insert path from ``src/db.py`` so the eval DB is
identical in shape to the real one.

Run with ``src`` on ``PYTHONPATH`` (matching the pytest config), e.g.
``PYTHONPATH=src uv run python evals/runner.py``.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain.agents import AgentExecutor

import db
from agent import DEFAULT_MODEL, build_agent
from prompts import build_system_prompt

PROJECT_ROOT = Path(__file__).parent.parent
_NOW_FORMAT = "%Y-%m-%d %H:%M:%S"


def load_eval_env() -> None:
    """Load the eval env file, overriding any prod ``.env`` or shell vars.

    Loads ``.env.eval`` from the project root. ``override=True`` ensures eval
    credentials win over a real ``.env`` so a prod key can't leak into an eval run.
    """
    env_file = ".env.eval"
    load_dotenv(PROJECT_ROOT / env_file, override=True)


@dataclass
class EvalRunMetadata:
    run_id: str  # uuid4
    timestamp: str  # ISO 8601 UTC
    git_commit: str  # agent code version under test
    agent_model: str
    agent_temperature: float
    agent_max_tokens: int | None
    dataset: str  # dataset filename the cases were run against
    system_prompt: str  # exact rendered system prompt sent to the model
    system_prompt_hash: str  # sha256[:12] of system_prompt, for quick comparison
    judge_model: str | None  # model used for LLM-as-judge scorer, if any


def load_dataset(dataset_path: Path) -> dict[str, Any]:
    """Return a dataset file's ``data`` block (its ``now`` and ``events``)."""
    with dataset_path.open() as f:
        return json.load(f)["data"]


def load_events(dataset_path: Path) -> list[dict[str, Any]]:
    """Return the list of event records held in a dataset's ``data.events``."""
    return load_dataset(dataset_path)["events"]


def load_cases(cases_path: Path) -> dict[str, Any]:
    """Return a cases JSON file's full contents (its ``dataset`` and ``cases``)."""
    with cases_path.open() as f:
        return json.load(f)


def select_cases(cases: list[dict[str, Any]], names: list[str] | None) -> list[dict[str, Any]]:
    """Filter ``cases`` to those whose ``id`` is in ``names`` (order preserved).

    With no names, all cases are returned. Names that match no case raise a
    ``ValueError`` so a typo'd ``--case`` fails loudly instead of silently
    running nothing.
    """
    if not names:
        return cases
    wanted = set(names)
    selected = [c for c in cases if c["id"] in wanted]
    missing = wanted - {c["id"] for c in selected}
    if missing:
        raise ValueError(f"unknown case id(s): {', '.join(sorted(missing))}")
    return selected


def init_eval_db(
    dataset_path: Path,
    events: list[dict[str, Any]] | None = None,
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
    dataset_path: Path,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int | None = None,
    return_intermediate_steps: bool = True,
) -> AgentExecutor:
    """Build an agent bound to a fresh in-memory eval DB instead of ``chuckle.db``.

    ``now`` is taken from the dataset's ``data.now`` (a fixed timestamp) so the
    system prompt's temporal context and few-shot examples are reproducible.
    Model parameters are passed through to ``build_agent`` so eval runs can
    configure and record them. ``return_intermediate_steps`` defaults to ``True``
    so ``invoke`` returns the full tool trace (tool inputs and outputs) for scoring.
    """
    data = load_dataset(dataset_path)
    now = datetime.strptime(data["now"], _NOW_FORMAT)
    conn = init_eval_db(dataset_path, data["events"])
    return build_agent(
        now,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        conn=conn,
        return_intermediate_steps=return_intermediate_steps,
    )


def render_system_prompt(dataset_path: Path) -> str:
    """Render the exact system prompt the eval agent will use.

    ``build_system_prompt`` is a pure function of ``(now, schema_context)``; both
    are fixed for a dataset (fixed ``data.now``, fixed seeded events in fixed
    order), so this reproduces the precise text ``build_agent`` sends to the model.
    """
    data = load_dataset(dataset_path)
    now = datetime.strptime(data["now"], _NOW_FORMAT)
    conn = init_eval_db(dataset_path, data["events"])
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
        dataset=config["dataset"],
        system_prompt=system_prompt,
        system_prompt_hash=hashlib.sha256(system_prompt.encode()).hexdigest()[:12],
        judge_model=config.get("judge_model"),
    )


def serialize_intermediate_steps(steps: list[Any]) -> list[dict[str, Any]]:
    """Convert an agent's ``intermediate_steps`` into JSON-serialisable dicts.

    Each step is an ``(AgentAction, observation)`` tuple; the action carries the
    tool name, its input, and the model's reasoning log, and the observation is
    the tool's returned output.
    """
    serialized = []
    for action, observation in steps:
        serialized.append(
            {
                "tool": getattr(action, "tool", None),
                "tool_input": getattr(action, "tool_input", None),
                "log": getattr(action, "log", None),
                "observation": observation,
            }
        )
    return serialized


def run_case(executor: AgentExecutor, case: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Run one case through the agent, returning its answer and tool trace."""
    user_message = case["input"]["user_message"]
    result = executor.invoke({"input": user_message, "chat_history": []})
    output = result.get("output", "")
    intermediate = serialize_intermediate_steps(result.get("intermediate_steps", []))
    return output, intermediate


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run eval cases through the agent.")
    parser.add_argument(
        "cases",
        type=Path,
        help="Path to the test-cases JSON file (default: evals/cases/ground_cases.json)",
    )
    parser.add_argument(
        "--case",
        dest="case_ids",
        action="append",
        metavar="CASE_ID",
        help="Only run the named case; repeatable (e.g. --case case_001 --case case_002)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help=("Path to the output file. Defaults to ./jobs/eval_run_output_<YYYYmmdd_HHMMSS>.json"),
    )
    return parser.parse_args(argv)


def default_output_path() -> Path:
    """Return the timestamped default output path used when ``--output`` is unset."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("./jobs") / f"eval_run_output_{stamp}.json"


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    load_eval_env()

    cases_file = load_cases(args.cases)
    cases = select_cases(cases_file["cases"], args.case_ids)
    if not cases:
        print("no cases to run")
        return

    dataset_name = cases_file["dataset"]
    dataset_path = Path(__file__).parent / "datasets" / dataset_name
    model = os.environ.get("CHUCKLE_MODEL") or DEFAULT_MODEL
    executor = build_eval_agent(dataset_path=dataset_path, model=model)
    print(f"running {len(cases)} case(s) against {dataset_name}\n")

    metadata = create_run_metadata(
        {
            "system_prompt": render_system_prompt(dataset_path),
            "model": model,
            "temperature": 0.0,
            "max_tokens": None,
            "dataset": dataset_name,
            "judge_model": None,
        }
    )

    results = []
    for case in cases:
        print(f"=== {case['id']} ===")
        print(f"Q: {case['input']['user_message']}")
        start = time.perf_counter()
        output, intermediate = run_case(executor, case)
        time_taken = time.perf_counter() - start
        print(f"A: {output}\n")
        results.append(
            {
                "case_id": case["id"],
                "input": case["input"],
                "output": output,
                "intermediate_results": intermediate,
                "time_taken": time_taken,
            }
        )

    output_path = Path(args.output) if args.output else default_output_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"metadata": asdict(metadata), "results": results}
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=4, ensure_ascii=False)
    print(f"wrote {len(results)} result(s) to {output_path}")


if __name__ == "__main__":
    main()
