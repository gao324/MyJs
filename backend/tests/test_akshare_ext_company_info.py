"""company_info 数据集契约: 八块键、公告按代码过滤、业绩预告回退。"""
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


def test_company_info_block_keys_and_notice_filter(monkeypatch):
    _install(monkeypatch, {
        "stock_individual_basic_info_xq": lambda symbol: _FakeFrame([{"item": "简称", "value": "贵州茅台"}]),
        "stock_zygc_em": lambda symbol: _FakeFrame([{"主营构成": "白酒"}]),
        "stock_news_em": lambda symbol: _FakeFrame([{"新闻标题": "新闻"}]),
        "stock_notice_report": lambda symbol, date: _FakeFrame(
            [{"代码": "600519", "公告标题": "公告"}, {"代码": "000001", "公告标题": "别家"}]),
        "stock_yjyg_em": lambda date: _FakeFrame([{"股票代码": "600519", "预测指标": "预增"}]),
        "stock_yjkb_em": lambda date: _FakeFrame([]),
        "stock_history_dividend_detail": lambda symbol, indicator: _FakeFrame([{"公告日期": "2026-06-01"}]),
    })
    blocks = AkshareExtProvider().get_company_info("600519.SH")
    assert set(blocks) == {"basic_info", "zygc", "news", "notice", "yjyg", "yjkb",
                           "dividend", "share_alloc"}
    assert [r["公告标题"] for r in blocks["notice"]] == ["公告"]
    assert blocks["basic_info"][0]["value"] == "贵州茅台"
    assert blocks["dividend"][0]["公告日期"] == "2026-06-01"


def test_company_info_missing_frames_yield_empty_blocks(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("接口异常")

    _install(monkeypatch, {
        "stock_individual_basic_info_xq": _boom,
        "stock_zygc_em": _boom,
        "stock_news_em": _boom,
        "stock_notice_report": _boom,
        "stock_yjyg_em": _boom,
        "stock_yjkb_em": _boom,
        "stock_history_dividend_detail": _boom,
    })
    blocks = AkshareExtProvider().get_company_info("600519.SH")
    assert set(blocks) == {"basic_info", "zygc", "news", "notice", "yjyg", "yjkb",
                           "dividend", "share_alloc"}
    assert all(rows == [] for rows in blocks.values())


def test_basic_info_uses_uppercase_prefixed_symbol(monkeypatch):
    """概况接口用大写前缀形式(SH600519, 与 stock_ai 同参)。"""
    seen: dict[str, str] = {}

    def _basic(symbol):
        seen["symbol"] = symbol
        return _FakeFrame([])

    _install(monkeypatch, {
        "stock_individual_basic_info_xq": _basic,
        "stock_zygc_em": lambda symbol: _FakeFrame([]),
        "stock_news_em": lambda symbol: _FakeFrame([]),
        "stock_notice_report": lambda symbol, date: _FakeFrame([]),
        "stock_yjyg_em": lambda date: _FakeFrame([]),
        "stock_yjkb_em": lambda date: _FakeFrame([]),
        "stock_history_dividend_detail": lambda symbol, indicator: _FakeFrame([]),
    })
    AkshareExtProvider().get_company_info("600519.SH")
    assert seen == {"symbol": "SH600519"}


def test_yj_blocks_concatenate_periods(monkeypatch):
    """业绩预告/快报拼接多个报告期(与 stock_ai 多 submit 合并一致)。"""
    _install(monkeypatch, {
        "stock_individual_basic_info_xq": lambda symbol: _FakeFrame([]),
        "stock_zygc_em": lambda symbol: _FakeFrame([]),
        "stock_news_em": lambda symbol: _FakeFrame([]),
        "stock_notice_report": lambda symbol, date: _FakeFrame([]),
        "stock_yjyg_em": lambda date: _FakeFrame([{"股票代码": "600519", "预测": date}]),
        "stock_yjkb_em": lambda date: _FakeFrame([]),
        "stock_history_dividend_detail": lambda symbol, indicator: _FakeFrame([]),
    })
    blocks = AkshareExtProvider().get_company_info("600519.SH")
    assert len(blocks["yjyg"]) == 4
    assert len({r["预测"] for r in blocks["yjyg"]}) == 4
