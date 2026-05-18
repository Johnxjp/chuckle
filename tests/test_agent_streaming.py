"""Tests for TB-4 streaming and TB-5 agent harness."""

from __future__ import annotations

import json
import queue
import uuid
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult, LLMResult
from langchain_core.prompts import ChatPromptTemplate

import agent as agent_module
import db
from agent import _FALLBACK_MSG, _FinalAnswerHandler


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


class TestAnswerGenerator:
    def test_answer_is_a_generator(self):
        gen = agent_module.answer("test", now=datetime.now())
        assert hasattr(gen, "__next__")
        list(gen)

    def test_yields_fallback_on_build_exception(self, monkeypatch):
        monkeypatch.setattr(agent_module, "build_agent", lambda *a, **kw: (_ for _ in []))

        def _raise(*args, **kwargs):
            raise RuntimeError("API down")

        monkeypatch.setattr(agent_module, "build_agent", _raise)
        assert list(agent_module.answer("test", now=datetime.now())) == [_FALLBACK_MSG]

    def test_yields_fallback_when_agent_stopped(self, monkeypatch):
        mock_exec = MagicMock()
        mock_exec.invoke.return_value = {"output": "Agent stopped due to iteration limit"}
        monkeypatch.setattr(agent_module, "build_agent", lambda *a, **kw: mock_exec)
        assert list(agent_module.answer("test", now=datetime.now())) == [_FALLBACK_MSG]

    def test_yields_fallback_on_empty_output(self, monkeypatch):
        mock_exec = MagicMock()
        mock_exec.invoke.return_value = {"output": ""}
        monkeypatch.setattr(agent_module, "build_agent", lambda *a, **kw: mock_exec)
        assert list(agent_module.answer("test", now=datetime.now())) == [_FALLBACK_MSG]


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

    def test_empty_table_returns_empty_array(self, mem_conn):
        [query_db] = agent_module._build_tools(mem_conn)
        result = query_db.invoke({"sql": "SELECT * FROM events"})
        assert json.loads(result) == []

    def test_drop_returns_error_string_not_raises(self, mem_conn):
        [query_db] = agent_module._build_tools(mem_conn)
        result = query_db.invoke({"sql": "DROP TABLE events"})
        assert result.startswith("ERROR:")
        assert "SELECT" in result

    def test_insert_returns_error_string(self, mem_conn):
        [query_db] = agent_module._build_tools(mem_conn)
        result = query_db.invoke(
            {"sql": "INSERT INTO events (type, start_time) VALUES ('Bath', '2026-01-01 10:00:00')"}
        )
        assert result.startswith("ERROR:")

    def test_multi_statement_returns_error_string(self, mem_conn):
        [query_db] = agent_module._build_tools(mem_conn)
        result = query_db.invoke({"sql": "SELECT 1; DROP TABLE events"})
        assert result.startswith("ERROR:")


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

    def test_select_only_enforced_in_agent_flow(self, mem_conn):
        tool_call_msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "query_database",
                    "args": {"sql": "DELETE FROM events"},
                    "id": "call_002",
                    "type": "tool_call",
                }
            ],
        )
        final_msg = AIMessage(content="I cannot delete data.")

        executor = _make_executor(mem_conn, [tool_call_msg, final_msg])
        result = executor.invoke({"input": "Delete all events."})
        assert result["output"] == "I cannot delete data."
        assert mem_conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
