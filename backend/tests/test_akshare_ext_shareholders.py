"""shareholders 数据集契约: 五块键、top10 流通股东回退、交易所分流。"""
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


def test_shareholders_block_keys(monkeypatch):
    _install(monkeypatch, {
        "stock_gdfx_top_10_em": lambda symbol, date: _FakeFrame([{"股东名称": "A"}]),
        "stock_gdfx_free_top_10_em": lambda symbol, date: _FakeFrame([{"股东名称": "B"}]),
        "stock_zh_a_gdhs_detail_em": lambda symbol: _FakeFrame([{"股东户数-本次": 100}]),
        "stock_zh_a_gbjg_em": lambda symbol: _FakeFrame([{"变动日期": "2026-01-01"}]),
        "stock_restricted_release_queue_em": lambda symbol: _FakeFrame([{"解禁数量": 1}]),
        "stock_share_hold_change_sse": lambda symbol: _FakeFrame([{"姓名": "张三"}]),
    })
    blocks = AkshareExtProvider().get_shareholders("600519.SH")
    assert set(blocks) == {"top10", "gdhs", "share_structure", "release", "share_hold_change"}
    assert blocks["top10"][0]["股东名称"] == "A"


def test_top10_falls_back_to_free_float_when_main_empty(monkeypatch):
    _install(monkeypatch, {
        "stock_gdfx_top_10_em": lambda symbol, date: _FakeFrame([]),
        "stock_gdfx_free_top_10_em": lambda symbol, date: _FakeFrame([{"股东名称": "B"}]),
        "stock_zh_a_gdhs_detail_em": lambda symbol: _FakeFrame([]),
        "stock_zh_a_gbjg_em": lambda symbol: _FakeFrame([]),
        "stock_restricted_release_queue_em": lambda symbol: _FakeFrame([]),
        "stock_share_hold_change_sse": lambda symbol: _FakeFrame([]),
    })
    blocks = AkshareExtProvider().get_shareholders("600519.SH")
    assert blocks["top10"][0]["股东名称"] == "B"


def test_sz_symbol_uses_szse_endpoint(monkeypatch):
    calls: list[str] = []

    def _sse(symbol):
        calls.append("sse")
        return _FakeFrame([{"姓名": "沪"}])

    def _szse(symbol):
        calls.append("szse")
        return _FakeFrame([{"姓名": "深"}])

    _install(monkeypatch, {
        "stock_gdfx_top_10_em": lambda symbol, date: _FakeFrame([]),
        "stock_gdfx_free_top_10_em": lambda symbol, date: _FakeFrame([]),
        "stock_zh_a_gdhs_detail_em": lambda symbol: _FakeFrame([]),
        "stock_zh_a_gbjg_em": lambda symbol: _FakeFrame([]),
        "stock_restricted_release_queue_em": lambda symbol: _FakeFrame([]),
        "stock_share_hold_change_sse": _sse,
        "stock_share_hold_change_szse": _szse,
    })
    blocks = AkshareExtProvider().get_shareholders("000001.SZ")
    assert calls == ["szse"]
    assert blocks["share_hold_change"][0]["姓名"] == "深"
