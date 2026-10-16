"""一致性验收: stock_ai 同一份块 → 同一 digest 结构 + 渲染通过 16 条终验。

离线部分(本文件)用 stock_ai 600519 真实缓存做验收基线; 真模型端到端
(采集→七分片→渲染)见 scripts/compare_stock_report_blocks.py, 需用户确认后执行。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.reporting import render_report, validate_html, validate_html_full
from app.services import report_shards
from scripts.compare_stock_report_blocks import compare_blocks

FIXTURES = Path(__file__).parent / "fixtures" / "stock_report"


def _payload():
    data = json.loads((FIXTURES / "data_600519.json").read_text(encoding="utf-8"))
    content = json.loads((FIXTURES / "content_600519.json").read_text(encoding="utf-8"))
    return data["blocks"], content


def test_compare_blocks_reports_row_count_diff():
    left = {"news": [{"a": 1}, {"a": 2}], "spot": [{"x": 1}]}
    right = {"news": [{"a": 1}], "spot": [{"x": 1}]}
    diffs = compare_blocks(left, right)
    assert diffs == ["news: 行数 2 vs 1"]


def test_compare_blocks_ignores_kline_minute():
    left = {"kline_minute": [{"a": 1}], "spot": []}
    right = {"spot": []}
    assert compare_blocks(left, right) == []


def test_cli_output_is_ascii_safe(tmp_path, capsys):
    """CLI 输出必须 GBK 可编码(Windows GBK 控制台打不出 emoji 等生僻字符)。

    中文在 GBK 下合法, 故不断言纯 ASCII, 而断言 GBK 严格编码不抛错。
    """
    from scripts.compare_stock_report_blocks import main

    left = tmp_path / "a.json"
    right = tmp_path / "b.json"
    left.write_text('{"blocks": {"spot": [{"x": 1}]}}', encoding="utf-8")
    right.write_text('{"blocks": {"spot": [{"x": 2}]}}', encoding="utf-8")
    assert main(["--stock-ai", str(left), "--ours", str(right)]) == 1
    out = capsys.readouterr().out
    out.encode("gbk")


def test_digest_render_validate_from_stock_ai_blocks(tmp_path):
    """验收口径: 同一 stock_ai 缓存 → digest 结构完整 + 渲染 + 16 条全绿。"""
    blocks, content = _payload()
    digest = report_shards.build_digest(blocks)
    assert "【公司简称】" in digest and "【券商研报（前10）】" in digest
    html = render_report.render(content, blocks)
    assert render_report.find_unresolved(html) == []
    out = tmp_path / "parity.html"
    out.write_text(html, encoding="utf-8")
    assert validate_html.check_html(str(out)) == []

    full = render_report.render_full(content, blocks)
    assert render_report.find_unresolved(full) == []
    full_out = tmp_path / "parity-full.html"
    full_out.write_text(full, encoding="utf-8")
    assert validate_html_full.check_html(str(full_out)) == []
