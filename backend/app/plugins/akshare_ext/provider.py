"""AKShare 研报扩展数据源 — 研报管线专用数据集(非 TickFlow 能力)。

数据集契约(本仓库定义, 消费方 = 个股研报管线):
- research / shareholders / fund_flow / company_info:
  返回 {块名: [记录, ...]}, 记录列名保持 akshare 原始中文列(与 stock_ai
  Phase 1 落盘块逐列对齐, 保证报告一致性); 缺失块返回 [], 不抛异常。
- 数值一律经 _clean 清洗(非有限值 → None), JSON 缓存内无 NaN/Infinity。
"""
from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

NAME = "akshare_ext"
DATASETS = ("research", "shareholders", "fund_flow", "company_info")


def availability() -> tuple[bool, str]:
    """返回 (是否可用, 原因)。不抛异常。"""
    try:
        import akshare  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, f"未安装 akshare({type(exc).__name__}), 可执行 pip install akshare"
    return True, "ok"


def _clean(value: Any) -> Any:
    """非有限数值 → None; 其余原样(供 JSON 落盘与 digest 消费)。"""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value if math.isfinite(float(value)) else None
    return value


def _records(frame: Any) -> list[dict]:
    """akshare DataFrame → 清洗后的记录列表; 结构漂移(非 DataFrame)打告警返回 []。"""
    if frame is None:
        return []
    to_dict = getattr(frame, "to_dict", None)
    if not callable(to_dict):
        logger.warning("[akshare_ext] 返回结构不是 DataFrame(%s), 已跳过", type(frame).__name__)
        return []
    try:
        rows = frame.to_dict(orient="records")
    except Exception:  # noqa: BLE001
        logger.warning("[akshare_ext] DataFrame 转记录失败", exc_info=True)
        return []
    return [{k: _clean(v) for k, v in row.items()} for row in rows]


def _import_ak():
    """惰性导入 akshare(未安装时由 availability 提前拦截)。"""
    import akshare

    return akshare


def _split_symbol(symbol: str) -> tuple[str, str]:
    """600519.SH → ("600519", "sh"); 非法输入抛 ValueError(fail-closed)。"""
    text = str(symbol or "").strip().upper()
    if "." not in text:
        raise ValueError(f"symbol 需带交易所后缀(如 600519.SH), 实际 {symbol!r}")
    code, _, suffix = text.partition(".")
    if len(code) != 6 or suffix not in ("SH", "SZ", "BJ"):
        raise ValueError(f"symbol 格式非法: {symbol!r}")
    return code, suffix.lower()


def _recent_periods(count: int) -> list[str]:
    """最近 count 个报告期(YYYYMMDD, 季度末), 倒序; 供按报告期查询的接口。"""
    import datetime as dt

    today = dt.date.today()
    quarter_ends: list[dt.date] = []
    year = today.year
    while len(quarter_ends) < count + 4:
        for month, day in ((12, 31), (9, 30), (6, 30), (3, 31)):
            q = dt.date(year, month, day)
            if q < today:
                quarter_ends.append(q)
        year -= 1
    return [d.strftime("%Y%m%d") for d in sorted(set(quarter_ends), reverse=True)[:count]]


def _safe(fn, label: str) -> Any:
    """接口异常降级为空结果 + 告警(绝不中断整包采集)。"""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[akshare_ext] %s 采集失败(%s), 该块置空", label, type(exc).__name__)
        return []


class _Config:
    """最小 provider 配置(需有 .datasets 属性)。"""

    def __init__(self) -> None:
        self.datasets = {name: True for name in DATASETS}


class AkshareExtProvider:
    name = NAME
    builtin = True

    def __init__(self) -> None:
        self.config = _Config()

    def close(self) -> None:
        """无长连接资源, 空实现。"""

    def get_research(self, symbol: str) -> dict[str, list[dict]]:
        """研报 / 机构评级 / 基金持仓三块。symbol 形如 600519.SH。"""
        ak = _import_ak()
        code, _market = _split_symbol(symbol)
        blocks: dict[str, list[dict]] = {"research": [], "recommend": [], "fund_hold": []}
        blocks["research"] = _safe(
            lambda: _records(ak.stock_research_report_em(symbol=code)), "research")
        blocks["recommend"] = _safe(
            lambda: _records(ak.stock_institute_recommend(symbol="股票综合评级")), "recommend")
        for period in _recent_periods(2):
            rows = _safe(lambda p=period: _records(
                ak.stock_report_fund_hold_detail(symbol=code, date=p)), "fund_hold")
            if rows:
                blocks["fund_hold"] = rows
                break
        return blocks
