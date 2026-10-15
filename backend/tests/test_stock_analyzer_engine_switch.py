"""个股分析引擎分发测试 — dsa / legacy 开关与 fail-closed 行为。

开关: settings.stock_analysis_engine (env STOCK_ANALYSIS_ENGINE)。
"""

from __future__ import annotations

import json
from datetime import date

import polars as pl
import pytest

from app.config import settings
from app.services import stock_analyzer


def _kline() -> pl.DataFrame:
    return pl.DataFrame({
        "date": [date(2026, 3, 2)] * 3,
        "open": [10.0, 10.5, 11.0],
        "high": [10.8, 11.2, 11.5],
        "low": [9.8, 10.2, 10.9],
        "close": [10.5, 11.0, 11.2],
        "raw_close": [10.5, 11.0, 11.2],
    })


class _Repo:
    def resolve_asset_type(self, symbol: str) -> str:
        return "stock"

    def get_daily_asset(self, asset_type, symbol, start, end, columns=None) -> pl.DataFrame:
        return _kline()


async def _collect() -> list[dict]:
    chunks = [
        chunk
        async for chunk in stock_analyzer.analyze_stock_stream(_Repo(), None, "600000.SH")
    ]
    return [json.loads(chunk) for chunk in chunks]


@pytest.mark.asyncio
async def test_dsa_engine_routes_to_dsa(monkeypatch) -> None:
    monkeypatch.setattr(settings, "stock_analysis_engine", "dsa")
    seen: dict = {}

    async def fake_stream(repo, data_dir, symbol, focus=""):
        seen["symbol"] = symbol
        yield json.dumps({"type": "done"})

    from app.services import stock_analyzer_dsa

    monkeypatch.setattr(stock_analyzer_dsa, "analyze_stock_stream", fake_stream)

    events = await _collect()

    assert seen["symbol"] == "600000.SH"
    assert [e["type"] for e in events] == ["done"]


@pytest.mark.asyncio
async def test_legacy_engine_routes_to_legacy(monkeypatch) -> None:
    monkeypatch.setattr(settings, "stock_analysis_engine", "legacy")

    async def fake_stream(repo, data_dir, symbol, focus=""):
        yield json.dumps({"type": "legacy-marker"})

    from app.services import stock_analyzer_legacy

    monkeypatch.setattr(stock_analyzer_legacy, "analyze_stock_stream", fake_stream)

    events = await _collect()

    assert [e["type"] for e in events] == ["legacy-marker"]


@pytest.mark.asyncio
async def test_invalid_engine_yields_error_event(monkeypatch) -> None:
    monkeypatch.setattr(settings, "stock_analysis_engine", "dsa2")

    events = await _collect()

    assert events[0]["type"] == "error"
    assert "STOCK_ANALYSIS_ENGINE" in events[0]["message"]


@pytest.mark.asyncio
async def test_dsa_without_vendor_dir_fails_closed(monkeypatch) -> None:
    """DSA 引擎缺少 vendor 路径: 先给 meta(图表价位), 再给明确 error, 不静默换实现。"""
    monkeypatch.setattr(settings, "stock_analysis_engine", "dsa")
    monkeypatch.setattr(settings, "dsa_vendor_dir", "")

    from app.services import dsa_bridge

    dsa_bridge._cfg = None

    events = await _collect()

    assert [e["type"] for e in events] == ["meta", "error"]
    assert "DSA_VENDOR_DIR" in events[1]["message"]
    assert "STOCK_ANALYSIS_ENGINE=legacy" in events[1]["message"]
