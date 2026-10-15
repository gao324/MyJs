"""块收集: 26 块齐全、缓存命中、缺块记入 missing、provider 不可用 fail-closed。"""
from __future__ import annotations

import pytest

from app.services import stock_report_data as srd


def _kline() -> list[dict]:
    return [{"date": f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", "open": 10.0,
             "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100.0}
            for i in range(120)]


class _FakeProvider:
    """返回全部 26 块(各一块非空记录); 与真实 provider 的键集合一致。"""

    name = "akshare_ext"

    def get_research(self, symbol):
        return {"research": [{"报告名称": "r"}], "recommend": [{"综合评级": "买入"}],
                "fund_hold": [{"基金名称": "f"}]}

    def get_shareholders(self, symbol):
        return {"top10": [{"股东名称": "A"}], "top10_free": [{"股东名称": "B"}],
                "gdhs": [{"股东户数-本次": 1}],
                "share_structure": [{"变动日期": "2026-01-01"}],
                "release": [{"解禁数量": 1}], "share_hold_change": [{"姓名": "张三"}]}

    def get_fund_flow(self, symbol):
        return {"fund_flow": [{"主力净流入-净额": 1}], "margin": [{"融资余额": 5}],
                "lhb": [{"代码": "600519"}]}

    def get_company_info(self, symbol):
        return {"basic_info": [{"item": "简称"}], "zygc": [{"主营构成": "白酒"}],
                "news": [{"新闻标题": "n"}], "notice": [{"公告标题": "g"}],
                "yjyg": [{"预测指标": "预增"}], "yjkb": [{"指标": "1"}],
                "dividend": [{"公告日期": "2026-06-01"}],
                "share_alloc": [{"公告日期": "2026-06-01"}]}

    def get_report_financials(self, symbol):
        return {"fin_abstract": [{"选项": "常用指标"}],
                "fin_indicator_ths": [{"报告期": "2026-06-30"}],
                "balance_sheet": [{"报表日期": "2026-06-30"}],
                "income_statement": [{"报表日期": "2026-06-30"}],
                "cashflow": [{"报表日期": "2026-06-30"}]}

    def get_report_spot(self, symbol):
        return [{"名称": "贵州茅台"}]

    def get_report_kline(self, symbol, years=3):
        return _kline()


@pytest.fixture()
def patched(monkeypatch, tmp_path):
    monkeypatch.setattr(srd, "_provider", lambda: _FakeProvider())
    monkeypatch.setattr(srd.settings, "data_dir", tmp_path, raising=False)
    return tmp_path


def test_collect_covers_all_blocks_and_writes_cache(patched):
    blocks, missing = srd.collect_blocks("600519.SH")
    assert set(blocks) == set(srd.ALL_BLOCK_NAMES) | {"_analysis_date"}
    assert missing == []
    assert blocks["research"][0]["报告名称"] == "r"
    assert blocks["technical_indicators"] and blocks["technical_signals"]
    assert srd.cache_path("600519.SH").exists()


def test_second_call_uses_cache_without_provider(patched, monkeypatch):
    srd.collect_blocks("600519.SH")
    monkeypatch.setattr(
        srd, "_provider",
        lambda: (_ for _ in ()).throw(AssertionError("缓存命中时不应再取数")))
    blocks, missing = srd.collect_blocks("600519.SH")
    assert blocks["research"] and missing == []


def test_empty_block_lands_in_missing(patched, monkeypatch):
    provider = _FakeProvider()
    monkeypatch.setattr(provider, "get_company_info", lambda symbol: {
        "basic_info": [], "zygc": [], "news": [], "notice": [],
        "yjyg": [], "yjkb": [], "dividend": [], "share_alloc": [],
    })
    monkeypatch.setattr(srd, "_provider", lambda: provider)
    _blocks, missing = srd.collect_blocks("600519.SH")
    assert "news" in missing and "basic_info" in missing
    assert "research" not in missing


def test_missing_kline_raises(patched, monkeypatch):
    provider = _FakeProvider()
    monkeypatch.setattr(provider, "get_report_kline", lambda symbol, years=3: [])
    monkeypatch.setattr(srd, "_provider", lambda: provider)
    with pytest.raises(srd.ReportDataUnavailableError):
        srd.collect_blocks("600519.SH")


def test_unavailable_provider_raises(patched, monkeypatch):
    monkeypatch.setattr(srd, "_provider", lambda: None)
    with pytest.raises(srd.ReportDataUnavailableError):
        srd.collect_blocks("600519.SH")


def test_invalid_symbol_raises(patched):
    with pytest.raises(ValueError):
        srd.collect_blocks("600519")
