"""fund_flow 数据集契约: 三块键、龙虎榜按代码过滤、market 参数传递。"""
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


def test_fund_flow_blocks_and_market(monkeypatch):
    seen: dict[str, str] = {}

    def _flow(stock, market):
        seen["stock"] = stock
        seen["market"] = market
        return _FakeFrame([{"主力净流入-净额": 1000}])

    _install(monkeypatch, {
        "stock_individual_fund_flow": _flow,
        "stock_margin_detail_sse": lambda date: _FakeFrame([{"融资余额": 5}]),
        "stock_lhb_detail_em": lambda start_date, end_date: _FakeFrame(
            [{"代码": "600519", "上榜日": "2026-10-01"}, {"代码": "000001", "上榜日": "2026-10-01"}]),
    })
    blocks = AkshareExtProvider().get_fund_flow("600519.SH")
    assert set(blocks) == {"fund_flow", "margin", "lhb"}
    assert seen == {"stock": "600519", "market": "sh"}
    assert blocks["fund_flow"][0]["主力净流入-净额"] == 1000
    assert [r["代码"] for r in blocks["lhb"]] == ["600519"]


def test_sz_symbol_uses_szse_margin(monkeypatch):
    calls: list[str] = []

    def _sse(date):
        calls.append("sse")
        return _FakeFrame([{"融资余额": 1}])

    def _szse(date):
        calls.append("szse")
        return _FakeFrame([{"融资余额": 2}])

    _install(monkeypatch, {
        "stock_individual_fund_flow": lambda stock, market: _FakeFrame([]),
        "stock_margin_detail_sse": _sse,
        "stock_margin_detail_szse": _szse,
        "stock_lhb_detail_em": lambda start_date, end_date: _FakeFrame([]),
    })
    blocks = AkshareExtProvider().get_fund_flow("000001.SZ")
    assert calls == ["szse"]
    assert blocks["margin"][0]["融资余额"] == 2
