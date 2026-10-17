"""模板渲染：数据槽 + 叙述槽 → 最终 HTML。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.reporting.render_report_loops import build_loop_slots
from app.reporting.script_builder import build_script

TEMPLATE = Path(__file__).with_name("templates") / "template_report.html"
FULL_TEMPLATE = Path(__file__).with_name("templates") / "template_report_full.html"
SLOT_PATTERN = re.compile(r"\{\{([A-Z0-9_]+)\}\}")
SLOT_RE = SLOT_PATTERN  # 简报代码块中的旧名，同一正则


def find_unresolved(html: str) -> list[str]:
    return sorted(set(SLOT_RE.findall(html)))


def esc(v: Any) -> str:
    """HTML 文本转义；None → '数据缺失'。"""
    if v is None or v == "":
        return "数据缺失"
    s = str(v)
    return (s.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;")
            .replace("'", "&#x27;"))


def _val(block: dict, *path, default=None):
    cur: Any = block
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def load_template(path: Path | None = None) -> str:
    with open(path or TEMPLATE, encoding="utf-8") as f:
        return f.read()


def load_full_template() -> str:
    with open(FULL_TEMPLATE, encoding="utf-8") as f:
        return f.read()


def build_static_slots(content: dict, blocks: dict) -> dict[str, str]:
    meta = content.get("meta", {})
    hero = content.get("hero", {})
    name = str(meta.get("name") or "数据缺失")
    change = str(hero.get("change_pct") or "数据缺失")
    from app.market_time import cn_today
    from app.services.report_shards import dividend_yield

    dy = dividend_yield(blocks if isinstance(blocks, dict) else {}, today=cn_today())
    slots = {
        "PAGE_TITLE": f"{esc(name)} {esc(meta.get('code'))} · 个股分析报告",
        "STOCK_NAME": esc(name),
        "STOCK_CODE": esc(meta.get("code")),
        "LOGO_CHAR": esc(name[:1]),
        "ANALYSIS_DATE": esc(meta.get("analysis_date") or blocks.get("_analysis_date")),
        "HERO_PRICE": esc(hero.get("price")),
        "HERO_CHANGE": esc(change),
        "HERO_CHANGE_STYLE": _hero_change_style(hero.get("change_dir"), hero.get("change_pct")),
        "HERO_META_1": esc(meta.get("analysis_date")),
        "HERO_META_2": _quality_meta(hero.get("quality_score")),
        "HERO_META_3": esc(hero.get("target_price")),
        "HERO_META_4": esc(hero.get("rr")),
        "DIV_YIELD": (f"{esc(dy['pct'])}%" if dy.get("pct") is not None else "数据缺失"),
        "DIV_TOTAL_CASH": (f"{esc(dy['total_cash'])}元"
                           if dy.get("total_cash") is not None else "数据缺失"),
        "HERO_META_5": esc(hero.get("risk_level")),
        "HERO_TAGS": _tags(hero.get("tags") or []),
        "BIG_VERDICT": esc(_val(content, "conclusion", "big_verdict")),
        "VERDICT_DETAIL": _rich(_val(content, "conclusion", "verdict_detail")),
        "VERDICT_TAGS": _tags(_val(content, "conclusion", "tags") or []),
        "TRADE_BUY_RANGE": esc(_val(content, "conclusion", "trade_params", "buy_range")),
        "TRADE_STOP": esc(_val(content, "conclusion", "trade_params", "stop_loss")),
        "TRADE_TARGET": esc(_val(content, "conclusion", "trade_params", "target")),
        "TRADE_POSITION": esc(_val(content, "conclusion", "trade_params", "position")),
        "TRADE_RR": esc(_val(content, "conclusion", "trade_params", "rr")),
        "PROFILE_PARAGRAPH": esc(_val(content, "profile", "paragraph")),
        "PROFILE_TAGS": _tags(_val(content, "profile", "tags") or []),
        "PROFILE_FOOTNOTE": esc(_val(content, "profile", "footnote")),
        "DATA_CUTOFF": esc(meta.get("analysis_date")),
        "RESEARCH_STATUS": "已完成深度研究（模板流水线 v4）",
        "CSS": _load_css(),
        "REPORT_SCRIPT": build_script(blocks),
    }
    return slots


def _hero_change_style(change_dir: Any, change_pct: Any = None) -> str:
    """涨跌配色归一化：A股红涨绿跌。优先取 change_dir（up/涨/红/1/increase → 红，
    down/跌/绿/0/decrease → 绿，strip() + lower() 归一）。
    兜底（控制方裁决）：change_dir 缺失/空白/无法识别时按 change_pct 符号判色——
    strip() 后以 '-' 开头 → 绿（下跌），其余 → 红（上涨）；change_pct 也缺失/空白时
    无下跌证据，默认红涨。Task 5 的 LLM 可能漏发 change_dir，若一律判绿则涨跌色反转。"""
    value = "" if change_dir is None else str(change_dir).strip().lower()
    if value in ("up", "涨", "红", "1", "increase"):
        return "color:var(--red-up);"
    if value in ("down", "跌", "绿", "0", "decrease"):
        return "color:var(--green-down);"
    pct = "" if change_pct is None else str(change_pct).strip()
    if pct.startswith("-"):
        return "color:var(--green-down);"
    return "color:var(--red-up);"


def _quality_meta(score: Any) -> str:
    """公司质地槽：有评分 → 'N分'；缺失/空白 → '数据缺失'（避免拼出'数据缺失分'）。"""
    if score is None or str(score).strip() == "":
        return "数据缺失"
    return f"{esc(score)}分"


def _tags(items: list) -> str:
    if not items:
        return '<span class="tag">数据缺失</span>'
    return "".join(f'<span class="tag">{esc(t)}</span>' for t in items)


def _rich(text: Any) -> str:
    """叙述文本：允许 **加粗** 语法 → <strong>，其余转义。"""
    if not text:
        return "数据缺失"
    out = esc(text)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)


def _load_css() -> str:
    path = Path(__file__).with_name("templates") / "template_base.css"
    with open(path, encoding="utf-8") as f:
        css = f.read()
    return "\n".join(line for line in css.split("\n") if not line.strip().startswith("/*"))


def render(content: dict, blocks: dict) -> str:
    """六项简报（默认视图）。"""
    return _render(TEMPLATE, content, blocks, compact=True)


def render_full(content: dict, blocks: dict) -> str:
    """完整版深度研报（保留原有全部章节）。"""
    return _render(FULL_TEMPLATE, content, blocks, compact=False)


def _render(template: Path, content: dict, blocks: dict, *, compact: bool) -> str:
    html = load_template(template)
    slots = build_static_slots(content, blocks)
    slots.update(build_loop_slots(content, blocks, compact=compact))
    for k, v in slots.items():
        html = html.replace("{{" + k + "}}", v)
    return html
