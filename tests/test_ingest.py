"""Unit and integration tests for ingest.py."""

from __future__ import annotations

import io
from collections import Counter
from pathlib import Path

import pytest

import ingest
from ingest import ParseResult

HEADER = "Type,Start,End,Duration,Start Condition,Start Location,End Condition,Notes\n"
REAL_CSV = Path(__file__).parent.parent / "huckleberry_data.csv"


def _parse(csv_text: str) -> ParseResult:
    return ingest.parse_csv(io.BytesIO(csv_text.encode()))


# ---- Feed (already covered by TB-1, light regression check) ----


def test_parse_feed_breast_sides():
    text = HEADER + "Feed,2026-01-01 08:00,2026-01-01 08:10,0:10,00:06R,Breast,00:04L,\n"
    result = _parse(text)
    assert len(result.events) == 1
    e = result.events[0]
    assert e["feed_mode"] == "breast"
    assert e["feed_right_minutes"] == 6
    assert e["feed_left_minutes"] == 4
    assert e["duration_minutes"] == 10


def test_parse_feed_bottle():
    text = HEADER + "Feed,2026-01-01 14:00,,,,Bottle,190ml,\n"
    result = _parse(text)
    e = result.events[0]
    assert e["feed_mode"] == "bottle"
    assert e["feed_bottle_volume"] == 190
    assert e["feed_bottle_units"] == "ml"
    assert e.get("feed_bottle_type") is None  # no Start Condition on this row


def test_parse_feed_bottle_with_type():
    text = HEADER + "Feed,2026-01-01 14:00,,,Breast Milk,Bottle,150ml,\n"
    result = _parse(text)
    e = result.events[0]
    assert e["feed_mode"] == "bottle"
    assert e["feed_bottle_type"] == "Breast Milk"
    assert e["feed_bottle_volume"] == 150
    assert e["feed_bottle_units"] == "ml"
    assert e.get("feed_left_minutes") is None
    assert e.get("feed_right_minutes") is None


# ---- Solids ----


def test_parse_solids_food_and_reaction():
    text = HEADER + "Solids,2026-05-31 16:00,,,Pumpkin,,LOVED,\n"
    result = _parse(text)
    assert len(result.events) == 1
    e = result.events[0]
    assert e["type"] == "Solids"
    assert e["feed_solids_food"] == "Pumpkin"
    assert e["feed_solids_reaction"] == "LOVED"
    assert e.get("duration_minutes") is None
    assert not result.warnings


def test_parse_solids_no_reaction():
    text = HEADER + '"Solids","2026-05-22 11:50",,,"Carrot, sweet potato",,,\n'
    result = _parse(text)
    e = result.events[0]
    assert e["feed_solids_food"] == "Carrot, sweet potato"
    assert e["feed_solids_reaction"] is None


def test_parse_solids_invalid_reaction_nulled():
    text = HEADER + "Solids,2026-05-24 12:20,,,Broccoli,,YUCK,\n"
    result = _parse(text)
    assert result.events[0]["feed_solids_reaction"] is None


# ---- Sleep ----


def test_parse_sleep_duration():
    text = HEADER + "Sleep,2026-01-01 22:00,2026-01-02 06:00,8:00,,,,\n"
    result = _parse(text)
    assert len(result.events) == 1
    e = result.events[0]
    assert e["type"] == "Sleep"
    assert e["duration_minutes"] == 480
    assert not result.warnings


# ---- Diaper ----


def test_parse_diaper_poo_with_colour_and_consistency():
    text = HEADER + "Diaper,2026-01-01 10:00,,yellow,Loose,,Poo:small,\n"
    result = _parse(text)
    e = result.events[0]
    assert e["diaper_colour"] == "yellow"
    assert e["diaper_consistency"] == "loose"
    assert e["diaper_kind"] == "poo"
    assert e["diaper_amount"] == "small"


def test_parse_diaper_pee_only():
    text = HEADER + "Diaper,2026-01-01 10:00,,,,,Pee:medium,\n"
    result = _parse(text)
    e = result.events[0]
    assert e["diaper_colour"] is None
    assert e["diaper_consistency"] is None
    assert e["diaper_kind"] == "pee"
    assert e["diaper_amount"] == "medium"


def test_parse_diaper_both_with_detail():
    text = HEADER + 'Diaper,2026-01-01 10:00,,yellow,Loose,,"Both, pee:medium poo:medium",\n'
    result = _parse(text)
    e = result.events[0]
    assert e["diaper_kind"] == "both"


def test_parse_diaper_no_amount():
    text = HEADER + "Diaper,2026-01-01 10:00,,,,,Poo,\n"
    result = _parse(text)
    e = result.events[0]
    assert e["diaper_kind"] == "poo"
    assert e["diaper_amount"] is None


def test_parse_diaper_invalid_colour_nulled():
    text = HEADER + "Diaper,2026-01-01 10:00,,purple,Loose,,Poo:small,\n"
    result = _parse(text)
    assert result.events[0]["diaper_colour"] is None


def test_parse_diaper_pebbles_consistency():
    text = HEADER + "Diaper,2026-01-01 10:00,,brown,Pebbles,,Poo:large,\n"
    result = _parse(text)
    assert result.events[0]["diaper_consistency"] == "pebbles"


# ---- Pump ----


def test_parse_pump_sums_both_sides():
    text = HEADER + "Pump,2026-01-01 07:00,2026-01-01 07:30,0:30,30ml,,10ml,\n"
    result = _parse(text)
    assert result.events[0]["pump_volume_ml"] == 40


def test_parse_pump_single_side():
    text = HEADER + "Pump,2026-01-01 07:00,2026-01-01 07:15,0:15,50ml,,,\n"
    result = _parse(text)
    assert result.events[0]["pump_volume_ml"] == 50


def test_parse_pump_oz_converted_to_ml():
    text = HEADER + "Pump,2026-01-01 07:00,,,1oz,,,\n"
    result = _parse(text)
    assert result.events[0]["pump_volume_ml"] == 30  # round(29.5735)


# ---- Meds ----


def test_parse_meds_with_dose():
    text = HEADER + "Meds,2026-01-01 08:00,,,,Calpol,,2.5\n"
    result = _parse(text)
    e = result.events[0]
    assert e["meds_medicine"] == "Calpol"
    assert e["meds_dose"] == 2.5
    assert e["meds_dose_units"] is None
    assert e["notes"] is None


def test_parse_meds_no_dose():
    text = HEADER + "Meds,2026-01-01 20:00,,,,Lactulose,,\n"
    result = _parse(text)
    e = result.events[0]
    assert e["meds_medicine"] == "Lactulose"
    assert e["meds_dose"] is None


# ---- Growth ----


def test_parse_growth_kg():
    text = HEADER + "Growth,2026-01-01 10:00,,,6.6kg,,,\n"
    result = _parse(text)
    e = result.events[0]
    assert e["growth_weight"] == pytest.approx(6.6)
    assert e["growth_weight_units"] == "kg"


# ---- Temp ----


def test_parse_temp_celsius():
    text = HEADER + "Temp,2026-01-01 23:00,,,37.4°C,,,\n"
    result = _parse(text)
    e = result.events[0]
    assert e["temp_value"] == pytest.approx(37.4)
    assert e["temp_units"] == "C"


# ---- Simple types (Bath, Tummy time, Story time) ----


def test_parse_bath():
    text = HEADER + "Bath,2026-01-01 18:00,,,,,,\n"
    result = _parse(text)
    assert len(result.events) == 1
    assert result.events[0]["type"] == "Bath"


# ---- Validation rules (spec §4.4) ----


def test_unknown_type_skipped_with_warning():
    text = HEADER + "Unknown,2026-01-01 10:00,,,,,,\n"
    result = _parse(text)
    assert len(result.events) == 0
    assert len(result.warnings) == 1
    assert "unknown type" in result.warnings[0]


def test_missing_start_time_skipped_with_warning():
    text = HEADER + "Sleep,,,8:00,,,,\n"
    result = _parse(text)
    assert len(result.events) == 0
    assert len(result.warnings) == 1
    assert "missing start_time" in result.warnings[0]


def test_end_before_start_warns_but_still_inserts():
    text = HEADER + "Sleep,2026-01-02 06:00,2026-01-01 22:00,8:00,,,,\n"
    result = _parse(text)
    assert len(result.events) == 1
    assert len(result.warnings) == 1
    assert "before start_time" in result.warnings[0]


def test_empty_csv_produces_empty_result():
    text = HEADER
    result = _parse(text)
    assert isinstance(result, ParseResult)
    assert result.events == []
    assert result.warnings == []


# ---- Integration: full CSV type counts ----


@pytest.mark.skipif(not REAL_CSV.exists(), reason="huckleberry_data.csv not present")
def test_full_csv_type_counts():
    expected = {
        "Feed": 1295,
        "Diaper": 1715,
        "Sleep": 764,
        "Pump": 205,
        "Bath": 76,
        "Tummy time": 18,
        "Meds": 15,
        "Story time": 6,
        "Growth": 3,
        "Temp": 1,
    }
    with open(REAL_CSV, "rb") as f:
        result = ingest.parse_csv(f)
    counts = dict(Counter(e["type"] for e in result.events))
    assert counts == expected
