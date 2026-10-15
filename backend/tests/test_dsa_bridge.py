"""DSA 问股 agent 桥接测试 - vendor 路径解析、Config 注入与单例短路。

本仓库不复制 vendor/dsa (见 docs/superpowers/specs/2026-10-14-dsa-stock-agent-port-design.md),
因此依赖真实 vendor 的用例在 DSA_VENDOR_DIR 未配置时跳过; 路径解析与 fail-closed
用例始终执行。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import secrets_store
from app.config import settings
from app.services import dsa_bridge


def _vendor_ready() -> bool:
    raw = str(getattr(settings, "dsa_vendor_dir", "") or "").strip()
    return bool(raw) and (Path(raw).expanduser() / "src").is_dir()


needs_vendor = pytest.mark.skipif(
    not _vendor_ready(), reason="DSA_VENDOR_DIR 未配置或无效 (本仓库不复制 vendor/dsa)"
)


def _patch_ai(monkeypatch, *, model: str = "deepseek-v4-flash") -> None:
    monkeypatch.setattr(secrets_store, "get_ai_key", lambda: "test-key")
    monkeypatch.setattr(
        secrets_store,
        "get_ai_config",
        lambda key, default: {
            "ai_model": model,
            "ai_base_url": "https://ark.example/v3",
        }.get(key, default),
    )
    monkeypatch.setattr(secrets_store, "get_tickflow_key", lambda: "")


def test_vendor_dir_unset_fails_closed(monkeypatch) -> None:
    """未配置 DSA_VENDOR_DIR: 报错并给出回退方式, 不静默换引擎。"""
    monkeypatch.setattr(settings, "dsa_vendor_dir", "")
    with pytest.raises(ValueError, match="DSA_VENDOR_DIR"):
        dsa_bridge._vendor_dir()
    with pytest.raises(ValueError, match="STOCK_ANALYSIS_ENGINE=legacy"):
        dsa_bridge._vendor_dir()


def test_vendor_dir_without_src_fails_closed(monkeypatch, tmp_path) -> None:
    """路径存在但不是 DSA vendor 根目录: 明确报错。"""
    monkeypatch.setattr(settings, "dsa_vendor_dir", str(tmp_path))
    with pytest.raises(ValueError, match="src"):
        dsa_bridge._vendor_dir()


@needs_vendor
def test_build_dsa_config_injects_tsp_ai_settings(monkeypatch) -> None:
    """DSA Config 用 TSP 的模型/key/base_url 构造, 走 litellm single-agent。"""
    _patch_ai(monkeypatch)

    cfg = dsa_bridge.build_dsa_config()

    assert cfg.agent_arch == "single"
    assert cfg.agent_mode is True
    assert cfg.generation_backend == "litellm"
    assert cfg.agent_generation_backend == "litellm"
    assert cfg.agent_litellm_model == "openai/deepseek-v4-flash"
    assert cfg.litellm_model == "openai/deepseek-v4-flash"
    assert cfg.openai_api_keys == ["test-key"]
    assert cfg.openai_base_url == "https://ark.example/v3"
    assert cfg.database_path == str(settings.data_dir / "dsa.db")


@needs_vendor
def test_build_dsa_config_model_without_prefix_gets_openai(monkeypatch) -> None:
    """模型名无 provider 前缀时自动补 openai/ (litellm 兼容渠道)。"""
    _patch_ai(monkeypatch, model="gpt-5.5")

    cfg = dsa_bridge.build_dsa_config()

    assert cfg.agent_litellm_model == "openai/gpt-5.5"
    assert cfg.litellm_model == "openai/gpt-5.5"


@needs_vendor
def test_reset_rebuilds_config_singleton(monkeypatch) -> None:
    """reset() 重建 Config 单例, 供 TSP AI 设置变更时调用。"""
    _patch_ai(monkeypatch)

    dsa_bridge._cfg = None
    cfg1 = dsa_bridge.get_dsa_config()
    from src.config import Config

    Config._instance = cfg1

    dsa_bridge.reset()

    assert dsa_bridge._cfg is not None
    assert Config.get_instance() is dsa_bridge._cfg
