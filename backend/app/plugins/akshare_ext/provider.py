"""AKShare 研报扩展数据源 — 研报管线专用数据集(非 TickFlow 能力)。

数据集契约(本仓库定义, 消费方 = 个股研报管线):
- research / shareholders / fund_flow / company_info:
  返回 {块名: [记录, ...]}, 记录列名保持 akshare 原始中文列(与 stock_ai
  Phase 1 落盘块逐列对齐, 保证报告一致性); 缺失块返回 [], 不抛异常。
- 数值一律经 _clean 清洗(非有限值 → None), JSON 缓存内无 NaN/Infinity。
"""
from __future__ import annotations

import datetime as dt
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


def _import_ak():
    """惰性导入 akshare(未安装时由 availability 提前拦截)。"""
    import akshare

    return akshare


def _split_symbol(symbol: str) -> tuple[str, str]:
    """600519.SH → ("600519", "sh"); 非法输入抛 ValueError(fail-closed)。"""
    text = str(symbol or "").strip().upper()
    if "." not in text:
        raise ValueError(f"symbol 需带交易所后缀(如 600519.SH), 实际 {symbol!r}")
    code, _, suffix = text.partition(".")
    if len(code) != 6 or suffix not in ("SH", "SZ", "BJ"):
        raise ValueError(f"symbol 格式非法: {symbol!r}")
    return code, suffix.lower()


def _safe(fn, label: str) -> Any:
    """接口异常降级为空结果 + 告警(绝不中断整包采集)。"""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[akshare_ext] %s 采集失败(%s), 该块置空", label, type(exc).__name__)
        return []


# ---------------------------------------------------------------- 日期与报告期
# 与 stock_ai stock_full_report.py 同口径(纯函数, 无网络): 交易日回退、披露驱动
# 报告期(季报披露有时滞, 不能直接用日历季度)。

def _last_trade_day(d: dt.date) -> dt.date:
    """往前回退到最近的交易日(周末跳过; 与 stock_ai 同口径, 不含节假日表)。"""
    d = d - dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def _prev_quarter_end(end: dt.date) -> dt.date:
    """返回上一个季末日期。"""
    prev = {3: dt.date(end.year - 1, 12, 31), 6: dt.date(end.year, 3, 31),
            9: dt.date(end.year, 6, 30), 12: dt.date(end.year, 9, 30)}
    return prev[end.month]


def _parse_period(period: str) -> dt.date:
    """'20260630' -> date(2026, 6, 30)。"""
    return dt.datetime.strptime(period, "%Y%m%d").date()


def _disclosure_deadline(end: dt.date) -> dt.date:
    """报告期对应的法定披露截止日：一季报→4/30，半年报→8/31，三季报→10/31，年报→次年4/30。"""
    deadline = {3: dt.date(end.year, 4, 30), 6: dt.date(end.year, 8, 31),
                9: dt.date(end.year, 10, 31), 12: dt.date(end.year + 1, 4, 30)}
    return deadline[end.month]


def latest_quarter_end(today: dt.date) -> str:
    """最近一个"可取数据"的季末(YYYYMMDD); 季末当天与次日仍取上一季末。"""
    year = today.year
    ends = [dt.date(year, 3, 31), dt.date(year, 6, 30), dt.date(year, 9, 30),
            dt.date(year, 12, 31), dt.date(year - 1, 12, 31)]
    end = max(e for e in ends if e <= today)
    if today <= end + dt.timedelta(days=1):
        end = _prev_quarter_end(end)
    return end.strftime("%Y%m%d")


def last_report_periods(today: dt.date, n: int = 4) -> list[str]:
    """最近 n 个"已过披露窗口"的报告期(YYYYMMDD, 由近及远)。"""
    end = _parse_period(latest_quarter_end(today))
    while _disclosure_deadline(end) >= today:
        end = _prev_quarter_end(end)
    periods: list[str] = []
    for _ in range(n):
        periods.append(end.strftime("%Y%m%d"))
        end = _prev_quarter_end(end)
    return periods


def candidate_periods(today: dt.date, n: int = 3) -> list[str]:
    """披露驱动接口的候选报告期(由近及远, 第一个必为「最近已披露」报告期)。"""
    return last_report_periods(today, max(1, int(n)))


def report_dates(today: dt.date | None = None, kline_years: int = 3) -> dict[str, str]:
    """研报采集所需的全部日期字符串(与 stock_ai collect() 同口径)。

    today_s: 当日 | last_trade_prev_s: 上一个交易日(融资融券用, T+1 披露) |
    kline_start: K 线起始日 | month_ago_s: 龙虎榜窗口起点(40 天)。
    """
    today = today or dt.date.today()
    last_trade = _last_trade_day(today + dt.timedelta(days=1))
    last_trade_prev = _last_trade_day(last_trade)
    return {
        "today_s": today.strftime("%Y%m%d"),
        "last_trade_prev_s": last_trade_prev.strftime("%Y%m%d"),
        "kline_start": (today - dt.timedelta(days=int(365.25 * kline_years))).strftime("%Y%m%d"),
        "month_ago_s": (today - dt.timedelta(days=40)).strftime("%Y%m%d"),
    }


def _matches_code(value: Any, code: str) -> bool:
    """单值代码匹配(stock_ai _filter_by_code 同规则): 相等或以后缀结尾。"""
    text = str(value or "").strip()
    return bool(text) and (text == code or text.endswith(code))


_CODE_COLUMNS = ("代码", "股票代码", "证券代码", "symbol", "code")


def _filter_records(rows: list[dict], code: str) -> list[dict]:
    """按代码列过滤记录(找不到代码列则原样返回, 与 stock_ai 同语义)。"""
    if not rows:
        return []
    cols = [c for c in _CODE_COLUMNS if any(c in r for r in rows)]
    if not cols:
        return rows
    col = cols[0]
    return [r for r in rows if _matches_code(r.get(col), code)]


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

    def get_research(self, symbol: str) -> dict[str, list[dict]]:
        """研报 / 机构评级 / 基金持仓三块。symbol 形如 600519.SH。

        recommend 是全市场表, 按代码过滤(与 stock_ai _filter_by_code 同规则);
        fund_hold 按披露驱动候选报告期逐个回退, 取首个非空。
        """
        ak = _import_ak()
        code, _market = _split_symbol(symbol)
        blocks: dict[str, list[dict]] = {"research": [], "recommend": [], "fund_hold": []}
        blocks["research"] = _safe(
            lambda: _records(ak.stock_research_report_em(symbol=code)), "research")
        recommend = _safe(
            lambda: _records(ak.stock_institute_recommend(symbol="股票综合评级")), "recommend")
        blocks["recommend"] = _filter_records(recommend, code)
        for period in candidate_periods(dt.date.today(), 3):
            rows = _safe(lambda p=period: _records(
                ak.stock_report_fund_hold_detail(symbol=code, date=p)), "fund_hold")
            if rows:
                blocks["fund_hold"] = rows
                break
        return blocks

    def get_shareholders(self, symbol: str) -> dict[str, list[dict]]:
        """股东/股本六块: 十大股东与十大流通股东是两个独立块(各走披露报告期回退,
        不互为回退——与 stock_ai 的 top10 / top10_free 双 submit 一致)。"""
        ak = _import_ak()
        code, market = _split_symbol(symbol)
        blocks: dict[str, list[dict]] = {
            "top10": [], "top10_free": [], "gdhs": [], "share_structure": [],
            "release": [], "share_hold_change": [],
        }
        periods = candidate_periods(dt.date.today(), 3)
        for key, fetch in (("top10", ak.stock_gdfx_top_10_em),
                           ("top10_free", ak.stock_gdfx_free_top_10_em)):
            for period in periods:
                rows = _safe(lambda p=period, f=fetch: _records(
                    f(symbol=f"{market}{code}", date=p)), key)
                if rows:
                    blocks[key] = rows
                    break
        blocks["gdhs"] = _safe(lambda: _records(
            ak.stock_zh_a_gdhs_detail_em(symbol=code)), "gdhs")
        blocks["share_structure"] = _safe(lambda: _records(
            ak.stock_zh_a_gbjg_em(symbol=f"{code}.{market.upper()}")), "share_structure")
        blocks["release"] = _safe(lambda: _records(
            ak.stock_restricted_release_queue_em(symbol=code)), "release")
        if market == "sz":
            blocks["share_hold_change"] = _safe(lambda: _records(
                ak.stock_share_hold_change_szse(symbol=code)), "share_hold_change")
        else:
            blocks["share_hold_change"] = _safe(lambda: _records(
                ak.stock_share_hold_change_sse(symbol=code)), "share_hold_change")
        return blocks

    def get_fund_flow(self, symbol: str) -> dict[str, list[dict]]:
        """资金流/融资融券/龙虎榜三块。

        融资融券用上一个交易日(与 stock_ai 的 last_trade_prev_s 同口径);
        融资融券与龙虎榜均按代码过滤; 龙虎榜窗口 40 天。
        """
        ak = _import_ak()
        code, market = _split_symbol(symbol)
        dates = report_dates(dt.date.today())
        blocks: dict[str, list[dict]] = {"fund_flow": [], "margin": [], "lhb": []}
        blocks["fund_flow"] = _safe(lambda: _records(
            ak.stock_individual_fund_flow(stock=code, market=market)), "fund_flow")
        margin_fn = ak.stock_margin_detail_szse if market == "sz" else ak.stock_margin_detail_sse
        margin = _safe(lambda: _records(margin_fn(date=dates["last_trade_prev_s"])), "margin")
        blocks["margin"] = _filter_records(margin, code)
        rows = _safe(lambda: _records(
            ak.stock_lhb_detail_em(start_date=dates["month_ago_s"],
                                   end_date=dates["today_s"])), "lhb")
        blocks["lhb"] = _filter_records(rows, code)
        return blocks

    def get_company_info(self, symbol: str) -> dict[str, list[dict]]:
        """公司信息八块(概况/主营/新闻/公告/业绩预告/快报/分红/送转)。

        basic_info 用大写前缀形式(SH600519, 与 stock_ai 同参); 公告/业绩类按代码
        过滤; 业绩预告与快报各取 4 个已披露报告期并拼接(与 stock_ai 多 submit 合并一致)。
        """
        ak = _import_ak()
        code, market = _split_symbol(symbol)
        today = dt.date.today()
        blocks: dict[str, list[dict]] = {
            "basic_info": [], "zygc": [], "news": [], "notice": [],
            "yjyg": [], "yjkb": [], "dividend": [], "share_alloc": [],
        }
        blocks["basic_info"] = _safe(lambda: _records(
            ak.stock_individual_basic_info_xq(symbol=f"{market.upper()}{code}")), "basic_info")
        blocks["zygc"] = _safe(lambda: _records(
            ak.stock_zygc_em(symbol=f"{market.upper()}{code}")), "zygc")
        blocks["news"] = _safe(lambda: _records(ak.stock_news_em(symbol=code)), "news")
        dates = report_dates(today)
        notice = _safe(lambda: _records(
            ak.stock_notice_report(symbol="全部", date=dates["today_s"])), "notice")
        blocks["notice"] = _filter_records(notice, code)
        for period in last_report_periods(today, 4):
            yjyg = _safe(lambda p=period: _records(ak.stock_yjyg_em(date=p)), "yjyg")
            blocks["yjyg"].extend(_filter_records(yjyg, code))
            yjkb = _safe(lambda p=period: _records(ak.stock_yjkb_em(date=p)), "yjkb")
            blocks["yjkb"].extend(_filter_records(yjkb, code))
        blocks["dividend"] = _safe(lambda: _records(
            ak.stock_history_dividend_detail(symbol=code, indicator="分红")), "dividend")
        blocks["share_alloc"] = _safe(lambda: _records(
            ak.stock_history_dividend_detail(symbol=code, indicator="配股")), "share_alloc")
        return blocks

    # ---- 非路由方法: 研报管线 stock_ai 同源取数 ----
    # 不声明 daily/financial/realtime 数据集, 不进能力矩阵, 不改变全局路由;
    # 先例: dragon_tiger 直接经 custom_sources 取 fuyao provider。

    def get_report_kline(self, symbol: str, years: int = 3) -> list[dict]:
        """日 K(前复权, 3 年窗口), 与 stock_ai stock_zh_a_daily 同源同参。"""
        ak = _import_ak()
        code, market = _split_symbol(symbol)
        dates = report_dates(dt.date.today(), kline_years=years)
        return _safe(lambda: _records(
            ak.stock_zh_a_daily(symbol=f"{market}{code}", start_date=dates["kline_start"],
                                end_date=dates["today_s"], adjust="qfq")), "kline_daily")

    def get_report_financials(self, symbol: str) -> dict[str, list[dict]]:
        """财务摘要/同花顺指标/三大报表五块。"""
        ak = _import_ak()
        code, market = _split_symbol(symbol)
        prefixed = f"{market}{code}"
        blocks: dict[str, list[dict]] = {
            "fin_abstract": [], "fin_indicator_ths": [], "balance_sheet": [],
            "income_statement": [], "cashflow": [],
        }
        blocks["fin_abstract"] = _safe(lambda: _records(
            ak.stock_financial_abstract(symbol=code)), "fin_abstract")
        blocks["fin_indicator_ths"] = _safe(lambda: _records(
            ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期")), "fin_indicator_ths")
        for key, table in (("balance_sheet", "资产负债表"),
                           ("income_statement", "利润表"), ("cashflow", "现金流量表")):
            blocks[key] = _safe(lambda t=table: _records(
                ak.stock_financial_report_sina(stock=prefixed, symbol=t)), key)
        return blocks

    def get_report_spot(self, symbol: str) -> list[dict]:
        """全市场快照 → 该标的单行(先精确匹配带前缀代码, 再按后缀回退, 与 stock_ai 同规则)。"""
        ak = _import_ak()
        code, market = _split_symbol(symbol)
        want = f"{market}{code}"
        rows = _safe(lambda: _records(ak.stock_zh_a_spot()), "spot")
        exact = [r for r in rows if str(r.get("代码") or "").strip().lower() == want]
        if exact:
            return exact
        return [r for r in rows if _matches_code(r.get("代码"), code)]
