"""LangChain agent setup and streaming query function."""

from __future__ import annotations

import json
import logging
import os
import queue
import sqlite3
import threading
import time
from collections.abc import Generator
from dataclasses import dataclass
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
from openai import APIError, RateLimitError
from opentelemetry import context as otel_context

import db
from prompts import build_system_prompt

DEFAULT_MODEL = "openai/gpt-4o-mini"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_AGENT_STOPPED = "agent stopped due to iteration limit"
_FALLBACK_MSG = "Sorry, I couldn't answer that from the data."

# Extra attempts after the first 429, and the pause between them.
RATE_LIMIT_RETRIES = 2
RATE_LIMIT_PAUSE_SECONDS = 5.0

_SENTINEL = object()
_FALLBACK = object()

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolStatus:
    """Signal that the agent has started running a tool, for UX rendering."""

    tool: str


@dataclass(frozen=True)
class AgentError:
    """Signal that the agent hit an upstream error, for UX rendering.

    kind is one of: "rate_limit_retry", "rate_limit_exhausted", "server_error".
    The app maps each kind to a user-facing message.
    """

    kind: str


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

    def on_tool_start(self, serialized: dict, input_str: str, **kwargs: Any) -> None:
        name = (serialized or {}).get("name") or "tool"
        self._q.put(ToolStatus(tool=name))


def _build_tools(conn: sqlite3.Connection) -> list:
    @tool
    def query_database(sql: str) -> str:
        """Run a single read-only SQL SELECT against the events table and
        return the rows as a JSON array. Use this for any question about the
        baby's activity data. Only SELECT is allowed."""
        _TRUNCATION_THRESHOLD = 100  # rows
        with logfire.span("tool: query_database", sql=sql):
            try:
                rows = db.run_select(conn, sql)
            except (ValueError, sqlite3.Error) as exc:
                logfire.warn("query rejected", error=str(exc))
                return json.dumps({"status": "error", "message": str(exc)})
            logfire.debug("query returned {row_count} rows", row_count=len(rows))
            truncated = len(rows) > _TRUNCATION_THRESHOLD  # arbitrary truncation threshold
            if truncated:
                rows = rows[
                    :_TRUNCATION_THRESHOLD
                ]  # very rough truncation to avoid cutting in middle of row
            logfire.debug("query truncated: {truncated}", truncated=truncated)
            rows = json.dumps(rows, default=str)
            return json.dumps(
                {
                    "status": "truncated" if truncated else "ok",
                    "row_count": len(rows),
                    "rows": rows,
                    "message": (
                        f"Too many rows. First {_TRUNCATION_THRESHOLD} rows truncated. There may be more data. "
                        "Inform the user and suggest narrowing the date range."
                        if truncated
                        else None
                    ),
                },
                default=str,
            )

    return [query_database]


def build_agent(
    now: datetime,
    model: str | None = None,
    temperature: float = 0.0,
    max_tokens: int | None = None,
    conn: sqlite3.Connection | None = None,
    return_intermediate_steps: bool = False,
) -> AgentExecutor:
    conn = conn or db.init_db()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    model = model or os.environ.get("CHUCKLE_MODEL", DEFAULT_MODEL)

    llm = ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=OPENROUTER_BASE_URL,
        temperature=temperature,
        max_tokens=max_tokens,
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
    return AgentExecutor(
        agent=agent,
        tools=tools,
        max_iterations=10,
        verbose=False,
        return_intermediate_steps=return_intermediate_steps,
    )


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
) -> Generator[str | ToolStatus | AgentError, None, None]:
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
        inputs = {"input": question, "chat_history": _to_lc_messages(history or [])}
        try:
            executor = build_agent(now, conn=conn)
            for attempt in range(RATE_LIMIT_RETRIES + 1):
                try:
                    result = executor.invoke(inputs, config={"callbacks": [handler]})
                except RateLimitError:
                    _log.warning(
                        "Rate limited (attempt %d/%d) for question: %r",
                        attempt + 1,
                        RATE_LIMIT_RETRIES + 1,
                        question,
                    )
                    logfire.warn("agent rate limited", question=question, attempt=attempt + 1)
                    if attempt == RATE_LIMIT_RETRIES:
                        q.put(AgentError("rate_limit_exhausted"))
                        return
                    q.put(AgentError("rate_limit_retry"))
                    time.sleep(RATE_LIMIT_PAUSE_SECONDS)
                    continue
                except APIError:
                    _log.exception("API error for question: %r", question)
                    logfire.exception("agent api error", question=question)
                    q.put(AgentError("server_error"))
                    return

                output = result.get("output", "")
                if not output or _AGENT_STOPPED in output.lower():
                    q.put(_FALLBACK)
                else:
                    logfire.info("agent answered", question=question, answer=output)
                return
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
        elif isinstance(item, (ToolStatus, AgentError)):
            yield item
        else:
            yield str(item)
            yielded_any = True

    t.join()
