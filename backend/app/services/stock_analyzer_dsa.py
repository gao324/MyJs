"""AI 个股分析（DSA 路径）— 关键价位 + DSA 问股 agent 透传。

移植自 stock checkout 的 app/services/stock_analyzer.py:
  读一只股票的 K 线算出关键价位 → yield meta(供前端图表回放) → 跑 DSA 原生问股 agent →
  markdown 事件流透传给前端。

与参考实现的唯一差异: ``_load_kline`` 使用 ``cn_today()``(北京时区)而非 ``date.today()``,
避免服务器时区影响交易日归属 (CONTRIBUTING §3.3)。与旧四维实现
(``stock_analyzer_legacy``)共用同一 NDJSON 协议, 由 ``stock_analyzer`` 按
``settings.stock_analysis_engine`` 分发。

不知道: HTTP、前端、配置持久化。
"""
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import polars as pl

from app.indicators.levels import compute_levels, summarize_levels
from app.market_time import cn_today

logger = logging.getLogger(__name__)

# 取最近多少根日 K(关键价位计算样本)
_KLINE_WINDOW = 90


def _load_kline(repo, symbol: str) -> pl.DataFrame:
    """读取该标的最近 N 根日 K(已含技术指标 / 信号)。

    repo: KlineRepository;走内存缓存,性能可控。
    """
    end = cn_today()
    start = end - timedelta(days=_KLINE_WINDOW * 2)  # 多取一些保证交易日够
    # 按资产类型分流: ETF/指数走独立 enriched 存储
    df = repo.get_daily_asset(repo.resolve_asset_type(symbol), symbol, start, end)
    if df.is_empty():
        return df
    return df.tail(_KLINE_WINDOW)


async def analyze_stock_stream(
    repo,
    data_dir: Path,
    symbol: str,
    focus: str = "",
) -> AsyncIterator[str]:
    """流式个股分析:yield 出每个 NDJSON 事件(DSA 问股 agent 唯一路径)。

    协议(前端解析无差异):
      {"type":"meta","symbol","summary","levels","close"}  数据 + 价位摘要
      {"type":"delta","content":"..."}                     agent 产出的 markdown
      {"type":"error","message":"..."}
      {"type":"done"}

    data_dir 保留入参(调用方按位置传参), 当前 agent 路径不再直接读取财务表。
    """
    # 1. 加载 K 线
    df = _load_kline(repo, symbol)
    if df.is_empty():
        yield json.dumps({
            "type": "error",
            "message": f"标的 {symbol} 暂无日 K 数据,请先同步",
        }, ensure_ascii=False)
        return

    # 2. 价位计算(基于 K 线，供图表回放)
    levels = compute_levels(df)
    close = float(df.tail(1)["close"][0]) if "close" in df.columns else None

    # 3. meta
    yield json.dumps({
        "type": "meta",
        "symbol": symbol,
        "summary": summarize_levels(levels, close),
        "levels": levels,
        "close": close,
    }, ensure_ascii=False)

    # 4. DSA 原生问股（K 线与 levels 图表回放不受影响）
    from app.services import dsa_reports
    async for ev in dsa_reports.dsa_analyze_stream(symbol, focus):
        yield json.dumps(ev, ensure_ascii=False)
