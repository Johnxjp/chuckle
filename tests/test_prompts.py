"""Unit tests for prompts.py."""

from __future__ import annotations

from datetime import datetime

from prompts import build_system_prompt, build_temporal_context


def test_temporal_context_renders_date_and_day_for_given_now() -> None:
    monday = datetime(2026, 5, 11, 9, 0)
    block = build_temporal_context(monday)
    assert "Monday" in block
    assert "2026-05-11 09:00" in block


def test_build_system_prompt_few_shot_dates_derived_from_now() -> None:
    now_tuesday = datetime(2026, 5, 12, 14, 32)
    prompt = build_system_prompt(now_tuesday, "SCHEMA HERE")
    # yesterday and monday-of-week both = 2026-05-11
    assert "2026-05-11" in prompt
    assert "2026-05-11 00:00:00" in prompt
