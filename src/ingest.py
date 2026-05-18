"""CSV parsing and normalisation for Huckleberry exports."""

from __future__ import annotations

import contextlib
import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, BinaryIO

EventRecord = dict[str, Any]

_CSV_DATETIME_FORMAT = "%Y-%m-%d %H:%M"
_FEED_SIDE_RE = re.compile(r"^(\d{1,2}):(\d{2})([LR])$", re.IGNORECASE)
_VOLUME_ML_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(ml|oz)$", re.IGNORECASE)
_HHMM_RE = re.compile(r"^(\d+):(\d{2})$")
_WEIGHT_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(kg|lbs\.oz)$", re.IGNORECASE)
_TEMP_RE = re.compile(r"^(\d+(?:\.\d+)?)°([CF])$", re.IGNORECASE)
_DIAPER_END_RE = re.compile(r"^(poo|pee|both)(?::(small|medium|large))?", re.IGNORECASE)

_FEED_LOCATION_MODE = {"breast": "breast", "bottle": "bottle", "solids": "solids"}
_BOTTLE_TYPES = frozenset(
    {"Breast Milk", "Formula", "Tube Feeding", "Cow Milk", "Goat Milk", "Soy Milk", "Other"}
)

_KNOWN_TYPES = frozenset(
    {
        "Feed",
        "Sleep",
        "Diaper",
        "Bath",
        "Tummy time",
        "Story time",
        "Pump",
        "Meds",
        "Growth",
        "Temp",
    }
)
_DIAPER_COLOURS = frozenset({"yellow", "brown", "black", "green", "red", "gray"})
_DIAPER_CONSISTENCIES = frozenset(
    {"solid", "loose", "runny", "mucousy", "hard", "pebbles", "diarrhea"}
)
_DIAPER_KINDS = frozenset({"pee", "poo", "both", "dry"})
_DIAPER_AMOUNTS = frozenset({"small", "medium", "large"})


@dataclass
class ParseResult:
    events: list[EventRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_csv(file: BinaryIO) -> ParseResult:
    text = _decode(file)
    reader = csv.DictReader(io.StringIO(text))
    result = ParseResult()
    for i, row in enumerate(reader, start=2):
        _parse_row(row, i, result)
    return result


def _decode(file: BinaryIO) -> str:
    raw = file.read()
    if isinstance(raw, str):
        return raw
    return raw.decode("utf-8-sig")


def _parse_row(row: dict[str, str], row_num: int, result: ParseResult) -> None:
    event_type = (row.get("Type") or "").strip()

    if event_type not in _KNOWN_TYPES:
        if event_type:
            result.warnings.append(f"Row {row_num}: unknown type {event_type!r}, skipped")
        return

    start_time = _parse_datetime(row.get("Start"))
    if start_time is None:
        result.warnings.append(f"Row {row_num}: {event_type} missing start_time, skipped")
        return

    end_time = _parse_datetime(row.get("End"))
    if end_time is not None and end_time < start_time:
        result.warnings.append(
            f"Row {row_num}: {event_type} end_time ({end_time}) before start_time ({start_time})"
        )

    base: EventRecord = {
        "type": event_type,
        "start_time": start_time,
        "end_time": end_time,
        "notes": (row.get("Notes") or "").strip() or None,
    }

    _dispatch = {
        "Feed": _parse_feed,
        "Sleep": _parse_sleep,
        "Diaper": _parse_diaper,
        "Pump": _parse_pump,
        "Meds": _parse_meds,
        "Growth": _parse_growth,
        "Temp": _parse_temp_row,
    }
    parser = _dispatch.get(event_type)
    result.events.append(parser(row, base) if parser else dict(base))


def _parse_feed(row: dict[str, str], base: EventRecord) -> EventRecord:
    feed_mode = _feed_mode(row.get("Start Location"))
    if feed_mode == "bottle":
        bottle_type_raw = (row.get("Start Condition") or "").strip()
        bottle_vol = _parse_volume_ml(row.get("End Condition"))
        return {
            **base,
            "feed_mode": feed_mode,
            "feed_bottle_type": bottle_type_raw if bottle_type_raw in _BOTTLE_TYPES else None,
            "feed_bottle_volume": bottle_vol,
            "feed_bottle_units": "ml" if bottle_vol is not None else None,
        }
    left_minutes, right_minutes = _feed_sides(row.get("Start Condition"), row.get("End Condition"))
    return {
        **base,
        "duration_minutes": _feed_duration(left_minutes, right_minutes),
        "feed_mode": feed_mode,
        "feed_left_minutes": left_minutes,
        "feed_right_minutes": right_minutes,
    }


def _parse_sleep(row: dict[str, str], base: EventRecord) -> EventRecord:
    return {**base, "duration_minutes": _parse_duration_hhmm(row.get("Duration"))}


def _parse_diaper(row: dict[str, str], base: EventRecord) -> EventRecord:
    colour_raw = (row.get("Duration") or "").strip().lower() or None
    consistency_raw = (row.get("Start Condition") or "").strip().lower() or None
    kind, amount = _parse_diaper_end(row.get("End Condition"))
    return {
        **base,
        "diaper_colour": colour_raw if colour_raw in _DIAPER_COLOURS else None,
        "diaper_consistency": consistency_raw if consistency_raw in _DIAPER_CONSISTENCIES else None,
        "diaper_kind": kind,
        "diaper_amount": amount,
    }


def _parse_pump(row: dict[str, str], base: EventRecord) -> EventRecord:
    vol_a = _parse_volume_ml(row.get("Start Condition"))
    vol_b = _parse_volume_ml(row.get("End Condition"))
    total: int | None = (
        (vol_a or 0) + (vol_b or 0) if (vol_a is not None or vol_b is not None) else None
    )
    return {**base, "pump_volume_ml": total}


def _parse_meds(row: dict[str, str], base: EventRecord) -> EventRecord:
    medicine = (row.get("Start Location") or "").strip() or None
    notes_raw = (row.get("Notes") or "").strip()
    dose: float | None = None
    if notes_raw:
        with contextlib.suppress(ValueError):
            dose = float(notes_raw)
    return {
        **base,
        "notes": None,
        "meds_medicine": medicine,
        "meds_dose": dose,
        "meds_dose_units": None,
    }


def _parse_growth(row: dict[str, str], base: EventRecord) -> EventRecord:
    weight, units = _parse_weight(row.get("Start Condition"))
    return {**base, "growth_weight": weight, "growth_weight_units": units}


def _parse_temp_row(row: dict[str, str], base: EventRecord) -> EventRecord:
    value, units = _parse_temp(row.get("Start Condition"))
    return {**base, "temp_value": value, "temp_units": units}


# ---- field helpers ----


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


def _parse_duration_hhmm(value: str | None) -> int | None:
    if not value:
        return None
    match = _HHMM_RE.match(value.strip())
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def _parse_volume_ml(value: str | None) -> int | None:
    if not value:
        return None
    match = _VOLUME_ML_RE.match(value.strip())
    if not match:
        return None
    amount, unit = float(match.group(1)), match.group(2).lower()
    if unit == "oz":
        amount *= 29.5735
    return round(amount)


def _parse_weight(value: str | None) -> tuple[float | None, str | None]:
    if not value:
        return None, None
    match = _WEIGHT_RE.match(value.strip())
    if not match:
        return None, None
    weight = float(match.group(1))
    units = "kg" if match.group(2).lower() == "kg" else "lbs.oz"
    return weight, units


def _parse_temp(value: str | None) -> tuple[float | None, str | None]:
    if not value:
        return None, None
    match = _TEMP_RE.match(value.strip())
    if not match:
        return None, None
    return float(match.group(1)), match.group(2).upper()


def _parse_diaper_end(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    match = _DIAPER_END_RE.match(value.strip())
    if not match:
        return None, None
    kind = match.group(1).lower()
    amount_raw = match.group(2)
    amount = amount_raw.lower() if amount_raw and amount_raw.lower() in _DIAPER_AMOUNTS else None
    return kind if kind in _DIAPER_KINDS else None, amount


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
    return side.upper(), int(hours) * 60 + int(minutes)


def _feed_duration(left: int | None, right: int | None) -> int | None:
    if left is None and right is None:
        return None
    return (left or 0) + (right or 0)
