"""渲染与终验: 600519 真实缓存 → 六项简报(16 条) 与完整版(17 条) 均须全绿。"""
from __future__ import annotations

import json
from pathlib import Path

from app.reporting import render_report, script_builder, validate_html, validate_html_full
from app.reporting.render_report_loops import tech_from_blocks

FIXTURES = Path(__file__).parent / "fixtures" / "stock_report"


def _payload():
    data = json.loads((FIXTURES / "data_600519.json").read_text(encoding="utf-8"))
    content = json.loads((FIXTURES / "content_600519.json").read_text(encoding="utf-8"))
    return data["blocks"], content


def test_render_leaves_no_unresolved_slots():
    blocks, content = _payload()
    html = render_report.render(content, blocks)
    assert render_report.find_unresolved(html) == []


def test_report_shows_only_the_six_requested_topics():
    blocks, content = _payload()
    html = render_report.render(content, blocks)
    for heading in ("整体评分与分红", "公司基本面与近三年盈利", "产业链、热点轮动与护城河",
                    "业务弹性测算", "技术面与筹码", "近期舆情"):
        assert heading in html
    for removed in ("宏观与周期定位", "估值与买卖时机", "跟踪计划与综合结论", "对标分析"):
        assert removed not in html
    assert "近12月股息率" in html and "年度财务概况" in html
    assert "主要股东" in html and "近半年涨幅" in html
    assert "热点关联无法核实" in html  # 没有轮动数据时不让模型猜热点。


def test_compact_report_has_full_version_entry(tmp_path):
    blocks, content = _payload()
    html = render_report.render(content, blocks)
    assert 'data-full-report="pending"' in html
    full = render_report.render_full(content, blocks)
    assert 'data-brief-report="pending"' in full


def test_full_report_renders_all_original_sections_and_passes_checks(tmp_path):
    blocks, content = _payload()
    html = render_report.render_full(content, blocks)
    assert render_report.find_unresolved(html) == []
    for heading in ("宏观与周期定位", "产业链深度拆解", "业绩弹性测算",
                    "估值与买卖时机", "对标分析", "跟踪计划与综合结论",
                    "技术面与资金面分析"):
        assert heading in html
    out = tmp_path / "full-report.html"
    out.write_text(html, encoding="utf-8")
    assert validate_html_full.check_html(str(out)) == []


def test_rendered_report_passes_all_checks(tmp_path):
    blocks, content = _payload()
    html = render_report.render(content, blocks)
    out = tmp_path / "report.html"
    out.write_text(html, encoding="utf-8")
    assert validate_html.check_html(str(out)) == []


def test_missing_news_and_dividends_render_as_explicitly_unavailable(tmp_path):
    blocks, content = _payload()
    blocks["dividend"] = []
    content["news"]["items"] = []
    html = render_report.render(content, blocks)
    out = tmp_path / "report-with-missing-data.html"
    out.write_text(html, encoding="utf-8")
    assert "最近分红记录：数据缺失或未检出" in html
    assert "近期未获取到可核实的舆情素材" in html
    assert validate_html.check_html(str(out)) == []


def test_script_builder_injects_no_nan():
    blocks, _ = _payload()
    script = script_builder.build_script(blocks)
    assert "NaN" not in script and "Infinity" not in script


def test_technical_summary_uses_half_year_range_volume_and_holder_trend():
    rows = []
    for i in range(121):
        close = 100 + i
        rows.append({"date": f"2026-01-{(i % 28) + 1:02d}", "close": close,
                     "low": close - 1, "high": close + 1,
                     "volume": 200 if i == 120 else 100})
    tech = tech_from_blocks({
        "kline_daily": rows,
        "technical_indicators": [{"MACD": 0.1}],
        "gdhs": [
            {"股东户数统计截止日": "2026-09-30", "股东户数-本次": 1100},
            {"股东户数统计截止日": "2026-06-30", "股东户数-本次": 1000},
        ],
    })
    assert tech["half_year_window"] == 120
    assert tech["half_year_change"] == 120.0
    assert tech["price_range_20"] == (200.0, 221.0)
    assert tech["volume_ratio"] == 2.0
    assert tech["holder_trend"].endswith("+10.00%")

    rows[-1]["volume"] = None
    stale = tech_from_blocks({"kline_daily": rows})
    assert stale["volume_ratio"] is None
    assert stale["latest_volume"] is None
