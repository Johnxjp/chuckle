"""Streamlit entry point for Chuckle."""

from __future__ import annotations

from datetime import datetime

import streamlit as st
from dotenv import load_dotenv

import agent as agent_module
import db
import ingest

load_dotenv()

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
                events = ingest.parse_csv(uploaded)
                row_count = db.replace_events(conn, events)
            except Exception as exc:  # noqa: BLE001 - surface to UI; full trace in logs
                st.session_state.last_error = str(exc)
                st.session_state.last_row_count = 0
                st.session_state.db_ready = False
            else:
                st.session_state.last_error = None
                st.session_state.last_row_count = row_count
                st.session_state.db_ready = row_count > 0
            st.session_state.last_upload_id = upload_id

    if st.session_state.last_error:
        st.error(f"Ingest failed: {st.session_state.last_error}")
    elif st.session_state.last_upload_id is not None:
        count = st.session_state.last_row_count
        if count > 0:
            st.success(f"{count} rows ingested")
        else:
            st.warning("No Feed rows found in the CSV (TB-1 ingests Feed only).")

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
        response = agent_module.answer(question, now=datetime.now(), conn=conn)
        st.write(response)
    st.session_state.messages.append({"role": "assistant", "content": response})
