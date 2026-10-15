"""引擎接入契约: 三态分发、非法值 fail-closed、NDJSON 事件序列。"""
from __future__ import annotations

import asyncio
import json

from app.services import stock_analyzer, stock_report_jobs, stock_report_pipeline


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
