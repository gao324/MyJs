"""七分片管线契约: digest 缺块标注、JSON 抽取、重试收敛、降级与填充率。"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.reporting import kline as kline_mod
from app.services import report_shards as rs


def _blocks(**over):
    base = {name: [] for name in (
        "basic_info", "spot", "share_structure", "zygc", "kline_daily", "fund_flow",
        "lhb", "fin_abstract", "fin_indicator_ths", "balance_sheet",
        "income_statement", "cashflow", "yjyg", "yjkb", "top10", "gdhs",
        "share_hold_change", "dividend", "share_alloc", "release", "notice", "news",
        "research", "recommend", "fund_hold", "margin")}
    base.update({
        "spot": [{"名称": "贵州茅台", "最新价": 1500.0}],
        "kline_daily": [{"date": f"2026-01-{(i % 28) + 1:02d}", "open": 1.0, "high": 1.2,
                         "low": 0.9, "close": 1.1, "volume": 10.0} for i in range(60)],
        "technical_indicators": [{"MACD": 0.1}],
        "technical_signals": {"macd": "金叉"},
        "_analysis_date": "2026-10-15",
    })
    base.update(over)
    return base


def _identity_payload():
    return {
        "meta": {"name": "贵州茅台", "code": "600519", "analysis_date": "2026-10-15"},
        "profile": {"paragraph": "公司简介", "tags": ["白酒"],
                    "info": [{"k": "简称", "v": "贵州茅台"}], "footnote": "来源: 数据"},
    }


def test_digest_marks_missing_blocks():
    digest = rs.build_digest(_blocks())
    assert "【公司简称】" in digest
    assert rs.MISSING in digest


def test_digest_includes_rotation_context_and_chip_inputs():
    blocks = _blocks(
        gdhs=[{"股东户数统计截止日": "2026-09-30", "股东户数-本次": 100}],
        top10_free=[{"股东名称": "流通股东A"}],
        basic_info=[{"item": "所属行业", "value": "电子设备"}],
        _industry_rotation={"available": True, "industry": [{"name": "电子"}],
                            "concept": [{"name": "算力"}]},
    )
    digest = rs.build_digest(blocks)
    assert "近期行业/概念轮动" in digest and "算力" in digest
    assert "所属行业: 电子设备" in digest
    assert "十大流通股东" in digest and "流通股东A" in digest
    assert "股东户数近两期" in digest


def test_full_report_contract_keeps_all_array_minimums():
    from app.reporting.content_schema import MIN_LEN

    assert MIN_LEN[("risk", "matrix")] == 3
    assert MIN_LEN[("news", "items")] == 5
    assert "risk.matrix 至少 3 条" in rs.shard_system("valuation_risk")
    assert "每个叙述字段最多2句" in rs.JSON_RULES
    assert "行业轮动数据" in rs.shard_system("macro_chain")


def test_extract_json_strips_fence():
    assert rs.extract_json("```json\n{\"a\": 1}\n```") == {"a": 1}


def test_extract_json_rejects_non_object():
    with pytest.raises(ValueError):
        rs.extract_json("[1, 2]")


def test_shard_prompts_cover_schema_keys():
    text = rs.shard_system("technical")
    assert "technical" in text
    assert "verdict" in text


def test_analyze_shards_retries_then_degrades(monkeypatch):
    calls: list[int] = []

    async def _fake_llm(system, user, max_tokens):
        calls.append(1)
        return "not json"

    monkeypatch.setattr(rs, "_call_llm", _fake_llm)
    content, failed = asyncio.run(rs.analyze_shards("600519", "贵州茅台", _blocks()))
    assert failed == list(rs.SHARD_ORDER)
    assert content["meta"]["code"] == "600519"
    assert content["meta"]["name"] == "贵州茅台"
    assert rs.fill_rate(content) <= 0.4
    assert len(calls) == 3 * len(rs.SHARD_ORDER)


def test_analyze_shards_merges_valid_output(monkeypatch):
    payload = json.dumps(_identity_payload(), ensure_ascii=False)

    async def _fake_llm(system, user, max_tokens):
        return payload

    monkeypatch.setattr(rs, "_call_llm", _fake_llm)
    content, failed = asyncio.run(rs.analyze_shards("600519", "贵州茅台", _blocks()))
    assert "identity" not in failed
    assert content["meta"]["code"] == "600519"
    assert content["profile"]["paragraph"] == "公司简介"


def test_kline_helpers_skip_malformed_rows():
    blocks = {
        "kline_daily": [
            {"date": "2026-01-01", "open": 1.0, "high": 1.2, "low": 0.9,
             "close": 1.1, "volume": 10.0},
            {"date": "", "open": None, "high": 1.0, "low": 1.0,
             "close": 1.0, "volume": 1.0},
            "not-a-row",
        ],
        "technical_indicators": [None, {"RSI": 55.0}],
    }
    assert kline_mod.kline_rows(blocks, limit=2) == [
        ["2026-01-01", 1.0, 1.2, 0.9, 1.1, 10.0]]
    assert kline_mod.latest_indicators(blocks) == {"RSI": 55.0}
