"""研报块收集 — akshare_ext 同源取数 + 技术指标计算 + 当日缓存。

与 stock_ai Phase 1 的 data_{code}.json 块名逐一对齐(26 个取数块 + 2 个计算块),
保证 digest / 渲染 / 一致性验收可比。取数走 provider 的**非路由**方法(先例:
dragon_tiger 直接经 custom_sources 取 fuyao), 锁定与 stock_ai 同源。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from app.config import settings
from app.market_time import cn_now
from app.reporting import technical_indicators as ti

logger = logging.getLogger(__name__)

PROVIDER_NAME = "akshare_ext"

# 与 stock_ai stock_full_report.py 的 submit() 键名一致(26 块)
BLOCK_NAMES: tuple[str, ...] = (
    "basic_info", "spot", "share_structure", "zygc", "kline_daily",
    "fund_flow", "lhb", "fin_abstract", "fin_indicator_ths", "balance_sheet",
    "income_statement", "cashflow", "yjyg", "yjkb", "top10", "gdhs",
    "share_hold_change", "dividend", "share_alloc", "release", "notice", "news",
    "research", "recommend", "fund_hold", "margin",
)
# 本地计算块(不做取数, 由日K推导)
COMPUTED_BLOCKS: tuple[str, ...] = ("technical_indicators", "technical_signals")
ALL_BLOCK_NAMES: tuple[str, ...] = BLOCK_NAMES + COMPUTED_BLOCKS
# stock_ai Phase 1 采集但 digest/渲染均不消费, 本管线不采集(对比脚本同样跳过)
SKIPPED_BLOCKS: tuple[str, ...] = ("kline_minute",)

_SYMBOL_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")


class ReportDataUnavailableError(ValueError):
    """数据层不可用(fail-closed): provider 缺失或日K为空。"""


def _validate_symbol(symbol: str) -> str:
    """归一化并校验标的(6 位代码 + 交易所后缀); 非法即 fail-closed。"""
    text = str(symbol or "").strip().upper()
    if not _SYMBOL_RE.match(text):
        raise ValueError(f"symbol 需为 6 位代码 + 交易所后缀(如 600519.SH), 实际 {symbol!r}")
    return text


def _provider():
    """取 akshare_ext 插件实例; 不可用时返回 None(调用方 fail-closed)。"""
    try:
        from app.data_providers.custom import loader as custom_loader
    except Exception:  # noqa: BLE001
        logger.warning("[report-data] 数据源加载器不可用", exc_info=True)
        return None
    try:
        if not custom_loader.provider_has_dataset(PROVIDER_NAME, "research"):
            return None
        return custom_loader.get_provider(PROVIDER_NAME)
    except Exception:  # noqa: BLE001
        logger.warning("[report-data] akshare_ext 不可用", exc_info=True)
        return None


def cache_dir(symbol: str) -> Path:
    safe = str(symbol).replace("/", "_").replace("\\", "_")
    return settings.data_dir / "report_cache" / safe


def cache_path(symbol: str, day: str = "") -> Path:
    stamp = day or cn_now().strftime("%Y%m%d")
    return cache_dir(symbol) / f"{stamp}.json"


def _indicators(blocks: dict) -> None:
    """日K → MACD/KDJ/RSI/BOLL 指标块与信号块(stock_ai 同款 pandas 实现)。"""
    rows = blocks.get("kline_daily") or []
    if not rows:
        blocks["technical_indicators"] = []
        blocks["technical_signals"] = {}
        return
    import pandas as pd

    df = pd.DataFrame(rows)
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    enriched = ti.calculate_all_indicators(df)
    blocks["technical_indicators"] = ti.format_indicators_for_json(enriched, max_rows=120)
    blocks["technical_signals"] = ti.get_latest_signals(enriched)


def collect_blocks(symbol: str, *, force: bool = False) -> tuple[dict, list[str]]:
    """采集全部块; 命中当日缓存且未 force 时直接复用。返回 (blocks, missing)。

    missing = 取数后仍为空的块(akshare 无数据/接口异常降级), 供任务状态展示;
    日K为空时直接抛 ReportDataUnavailable(没有 K 线无法出图与算指标)。
    """
    symbol = _validate_symbol(symbol)
    path = cache_path(symbol)
    if path.exists() and not force:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload["blocks"], list(payload.get("missing") or [])
        except (OSError, ValueError, KeyError, TypeError):
            logger.warning("[report-data] 缓存损坏, 重新采集: %s", path)

    provider = _provider()
    if provider is None:
        raise ReportDataUnavailableError(
            "akshare_ext 数据源不可用: 请安装 akshare 并在数据源设置中启用该插件")

    blocks: dict = {}
    blocks.update(provider.get_company_info(symbol))
    blocks.update(provider.get_shareholders(symbol))
    blocks.update(provider.get_research(symbol))
    blocks.update(provider.get_fund_flow(symbol))
    blocks.update(provider.get_report_financials(symbol))
    blocks["kline_daily"] = provider.get_report_kline(symbol)
    blocks["spot"] = provider.get_report_spot(symbol)
    if not blocks.get("kline_daily"):
        raise ReportDataUnavailable(f"标的 {symbol} 日K为空, 无法生成研报")

    _indicators(blocks)
    for name in BLOCK_NAMES:
        blocks.setdefault(name, [])
    missing = [name for name in BLOCK_NAMES if not blocks.get(name)]
    blocks["_analysis_date"] = cn_now().strftime("%Y-%m-%d")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"blocks": blocks, "missing": missing},
                               ensure_ascii=False, default=str), encoding="utf-8")
    return blocks, missing
