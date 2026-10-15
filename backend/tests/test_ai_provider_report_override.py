"""研报 AI 端点覆盖: 缺省回退全局、覆盖生效、JSON mode 被拒自动去参。"""
from __future__ import annotations

import asyncio

from app.services import ai_provider, report_ai_config


def test_resolve_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr(report_ai_config, "_get", lambda key, default="": default)
    assert report_ai_config.resolve() is None


def test_resolve_maps_configured_values(monkeypatch):
    values = {
        "report_ai_base_url": "https://ark.example.com/api/v3",
        "report_ai_model": "deepseek-v4.1-flash",
        "report_ai_api_key": "sk-test",
    }
    monkeypatch.setattr(report_ai_config, "_get", lambda key, default="": values.get(key, default))
    override = report_ai_config.resolve()
    assert override is not None
    assert override.model == "deepseek-v4.1-flash"
    assert override.base_url.endswith("/api/v3")
    assert override.api_key == "sk-test"


def test_kwargs_carry_response_format_only_when_requested():
    assert "response_format" not in ai_provider._openai_kwargs(temperature=0.2, max_tokens=100)
    kwargs = ai_provider._openai_kwargs(
        temperature=0.2, max_tokens=100, response_format={"type": "json_object"})
    assert kwargs["response_format"] == {"type": "json_object"}


def test_retry_kwargs_drops_response_format_on_400():
    class _BadRequestError(Exception):
        def __init__(self) -> None:
            super().__init__("bad request")
            self.status_code = 400
            self.body = {"error": {"param": "response_format",
                                   "message": "json mode unsupported"}}

    kwargs = {"max_tokens": 100, "response_format": {"type": "json_object"}}
    retried = ai_provider._openai_retry_kwargs(_BadRequestError(), kwargs)
    assert retried is not None and "response_format" not in retried


def test_retry_kwargs_keeps_response_format_on_other_params():
    class _BadRequestError(Exception):
        def __init__(self) -> None:
            super().__init__("bad request")
            self.status_code = 400
            self.body = {"error": {"param": "model", "message": "unknown model"}}

    kwargs = {"max_tokens": 100, "response_format": {"type": "json_object"}}
    assert ai_provider._openai_retry_kwargs(_BadRequestError(), kwargs) is None


def test_override_drives_client_model_and_json_mode(monkeypatch):
    from types import SimpleNamespace

    seen: dict[str, object] = {}

    async def _create(**kwargs):
        seen["model"] = kwargs["model"]
        seen["response_format"] = kwargs.get("response_format")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_create)))

    def _fake_client(api_key, timeout, *, base_url=""):
        seen["key"] = api_key
        seen["base_url"] = base_url
        return client

    monkeypatch.setattr(ai_provider, "_openai_client", _fake_client)
    monkeypatch.setattr(ai_provider, "current_ai_model", lambda: "global-model")
    monkeypatch.setattr(ai_provider, "_resolve_max_tokens", lambda v: v)
    monkeypatch.setattr(ai_provider, "_check_input_budget", lambda *a, **k: None)

    override = ai_provider.AiEndpointOverride(
        base_url="https://ark.example.com/api/v3", api_key="k", model="deepseek-v4.1-flash")
    out = asyncio.run(ai_provider.generate_ai_text(
        [{"role": "user", "content": "hi"}], temperature=0.2, max_tokens=100,
        response_format={"type": "json_object"}, ai_config=override))

    assert out == "{}"
    assert seen == {
        "key": "k",
        "base_url": "https://ark.example.com/api/v3",
        "model": "deepseek-v4.1-flash",
        "response_format": {"type": "json_object"},
    }
