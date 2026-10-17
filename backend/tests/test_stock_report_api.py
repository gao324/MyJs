"""研报 API 契约: 生成/状态/HTML/列表、同 symbol 防重、失败 fail-closed。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import stock_reports as api_mod
from app.services import stock_report_jobs, stock_report_store


@pytest.fixture(autouse=True)
def _isolated_jobs():
    """任务表是模块全局状态: 每个测试前后清空, 防止 running 残留污染后续测试文件。"""
    stock_report_jobs._JOBS.clear()
    yield
    stock_report_jobs._JOBS.clear()


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(stock_report_store, "_base_dir", lambda: tmp_path, raising=False)
    app = FastAPI()
    app.state.repo = object()
    app.include_router(api_mod.router)
    return TestClient(app)


def test_generate_then_status(client, monkeypatch):
    started: list[str] = []

    def _fake_spawn(job, runner):
        started.append(job["id"])

    monkeypatch.setattr(stock_report_jobs, "_spawn", _fake_spawn)
    res = client.post("/api/stock-reports/generate", json={"symbol": "600519.SH"})
    assert res.status_code == 200
    job = res.json()["job"]
    assert job["status"] == "running" and started == [job["id"]]

    again = client.post("/api/stock-reports/generate", json={"symbol": "600519.SH"})
    assert again.json()["job"]["id"] == job["id"]  # 防重
    assert again.json()["deduped"] is True


def test_generate_passes_repository_to_rotation_context(client, monkeypatch):
    import asyncio

    from app.services import stock_report_pipeline

    received = []
    dispatched = {}

    async def _fake_run(job, repo=None):
        received.append(repo)

    def _capture_runner(job, runner):
        dispatched.update(job=job, runner=runner)

    monkeypatch.setattr(stock_report_pipeline, "run_report_job", _fake_run)
    monkeypatch.setattr(stock_report_jobs, "_spawn", _capture_runner)
    response = client.post("/api/stock-reports/generate", json={"symbol": "600519.SH"})
    assert response.status_code == 200
    asyncio.run(dispatched["runner"](dispatched["job"]))
    assert received == [client.app.state.repo]


def test_generate_rejects_blank_symbol(client):
    res = client.post("/api/stock-reports/generate", json={"symbol": "  "})
    assert res.status_code == 400


def test_status_unknown_job_is_404(client):
    assert client.get("/api/stock-reports/nope").status_code == 404


def test_html_endpoint_serves_saved_report(client, monkeypatch, tmp_path):
    monkeypatch.setattr(stock_report_store, "_base_dir", lambda: tmp_path)
    meta = stock_report_store.save_report(
        {"symbol": "600519.SH", "name": "贵州茅台", "status": "done"}, "<html>x</html>")
    res = client.get(f"/api/stock-reports/{meta['id']}/html")
    assert res.status_code == 200 and res.text == "<html>x</html>"


def test_html_endpoint_missing_report_is_404(client):
    assert client.get("/api/stock-reports/sr-missing/html").status_code == 404


def test_full_html_endpoint_serves_saved_full_report(client, monkeypatch, tmp_path):
    monkeypatch.setattr(stock_report_store, "_base_dir", lambda: tmp_path)
    meta = stock_report_store.save_report(
        {"symbol": "600519.SH", "name": "贵州茅台", "status": "done",
         "full_available": True}, "<html>brief</html>")
    full = stock_report_store.full_html_path(meta["id"])
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text("<html>full</html>", encoding="utf-8")
    res = client.get(f"/api/stock-reports/{meta['id']}/full-html")
    assert res.status_code == 200 and res.text == "<html>full</html>"


def test_full_html_endpoint_rejects_degraded_and_unknown(client, monkeypatch, tmp_path):
    monkeypatch.setattr(stock_report_store, "_base_dir", lambda: tmp_path)
    assert client.get("/api/stock-reports/sr-missing/full-html").status_code == 404
    meta = stock_report_store.save_report(
        {"symbol": "600519.SH", "name": "贵州茅台", "status": "degraded"},
        "<html>x</html>")
    assert client.get(f"/api/stock-reports/{meta['id']}/full-html").status_code == 404


def test_html_path_rejects_traversal():
    import pytest

    with pytest.raises(ValueError):
        stock_report_store.html_path("../../etc/passwd")
    with pytest.raises(ValueError):
        stock_report_store.html_path("")
    assert stock_report_store.html_path("sr123").name == "stock-report-sr123.html"
    with pytest.raises(ValueError):
        stock_report_store.full_html_path("../../etc/passwd")
    assert stock_report_store.full_html_path("sr123").name == "stock-report-sr123-full.html"


def test_list_reports_filters_by_symbol(client, monkeypatch, tmp_path):
    monkeypatch.setattr(stock_report_store, "_base_dir", lambda: tmp_path)
    stock_report_store.save_report({"symbol": "600519.SH", "name": "贵州茅台"}, "<html>a</html>")
    stock_report_store.save_report({"symbol": "000001.SZ", "name": "平安银行"}, "<html>b</html>")
    res = client.get("/api/stock-reports", params={"symbol": "600519.SH"})
    assert [r["symbol"] for r in res.json()["reports"]] == ["600519.SH"]


def test_saved_reports_capped_at_fifty(client, monkeypatch, tmp_path):
    monkeypatch.setattr(stock_report_store, "_base_dir", lambda: tmp_path)
    monkeypatch.setattr(stock_report_store, "MAX_REPORTS", 3)
    for i in range(5):
        stock_report_store.save_report({"symbol": "600519.SH", "name": "贵州茅台"}, f"<html>{i}</html>")
    assert len(stock_report_store.list_reports()) == 3


def test_job_response_has_no_internal_keys(client, monkeypatch):
    """任务派发标记等内部字段不得出现在 API 返回里。"""
    monkeypatch.setattr(stock_report_jobs, "_spawn", lambda job, runner: None)
    res = client.post("/api/stock-reports/generate", json={"symbol": "600519.SH"})
    job = res.json()["job"]
    assert "_spawned" not in job
    status = client.get(f"/api/stock-reports/{job['id']}")
    assert "_spawned" not in status.json()["job"]


def test_finished_jobs_pruned_beyond_cap(monkeypatch):
    """任务表超限时丢弃最旧终态任务, running 任务永不丢弃。"""
    monkeypatch.setattr(stock_report_jobs, "MAX_JOBS", 3)
    saved = dict(stock_report_jobs._JOBS)
    stock_report_jobs._JOBS.clear()
    try:
        ids = [stock_report_jobs.start(f"60000{i}.SH", "")["id"] for i in range(5)]
        for jid in ids:
            stock_report_jobs.update(jid, status="done")
        running = stock_report_jobs.start("600519.SH", "")
        assert len(stock_report_jobs._JOBS) == 3
        assert running["id"] in stock_report_jobs._JOBS
    finally:
        stock_report_jobs._JOBS.clear()
        stock_report_jobs._JOBS.update(saved)
