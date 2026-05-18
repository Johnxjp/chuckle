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

load_dotenv()

logfire.configure()
logfire.instrument_openai()
logfire.instrument_sqlite3()

_SORRY = "Sorry, I couldn't answer that from the data."

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
    elif st.session_state.last_upload_id is not None:
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
        status = st.status("Searching your data...", expanded=False)
        try:
            with logfire.span("user_query", question=question):
                now = datetime.now()
                response = st.write_stream(agent_module.answer(question, now=now, conn=conn))
            status.update(state="complete", expanded=False)
        except Exception:
            logging.exception("Agent error in UI for question: %r", question)
            response = _SORRY
            st.write(response)
            status.update(state="error", expanded=False)
    st.session_state.messages.append({"role": "assistant", "content": response})
