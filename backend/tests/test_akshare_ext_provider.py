"""akshare_ext 插件发现与能力声明契约。"""
from __future__ import annotations

import sys
import types

from app.data_providers import custom as custom_sources
from app.data_providers.capabilities import CAPABILITY_REGISTRY, build_capability_matrix
from app.services import preferences

NEW_CAPS = ("research", "shareholders", "fund_flow", "company_info")


def _fake_akshare(monkeypatch):
    """注入最小可用的假 akshare 模块，测试不触发网络。"""
    mod = types.ModuleType("akshare")
    monkeypatch.setitem(sys.modules, "akshare", mod)
    return mod


def _load_plugins(monkeypatch, tmp_path):
    """在隔离的数据源目录里重建插件注册表，返回 name -> 插件状态。"""
    monkeypatch.setattr(custom_sources, "data_sources_dir", lambda: tmp_path)
    custom_sources.load_all()
    return {p["name"]: p for p in custom_sources.list_plugins()}


def test_plugin_declares_four_datasets(monkeypatch, tmp_path):
    _fake_akshare(monkeypatch)
    plugins = _load_plugins(monkeypatch, tmp_path)
    assert "akshare_ext" in plugins
    assert set(plugins["akshare_ext"]["datasets"]) == set(NEW_CAPS)


def test_availability_reports_missing_dependency(monkeypatch):
    from app.plugins.akshare_ext import provider as mod

    monkeypatch.setitem(sys.modules, "akshare", None)  # import akshare -> ImportError
    ok, reason = mod.availability()
    assert ok is False and "akshare" in reason


def test_registry_contains_new_capabilities():
    ids = {c["id"] for c in CAPABILITY_REGISTRY}
    assert set(NEW_CAPS) <= ids
    for cap in CAPABILITY_REGISTRY:
        if cap["id"] in NEW_CAPS:
            assert cap["field"].endswith("_data_provider")
            assert cap["default"] == "akshare_ext"
            assert cap["tf_tier"] == "never"


def test_tickflow_never_candidate_for_new_capabilities(monkeypatch, tmp_path):
    _fake_akshare(monkeypatch)
    _load_plugins(monkeypatch, tmp_path)
    current = {c["field"]: c["default"] for c in CAPABILITY_REGISTRY if c["field"]}
    matrix = build_capability_matrix(current, tickflow_tier="expert")
    for cap in matrix["capabilities"]:
        if cap["id"] in NEW_CAPS:
            assert cap["tf_available"] is False
            assert all(c["name"] != "tickflow" for c in cap["candidates"])
            assert cap["usable"] is True


def test_getters_fall_back_when_provider_missing(monkeypatch):
    monkeypatch.setattr(preferences, "_allowed_data_providers", lambda: set())
    assert preferences.get_research_data_provider() == "akshare_ext"
