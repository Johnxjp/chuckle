"""Tests for TB-4 streaming and TB-5 agent harness."""

from __future__ import annotations

import json
import queue
import uuid
from datetime import datetime

import httpx
import pytest
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult, LLMResult
from langchain_core.prompts import ChatPromptTemplate
from openai import APIError, RateLimitError

import agent as agent_module
import db
from agent import _FALLBACK_MSG, AgentError, ToolStatus, _FinalAnswerHandler


@pytest.fixture()
def q() -> queue.Queue:
    return queue.Queue()


@pytest.fixture()
def handler(q: queue.Queue) -> _FinalAnswerHandler:
    return _FinalAnswerHandler(q)


def _plain_result(text: str) -> LLMResult:
    msg = AIMessage(content=text)
    return LLMResult(generations=[[ChatGeneration(message=msg, text=text)]])


def _tool_call_result() -> LLMResult:
    msg = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "query_database",
                "args": {"sql": "SELECT 1"},
                "id": "abc123",
                "type": "tool_call",
            }
        ],
    )
    return LLMResult(generations=[[ChatGeneration(message=msg, text="")]])


def _drain(q: queue.Queue) -> list[str]:
    tokens: list[str] = []
    while not q.empty():
        tokens.append(q.get_nowait())
    return tokens


class TestFinalAnswerHandler:
    def test_emits_tokens_for_plain_text_response(self, handler, q):
        run_id = uuid.uuid4()
        handler.on_llm_new_token("Hello", run_id=run_id)
        handler.on_llm_new_token(" world", run_id=run_id)
        handler.on_llm_end(_plain_result("Hello world"), run_id=run_id)
        assert _drain(q) == ["Hello", " world"]

    def test_discards_tokens_for_tool_call_response(self, handler, q):
        run_id = uuid.uuid4()
        handler.on_llm_new_token('{"name":', run_id=run_id)
        handler.on_llm_new_token('"query_database"', run_id=run_id)
        handler.on_llm_end(_tool_call_result(), run_id=run_id)
        assert _drain(q) == []

    def test_clears_buffer_on_llm_error(self, handler, q):
        run_id = uuid.uuid4()
        handler.on_llm_new_token("partial", run_id=run_id)
        handler.on_llm_error(RuntimeError("boom"), run_id=run_id)
        handler.on_llm_end(_plain_result(""), run_id=run_id)
        assert _drain(q) == []

    def test_two_rounds_only_emits_final_answer_tokens(self, handler, q):
        run_id = uuid.uuid4()
        handler.on_llm_new_token("tool-json-fragment", run_id=run_id)
        handler.on_llm_end(_tool_call_result(), run_id=run_id)

        handler.on_llm_new_token("Leo ", run_id=run_id)
        handler.on_llm_new_token("fed at 10am.", run_id=run_id)
        handler.on_llm_end(_plain_result("Leo fed at 10am."), run_id=run_id)

        assert _drain(q) == ["Leo ", "fed at 10am."]

    def test_on_tool_start_emits_tool_status(self, handler, q):
        handler.on_tool_start({"name": "query_database"}, "SELECT 1", run_id=uuid.uuid4())
        items = _drain(q)
        assert items == [ToolStatus(tool="query_database")]

    def test_on_tool_start_falls_back_when_name_missing(self, handler, q):
        handler.on_tool_start(None, "SELECT 1", run_id=uuid.uuid4())
        items = _drain(q)
        assert items == [ToolStatus(tool="tool")]


class TestAnswerGenerator:
    def test_cross_thread_connection_does_not_raise(self):
        """Regression: connection created in one thread must be usable from another.

        Before the fix, db.get_schema_context(conn) raised sqlite3.ProgrammingError
        when called from the agent daemon thread with a connection from the main thread
        (e.g. via @st.cache_resource).
        """
        import sqlite3
        import threading

        conn = db.init_db(":memory:")
        errors: list[Exception] = []

        def use_in_other_thread() -> None:
            try:
                db.get_schema_context(conn)
            except sqlite3.ProgrammingError as exc:
                errors.append(exc)

        t = threading.Thread(target=use_in_other_thread)
        t.start()
        t.join()

        assert not errors, f"Cross-thread SQLite access raised: {errors[0]}"

    def test_yields_fallback_on_build_exception(self, monkeypatch):
        monkeypatch.setattr(agent_module, "build_agent", lambda *a, **kw: (_ for _ in []))

        def _raise(*args, **kwargs):
            raise RuntimeError("API down")

        monkeypatch.setattr(agent_module, "build_agent", _raise)
        assert list(agent_module.answer("test", now=datetime.now())) == [_FALLBACK_MSG]

    def test_passes_tool_status_through_to_caller(self, monkeypatch):
        class _StatusExecutor:
            def invoke(self, inputs, config=None):
                handler = config["callbacks"][0]
                handler.on_tool_start({"name": "query_database"}, "SELECT 1", run_id=uuid.uuid4())
                return {"output": "done"}

        monkeypatch.setattr(agent_module, "build_agent", lambda *a, **kw: _StatusExecutor())
        out = list(agent_module.answer("q", now=datetime.now()))
        assert ToolStatus(tool="query_database") in out
        assert _FALLBACK_MSG not in out


class _FlakyExecutor:
    """Fake AgentExecutor whose invoke() replays a list of results/exceptions."""

    def __init__(self, side_effects: list) -> None:
        self._side_effects = list(side_effects)
        self.calls = 0

    def invoke(self, inputs, config=None):
        effect = self._side_effects[self.calls]
        self.calls += 1
        if isinstance(effect, Exception):
            raise effect
        return effect


def _rate_limit_error() -> RateLimitError:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(429, request=request)
    return RateLimitError("rate limited", response=response, body=None)


def _api_error() -> APIError:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    return APIError("internal server error", request=request, body=None)


class TestAnswerErrorHandling:
    """429 / 500 paths emit typed AgentError signals, not the data fallback."""

    def _patch(self, monkeypatch, side_effects: list) -> _FlakyExecutor:
        monkeypatch.setattr(agent_module, "RATE_LIMIT_PAUSE_SECONDS", 0)
        executor = _FlakyExecutor(side_effects)
        monkeypatch.setattr(agent_module, "build_agent", lambda *a, **kw: executor)
        return executor

    def test_server_error_signals_once_no_retry(self, monkeypatch):
        executor = self._patch(monkeypatch, [_api_error()])
        out = list(agent_module.answer("q", now=datetime.now()))
        assert out == [AgentError("server_error")]
        assert executor.calls == 1

    def test_rate_limit_then_success_retries(self, monkeypatch):
        executor = self._patch(monkeypatch, [_rate_limit_error(), {"output": "It was 3."}])
        out = list(agent_module.answer("q", now=datetime.now()))
        assert out == [AgentError("rate_limit_retry")]
        assert executor.calls == 2

    def test_rate_limit_exhausted_signals_after_all_attempts(self, monkeypatch):
        errors = [_rate_limit_error() for _ in range(agent_module.RATE_LIMIT_RETRIES + 1)]
        executor = self._patch(monkeypatch, errors)
        out = list(agent_module.answer("q", now=datetime.now()))
        assert out[-1] == AgentError("rate_limit_exhausted")
        assert out.count(AgentError("rate_limit_retry")) == agent_module.RATE_LIMIT_RETRIES
        assert executor.calls == agent_module.RATE_LIMIT_RETRIES + 1


# ---- T-5.5: Agent harness with stub LLM ----


class _CannedChatModel(BaseChatModel):
    """Fake chat model that returns pre-configured messages in sequence."""

    responses: list

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        msg = self.responses.pop(0) if self.responses else AIMessage(content="")
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools, **kwargs):
        return self

    @property
    def _llm_type(self) -> str:
        return "canned"


def _make_executor(conn, responses: list) -> AgentExecutor:
    """Build an AgentExecutor with real tools and a canned fake LLM."""
    tools = agent_module._build_tools(conn)
    fake_llm = _CannedChatModel(responses=responses)
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "Test agent."),
            ("human", "{input}"),
            ("placeholder", "{agent_scratchpad}"),
        ]
    )
    agent = create_tool_calling_agent(fake_llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, max_iterations=3)


@pytest.fixture()
def mem_conn():
    conn = db.init_db(":memory:")
    yield conn
    conn.close()


class TestQueryDatabaseTool:
    """T-5.5: assert tool invoked and SELECT-only enforced."""

    def test_valid_select_returns_json_array(self, mem_conn):
        [query_db] = agent_module._build_tools(mem_conn)
        result = query_db.invoke({"sql": "SELECT 1 AS n"})
        assert json.loads(result) == [{"n": 1}]

    def test_mutation_returns_error_string_not_raises(self, mem_conn):
        [query_db] = agent_module._build_tools(mem_conn)
        result = query_db.invoke({"sql": "DROP TABLE events"})
        assert result.startswith("ERROR:")
        assert "SELECT" in result


class TestBuildAgentModelParams:
    """build_agent forwards model parameters to the LLM."""

    def _capture_chatopenai(self, monkeypatch, mem_conn):
        captured: dict = {}

        def fake_factory(**kwargs):
            captured.update(kwargs)
            return _CannedChatModel(responses=[])

        monkeypatch.setattr(agent_module, "ChatOpenAI", fake_factory)
        return captured

    def test_explicit_params_forwarded_to_llm(self, monkeypatch, mem_conn):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        captured = self._capture_chatopenai(monkeypatch, mem_conn)

        agent_module.build_agent(
            datetime(2026, 5, 7, 19, 0, 0),
            model="anthropic/claude-x",
            temperature=0.7,
            max_tokens=512,
            conn=mem_conn,
        )

        assert captured["model"] == "anthropic/claude-x"
        assert captured["temperature"] == 0.7
        assert captured["max_tokens"] == 512
        assert captured["api_key"] == "test-key"
        assert captured["streaming"] is True

    def test_defaults_preserve_app_behaviour(self, monkeypatch, mem_conn):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("CHUCKLE_MODEL", "env/model")
        captured = self._capture_chatopenai(monkeypatch, mem_conn)

        agent_module.build_agent(datetime(2026, 5, 7, 19, 0, 0), conn=mem_conn)

        assert captured["model"] == "env/model"
        assert captured["temperature"] == 0.0
        assert captured["max_tokens"] is None


class TestAgentWithFakeLLM:
    """T-5.5: stub LLM emitting canned tool calls; assert final string produced."""

    def test_tool_invoked_and_final_string_produced(self, mem_conn):
        db.replace_events(
            mem_conn,
            [
                {
                    "type": "Feed",
                    "start_time": "2026-01-01 08:00:00",
                    "end_time": None,
                    "duration_minutes": 10,
                    "feed_mode": "breast",
                    "feed_left_minutes": 5,
                    "feed_right_minutes": 5,
                }
            ],
        )

        tool_call_msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "query_database",
                    "args": {"sql": "SELECT COUNT(*) AS n FROM events WHERE type='Feed'"},
                    "id": "call_001",
                    "type": "tool_call",
                }
            ],
        )
        final_msg = AIMessage(content="Leo had 1 feed recorded.")

        executor = _make_executor(mem_conn, [tool_call_msg, final_msg])
        result = executor.invoke({"input": "How many feeds are there?"})
        assert result["output"] == "Leo had 1 feed recorded."
