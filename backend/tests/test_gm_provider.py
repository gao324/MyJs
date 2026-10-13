"""GmProvider 契约与单位标准化测试。

不依赖真实网络与 gm venv: 以 run_op 桥接返回的真实形状样例行
(2026-10-13 实测探针确认) 验证代码映射、单位口径 (CONTRIBUTING §3.1:
volume 股→手 floor、amount 元透传)、eob 北京时区归一 (§3.3)、
累计后复权因子转单事件比值、财务 canonical 映射与能力声明。
"""

from __future__ import annotations

from datetime import date, datetime

from app.plugins.gm import provider as gp
from app.plugins.gm.provider import GmProvider


def _tick(symbol="SHSE.600519", **kw):
    row = {
        "symbol": symbol, "open": 1287.0, "high": 1288.48, "low": 1268.11,
        "price": 1274.89,
        "quotes": [
            {"bid_p": 1274.66, "bid_v": 100, "ask_p": 1274.89, "ask_v": 56},
            {"bid_p": 1274.43, "bid_v": 200, "ask_p": 1274.95, "ask_v": 100},
        ],
        "cum_volume": 2657276, "cum_amount": 3393315572.0,
        "created_at": "2026-10-13 15:01:38.354000+08:00",
    }
    row.update(kw)
    return row


def _bar(gm_symbol="SHSE.600519", eob="2026-09-02T16:00:00.000Z", **kw):
    row = {
        "_gm_symbol": gm_symbol, "symbol": gm_symbol,
        "open": 1297.5, "high": 1305.0, "low": 1293.02, "close": 1298.88,
        "volume": 1774765, "amount": 2305193119.35, "pre_close": 1297.5, "eob": eob,
    }
    row.update(kw)
    return row


# ---- 代码映射 ----

def test_symbol_mapping_both_directions():
    assert gp._to_internal("SHSE.600519") == "600519.SH"
    assert gp._to_internal("SZSE.000001") == "000001.SZ"
    assert gp._to_gm("600519.SH") == "SHSE.600519"
    assert gp._to_gm("000001.SZ") == "SZSE.000001"
    assert gp._to_gm("000680.SH") == "SHSE.000680"


def test_symbol_mapping_unsupported():
    # 北交所不在 GM 交易所清单: 去程 None (跳过, 上层回退), 域外代码丢弃
    assert gp._to_gm("430047.BJ") is None
    assert gp._to_internal("BJ.430047") is None
    assert gp._to_internal("") is None
    assert gp._to_internal(None) is None


# ---- 日K ----

def test_daily_frame_units_and_window():
    rows = [
        _bar(eob="2026-08-31T16:00:00.000Z"),  # 窗口前一行 (GM 前开后闭多带) 应过滤
        _bar(eob="2026-09-02T16:00:00.000Z"),
        _bar(eob="2026-10-20T16:00:00.000Z"),  # 窗口后应过滤
        _bar(gm_symbol="BJ.430047", eob="2026-09-02T16:00:00.000Z"),  # 不支持的交易所丢弃
    ]
    df = GmProvider._daily_frame(rows, date(2026, 9, 1), date(2026, 10, 10))
    assert df.columns == ["symbol", "date", "open", "high", "low", "close", "volume", "amount"]
    assert df.height == 1
    row = df.row(0, named=True)
    assert row["symbol"] == "600519.SH"
    assert row["date"] == date(2026, 9, 2)  # 上海零点戳 → 交易日, 非 UTC 日期
    assert row["volume"] == 17747  # 股 floor /100 → 手
    assert row["amount"] == 2305193119.35  # 元直接透传
    assert row["close"] == 1298.88  # ADJUST_NONE 原始价


def test_daily_frame_empty():
    df = GmProvider._daily_frame([], date(2026, 9, 1), date(2026, 10, 10))
    assert df.is_empty()


# ---- 分钟K ----

def test_minute_frame_wallclock_and_filter():
    rows = [
        _bar(eob="2026-10-09T01:30:00.000Z"),  # = 北京 09:30
        _bar(eob="2026-10-09T01:31:00.000Z", close=None),  # 无收分钟丢弃, 不伪造
    ]
    df = GmProvider._minute_frame(rows, datetime(2026, 10, 9, 9, 0, 0), datetime(2026, 10, 9, 15, 0, 0))
    assert df.height == 1
    row = df.row(0, named=True)
    assert row["datetime"] == datetime(2026, 10, 9, 9, 30, 0)  # 北京墙钟 naive
    assert row["datetime"].tzinfo is None
    assert row["volume"] == 17747.0


# ---- 实时快照 ----

def test_map_tick_units():
    rec = GmProvider._map_tick(_tick(), 123)
    assert rec["symbol"] == "600519.SH"
    assert rec["last_price"] == 1274.89
    assert rec["volume"] == 26572  # 累计股 floor /100 → 手
    assert rec["amount"] == 3393315572.0
    assert rec["prev_close"] is None  # GM current() 无 pre_close, 下游 enriched 回退
    assert rec["change_pct"] is None
    assert rec["timestamp"] > 0  # created_at(+08) → epoch ms


def test_map_tick_drops_bad_rows():
    assert GmProvider._map_tick(_tick(symbol="??"), 1) is None
    assert GmProvider._map_tick(_tick(price=None), 1) is None


def test_depth_entry_units():
    entry = GmProvider._depth_entry(_tick(), 7)
    assert entry["bid_prices"] == [1274.66, 1274.43]
    assert entry["ask_prices"] == [1274.89, 1274.95]
    assert entry["bid_volumes"] == [1, 2]  # 股 floor /100 → 手 (封单量口径 §3.1)
    assert entry["ask_volumes"] == [0, 1]  # 56 股 → 0 手, 不伪造为 1
    assert entry["timestamp"] > 0


def test_depth_entry_empty_quotes():
    assert GmProvider._depth_entry(_tick(quotes=[]), 1) is None


# ---- 除权因子 (累计 → 单事件比值) ----

def _hist_rows():
    return [
        {"trade_date": "2026-08-29T16:00:00.000Z", "adj_factor": 7.66925686, "pre_close": 1299.52},
        {"trade_date": "2026-09-01T16:00:00.000Z", "adj_factor": 7.66925686, "pre_close": 1299.56},
        {"trade_date": "2026-09-02T16:00:00.000Z", "adj_factor": 6.9, "pre_close": 1297.5},
    ]


def test_adj_factor_ratio(monkeypatch):
    monkeypatch.setattr(gp, "run_op", lambda *a, **k: _hist_rows())
    df = GmProvider().get_adj_factors(
        ["600519.SH"], datetime(2026, 8, 1), datetime(2026, 9, 30))
    assert df.height == 1  # 仅因子变化日产出, 无变化日不产出行
    row = df.row(0, named=True)
    assert (row["symbol"], row["trade_date"]) == ("600519.SH", date(2026, 9, 2))
    assert row["ex_factor"] == 6.9 / 7.66925686  # 单事件比值, 非累积
    assert row["prev_close"] == 1297.5
    assert row["dividend"] is None  # GM 无事件明细, 可空


def test_adj_factor_bridge_failure_returns_empty(monkeypatch):
    from app.plugins.gm.client import GmError

    def _boom(*a, **k):
        raise GmError("nope")

    monkeypatch.setattr(gp, "run_op", _boom)
    df = GmProvider().get_adj_factors(["600519.SH"], None, None)
    assert df.is_empty()


# ---- 维表 ----

def test_get_instruments_filters_delisted(monkeypatch):
    rows = [
        {"symbol": "SHSE.600519", "sec_id": "600519", "sec_name": "贵州茅台",
         "listed_date": "2001-08-27 00:00:00+08:00", "delisted_date": "2038-01-01 00:00:00+08:00"},
        {"symbol": "SHSE.600000", "sec_id": "600000", "sec_name": "已退市",
         "listed_date": "2000-01-01 00:00:00+08:00", "delisted_date": "2020-01-01 00:00:00+08:00"},
    ]
    monkeypatch.setattr(gp, "run_op", lambda *a, **k: rows)
    items = GmProvider().get_instruments("stock")
    assert len(items) == 1
    assert items[0]["symbol"] == "600519.SH"
    assert items[0]["name"] == "贵州茅台"
    assert items[0]["code"] == "600519"
    assert items[0]["exchange"] == "SH"


# ---- 财务 ----

def test_fin_frame_canonical_mapping():
    rows = [{"_gm_symbol": "SHSE.600519", "symbol": "SHSE.600519",
             "rpt_date": "2023-06-30", "pub_date": "2023-08-27",
             "inc_oper": 7e10, "net_prof_pcom": 3e10, "eps_base": 1.5}]
    df = GmProvider._fin_frame(rows, {"SHSE.600519": "600519.SH"}, gp._INCOME_MAP)
    row = df.row(0, named=True)
    assert row["symbol"] == "600519.SH"
    assert row["period_end"] == "2023-06-30"  # rpt_date → period_end
    assert row["announce_date"] == date(2023, 8, 27)  # pub_date → announce_date
    assert row["revenue"] == 7e10
    assert row["net_income_attributable"] == 3e10
    assert row["basic_eps"] == 1.5


def test_metrics_frame_merges_prime_and_deriv():
    prime = [{"_gm_symbol": "SHSE.600519", "rpt_date": "2023-06-30", "pub_date": "2023-08-27",
              "roe": 15.2, "bps_pcom_ps": 20.1, "eps_basic": 1.5,
              "inc_oper_yoy": 10.0, "net_prof_pcom_yoy": 12.0, "net_cf_oper": 1e10}]
    deriv = [{"_gm_symbol": "SHSE.600519", "rpt_date": "2023-06-30", "pub_date": "2023-08-27",
              "roa": 8.1, "sale_gpm": 90.0, "sale_npm": 50.0, "ast_liab_rate": 15.0,
              "ocf_toi": 1.1, "inv_turnover_rate": 0.5}]
    df = GmProvider._metrics_frame(prime, deriv, {"SHSE.600519": "600519.SH"})
    assert df.height == 1
    row = df.row(0, named=True)
    assert row["bps"] == 20.1
    assert row["roe"] == 15.2
    assert row["gross_margin"] == 90.0
    assert row["net_margin"] == 50.0
    assert row["debt_to_asset_ratio"] == 15.0
    assert row["revenue_yoy"] == 10.0
    assert row["net_income_yoy"] == 12.0


def test_shares_frame_maps_circ_shares():
    rows = [{"_gm_symbol": "SHSE.600519", "trade_date": "2026-09-21",
             "ttl_shr": 1250081601.0, "circ_shr": 1250081601.0}]
    df = GmProvider._shares_frame(rows, {"SHSE.600519": "600519.SH"})
    row = df.row(0, named=True)
    assert row["float_shares"] == 1250081601.0
    assert row["total_shares"] == 1250081601.0
    assert row["period_end"] == "2026-09-21"


# ---- 能力声明 ----

def test_datasets_declared():
    assert set(gp._DATASETS) == {
        "realtime", "depth5", "daily", "adj_factor", "minute", "full_minute", "financial",
    }
    assert GmProvider().config.datasets.keys() == set(gp._DATASETS)
