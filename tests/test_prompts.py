"""Unit tests for prompts.py."""

from __future__ import annotations

from datetime import datetime

import pytest

from prompts import TIME_PERIODS, build_system_prompt, build_temporal_context


@pytest.fixture()
def now_tuesday() -> datetime:
    return datetime(2026, 5, 12, 14, 32)


def test_temporal_context_contains_date_and_time(now_tuesday: datetime) -> None:
    block = build_temporal_context(now_tuesday)
    assert "2026-05-12 14:32" in block


def test_temporal_context_contains_day_of_week(now_tuesday: datetime) -> None:
    block = build_temporal_context(now_tuesday)
    assert "Tuesday" in block


def test_temporal_context_contains_all_periods(now_tuesday: datetime) -> None:
    block = build_temporal_context(now_tuesday)
    for period in TIME_PERIODS:
        assert period in block


def test_temporal_context_midnight_crossing_annotated(now_tuesday: datetime) -> None:
    block = build_temporal_context(now_tuesday)
    assert "crosses midnight" in block


def test_temporal_context_datetime_format_instruction(now_tuesday: datetime) -> None:
    block = build_temporal_context(now_tuesday)
    assert "YYYY-MM-DD HH:MM:SS" in block


def test_temporal_context_changes_with_different_now() -> None:
    monday = datetime(2026, 5, 11, 9, 0)
    block = build_temporal_context(monday)
    assert "Monday" in block
    assert "2026-05-11 09:00" in block


def test_build_system_prompt_contains_role(now_tuesday: datetime) -> None:
    prompt = build_system_prompt(now_tuesday, "SCHEMA HERE")
    assert "Chuckle" in prompt


def test_build_system_prompt_contains_schema_context(now_tuesday: datetime) -> None:
    prompt = build_system_prompt(now_tuesday, "SCHEMA HERE")
    assert "SCHEMA HERE" in prompt


def test_build_system_prompt_contains_temporal_block(now_tuesday: datetime) -> None:
    prompt = build_system_prompt(now_tuesday, "SCHEMA HERE")
    assert "2026-05-12 14:32" in prompt
    assert "Tuesday" in prompt


def test_build_system_prompt_contains_few_shot_examples(now_tuesday: datetime) -> None:
    prompt = build_system_prompt(now_tuesday, "SCHEMA HERE")
    assert "last night" in prompt.lower()
    assert "last nappy" in prompt.lower()


def test_build_system_prompt_contains_output_rule(now_tuesday: datetime) -> None:
    prompt = build_system_prompt(now_tuesday, "SCHEMA HERE")
    assert "Looking at" in prompt


def test_build_system_prompt_few_shot_dates_derived_from_now(now_tuesday: datetime) -> None:
    prompt = build_system_prompt(now_tuesday, "SCHEMA HERE")
    # yesterday relative to 2026-05-12
    assert "2026-05-11" in prompt
    # monday of 2026-05-12 (Tuesday) is 2026-05-11
    assert "2026-05-11 00:00:00" in prompt
