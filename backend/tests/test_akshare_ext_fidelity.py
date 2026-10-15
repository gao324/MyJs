"""同源保真方法契约: 接口与 stock_ai Phase 1 一致, spot 按代码过滤。"""
from __future__ import annotations

import sys
import types

from app.plugins.akshare_ext.provider import AkshareExtProvider


class _FakeFrame:
    def __init__(self, rows):
        self._rows = rows

    def to_dict(self, orient="records"):
        return list(self._rows)


def _install(monkeypatch, mapping):
    mod = types.ModuleType("akshare")
    for name, value in mapping.items():
        setattr(mod, name, value)
    monkeypatch.setitem(sys.modules, "akshare", mod)


def test_report_kline_uses_qfq_adjust(monkeypatch):
    seen: dict[str, str] = {}

    def _daily(symbol, adjust=""):
        seen["symbol"] = symbol
        seen["adjust"] = adjust
        return _FakeFrame([{"date": "2026-10-14", "open": 1.0, "close": 1.1}])

    _install(monkeypatch, {"stock_zh_a_daily": _daily})
    rows = AkshareExtProvider().get_report_kline("600519.SH")
    assert seen == {"symbol": "sh600519", "adjust": "qfq"}
    assert rows[0]["close"] == 1.1


def test_report_financials_blocks(monkeypatch):
    _install(monkeypatch, {
        "stock_financial_abstract": lambda symbol: _FakeFrame([{"选项": "常用指标"}]),
        "stock_financial_abstract_ths": lambda symbol, indicator: _FakeFrame([{"报告期": "2026-06-30"}]),
        "stock_financial_report_sina": lambda stock, symbol: _FakeFrame([{"报表日期": "2026-06-30"}]),
    })
    blocks = AkshareExtProvider().get_report_financials("600519.SH")
    assert set(blocks) == {"fin_abstract", "fin_indicator_ths", "balance_sheet",
                           "income_statement", "cashflow"}
    assert blocks["balance_sheet"]
    assert blocks["cashflow"]


def test_report_spot_filters_single_symbol(monkeypatch):
    _install(monkeypatch, {
        "stock_zh_a_spot": lambda: _FakeFrame(
            [{"代码": "sh600519", "名称": "贵州茅台", "最新价": 1500.0},
             {"代码": "sz000001", "名称": "平安银行", "最新价": 11.0}]),
    })
    rows = AkshareExtProvider().get_report_spot("600519.SH")
    assert [r["名称"] for r in rows] == ["贵州茅台"]


def test_report_spot_missing_symbol_yields_empty(monkeypatch):
    _install(monkeypatch, {
        "stock_zh_a_spot": lambda: _FakeFrame([{"代码": "sz000001", "名称": "平安银行"}]),
    })
    assert AkshareExtProvider().get_report_spot("600519.SH") == []
