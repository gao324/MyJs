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


def test_report_stream_shows_queue_phase(monkeypatch):
    """额度等待期间前端可见 queued 阶段文案。"""
    monkeypatch.setattr(stock_analyzer.settings, "stock_analysis_engine", "report", raising=False)

    def _fake_spawn(job, runner):
        job.update({"status": "done", "phase": "queued", "progress": 0,
                    "report_id": "sr2", "report_url": "/api/stock-reports/sr2/html",
                    "full_report_url": ""})

    monkeypatch.setattr(stock_report_jobs, "_spawn", _fake_spawn)
    monkeypatch.setattr(stock_report_pipeline, "POLL_SECONDS", 0.01)
    events = _events(asyncio.run(_collect(
        stock_report_pipeline.analyze_stock_stream(None, None, "600519.SH"))))
    deltas = [e for e in events if e["type"] == "delta"]
    assert deltas and "等待系统资源" in deltas[0]["content"]


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


def test_rotation_context_averages_recent_industry_and_concept_leaders(monkeypatch):
    from app.services import rps_rotation

    dates = [f"2026-10-{day:02d}" for day in range(15, 10, -1)]
    def _matrix(repo, days, kind, level=None):
        columns = {day: [["电子", 0.05], ["医药", 0.02]] for day in dates}
        if kind == "concept":
            columns = {day: [["算力", 0.08], ["机器人", 0.03]] for day in dates}
        return {"dates": dates, "columns": columns, "concept_count": 2}

    monkeypatch.setattr(rps_rotation, "build_rps_rotation", _matrix)
    context = stock_report_pipeline._collect_rotation_context(object())
    assert context["available"] is True
    assert context["as_of"] == "2026-10-15"
    assert context["industry_window"] == context["concept_window"] == 5
    assert context["industry"][0] == {"name": "电子", "change_pct": 5.0}
    assert context["concept"][0] == {"name": "算力", "change_pct": 8.0}


def test_rotation_context_fails_closed_without_repository():
    assert stock_report_pipeline._collect_rotation_context(None) == {"available": False}


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
    assert events[-1]["full_report_url"] == ""  # 降级/桩任务没有完整版入口


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
    assert record["full_available"] is True
    compact_path = stock_report_store.html_path(job["report_id"])
    full_path = stock_report_store.full_html_path(job["report_id"])
    assert compact_path.exists() and full_path.exists()
    full_url = f'/api/stock-reports/{job["report_id"]}/full-html'
    assert f'href="{full_url}"' in compact_path.read_text(encoding="utf-8")
    assert job["full_report_url"] == full_url
    drafts = list((tmp_path / "user_data" / "stock_reports").glob("stock-report-draft-*"))
    assert drafts == []


def test_report_job_reports_error_when_limiter_wait_times_out(monkeypatch):
    """排队等待超过上限才失败: 独占管道运行期间研报不再秒失败。"""
    from app.services.heavy_job_limiter import heavy_job_limiter

    monkeypatch.setattr(stock_report_pipeline, "HEAVY_ACQUIRE_TIMEOUT", 0.05)
    assert heavy_job_limiter.acquire("normal", timeout=0) is True
    assert heavy_job_limiter.acquire("normal", timeout=0) is True
    try:
        job = stock_report_jobs.start("600519.SH", "")
        asyncio.run(stock_report_pipeline.run_report_job(job))
        assert job["status"] == "error" and "繁忙" in job["error"]
        assert job["phase"] == "queued"
    finally:
        heavy_job_limiter.release("normal")
        heavy_job_limiter.release("normal")
