"""akshare_ext 日期/报告期/代码匹配纯函数契约(与 stock_ai 同口径)。"""
from __future__ import annotations

import datetime as dt

from app.plugins.akshare_ext import provider as mod


def test_candidate_periods_skip_undisclosed_quarter():
    # 2026-10-15(周四): 三季报(9/30)披露截止 10/31, 不可用 → 起点为半年报,
    # 再逐季回退(20260630 → 20260331 → 20251231)
    assert mod.candidate_periods(dt.date(2026, 10, 15), 3) == [
        "20260630", "20260331", "20251231",
    ]
    assert "20260930" not in mod.candidate_periods(dt.date(2026, 10, 15), 3)


def test_last_report_periods_cover_four_quarters():
    assert mod.last_report_periods(dt.date(2026, 10, 15), 4) == [
        "20260630", "20260331", "20251231", "20250930",
    ]


def test_report_dates_use_previous_trading_day_for_margin():
    # 2026-10-15(周四): 融资融券取上一交易日 10-14; 龙虎榜窗口 40 天
    dates = mod.report_dates(dt.date(2026, 10, 15))
    assert dates["today_s"] == "20261015"
    assert dates["last_trade_prev_s"] == "20261014"
    assert dates["month_ago_s"] == "20260905"
    assert dates["kline_start"] < dates["today_s"]


def test_report_dates_skip_weekend_for_margin():
    # 2026-10-19(周一): 上一交易日应为上周五 10-16, 而非周日
    dates = mod.report_dates(dt.date(2026, 10, 19))
    assert dates["last_trade_prev_s"] == "20261016"


def test_matches_code_exact_and_suffix():
    assert mod._matches_code("600519", "600519") is True
    assert mod._matches_code("sh600519", "600519") is True
    assert mod._matches_code(" 600519 ", "600519") is True
    assert mod._matches_code("000001", "600519") is False
    assert mod._matches_code("", "600519") is False
    assert mod._matches_code(None, "600519") is False


def test_filter_records_without_code_column_returns_as_is():
    rows = [{"名称": "贵州茅台"}]
    assert mod._filter_records(rows, "600519") == rows
    assert mod._filter_records([], "600519") == []


def test_filter_records_matches_first_code_column():
    rows = [{"股票代码": "600519", "x": 1}, {"股票代码": "000001", "x": 2}]
    assert mod._filter_records(rows, "600519") == [{"股票代码": "600519", "x": 1}]
