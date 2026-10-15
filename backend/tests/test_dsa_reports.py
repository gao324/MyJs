"""DSA 问股报告服务测试 - chat 路径透传与失败路径。"""

import pytest

from app.services import dsa_reports


class _FakeResult:
    def __init__(self, *, success=True, content="", error=None):
        self.success = success
        self.content = content
        self.error = error


class _FakeExec:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls = []

    def chat(self, message, session_id, context=None):
        self.calls.append({"message": message, "session_id": session_id, "context": context})
        if self.exc is not None:
            raise self.exc
        return self.result


@pytest.mark.asyncio
async def test_dsa_analyze_stream_passthrough_chat_content(monkeypatch):
    exec_ = _FakeExec(_FakeResult(content="## 综合分析报告\n多头排列"))
    monkeypatch.setattr(dsa_reports.dsa_bridge, "get_agent_executor", lambda: exec_)

    events = [ev async for ev in dsa_reports.dsa_analyze_stream("600579")]

    assert [e["type"] for e in events] == ["delta", "done"]
    # 原生 markdown 原样透传，不做二次渲染
    assert events[0]["content"] == "## 综合分析报告\n多头排列"
    assert exec_.calls[0]["message"] == "请分析股票 600579"
    assert exec_.calls[0]["context"]["stock_code"] == "600579"


@pytest.mark.asyncio
async def test_dsa_analyze_stream_appends_focus_and_uses_fresh_session(monkeypatch):
    exec_ = _FakeExec(_FakeResult(content="ok"))
    monkeypatch.setattr(dsa_reports.dsa_bridge, "get_agent_executor", lambda: exec_)
    seen = set()
    for _ in range(2):
        events = [ev async for ev in dsa_reports.dsa_analyze_stream("600579", focus="看筹码")]
        assert events[0]["type"] == "delta"
        seen.add(exec_.calls[-1]["session_id"])
    assert "看筹码" in exec_.calls[0]["message"]
    # 每次分析全新 session，避免串扰
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_dsa_analyze_stream_yields_error_on_executor_failure(monkeypatch):
    exec_ = _FakeExec(exc=RuntimeError("boom"))
    monkeypatch.setattr(dsa_reports.dsa_bridge, "get_agent_executor", lambda: exec_)

    events = [ev async for ev in dsa_reports.dsa_analyze_stream("600579")]

    assert events and events[0]["type"] == "error"
    assert "boom" in events[0]["message"]


@pytest.mark.asyncio
async def test_dsa_analyze_stream_yields_error_on_unsuccessful_result(monkeypatch):
    exec_ = _FakeExec(_FakeResult(success=False, error="no tool"))
    monkeypatch.setattr(dsa_reports.dsa_bridge, "get_agent_executor", lambda: exec_)

    events = [ev async for ev in dsa_reports.dsa_analyze_stream("600579")]

    assert events and events[0]["type"] == "error"
    assert "no tool" in events[0]["message"]


@pytest.mark.asyncio
async def test_dsa_analyze_stream_yields_error_on_empty_content(monkeypatch):
    exec_ = _FakeExec(_FakeResult(content="  "))
    monkeypatch.setattr(dsa_reports.dsa_bridge, "get_agent_executor", lambda: exec_)

    events = [ev async for ev in dsa_reports.dsa_analyze_stream("600579")]

    assert events and events[0]["type"] == "error"
