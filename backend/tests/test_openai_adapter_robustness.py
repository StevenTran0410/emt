"""Adapter robustness: an OpenAI-compatible provider (e.g. DeepSeek via OpenRouter) may return
`content: null`. The adapter must coerce that to a string, never crash ChatResponse validation."""
from __future__ import annotations

import pytest

from domain.model_connector.openai.adapter import OpenAIAdapter
from domain.model_connector.types import ChatMessage, ChatRequest, ProviderConfig, ProviderKind


def _adapter() -> OpenAIAdapter:
    cfg = ProviderConfig(
        id="p1", kind=ProviderKind.OPENROUTER, display_name="t",
        base_url="https://openrouter.ai/api/v1", model_id="deepseek/deepseek-v4-flash",
        extra={"api_key": "k"},
    )
    return OpenAIAdapter(cfg)


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


@pytest.mark.asyncio
async def test_null_content_coerced_to_reasoning_then_empty(monkeypatch):
    adapter = _adapter()
    req = ChatRequest(provider_id="p1", messages=[ChatMessage(role="user", content="hi")], max_completion_tokens=100)

    # content=null with reasoning_content present -> use reasoning_content.
    async def fake_post_reasoning(*a, **k):
        return _FakeResp({"choices": [{"message": {"content": None, "reasoning_content": "R"}}]})

    monkeypatch.setattr(adapter._client, "post", fake_post_reasoning)
    resp = await adapter.chat(req)
    assert resp.content == "R"

    # content=null with no reasoning -> empty string, no crash.
    async def fake_post_null(*a, **k):
        return _FakeResp({"choices": [{"message": {"content": None}}]})

    monkeypatch.setattr(adapter._client, "post", fake_post_null)
    resp2 = await adapter.chat(req)
    assert resp2.content == ""
