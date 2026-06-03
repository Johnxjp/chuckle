import re

from dateutil import parser as dateparser

from evals.scorers.registry import scorer


@scorer("datetime_present")
def score_datetime_present(output: str, *, expected: str) -> dict:
    """
    Checks that the expected time/date string appears in the response.
    Handles format variants: 14:32, 2:32pm, 14:32:00 etc.
    """
    expected_dt = dateparser.parse(expected)
    response = output

    # Strategy 1: literal substring match
    if expected in response:
        return {"score": 1.0, "passed": True, "reason": None}

    # Strategy 2: parse all time-like strings from response, compare semantically
    time_pattern = r"\b\d{1,2}:\d{2}(?::\d{2})?(?:\s?[ap]m)?\b"
    found_times = re.findall(time_pattern, response, re.IGNORECASE)

    try:
        for t in found_times:
            candidate = dateparser.parse(t)
            if (
                candidate
                and candidate.hour == expected_dt.hour
                and candidate.minute == expected_dt.minute
            ):
                return {"score": 1.0, "passed": True, "reason": None}
    except Exception:
        pass

    return {
        "score": 0.0,
        "passed": False,
        "reason": f"Expected time {expected!r} not found in response. Found: {found_times}",
    }


@scorer("output_not_contains")
def score_output_not_contains(output: str, *, expected: str) -> dict:
    """Checks that ``expected`` does not appear literally in the response.

    Used to assert the agent never leaks raw values (e.g. database-format
    datetimes) into its natural-language answer.
    """
    contained = expected in output
    return {
        "score": 0.0 if contained else 1.0,
        "passed": not contained,
        "reason": f"Response unexpectedly contains {expected!r}" if contained else None,
    }
