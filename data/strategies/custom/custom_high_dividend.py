"""高股息筛选 — 连续 N 年股息率 ≥ 阈值。

口径 (2026-10 经 tushare / 掘金 / 东财三源逐笔复核后固化, 修正了来源实现的两处缺陷):

    股息率(Y) = 除权日落在 Y 年的每股税前现金分红合计 ÷ Y 年最后交易日不复权收盘价 × 100

  - 分子用本地除权除息事件表 (adj_factor, 单一存储) 按除权日归属自然年, 不做跨源求和。
    旧实现曾因 tushare 同一事件返回多行「实施」而把分子翻倍 (南京银行 2024:
    真实 8.29% 被算成 16.58%, 全市场 236 只入选股中 28 只受污染)。
  - 分母用 kline_daily 的不复权 close; 不用前复权价 —— 前复权锚点随取数来源变化
    (tushare 锚查询区间末日、GM 锚最新日), 会让历史年份股息率虚高。
  - 连续 N 个完整年度全部 ≥ min_yield 才命中。完整年度指 as_of 所在年 y 之前的
    [y-N, y-1]; 同一自然年内命中集合不变 (目标年度只取决于年份)。
  - 数据缺失 fail-closed: 某年无年末价、无现金分红或分母非正 → 该年不达标。
    本地历史未回补时不会静默使用残缺数据, 只会不命中。

无未来数据: 目标年度恒为 as_of 所在年之前的完整年度, 其分红事件与年末收盘价
都早于 as_of; 当年的分红不进分子。因此跨年回测窗口也能逐日复现。

数据前提: 本地日K与除权事件需覆盖 N+ 年 (数据页「日K卡片 → 向前扩展历史」)。

参数:
    min_yield  股息率下限(%)   默认 3.0
    years      连续达标年数    默认 3

输出附加列:
    div_yield_l1..lN  最近第 1..N 个完整年度的股息率 (l1 = as_of 前一年)
    div_yield_avg     上述年度的平均股息率 (评分与排序依据)
    div_yield_years   年度标签, 如 "2025|2024|2023"
"""

from __future__ import annotations

from datetime import date

import polars as pl

# 策略 import 白名单只放行该模块本身, 且用模块别名引用 (便于测试替换读取函数)
import app.strategy.market_data as market_data

META = {
    "id": "custom_high_dividend",
    "name": "高股息筛选",
    "description": "连续 N 年股息率(当年除权现金分红 ÷ 当年末不复权收盘价)≥ 阈值的股票",
    "tags": ["高股息", "红利", "基本面"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {"exclude_st": True},
    "params": [
        {"id": "min_yield", "label": "股息率下限(%)", "type": "float",
         "default": 3.0, "min": 0.0, "max": 20.0, "step": 0.1},
        {"id": "years", "label": "连续达标年数", "type": "int",
         "default": 3, "min": 1, "max": 5, "step": 1},
    ],
    # 三年股息率均值参与评分 (min-max 归一化 → 0~100), 结果按 score 降序
    "scoring": {"div_yield_avg": 1.0},
    "descending": True,
    "limit": 100,
}

EXECUTION_BACKEND = "python_history_legacy"

# python_history_legacy 必须拿到历史窗口 (引擎在 history 为空时报错); 本策略的
# 多年分红数据自行经 market_data 读取, 窗口只用于确定候选标的与 as_of。
LOOKBACK_DAYS = 2
REQUIRED_FEATURES = {"symbol", "date"}

RULES = """
1. 分子 = 除权日落在该自然年的每股税前现金分红合计 (只计现金分红, 不做跨源求和)
2. 分母 = 该年最后交易日的不复权收盘价 (本地日K)
3. 连续 years 个完整年度全部 ≥ min_yield(%) 才入选; 任一缺失即不达标
"""


def filter_history(df: pl.DataFrame, params: dict) -> pl.DataFrame:
    """返回窗口内全部命中行; 实盘扫描引擎会截取 as_of 当日, 回测使用全区间。"""
    if df.is_empty() or "symbol" not in df.columns or "date" not in df.columns:
        return df.head(0)

    min_yield = _to_float(params.get("min_yield"), 3.0)
    years = max(1, min(5, _to_int(params.get("years"), 3)))

    symbols = df["symbol"].cast(pl.Utf8).unique().to_list()
    window_years = sorted(
        {d.year for d in df["date"].unique().to_list() if d is not None}
    )
    if not symbols or not window_years:
        return df.head(0)

    target_years = sorted({t for y in window_years for t in range(y - years, y)})
    start = date(target_years[0], 1, 1)
    end = date(target_years[-1], 12, 31)

    events = market_data.get_dividend_events(symbols=symbols, start=start, end=end)
    closes = market_data.get_raw_daily_close(symbols=symbols, start=start, end=end)

    cand = _yearly_yields(
        symbols, target_years,
        _dividend_per_year(events, target_years),
        _year_end_close(closes, target_years),
        min_yield,
    )

    frames = [
        hit for hit in (_group_hits(df, cand, y, years) for y in window_years)
        if hit is not None and not hit.is_empty()
    ]
    if not frames:
        return df.head(0)
    return pl.concat(frames, how="vertical_relaxed").sort(["date", "symbol"])


# ── 取数解析 ────────────────────────────────────────────────────────


def _dividend_per_year(events, target_years) -> pl.DataFrame:
    """除权年 → 每股现金分红合计 [symbol, _y, dps]。

    事件表可为空/缺列/日期类型异常 (存量三列库、数据源不提供明细) —— 一律返回
    空表, 由 _yearly_yields 的交叉网格把缺失年度判为不达标 (fail-closed)。
    """
    schema = {"symbol": pl.Utf8, "_y": pl.Int32, "dps": pl.Float64}
    if (
        events is None
        or events.is_empty()
        or "dividend" not in events.columns
        or "trade_date" not in events.columns
    ):
        return pl.DataFrame(schema=schema)
    try:
        return (
            events.select("symbol", "trade_date", "dividend")
            .filter(pl.col("dividend").is_not_null() & (pl.col("dividend") > 0))
            .with_columns(pl.col("trade_date").dt.year().cast(pl.Int32).alias("_y"))
            .filter(pl.col("_y").is_in(target_years))
            .group_by(["symbol", "_y"])
            .agg(pl.col("dividend").sum().alias("dps"))
        )
    except Exception:  # noqa: BLE001 — 口径不明时宁可判不达标
        return pl.DataFrame(schema=schema)


def _year_end_close(closes, target_years) -> pl.DataFrame:
    """除权年 → 该年最后交易日不复权收盘价 [symbol, _y, ye_close]。"""
    schema = {"symbol": pl.Utf8, "_y": pl.Int32, "ye_close": pl.Float64}
    if (
        closes is None
        or closes.is_empty()
        or "close" not in closes.columns
        or "date" not in closes.columns
    ):
        return pl.DataFrame(schema=schema)
    try:
        return (
            closes.select("symbol", "date", "close")
            .filter(pl.col("close").is_not_null() & (pl.col("close") > 0))
            .with_columns(pl.col("date").dt.year().cast(pl.Int32).alias("_y"))
            .filter(pl.col("_y").is_in(target_years))
            .sort(["symbol", "date"])
            .group_by(["symbol", "_y"])
            .agg(pl.col("close").last().alias("ye_close"))
        )
    except Exception:  # noqa: BLE001
        return pl.DataFrame(schema=schema)


def _yearly_yields(symbols, target_years, dps, ye, min_yield) -> pl.DataFrame:
    """(symbol × 目标年度) 全交叉 → [symbol, _y, dps, ye_close, _yield, _ok]。

    先铺全交叉网格再左连: 缺分红/缺年末价的年度显式落成 _ok=False,
    不会因"该年度没有行"被 all() 误判为通过。
    """
    grid = pl.DataFrame({"symbol": symbols}).join(
        pl.DataFrame({"_y": target_years}), how="cross"
    )
    return (
        grid.join(dps, on=["symbol", "_y"], how="left")
        .join(ye, on=["symbol", "_y"], how="left")
        .with_columns(pl.col("dps").fill_null(0.0))
        .with_columns((pl.col("dps") / pl.col("ye_close") * 100.0).alias("_yield"))
        .with_columns(
            (
                pl.col("ye_close").is_not_null()
                & (pl.col("ye_close") > 0)
                & (pl.col("dps") > 0)
                & (pl.col("_yield") >= min_yield)
            ).fill_null(False).alias("_ok")
        )
    )


def _group_hits(df: pl.DataFrame, cand: pl.DataFrame, window_year: int,
                years: int) -> pl.DataFrame | None:
    """某个窗口年 (as_of 所在自然年) 的命中行。

    该年内每一天共用同一组目标年度, 命中集合一致 → 回测逐日可复现。
    目标年度按最近在前编号: l1 = window_year-1, l2 = window_year-2, ...
    """
    targets = list(range(window_year - years, window_year))   # 升序
    targets_desc = list(reversed(targets))                    # 最近年度在前
    sub = cand.filter(pl.col("_y").is_in(targets))

    exprs = [pl.col("_ok").all().alias("_qualify")]
    for idx, t in enumerate(targets_desc, start=1):
        exprs.append(
            pl.col("_yield").filter(pl.col("_y") == t).first().alias(f"div_yield_l{idx}")
        )
    annotation = sub.group_by("symbol").agg(exprs).with_columns(
        pl.mean_horizontal(
            [pl.col(f"div_yield_l{i}") for i in range(1, years + 1)]
        ).alias("div_yield_avg"),
        pl.lit("|".join(str(t) for t in targets_desc)).alias("div_yield_years"),
    )
    hits = annotation.filter(pl.col("_qualify")).drop("_qualify")
    if hits.is_empty():
        return None

    rows = df.filter(pl.col("date").dt.year() == window_year)
    if rows.is_empty():
        return None
    return rows.join(hits, on="symbol", how="inner")


def _to_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
