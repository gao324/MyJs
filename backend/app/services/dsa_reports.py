"""DSA 问股报告服务 - 在 TSP 进程内跑 DSA 原生问股 agent（vendor 引用方式接入）。

与 DSA 原生问股同口径：调 executor.chat()（free-form 回答，不做 dashboard 解析），
agent 产出的 markdown 原样透传给前端 NDJSON delta 流。
注意：不要改走 executor.run()——dashboard JSON 再手写渲染会丢嵌套结构，
英文裸 key 会直接漏到前端。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator

from app.services import dsa_bridge

logger = logging.getLogger(__name__)


async def dsa_analyze_stream(symbol: str, focus: str = "", *, timeout_s: int = 660) -> AsyncIterator[dict]:
    """跑 DSA 原生问股（chat 路径），产出 markdown 事件流。

    yield: {"type":"delta","content":markdown} / {"type":"error","message"} / {"type":"done"}
    DSA chat() 是同步返回（无 token 级流式），故整段 markdown 一次性吐出。
    每次分析用全新 session，避免不同标的/不同次分析的上下文串扰。
    """
    try:
        executor = dsa_bridge.get_agent_executor()
        message = f"请分析股票 {symbol}"
        focus_text = (focus or "").strip()
        if focus_text:
            message += f"，关注点：{focus_text}"
        # 全新 session：单次问股无追问 UI，不复用历史，避免串扰
        session_id = f"tsp-stock-{symbol}-{uuid.uuid4().hex[:8]}"
        context = {"stock_code": symbol, "report_language": "zh"}
        result = await asyncio.wait_for(
            asyncio.to_thread(executor.chat, message, session_id, context=context),
            timeout=timeout_s,
        )
    except TimeoutError:
        logger.error("DSA agent timed out for %s", symbol)
        yield {"type": "error", "message": f"DSA 问股 agent 超时（>{timeout_s}s），请重试或稍后再试"}
        return
    except Exception as exc:
        logger.exception("DSA agent failed for %s: %s", symbol, exc)
        yield {"type": "error", "message": f"DSA 问股 agent 执行失败: {exc}"}
        return

    if not result.success:
        detail = result.error or "unknown error"
        logger.warning("DSA agent result failed for %s: %s", symbol, detail)
        yield {"type": "error", "message": f"DSA 问股 agent 未产出报告: {detail}"}
        return

    content = (result.content or "").strip()
    if not content:
        yield {"type": "error", "message": "DSA 问股 agent 返回空报告"}
        return
    yield {"type": "delta", "content": content}
    yield {"type": "done"}
