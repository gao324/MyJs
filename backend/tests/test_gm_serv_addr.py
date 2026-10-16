"""GM 容器化终端地址: 探测、请求透传与桥接 set_serv_addr 契约。"""
from __future__ import annotations

import json
import sys
import types

from app.plugins.gm import bridge
from app.plugins.gm import client as gm_client


def test_gm_serv_addr_env_override(monkeypatch):
    monkeypatch.setenv("GM_SERV_ADDR", "10.0.0.5:7001")
    assert gm_client._gm_serv_addr() == "10.0.0.5:7001"


def test_gm_serv_addr_container_autodetect(monkeypatch):
    monkeypatch.delenv("GM_SERV_ADDR", raising=False)

    class _FakePath:
        def __init__(self, _p):
            pass

        def exists(self):
            return True

    monkeypatch.setattr(gm_client, "Path", _FakePath)
    monkeypatch.setattr(gm_client.socket, "gethostbyname", lambda _host: "192.168.65.254")
    assert gm_client._gm_serv_addr() == "host.docker.internal:7001"


def test_gm_serv_addr_absent_outside_container(monkeypatch):
    monkeypatch.delenv("GM_SERV_ADDR", raising=False)

    class _FakePath:
        def __init__(self, _p):
            pass

        def exists(self):
            return False

    monkeypatch.setattr(gm_client, "Path", _FakePath)
    assert gm_client._gm_serv_addr() is None


def test_run_op_passes_serv_addr_to_bridge(monkeypatch):
    captured: dict = {}

    class _Proc:
        returncode = 0
        stdout = '{"ok": true, "rows": []}'
        stderr = ""

    def _fake_run(argv, **_kwargs):
        with open(argv[-1], encoding="utf-8") as f:
            captured["req"] = json.loads(f.read())
        return _Proc()

    monkeypatch.setattr(gm_client, "ensure_venv", lambda *a, **k: None)
    monkeypatch.setattr(gm_client, "get_api_key", lambda: "k")
    monkeypatch.setattr(gm_client, "_gm_serv_addr", lambda: "host.docker.internal:7001")
    monkeypatch.setattr(gm_client.subprocess, "run", _fake_run)
    gm_client.run_op("current", {"symbols": ["SHSE.600519"]})
    assert captured["req"]["serv_addr"] == "host.docker.internal:7001"


def test_bridge_applies_serv_addr(monkeypatch, tmp_path, capsys):
    calls: dict = {}
    fake_gm = types.ModuleType("gm")
    fake_api = types.ModuleType("gm.api")
    fake_api.ADJUST_NONE = 0
    fake_api.set_token = lambda token: calls.__setitem__("token", token)
    fake_api.set_serv_addr = lambda addr: calls.__setitem__("serv", addr)
    fake_api.current = lambda symbols: [{"symbol": symbols[0]}]
    for name in ("get_history_symbol", "get_symbol_infos", "get_symbols",
                 "get_trading_dates_by_year", "history", "stk_get_daily_basic",
                 "stk_get_finance_deriv", "stk_get_finance_prime",
                 "stk_get_fundamentals_balance", "stk_get_fundamentals_cashflow",
                 "stk_get_fundamentals_income"):
        setattr(fake_api, name, lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "gm", fake_gm)
    monkeypatch.setitem(sys.modules, "gm.api", fake_api)

    req = tmp_path / "req.json"
    req.write_text(json.dumps({
        "token": "k", "op": "current", "serv_addr": "host.docker.internal:7001",
        "params": {"symbols": ["SHSE.600519"]},
    }), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["bridge.py", str(req)])
    bridge.main()

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert calls["serv"] == "host.docker.internal:7001"
    assert calls["token"] == "k"
