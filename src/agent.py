"""LangChain agent setup and streaming query function."""

from __future__ import annotations

import json
import logging
import os
import queue
import sqlite3
import threading
from collections.abc import Generator
from datetime import datetime
from typing import Any

import logfire
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import LLMResult
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from opentelemetry import context as otel_context

import db
from prompts import build_system_prompt

DEFAULT_MODEL = "openai/gpt-4o-mini"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_AGENT_STOPPED = "agent stopped due to iteration limit"
_FALLBACK_MSG = "Sorry, I couldn't answer that from the data."
_SENTINEL = object()
_FALLBACK = object()

_log = logging.getLogger(__name__)


class _FinalAnswerHandler(BaseCallbackHandler):
    """Queues final-answer tokens; silently discards tool-call JSON fragments.

    Strategy: buffer every on_llm_new_token event. On on_llm_end, flush the
    buffer to the queue only if the response was plain text (no tool_calls).
    This avoids leaking JSON tool-call fragments to the UI.
    """

    def __init__(self, q: queue.Queue[str | object]) -> None:
        self._q = q
        self._buffer: list[str] = []

    def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        self._buffer.append(token)

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        try:
            gen = response.generations[0][0]
            has_tool_calls = bool(getattr(getattr(gen, "message", None), "tool_calls", None))
        except (IndexError, AttributeError):
            has_tool_calls = False
        if not has_tool_calls:
            for tok in self._buffer:
                self._q.put(tok)
        self._buffer.clear()

    def on_llm_error(self, error: Exception, **kwargs: Any) -> None:
        self._buffer.clear()


def _build_tools(conn: sqlite3.Connection) -> list:
    @tool
    def query_database(sql: str) -> str:
        """Run a single read-only SQL SELECT against the events table and
        return the rows as a JSON array. Use this for any question about the
        baby's activity data. Only SELECT is allowed."""
        with logfire.span("tool: query_database", sql=sql):
            try:
                rows = db.run_select(conn, sql)
            except (ValueError, sqlite3.Error) as exc:
                logfire.warn("query rejected", error=str(exc))
                return f"ERROR: {exc}"
            logfire.debug("query returned {row_count} rows", row_count=len(rows))
            return json.dumps(rows, default=str)

    return [query_database]


def build_agent(now: datetime, conn: sqlite3.Connection | None = None) -> AgentExecutor:
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
        streaming=True,
    )

    tools = _build_tools(conn)
    schema_context = db.get_schema_context(conn)
    system_prompt = build_system_prompt(now, schema_context)
    escaped_system = system_prompt.replace("{", "{{").replace("}", "}}")
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", escaped_system),
            MessagesPlaceholder("chat_history", optional=True),
            ("human", "{input}"),
            ("placeholder", "{agent_scratchpad}"),
        ]
    )

    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, max_iterations=10, verbose=False)


def _to_lc_messages(history: list[dict]) -> list:
    messages = []
    for m in history:
        if m["role"] == "user":
            messages.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            messages.append(AIMessage(content=m["content"]))
    return messages


def answer(
    question: str,
    now: datetime,
    conn: sqlite3.Connection | None = None,
    history: list[dict] | None = None,
) -> Generator[str, None, None]:
    """Stream the agent's final answer token by token.

    Runs the agent in a daemon thread. Tool-call JSON fragments are filtered
    by _FinalAnswerHandler; only final-answer tokens reach the caller. Yields
    _FALLBACK_MSG as a single chunk if the agent errors or produces no output.
    """
    q: queue.Queue[str | object] = queue.Queue()
    handler = _FinalAnswerHandler(q)
    ctx = otel_context.get_current()  # capture span context for the thread

    def _run() -> None:
        token = otel_context.attach(ctx)
        try:
            executor = build_agent(now, conn=conn)
            result = executor.invoke(
                {"input": question, "chat_history": _to_lc_messages(history or [])},
                config={"callbacks": [handler]},
            )
            output = result.get("output", "")
            if not output or _AGENT_STOPPED in output.lower():
                q.put(_FALLBACK)
            else:
                logfire.info("agent answered", question=question, answer=output)
        except Exception:
            _log.exception("Agent error for question: %r", question)
            logfire.exception("agent error", question=question)
            q.put(_FALLBACK)
        finally:
            otel_context.detach(token)
            q.put(_SENTINEL)

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    yielded_any = False
    for item in iter(q.get, _SENTINEL):
        if item is _FALLBACK:
            if not yielded_any:
                yield _FALLBACK_MSG
        else:
            yield str(item)
            yielded_any = True

    t.join()
