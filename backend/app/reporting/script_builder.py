"""模板脚本注入 — K 线/饼图/markLine/markPoint 数据注入（由 stock_ai analyze_api 移植）。

K 线清洗工具复用 app.reporting.kline（digest 与脚本注入共用，不重复实现）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.reporting.kline import _num, kline_rows, latest_indicators

JS_TEMPLATE = Path(__file__).with_name("templates") / "template_base.js"

# json 只在非有限值时才产出这三个裸字面量；带引号的同名文本（如主营构成叫 "Infinity"）不算
_NONFINITE_RE = re.compile(r'(?<![\w"])-?(?:NaN|Infinity)(?![\w"])')


def _json_text(obj: Any, fallback: str = "[]") -> str:
    """JSON 文本化（注入 JS 用）：allow_nan=False；失败或残留非有限字面量 → fallback。"""
    try:
        text = json.dumps(obj, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return fallback
    return fallback if _NONFINITE_RE.search(text) else text


def pie_from_zygc(zygc: Any) -> list[dict]:
    """主营构成 → 饼图数据（去重/过滤"分类/抵减"行，最多 7 项，收入非有限则跳过）。"""
    if not isinstance(zygc, list):
        return []
    seen, pie = set(), []
    for r in zygc:
        if not isinstance(r, dict):
            continue
        nm = str(r.get("主营构成") or "").strip()
        if not nm or nm in seen or "分类" in nm or "抵减" in nm:
            continue
        val = _num(r.get("主营收入"))
        if val is None:
            continue
        seen.add(nm)
        pie.append({"value": round(val / 1e8, 1), "name": nm})
        if len(pie) >= 7:
            break
    return pie


def _markline(ti: dict, closes: list[float]) -> list[dict]:
    """指标线：指标缺失/非有限的项不注入（不伪造 0，也不写 null）；止损位取区间最低收盘价。"""
    out = []
    for field, label in (("BOLL_UPPER", "BOLL上轨"), ("MA60", "MA60"), ("MA20", "加仓位")):
        v = _num(ti.get(field))
        if v is not None:
            out.append({"yAxis": v, "name": label})
    if closes:
        out.append({"yAxis": round(min(closes), 2), "name": "止损位"})
    return out


def _markpoint(rows: list[list]) -> list[dict]:
    """当前/前高标注：索引只按已清洗行数计算（空 K 线返回 []，绝不出现 raw.length-1 = -1）。"""
    if not rows:
        return []
    closes = [r[4] for r in rows]
    mx = max(closes)
    return [
        {"coord": [len(rows) - 1, rows[-1][4]], "value": "当前",
         "itemStyle": {"color": "#d4a853"}},
        {"coord": [closes.index(mx), mx], "value": "前高",
         "itemStyle": {"color": "#f55656"}},
    ]


def build_script(blocks: Any) -> str:
    """模板 JS + K线/饼图/markLine/markPoint 数据注入，返回 `<script>…</script>` 片段。

    加固口径：
    - 行读取一律 `.get(...)` + `_num`（float + isfinite）；畸形行跳过而不是抛错；
    - 空/全无效 K 线：rawData = []、markPoint = []、markLine 不放非法坐标；
    - 注入文本 `allow_nan=False`，残留非有限字面量时退化为 `[]` —— 脚本块内无 NaN/Infinity。
    """
    d = blocks if isinstance(blocks, dict) else {}
    with open(JS_TEMPLATE, encoding="utf-8") as f:
        js = f.read()
    rows = kline_rows(d)
    closes = [r[4] for r in rows]
    ti = latest_indicators(d)
    js = js.replace("__RAW_DATA_ARRAY__", _json_text(rows))
    js = js.replace("__PIE_DATA_ARRAY__", _json_text(pie_from_zygc(d.get("zygc"))))
    js = js.replace("__MARKLINE_DATA__", _json_text(_markline(ti, closes)))
    js = js.replace("__MARKPOINT_DATA__", _json_text(_markpoint(rows)))
    return "<script>\n" + js + "\n</script>"
