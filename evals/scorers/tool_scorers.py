from evals.scorers.registry import scorer


@scorer("required_tools")
def score_required_tools(tools_used: list[str], *, expected: list[str]) -> dict:
    required = set(expected)
    used = set(tools_used)
    missing = required - used
    passed = len(missing) == 0
    return {
        "score": len(required - missing) / len(required) if required else 1.0,
        "passed": passed,
        "reason": f"Missing tool calls: {missing}" if not passed else None,
    }


@scorer("tool_count")
def score_tool_count(tool_counts: dict[str, int], *, expected: list[list]) -> dict:
    """
    Checks each tool was called the expected number of times.

    ``expected`` is a list of ``[tool_name, count]`` pairs; a tool never called
    counts as 0.
    """
    mismatches = {
        tool: (count, tool_counts.get(tool, 0))
        for tool, count in expected
        if tool_counts.get(tool, 0) != count
    }
    passed = len(mismatches) == 0
    return {
        "score": (len(expected) - len(mismatches)) / len(expected) if expected else 1.0,
        "passed": passed,
        "reason": (
            (
                "Tool call count mismatches (tool: expected vs actual): "
                + ", ".join(f"{t}: {exp} vs {act}" for t, (exp, act) in mismatches.items())
            )
            if not passed
            else None
        ),
    }
