"""研报 K 线/指标行工具（digest 与脚本注入共用）。

从 stock_ai 的 analyze_api 拆出，避免 services 层与渲染包重复实现同一份清洗逻辑。
"""
from __future__ import annotations

import math
from typing import Any

KLINE_LIMIT = 120  # 与模板 K 线窗口一致


def _num(v: Any) -> float | None:
    """数值归一化：数字/数值字符串 → float；None/bool/NaN/±Inf/非数值 → None（绝不抛错）。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def kline_rows(blocks: Any, limit: int = KLINE_LIMIT) -> list[list]:
    """K 线清洗：[date, open, high, low, close, volume]；缺字段/非有限的畸形行直接跳过。"""
    src = blocks.get("kline_daily") if isinstance(blocks, dict) else None
    if not isinstance(src, list):
        return []
    rows: list[list] = []
    for r in src:
        if not isinstance(r, dict):
            continue
        date = str(r.get("date") or "").strip()
        op, hi, lo, cl = (_num(r.get(k)) for k in ("open", "high", "low", "close"))
        if not date or op is None or hi is None or lo is None or cl is None:
            continue
        vol = _num(r.get("volume"))
        rows.append([date[:10], op, hi, lo, cl, 0 if vol is None else vol])
    return rows[-limit:] if limit and limit > 0 else rows


def latest_indicators(blocks: Any) -> dict:
    """最新一行技术指标；`[None]` 之类非 dict 行跳过，全非法时降级为 {}。"""
    src = blocks.get("technical_indicators") if isinstance(blocks, dict) else None
    if not isinstance(src, list):
        return {}
    for row in reversed(src):
        if isinstance(row, dict):
            return row
    return {}
