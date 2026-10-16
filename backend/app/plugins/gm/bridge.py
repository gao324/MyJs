"""GM SDK 桥接脚本 — 只在独立 gm venv 中运行 (stdlib + gm + pandas)。

协议: argv[1] 为请求 JSON 文件 {token, op, params};
      结果 JSON 打到 stdout ({"ok": true, "rows": [...]} 或 {"ok": false, "error": ...})。
批量循环在本进程内完成 (摊销一次 spawn 开销), 每次 GM 调用后小节流。
行内 datetime 经 to_json 转 ISO 字符串, NaN 转 null, 后端按契约解析。
"""
from __future__ import annotations

import json
import sys
import time

_THROTTLE_S = 0.05


def _frame_rows(df) -> list[dict]:
    if df is None or len(df) == 0:
        return []
    return json.loads(df.to_json(orient="records", date_format="iso"))


def _dict_rows(rows) -> list[dict]:
    return json.loads(json.dumps(rows or [], default=str))


def main() -> None:
    try:
        with open(sys.argv[1], encoding="utf-8") as f:
            req = json.load(f)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"bad request: {e}"}))
        return

    try:
        from gm.api import (
            ADJUST_NONE,
            current,
            get_history_symbol,
            get_symbol_infos,
            get_symbols,
            get_trading_dates_by_year,
            history,
            set_serv_addr,
            set_token,
            stk_get_daily_basic,
            stk_get_finance_deriv,
            stk_get_finance_prime,
            stk_get_fundamentals_balance,
            stk_get_fundamentals_cashflow,
            stk_get_fundamentals_income,
        )
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"gm import failed: {e}"}))
        return

    try:
        set_token(req.get("token") or "")
        serv = (req.get("serv_addr") or "").strip()
        if serv:
            set_serv_addr(serv)
        op = req.get("op")
        p = req.get("params") or {}
        rows: list[dict] = []

        if op == "history":
            for i, sym in enumerate(p["symbols"]):
                if i:
                    time.sleep(_THROTTLE_S)
                df = history(
                    symbol=sym, frequency=p.get("frequency", "1d"),
                    start_time=p.get("start"), end_time=p.get("end"),
                    adjust=ADJUST_NONE, df=True,
                )
                for r in _frame_rows(df):
                    r["_gm_symbol"] = sym
                    rows.append(r)
        elif op == "current":
            for i in range(0, len(p["symbols"]), 500):
                if i:
                    time.sleep(_THROTTLE_S)
                rows.extend(_dict_rows(current(symbols=p["symbols"][i:i + 500])))
        elif op == "universe":
            rows = _frame_rows(get_symbols(
                sec_type1=p["sec_type1"], sec_type2=p.get("sec_type2"), df=True,
            ))
        elif op == "symbol_infos":
            rows = _dict_rows(get_symbol_infos(
                sec_type1=p["sec_type1"], sec_type2=p.get("sec_type2"),
                symbols=p.get("symbols"),
            ))
        elif op == "history_symbol":
            rows = _frame_rows(get_history_symbol(
                symbol=p["symbol"], start_date=p.get("start_date"),
                end_date=p.get("end_date"), df=True,
            ))
        elif op == "trading_dates":
            rows = _frame_rows(get_trading_dates_by_year(
                exchange=p.get("exchange", "SHSE"),
                start_year=p["start_year"], end_year=p["end_year"],
            ))
        elif op == "fin_batch":
            fns = {
                "income": stk_get_fundamentals_income,
                "balance_sheet": stk_get_fundamentals_balance,
                "cash_flow": stk_get_fundamentals_cashflow,
                "prime": stk_get_finance_prime,
                "deriv": stk_get_finance_deriv,
                "basic": stk_get_daily_basic,
            }
            fn = fns[p["kind"]]
            for i, sym in enumerate(p["symbols"]):
                if i:
                    time.sleep(_THROTTLE_S)
                try:
                    df = fn(symbol=sym, fields=p["fields"],
                            start_date=p.get("start_date"), end_date=p.get("end_date"),
                            df=True)
                except Exception:  # noqa: BLE001 — 单标的失败跳过, 不整批失败
                    continue
                for r in _frame_rows(df):
                    r["_gm_symbol"] = sym
                    rows.append(r)
        else:
            print(json.dumps({"ok": False, "error": f"unknown op: {op}"}))
            return
        print(json.dumps({"ok": True, "rows": rows}))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(e)[:500]}))


if __name__ == "__main__":
    main()
