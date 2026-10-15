"""研报管线专用 AI 端点配置: REPORT_AI_* 逐项缺省回退全局 AI 配置。

三项全空 → 返回 None, 由 ai_provider 走全局配置(与旧行为一致);
任一项非空 → 返回覆盖对象, 只覆盖显式配置的字段。
"""
from __future__ import annotations

from app import secrets_store
from app.config import settings
from app.services.ai_provider import AiEndpointOverride


def _get(key: str, default: str = "") -> str:
    return (secrets_store.get_ai_config(key, default) or "").strip()


def resolve() -> AiEndpointOverride | None:
    """解析研报端点覆盖; 全空返回 None(回退全局配置)。"""
    base_url = _get("report_ai_base_url") or str(
        getattr(settings, "report_ai_base_url", "") or "")
    model = _get("report_ai_model") or str(getattr(settings, "report_ai_model", "") or "")
    api_key = _get("report_ai_api_key")
    if not (base_url or model or api_key):
        return None
    return AiEndpointOverride(
        base_url=base_url.strip(), model=model.strip(), api_key=api_key.strip())
