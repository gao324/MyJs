"""GM 股息率截面数据 — 同步与读取 (策略结果列表「股息率」列的数据底座)。

数据契约: data/dividend_yields/part.parquet
  列: symbol(如 600519.SH), trade_date(截面交易日), dy_ttm(股息率滚动12月, %),
      dy_lfy(股息率上一财年, %) —— GM 原始量纲为 %, 消费方 /100 转小数。

来源: GM `stk_get_daily_valuation_pt` (免费函数, 多标的单日截面), 经 gm 插件
桥接子进程拉取, 不进入主 venv (与 gm provider 其他数据集同机制)。
GM 未配置/不可用时同步跳过、读取返回空 dict —— 策略列表股息率 fail-closed 显示「—」。
全量覆盖落盘 (快照语义: 展示口径, 与策略结果 as_of 无关, 不参与回测)。
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

import polars as pl

from app.services.fs_utils import atomic_write_parquet

logger = logging.getLogger(__name__)

# 单次截面请求的标的数上限 (保守值; GM 该函数为多标的一次调用)
_VALUATION_CHUNK = 2000

# GM 估值字段: dy_ttm 股息率(滚动12月TTM) / dy_lfy 股息率(上一财年LFY)
FIELDS = "dy_ttm,dy_lfy"


def _get_symbols(data_dir: Path) -> list[str]:
    """从 instruments 维表取标的列表。"""
    inst_path = data_dir / "instruments" / "instruments.parquet"
    if not inst_path.exists():
        return []
    try:
        df = pl.read_parquet(inst_path, columns=["symbol"])
        return df["symbol"].to_list()
    except Exception as e:  # noqa: BLE001
        logger.warning("读取 instruments 失败: %s", e)
        return []


def gm_available() -> bool:
    """GM 估值能力可用性: token 已配置且独立 venv 就绪。轻量检查, 不安装。"""
    try:
        from app.plugins.gm.client import get_api_key, venv_ready
        return bool(get_api_key()) and venv_ready()
    except Exception:  # noqa: BLE001
        return False


def sync_dividend_yields(data_dir: Path) -> dict:
    """GM 全市场股息率截面同步 → data/dividend_yields/part.parquet。

    返回 {"rows": int, "skipped"?: str, "errors"?: [str]}; GM 未配置/无标的时
    skipped 说明原因, 不落盘 (保留旧快照)。
    """
    symbols = _get_symbols(data_dir)
    if not symbols:
        return {"rows": 0, "skipped": "no symbols"}
    if not gm_available():
        return {"rows": 0, "skipped": "gm unavailable (token 未配置或数据环境未就绪)"}

    try:
        from app.data_providers import custom as custom_sources
        provider = custom_sources.get_provider("gm")
    except Exception as e:  # noqa: BLE001
        return {"rows": 0, "skipped": f"gm provider 不可用: {e}"}

    frames: list[pl.DataFrame] = []
    errors: list[str] = []
    for i in range(0, len(symbols), _VALUATION_CHUNK):
        chunk = symbols[i:i + _VALUATION_CHUNK]
        try:
            df = provider.get_daily_valuation(chunk, fields=FIELDS)
            if not df.is_empty():
                frames.append(df)
        except Exception as e:  # noqa: BLE001 — 单段失败不阻断其余段
            errors.append(str(e)[:160])
            logger.warning("GM 股息率截面拉取失败 (段 %d): %s", i // _VALUATION_CHUNK, e)

    if not frames:
        logger.warning("GM 股息率同步无数据: %s", "; ".join(errors) or "空响应")
        return {"rows": 0, "errors": errors}

    merged = pl.concat(frames, how="diagonal_relaxed")
    if "symbol" in merged.columns:
        merged = merged.filter(pl.col("symbol").is_not_null()).unique(subset=["symbol"], keep="last")
    out_dir = data_dir / "dividend_yields"
    out_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_parquet(merged, out_dir / "part.parquet")
    logger.info("GM 股息率同步完成: %d 标的", merged.height)
    return {"rows": merged.height, "errors": errors}


def read_dividend_yields(data_dir: Path) -> pl.DataFrame:
    """读取本地股息率快照; 表缺失/损坏 → 空 DataFrame (不抛)。"""
    path = data_dir / "dividend_yields" / "part.parquet"
    if not path.exists():
        return pl.DataFrame()
    try:
        return pl.read_parquet(path)
    except Exception as e:  # noqa: BLE001
        logger.warning("读取 dividend_yields 失败: %s", e)
        return pl.DataFrame()


def yield_maps(data_dir: Path) -> dict[str, dict[str, float]]:
    """快照 → {"lfly": {symbol: 小数}, "ttm": {symbol: 小数}}。

    GM 量纲 % → /100 转小数 (与表内 momentum_*/pct 小数制一致, CONTRIBUTING §3.1);
    非有限值/负值丢弃; 表缺失 → 两个空 dict (消费方显示「—」)。
    """
    df = read_dividend_yields(data_dir)
    out: dict[str, dict[str, float]] = {"lfly": {}, "ttm": {}}
    if df.is_empty():
        return out
    for row in df.to_dicts():
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        for key, col in (("lfly", "dy_lfy"), ("ttm", "dy_ttm")):
            value = row.get(col)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if not math.isfinite(float(value)) or value < 0:
                continue
            out[key][symbol] = float(value) / 100.0
    return out
