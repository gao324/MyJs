"""股息率运行时列 (GM 截面) 的接线测试 — run / run_preset / run_all / cached / cached-result。

契约 (透传口径见 test_screener_dividend_yield.py):
- 结果行与「今日曾命中」行同时带两个运行时列: dividend_yield (GM dy_lfy) 与
  dividend_yield_ttm (GM dy_ttm), 与 as_of 无关 (展示口径);
- 两列都是响应期投影, 不写进 strategy_cache, 也不修改调用方传入的行;
- 快照无该标的/缺失 → 一律 None (fail-closed, 前端显示「—」)。
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.api import screener as screener_api
from app.services import strategy_cache
from app.services.screener import ScreenerResult

AS_OF = date(2026, 10, 15)
MAPS = {
    "lfly": {"000001.SZ": 0.5, "000002.SZ": 0.15},
    "ttm": {"000001.SZ": 0.8, "000002.SZ": 0.25},
}


class _Svc:
    def latest_date(self):
        return AS_OF

    def build_strategy_context(self, engine, as_of, strategy_ids, **kwargs):
        return SimpleNamespace(as_of=as_of)

    def coverage_warnings(self, as_of, *, required_bars=None):
        return []


class _Engine:
    def __init__(self, rows_by_strategy):
        self._rows = rows_by_strategy

    def has(self, strategy_id):
        return strategy_id in self._rows

    def get(self, strategy_id):
        return SimpleNamespace(meta={"id": strategy_id})

    def run(self, strategy_id, context, **kwargs):
        return ScreenerResult(
            as_of=context.as_of, strategy=strategy_id, rows=[dict(r) for r in self._rows[strategy_id]],
            total=len(self._rows[strategy_id]),
        )

    def run_all(self, context, params_map=None, overrides_map=None, strategy_ids=None, **kwargs):
        return {
            sid: ScreenerResult(
                as_of=context.as_of, strategy=sid, rows=[dict(r) for r in self._rows[sid]],
                total=len(self._rows[sid]),
            )
            for sid in (strategy_ids or list(self._rows))
        }


def _request(tmp_path, engine, monitor_results=None):
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    state = SimpleNamespace(
        repo=repo,
        strategy_engine=engine,
        monitor_engine=SimpleNamespace(latest_strategy_results=lambda: monitor_results or {}),
    )
    return SimpleNamespace(app=SimpleNamespace(state=state))


def _patch(monkeypatch):
    monkeypatch.setattr(screener_api, "ScreenerService", lambda *a, **k: _Svc())
    monkeypatch.setattr(screener_api, "_load_ext_value_maps", lambda *a, **k: {})
    monkeypatch.setattr(screener_api.strategy_config, "load_override", lambda *a: {})
    monkeypatch.setattr(screener_api, "_gm_dividend_yield_maps", lambda: MAPS)


def test_run_preset_projects_both_yield_columns_without_polluting_cache(monkeypatch, tmp_path):
    """单跑响应带两个股息率列; 落缓存的 payload 保持无这两列 (展示口径不入库)。"""
    _patch(monkeypatch)
    engine = _Engine({"s1": [{"symbol": "000001.SZ", "raw_close": 10.0},
                             {"symbol": "000009.SZ", "raw_close": 3.0}]})
    # 已有同日缓存 (盘后 run_all 写过) —— 单跑只更新这一条
    strategy_cache.write_cache(
        tmp_path, AS_OF.isoformat(),
        {"s1": {"total": 0, "as_of": AS_OF.isoformat(), "rows": []}},
    )

    resp = screener_api.run_preset(
        screener_api.PresetRequest(strategy_id="s1", as_of=AS_OF),
        _request(tmp_path, engine),
    )

    assert [(r["symbol"], r["dividend_yield"], r["dividend_yield_ttm"]) for r in resp["rows"]] == [
        ("000001.SZ", 0.5, 0.8),
        ("000009.SZ", None, None),  # 快照无该标的 → None, 不是 0
    ]

    written = strategy_cache.read_cache(tmp_path)["results"]["s1"]["rows"]
    assert [row["symbol"] for row in written] == ["000001.SZ", "000009.SZ"]
    assert all("dividend_yield" not in row and "dividend_yield_ttm" not in row for row in written)


def test_run_all_projects_both_yield_columns_per_strategy(monkeypatch, tmp_path):
    _patch(monkeypatch)
    engine = _Engine({
        "s1": [{"symbol": "000001.SZ", "raw_close": 10.0}],
        "s2": [{"symbol": "000002.SZ", "raw_close": 5.0}],
    })

    resp = screener_api.run_all(
        _request(tmp_path, engine),
        {"as_of": AS_OF.isoformat(), "strategy_ids": ["s1", "s2"], "asset_type": "stock", "timeframe": "1d"},
    )

    assert resp["results"]["s1"]["rows"][0]["dividend_yield"] == 0.5
    assert resp["results"]["s1"]["rows"][0]["dividend_yield_ttm"] == 0.8
    assert resp["results"]["s2"]["rows"][0]["dividend_yield"] == 0.15
    assert resp["results"]["s2"]["rows"][0]["dividend_yield_ttm"] == 0.25
    assert all(
        "dividend_yield" not in row and "dividend_yield_ttm" not in row
        for r in strategy_cache.read_cache(tmp_path)["results"].values()
        for row in r["rows"]
    )


def test_cached_projects_results_and_ever_rows(monkeypatch, tmp_path):
    """缓存读取: 结果行与今日曾命中行都带两列 (与行日期无关)。"""
    _patch(monkeypatch)
    cached = {
        "as_of": AS_OF.isoformat(),
        "results": {
            "s1": {"as_of": AS_OF.isoformat(), "total": 1,
                   "rows": [{"symbol": "000001.SZ", "raw_close": 10.0}]},
        },
        "today_ever_rows": {
            "s1": {
                "000001.SZ": {"symbol": "000001.SZ", "raw_close": 10.0, "date": AS_OF.isoformat()},
                "000002.SZ": {"symbol": "000002.SZ", "raw_close": 5.0, "date": "2026-10-14"},
            },
        },
        "updated_at": 1,
    }
    monkeypatch.setattr(screener_api.strategy_cache, "read_cache", lambda *_a: cached)

    payload = screener_api.get_cached(
        _request(tmp_path, _Engine({})), ext_columns=None,
    )

    assert payload["results"]["s1"]["rows"][0]["dividend_yield"] == 0.5
    assert payload["results"]["s1"]["rows"][0]["dividend_yield_ttm"] == 0.8
    assert payload["today_ever_rows"]["s1"]["000001.SZ"]["dividend_yield"] == 0.5
    assert payload["today_ever_rows"]["s1"]["000002.SZ"]["dividend_yield_ttm"] == 0.25
    # 源缓存 payload 不被就地改写 (read_cache 对象可能被监控实时结果共享)
    assert "dividend_yield" not in cached["results"]["s1"]["rows"][0]
    assert "dividend_yield_ttm" not in cached["today_ever_rows"]["s1"]["000001.SZ"]


def test_cached_with_missing_snapshot_degrades_to_none(monkeypatch, tmp_path):
    """快照不可用 (GM 未同步) → 两列一律 None, 不伪造数值。"""
    _patch(monkeypatch)
    monkeypatch.setattr(screener_api, "_gm_dividend_yield_maps",
                        lambda: {"lfly": {}, "ttm": {}})
    cached = {
        "as_of": AS_OF.isoformat(),
        "results": {"s1": {"as_of": AS_OF.isoformat(), "total": 1,
                           "rows": [{"symbol": "000001.SZ", "raw_close": 10.0}]}},
        "today_ever_rows": {},
        "updated_at": 1,
    }
    monkeypatch.setattr(screener_api.strategy_cache, "read_cache", lambda *_a: cached)

    payload = screener_api.get_cached(_request(tmp_path, _Engine({})), ext_columns=None)

    assert payload["results"]["s1"]["rows"][0]["dividend_yield"] is None
    assert payload["results"]["s1"]["rows"][0]["dividend_yield_ttm"] is None
