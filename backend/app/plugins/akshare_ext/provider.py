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
