"""OpenAI provider adapter."""
from __future__ import annotations

import json as _json
from collections.abc import AsyncGenerator

import httpx

from domain.model_connector._cloud_base import CloudAdapterBase
from domain.model_connector.errors import ProviderError
from domain.model_connector.reasoning import ReasoningStyle, classify
from domain.model_connector.types import ChatRequest, ChatResponse, EmbedRequest, EmbedResponse, ProviderConfig
from shared.logger import logger

# Chat-capable models returned by /v1/models that we surface to the user
_CHAT_MODEL_PREFIXES = ("gpt-", "o1", "o3", "chatgpt-")

OPENAI_MODEL_PRESETS = [
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-turbo",
    "gpt-3.5-turbo",
    "o3-mini",
]


class OpenAIAdapter(CloudAdapterBase):
    CHAT_MODEL_PREFIXES: tuple[str, ...] | None = _CHAT_MODEL_PREFIXES
    MODEL_PRESETS: list[str] = OPENAI_MODEL_PRESETS
    # `include_reasoning` is an OpenRouter extension; official OpenAI (and other strict
    # OpenAI-compatible endpoints) reject the unknown field. DeepSeek streams reasoning_content
    # natively without it. Only OpenRouter opts in.
    STREAM_INCLUDE_REASONING: bool = False

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config, base_url="https://api.openai.com")

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._require_api_key()}"}

    async def list_models(self) -> list[str]:
        try:
            res = await self._client.get(self._url("models"), headers=self._auth())
            res.raise_for_status()
            data = res.json()
            raw_ids = [m["id"] for m in data.get("data", []) if isinstance(m, dict) and "id" in m]
            if not raw_ids:
                return self.MODEL_PRESETS
            if self.CHAT_MODEL_PREFIXES is None:
                return sorted(raw_ids)
            filtered = [m for m in raw_ids if any(m.startswith(p) for p in self.CHAT_MODEL_PREFIXES)]
            # A non-empty response that the chat-model prefix filter zeroes out entirely
            # means this endpoint doesn't use OpenAI's own naming convention (e.g. a 3rd-party
            # gateway proxying other vendors' models) — surface the raw list rather than
            # silently substituting fake OpenAI preset names that don't exist on it.
            return sorted(filtered) if filtered else sorted(raw_ids)
        except httpx.ConnectError as e:
            raise self._map_connect_error(e) from e
        except httpx.TimeoutException as e:
            raise self._map_timeout(e) from e
        except httpx.HTTPStatusError as e:
            raise self._map_http_error(e) from e
        except Exception as e:
            raise ProviderError(self._code_unknown(), str(e), provider_id=self.config.id) from e

    async def list_embedding_models(self) -> list[str]:
        """Embedding models the endpoint actually exposes via /v1/models (id contains 'embedding')."""
        try:
            res = await self._client.get(self._url("models"), headers=self._auth())
            res.raise_for_status()
            raw_ids = [m["id"] for m in res.json().get("data", []) if isinstance(m, dict) and "id" in m]
            return sorted(m for m in raw_ids if "embedding" in m.lower())
        except httpx.ConnectError as e:
            raise self._map_connect_error(e) from e
        except httpx.TimeoutException as e:
            raise self._map_timeout(e) from e
        except httpx.HTTPStatusError as e:
            raise self._map_http_error(e) from e
        except Exception as e:
            raise ProviderError(self._code_unknown(), str(e), provider_id=self.config.id) from e

    # Models that reject any explicit temperature value — must omit it from the payload.
    # GPT-5 series and reasoning models (o1/o3/o4) only accept their built-in default.
    _NO_TEMPERATURE_PREFIXES = ("o1", "o3", "o4", "gpt-5")

    def _build_payload(self, request: ChatRequest) -> dict:
        payload: dict = {
            "model": self.config.model_id,
            "messages": [m.model_dump() for m in request.messages],
            "max_completion_tokens": request.max_completion_tokens,
        }
        mid = (self.config.model_id or "").lower()
        model_rejects_temp = any(mid.startswith(p) for p in self._NO_TEMPERATURE_PREFIXES)
        if request.temperature is not None and not model_rejects_temp:
            payload["temperature"] = request.temperature
        if model_rejects_temp and request.reasoning_effort:
            payload["reasoning_effort"] = request.reasoning_effort

        # DeepSeek V4 over the OpenAI-compatible endpoint: an explicit thinking toggle plus
        # reasoning_effort. "disable" (or unset) turns thinking off; "high"/"max" turns it on.
        if classify(self.config.kind, self.config.model_id or "") == ReasoningStyle.EFFORT_TOGGLE:
            effort = (request.reasoning_effort or "").lower()
            if effort in ("high", "max"):
                payload["thinking"] = {"type": "enabled"}
                payload["reasoning_effort"] = effort
            else:
                payload["thinking"] = {"type": "disabled"}
        # json_object mode is supported by gpt-4o, gpt-4-turbo, gpt-3.5-turbo-1106+
        # but NOT by o1/o3/o4/gpt-5 reasoning models.
        if request.json_mode and not model_rejects_temp:
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def chat(self, request: ChatRequest) -> ChatResponse:
        payload = self._build_payload(request)
        try:
            logger.debug(f"{self.config.kind.value} chat: model={self.config.model_id}")
            res = await self._client.post(self._url("chat/completions"), json=payload, headers=self._auth())
            res.raise_for_status()
            data = res.json()
            choice = data["choices"][0]
            usage = data.get("usage", {})
            message = choice.get("message") or {}
            # Some providers (e.g. DeepSeek via OpenRouter) return content=null and put text in
            # reasoning_content; coerce to a string so ChatResponse never crashes on a null body.
            content = message.get("content")
            if content is None:
                content = message.get("reasoning_content") or ""
            return ChatResponse(
                provider_id=self.config.id,
                model_id=self.config.model_id,
                content=content,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
            )
        except httpx.ConnectError as e:
            raise self._map_connect_error(e) from e
        except httpx.TimeoutException as e:
            raise self._map_timeout(e) from e
        except httpx.HTTPStatusError as e:
            raise self._map_http_error(e) from e
        except Exception as e:
            raise ProviderError(self._code_unknown(), str(e), provider_id=self.config.id) from e

    async def chat_stream_events(self, request: ChatRequest) -> AsyncGenerator[dict, None]:
        """Stream chat completions with reasoning/thinking events via OpenAI's SSE API.

        Yields typed events:
        - {"type": "thinking", "text": <str>} for reasoning deltas
        - {"type": "content", "text": <str>} for content deltas
        - {"type": "done", "content": <full_content>, "prompt_tokens": <int>, "completion_tokens": <int>}
        """
        payload = self._build_payload(request)
        payload["stream"] = True
        # Keep response_format (json_object) for streaming — OpenAI/DeepSeek/OpenRouter all honor it
        # WITH stream=True. Dropping it (an old, wrong assumption) made json_mode callers get free-form
        # prose instead of JSON, so downstream json.loads failed at char 0. Non-streaming kept it too.
        # Enable reasoning stream only for providers that accept the OpenRouter extension.
        if self.STREAM_INCLUDE_REASONING:
            payload["include_reasoning"] = True

        content_parts: list[str] = []
        prompt_tokens = None
        completion_tokens = None

        try:
            async with self._client.stream(
                "POST", self._url("chat/completions"),
                json=payload, headers=self._auth(),
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = _json.loads(data)
                        choice = (chunk.get("choices") or [{}])[0]
                        delta = choice.get("delta", {})

                        # Check for reasoning/thinking
                        reasoning = delta.get("reasoning") or delta.get("reasoning_content")
                        if reasoning:
                            yield {"type": "thinking", "text": reasoning}

                        # Check for content
                        content = delta.get("content")
                        if content:
                            content_parts.append(content)
                            yield {"type": "content", "text": content}

                        # Check for usage on chunks that carry it
                        usage = chunk.get("usage")
                        if usage:
                            prompt_tokens = usage.get("prompt_tokens")
                            completion_tokens = usage.get("completion_tokens")
                    except (KeyError, IndexError, ValueError):
                        continue

                # Emit final done event with accumulated content and tokens
                yield {
                    "type": "done",
                    "content": "".join(content_parts),
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                }
        except httpx.ConnectError as e:
            raise self._map_connect_error(e) from e
        except httpx.TimeoutException as e:
            raise self._map_timeout(e) from e
        except httpx.HTTPStatusError as e:
            raise self._map_http_error(e) from e
        except Exception as e:
            raise ProviderError(self._code_unknown(), str(e), provider_id=self.config.id) from e

    async def chat_stream(self, request: ChatRequest) -> AsyncGenerator[str, None]:
        """Stream chat completions token-by-token via OpenAI's SSE API.

        Yields content deltas only (for Ask UI compatibility — no reasoning).
        """
        async for event in self.chat_stream_events(request):
            if event["type"] == "content":
                yield event["text"]

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        model = request.model_id or "text-embedding-3-small"
        payload = {"model": model, "input": request.texts}
        try:
            logger.debug(f"openai embed: model={model}, n={len(request.texts)}")
            res = await self._client.post(self._url("embeddings"), json=payload, headers=self._auth())
            res.raise_for_status()
            data = res.json()
            embeddings = [item["embedding"] for item in data["data"]]
            dims = len(embeddings[0]) if embeddings else 0
            return EmbedResponse(
                provider_id=self.config.id,
                model_id=model,
                embeddings=embeddings,
                dimensions=dims,
            )
        except httpx.ConnectError as e:
            raise self._map_connect_error(e) from e
        except httpx.TimeoutException as e:
            raise self._map_timeout(e) from e
        except httpx.HTTPStatusError as e:
            raise self._map_http_error(e) from e
        except Exception as e:
            raise ProviderError(self._code_unknown(), str(e), provider_id=self.config.id) from e

    async def test_connection(self) -> tuple[bool, str, str | None]:
        if not self._api_key:
            return False, "No API key configured", None
        try:
            models = await self.list_models()
            return True, f"Connected — {len(models)} model(s) available", None
        except ProviderError as e:
            return False, e.message, None

    @staticmethod
    def _code_unknown():
        from domain.model_connector.errors import ProviderErrorCode
        return ProviderErrorCode.UNKNOWN
