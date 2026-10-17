"""GM 股息率截面同步服务契约 — 同步落盘 / 快照读取 / 小数转换。

口径: GM dy_ttm/dy_lfy 量纲 % → yield_maps /100 转小数; 非有限值/负值丢弃;
GM 未配置或无数据 → 跳过/空 dict (消费方 fail-closed)。
"""
from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from app.services import dividend_yield_sync as dys


def _valuation_frame(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={
            "symbol": pl.Utf8, "trade_date": pl.Date,
            "dy_ttm": pl.Float64, "dy_lfy": pl.Float64,
        },
    )


class _FakeProvider:
    def __init__(self, frame):
        self._frame = frame
        self.calls: list[tuple] = []

    def get_daily_valuation(self, symbols, fields="dy_ttm,dy_lfy", trade_date=None):
        self.calls.append((symbols, fields, trade_date))
        return self._frame.filter(pl.col("symbol").is_in(symbols))


def _write_snapshot(tmp_path, rows: list[dict]) -> None:
    (tmp_path / "dividend_yields").mkdir(parents=True, exist_ok=True)
    dys.atomic_write_parquet(
        _valuation_frame(rows),
        tmp_path / "dividend_yields" / "part.parquet",
    )


def test_yield_maps_converts_percent_to_decimal(tmp_path):
    _write_snapshot(tmp_path, [
        {"symbol": "601021.SH", "trade_date": date(2026, 10, 16), "dy_ttm": 3.2314, "dy_lfy": 2.4107},
        {"symbol": "002603.SZ", "trade_date": date(2026, 10, 16), "dy_ttm": 5.3121, "dy_lfy": 5.3121},
    ])

    maps = dys.yield_maps(tmp_path)

    assert maps["lfly"] == {"601021.SH": pytest.approx(0.024107), "002603.SZ": pytest.approx(0.053121)}
    assert maps["ttm"] == {"601021.SH": pytest.approx(0.032314), "002603.SZ": pytest.approx(0.053121)}


def test_yield_maps_drops_bad_values(tmp_path):
    _write_snapshot(tmp_path, [
        {"symbol": "A.SZ", "trade_date": date(2026, 10, 16), "dy_ttm": float("nan"), "dy_lfy": -1.0},
        {"symbol": "B.SZ", "trade_date": date(2026, 10, 16), "dy_ttm": None, "dy_lfy": None},
        {"symbol": "C.SZ", "trade_date": date(2026, 10, 16), "dy_ttm": 1.5, "dy_lfy": 0.0},
    ])

    maps = dys.yield_maps(tmp_path)

    assert maps["lfly"] == {"C.SZ": 0.0}
    assert maps["ttm"] == {"C.SZ": 0.015}


def test_yield_maps_missing_table_returns_empty(tmp_path):
    maps = dys.yield_maps(tmp_path)
    assert maps == {"lfly": {}, "ttm": {}}


def test_sync_writes_snapshot_and_chunks(monkeypatch, tmp_path):
    symbols = [f"{i:06d}.{'SH' if i % 2 else 'SZ'}" for i in range(10)]
    frame = _valuation_frame([
        {"symbol": sym, "trade_date": date(2026, 10, 16), "dy_ttm": 1.0, "dy_lfy": 2.0}
        for sym in symbols
    ])
    provider = _FakeProvider(frame)
    monkeypatch.setattr(dys, "_VALUATION_CHUNK", 4)
    monkeypatch.setattr(dys, "_get_symbols", lambda data_dir: symbols)
    monkeypatch.setattr(dys, "gm_available", lambda: True)

    def _fake_get_provider(name):
        assert name == "gm"
        return provider

    monkeypatch.setattr("app.data_providers.custom.get_provider", _fake_get_provider)

    summary = dys.sync_dividend_yields(tmp_path)

    assert summary["rows"] == 10
    assert len(provider.calls) == 3  # 10 个标的按 4 分 3 段
    assert (tmp_path / "dividend_yields" / "part.parquet").exists()
    assert dys.read_dividend_yields(tmp_path).height == 10


def test_sync_skips_when_gm_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(dys, "_get_symbols", lambda data_dir: ["000001.SZ"])
    monkeypatch.setattr(dys, "gm_available", lambda: False)

    summary = dys.sync_dividend_yields(tmp_path)

    assert summary["rows"] == 0
    assert "gm unavailable" in summary.get("skipped", "")
    assert not (tmp_path / "dividend_yields" / "part.parquet").exists()


def test_sync_skips_when_no_symbols(monkeypatch, tmp_path):
    monkeypatch.setattr(dys, "_get_symbols", lambda data_dir: [])

    summary = dys.sync_dividend_yields(tmp_path)

    assert summary["rows"] == 0
    assert summary.get("skipped") == "no symbols"
