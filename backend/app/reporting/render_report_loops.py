"""循环槽位 + 第 9 章确定性计算。

空数据必须降级、永不抛错：所有构建器只经 .get 读取，
缺块/缺键/空列表一律渲染“数据缺失”。

注意：本模块对 render_report 只做函数级延迟导入
（转义语义以 render_report.esc 为唯一来源，同时避免循环导入），
因此 render_report.py 顶部 ``from render_report_loops import build_loop_slots``
无论以何种顺序导入都是安全的。
"""
from __future__ import annotations

import math
from typing import Any


def _esc(v: Any) -> str:
    from app.reporting.render_report import esc
    if isinstance(v, float) and not math.isfinite(v):
        # 非有限数值（nan/±inf）一律按缺失降级，绝不把 "nan"/"inf" 写进任何槽位。
        return MISS
    if _is_nonfinite_text(v):
        # 字符串 "nan"/"inf"/"1e309" 等同属非有限数据，同样降级（绝不直通槽位）。
        return MISS
    return esc(v)


def _v(block: Any, *path, default=None):
    from app.reporting.render_report import _val
    return _val(block, *path, default=default)


def _num(v: Any):
    """数值归一化（边界唯一入口）：能转成有限 float 才返回 float，否则返回 None。

    None/布尔/非数值字符串/nan/inf 一律视为缺失（与空 K 线路径同一表示），
    绝不让原始 nan 或字符串参与下游算术。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


MISS = "数据缺失"

#: 47 个循环槽全名（与模板一一对应，禁止改名/新增/删除）。
LOOP_SLOT_NAMES = (
    "CHAIN_JUDGMENT", "CHAIN_NODES", "CHAIN_PROFIT", "CHAIN_TREND",
    "COMPARE_ENGINES", "COMPARE_ROWS",
    "ELASTIC_SCENARIOS", "ELASTIC_SENSITIVITY", "ELASTIC_TREE",
    "FIN_ROWS",
    "KLINE_KPI_1", "KLINE_KPI_2", "KLINE_KPI_3", "KLINE_KPI_4",
    "MACRO_CONTRADICTION", "MACRO_CYCLE", "MACRO_POLICY",
    "MISSION_CYCLE", "MISSION_STYLE", "MISSION_TARGET", "MISSION_THESIS",
    "NEWS_ITEMS", "PROFILE_INFO_ROWS",
    "QUALITY_BADGE", "QUALITY_NEGATIVES", "QUALITY_POSITIVES", "QUALITY_SCORES",
    "RISK_ROWS", "RISK_STOP_SIGNALS",
    "TECH_FUNDS", "TECH_LEVELS", "TECH_MOMENTUM", "TECH_PATTERNS",
    "TECH_PLAN_ROWS", "TECH_SCORES", "TECH_SIGNAL_CARDS", "TECH_STYLE",
    "TECH_TRENDS", "TECH_VERDICT",
    "TRACKING_CATALYSTS", "TRACKING_CHECKLIST", "TRACKING_SCORES",
    "TRACKING_SUMMARY",
    "VALUATION_FUND_TECH", "VALUATION_METHODS", "VALUATION_RR",
    "VALUATION_TARGETS",
)


# ---------------------------------------------------------------- 技术信号归一化

def _tri(sig: Any, key: str):
    """三态读取：True/False；缺键或无法识别 -> None（调用方渲染数据缺失）。

    Phase 1 采集落盘后布尔值可能是字符串 "True"/"False"，此处同时兼容
    真布尔、数字与常见字符串写法。
    """
    if not isinstance(sig, dict) or key not in sig:
        return None
    v = sig[key]
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    s = str(v).strip().lower()
    if s in ("1", "true", "t", "yes", "y", "on", "是", "真"):
        return True
    if s in ("0", "false", "f", "no", "n", "off", "", "none", "null", "nan",
             "否", "假"):
        return False
    return None


def _s(sig: Any, key: str, yes: str, no: str) -> str:
    t = _tri(sig, key)
    if t is None:
        return MISS
    return yes if t else no


# ---------------------------------------------------------------- 第 9 章确定性数据

def tech_from_blocks(blocks: dict) -> dict:
    """从 blocks 计算第 9 章所需的确定性技术数据。

    空数据降级：ind/kline 为空时 latest == {} 且全部信号为“数据缺失”，永不抛错。
    """
    blocks = blocks if isinstance(blocks, dict) else {}
    ind = blocks.get("technical_indicators") or []
    kline = blocks.get("kline_daily") or []
    sig = blocks.get("technical_signals") or {}
    latest = ind[-1] if ind else {}
    if not isinstance(latest, dict):
        latest = {}
    closes: list = []
    volumes: list = []
    # 边界归一化（评审 Finding 修复）：close/volume 逐值经 _num，
    # nan/inf/字符串/None 一律丢弃（= 缺失值），下游只拿到有限 float。
    for r in kline:
        if not isinstance(r, dict):
            continue
        c = _num(r.get("close"))
        vol = _num(r.get("volume"))
        if c is not None:
            closes.append(c)
        if vol is not None:
            volumes.append(vol)

    def mom(n: int):
        if len(closes) <= n:
            return None
        try:
            m = (closes[-1] / closes[-n - 1] - 1) * 100
        except (TypeError, ZeroDivisionError):
            return None
        # 除法结果非有限（如极端小基数）也按缺失降级，绝不返回 nan/±inf。
        return round(m, 2) if math.isfinite(m) else None

    if not latest:
        signals = {k: MISS for k in ("MACD", "KDJ", "RSI", "BOLL", "MA", "VOL")}
    else:
        rsi_over = _tri(sig, "rsi12_overbought")
        rsi_under = _tri(sig, "rsi12_oversold")
        if rsi_over is True:
            rsi = "超买"
        elif rsi_under is True:
            rsi = "超卖"
        elif rsi_over is not None or rsi_under is not None:
            rsi = "中性"
        else:
            rsi = MISS
        above = _tri(sig, "price_above_upper")
        below = _tri(sig, "price_below_lower")
        if above is True:
            boll = "突破上轨"
        elif below is True:
            boll = "跌破下轨"
        elif above is not None or below is not None:
            boll = "轨内运行"
        else:
            boll = MISS
        signals = {
            "MACD": _s(sig, "macd_golden_cross", "金叉",
                       "死叉" if _tri(sig, "macd_dead_cross") is True else "无交叉"),
            "KDJ": _s(sig, "kdj_golden_cross", "金叉",
                      "超买" if _tri(sig, "kdj_overbought") is True else "中性"),
            "RSI": rsi,
            "BOLL": boll,
            "MA": _s(sig, "ma5_above_ma20", "多头排列", "空头排列"),
            "VOL": MISS if not kline else "量价数据见K线图",
        }
    return {
        "latest": latest,
        "closes": closes,
        "volumes": volumes,
        "momentum": {w: mom(w) for w in (5, 10, 20, 60)},
        "signals": signals,
    }


# ---------------------------------------------------------------- 通用小组件

def _score_bar(dim: Any, score: Any, reason: Any = "") -> str:
    """质量/跟踪/技术评分条：.score-bar 行 + div.score-fill + width:X%（禁止 span 版）。

    标记结构对齐冻结 CSS（shared/template_base.css 评分条节）：
    .score-bar（行）> .score-label（维度名）+ .score-track >
    .score-fill（div，内联 width）+ .score-num（分值）；理由文本
    无对应 CSS 类，一律带内联样式，保证双主题可读。
    三段配色：>=70 用 var(--gold)／40-69 用 var(--orange-warn)／<40 用
    var(--red-up)，与 CSS 变量同名。

    score 缺失/非数值/非有限（nan、±inf）→ 该维渲染“数据缺失”，
    绝不让 int(nan) 抛 ValueError 或把 nan 写进槽位；超大有限值
    （如 1e300）先在 float 域夹紧再 int，避免 OverflowError 杀死渲染。"""
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return (f'<div class="score-bar"><span class="score-label">{_esc(dim)}</span>'
                f'<div class="score-track"><div class="score-fill" '
                f'style="width:0%"></div></div>'
                f'<span class="score-num">{MISS}</span></div>')
    if isinstance(score, float) and not math.isfinite(score):
        return (f'<div class="score-bar"><span class="score-label">{_esc(dim)}</span>'
                f'<div class="score-track"><div class="score-fill" '
                f'style="width:0%"></div></div>'
                f'<span class="score-num">{MISS}</span></div>')
    if isinstance(score, int):
        w = 100 if score > 100 else (0 if score < 0 else score)
    else:
        if score > 100:
            w = 100
        elif score < 0:
            w = 0
        else:
            w = int(score)
    if w >= 70:
        bg = "background:var(--gold);"
    elif w >= 40:
        bg = "background:var(--orange-warn);"
    else:
        bg = "background:var(--red-up);"
    r = (f'<span style="font-size:12px;color:var(--text-secondary);'
         f'margin-left:4px;">{_esc(reason)}</span>') if reason else ""
    return (f'<div class="score-bar"><span class="score-label">{_esc(dim)}</span>'
            f'<div class="score-track"><div class="score-fill" '
            f'style="width:{w}%;{bg}"></div></div>'
            f'<span class="score-num">{w}分</span>{r}</div>')


def _cut14(s: Any) -> str:
    """SVG <text> 截断：每段不超过 14 个字符。"""
    t = "" if s is None else str(s)
    return t if len(t) <= 14 else t[:14]


def _li(items: Any, prefix: str = "") -> str:
    items = items if isinstance(items, list) else []
    if not items:
        return f"<li>{MISS}</li>"
    return "".join(f"<li>{prefix}{_esc(it)}</li>" for it in items)


def _is_nonfinite_text(v: Any) -> bool:
    """字符串形态的非有限数值（"nan"/"inf"/"1e309" 等）→ 判 True，按缺失降级。

    数值槽位绝不回显这类文本（避免 "nan" 经 esc 直通槽位）；其余字符串
    （如 "18.71" 的带上后缀写法）保持原样转义显示。"""
    if not isinstance(v, str):
        return False
    try:
        return not math.isfinite(float(v.strip()))
    except ValueError:
        return False


def _fmt_money(v: Any) -> str:
    if v is None:
        return MISS
    if isinstance(v, bool):
        return _esc(v)
    if isinstance(v, (int, float)):
        if not math.isfinite(v):
            # nan/±inf 一律缺失，绝不输出 "nan亿"/"inf亿"。
            return MISS
        return f"{v / 1e8:.2f}亿"
    return MISS if _is_nonfinite_text(v) else _esc(v)


def _fmt_num(v: Any) -> str:
    if v is None:
        return MISS
    if isinstance(v, bool):
        return _esc(v)
    if isinstance(v, (int, float)):
        if not math.isfinite(v):
            return MISS
        return f"{v:.2f}"
    return MISS if _is_nonfinite_text(v) else _esc(v)


# ---------------------------------------------------------------- 各槽构建器

def _is_miss(v: Any) -> bool:
    """该单元格是否算占位：空串或等于/以"数据缺失"开头（含"数据缺失（可比同业）"这类）。"""
    s = str(v).strip() if v is not None else ""
    return (not s) or s == MISS or s.startswith(MISS)


def _drop_placeholder_rows(items: Any, keys: tuple[str, ...]) -> list[dict]:
    """丢弃"整条都是占位"的条目（keys 指定字段全部缺失）。

    模型为凑 MIN_LEN 条数下限会补"数据缺失"行，这是用户报告页面噪音的主要来源；
    只要该条还有任一真实字段就保留（部分单元格缺失属正常，不得误删）。
    """
    out: list[dict] = []
    for it in (items if isinstance(items, list) else []):
        if not isinstance(it, dict):
            continue
        vals = [it.get(k) for k in keys]
        if vals and all(_is_miss(v) for v in vals):
            continue
        out.append(it)
    return out


def _dividend_info_rows(blocks: dict) -> list[tuple[str, str]]:
    """确定性分红行（股息率 / 近12月每10股派息），供公司画像网格置顶显示。

    股息率来自 `report_shards.dividend_yield`（分红块 ÷ 现价），不依赖 LLM 输出；
    无分红数据时返回空列表——不硬塞一行"数据缺失"制造噪音。
    """
    from app.services.report_shards import dividend_yield
    dy = dividend_yield(blocks if isinstance(blocks, dict) else {})
    if dy.get("pct") is None:
        return []
    rows = [("股息率", f"{dy['pct']}%")]
    if dy.get("total_cash"):
        rows.append(("近12月每10股派息", f"{dy['total_cash']}元"))
    return rows


def _news_items(content: dict) -> str:
    items = _v(content, "news", "items", default=[]) or []
    if not isinstance(items, list) or not items:
        return f'<div class="news-item">{MISS}</div>'
    color = {"利好": "var(--red-up)", "利空": "var(--green-down)",
             "中性": "var(--text-muted)"}
    cls = {"利好": "bullish", "利空": "bearish", "中性": "neutral"}
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        s = it.get("sentiment") or MISS
        out.append(
            f'<div class="news-item {cls.get(s, "neutral")}" '
            f'style="border-left:4px solid {color.get(s, "var(--text-muted)")};'
            f"padding:8px 12px;background:var(--card-bg-alt);border-radius:6px;\">"
            f'<p style="font-size:13px;color:var(--text-secondary);'
            f'line-height:1.7;margin:0 0 6px;">{_esc(it.get("text"))}</p>'
            f'<span class="sentiment-tag sentiment-{_esc(s)} '
            f'{cls.get(s, "neutral")}">{_esc(s)}</span></div>')
    return "".join(out) or f'<div class="news-item">{MISS}</div>'


def _profile_info_rows(content: dict, blocks: dict) -> str:
    info = _v(content, "profile", "info", default=[]) or []
    info = [it for it in (info if isinstance(info, list) else []) if isinstance(it, dict)]
    det = _dividend_info_rows(blocks)
    det_keys = [k for k, _ in det]
    pairs: list[tuple[Any, Any]] = list(det) + [
        (it.get("k"), it.get("v")) for it in info
        if not any(k in str(it.get("k") or "") for k in det_keys)]
    if not pairs:
        return f"<dt>{MISS}</dt><dd>{MISS}</dd>"
    return "".join(f"<dt>{_esc(k)}</dt><dd>{_esc(v)}</dd>" for k, v in pairs)


# TODO(2027-01)：_FIN_YEARS 硬编码 FY2021–FY2025，FY2026 年报披露后需改为从数据推导（控制方裁定暂缓，见最终交代）
_FIN_ORDER = ("营业总收入", "归母净利润", "扣非净利润", "毛利率",
              "净资产收益率(ROE)")
_FIN_YEARS = (("20251231", "FY2025"), ("20241231", "FY2024"),
              ("20231231", "FY2023"), ("20221231", "FY2022"),
              ("20211231", "FY2021"))
_FIN_MONEY = {"营业总收入", "归母净利润", "扣非净利润"}


def _fin_rows(blocks: dict) -> str:
    rows = blocks.get("fin_abstract") if isinstance(blocks, dict) else None
    by_name = {}
    if isinstance(rows, list):
        for r in rows:
            if isinstance(r, dict) and r.get("指标") is not None:
                by_name[str(r.get("指标"))] = r
    out = []
    for name in _FIN_ORDER:
        r = by_name.get(name)
        cells = []
        for col, _label in _FIN_YEARS:
            v = r.get(col) if isinstance(r, dict) else None
            if v is None:
                cells.append(f"<td>{MISS}</td>")
            elif name in _FIN_MONEY:
                cells.append(f"<td>{_fmt_money(v)}</td>")
            else:
                cells.append(f"<td>{_fmt_num(v)}</td>")
        out.append(f"<tr><td>{_esc(name)}</td>{''.join(cells)}</tr>")
    return "".join(out)


def _kline_kpis(tech: dict) -> dict:
    closes = [_num(c) for c in (tech.get("closes") or [])]
    closes = [c for c in closes if c is not None]
    kl = [r for r in (tech.get("_kline") or []) if isinstance(r, dict)]
    # 现价只认最后一根 K 线的收盘价（逐值归一化）。不可用（缺/None/字符串/
    # nan/inf）时四项 KPI 全部“数据缺失”，绝不拿更早一天的收盘价冒充现价
    # 编造上行空间或建议周期。
    c = _num(kl[-1].get("close")) if kl else None
    if c is None:
        return {"KLINE_KPI_1": MISS, "KLINE_KPI_2": MISS,
                "KLINE_KPI_3": MISS, "KLINE_KPI_4": MISS}
    highs: list = []
    lows: list = []
    for r in kl:
        h = _num(r.get("high"))
        lo = _num(r.get("low"))
        if h is not None:
            highs.append(h)
        if lo is not None:
            lows.append(lo)
    # 聚合前 high/low 已逐值归一化，max/min 只收到有限 float，绝不抛 TypeError。
    maxh = max(highs) if highs else (max(closes) if closes else c)
    minl = min(lows) if lows else (min(closes) if closes else c)
    try:
        upside = (maxh - c) / c * 100 if c else None
    except (TypeError, ZeroDivisionError):
        upside = None
    if upside is not None and not math.isfinite(upside):
        upside = None
    kpi1 = MISS if upside is None else f"{upside:+.2f}%"
    try:
        denom = c - minl
        rr = (maxh - c) / denom if denom and denom > 0 else None
    except TypeError:
        rr = None
    if rr is not None and not math.isfinite(rr):
        rr = None
    kpi2 = f"1:{rr:.2f}" if rr is not None else MISS
    kpi3 = f"{maxh:.2f}"  # maxh 由归一化数组取 max，必为有限 float
    # 建议周期阶梯只在有限上行空间上取值；upside 缺失/非有限 → “数据缺失”，
    # 绝不掉进“2-4周（短线）”的默认看多分支。
    if upside is None:
        kpi4 = MISS
    elif upside >= 20:
        kpi4 = "3-6个月"
    elif upside >= 10:
        kpi4 = "1-3个月"
    else:
        kpi4 = "2-4周（短线）"
    return {"KLINE_KPI_1": _esc(kpi1) if kpi1 == MISS else kpi1,
            "KLINE_KPI_2": _esc(kpi2) if kpi2 == MISS else kpi2,
            "KLINE_KPI_3": _esc(kpi3) if kpi3 == MISS else kpi3,
            "KLINE_KPI_4": _esc(kpi4) if kpi4 == MISS else kpi4}


def _chain_nodes(content: dict) -> str:
    nodes = _v(content, "chain", "nodes", default=[]) or []
    if not isinstance(nodes, list):
        nodes = []
    nodes = [n for n in nodes if isinstance(n, dict)][:3]
    while len(nodes) < 3:
        nodes.append({"stage": MISS, "name": MISS, "sub": MISS})
    if not any(n.get("stage") != MISS or n.get("name") != MISS for n in nodes):
        return (f'<text x="400" y="90" text-anchor="middle" font-size="14" '
                f'fill="var(--text-muted)">{MISS}</text>')
    parts = []
    xs = (20, 300, 580)
    for x, n in zip(xs, nodes, strict=False):
        cx = x + 100
        parts.append(
            f'<g><rect x="{x}" y="45" width="200" height="80" rx="12" '
            f'fill="rgba(212,168,83,0.08)" stroke="#d4a853" stroke-width="2"/>'
            f'<text x="{cx}" y="72" text-anchor="middle" font-size="12" '
            f'fill="var(--gold-light)">{_esc(_cut14(n.get("stage")))}</text>'
            f'<text x="{cx}" y="94" text-anchor="middle" font-size="13" '
            f'fill="var(--text-primary)">{_esc(_cut14(n.get("name")))}</text>'
            f'<text x="{cx}" y="112" text-anchor="middle" font-size="11" '
            f'fill="var(--text-muted)">{_esc(_cut14(n.get("sub")))}</text></g>')
    for x1, x2 in ((220, 292), (500, 572)):
        parts.append(
            f'<line x1="{x1}" y1="85" x2="{x2}" y2="85" stroke="#d4a853" '
            f'stroke-width="2" marker-end="url(#arrow)"/>')
    return "".join(parts)


def _block_card(text: Any) -> str:
    if text is None or (isinstance(text, str) and text.strip() == ""):
        return MISS
    return (f'<div class="loop-block" style="font-size:13px;'
            f"color:var(--text-secondary);line-height:1.8;\">{_esc(text)}</div>")


def _quality_scores(content: dict) -> str:
    scores = _v(content, "quality", "scores", default=[]) or []
    if not isinstance(scores, list) or not scores:
        return MISS
    out = []
    for s in scores:
        if not isinstance(s, dict):
            continue
        out.append(_score_bar(s.get("dim"), s.get("score"), s.get("reason")))
    return "".join(out) or MISS


def _elastic_tree(content: dict) -> str:
    root = _v(content, "elasticity", "tree_root")
    branches = _v(content, "elasticity", "branches", default=[]) or []
    if not isinstance(branches, list):
        branches = []
    branches = [b for b in branches if isinstance(b, dict)][:3]
    while len(branches) < 3:
        branches.append({})
    cols = []
    flex = (2, 1, 1)
    for b, f in zip(branches, flex, strict=False):
        if not b:
            body = MISS
        else:
            body = (f'<div style="font-weight:700;color:var(--text-primary);'
                     f'margin-bottom:6px;">{_esc(b.get("name"))}</div>'
                     f'<div>营收占比：{_esc(b.get("rev_pct"))}</div>'
                     f'<div>毛利率：{_esc(b.get("gm_pct"))}</div>'
                     f'<div>层级：{_esc(b.get("tier"))}</div>'
                     f'<div>弹性因子：{_esc(b.get("factor"))}</div>')
        cols.append(
            f'<div style="flex:{f};background:var(--card-bg-alt);'
            f"border:1px solid var(--border);border-radius:8px;padding:10px 12px;"
            f'font-size:12px;color:var(--text-secondary);line-height:1.7;">'
            f"{body}</div>")
    return (
        f'<div class="elastic-tree"><div style="border:2px solid var(--gold);'
        f"border-radius:8px;padding:10px 14px;text-align:center;font-weight:700;"
        f'color:var(--gold-light);font-size:14px;">{_esc(root)}</div>'
        f'<div style="width:2px;height:18px;background:var(--gold);'
        f'margin:0 auto;"></div>'
        f'<div style="border-top:2px solid var(--gold);width:70%;'
        f'margin:0 auto 12px;"></div>'
        f'<div style="display:flex;gap:12px;">{"".join(cols)}</div></div>')


def _elastic_scenarios(content: dict) -> str:
    scens = _v(content, "elasticity", "scenarios", default=[]) or []
    if not isinstance(scens, list):
        scens = []
    scens = [s for s in scens if isinstance(s, dict)][:3]
    while len(scens) < 3:
        scens.append({})
    cards = []
    for s in scens:
        if not s:
            body = MISS
        else:
            body = (f'<div style="font-weight:700;color:var(--gold-light);'
                     f'margin-bottom:6px;">{_esc(s.get("name"))}</div>'
                     f'<div>目标价：{_esc(s.get("price"))}</div>'
                     f'<div>概率：{_esc(s.get("prob"))}</div>')
        cards.append(
            f'<div style="background:var(--card-bg-alt);border-radius:8px;'
            f"padding:12px 14px;font-size:13px;color:var(--text-secondary);"
            f'line-height:1.7;">{body}</div>')
    return (f'<div style="display:grid;grid-template-columns:repeat(3,1fr);'
            f'gap:12px;">{"".join(cards)}</div>')


def _risk_rows(content: dict) -> str:
    matrix = _v(content, "risk", "matrix", default=[]) or []
    if not isinstance(matrix, list) or not matrix:
        return f'<tr><td colspan="4">{MISS}</td></tr>'
    out = []
    for r in matrix:
        if not isinstance(r, dict):
            continue
        out.append(f"<tr><td>{_esc(r.get('risk'))}</td>"
                   f"<td>{_esc(r.get('mitigation'))}</td>"
                   f"<td>{_esc(r.get('impact'))}</td>"
                   f"<td>{_esc(r.get('prob'))}</td></tr>")
    return "".join(out) or f'<tr><td colspan="4">{MISS}</td></tr>'


def _valuation_targets(content: dict) -> str:
    targets = _v(content, "valuation", "targets", default=[]) or []
    if not isinstance(targets, list):
        targets = []
    targets = [t for t in targets if isinstance(t, dict)][:3]
    while len(targets) < 3:
        targets.append({})
    cards = []
    for t in targets:
        if not t:
            body = MISS
        else:
            body = (f'<div style="font-size:12px;color:var(--text-muted);">'
                     f'{_esc(t.get("scenario"))}</div>'
                     f'<div style="font-size:18px;font-weight:700;'
                     f'color:var(--red-up);">{_esc(t.get("price"))}</div>')
        cards.append(
            f'<div style="background:var(--card-bg-alt);border:1px solid '
            f"var(--gold);border-radius:8px;padding:12px;text-align:center;\">"
            f"{body}</div>")
    return (f'<div style="display:grid;grid-template-columns:repeat(3,1fr);'
            f'gap:12px;">{"".join(cards)}</div>')


def _compare_rows(content: dict) -> str:
    rows = _drop_placeholder_rows(
        _v(content, "compare", "rows", default=[]) or [], ("company", "metric", "value"))
    if not rows:
        return f'<tr><td colspan="3">{MISS}</td></tr>'
    return "".join(
        f"<tr><td>{_esc(r.get('company'))}</td>"
        f"<td>{_esc(r.get('metric'))}</td>"
        f"<td>{_esc(r.get('value'))}</td></tr>" for r in rows)


def _compare_engines(content: dict) -> str:
    engines = _v(content, "compare", "engines", default=[]) or []
    if not isinstance(engines, list):
        engines = []
    engines = [e for e in engines if isinstance(e, str) and e][:3]
    while len(engines) < 3:
        engines.append(MISS)
    return "".join(
        f'<div class="engine-card" style="flex:1;min-width:160px;'
        f"background:var(--card-bg-alt);border-radius:8px;padding:10px 14px;\">"
        f"{_esc(e)}</div>" for e in engines)


def _tracking_catalysts(content: dict) -> str:
    cats = _v(content, "tracking", "catalysts", default=[]) or []
    if not isinstance(cats, list) or not cats:
        return (f'<table class="cons"><thead><tr><th>催化剂事件</th><th>时间</th>'
                f"<th>状态</th></tr></thead><tbody>"
                f'<tr><td colspan="3">{MISS}</td></tr></tbody></table>')
    rows = "".join(
        f"<tr><td>{_esc(c.get('event'))}</td><td>{_esc(c.get('date'))}</td>"
        f"<td>{_esc(c.get('status'))}</td></tr>"
        for c in cats if isinstance(c, dict)) or \
        f'<tr><td colspan="3">{MISS}</td></tr>'
    return (f'<table class="cons"><thead><tr><th>催化剂事件</th><th>时间</th>'
            f"<th>状态</th></tr></thead><tbody>{rows}</tbody></table>")


def _tracking_scores(content: dict) -> str:
    scores = _v(content, "tracking", "scores", default=[]) or []
    if not isinstance(scores, list) or not scores:
        return MISS
    out = []
    for s in scores:
        if not isinstance(s, dict):
            continue
        out.append(_score_bar(s.get("dim"), s.get("score")))
    return "".join(out) or MISS


# ---------------------------------------------------------------- 第 9 章展示槽

_SIGNAL_LABEL = (("MACD", "MACD"), ("KDJ", "KDJ"), ("RSI", "RSI"),
                 ("BOLL", "BOLL布林"), ("MA", "均线"), ("VOL", "成交量"))


def _tech_signal_cards(tech: dict) -> str:
    sig = tech.get("signals") or {}
    cards = []
    for key, label in _SIGNAL_LABEL:
        cards.append(
            f'<div class="signal-card" style="background:var(--card-bg-alt);'
            f"border:1px solid var(--border);border-radius:8px;padding:10px 12px;"
            f'text-align:center;"><div style="font-size:12px;'
            f'color:var(--text-muted);">{label}</div>'
            f'<div style="font-size:15px;font-weight:700;'
            f'color:var(--text-primary);margin-top:4px;">'
            f"{_esc(sig.get(key))}</div></div>")
    return "".join(cards)


def _tech_trends(tech: dict) -> str:
    if not tech.get("latest"):
        return MISS
    mom = tech.get("momentum") or {}
    cols = []
    for w, name in ((5, "短期（5日）"), (20, "中期（20日）"), (60, "长期（60日）")):
        v = mom.get(w)
        if v is None:
            body = MISS
        else:
            arrow = "走强" if v > 2 else ("走弱" if v < -2 else "震荡")
            body = (f'<div style="font-size:18px;font-weight:700;'
                     f'color:var(--text-primary);">{v:+.2f}%</div>'
                     f'<div style="font-size:12px;color:var(--text-muted);">'
                     f"{arrow}</div>")
        cols.append(
            f'<div style="background:var(--card-bg-alt);border-radius:8px;'
            f"padding:12px;text-align:center;\">"
            f'<div style="font-size:12px;color:var(--text-muted);'
            f'margin-bottom:6px;">{name}</div>{body}</div>')
    return (f'<div style="display:grid;grid-template-columns:repeat(3,1fr);'
            f'gap:12px;">{"".join(cols)}</div>')


def _tech_levels(tech: dict) -> str:
    latest = tech.get("latest") or {}
    if not latest:
        return MISS
    closes = tech.get("closes") or []
    rows = [
        ("当前价", _fmt_num(latest.get("close"))),
        ("BOLL上轨", _fmt_num(latest.get("BOLL_UPPER"))),
        ("BOLL中轨", _fmt_num(latest.get("BOLL_MID"))),
        ("BOLL下轨", _fmt_num(latest.get("BOLL_LOWER"))),
        ("MA20", _fmt_num(latest.get("MA20"))),
        ("MA60", _fmt_num(latest.get("MA60"))),
    ]
    if closes:
        rows.append(("近期最高", _fmt_num(max(closes))))
        rows.append(("近期最低", _fmt_num(min(closes))))
    body = "".join(f"<tr><td>{_esc(k)}</td><td>{v}</td></tr>" for k, v in rows)
    return (f'<table class="cons"><thead><tr><th>关键位</th><th>数值</th></tr>'
            f"</thead><tbody>{body}</tbody></table>")


def _tech_patterns(tech: dict) -> str:
    sig = tech.get("signals") or {}
    if not tech.get("latest"):
        return MISS
    notes = {"MACD": "动能方向", "KDJ": "短线买卖点", "RSI": "强弱状态",
             "BOLL": "波动位置", "MA": "趋势排列", "VOL": "量能配合"}
    rows = "".join(
        f"<tr><td>{label}</td><td>{_esc(sig.get(k))}</td>"
        f"<td>{_esc(notes[k])}</td></tr>"
        for k, label in _SIGNAL_LABEL)
    return (f'<table class="cons"><thead><tr><th>形态/指标</th><th>当前状态</th>'
            f"<th>解读维度</th></tr></thead><tbody>{rows}</tbody></table>")


def _tech_momentum(tech: dict) -> str:
    if not tech.get("latest"):
        return MISS
    mom = tech.get("momentum") or {}
    sig = tech.get("signals") or {}
    cells = []
    for w in (5, 10, 20, 60):
        v = mom.get(w)
        cells.append(
            f'<div style="flex:1;min-width:100px;background:var(--card-bg-alt);'
            f'border-radius:8px;padding:10px;text-align:center;">'
            f'<div style="font-size:11px;color:var(--text-muted);">{w}日涨跌</div>'
            f'<div style="font-weight:700;font-size:15px;">'
            f"{MISS if v is None else f'{v:+.2f}%'}</div></div>")
    return (
        f'<div style="display:flex;gap:12px;flex-wrap:wrap;">{"".join(cells)}'
        f"</div>"
        f'<p style="font-size:13px;color:var(--text-secondary);margin-top:10px;">'
        f"量价状态：{_esc(sig.get('VOL'))}</p>")


def _tech_funds(tech: dict) -> str:
    if not tech.get("latest"):
        return MISS
    vols = tech.get("volumes") or []
    latest_vol = vols[-1] if vols else None
    avg5 = (sum(vols[-5:]) / len(vols[-5:])) if vols else None
    ratio = None
    if latest_vol and avg5:
        try:
            ratio = latest_vol / avg5
        except (TypeError, ZeroDivisionError):
            ratio = None
    if ratio is not None and not math.isfinite(ratio):
        # 非有限量比（极端小均量等）一律缺失，绝不渲染 “量比nan”。
        ratio = None
    if ratio is None:
        state = MISS
    elif ratio >= 1.2:
        state = f"放量（量比{ratio:.2f}）"
    elif ratio <= 0.8:
        state = f"缩量（量比{ratio:.2f}）"
    else:
        state = f"量能持平（量比{ratio:.2f}）"
    return (
        f'<div class="loop-block" style="font-size:13px;'
        f"color:var(--text-secondary);line-height:1.8;\">"
        f"<div>最新成交量：{_fmt_num(latest_vol)}</div>"
        f"<div>近5日均量：{_fmt_num(round(avg5, 2) if avg5 else None)}</div>"
        f"<div>资金状态：{_esc(state)}</div></div>")


def _tech_style(tech: dict) -> str:
    latest = tech.get("latest") or {}
    if not latest:
        return MISS
    sig = tech.get("signals") or {}
    close = _num(latest.get("close"))
    ma60 = _num(latest.get("MA60"))
    if close is not None and ma60:
        pos = "价格站上MA60（趋势偏强）" if close > ma60 else "价格位于MA60之下（趋势偏弱）"
    else:
        # 任一端缺失/非有限 → 不下结论，降级“数据缺失”，绝不因 nan 比较为 False 而误判偏弱。
        pos = MISS
    ma = sig.get("MA") or MISS
    boll = sig.get("BOLL") or MISS
    return (f'<div class="loop-block" style="font-size:13px;'
            f"color:var(--text-secondary);line-height:1.8;\">"
            f"<div>趋势位置：{_esc(pos)}</div>"
            f"<div>均线结构：{_esc(ma)}</div>"
            f"<div>波动状态：{_esc(boll)}</div></div>")


def _tech_scores(tech: dict) -> str:
    """技术面五维评分：全部由 tech_from_blocks 确定性推导（content 无对应字段）。

    空数据时每维渲染“数据缺失”。
    """
    latest = tech.get("latest") or {}
    if not latest:
        return "".join(
            f'<div class="score-bar"><span class="score-label">{d}</span>'
            f'<div class="score-track"><div class="score-fill" style="width:0%"></div></div><span class="score-num">{MISS}</span></div>'
            for d in ("趋势", "动量", "量能", "空间", "支撑"))
    mom = tech.get("momentum") or {}
    sig = tech.get("signals") or {}
    vols = tech.get("volumes") or []
    closes = tech.get("closes") or []
    m20 = mom.get(20)
    if m20 is None:
        s_mom = None
    elif m20 >= 10:
        s_mom = 90
    elif m20 >= 5:
        s_mom = 80
    elif m20 > 0:
        s_mom = 65
    elif m20 > -5:
        s_mom = 45
    else:
        s_mom = 30
    if vols:
        tail = vols[-5:]
        avg = sum(tail) / len(tail)
        ratio = vols[-1] / avg if avg else None
    else:
        ratio = None
    if ratio is not None and not math.isfinite(ratio):
        # 非有限量比一律缺失，量能维度降级而非误判“一致量能”。
        ratio = None
    if ratio is None:
        s_vol = None
    elif ratio >= 1.5:
        s_vol = 85
    elif ratio >= 1.2:
        s_vol = 75
    elif ratio >= 0.8:
        s_vol = 60
    else:
        s_vol = 40
    c = closes[-1] if closes else None
    hi = max(closes) if closes else None
    if isinstance(c, (int, float)) and isinstance(hi, (int, float)) \
            and not isinstance(c, bool) and c:
        up = (hi - c) / c * 100
        # c/hi 均为归一化后的有限值，除法结果非有限只可能来自极端小基数。
        if math.isfinite(up):
            s_room = 85 if up >= 30 else (70 if up >= 15 else (55 if up > 0 else 35))
        else:
            s_room = None
    else:
        s_room = None
    ma60 = _num(latest.get("MA60"))  # 归一化：字符串/nan/inf 一律缺失
    s_sup = (75 if c > ma60 else 35) if c is not None and ma60 else None
    dims = (("趋势", 80 if sig.get("MA") == "多头排列"
             else (30 if sig.get("MA") not in (None, MISS) else None)),
            ("动量", s_mom), ("量能", s_vol), ("空间", s_room), ("支撑", s_sup))
    return "".join(_score_bar(d, s) for d, s in dims)


# ---------------------------------------------------------------- 总装

def _tech_plan_rows(content: dict) -> str:
    plan = _drop_placeholder_rows(
        _v(content, "technical", "plan", default=[]) or [], ("action", "trigger", "position"))
    if not plan:
        return f'<tr><td colspan="3">{MISS}</td></tr>'
    return "".join(
        f"<tr><td>{_esc(p.get('action'))}</td>"
        f"<td>{_esc(p.get('trigger'))}</td>"
        f"<td>{_esc(p.get('position'))}</td></tr>" for p in plan)


def build_loop_slots(content: dict, blocks: dict) -> dict[str, str]:
    """构建全部 47 个循环槽。content/blocks 缺失时降级为“数据缺失”，不抛错。"""
    content = content if isinstance(content, dict) else {}
    blocks = blocks if isinstance(blocks, dict) else {}
    tech = tech_from_blocks(blocks)
    tech["_kline"] = blocks.get("kline_daily") or []
    slots = {
        "NEWS_ITEMS": _news_items(content),
        "PROFILE_INFO_ROWS": _profile_info_rows(content, blocks),
        "FIN_ROWS": _fin_rows(blocks),
        "MISSION_TARGET": _esc(_v(content, "mission", "target")),
        "MISSION_CYCLE": _esc(_v(content, "mission", "cycle")),
        "MISSION_STYLE": _esc(_v(content, "mission", "style")),
        "MISSION_THESIS": _esc(_v(content, "mission", "thesis")),
        "MACRO_CYCLE": _esc(_v(content, "macro", "cycle_position")),
        "MACRO_POLICY": _esc(_v(content, "macro", "policy_scan")),
        "MACRO_CONTRADICTION": _esc(_v(content, "macro", "core_contradiction")),
        "CHAIN_NODES": _chain_nodes(content),
        "CHAIN_TREND": _block_card(_v(content, "chain", "trend_three")),
        "CHAIN_PROFIT": _block_card(_v(content, "chain", "profit_distribution")),
        "CHAIN_JUDGMENT": _esc(_v(content, "chain", "judgment")),
        "QUALITY_POSITIVES": _li(_v(content, "quality", "positives",
                                      default=[]), "✅ "),
        "QUALITY_NEGATIVES": _li(_v(content, "quality", "negatives",
                                      default=[]), "⚠️ "),
        "QUALITY_SCORES": _quality_scores(content),
        "QUALITY_BADGE": (
            f'<div class="rating-callout" style="margin-top:12px;'
            f"background:rgba(212,168,83,0.08);border-left:4px solid var(--gold);"
            f'padding:10px 14px;border-radius:6px;">'
            f'<strong style="color:var(--gold);font-size:14px;">⭐ '
            f"{_esc(_v(content, 'quality', 'badge'))}</strong></div>"),
        "ELASTIC_TREE": _elastic_tree(content),
        "ELASTIC_SENSITIVITY": (
            f'<div class="formula-card" style="background:var(--card-bg-alt);'
            f"border:1px dashed var(--gold);border-radius:8px;padding:12px 16px;"
            f'font-size:13px;color:var(--text-secondary);line-height:1.8;">'
            f"📐 {_esc(_v(content, 'elasticity', 'sensitivity'))}</div>"),
        "ELASTIC_SCENARIOS": _elastic_scenarios(content),
        "RISK_ROWS": _risk_rows(content),
        "RISK_STOP_SIGNALS": _li(_v(content, "risk", "stop_signals",
                                      default=[]), "🛑 "),
        "VALUATION_METHODS": _block_card(_v(content, "valuation", "methods")),
        "VALUATION_TARGETS": _valuation_targets(content),
        "VALUATION_RR": (
            f'<div class="loop-block" style="font-size:13px;'
            f"color:var(--text-secondary);line-height:1.8;\">"
            f"⚖️ 盈亏比：{_esc(_v(content, 'valuation', 'rr'))}</div>"),
        "VALUATION_FUND_TECH": _block_card(_v(content, "valuation",
                                                 "fund_tech")),
        "COMPARE_ROWS": _compare_rows(content),
        "COMPARE_ENGINES": _compare_engines(content),
        "TRACKING_CATALYSTS": _tracking_catalysts(content),
        "TRACKING_CHECKLIST": _li(_v(content, "tracking", "checklist",
                                       default=[]), "☑️ "),
        "TRACKING_SCORES": _tracking_scores(content),
        "TRACKING_SUMMARY": _esc(_v(content, "tracking", "summary")),
        "TECH_SIGNAL_CARDS": _tech_signal_cards(tech),
        "TECH_TRENDS": _tech_trends(tech),
        "TECH_LEVELS": _tech_levels(tech),
        "TECH_PATTERNS": _tech_patterns(tech),
        "TECH_MOMENTUM": _tech_momentum(tech),
        "TECH_FUNDS": _tech_funds(tech),
        "TECH_STYLE": _tech_style(tech),
        "TECH_VERDICT": _esc(_v(content, "technical", "verdict")),
        "TECH_PLAN_ROWS": _tech_plan_rows(content),
        "TECH_SCORES": _tech_scores(tech),
    }
    slots.update(_kline_kpis(tech))
    if set(slots) != set(LOOP_SLOT_NAMES):
        raise ValueError(
            f"loop slots mismatch: {sorted(set(LOOP_SLOT_NAMES) - set(slots))}")
    return slots
