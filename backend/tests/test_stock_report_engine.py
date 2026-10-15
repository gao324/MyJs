"""引擎接入契约: 三态分发、非法值 fail-closed、NDJSON 事件序列。"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.services import stock_analyzer, stock_report_jobs, stock_report_pipeline


@pytest.fixture(autouse=True)
def _isolated_jobs():
    """任务表是模块全局状态: 每个测试前后清空, 防止跨文件污染导致轮询挂起。"""
    stock_report_jobs._JOBS.clear()
    yield
    stock_report_jobs._JOBS.clear()


async def _collect(agen):
    return [chunk async for chunk in agen]


def _events(chunks):
    return [json.loads(c) for c in chunks]


def test_invalid_engine_yields_error(monkeypatch):
    monkeypatch.setattr(stock_analyzer.settings, "stock_analysis_engine", "nope", raising=False)
    out = asyncio.run(_collect(stock_analyzer.analyze_stock_stream(None, None, "600519.SH")))
    events = _events(out)
    assert events[0]["type"] == "error"


def test_default_engine_is_report():
    """代码默认值改为 report(运行时仍可被本地 .env / 环境变量覆盖)。"""
    from app.config import Settings

    assert Settings.model_fields["stock_analysis_engine"].default == "report"
    assert set(stock_analyzer.VALID_ENGINES) == {"report", "dsa", "legacy"}


def test_report_engine_streams_progress_and_done(monkeypatch):
    monkeypatch.setattr(stock_analyzer.settings, "stock_analysis_engine", "report", raising=False)

    def _fake_spawn(job, runner):
        # 不真正跑任务: 直接把作业置为完成, 验证事件映射(meta/delta/done)
        job.update({"status": "done", "report_id": "sr1",
                    "report_url": "/api/stock-reports/sr1/html",
                    "phase": "done", "progress": 100})

    monkeypatch.setattr(stock_report_jobs, "_spawn", _fake_spawn)
    monkeypatch.setattr(stock_report_pipeline, "POLL_SECONDS", 0.01)
    out = asyncio.run(_collect(stock_report_pipeline.analyze_stock_stream(
        None, None, "600519.SH")))
    events = _events(out)
    assert events[0]["type"] == "meta" and events[0]["symbol"] == "600519.SH"
    assert events[-1]["type"] == "done" and events[-1]["report_id"] == "sr1"
    assert events[-1]["report_url"] == "/api/stock-reports/sr1/html"


def test_report_job_marks_config_failure(monkeypatch):
    from app.services import stock_report_data

    def _boom(symbol, force=False):
        raise stock_report_data.ReportDataUnavailableError("数据源不可用")

    monkeypatch.setattr(stock_report_data, "collect_blocks", _boom)
    job = stock_report_jobs.start("600519.SH", "")
    asyncio.run(stock_report_pipeline.run_report_job(job))
    assert job["status"] == "error" and "数据源不可用" in job["error"]


def test_full_job_renders_and_publishes_with_fixture_data(monkeypatch, tmp_path):
    """全链路集成: 采集与 LLM 用桩, 渲染/终验/存储走真实实现。"""
    import json
    from pathlib import Path

    from app.services import report_shards, stock_report_data, stock_report_store

    monkeypatch.setattr(stock_report_data.settings, "data_dir", tmp_path, raising=False)
    monkeypatch.setattr(stock_report_store, "_base_dir", lambda: tmp_path / "user_data")

    fx = Path(__file__).parent / "fixtures" / "stock_report"
    blocks = json.loads((fx / "data_600519.json").read_text(encoding="utf-8"))["blocks"]
    content = json.loads((fx / "content_600519.json").read_text(encoding="utf-8"))

    monkeypatch.setattr(stock_report_data, "collect_blocks",
                        lambda symbol, force=False: (blocks, []))

    async def _fake_shards(code, name, blocks):
        return content, []

    monkeypatch.setattr(report_shards, "analyze_shards", _fake_shards)

    job = stock_report_jobs.start("600519.SH", "贵州茅台")
    asyncio.run(stock_report_pipeline.run_report_job(job))

    assert job["status"] == "done"
    assert job["report_id"]
    record = stock_report_store.get_report(job["report_id"])
    assert record is not None and record["fill_rate"] >= 0.5
    assert stock_report_store.html_path(job["report_id"]).exists()
    drafts = list((tmp_path / "user_data" / "stock_reports").glob("stock-report-draft-*"))
    assert drafts == []


def test_report_job_rejected_when_limiter_full():
    """并发预算占满时直接失败(不排队): heavy_job_limiter 共享预算。"""
    from app.services.heavy_job_limiter import heavy_job_limiter

    assert heavy_job_limiter.acquire("normal", timeout=0) is True
    assert heavy_job_limiter.acquire("normal", timeout=0) is True
    try:
        job = stock_report_jobs.start("600519.SH", "")
        asyncio.run(stock_report_pipeline.run_report_job(job))
        assert job["status"] == "error" and "繁忙" in job["error"]
    finally:
        heavy_job_limiter.release("normal")
        heavy_job_limiter.release("normal")
