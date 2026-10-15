"""个股深度研报七分片管线（由 stock_ai analyze_api 移植并异步化）。

保留：数据包 digest、七分片 system/user prompt、JSON 抽取、分片校验/收敛式重试/
降级、填充率，以及分片覆盖/顺序断言。K 线清洗工具移至 app.reporting.kline；
脚本注入移至 app.reporting.script_builder。LLM 调用走 app.services.ai_provider
（研报端点覆盖 + JSON mode）；密钥只进请求配置，不进日志。
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
import re
import time
from typing import Any

from app.config import settings
from app.reporting.content_schema import ITEM_KEYS, MIN_LEN, SCHEMA, validate_content
from app.reporting.kline import _num, kline_rows, latest_indicators
from app.services import ai_provider
from app.services.report_ai_config import resolve as resolve_report_ai

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 常量

MISSING = "数据缺失"

TEMPERATURE = 0.2                       # 结论要稳定：低温 + 明确 JSON 契约（0.4→0.2，提高模式 adherence）
# 七分片上限（小片小上限：单片 JSON 越小、一次成型率越高；valuation_risk 四块最大，单独给 12000）
SHARD_MAX_TOKENS = {
    "identity": 6000,
    "verdict": 8000,
    "macro_chain": 8000,
    "quality_news": 10000,
    "valuation_risk": 12000,
    "tracking": 6000,
    "technical": 8000,
}
# 七分片：每片 ≤4 块（除 valuation_risk 外均为 ≤3 块），15 个 content_schema 块恰好覆盖一次
SHARDS = {
    "identity": "只输出 JSON，键：meta/profile",
    "verdict": "只输出 JSON，键：hero/conclusion",
    "macro_chain": "只输出 JSON，键：mission/macro/chain",
    "quality_news": "只输出 JSON，键：quality/news",
    "valuation_risk": "只输出 JSON，键：valuation/risk/elasticity/compare",
    "tracking": "只输出 JSON，键：tracking",
    "technical": "只输出 JSON，键：technical",
}
SHARD_ORDER = ("identity", "verdict", "macro_chain", "quality_news",
               "valuation_risk", "tracking", "technical")
RESEARCH_KEYS = ("报告名称", "东财评级", "机构", "日期",
                 "2026-盈利预测-收益", "2026-盈利预测-市盈率")


def _keys_from_spec(spec: str) -> tuple[str, ...]:
    """从 SHARDS 说明里解析键清单并对照 SCHEMA 校验（不一致则导入即失败，防 prompt 漂移）。"""
    names = [k for k in spec.partition("键：")[2].split("/") if k]
    unknown = [k for k in names if k not in SCHEMA]
    if not names or unknown:
        raise ValueError(f"分片键清单与 content_schema 不一致：{unknown or names}")
    return tuple(names)


# 分片键清单直接由 SHARDS 的键说明派生，避免 schema 与 prompt 两处漂移
SHARD_KEYS: dict[str, tuple[str, ...]] = {k: _keys_from_spec(s) for k, s in SHARDS.items()}

# 覆盖断言（导入即验）：15 个块必须被恰好覆盖一次——多一片、少一块、重复覆盖都直接失败，
# 防分片键清单与 content_schema 漂移（schema 是合同，分片必须满足它，而不是反过来）。
_COVERED = sorted(b for keys in SHARD_KEYS.values() for b in keys)
if sorted(SCHEMA) != _COVERED:
    raise ValueError(f"分片覆盖与 content_schema 不一致（须恰好覆盖一次）：{_COVERED}")
# 顺序断言（导入即验）：SHARD_ORDER 是手写的执行顺序，必须恰好列出 SHARDS 的全部键一次——
# 加片/改名只改 SHARDS 却漏改 SHARD_ORDER 时导入即失败，而不是静默少跑一片。
if tuple(sorted(SHARD_ORDER)) != tuple(sorted(SHARDS)) or len(SHARD_ORDER) != len(set(SHARD_ORDER)):
    raise ValueError(f"SHARD_ORDER 必须恰好列出 SHARDS 的全部键一次：{SHARD_ORDER}")

# ---------------------------------------------------------------- LLM 调用（研报端点覆盖 + JSON mode）


async def _call_llm(system: str, user: str, max_tokens: int) -> str:
    """七分片单次调用：研报端点覆盖 + JSON mode；密钥只进请求配置，不进日志。"""
    cap = int(getattr(settings, "report_ai_max_output_tokens", 16000) or 16000)
    return await ai_provider.generate_ai_text(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=TEMPERATURE,
        max_tokens=min(max_tokens, cap),
        timeout=600.0,
        response_format={"type": "json_object"},
        ai_config=resolve_report_ai(),
    )


# ---------------------------------------------------------------- JSON 抽取

def extract_json(text: Any) -> dict:
    """去 markdown 围栏 → 取第一个 `{` 到最后一个 `}` → json.loads。失败抛 ValueError。"""
    if not isinstance(text, str):
        raise ValueError(f"输出不是文本（{type(text).__name__}）")
    s = text.strip()
    s = re.sub(r"^```[A-Za-z0-9_+-]*[ \t]*\r?\n?", "", s)
    s = re.sub(r"\r?\n?```$", "", s).strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("未找到完整 JSON 对象（缺少花括号）")
    try:
        obj = json.loads(s[i:j + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 解析失败：{e.msg}（行 {e.lineno} 列 {e.colno}）") from e
    if not isinstance(obj, dict):
        raise ValueError(f"JSON 根节点应为对象，实际 {type(obj).__name__}")
    return obj


# 数值清洗与 K 线工具见 app.reporting.kline（digest 与脚本注入共用）。


# ---------------------------------------------------------------- digest

def _jd(obj: Any, limit: int | None = None) -> str:
    """JSON 文本化（prompt 用）：非有限值/不可序列化 → 数据缺失，绝不让 NaN 进提示词。"""
    try:
        text = json.dumps(obj, ensure_ascii=False, allow_nan=False, default=str)
    except (TypeError, ValueError):
        return MISSING
    return text[:limit] if limit else text


def _head(v: Any, n: int) -> Any:
    return v[:n] if isinstance(v, list) else v


def _jv(v: Any, limit: int | None = None) -> str:
    """JSON 文本化；空值（None/空串/空容器）直接写"数据缺失"（避免 prompt 里出现 null）。"""
    if v is None or (isinstance(v, (str, list, dict, tuple)) and len(v) == 0):
        return MISSING
    return _jd(v, limit)


def _rows(v: Any) -> list:
    """列表化 + 去掉 None 项；非列表 → []。"""
    return [x for x in v if x is not None] if isinstance(v, list) else []


def _dict_rows(v: Any) -> list[dict]:
    return [x for x in _rows(v) if isinstance(x, dict)]


def _momentum(rows: list[list]) -> str:
    """动量：近 5/10/20/60 日涨跌幅；K 线不足或基期为 0 → 该窗口写数据缺失。"""
    closes = [r[4] for r in rows]
    parts = []
    for w in (5, 10, 20, 60):
        if len(closes) > w and closes[-w - 1]:
            parts.append(f"近{w}日{round((closes[-1] / closes[-w - 1] - 1) * 100, 2)}%")
        else:
            parts.append(f"近{w}日{MISSING}")
    return "【动量】" + " ".join(parts)


def _news_line(r: Any) -> str:
    """新闻一条 → '日期 标题'；兼容 akshare 的多种字段名与纯字符串。"""
    title, date = "", ""
    if isinstance(r, dict):
        for k in ("新闻标题", "标题", "title", "摘要", "内容"):
            if r.get(k):
                title = str(r[k]).strip()
                break
        for k in ("发布时间", "时间", "日期", "date", "datetime"):
            if r.get(k):
                date = str(r[k]).strip()
                break
    elif r is not None:
        title = str(r).strip()
    title = title.replace("\n", " ")[:120]
    line = f"{date[:10]} {title}".strip()
    return line or MISSING


# ---------------------------------------------------------------- 分红/股息率（确定性）

DIV_PER_LOT = 10.0   # akshare 分红送配的「派息」口径：每 10 股派息（元，税前）


def _day10(v: Any) -> str:
    """'2026-06-26T00:00:00.000' / '20260626' → 'YYYY-MM-DD'；无法解析 → ''。"""
    s = str(v or "").strip()
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return ""


def spot_price(d: Any) -> float | None:
    """现价：spot 最新价优先，缺失回退最后一根 K 线收盘价（新浪快照常为空）。"""
    for row in _dict_rows((d if isinstance(d, dict) else {}).get("spot")):
        for col in ("最新价", "现价", "price", "close"):
            p = _num(row.get(col))
            if p and p > 0:
                return p
    rows = kline_rows(d)
    if rows and len(rows[-1]) > 4:
        c = _num(rows[-1][4])
        if c and c > 0:
            return c
    # K 线行不完整（缺 OHLC 任一项会被 kline_rows 跳过）时，仍取最后一行的 close
    src = (d if isinstance(d, dict) else {}).get("kline_daily")
    if isinstance(src, list):
        for r in reversed(src):
            if isinstance(r, dict):
                c = _num(r.get("close"))
                if c and c > 0:
                    return c
                break
    return None


def dividend_yield(blocks: Any, today: Any = None) -> dict:
    """近 12 个月现金分红 ÷ 现价 → 股息率（确定性计算，不依赖 LLM）。

    「派息」按每 10 股派息（元，税前）理解 → 每股 = 派息 / 10；只计「实施」且落在
    近 365 天窗口内的记录（预案/未实施不计入）。返回 pct/per_share/price/total_cash/
    count/latest_date/latest_cash/window_start；价格或分红不足时相应键为 None。
    """
    d = blocks if isinstance(blocks, dict) else {}
    end = _day10(today) or dt.date.today().isoformat()
    start = (dt.date.fromisoformat(end) - dt.timedelta(days=365)).isoformat()
    price = spot_price(d)
    total, count = 0.0, 0
    latest_date, latest_cash = "", None
    for row in _dict_rows(d.get("dividend")):
        prog = str(row.get("进度") or "").strip()
        if prog and "实施" not in prog:
            continue
        day = _day10(row.get("除权除息日") or row.get("公告日期"))
        cash = _num(row.get("派息"))
        if cash is None:
            continue
        if latest_cash is None or (day and day >= latest_date):
            latest_date, latest_cash = day or latest_date, cash
        if day and start <= day <= end:
            total += cash
            count += 1
    per_share = round(total / DIV_PER_LOT, 4) if count else None
    pct = round(per_share / price * 100, 2) if (per_share and price) else None
    return {"pct": pct, "per_share": per_share, "price": price,
            "total_cash": round(total, 2) if count else None, "count": count,
            "latest_date": latest_date or None, "latest_cash": latest_cash,
            "window_start": start}


def _recent_lines(d: Any, limit: int = 8) -> list[str]:
    """近期动态素材：新闻优先；news/notice 为空时回退研报、高管变动、股东户数。

    上游 news/notice 接口长期取不到数（Arrow 解析失败），若不给回退素材，
    模型只能拿"数据缺失"凑够 news.items 的下限（页面噪音的真正来源）。
    """
    d = d if isinstance(d, dict) else {}
    out = [x for x in (_news_line(r) for r in _rows(d.get("news"))[:limit]) if x and x != MISSING]
    if len(out) < limit:
        for r in _dict_rows(d.get("research")):
            title = str(r.get("报告名称") or "").strip().replace("\n", " ")[:60]
            if not title:
                continue
            org = str(r.get("机构") or "").strip()[:16]
            rate = str(r.get("东财评级") or "").strip()[:8]
            out.append(f"研报 {org}《{title}》" + (f"[{rate}]" if rate else ""))
            if len(out) >= limit:
                break
    if len(out) < limit:
        for r in _dict_rows(d.get("share_hold_change"))[:limit]:
            day = _day10(r.get("变动日期"))
            who = str(r.get("姓名") or r.get("公司名称") or "").strip()[:12]
            chg = r.get("变动数") if r.get("变动数") is not None else r.get("变动数量")
            out.append(f"{day} 高管持股变动 {who} {chg}".strip())
            if len(out) >= limit:
                break
    if len(out) < limit:
        for r in _dict_rows(d.get("gdhs"))[:limit]:
            day = _day10(r.get("股东户数统计截止日"))
            n = r.get("股东户数-本次")
            if n is None:
                n = r.get("股东户数")
            chg = r.get("股东户数-增减比例")
            if chg is None:
                chg = r.get("股东户数-增减")
            out.append(f"{day} 股东户数 {n}（较上期 {chg}）".strip())
            if len(out) >= limit:
                break
    return out[:limit]


# 数据派生的公司简称来源（块，候选列）：全程只从数据里找，无硬编码对照表。
# 与 report_pipeline.resolve_name 同口径（后者另有 meta 优先与 item/value 形态）。
NAME_SOURCES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("spot", ("名称", "name")),
    ("basic_info", ("名称", "股票简称", "证券简称", "简称", "name", "stock_name")),
    ("research", ("股票简称",)),
    ("gdhs", ("名称",)),
    ("share_hold_change", ("公司名称",)),
)


def _usable_name(v: Any, code: str = "") -> str:
    """可用名称：非空字符串，且不是纯数字、也不是代码本身——否则视为不可用继续回退。"""
    s = v.strip() if isinstance(v, str) and v.strip() else ""
    if not s or s.isdigit():
        return ""
    if code and s == str(code).strip():
        return ""
    return s


def resolve_company_name(blocks: Any, code: str = "") -> str:
    """数据块 → 公司简称（确定性、与 resolve_name 同口径）。

    按 NAME_SOURCES 逐块逐列取第一个可用名；无可用名时回退代码（代码为空则回空串，
    调用方自行写"数据缺失"）。spot/basic_info 双空时 research/gdhs/share_hold_change
    的缓存行仍能给出真名（如 600519 → 贵州茅台）。
    """
    d = blocks if isinstance(blocks, dict) else {}
    for blk, cols in NAME_SOURCES:
        src = d.get(blk)
        if not isinstance(src, list):
            continue
        for row in src:
            if not isinstance(row, dict):
                continue
            for col in cols:
                hit = _usable_name(row.get(col), code)
                if hit:
                    return hit
    return str(code) if code else ""


def build_digest(blocks: Any) -> str:
    """数据包 → 提示词摘要（现价/主营/财务/股东/研报/技术信号/最新指标/动量/K线尾/资金流/近期新闻）。"""
    d = blocks if isinstance(blocks, dict) else {}
    rows = kline_rows(d)
    lines: list[str] = []
    lines.append("【公司简称】" + (resolve_company_name(d) or MISSING))
    lines.append("【现价】" + _jv(_head(d.get("spot"), 3), 900))
    dy = dividend_yield(d)
    if dy.get("pct") is not None:
        lines.append(f"【股息率】{dy['pct']}%（近12个月每10股派息合计 {dy['total_cash']} 元"
                 f"／{dy['count']} 次；现价 {dy['price']} 元）")
    else:
        lines.append("【股息率】" + MISSING)
    if dy.get("latest_cash") is not None:
        lines.append(f"【最近分红】{dy.get('latest_date') or MISSING} 除权除息，"
                 f"每10股派息 {dy['latest_cash']} 元")
    lines.append("【主营构成】")
    zygc = _dict_rows(d.get("zygc"))[:10]
    lines += [_jd(r, 200) for r in zygc] or [MISSING]
    lines.append("【财务（归母/营收/扣非/ROE/毛利率）】")
    fin = _dict_rows(d.get("fin_abstract"))[:6]
    lines += [_jd(r, 400) for r in fin] or [MISSING]
    lines.append("【十大股东】")
    top = _dict_rows(d.get("top10"))[:5]
    lines += [_jd(r, 160) for r in top] or [MISSING]
    lines.append("【券商研报（前10）】")
    res = _dict_rows(d.get("research"))[:10]
    lines += [_jd({k: str(v)[:30] for k, v in r.items() if k in RESEARCH_KEYS}) for r in res] or [MISSING]
    lines.append("【技术信号】" + _jv(d.get("technical_signals"), 800))
    lines.append("【最新技术指标】" + _jv(latest_indicators(d), 400))
    lines.append(_momentum(rows))
    lines.append(f"【K线总数】{len(rows)}，最新3条：" + _jd(rows[-3:], 400))
    ff = _dict_rows(d.get("fund_flow"))
    if ff:
        lines.append("【资金流（近3日）】" + _jd(ff[-3:], 600))
    lines.append("【近期动态（news.items 的素材，按事件性质判断利好/利空/中性）】")
    lines += _recent_lines(d) or [MISSING]
    return "\n".join(lines)


# K 线/饼图/markLine/markPoint 注入见 app.reporting.script_builder；
# 本模块只保留 digest/分片/校验/填充率。


# ---------------------------------------------------------------- 分片提示词

ROLE = "你是A股资深卖方研究员，负责把给定的数据包写成结构化研究结论"
JSON_RULES = ("严格 JSON：只输出 JSON，无解释、无 Markdown 围栏。"
              "所有数字必须来自数据包，禁止编造；数据包里没有的字段一律写\"数据缺失\"。"
              "本次只输出指定的键，键名与层级必须与要求完全一致，数组条数必须达到下限。")


# ---- JSON 骨架（由 content_schema 机械生成：键名/类型/枚举/条数与校验器同源）----

def _item_note(sub: str) -> str:
    """数组条目子键的取值说明：枚举与分数范围只在这里讲一次，各分片骨架共用。"""
    if sub == "sentiment":
        return "利好/利空/中性（三选一，只能取其一）"
    if sub == "score":
        return "0-100整数（只写数字，禁止字符串/范围外数字）"
    if sub == "tier":
        return "核心/次要/其他（三选一）"
    if sub == "scenario":
        return "乐观/中性/悲观（三选一）"
    if sub == "status":
        return "字符串（待兑现/已兑现/未验证 其一，或其它自由文本）"
    return "字符串"


_STR_NOTES = {
    ("meta", "name"): "字符串·公司简称",
    ("meta", "code"): "字符串·6位代码",
    ("meta", "analysis_date"): "字符串·YYYY-MM-DD",
    ("hero", "price"): "字符串·现价数字写成字符串",
    ("hero", "change_pct"): "字符串·如 +1.55%",
    ("hero", "change_dir"): "涨/跌（二选一，只能取其一）",
    ("hero", "target_price"): "字符串·中期目标价",
    ("hero", "rr"): "字符串·如 2.3:1",
    ("hero", "risk_level"): "字符串·如 高/中/低",
    ("hero", "quality_score"): "数字·0-100",
    ("conclusion", "big_verdict"): "字符串·一句话核心判断",
    ("conclusion", "verdict_detail"): "字符串·详细说明段落",
    ("mission", "thesis"): "字符串·核心命题",
    ("macro", "core_contradiction"): "字符串·XX vs YY",
    ("chain", "judgment"): "字符串·关键判断",
    ("quality", "badge"): "字符串·评级徽章",
    ("elasticity", "tree_root"): "字符串·只写一段文字，禁止对象/数组",
    ("elasticity", "sensitivity"): "字符串·只写一段文字，禁止对象/数组",
    ("valuation", "methods"): "字符串·只写一段文字，禁止对象/数组",
    ("valuation", "rr"): "字符串·只写一段文字，禁止对象/数组",
    ("valuation", "fund_tech"): "字符串·只写一段文字，禁止对象/数组",
    ("tracking", "summary"): "字符串·总结段落",
    ("technical", "verdict"): "字符串·综合研判段落",
}


def _skel_leaf(blk: str, key: str, typ: Any) -> Any:
    """单个键的骨架值：字符串/数字原样注记，数组只展开一条条目形状。

    数组的条数下限不在这里讲（元素位置只放条目形状，防 LLM 照抄进一个
    字符串元素污染 news.items/quality.scores/hero.tags），统一由
    `_skeleton_text` 在骨架 JSON 之后另起一行说明 + `_shape_hints` 复述。
    """
    if isinstance(typ, dict):
        return {k2: _STR_NOTES.get((blk, key), "字符串") for k2 in typ}
    if typ is str:
        return _STR_NOTES.get((blk, key), "字符串")
    if typ == (int, float):
        return _STR_NOTES.get((blk, key), "数字")
    if typ is list:
        item_keys = ITEM_KEYS.get((blk, key))
        item: Any = ({k: _item_note(k) for k in item_keys} if item_keys
                     else "字符串条目")
        return [item]
    return "字符串"


def _skeleton_text(keys: tuple[str, ...]) -> str:
    """该分片的 JSON 骨架文本：键名/层级/类型/枚举一次给全，照抄即过校验。

    数组条数下限单独成行（不在数组元素位置），数组里永远只有条目形状。
    """
    skel = {blk: {k: _skel_leaf(blk, k, t) for k, t in SCHEMA[blk].items()}
            for blk in keys}
    text = json.dumps(skel, ensure_ascii=False, indent=1)
    needs = [f"{blk}.{k} 至少 {MIN_LEN.get((blk, k), 1)} 条"
             for blk in keys for k, t in SCHEMA[blk].items() if t is list]
    if needs:
        text += "\n（条数下限另行说明，不在数组里：不足时用【数据包】里的真实素材补足——" \
                "禁止用\"数据缺失\"占位凑数；" + "；".join(needs) + "。）"
    return text


def _shape_hints(keys: tuple[str, ...]) -> list[str]:
    """由 content_schema 的 MIN_LEN/ITEM_KEYS 生成下限与条目形状提示（schema 与 prompt 同源）。"""
    hints = [f"{blk}.{k} 至少 {n} 条" for (blk, k), n in MIN_LEN.items() if blk in keys]
    hints += [f"{blk}.{k} 每项必须含 " + "/".join(item) for (blk, k), item in ITEM_KEYS.items()
              if blk in keys]
    return hints


def shard_system(key: str) -> str:
    """某一片的 system prompt：角色 + 严格 JSON 契约 + JSON 骨架 + 下限/形状提示。"""
    parts = [ROLE + "。", JSON_RULES,
             SHARDS[key] + "。",
             "【本次只输出以下 JSON】键名/层级/类型照抄骨架，多一键少一键都不行；"
             "数据包里确实没有的**原始数据**（某项指标/金额/日期本身缺失）才写字符串 "
             "\"数据缺失\" 四个字；**判断类字段**（结论与理由、评级、目标价、盈亏比、"
             "建议仓位、事件描述、方法说明、画像叙述、跟踪总结等）必须基于数据包给出判断，"
             "一律禁止写\"数据缺失\"；数组条数不足时用数据包里的真实素材补足，"
             "禁止用\"数据缺失\"占位（字符串槽位绝不写对象/数组）：",
             _skeleton_text(SHARD_KEYS[key])]
    hints = _shape_hints(SHARD_KEYS[key])
    if hints:
        parts.append("字段下限与形状：" + "；".join(hints) + "。")
    return "\n".join(parts)


def shard_user(code: str, name: str, digest: str, date: str, key: str) -> str:
    """某一片的 user prompt：标的 + 数据包 + 本次任务（骨架在 system 里，重试只点名错键）。"""
    return (f"标的：{name}（{code}）。分析基准日：{date or MISSING}。\n\n"
            f"【数据包】\n{digest}\n\n"
            f"【本次任务】根据数据包填写这一片 JSON：{SHARDS[key]}。"
            "要求：结论先行、逻辑自洽；数字必须来自数据包；仅原始数据缺失才写\"数据缺失\"，"
            "判断类字段必须给判断；近期动态优先用【近期动态】素材（研报/高管变动/股东户数）；"
            "不要省略任何键，数组条数达到下限且不得用\"数据缺失\"占位，内容完整不截断。")


# ---------------------------------------------------------------- 分片校验/重试/降级

def _validate_shard(obj: Any, key: str) -> list[str]:
    """只校验这一片涉及的键（复用 content_schema.validate_content，过滤其它片的缺失报错）。"""
    if not isinstance(obj, dict):
        return [f"输出根节点应为 JSON 对象，实际 {type(obj).__name__}"]
    wanted = SHARD_KEYS[key]
    out = []
    for err in validate_content(obj):
        if not err.startswith("["):
            out.append(err)
            continue
        blk = err[1:].split("]")[0].split(".")[0]
        if blk in wanted:
            out.append(err)
    return out


async def _shard_retry(system: str, user: str, key: str, max_attempts: int = 3) -> tuple[dict, bool]:
    """调一次 → extract_json → 只校验该片键；失败则把具体错误追加进 prompt 重试。

    重试只点名"缺/错的键"（其它已通过的键原样保留），仍要求输出完整 JSON——
    收敛式重试，而不是整片重 roll。输出截断由 ai_provider 上限配置处理。

    返回 `(obj, failed)`：通过 → `(obj, False)`；耗尽 → `({}, True)`（上层据此降级）。
    """
    error = ""
    last_errs: list[str] = []
    for attempt in range(1, max_attempts + 1):
        if not error:
            prompt = user
        else:
            focus = "\n".join(f"- {e}" for e in last_errs[:12])
            prompt = (user + "\n\n【重试·只修下面这些键】上次输出中以下键不合格，"
                      "其它键原样保留；仍输出完整 JSON（全部键缺一不可），"
                      "没有确切数的写\"数据缺失\"：\n" + focus)
        try:
            text = await _call_llm(system, prompt, max_tokens=SHARD_MAX_TOKENS[key])
        except Exception as e:  # noqa: BLE001 — 网络/配置异常同样降级重试，日志不含密钥
            error = f"调用失败（{type(e).__name__}）"
            last_errs = [error]
            logger.warning("[分析] 分片 %s 第 %s/%s 次调用异常：%s",
                           key, attempt, max_attempts, error)
            continue
        try:
            obj = extract_json(text)
        except ValueError as e:
            error = str(e)
            last_errs = [error]
            logger.warning("[分析] 分片 %s 第 %s/%s 次 JSON 不合格：%s",
                           key, attempt, max_attempts, error)
            continue
        errs = _validate_shard(obj, key)
        if not errs:
            return obj, False
        last_errs = errs
        error = "；".join(errs[:8])
        logger.warning("[分析] 分片 %s 第 %s/%s 次结构不合格：%s",
                       key, attempt, max_attempts, error)
    logger.error("[分析] 分片 %s 重试 %s 次仍失败，降级为空片", key, max_attempts)
    return {}, True


def _deep_merge(base: dict, extra: Any) -> dict:
    """深合并：dict 递归，其余（含 list/标量）整体覆盖。"""
    if not isinstance(extra, dict):
        return base
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _force_identity(content: dict, code: str, name: str, date: str) -> None:
    """身份字段以入参为准（防 LLM 改写标的），缺基准日时用 Phase1 的 _analysis_date 补。"""
    meta = content.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        content["meta"] = meta
    if name:
        meta["name"] = str(name)
    if code:
        meta["code"] = str(code)
    if date and not meta.get("analysis_date"):
        meta["analysis_date"] = date


async def analyze_shards(code: str, name: str, blocks: Any) -> tuple[dict, list[str]]:
    """七片**串行**分析（避免限流）→ 深合并 → `(content, failed_keys)`。"""
    digest = build_digest(blocks)
    date = str(blocks.get("_analysis_date") or "") if isinstance(blocks, dict) else ""
    content: dict = {}
    failed: list[str] = []
    for key in SHARD_ORDER:
        t0 = time.perf_counter()
        obj, bad = await _shard_retry(shard_system(key),
                                      shard_user(code, name, digest, date, key), key)
        logger.info("[分析] 分片 %s %s，耗时 %.1fs",
                    key, "降级" if bad else "完成", time.perf_counter() - t0)
        content = _deep_merge(content, obj)
        if bad:
            failed.append(key)
    _force_identity(content, code, name, date)
    logger.info("[分析] 完成：分片 %s/%s 通过，填充率 %.0f%%",
                len(SHARD_ORDER) - len(failed), len(SHARD_ORDER), fill_rate(content) * 100)
    return content, failed


# ---------------------------------------------------------------- 质量阈值

def _is_filled(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, bool):
        return True
    if isinstance(v, (int, float)):
        return math.isfinite(float(v))
    s = str(v).strip()
    return bool(s) and s != MISSING


FILL_CAP_MISSING_BLOCK = 0.4  # 任一整块缺失（整片降级）→ 结构性降级：得分锁死 ≤0.4，
# 其它块写得再满也拉不回 0.5 门限——缺块的报告章只能是"数据缺失"，只能是降级品。


def _expected_slots() -> dict[str, list[tuple]]:
    """schema 期望槽位：标量/嵌套子键各计 1 叶；数组按 MIN_LEN（缺省 1）× 条目键数展开。"""
    out: dict[str, list[tuple]] = {}
    for blk, spec in SCHEMA.items():
        slots: list[tuple] = []
        for key, typ in spec.items():
            if isinstance(typ, dict):
                slots.extend((key, sub, 1, None, False) for sub in typ)
            elif typ is list:
                slots.append((key, None, MIN_LEN.get((blk, key), 1),
                              ITEM_KEYS.get((blk, key)), True))
            else:
                slots.append((key, None, 1, None, False))
        out[blk] = slots
    return out


_EXPECTED_SLOTS = _expected_slots()


def fill_rate(content: Any) -> float:
    """填充率（0.0-1.0）：按 schema **期望叶集**统计（缺席的块/键计入分母）。

    - 空 content → 0.0；任一整块缺席（缺键/非对象/空对象）→ 得分封顶 0.4
      （缺一片的分片永远过不了 0.5 门限，report_pipeline 只能 exit 2/1，绝不 exit 0）；
    - 数组只看前 MIN_LEN 条（多写不扣分）；条目缺键/超范围值一律算未填充。
    """
    c = content if isinstance(content, dict) else {}
    total = filled = 0
    missing_block = False
    for blk, slots in _EXPECTED_SLOTS.items():
        b = c.get(blk)
        if not isinstance(b, dict) or not b:
            missing_block = True
        for key, sub, n, ik, is_list in slots:
            if not is_list:
                v = None
                if isinstance(b, dict):
                    if sub is None:
                        v = b.get(key)
                    else:
                        d = b.get(key)
                        v = d.get(sub) if isinstance(d, dict) else None
                total += 1
                filled += 1 if _is_filled(v) else 0
            else:
                lst = b.get(key) if isinstance(b, dict) else None
                if not isinstance(lst, list):
                    lst = []
                for i in range(n):
                    if i >= len(lst):
                        total += len(ik) if ik else 1
                        continue
                    item = lst[i]
                    if ik:
                        if not isinstance(item, dict):
                            total += len(ik)
                            continue
                        for k in ik:
                            total += 1
                            filled += 1 if _is_filled(item.get(k)) else 0
                    else:
                        total += 1
                        filled += 1 if _is_filled(item) else 0
    rate = filled / total if total else 0.0
    if missing_block:
        rate = min(rate, FILL_CAP_MISSING_BLOCK)
    return round(rate, 4)