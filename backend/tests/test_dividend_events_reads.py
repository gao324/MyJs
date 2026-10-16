"""高股息数据链 — 分红事件与不复权收盘的读取契约测试。

新增 (KlineRepository / market_data):
  - get_adj_factor_events(symbols, start, end): 本地除权除息事件明细窄读
  - get_raw_daily_close(symbols, start, end):  日K不复权收盘价窄读

口径依据 (CONTRIBUTING §3.2): kline_daily 的 close 即不复权原始价,
enriched 的 close 为前复权价 — 股息率分母必须走 get_raw_daily_close,
不得复用 get_daily_batch (其 300 天缓存路径会返回前复权 close)。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from app.strategy import market_data
from app.strategy.ai_generator import AIStrategyGenerator
from app.tickflow.repository import DataStore, KlineRepository


def _repo(tmp_path) -> KlineRepository:
    return KlineRepository(DataStore(tmp_path))


def _write_adj_events(data_dir: Path, rows: list[dict]) -> None:
    d = data_dir / "adj_factor"
    d.mkdir(parents=True, exist_ok=True)
    full = [
        {
            "ex_factor": 1.01,
            "dividend": 0.0,
            "bonus": 0.0,
            "allot": 0.0,
            "allot_price": 0.0,
            "prev_close": 10.0,
            **r,
        }
        for r in rows
    ]
    pl.DataFrame(full).write_parquet(d / "all.parquet")


def _write_daily_rows(data_dir: Path, rows: list[tuple]) -> None:
    """按 date 分区写日K (同一天的多标的合并进同一 part.parquet, 与真实布局一致)。"""
    by_date: dict[date, list[tuple]] = {}
    for symbol, d, c in rows:
        by_date.setdefault(d, []).append((symbol, c))
    for d, items in by_date.items():
        part = data_dir / "kline_daily" / f"date={d.isoformat()}"
        part.mkdir(parents=True, exist_ok=True)
        frame = pl.DataFrame({
            "symbol": [s for s, _ in items],
            "date": [d] * len(items),
            "open": [c for _, c in items],
            "high": [c for _, c in items],
            "low": [c for _, c in items],
            "close": [c for _, c in items],
            "volume": [1.0] * len(items),
            "amount": [1.0] * len(items),
        })
        frame.write_parquet(part / "part.parquet")


# ── KlineRepository.get_adj_factor_events ────────────────────────────


def test_get_adj_factor_events_filters_symbol_and_window(tmp_path):
    _write_adj_events(tmp_path, [
        {"symbol": "601288.SH", "trade_date": date(2023, 7, 18), "dividend": 0.2222},
        {"symbol": "601288.SH", "trade_date": date(2026, 5, 13), "dividend": 0.13},
        {"symbol": "603799.SH", "trade_date": date(2024, 6, 17), "dividend": 1.0},
        {"symbol": "603799.SH", "trade_date": date(2025, 6, 11), "dividend": 0.5},
    ])
    repo = _repo(tmp_path)

    df = repo.get_adj_factor_events(
        symbols=["601288.SH"], start=date(2023, 1, 1), end=date(2025, 12, 31)
    )
    assert df.height == 1
    assert df["trade_date"][0] == date(2023, 7, 18)
    assert df["dividend"][0] == pytest.approx(0.2222)

    # symbols=None → 全市场, 按 symbol/trade_date 升序
    all_df = repo.get_adj_factor_events()
    assert all_df.height == 4
    assert all_df["symbol"].to_list() == [
        "601288.SH", "601288.SH", "603799.SH", "603799.SH",
    ]
    assert all_df["trade_date"].to_list() == [
        date(2023, 7, 18), date(2026, 5, 13),
        date(2024, 6, 17), date(2025, 6, 11),
    ]


def test_get_adj_factor_events_keeps_detail_columns(tmp_path):
    _write_adj_events(tmp_path, [
        {"symbol": "600519.SH", "trade_date": date(2026, 6, 26),
         "ex_factor": 1.023663, "dividend": 28.019, "prev_close": 1212.1},
    ])
    df = _repo(tmp_path).get_adj_factor_events()
    assert df.columns == [
        "symbol", "trade_date", "ex_factor",
        "dividend", "bonus", "allot", "allot_price", "prev_close",
    ]
    assert df["dividend"][0] == pytest.approx(28.019)


def test_get_adj_factor_events_missing_table_returns_empty(tmp_path):
    assert _repo(tmp_path).get_adj_factor_events().is_empty()


# ── KlineRepository.get_raw_daily_close ─────────────────────────────


def test_get_raw_daily_close_reads_unadjusted_close(tmp_path):
    _write_daily_rows(tmp_path, [
        ("601288.SH", date(2023, 12, 29), 3.64),
        ("601288.SH", date(2024, 12, 31), 5.34),
        ("601288.SH", date(2025, 12, 31), 7.68),
        ("603799.SH", date(2023, 12, 29), 32.93),
    ])
    df = _repo(tmp_path).get_raw_daily_close(
        symbols=["601288.SH"], start=date(2023, 1, 1), end=date(2024, 12, 31)
    )
    assert df.columns == ["symbol", "date", "close"]
    assert df["close"].to_list() == [3.64, 5.34]

    # symbols=None → 全部
    all_df = _repo(tmp_path).get_raw_daily_close()
    assert all_df.height == 4


def test_get_raw_daily_close_missing_table_returns_empty(tmp_path):
    assert _repo(tmp_path).get_raw_daily_close().is_empty()


# ── market_data 白名单门面 ───────────────────────────────────────────


class _FakeRepo:
    """最小 fake: 只实现新增的两个只读接口。"""

    def __init__(self):
        self.calls: list[tuple] = []
        self.events = pl.DataFrame({"symbol": ["601288.SH"], "dividend": [0.2222]})
        self.closes = pl.DataFrame({"symbol": ["601288.SH"], "close": [3.64]})

    def get_adj_factor_events(self, symbols=None, start=None, end=None):
        self.calls.append(("events", symbols, start, end))
        return self.events

    def get_raw_daily_close(self, symbols=None, start=None, end=None):
        self.calls.append(("close", symbols, start, end))
        return self.closes


@pytest.fixture()
def fake_repo():
    fake = _FakeRepo()
    market_data._set_repo(fake)
    yield fake
    market_data._reset_repo()


def test_whitelist_allows_dividend_data_import():
    AIStrategyGenerator._validate_safety(
        "from app.strategy.market_data import get_dividend_events, get_raw_daily_close"
    )


def test_market_data_dividend_reads_delegate_with_normalized_dates(fake_repo):
    market_data.get_dividend_events(
        symbols=["601288.SH"], start="2023-01-01", end="2025-12-31"
    )
    kind, syms, s, e = fake_repo.calls[-1]
    assert kind == "events"
    assert syms == ["601288.SH"]
    assert s == date(2023, 1, 1)
    assert e == date(2025, 12, 31)

    market_data.get_raw_daily_close(symbols=["601288.SH"])
    kind, syms, _s, e = fake_repo.calls[-1]
    assert kind == "close"
    assert e == market_data.cn_today()  # 未传 end → 北京今天 (§3.3)

    # symbols=None 透传 None (全市场)
    market_data.get_dividend_events()
    assert fake_repo.calls[-1][1] is None


def test_market_data_dividend_reads_survive_repo_errors(fake_repo, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(fake_repo, "get_adj_factor_events", boom)
    monkeypatch.setattr(fake_repo, "get_raw_daily_close", boom)
    assert market_data.get_dividend_events(symbols=["601288.SH"]).is_empty()
    assert market_data.get_raw_daily_close(symbols=["601288.SH"]).is_empty()
