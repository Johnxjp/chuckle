"""Streamlit entry point for Chuckle."""

from __future__ import annotations

import logging
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import logfire
import streamlit as st
from dotenv import load_dotenv

import agent as agent_module
import db
import ingest
from constants import FIXED_NOW

load_dotenv()

logfire.configure()
logfire.instrument_openai()
logfire.instrument_sqlite3()

_SORRY = "Sorry, I couldn't answer that from the data."

# User-facing copy for agent error signals. Terminal kinds become the answer
# text (shown outside the box, saved to history); the transient retry kind is
# logged as a line inside the status box.
_RATE_LIMIT_RETRY_MSG = (
    "I'm experiencing some technical issues at the moment. Hold on while I try "
    "again after a short pause."
)
_TERMINAL_ERROR_MSGS = {
    "rate_limit_exhausted": "I'm sorry I couldn't get around the issue. Please try again later",
    "server_error": (
        "Sorry, something went wrong. I'm experiencing some technical issues at "
        "the moment. Can you please try again later"
    ),
}


def _render_stream(events, status):
    """Log progress lines into the status box; yield answer text outside it.

    Tool activity and the transient retry notice are written into `status` as a
    side effect, so they never reach st.write_stream's return value and stay out
    of saved chat history. Answer tokens and terminal error messages are yielded
    so they render below the box and are saved as the assistant's response.
    """
    for ev in events:
        if isinstance(ev, agent_module.ToolStatus):
            status.write(f"Looking up data ({ev.tool})")
        elif isinstance(ev, agent_module.AgentError):
            if ev.kind == "rate_limit_retry":
                status.write(_RATE_LIMIT_RETRY_MSG)
            else:
                yield _TERMINAL_ERROR_MSGS[ev.kind]
        else:
            yield ev


st.set_page_config(page_title="Chuckle", page_icon=None)
st.title("Chuckle")
st.write("Upload a Huckleberry CSV and ask questions about it.")


@st.cache_resource
def _connection():
    return db.init_db()


for key, default in (
    ("messages", []),
    ("db_ready", False),
    ("last_row_count", 0),
    ("last_type_counts", {}),
    ("last_warnings", []),
    ("last_upload_id", None),
    ("last_error", None),
):
    if key not in st.session_state:
        st.session_state[key] = default

conn = _connection()

# Restore db_ready from persistent SQLite after a page refresh
if not st.session_state.db_ready:
    _row_count, _type_counts = db.get_event_summary(conn)
    if _row_count > 0:
        st.session_state.db_ready = True
        st.session_state.last_row_count = _row_count
        st.session_state.last_type_counts = _type_counts

with st.sidebar:
    st.header("Data")
    uploaded = st.file_uploader("Huckleberry CSV", type=["csv"])
    if uploaded is not None:
        upload_id = (uploaded.name, uploaded.size)
        if upload_id != st.session_state.last_upload_id:
            try:
                result = ingest.parse_csv(uploaded)
                row_count = db.replace_events(conn, result.events)
            except Exception as exc:
                st.session_state.last_error = str(exc)
                st.session_state.last_row_count = 0
                st.session_state.last_type_counts = {}
                st.session_state.last_warnings = []
                st.session_state.db_ready = False
            else:
                st.session_state.last_error = None
                st.session_state.last_row_count = row_count
                st.session_state.last_type_counts = dict(Counter(e["type"] for e in result.events))
                st.session_state.last_warnings = result.warnings
                st.session_state.db_ready = row_count > 0
            st.session_state.last_upload_id = upload_id

    if st.session_state.last_error:
        st.error(f"Ingest failed: {st.session_state.last_error}")
    elif st.session_state.db_ready or st.session_state.last_upload_id is not None:
        count = st.session_state.last_row_count
        if count > 0:
            st.success(f"{count} rows ingested")
            for type_name, cnt in sorted(st.session_state.last_type_counts.items()):
                st.write(f"**{type_name}:** {cnt}")
            if st.session_state.last_warnings:
                with st.expander(f"Warnings ({len(st.session_state.last_warnings)})"):
                    for w in st.session_state.last_warnings:
                        st.write(w)
        else:
            st.warning("No rows found in the CSV.")

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.write(message["content"])

question = st.chat_input(
    "Ask about feeds, sleep, nappies...",
    disabled=not st.session_state.db_ready,
)
if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.write(question)
    with st.chat_message("assistant"):
        status = st.status("Processing...", expanded=False)
        try:
            with logfire.span("user_query", question=question):
                now = FIXED_NOW or datetime.now()
                history = st.session_state.messages[:-1][-10:]
                response = st.write_stream(
                    _render_stream(
                        agent_module.answer(question, now=now, conn=conn, history=history),
                        status,
                    )
                )
            status.update(state="complete", expanded=False)
        except Exception:
            logging.exception("Agent error in UI for question: %r", question)
            response = _SORRY
            status.update(state="error")
            st.write(response)
    st.session_state.messages.append({"role": "assistant", "content": response})
    st.rerun()
