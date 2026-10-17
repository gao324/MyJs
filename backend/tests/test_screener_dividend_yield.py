"""策略结果行股息率 (dividend_yield / dividend_yield_ttm) — GM 截面透传契约测试。

口径: 数据来自 GM 估值截面 dy_lfy(股息率, 上一财年) 与 dy_ttm(股息率, 滚动12月),
由 app/services/dividend_yield_sync.py 同步为本地快照 (%, /100 转小数); 本层
只按 symbol 透传, 不再本地重算 (GM 已含自己的价格/分红口径)。

- 两个运行时列都是展示口径, 与策略结果 as_of 无关 (GM 最新交易日截面);
- 无该标的 / 快照缺失 / 取数异常 → None (fail-closed, 前端「—」, 不伪装成 0);
- 投影返回新行, 不改调用方传入的行字典。
"""
from __future__ import annotations

from app.api import screener


def _patch_maps(monkeypatch, lfly=None, ttm=None):
    monkeypatch.setattr(
        screener, "_gm_dividend_yield_maps",
        lambda: {"lfly": lfly or {}, "ttm": ttm or {}},
    )


def test_dividend_yield_projects_both_gm_columns(monkeypatch):
    """dy_lfy → dividend_yield; dy_ttm → dividend_yield_ttm (小数制透传)。"""
    _patch_maps(monkeypatch,
                lfly={"601021.SH": 0.024107, "002603.SZ": 0.053121},
                ttm={"601021.SH": 0.032314, "002603.SZ": 0.053121})

    index = screener._DividendYieldIndex()
    out = index.rows([
        {"symbol": "601021.SH", "raw_close": 39.07},
        {"symbol": "002603.SZ"},
    ])

    assert out[0]["dividend_yield"] == 0.024107
    assert out[0]["dividend_yield_ttm"] == 0.032314
    assert out[1]["dividend_yield"] == 0.053121
    assert out[1]["dividend_yield_ttm"] == 0.053121


def test_dividend_yield_missing_symbol_is_none(monkeypatch):
    """快照无该标的 → 两列 None; 快照为空 → 两列 None。"""
    _patch_maps(monkeypatch, lfly={"000001.SZ": 0.05})
    index = screener._DividendYieldIndex()

    out = index.rows([{"symbol": "000002.SZ"}, {"symbol": "000001.SZ"}])
    assert out[0]["dividend_yield"] is None
    assert out[0]["dividend_yield_ttm"] is None
    assert out[1]["dividend_yield"] == 0.05

    _patch_maps(monkeypatch)
    out = screener._DividendYieldIndex().rows([{"symbol": "000001.SZ"}])
    assert out[0]["dividend_yield"] is None
    assert out[0]["dividend_yield_ttm"] is None


def test_dividend_yield_does_not_mutate_input(monkeypatch):
    """投影返回新行, 不改调用方传入的行字典 (缓存 payload 复用同一对象)。"""
    _patch_maps(monkeypatch, lfly={"000001.SZ": 0.05})
    rows = [{"symbol": "000001.SZ", "raw_close": 10.0}]
    screener._DividendYieldIndex().rows(rows)
    assert "dividend_yield" not in rows[0]
    assert "dividend_yield_ttm" not in rows[0]


def test_dividend_yield_reads_maps_once_per_index(monkeypatch):
    """同一请求内多策略复用同一份快照: 每个 index 只取一次数。"""
    calls = []

    def _maps():
        calls.append(1)
        return {"lfly": {}, "ttm": {}}

    monkeypatch.setattr(screener, "_gm_dividend_yield_maps", _maps)
    index = screener._DividendYieldIndex()
    index.rows([{"symbol": "A"}, {"symbol": "B"}])
    index.symbol_rows({"C": {"symbol": "C"}})
    assert len(calls) == 1
