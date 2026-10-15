"""GM (掘金量化) 内置数据源 provider。

方法签名对齐 custom.GenericHTTPProvider(service 分流点按这套签名调用),
注入 custom loader 注册表后, 各 service 无需改动即可路由到本 provider。

SDK 不进后端主 venv: gm 3.0.190 钉死 numpy 1.26.4 / pandas 1.5.3,
与项目锁定的 numpy 2.x / pandas 3.x 冲突, 故经独立 venv (data/gm-env)
子进程桥接 (client.run_op), 本文件只做纯映射 (polars 可用)。

实现数据集:
  - realtime    全市场快照: 股票池 + ETF 池 (universe 日缓存) → current() 分批
  - depth5      current() tick 自带五档 quotes, 与快照同源
  - daily       history(1d, ADJUST_NONE 原始价) 多标的批量; stock/etf/index 均可
  - adj_factor  get_history_symbol 每日累计后复权因子 → 相邻交易日比值即单事件因子
  - minute      history(60s, ADJUST_NONE); 仅 1m
  - full_minute 当日窗口逐标的 history(60s) (修复轮/增量轮各一)
  - financial   三大表 + prime/deriv 指标 + daily_basic 股本, 逐标的 (免费函数)

单位与口径 (CONTRIBUTING §3, 2026-10-13 实测探针确认, 见 out.json):
  - GM volume 单位为股 → floor(/100) 转手; amount 单位元, 直接透传。
  - 日K取数 adjust=ADJUST_NONE 锁定原始价 (项目内前复权一律由
    indicators.pipeline 用本地因子计算, 与 fuyao/tdx 保持一致)。
  - eob 为 Asia/Shanghai 零点/分钟戳 → date()/naive wall clock, 契约 §3.3。
  - current() tick 无 pre_close 字段 → realtime 记录 prev_close/change 置空,
    下游 quote_service 对全空列丢弃并由 enriched 本地日K回退计算
    (与 fuyao turnover_rate=None 同机制)。
  - history 为前开后闭区间, 起点可能多带一行 → 本层按窗口过滤。
  - adj_factor 为累计后复权因子 → 单事件比值 = 当日/上一交易日 (非累积),
    累积链由 pipeline._apply_adj_factor 构建。
  - 财务 % 口径 (roe 等) 保持原值透传, 与 fuyao 一致, 不做 /100。
  - 北交所 (.BJ): GM 交易所清单无北交所, 相关标的跳过并 warning,
    由上层回退 TickFlow。
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import polars as pl

from app.plugins.gm.client import GmError, get_api_key, run_op

logger = logging.getLogger(__name__)

# 只声明真实提供的数据集; 其余 provider_has_dataset 返回 False → 回退 tickflow
_DATASETS = ("realtime", "depth5", "daily", "adj_factor", "minute", "full_minute", "financial")

API_KEY_ENV = "GM_API_KEY"
SECRETS_FIELD = "gm_api_key"  # UI 配置的 Key 存 secrets.json, 优先级高于 .env

_UNIVERSE_TTL_S = 24 * 3600
_DAILY_CHUNK = 50
_MINUTE_CHUNK = 20
_CURRENT_CHUNK = 500  # 桥接内 current() 已按 500 切分, 此处仅用于 depth 批大小
_HIST_INTERVAL_S = 0.05  # 桥接内节流; provider 侧 spawn 间不再额外 sleep
_BEIJING = timezone(timedelta(hours=8))

_EX_TO_GM = {"SH": "SHSE", "SZ": "SZSE"}
_GM_TO_EX = {"SHSE": "SH", "SZSE": "SZ"}
_ASSET_SEC = {"stock": (1010, 101001), "etf": (1020, 102001), "index": (1060, 106001)}

_DAILY_SCHEMA = {
    "symbol": pl.String, "date": pl.Date,
    "open": pl.Float64, "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
    "volume": pl.Int64, "amount": pl.Float64,
}
_ADJ_SCHEMA = {
    "symbol": pl.String, "trade_date": pl.Date, "ex_factor": pl.Float64,
    "dividend": pl.Float64, "bonus": pl.Float64, "allot": pl.Float64,
    "allot_price": pl.Float64, "prev_close": pl.Float64,
}
_MINUTE_SCHEMA = {
    "symbol": pl.String, "datetime": pl.Datetime("us"),
    "open": pl.Float64, "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
    "volume": pl.Float64, "amount": pl.Float64,
}

_INCOME_MAP = {
    "inc_oper": "revenue", "cost_oper": "operating_cost", "exp_sell": "selling_expense",
    "exp_adm": "admin_expense", "exp_rd": "rd_expense", "oper_prof": "operating_profit",
    "exp_fin": "financial_expense", "ttl_prof": "total_profit", "inc_tax": "income_tax",
    "net_prof": "net_income", "net_prof_pcom": "net_income_attributable",
    "eps_base": "basic_eps",
}
_BALANCE_MAP = {
    "ttl_ast": "total_assets", "ttl_cur_ast": "total_current_assets",
    "ttl_ncur_ast": "total_non_current_assets", "mny_cptl": "cash_and_equivalents",
    "acct_rcv": "accounts_receivable", "ttl_liab": "total_liabilities",
    "ttl_eqy": "total_equity",
}
_CASHFLOW_MAP = {
    "net_cf_oper": "net_operating_cash_flow", "net_cf_inv": "net_investing_cash_flow",
    "net_cf_fin": "net_financing_cash_flow", "pur_fix_intg_ast": "capex",
    "net_incr_cash_eq": "net_cash_change",
}
_METRICS_MAP = {
    "eps_basic": "eps_basic", "bps_pcom_ps": "bps", "roe": "roe", "roa": "roa",
    "sale_gpm": "gross_margin", "sale_npm": "net_margin",
    "ast_liab_rate": "debt_to_asset_ratio", "inc_oper_yoy": "revenue_yoy",
    "net_prof_pcom_yoy": "net_income_yoy", "ocf_toi": "operating_cash_to_revenue",
    "inv_turnover_rate": "inventory_turnover",
}
_PRIME_FIELDS = "eps_basic,bps_pcom_ps,roe,inc_oper_yoy,net_prof_pcom_yoy,net_cf_oper"
_DERIV_FIELDS = "roa,sale_gpm,sale_npm,ast_liab_rate,ocf_toi,inv_turnover_rate"


def availability() -> tuple[bool, str]:
    """loader 启动自检: GM Token 已配置才注册; venv 缺失时尝试自动创建。不抛异常。"""
    if not get_api_key():
        return False, f"缺少 API Key(可在下方输入框直接填写,或配置环境变量 {API_KEY_ENV})"
    from app.plugins.gm import client as gm_client

    if gm_client.venv_ready():
        return True, "ok"
    try:
        gm_client.ensure_venv()
        return True, "ok"
    except GmError as e:
        return False, f"GM 数据环境未就绪: {e}"


def probe_api_key(api_key: str) -> tuple[bool, str]:
    """用候选 Key 实探一次 current 快照 (先探后存, 不落盘)。"""
    from app.plugins.gm import client as gm_client

    return gm_client.probe_token(api_key)


@dataclass
class _GmConfig:
    """轻量 config shim, 让 custom loader 的 provider_has_dataset 能识别本 provider。"""

    name: str = "gm"
    display_name: str = "GM (掘金量化)"
    datasets: dict = field(default_factory=lambda: dict.fromkeys(_DATASETS))
    path: None = None
    builtin: bool = True


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _to_int_hand(value) -> int | None:
    """股 → 手 (floor)。"""
    f = _to_float(value)
    return math.floor(f / 100.0) if f is not None else None


def _to_internal(gm_symbol: str | None) -> str | None:
    """SHSE.600519 → 600519.SH; 未知交易所返回 None (行丢弃, 不伪造)。"""
    if not gm_symbol or "." not in gm_symbol:
        return None
    ex, code = gm_symbol.split(".", 1)
    short = _GM_TO_EX.get(ex.upper())
    return f"{code}.{short}" if short and code else None


def _to_gm(symbol: str) -> str | None:
    """600519.SH → SHSE.600519; 北交所等 GM 不支持的返回 None。"""
    if not symbol or "." not in symbol:
        return None
    code, ex = symbol.rsplit(".", 1)
    gm_ex = _EX_TO_GM.get(ex.upper())
    return f"{gm_ex}.{code}" if gm_ex and code else None


def _beijing_date(iso: str | None) -> date | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso)).date()
    except ValueError:
        try:
            return date.fromisoformat(str(iso)[:10])
        except ValueError:
            return None


def _beijing_naive(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(_BEIJING).replace(tzinfo=None)
    return dt


def _beijing_wallclock(dt: datetime | None) -> datetime | None:
    """取数窗口归一为北京墙钟 naive (与帧内 datetime 同口径); naive 值原样返回。

    调用方窗口两端带北京时区 (kline_sync.sync_and_persist_minute 的 CN_TZ 窗口),
    与帧内 naive datetime 直接比较会抛 TypeError。
    """
    if dt is None or dt.tzinfo is None:
        return dt
    return dt.astimezone(_BEIJING).replace(tzinfo=None)


def _created_ms(iso: str | None, fallback_ms: int) -> int:
    dt = _beijing_naive(iso)
    if dt is None:
        return fallback_ms
    return int(dt.replace(tzinfo=_BEIJING).timestamp() * 1000)


class GmProvider:
    """GM 数据源。realtime = 股票池 + ETF 池 current() 批量快照。"""

    name = "gm"
    builtin = True

    def __init__(self) -> None:
        self.config = _GmConfig()

    def close(self) -> None:  # loader.load_all 重建注册表时会对每个 provider 调 close
        pass  # 桥接为单发子进程, 无常驻连接

    # ---- universe 日缓存 ----
    def _universe_cache_path(self) -> Path:
        from app.config import settings

        d = settings.data_dir / "cache" / "gm"
        d.mkdir(parents=True, exist_ok=True)
        return d / "universe.json"

    def _load_universe(self) -> tuple[list[str], list[str]]:
        """(stocks, etfs) 内部代码; 缓存 24h, 失效则经桥接刷新。"""
        import json as _json

        path = self._universe_cache_path()
        if path.exists():
            try:
                cached = _json.loads(path.read_text(encoding="utf-8"))
                if time.time() - float(cached.get("as_of", 0)) < _UNIVERSE_TTL_S:
                    return cached.get("stocks", []), cached.get("etfs", [])
            except Exception:  # noqa: BLE001 — 缓存损坏即刷新
                pass
        stocks = self._fetch_universe_symbols(1010, 101001)
        etfs = self._fetch_universe_symbols(1020, 102001)
        try:
            path.write_text(
                _json.dumps({"as_of": time.time(), "stocks": stocks, "etfs": etfs}),
                encoding="utf-8",
            )
        except Exception as e:  # noqa: BLE001 — 缓存写失败不致命
            logger.debug("GM universe 缓存写入失败: %s", e)
        return stocks, etfs

    @staticmethod
    def _fetch_universe_symbols(sec_type1: int, sec_type2: int) -> list[str]:
        try:
            rows = run_op("universe", {"sec_type1": sec_type1, "sec_type2": sec_type2},
                          timeout_s=180.0)
        except GmError as e:
            logger.warning("GM 股票池枚举失败 (%s/%s): %s", sec_type1, sec_type2, e)
            return []
        out = []
        for r in rows:
            sym = _to_internal(r.get("symbol"))
            if sym and sym not in out:
                out.append(sym)
        logger.info("GM 股票池枚举: sec_type1=%s 共 %d 只", sec_type1, len(out))
        return out

    # ---- realtime ----
    def get_realtime(self) -> list[dict]:
        """全市场实时快照 → 内部 realtime records。失败软返回空列表(不阻断轮询)。"""
        stocks, etfs = self._load_universe()
        gm_syms = [g for s in (*stocks, *etfs) if (g := _to_gm(s))]
        if not gm_syms:
            return []
        try:
            rows = run_op("current", {"symbols": gm_syms}, timeout_s=240.0)
        except GmError as e:
            logger.warning("GM 实时行情拉取失败: %s", e)
            return []
        fallback_ms = int(time.time() * 1000)
        records, dropped = [], 0
        for row in rows:
            rec = self._map_tick(row, fallback_ms, volume_to_hand=True)
            if rec is not None:
                records.append(rec)
            else:
                dropped += 1
        if dropped and not records:
            logger.warning("GM 快照 %d 行全部无法识别, 疑似接口结构变化", dropped)
            return []
        logger.info("GM 实时行情拉取完成: %d 条(丢弃 %d 行)", len(records), dropped)
        return records

    def get_realtime_indices(self, symbols: list[str]) -> list[dict] | None:
        """指数实时快照 (可选插件协议, quote_service 鸭子类型调用)。

        失败返回 None (保留上轮有效指数缓存); 成功但无数据返回 []。
        """
        gm_syms = [g for s in (symbols or []) if s and (g := _to_gm(s))]
        if not gm_syms:
            return []
        try:
            rows = run_op("current", {"symbols": gm_syms}, timeout_s=60.0)
        except GmError as e:
            logger.warning("GM 指数行情拉取失败: %s", e)
            return None
        fallback_ms = int(time.time() * 1000)
        records = []
        for row in rows:
            rec = self._map_tick(row, fallback_ms, volume_to_hand=False)
            if rec is not None:
                records.append(rec)
        logger.info("GM 指数行情拉取完成: %d 条(请求 %d 只)", len(records), len(gm_syms))
        return records

    @staticmethod
    def _map_tick(row: dict, fallback_ms: int, *, volume_to_hand: bool = True) -> dict | None:
        """GM current() tick → 内部 realtime record。无 pre_close, 涨跌幅由下游回退。"""
        symbol = _to_internal(row.get("symbol"))
        if not symbol:
            return None
        last = _to_float(row.get("price"))
        if last is None:
            return None
        volume = _to_float(row.get("cum_volume"))
        return {
            "symbol": symbol,
            "name": None,  # 快照无名称, 由下游维表关联
            "last_price": last,
            "prev_close": None,  # GM current() 无 pre_close, enriched 用本地日K回退
            "open": _to_float(row.get("open")),
            "high": _to_float(row.get("high")),
            "low": _to_float(row.get("low")),
            "volume": _to_int_hand(volume) if volume_to_hand else volume,
            "amount": _to_float(row.get("cum_amount")),
            "change_pct": None,
            "change_amount": None,
            "amplitude": None,
            "turnover_rate": None,  # 需股本口径 (§3.4), 交给 enriched 管道
            "timestamp": _created_ms(row.get("created_at"), fallback_ms),
            "session": None,
        }

    # ---- depth5 ----
    def get_depth_batch(self, symbols: list[str]) -> dict[str, dict]:
        """五档盘口 → {symbol: 标准盘口字典}。量单位手 (股 floor /100)。"""
        gm_by_internal = {s: g for s in (symbols or []) if (g := _to_gm(s))}
        if not gm_by_internal:
            return {}
        try:
            rows = run_op("current", {"symbols": sorted(gm_by_internal.values())},
                          timeout_s=120.0)
        except GmError as e:
            logger.warning("GM 五档拉取失败(%d 只): %s", len(gm_by_internal), e)
            return {}
        gm_of = {g: s for s, g in gm_by_internal.items()}
        fallback_ms = int(time.time() * 1000)
        out: dict[str, dict] = {}
        for row in rows:
            internal = gm_of.get(row.get("symbol"))
            if not internal:
                continue
            entry = self._depth_entry(row, fallback_ms)
            if entry is not None:
                out[internal] = entry
        return out

    @staticmethod
    def _depth_entry(row: dict, fallback_ms: int) -> dict | None:
        quotes = row.get("quotes") or []
        bid_p, bid_v, ask_p, ask_v = [], [], [], []
        for q in quotes:
            bp, bv = _to_float(q.get("bid_p")), _to_int_hand(q.get("bid_v"))
            ap, av = _to_float(q.get("ask_p")), _to_int_hand(q.get("ask_v"))
            if bp is None or ap is None:
                return None
            bid_p.append(bp)
            bid_v.append(bv if bv is not None else 0)
            ask_p.append(ap)
            ask_v.append(av if av is not None else 0)
        if not bid_p:
            return None
        return {
            "bid_prices": bid_p, "bid_volumes": bid_v,
            "ask_prices": ask_p, "ask_volumes": ask_v,
            "timestamp": _created_ms(row.get("created_at"), fallback_ms),
        }

    # ---- instruments ----
    def get_instruments(self, asset_type: str = "stock") -> list[dict]:
        """标的维表 → flatten 行 (symbol/name/code/exchange, 内部代码制)。"""
        sec = _ASSET_SEC.get(asset_type)
        if sec is None:
            return []
        try:
            rows = run_op("symbol_infos",
                          {"sec_type1": sec[0], "sec_type2": sec[1]}, timeout_s=180.0)
        except GmError as e:
            logger.warning("GM get_instruments(%s) 失败: %s", asset_type, e)
            return []
        today = date.today()
        out = []
        for r in rows:
            symbol = _to_internal(r.get("symbol"))
            if not symbol:
                continue
            try:
                delisted = _beijing_date(r.get("delisted_date"))
            except Exception:  # noqa: BLE001
                delisted = None
            if delisted is not None and delisted <= today:
                continue  # 已退市不进维表
            out.append({
                "symbol": symbol,
                "name": r.get("sec_name") or symbol,
                "code": r.get("sec_id") or symbol.split(".")[0],
                "exchange": symbol.rsplit(".", 1)[1],
                "region": None,
                "type": asset_type,
                "ext": {"listing_date": _beijing_date(r.get("listed_date"))},
            })
        logger.info("GM instruments(%s): %d 行", asset_type, len(out))
        return out

    # ---- daily ----
    def get_daily(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> pl.DataFrame:
        chunks = [
            df for df in self.iter_daily(
                symbols, start_time=start_time, end_time=end_time,
                asset_type=asset_type, on_chunk_done=on_chunk_done,
            ) if not df.is_empty()
        ]
        return pl.concat(chunks, how="diagonal_relaxed") if chunks else pl.DataFrame(schema=_DAILY_SCHEMA)

    def iter_daily(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> Iterator[pl.DataFrame]:
        """history(1d, ADJUST_NONE 原始价) 多标的批量 → 内部契约 (volume 手, amount 元)。"""
        if not symbols or asset_type not in ("stock", "etf", "index"):
            if on_chunk_done:
                on_chunk_done(0, 0)
            return
        end_dt = end_time or datetime.now()
        start_dt = start_time or (end_dt - timedelta(days=365))
        gm_syms = [g for s in symbols if (g := _to_gm(s))]
        if not gm_syms:
            if on_chunk_done:
                on_chunk_done(0, 0)
            return
        total = math.ceil(len(gm_syms) / _DAILY_CHUNK)
        for done, i in enumerate(range(0, len(gm_syms), _DAILY_CHUNK), start=1):
            batch = gm_syms[i:i + _DAILY_CHUNK]
            try:
                rows = run_op("history", {
                    "symbols": batch, "frequency": "1d",
                    "start": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "end": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
                }, timeout_s=300.0)
            except GmError as e:
                logger.warning("GM 日K批次失败(%d 只): %s", len(batch), e)
                rows = []
            if on_chunk_done:
                on_chunk_done(done, total)
            yield self._daily_frame(rows, start_dt.date(), end_dt.date())

    @staticmethod
    def _daily_frame(rows: list[dict], start_d: date, end_d: date) -> pl.DataFrame:
        out = []
        for r in rows:
            symbol = _to_internal(r.get("_gm_symbol") or r.get("symbol"))
            d = _beijing_date(r.get("eob"))
            if symbol is None or d is None or d < start_d or d > end_d:
                continue
            volume = _to_float(r.get("volume"))
            out.append({
                "symbol": symbol, "date": d,
                "open": _to_float(r.get("open")), "high": _to_float(r.get("high")),
                "low": _to_float(r.get("low")), "close": _to_float(r.get("close")),
                "volume": math.floor(volume / 100.0) if volume is not None else None,
                "amount": _to_float(r.get("amount")),
            })
        if not out:
            return pl.DataFrame(schema=_DAILY_SCHEMA)
        return pl.DataFrame(out, schema=_DAILY_SCHEMA).sort(["symbol", "date"])

    # ---- adj_factor ----
    def get_adj_factors(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> pl.DataFrame:
        """累计后复权因子 → 单事件比值 (相邻交易日 adj_factor 之比, 非累积)。

        GM 不提供分红送转事件明细, dividend/bonus/allot/allot_price 置空
        (可空列, 只影响等差显示投影, 不影响复权价)。
        """
        if not symbols or asset_type != "stock":
            return pl.DataFrame(schema=_ADJ_SCHEMA)
        end_dt = end_time or datetime.now()
        start_dt = start_time or (end_dt - timedelta(days=365))
        gm_of = {g: s for s in symbols if (g := _to_gm(s))}
        out_rows: list[dict] = []
        total = len(gm_of)
        for i, (gm_sym, symbol) in enumerate(gm_of.items()):
            try:
                rows = run_op("history_symbol", {
                    "symbol": gm_sym,
                    "start_date": (start_dt - timedelta(days=30)).strftime("%Y-%m-%d"),
                    "end_date": end_dt.strftime("%Y-%m-%d"),
                }, timeout_s=120.0)
            except GmError as e:
                logger.warning("GM 除权因子 %s 拉取失败: %s", symbol, e)
                rows = []
            days = sorted(
                (d, _to_float(r.get("adj_factor")), _to_float(r.get("pre_close")))
                for r in rows if (d := _beijing_date(r.get("trade_date")))
            )
            prev_factor: float | None = None
            for d, adj, pre in days:
                if adj is not None and adj > 0:
                    if prev_factor is not None and abs(adj - prev_factor) > 1e-9 and d >= start_dt.date():
                        out_rows.append({
                            "symbol": symbol, "trade_date": d,
                            "ex_factor": adj / prev_factor,
                            "dividend": None, "bonus": None, "allot": None,
                            "allot_price": None, "prev_close": pre,
                        })
                    prev_factor = adj
            if on_chunk_done:
                on_chunk_done(i + 1, total)
            time.sleep(_HIST_INTERVAL_S)
        if not out_rows:
            return pl.DataFrame(schema=_ADJ_SCHEMA)
        return (
            pl.DataFrame(out_rows, schema=_ADJ_SCHEMA)
            .unique(subset=["symbol", "trade_date"], keep="last")
            .sort(["symbol", "trade_date"])
        )

    # ---- minute / full_minute ----
    def get_minute(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        freq: str = "1m",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> pl.DataFrame:
        """分钟K → 内部契约 (datetime 北京墙钟 naive, volume 手, amount 元)。仅 1m。"""
        chunks = list(self.iter_minute(
            symbols, start_time=start_time, end_time=end_time,
            asset_type=asset_type, freq=freq, on_chunk_done=on_chunk_done,
        ))
        parts = [df for df in chunks if not df.is_empty()]
        return pl.concat(parts, how="diagonal_relaxed") if parts else pl.DataFrame(schema=_MINUTE_SCHEMA)

    def iter_minute(
        self,
        symbols: list[str],
        start_time: datetime | None,
        end_time: datetime | None,
        asset_type: str = "stock",
        freq: str = "1m",
        on_chunk_done: Callable[[int, int], None] | None = None,
    ) -> Iterator[pl.DataFrame]:
        if not symbols or asset_type not in ("stock", "etf", "index") or freq != "1m":
            if on_chunk_done:
                on_chunk_done(0, 0)
            return
        end_dt = end_time or datetime.now()
        gm_syms = [g for s in symbols if (g := _to_gm(s))]
        total = math.ceil(len(gm_syms) / _MINUTE_CHUNK) if gm_syms else 0
        for done, i in enumerate(range(0, len(gm_syms), _MINUTE_CHUNK), start=1):
            batch = gm_syms[i:i + _MINUTE_CHUNK]
            try:
                rows = run_op("history", {
                    "symbols": batch, "frequency": "60s",
                    "start": (start_time or (end_dt - timedelta(days=30))).strftime("%Y-%m-%d %H:%M:%S"),
                    "end": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
                }, timeout_s=300.0)
            except GmError as e:
                logger.warning("GM 分钟K批次失败(%d 只): %s", len(batch), e)
                rows = []
            if on_chunk_done:
                on_chunk_done(done, total)
            yield self._minute_frame(rows, start_time, end_dt)

    @staticmethod
    def _minute_frame(rows: list[dict], start_dt: datetime | None, end_dt: datetime) -> pl.DataFrame:
        # 窗口可能带北京时区 (kline_sync 的增量/向前扩展窗口), 帧内 datetime 是
        # 北京墙钟 naive: 先归一到同一口径再比较, 否则 naive/aware 混比直接抛错。
        start_dt = _beijing_wallclock(start_dt)
        end_dt = _beijing_wallclock(end_dt)
        out = []
        for r in rows:
            symbol = _to_internal(r.get("_gm_symbol") or r.get("symbol"))
            dt = _beijing_naive(r.get("eob"))
            close = _to_float(r.get("close"))
            if symbol is None or dt is None or close is None:
                continue
            if start_dt is not None and dt < start_dt:
                continue
            if dt > end_dt:
                continue
            volume = _to_float(r.get("volume"))
            out.append({
                "symbol": symbol, "datetime": dt,
                "open": _to_float(r.get("open")), "high": _to_float(r.get("high")),
                "low": _to_float(r.get("low")), "close": close,
                "volume": float(math.floor(volume / 100.0)) if volume is not None else None,
                "amount": _to_float(r.get("amount")),
            })
        if not out:
            return pl.DataFrame(schema=_MINUTE_SCHEMA)
        return pl.DataFrame(out, schema=_MINUTE_SCHEMA).sort(["symbol", "datetime"])

    def get_intraday_batch(
        self, symbols: list[str], count: int = 300, asset_type: str = "stock",
    ) -> pl.DataFrame:
        """全量分钟修复轮: 当日窗口逐批拉取, 只保留今日 1m 行。"""
        if not symbols or asset_type not in ("stock", "etf", "index"):
            return pl.DataFrame(schema=_MINUTE_SCHEMA)
        today = datetime.now().date()
        start = datetime(today.year, today.month, today.day)
        gm_syms = [g for s in symbols if (g := _to_gm(s))]
        out: list[pl.DataFrame] = []
        for i in range(0, len(gm_syms), _MINUTE_CHUNK):
            batch = gm_syms[i:i + _MINUTE_CHUNK]
            try:
                rows = run_op("history", {
                    "symbols": batch, "frequency": "60s",
                    "start": start.strftime("%Y-%m-%d %H:%M:%S"),
                    "end": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }, timeout_s=300.0)
            except GmError as e:
                logger.warning("GM 修复轮批次失败(%d 只): %s", len(batch), e)
                continue
            df = self._minute_frame(rows, start, datetime.now())
            if not df.is_empty():
                out.append(df.filter(pl.col("datetime").dt.date() == today))
        return pl.concat(out, how="diagonal_relaxed") if out else pl.DataFrame(schema=_MINUTE_SCHEMA)

    def get_intraday_latest(self, symbols: list[str] | None = None, count: int = 3) -> pl.DataFrame:
        """全量分钟稳态增量轮: 缺省 = 全市场股票池, 每只最新若干根 (GM 无批量端点, 按批拉取)。"""
        targets = list(symbols) if symbols else []
        if not targets:
            stocks, _ = self._load_universe()
            targets = stocks
        count = max(1, min(int(count), 30))
        gm_syms = [g for s in targets if (g := _to_gm(s))]
        out: list[pl.DataFrame] = []
        for i in range(0, len(gm_syms), _MINUTE_CHUNK):
            batch = gm_syms[i:i + _MINUTE_CHUNK]
            try:
                rows = run_op("history", {
                    "symbols": batch, "frequency": "60s",
                    "start": (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S"),
                    "end": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }, timeout_s=300.0)
            except GmError as e:
                logger.warning("GM 增量轮批次失败(%d 只): %s", len(batch), e)
                continue
            df = self._minute_frame(rows, None, datetime.now())
            if not df.is_empty():
                out.append(df.sort("datetime").group_by("symbol").tail(count))
        return pl.concat(out, how="diagonal_relaxed") if out else pl.DataFrame(schema=_MINUTE_SCHEMA)

    # ---- financial ----
    def get_financials(
        self,
        table: str,
        symbols: list[str],
        latest_only: bool = True,
    ) -> pl.DataFrame:
        """财务表 → 内部 canonical 列 (symbol/period_end/announce_date/指标)。

        GM 财务函数均为单标的; 桥接内逐标的循环 + 节流。
        shares 经 daily_basic 股本端点 (circ_shr→float_shares)。
        """
        if not symbols:
            return pl.DataFrame()
        gm_of = {g: s for s in symbols if (g := _to_gm(s))}
        if not gm_of:
            return pl.DataFrame()
        if latest_only:
            start = end = None
        else:
            end = date.today().isoformat()
            start = (date.today() - timedelta(days=730)).isoformat()
        try:
            if table == "income":
                rows = run_op("fin_batch", {"kind": "income", "symbols": sorted(gm_of),
                                            "fields": ",".join(_INCOME_MAP),
                                            "start_date": start, "end_date": end},
                              timeout_s=600.0)
                return self._fin_frame(rows, gm_of, _INCOME_MAP)
            if table == "balance_sheet":
                rows = run_op("fin_batch", {"kind": "balance_sheet", "symbols": sorted(gm_of),
                                            "fields": ",".join(_BALANCE_MAP),
                                            "start_date": start, "end_date": end},
                              timeout_s=600.0)
                return self._fin_frame(rows, gm_of, _BALANCE_MAP)
            if table == "cash_flow":
                rows = run_op("fin_batch", {"kind": "cash_flow", "symbols": sorted(gm_of),
                                            "fields": ",".join(_CASHFLOW_MAP),
                                            "start_date": start, "end_date": end},
                              timeout_s=600.0)
                return self._fin_frame(rows, gm_of, _CASHFLOW_MAP)
            if table == "metrics":
                prime = run_op("fin_batch", {"kind": "prime", "symbols": sorted(gm_of),
                                             "fields": _PRIME_FIELDS,
                                             "start_date": start, "end_date": end},
                               timeout_s=600.0)
                deriv = run_op("fin_batch", {"kind": "deriv", "symbols": sorted(gm_of),
                                             "fields": _DERIV_FIELDS,
                                             "start_date": start, "end_date": end},
                               timeout_s=600.0)
                return self._metrics_frame(prime, deriv, gm_of)
            if table == "shares":
                rows = run_op("fin_batch", {"kind": "basic", "symbols": sorted(gm_of),
                                            "fields": "ttl_shr,circ_shr",
                                            "start_date": start, "end_date": end},
                              timeout_s=600.0)
                return self._shares_frame(rows, gm_of)
        except GmError as e:
            logger.warning("GM 财务 %s 拉取失败: %s", table, e)
        return pl.DataFrame()

    @staticmethod
    def _fin_frame(rows: list[dict], gm_of: dict[str, str], fmap: dict[str, str]) -> pl.DataFrame:
        out = []
        for r in rows:
            symbol = gm_of.get(r.get("_gm_symbol") or r.get("symbol") or "")
            period = _beijing_date(r.get("rpt_date"))
            if not symbol or period is None:
                continue
            row = {"symbol": symbol, "period_end": period.isoformat(),
                   "announce_date": _beijing_date(r.get("pub_date"))}
            for src, dst in fmap.items():
                row[dst] = _to_float(r.get(src))
            out.append(row)
        return pl.DataFrame(out, infer_schema_length=None) if out else pl.DataFrame()

    @staticmethod
    def _metrics_frame(prime: list[dict], deriv: list[dict], gm_of: dict[str, str]) -> pl.DataFrame:
        merged: dict[tuple[str, str], dict] = {}
        for r in (*prime, *deriv):
            symbol = gm_of.get(r.get("_gm_symbol") or r.get("symbol") or "")
            period = _beijing_date(r.get("rpt_date"))
            if not symbol or period is None:
                continue
            key = (symbol, period.isoformat())
            cell = merged.setdefault(key, {"symbol": symbol, "period_end": period.isoformat(),
                                           "announce_date": _beijing_date(r.get("pub_date"))})
            for src, dst in _METRICS_MAP.items():
                if src in r and cell.get(dst) is None:
                    cell[dst] = _to_float(r.get(src))
        rows = list(merged.values())
        return pl.DataFrame(rows, infer_schema_length=None) if rows else pl.DataFrame()

    @staticmethod
    def _shares_frame(rows: list[dict], gm_of: dict[str, str]) -> pl.DataFrame:
        out = []
        for r in rows:
            symbol = gm_of.get(r.get("_gm_symbol") or r.get("symbol") or "")
            period = _beijing_date(r.get("trade_date"))
            if not symbol or period is None:
                continue
            out.append({"symbol": symbol, "period_end": period.isoformat(),
                        "total_shares": _to_float(r.get("ttl_shr")),
                        "float_shares": _to_float(r.get("circ_shr"))})
        return pl.DataFrame(out, infer_schema_length=None) if out else pl.DataFrame()

    # ---- trading calendar ----
    def trading_days(self) -> set:
        """近两年交易日集合 (供交易日探针)。失败抛 GmError, 由探针兜为未知。"""
        year = datetime.now().year
        rows = run_op("trading_dates", {"exchange": "SHSE",
                                        "start_year": year - 1, "end_year": year},
                      timeout_s=60.0)
        return {d for r in rows if (d := _beijing_date(r.get("trade_date"))) is not None}

    # ---- 设置页试拉 ----
    def test_dataset(self, dataset: str, symbols: list[str] | None = None) -> dict:
        if dataset in ("daily", "adj_factor"):
            syms = [s for s in (symbols or [])][:3] or ["000001.SZ"]
            try:
                if dataset == "daily":
                    df = self.get_daily(syms, datetime.now() - timedelta(days=30), datetime.now())
                else:
                    df = self.get_adj_factors(syms, datetime.now() - timedelta(days=365), datetime.now())
            except GmError as e:
                return {"provider": self.name, "dataset": dataset, "rows": 0, "error": str(e)}
            return {"provider": self.name, "dataset": dataset, "rows": df.height,
                    "columns": df.columns, "preview": _preview(df)}
        if dataset in ("minute", "full_minute"):
            syms = [s for s in (symbols or [])][:3] or ["600519.SH"]
            try:
                end = datetime.now()
                df = (self.get_intraday_batch(syms) if dataset == "full_minute"
                      else self.get_minute(syms, end - timedelta(days=3), end))
            except GmError as e:
                return {"provider": self.name, "dataset": dataset, "rows": 0, "error": str(e)}
            return {"provider": self.name, "dataset": dataset, "rows": df.height,
                    "columns": df.columns, "preview": _preview(df)}
        if dataset in ("realtime", "depth5"):
            syms = [s for s in (symbols or [])][:5] or ["600519.SH", "000001.SZ"]
            gm_syms = [g for s in syms if (g := _to_gm(s))]
            try:
                rows = run_op("current", {"symbols": gm_syms}, timeout_s=60.0)
            except GmError as e:
                return {"provider": self.name, "dataset": dataset, "rows": 0, "error": str(e)}
            fallback_ms = int(time.time() * 1000)
            if dataset == "realtime":
                head = [r for r in (self._map_tick(row, fallback_ms) for row in rows) if r]
                return {"provider": self.name, "dataset": dataset, "rows": len(head),
                        "columns": list(head[0].keys()) if head else [], "preview": head[:5]}
            gm_of = {g: s for s in syms if (g := _to_gm(s))}
            depths = {}
            for row in rows:
                internal = gm_of.get(row.get("symbol"))
                entry = self._depth_entry(row, fallback_ms)
                if internal and entry is not None:
                    depths[internal] = entry
            return {"provider": self.name, "dataset": dataset, "rows": len(depths),
                    "columns": ["symbol", "bid_prices", "bid_volumes", "ask_prices", "ask_volumes", "timestamp"],
                    "preview": [{"symbol": c, **d} for c, d in list(depths.items())[:5]]}
        if dataset == "financial":
            syms = [s for s in (symbols or [])][:1] or ["600519.SH"]
            try:
                df = self.get_financials("metrics", syms, latest_only=True)
            except GmError as e:
                return {"provider": self.name, "dataset": dataset, "rows": 0, "error": str(e)}
            return {"provider": self.name, "dataset": dataset, "rows": df.height,
                    "columns": df.columns, "preview": _preview(df)}
        return {"provider": self.name, "dataset": dataset, "rows": 0,
                "error": f"gm 插件未接入 {dataset} 数据集(自动回退 TickFlow)"}


def _preview(df: pl.DataFrame) -> list[dict]:
    head = df.head(5).to_dicts()
    for row in head:  # date/datetime → ISO 字符串, 保证 JSON 可序列化
        for k, v in list(row.items()):
            if isinstance(v, (date, datetime)):
                row[k] = v.isoformat()
    return head


__all__ = [
    "API_KEY_ENV",
    "GmProvider",
    "availability",
    "get_api_key",
    "probe_api_key",
]
