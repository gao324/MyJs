"""渲染与终验: 用 stock_ai 600519 真实缓存渲染, 17 条终验必须全绿。"""
from __future__ import annotations

import json
from pathlib import Path

from app.reporting import render_report, script_builder, validate_html

FIXTURES = Path(__file__).parent / "fixtures" / "stock_report"


def _payload():
    data = json.loads((FIXTURES / "data_600519.json").read_text(encoding="utf-8"))
    content = json.loads((FIXTURES / "content_600519.json").read_text(encoding="utf-8"))
    return data["blocks"], content


def test_render_leaves_no_unresolved_slots():
    blocks, content = _payload()
    html = render_report.render(content, blocks)
    assert render_report.find_unresolved(html) == []


def test_rendered_report_passes_all_checks(tmp_path):
    blocks, content = _payload()
    html = render_report.render(content, blocks)
    out = tmp_path / "report.html"
    out.write_text(html, encoding="utf-8")
    assert validate_html.check_html(str(out)) == []


def test_script_builder_injects_no_nan():
    blocks, _ = _payload()
    script = script_builder.build_script(blocks)
    assert "NaN" not in script and "Infinity" not in script
