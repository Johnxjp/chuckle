"""Tests for TB-4 streaming: _FinalAnswerHandler and answer() fallback."""

from __future__ import annotations

import queue
import uuid
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

import agent as agent_module
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
