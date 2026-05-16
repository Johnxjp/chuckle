"""CSV parsing and normalisation for Huckleberry exports.

TB-1 scope: Feed rows only. Other types are skipped silently and will be
implemented in TB-2 (T-2.1a–g).
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from typing import Any, BinaryIO

EventRecord = dict[str, Any]

_CSV_DATETIME_FORMAT = "%Y-%m-%d %H:%M"
_FEED_SIDE_RE = re.compile(r"^(\d{1,2}):(\d{2})([LR])$", re.IGNORECASE)
_FEED_LOCATION_MODE = {
    "breast": "breast",
    "bottle": "bottle",
    "solids": "solids",
}


def parse_csv(file: BinaryIO) -> list[EventRecord]:
    text = _decode(file)
    reader = csv.DictReader(io.StringIO(text))
    events: list[EventRecord] = []
    for row in reader:
        record = _parse_row(row)
        if record is not None:
            events.append(record)
    return events


def _decode(file: BinaryIO) -> str:
    raw = file.read()
    if isinstance(raw, str):
        return raw
    return raw.decode("utf-8-sig")


def _parse_row(row: dict[str, str]) -> EventRecord | None:
    event_type = (row.get("Type") or "").strip()
    if event_type != "Feed":
        return None

    start_time = _parse_datetime(row.get("Start"))
    if start_time is None:
        return None
    end_time = _parse_datetime(row.get("End"))

    feed_mode = _feed_mode(row.get("Start Location"))
    left_minutes, right_minutes = _feed_sides(row.get("Start Condition"), row.get("End Condition"))
    duration_minutes = _feed_duration(left_minutes, right_minutes)

    return {
        "type": "Feed",
        "start_time": start_time,
        "end_time": end_time,
        "duration_minutes": duration_minutes,
        "notes": (row.get("Notes") or "").strip() or None,
        "feed_mode": feed_mode,
        "feed_left_minutes": left_minutes,
        "feed_right_minutes": right_minutes,
    }


def _parse_datetime(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        dt = datetime.strptime(text, _CSV_DATETIME_FORMAT)
    except ValueError:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _feed_mode(location: str | None) -> str | None:
    if not location:
        return None
    return _FEED_LOCATION_MODE.get(location.strip().lower())


def _feed_sides(
    start_condition: str | None, end_condition: str | None
) -> tuple[int | None, int | None]:
    left: int | None = None
    right: int | None = None
    for value in (start_condition, end_condition):
        side, minutes = _parse_feed_side(value)
        if side == "L":
            left = (left or 0) + minutes
        elif side == "R":
            right = (right or 0) + minutes
    return left, right


def _parse_feed_side(value: str | None) -> tuple[str | None, int]:
    if not value:
        return None, 0
    match = _FEED_SIDE_RE.match(value.strip())
    if not match:
        return None, 0
    hours, minutes, side = match.groups()
    total_minutes = int(hours) * 60 + int(minutes)
    return side.upper(), total_minutes


def _feed_duration(left: int | None, right: int | None) -> int | None:
    if left is None and right is None:
        return None
    return (left or 0) + (right or 0)
