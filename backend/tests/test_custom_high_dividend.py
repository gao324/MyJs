"""高股息筛选策略 — 契约与口径测试。

真值 fixture 来自 2026-10 对 t_gzf step2 的逐笔复核 (tushare/GM/东财三源一致):
  农业银行 601288: 2023/2024/2025 = 6.10% / 4.32% / 4.71%  → 三年全达标, 入选
  华友钴业 603799: 0.61% / 3.42% / 0.73%                    → 2023/2025 不达标
  好想你   002582: 1.30% / 12.08% / 9.05%                   → 2023 不达标

口径 (策略 docstring 有完整说明):
  股息率(Y) = 除权日在 Y 年的每股税前现金分红合计 ÷ Y 年最后交易日不复权收盘价
  连续 N 个完整年度全 ≥ min_yield 才命中; 数据缺失 fail-closed。
"""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from app.strategy import market_data
from app.strategy.engine import StrategyEngine

_NAME = "custom_high_dividend"
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "strategies"
_DATA_COPY = (
    Path(__file__).resolve().parents[2]
    / "data" / "strategies" / "custom" / f"{_NAME}.py"
)


def _load_fixture(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_fixture", _FIXTURES / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


strategy = _load_fixture(_NAME)


# 已验证的三只股票真值 (除权日, 每股税前分红) / (年末最后交易日, 不复权收盘价)
EVENTS = [
    ("601288.SH", date(2023, 7, 18), 0.2222),
    ("601288.SH", date(2024, 6, 7), 0.2309),
    ("601288.SH", date(2025, 1, 8), 0.1164),
    ("601288.SH", date(2025, 7, 17), 0.1255),
    ("601288.SH", date(2025, 12, 15), 0.1195),
    ("601288.SH", date(2026, 5, 13), 0.13),
    ("603799.SH", date(2023, 6, 1), 0.2),
    ("603799.SH", date(2024, 6, 17), 1.0),
    ("603799.SH", date(2025, 6, 11), 0.5),
    ("603799.SH", date(2026, 5, 19), 0.5),
    ("002582.SZ", date(2023, 6, 2), 0.1),
    ("002582.SZ", date(2024, 5, 27), 0.6),
    ("002582.SZ", date(2024, 9, 4), 0.4),
    ("002582.SZ", date(2025, 5, 26), 1.0),
    ("002582.SZ", date(2026, 6, 1), 0.6),
]
CLOSES = [
    ("601288.SH", date(2023, 12, 29), 3.64),
    ("601288.SH", date(2024, 12, 31), 5.34),
    ("601288.SH", date(2025, 12, 31), 7.68),
    ("601288.SH", date(2026, 12, 31), 7.90),
    ("603799.SH", date(2023, 12, 29), 32.93),
    ("603799.SH", date(2024, 12, 31), 29.26),
    ("603799.SH", date(2025, 12, 31), 68.26),
    ("002582.SZ", date(2023, 12, 29), 7.67),
    ("002582.SZ", date(2024, 12, 31), 8.28),
    ("002582.SZ", date(2025, 12, 31), 11.05),
]


def _filter_rows(rows, symbols, start, end, date_idx):
    out = []
    for row in rows:
        if symbols is not None and row[0] not in symbols:
            continue
        if start is not None and row[date_idx] < start:
            continue
        if end is not None and row[date_idx] > end:
            continue
        out.append(row)
    return out


def _fake_events(symbols=None, start=None, end=None):
    """模拟 KlineRepository.get_adj_factor_events 的过滤语义。"""
    return _rows_to_events(_filter_rows(EVENTS, symbols, start, end, 1))


def _fake_closes(symbols=None, start=None, end=None):
    """模拟 KlineRepository.get_raw_daily_close 的过滤语义。"""
    return _rows_to_closes(_filter_rows(CLOSES, symbols, start, end, 1))


def _patch_reads(monkeypatch, events, closes) -> None:
    monkeypatch.setattr(
        market_data, "get_dividend_events",
        lambda symbols=None, start=None, end=None: _rows_to_events(
            _filter_rows(events, symbols, start, end, 1)))
    monkeypatch.setattr(
        market_data, "get_raw_daily_close",
        lambda symbols=None, start=None, end=None: _rows_to_closes(
            _filter_rows(closes, symbols, start, end, 1)))


@pytest.fixture()
def fake_reads(monkeypatch):
    monkeypatch.setattr(market_data, "get_dividend_events", _fake_events)
    monkeypatch.setattr(market_data, "get_raw_daily_close", _fake_closes)


def _window(symbols: list[str], dates: list[date]) -> pl.DataFrame:
    """策略只需要窗口里的 symbol/date (数据自行读取), close 仅占位。"""
    return pl.DataFrame({
        "symbol": [s for d in dates for s in symbols],
        "date": [d for d in dates for _ in symbols],
        "close": [1.0] * (len(symbols) * len(dates)),
    })


# ── 真值: 三只股票 ───────────────────────────────────────────────────


def test_nongye_qualifies_with_verified_three_year_yields(fake_reads):
    df = _window(["601288.SH"], [date(2026, 10, 14), date(2026, 10, 15)])
    out = strategy.filter_history(df, {"min_yield": 3.0, "years": 3})

    assert out["symbol"].unique().to_list() == ["601288.SH"]
    assert sorted(out["date"].unique().to_list()) == [date(2026, 10, 14), date(2026, 10, 15)]
    row = out.row(0, named=True)
    # l1 = 最近一个完整年度, 依次往前
    assert row["div_yield_l1"] == pytest.approx(4.7057, abs=0.01)   # 2025
    assert row["div_yield_l2"] == pytest.approx(4.3239, abs=0.01)   # 2024
    assert row["div_yield_l3"] == pytest.approx(6.1044, abs=0.01)   # 2023
    assert row["div_yield_avg"] == pytest.approx(5.0447, abs=0.01)
    assert row["div_yield_years"] == "2025|2024|2023"


def test_huayou_and_haoxiang_excluded(fake_reads):
    """华友钴业 2023=0.61%、好想你 2023=1.30%, 均低于 3% → 不入选。"""
    df = _window(["603799.SH", "002582.SZ"], [date(2026, 10, 15)])
    out = strategy.filter_history(df, {"min_yield": 3.0, "years": 3})
    assert out.is_empty()


def test_two_year_window_keeps_huayou(fake_reads):
    """把年数降到 2 年时, 华友 2024/2025 = 3.42%/0.73% 仍不达标; 好想你 12.08/9.05 达标。"""
    df = _window(["603799.SH", "002582.SZ"], [date(2026, 10, 15)])
    out = strategy.filter_history(df, {"min_yield": 3.0, "years": 2})
    assert out["symbol"].unique().to_list() == ["002582.SZ"]
    row = out.row(0, named=True)
    assert row["div_yield_l1"] == pytest.approx(9.0498, abs=0.01)   # 2025
    assert row["div_yield_l2"] == pytest.approx(12.0773, abs=0.01)  # 2024
    assert row["div_yield_years"] == "2025|2024"


# ── 失败路径 (fail-closed) ──────────────────────────────────────────


def test_missing_target_year_close_fails_closed(fake_reads, monkeypatch):
    """2023 年无本地收盘价 (未回补/未上市) → 该年不可验证 → 剔除。"""
    events = [*EVENTS, ("301999.SZ", date(2024, 6, 3), 0.5), ("301999.SZ", date(2025, 6, 3), 0.5)]
    closes = [*CLOSES, ("301999.SZ", date(2024, 12, 31), 8.0), ("301999.SZ", date(2025, 12, 31), 8.0)]
    _patch_reads(monkeypatch, events, closes)
    df = _window(["301999.SZ"], [date(2026, 10, 15)])
    assert strategy.filter_history(df, {"min_yield": 3.0, "years": 3}).is_empty()


def test_zero_dividend_year_excluded(fake_reads, monkeypatch):
    """2024 年无任何分红 → 连续三年不成立 → 剔除 (即使前后两年高股息)。"""
    events = [("600111.SH", date(2023, 6, 1), 0.5), ("600111.SH", date(2025, 6, 1), 0.5)]
    closes = [("600111.SH", date(2023, 12, 29), 10.0),
              ("600111.SH", date(2024, 12, 31), 10.0),
              ("600111.SH", date(2025, 12, 31), 10.0)]
    _patch_reads(monkeypatch, events, closes)
    df = _window(["600111.SH"], [date(2026, 10, 15)])
    assert strategy.filter_history(df, {"min_yield": 3.0, "years": 3}).is_empty()


def test_threshold_boundary_is_inclusive(fake_reads, monkeypatch):
    """恰好等于阈值算达标 (>= 口径)。"""
    events = [("600222.SH", date(2023, 6, 1), 0.30),
              ("600222.SH", date(2024, 6, 1), 0.30),
              ("600222.SH", date(2025, 6, 1), 0.30)]
    closes = [("600222.SH", date(2023, 12, 29), 10.0),
              ("600222.SH", date(2024, 12, 31), 10.0),
              ("600222.SH", date(2025, 12, 31), 10.0)]
    _patch_reads(monkeypatch, events, closes)
    df = _window(["600222.SH"], [date(2026, 10, 15)])
    out = strategy.filter_history(df, {"min_yield": 3.0, "years": 3})
    assert out.height == 1
    assert out["div_yield_l1"][0] == pytest.approx(3.0)


def test_empty_window_or_missing_columns(fake_reads):
    assert strategy.filter_history(pl.DataFrame(), {}).is_empty()
    assert strategy.filter_history(pl.DataFrame({"symbol": ["A"]}), {}).is_empty()


# ── 年度切换 / 无未来数据 ───────────────────────────────────────────


def test_year_rollover_switches_target_years(fake_reads, monkeypatch):
    """as_of 进入 2027 年后目标年度切换为 2024/2025/2026。

    农业银行 2026 年只有 5 月一笔 0.13 (÷7.90 = 1.65% < 3%) → 2027 年被剔除;
    600000.SH 三年各 0.4÷10 = 4% → 入选。
    """
    events = [*EVENTS,
              ("600000.SH", date(2024, 6, 3), 0.4),
              ("600000.SH", date(2025, 6, 3), 0.4),
              ("600000.SH", date(2026, 6, 3), 0.4)]
    closes = [*CLOSES,
              ("600000.SH", date(2024, 12, 31), 10.0),
              ("600000.SH", date(2025, 12, 31), 10.0),
              ("600000.SH", date(2026, 12, 31), 10.0)]
    _patch_reads(monkeypatch, events, closes)

    df = _window(["601288.SH", "600000.SH"], [date(2027, 3, 31)])
    out = strategy.filter_history(df, {"min_yield": 3.0, "years": 3})
    assert out["symbol"].unique().to_list() == ["600000.SH"]
    assert out.row(0, named=True)["div_yield_years"] == "2026|2025|2024"


def test_future_events_do_not_leak_into_target_years(fake_reads, monkeypatch):
    """2026 年的事件不得进入 2025 年分子 (真值 test_nongye 已逐位断言, 此处显式边界)。"""
    events = [("600333.SH", date(2025, 6, 2), 0.3), ("600333.SH", date(2026, 6, 2), 9.9)]
    closes = [("600333.SH", date(2023, 12, 29), 10.0),
              ("600333.SH", date(2024, 12, 31), 10.0),
              ("600333.SH", date(2025, 12, 31), 10.0)]
    _patch_reads(monkeypatch, events, closes)
    df = _window(["600333.SH"], [date(2026, 10, 15)])
    # 只看 2025: 分子只应含 0.3 (9.9 属 2026 年, 不进任何目标年度)
    out = strategy.filter_history(df, {"min_yield": 1.0, "years": 1})
    assert out.height == 1
    assert out["div_yield_l1"][0] == pytest.approx(3.0, abs=0.01)  # 0.3/10, 不是 9.9/10


# ── 引擎契约 ─────────────────────────────────────────────────────────


def test_strategy_loads_through_engine_contract(tmp_path):
    import shutil

    dest = tmp_path / f"{_NAME}.py"
    shutil.copyfile(_FIXTURES / f"{_NAME}.py", dest)
    engine = StrategyEngine(strategy_dirs=[tmp_path])

    assert engine.load_errors() == []
    assert engine.has(_NAME)
    definition = engine.get(_NAME)
    assert definition.execution_backend == "python_history_legacy"
    assert definition.filter_history_fn is not None
    # python_history_legacy 必须取到 history (引擎在 history 为空时直接报错)
    assert definition.lookback_days >= 2
    assert engine.required_history_bars([_NAME]) == definition.lookback_days
    params = {p["id"]: p for p in definition.meta["params"]}
    assert params["min_yield"]["default"] == 3.0
    assert params["years"]["default"] == 3


def test_deployed_copy_matches_fixture():
    """data/strategies/custom 下的部署副本必须与 fixture 逐字一致 (防两处漂移)。"""
    if not _DATA_COPY.exists():
        pytest.skip("未部署到 data/strategies/custom (干净检出)")
    assert _DATA_COPY.read_text(encoding="utf-8") == (_FIXTURES / f"{_NAME}.py").read_text(encoding="utf-8")


def _rows_to_events(rows) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [r[0] for r in rows],
        "trade_date": [r[1] for r in rows],
        "ex_factor": [1.01] * len(rows),
        "dividend": [r[2] for r in rows],
        "bonus": [0.0] * len(rows),
        "allot": [0.0] * len(rows),
        "allot_price": [0.0] * len(rows),
        "prev_close": [10.0] * len(rows),
    })


def _rows_to_closes(rows) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [r[0] for r in rows],
        "date": [r[1] for r in rows],
        "close": [r[2] for r in rows],
    })
