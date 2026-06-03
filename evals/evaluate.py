"""Evaluate runner output: run each case's scorers over its captured result.

Reads a runner output file (the ``{metadata, results}`` JSON written by
``runner.py``) and a ground-cases file, matches results to cases by id, runs
each case's configured scorers against its result, and prints one pass/fail
line per scorer to stdout:

    case_001: Check 'required_tools' passed. Reason: -

Scorers are dispatched generically: each registered scorer declares the inputs
it needs by parameter name (``output``, ``tools_used``, ``tool_counts``,
``intermediate_results``, ``expected``, ``args``) and is given only those, so
scorers with different signatures all work through one call path.

Run from the project root so the ``evals`` package is importable, e.g.
``uv run python -m evals.evaluate jobs/eval_run_output.json``.
"""

import argparse
import inspect
import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from evals.scorers.registry import SCORER_REGISTRY

_VAR_KEYWORD = inspect.Parameter.VAR_KEYWORD


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def scorer_inputs(result: dict[str, Any]) -> dict[str, Any]:
    """Derive the inputs a scorer may consume from one case's runner result.

    ``tools_used`` is the ordered list of tool names the agent called and
    ``tool_counts`` maps each tool name to how many times it was called.
    """
    intermediate = result.get("intermediate_results", [])
    tools_used = [step["tool"] for step in intermediate if step.get("tool")]
    return {
        "output": result.get("output", ""),
        "tools_used": tools_used,
        "tool_counts": dict(Counter(tools_used)),
        "intermediate_results": intermediate,
    }


def call_scorer(fn: Callable, inputs: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """Call ``fn`` with only the inputs its signature declares.

    ``inputs`` are the derived per-result values; ``args`` is the scorer's
    configured argument block (its ``expected`` is exposed both as the
    ``expected`` kwarg and as the whole ``args`` dict). A scorer accepting
    ``**kwargs`` receives everything.
    """
    available = {**inputs, "expected": args.get("expected"), "args": args}
    params = inspect.signature(fn).parameters
    if any(p.kind is _VAR_KEYWORD for p in params.values()):
        return fn(**available)
    return fn(**{name: available[name] for name in params if name in available})


def evaluate_case(case: dict[str, Any], result: dict[str, Any]) -> list[str]:
    """Run every scorer configured on ``case`` against ``result``.

    Returns one formatted stdout line per scorer. A scorer that is not
    registered, or that raises, yields an ``ERROR`` line rather than aborting
    the whole evaluation.
    """
    inputs = scorer_inputs(result)
    lines = []
    for spec in case["expected"]["scorers"]:
        name = spec["name"]
        args = spec.get("args", {})
        fn = SCORER_REGISTRY.get(name)
        if fn is None:
            lines.append(f"[FAIL] Check '{name}'. Reason: scorer not registered")
            continue
        try:
            outcome = call_scorer(fn, inputs, args)
        except Exception as exc:
            lines.append(f"[FAIL] Check '{name}'. Reason: {exc}")
            continue
        tag = "[PASS]" if outcome.get("passed") else "[FAIL]"
        reason = outcome.get("reason") or "-"
        lines.append(f"{tag} Check '{name}'. Reason: {reason}")
    return lines


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run scorers over runner output.",
        allow_abbrev=False,  # keep --case distinct from --cases
    )
    parser.add_argument(
        "results",
        type=Path,
        help="Path to runner output JSON (the {metadata, results} file)",
    )
    parser.add_argument(
        "cases",
        type=Path,
        help="Ground-cases JSON (default: evals/cases/ground_cases.json)",
    )
    parser.add_argument(
        "--case",
        dest="case_ids",
        action="append",
        metavar="CASE_ID",
        help="Only evaluate the named case; repeatable (e.g. --case case_001 --case case_002)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    output = load_json(args.results)
    cases_by_id = {c["id"]: c for c in load_json(args.cases)["cases"]}

    results = output["results"]
    if args.case_ids:
        wanted = set(args.case_ids)
        results = [r for r in results if r["case_id"] in wanted]
        missing = wanted - {r["case_id"] for r in results}
        if missing:
            raise ValueError(f"no result for case id(s): {', '.join(sorted(missing))}")

    for result in results:
        case_id = result["case_id"]
        case = cases_by_id.get(case_id)
        if case is None:
            print(f"{case_id}: no matching ground case; skipping")
            continue
        if not case["expected"]["scorers"]:
            print(f"{case_id}: no scorers configured")
            continue
        for line in evaluate_case(case, result):
            print(line)


if __name__ == "__main__":
    main()
