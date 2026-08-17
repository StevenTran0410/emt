"""OpenRouter provider adapter — OpenAI-compatible wire format, unified reasoning param,
per-model reasoning capability retrieved from /models."""
from __future__ import annotations

import httpx

from domain.model_connector._cloud_base import CloudAdapterBase
from domain.model_connector.errors import ProviderError
from domain.model_connector.openai.adapter import OpenAIAdapter
from domain.model_connector.types import ChatRequest, ProviderConfig

# Reasoning tokens count toward max_tokens, so an uncapped thinking budget can eat the whole
# allowance and leave nothing for the answer. Always reserve output room: >=10% of the completion
# budget, but never less than this floor.
_MIN_OUTPUT_TOKENS = 2000
_OUTPUT_RESERVE_FRACTION = 0.10
_MIN_REASONING_TOKENS = 256
# Real per-tier ceilings. Kept deliberately modest so reasoning tokens don't starve the answer
# (a 45k thinking budget left only ~5k for output and caused empty completions). "high" is the
# default judgment tier at 15k; "max" (30k) is reserved for genuinely reasoning-heavy calls.
_TIER_BUDGETS: dict[str, int] = {"low": 4000, "medium": 12000, "high": 15000, "max": 30000}
# OpenRouter's native reasoning uses `reasoning: {effort: <level>}` (effort OR max_tokens, never both).
# Valid effort levels per the OpenRouter reasoning-tokens doc:
_OR_EFFORTS = {"minimal", "low", "medium", "high", "xhigh", "max"}


class OpenRouterAdapter(OpenAIAdapter):
    # Do NOT filter by OpenAI naming prefixes — OpenRouter proxies every vendor.
    CHAT_MODEL_PREFIXES = None
    MODEL_PRESETS: list[str] = []
    STREAM_INCLUDE_REASONING = True  # OpenRouter's unified reasoning stream extension

    def __init__(self, config: ProviderConfig) -> None:
        # Bypass OpenAIAdapter.__init__'s api.openai.com default; go straight to CloudAdapterBase.
        CloudAdapterBase.__init__(self, config, base_url="https://openrouter.ai/api/v1")
        self._reasoning_by_id: dict[str, dict | None] = {}

    def _auth(self) -> dict[str, str]:
        # X-Session-Id pins OpenRouter's provider routing 
        headers = super()._auth()
        headers["X-Session-Id"] = f"aeh-{self.config.id}"
        return headers

    async def list_models(self) -> list[str]:
        try:
            res = await self._client.get(self._url("models"), headers=self._auth())
            res.raise_for_status()
            data = res.json()
            ids: list[str] = []
            for m in data.get("data", []):
                if isinstance(m, dict) and "id" in m:
                    ids.append(m["id"])
                    self._reasoning_by_id[m["id"]] = m.get("reasoning")
            return sorted(ids)
        except httpx.ConnectError as e:
            raise self._map_connect_error(e) from e
        except httpx.TimeoutException as e:
            raise self._map_timeout(e) from e
        except httpx.HTTPStatusError as e:
            raise self._map_http_error(e) from e
        except Exception as e:
            raise ProviderError(self._code_unknown(), str(e), provider_id=self.config.id) from e

    def reasoning_by_id(self) -> dict[str, dict | None]:
        return self._reasoning_by_id

    def _reasoning_budget(self, request: ChatRequest) -> int:
        """Thinking-token cap derived from the app's completion budget, reserving output room."""
        total = request.max_completion_tokens or 0
        output_reserve = max(_MIN_OUTPUT_TOKENS, int(total * _OUTPUT_RESERVE_FRACTION))
        cap = max(_MIN_REASONING_TOKENS, total - output_reserve)
        if request.thinking_budget:
            return min(request.thinking_budget, cap)
        # low/medium get a real (smaller) ceiling; high/max/unrecognized keep the full cap.
        tier_budget = _TIER_BUDGETS.get((request.reasoning_effort or "").lower())
        if tier_budget is not None:
            return min(tier_budget, cap)
        return cap

    def _build_payload(self, request: ChatRequest) -> dict:
        payload = super()._build_payload(request)
        payload.pop("reasoning_effort", None)
        payload.pop("thinking", None)

        # Per-provider override wins over the per-call effort (mirrors the base adapter) — lets us
        # pin e.g. deepseek-v4-flash to "low" via extra.default_reasoning_effort.
        effort = ((self.config.extra or {}).get("default_reasoning_effort") or request.reasoning_effort or "").lower()
        if request.thinking_budget:
            # Explicit token budget → OpenRouter's max_tokens form (Anthropic/Gemini style).
            payload["reasoning"] = {"max_tokens": self._reasoning_budget(request)}
        elif effort in ("none", "disable", "off"):
            payload["reasoning"] = {"effort": "none"}  # disable reasoning
        elif effort:
            # OpenRouter native effort level (mutually exclusive with max_tokens).
            payload["reasoning"] = {"effort": effort if effort in _OR_EFFORTS else "high"}
        chosen = (self.config.extra or {}).get("openrouter_provider")
        if chosen:
            payload["provider"] = {"order": [chosen], "allow_fallbacks": False}

        return payload

    async def list_endpoints(self) -> list[dict]:
        if not self.config.model_id:
            return []
        try:
            res = await self._client.get(
                self._url(f"models/{self.config.model_id}/endpoints"),
                headers=self._auth(),
            )
            res.raise_for_status()
            data = res.json()
            endpoints: list[dict] = []
            ep_list = (data.get("data") or {}).get("endpoints", []) if isinstance(data.get("data"), dict) else []
            for ep in ep_list:
                if not isinstance(ep, dict):
                    continue
                tag = str(ep.get("tag") or "")
                provider_name = str(ep.get("provider_name") or "")
                slug = tag.split("/")[0] if "/" in tag else provider_name.lower()
                pricing = ep.get("pricing") or {}
                endpoints.append({
                    "slug": slug,
                    "provider_name": provider_name,
                    "tag": tag,
                    "prompt_price": str(pricing.get("prompt") or "0"),
                    "completion_price": str(pricing.get("completion") or "0"),
                    "context_length": ep.get("context_length"),
                })
            return endpoints
        except httpx.ConnectError as e:
            raise self._map_connect_error(e) from e
        except httpx.TimeoutException as e:
            raise self._map_timeout(e) from e
        except httpx.HTTPStatusError as e:
            raise self._map_http_error(e) from e
        except Exception as e:
            raise ProviderError(self._code_unknown(), str(e), provider_id=self.config.id) from e
