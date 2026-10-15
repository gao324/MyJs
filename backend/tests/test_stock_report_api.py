"""研报 API 契约: 生成/状态/HTML/列表、同 symbol 防重、失败 fail-closed。"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import stock_reports as api_mod
from app.services import stock_report_jobs, stock_report_store


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(stock_report_store, "_base_dir", lambda: tmp_path, raising=False)
    app = FastAPI()
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
