"""Shared mixin for LLM adapter base classes."""
from __future__ import annotations

from collections.abc import AsyncGenerator

from .types import ChatRequest


class StreamFallbackMixin:
    """Provides default chat_stream*() methods that wrap the blocking chat() call.

    Adapters that support native token streaming (e.g. OpenAI) should override
    chat_stream_events() directly. All others get this safe fallback automatically.
    """

    async def chat_stream_events(self, request: ChatRequest) -> AsyncGenerator[dict, None]:
        """Yield the full response as a single event (no native streaming).

        Emits a single content event followed by a done event with tokens.
        """
        from .types import ChatResponse  # avoid circular at module level
        response: ChatResponse = await self.chat(request)  # type: ignore[attr-defined]
        content = response.content or ""
        if content:
            yield {"type": "content", "text": content}
        yield {
            "type": "done",
            "content": content,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
        }

    async def chat_stream(self, request: ChatRequest) -> AsyncGenerator[str, None]:
        """Yield the full response as a single chunk — no native streaming required."""
        async for event in self.chat_stream_events(request):
            if event["type"] == "content":
                yield event["text"]
