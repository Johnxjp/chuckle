"""Smoke tests for the Streamlit UI using streamlit.testing.v1.AppTest.

These tests do not call OpenRouter; they only verify wiring between the
uploader, ingestion, db, and chat-input enablement.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

FIXTURE = Path(__file__).parent / "fixtures" / "feeds_only.csv"
EMPTY_CSV = b"Type,Start,End,Duration,Start Condition,Start Location,End Condition,Notes\n"


def _new_app() -> AppTest:
    return AppTest.from_file(str(Path(__file__).parent.parent / "app.py")).run()


def test_chat_disabled_before_upload():
    at = _new_app()
    assert at.chat_input[0].disabled is True


def test_single_upload_ingests_and_enables_chat():
    at = _new_app()
    at.sidebar.get("file_uploader")[0].upload(
        "feeds_only.csv", FIXTURE.read_bytes(), "text/csv"
    ).run()

    assert at.session_state.last_row_count == 5
    assert at.session_state.last_type_counts == {"Feed": 5}
    assert at.session_state.db_ready is True
    assert at.chat_input[0].disabled is False
    successes = [s.value for s in at.sidebar.get("success")]
    assert successes == ["5 rows ingested"]


def test_reupload_same_file_is_idempotent():
    at = _new_app()
    fu = at.sidebar.get("file_uploader")[0]
    fu.upload("feeds_only.csv", FIXTURE.read_bytes(), "text/csv").run()
    fu.upload("feeds_only.csv", FIXTURE.read_bytes(), "text/csv").run()
    assert at.session_state.last_row_count == 5


def test_csv_with_no_feed_rows_warns_and_keeps_chat_disabled():
    at = _new_app()
    at.sidebar.get("file_uploader")[0].upload("empty.csv", EMPTY_CSV, "text/csv").run()

    warnings = [w.value for w in at.sidebar.get("warning")]
    assert warnings == ["No rows found in the CSV."]
    assert at.chat_input[0].disabled is True


def test_agent_error_swallowed_and_sorry_message_stored():
    at = _new_app()
    at.sidebar.get("file_uploader")[0].upload(
        "feeds_only.csv", FIXTURE.read_bytes(), "text/csv"
    ).run()

    with patch("agent.answer", side_effect=RuntimeError("API down")):
        at.chat_input[0].set_value("When was the last feed?").run()

    msgs = at.session_state.messages
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
    assert "Sorry" in msgs[1]["content"]
