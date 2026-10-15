"""research 数据集契约: 块键、列透传、日期窗口、空数据不抛错。"""
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
    """mapping: 函数名 -> 返回值(或抛异常的 lambda)。"""
    mod = types.ModuleType("akshare")
    for name, value in mapping.items():
        setattr(mod, name, value)
    monkeypatch.setitem(sys.modules, "akshare", mod)
    return mod


def test_research_returns_three_blocks(monkeypatch):
    _install(monkeypatch, {
        "stock_research_report_em": lambda symbol: _FakeFrame(
            [{"报告名称": "深度报告", "东财评级": "买入", "机构": "某证券", "日期": "2026-10-01"}]),
        "stock_institute_recommend": lambda symbol: _FakeFrame(
            [{"股票简称": "贵州茅台", "综合评级": "买入", "评级机构数": 12}]),
        "stock_report_fund_hold_detail": lambda symbol, date: _FakeFrame(
            [{"基金名称": "某基金", "持股数": 100}]),
    })
    blocks = AkshareExtProvider().get_research("600519.SH")
    assert set(blocks) == {"research", "recommend", "fund_hold"}
    assert blocks["research"][0]["报告名称"] == "深度报告"


def test_research_missing_frames_yield_empty_lists(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("接口异常")

    _install(monkeypatch, {
        "stock_research_report_em": _boom,
        "stock_institute_recommend": _boom,
        "stock_report_fund_hold_detail": _boom,
    })
    blocks = AkshareExtProvider().get_research("600519.SH")
    assert blocks == {"research": [], "recommend": [], "fund_hold": []}


def test_non_finite_values_become_none(monkeypatch):
    _install(monkeypatch, {
        "stock_research_report_em": lambda symbol: _FakeFrame([{"目标价": float("nan")}]),
        "stock_institute_recommend": lambda symbol: _FakeFrame([]),
        "stock_report_fund_hold_detail": lambda symbol, date: _FakeFrame([]),
    })
    blocks = AkshareExtProvider().get_research("600519.SH")
    assert blocks["research"][0]["目标价"] is None
