"""通达信「红箭头」— AA 上穿 A5 且昨日 AA 低于阈值。

移植自用户提供的通达信指标, 只取其中唯一 COLORRED 的 DRAWICON:
    DRAWICON(CROSS(AA, A5) AND REF(AA,1) < 34, REF(AA,1), 1), COLORRED;
公式其余部分(VAR1~VAR9、买/卖、极品底、郑云绝底 等)仅用于画图, 或依赖本项目
没有的外部数据(CAPITAL、BIAS 系列), 不参与筛选。

最小计算链(仅用前复权 OHLC):
    Q2 = (CLOSE-LLV(LOW,18))/(HHV(HIGH,18)-LLV(LOW,18))*100
    Q3 = SMA(Q2,9,1) ; Q4 = SMA(Q3,3,1) ; AA = 3*Q3 - 2*Q4
    A1 = EMA(Q3,3) ; A2 = EMA(A1,3) ; ... ; A5 = EMA(A4,3)
    红箭头 = CROSS(AA, A5) AND REF(AA,1) < aa_prev_max

口径说明:
  - 通达信 SMA(X,N,M) 是递归均线 Y=(M*X+(N-M)*Y')/N, 等价于 Polars
    ewm_mean(alpha=M/N, adjust=False); EMA(X,N) 等价于 alpha=2/(N+1)。
  - HHV/LLV 用满 18 根窗口(min_samples=18), 不足 18 根不出值; 新股已被
    basic_filter.exclude_new_days 排除, 与通达信"窗口不足用已有数据"的差异
    不影响筛选结果。
  - 分母为 0(连续一字板)按通达信除法口径取 0; 缺数据保持 null(不命中)。
  - 递归均线需要预热: LOOKBACK_DAYS=120 时 alpha=1/9 的冷启动误差约 1e-6。
"""
import polars as pl

META = {
    "id": "custom_tdx_red_arrow",
    "name": "通达信红箭头",
    "description": "AA(3*SMA(Q2,9,1)-2*SMA(Q3,3,1)) 上穿其 5 级 EMA(A5), 且昨日 AA 低于阈值",
    "tags": ["通达信", "指标移植", "超跌反弹"],
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "basic_filter": {
        "price_min": 3,
        "price_max": 200,
        "market_cap_min": 10e8,
        "amount_min": 0.5e8,
        "exclude_st": True,
        "exclude_new_days": 30,
    },
    "params": [
        {"id": "aa_prev_max", "label": "昨日AA上限", "type": "float",
         "default": 34.0, "min": 0.0, "max": 100.0, "step": 1.0},
    ],
    "scoring": {"change_pct": 0.4, "vol_ratio_5d": 0.3, "momentum_5d": 0.3},
    "order_by": "score",
    "descending": True,
    "limit": 100,
}

EXECUTION_BACKEND = "python_history_legacy"
LOOKBACK_DAYS = 120
REQUIRED_FEATURES = {"close", "low", "high"}

ENTRY_SIGNALS = []
EXIT_SIGNALS = []
STOP_LOSS = -0.05
MAX_HOLD_DAYS = 10

RULES = """
1. Q2 = 18 日区间收盘位置：(收盘 - 18日最低) / (18日最高 - 18日最低) * 100
2. Q3 = SMA(Q2,9,1), Q4 = SMA(Q3,3,1), AA = 3*Q3 - 2*Q4
3. A5 = Q3 连续五次 EMA(·,3)
4. 红箭头：AA 上穿 A5（今日 AA>A5 且昨日 AA<昨日 A5），且昨日 AA 低于 aa_prev_max（默认 34）
"""


def filter_history(df: pl.DataFrame, params: dict) -> pl.DataFrame:
    """返回全部命中行(不只最新日); 引擎负责截取 as_of, 回测需要全区间命中。"""
    if df.is_empty():
        return df
    if "symbol" not in df.columns or "date" not in df.columns:
        return df.head(0)

    aa_prev_max = float(params.get("aa_prev_max", 34.0))
    data = df.sort(["symbol", "date"])

    low18 = pl.col("low").rolling_min(18, min_samples=18).over("symbol")
    high18 = pl.col("high").rolling_max(18, min_samples=18).over("symbol")
    span = high18 - low18
    data = data.with_columns(
        pl.when(span == 0)
        .then(pl.lit(0.0))
        .otherwise((pl.col("close") - low18) / span * 100.0)
        .alias("_q2")
    )
    data = data.with_columns(
        pl.col("_q2").ewm_mean(alpha=1 / 9, adjust=False, ignore_nulls=True)
        .over("symbol").alias("_q3")
    )
    data = data.with_columns(
        pl.col("_q3").ewm_mean(alpha=1 / 3, adjust=False, ignore_nulls=True)
        .over("symbol").alias("_q4")
    )
    data = data.with_columns((3 * pl.col("_q3") - 2 * pl.col("_q4")).alias("_aa"))
    for level in range(1, 6):
        source = "_q3" if level == 1 else f"_a{level - 1}"
        data = data.with_columns(
            pl.col(source).ewm_mean(alpha=0.5, adjust=False, ignore_nulls=True)
            .over("symbol").alias(f"_a{level}")
        )

    aa, a5 = pl.col("_aa"), pl.col("_a5")
    prev_aa = aa.shift(1).over("symbol")
    prev_a5 = a5.shift(1).over("symbol")
    hit = (aa > a5) & (prev_aa < prev_a5) & (prev_aa < aa_prev_max)
    return data.filter(hit.fill_null(False)).drop(
        ["_q2", "_q3", "_q4", "_aa", "_a1", "_a2", "_a3", "_a4", "_a5"]
    )
