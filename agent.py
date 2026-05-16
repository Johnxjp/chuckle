"""LangChain agent setup and query function.

TB-1 wires the smallest possible end-to-end agent: one tool (`query_database`)
that runs a read-only SELECT against the events table, and a hardcoded system
prompt with the live DDL. Streaming, temporal reasoning, and the full prompt
arrive in TB-3 / TB-4.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

import db
from prompts import build_system_prompt

DEFAULT_MODEL = "openai/gpt-4o-mini"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _live_ddl(conn: sqlite3.Connection) -> str:
    cursor = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE sql IS NOT NULL AND name IN ('events', 'idx_events_type', 'idx_events_start')"
    )
    return "\n\n".join(row[0] for row in cursor.fetchall())


def _build_tools(conn: sqlite3.Connection) -> list:
    @tool
    def query_database(sql: str) -> str:
        """Run a single read-only SQL SELECT against the events table and
        return the rows as a JSON array. Use this for any question about the
        baby's activity data. Only SELECT is allowed."""
        try:
            rows = db.run_select(conn, sql)
        except (ValueError, sqlite3.Error) as exc:
            return f"ERROR: {exc}"
        return json.dumps(rows, default=str)

    return [query_database]


def build_agent(now: datetime, conn: sqlite3.Connection | None = None) -> AgentExecutor:
    del now  # unused in TB-1; lands in TB-3 via build_temporal_context.
    conn = conn or db.init_db()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    model_name = os.environ.get("CHUCKLE_MODEL", DEFAULT_MODEL)

    llm = ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=OPENROUTER_BASE_URL,
        temperature=0,
    )

    tools = _build_tools(conn)
    system_prompt = build_system_prompt(_live_ddl(conn))
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_prompt),
            ("human", "{input}"),
            ("placeholder", "{agent_scratchpad}"),
        ]
    )

    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, max_iterations=3, verbose=False)


def answer(question: str, now: datetime, conn: sqlite3.Connection | None = None) -> str:
    executor = build_agent(now, conn=conn)
    result = executor.invoke({"input": question})
    return result.get("output", "")
