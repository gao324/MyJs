"""AI 个股分析入口 — 按开关分发 DSA 问股 agent / 旧四维分析。

开关: ``settings.stock_analysis_engine`` (env ``STOCK_ANALYSIS_ENGINE``)
  - ``dsa``    (默认) → ``stock_analyzer_dsa``: DSA 问股 agent(需 ``DSA_VENDOR_DIR``)
  - ``legacy``        → ``stock_analyzer_legacy``: 原「技术/基本/财务/消息面」四维流式分析

两种实现共用同一 NDJSON 协议(meta / delta / error / done), 前端解析无差异。
引擎值非法或所选引擎不可用时产出 ``error`` 事件(fail-closed), 不静默切换口径。
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

ENGINE_DSA = "dsa"
ENGINE_LEGACY = "legacy"
VALID_ENGINES = (ENGINE_DSA, ENGINE_LEGACY)


def resolve_engine() -> str:
    """解析当前生效的分析引擎; 非法配置直接报错(不静默回退)。"""
    value = str(getattr(settings, "stock_analysis_engine", ENGINE_DSA) or "").strip().lower()
    if value not in VALID_ENGINES:
        raise ValueError(
            f"STOCK_ANALYSIS_ENGINE={value!r} 非法, 只能是 {' 或 '.join(VALID_ENGINES)}"
        )
    return value


async def analyze_stock_stream(
    repo,
    data_dir: Path,
    symbol: str,
    focus: str = "",
) -> AsyncIterator[str]:
    """按 ``settings.stock_analysis_engine`` 分发到对应实现。"""
    try:
        engine = resolve_engine()
    except ValueError as exc:
        logger.error("stock analysis engine misconfigured: %s", exc)
        yield json.dumps({"type": "error", "message": str(exc)}, ensure_ascii=False)
        return

    if engine == ENGINE_LEGACY:
        from app.services import stock_analyzer_legacy

        async for chunk in stock_analyzer_legacy.analyze_stock_stream(
            repo, data_dir, symbol, focus
        ):
            yield chunk
        return

    try:
        from app.services import stock_analyzer_dsa
    except Exception as exc:
        # 引擎不可用时给前端明确报错, 不静默换实现
        logger.exception("DSA stock analyzer unavailable: %s", exc)
        yield json.dumps({
            "type": "error",
            "message": f"DSA 问股 agent 不可用: {exc}; 可设 STOCK_ANALYSIS_ENGINE=legacy 回退旧四维分析",
        }, ensure_ascii=False)
        return

    async for chunk in stock_analyzer_dsa.analyze_stock_stream(repo, data_dir, symbol, focus):
        yield chunk
