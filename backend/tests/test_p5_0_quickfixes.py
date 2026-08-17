"""Acceptance tests for TICKET P5-0 (double mapping run + reasoning tier ladder).

OFFLINE ONLY — no real LLM or network calls.
"""
import pytest

from domain.model_connector.openrouter.adapter import OpenRouterAdapter
from domain.model_connector.types import ChatMessage, ChatRequest, ProviderConfig, ProviderKind
from infrastructure.db.database import close_db, get_db, init_db
from shared.utils import new_id, utc_now_iso


def _make_openrouter_adapter() -> OpenRouterAdapter:
    config = ProviderConfig(
        id="p1",
        kind=ProviderKind.OPENROUTER,
        display_name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        model_id="some-vendor/some-model",
    )
    return OpenRouterAdapter(config)


@pytest.mark.parametrize(
    ("effort", "thinking_budget", "expected_reasoning"),
    [
        ("none", None, {"effort": "none"}),
        ("disable", None, {"effort": "none"}),
        ("low", None, {"effort": "low"}),
        ("medium", None, {"effort": "medium"}),
        ("high", None, {"effort": "high"}),
        ("max", None, {"effort": "max"}),
        ("high", 1000, {"max_tokens": 1000}),    # explicit thinking_budget still uses the max_tokens form
    ],
)
def test_openrouter_payload_reasoning_tier_table(effort, thinking_budget, expected_reasoning):
    """OpenRouter native reasoning: effort levels go as `reasoning: {effort: <level>}` (per the
    OpenRouter reasoning-tokens doc — effort OR max_tokens, never both); only an explicit
    thinking_budget uses the max_tokens form."""
    adapter = _make_openrouter_adapter()
    req = ChatRequest(
        provider_id="p1",
        messages=[ChatMessage(role="user", content="hi")],
        max_completion_tokens=50000,
        temperature=0.0,
        json_mode=True,
        reasoning_effort=effort,
        thinking_budget=thinking_budget,
    )

    payload = adapter._build_payload(req)

    assert payload["reasoning"] == expected_reasoning


@pytest.mark.asyncio
async def test_reasoning_ladder_retries_once_at_low_then_falls_back(monkeypatch):
    """Bug 2b ladder: first attempt at 'high' forced empty -> exactly one retry at 'low' -> success.
    Then: both attempts failing -> caller gets None (its own deterministic fallback), and nothing
    from the failed run is ever returned/cached."""
    from domain.business_flow_integrity._llm import call_with_reasoning_ladder
    from domain.model_connector.service import ProviderConfigService

    def _build_req(effort: str) -> ChatRequest:
        return ChatRequest(provider_id="p1", messages=[ChatMessage(role="user", content="x")], reasoning_effort=effort)

    def _parse(text: str) -> str:
        if text != "ok-payload":
            raise ValueError(f"unexpected payload: {text}")
        return text.upper()

    efforts_seen: list[str] = []

    async def stub_empty_then_ok(self, request):
        efforts_seen.append(request.reasoning_effort)
        if len(efforts_seen) == 1:
            if False:
                yield {}  # pragma: no cover - makes this an async generator
            return
        yield {"type": "content", "text": "ok-payload"}

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", stub_empty_then_ok)

    result = await call_with_reasoning_ladder(_build_req, _parse, first_effort="high", label="test")

    assert efforts_seen == ["high", "low"]
    assert result == ("OK-PAYLOAD", "ok-payload")

    # Both attempts fail (always empty) -> None, exactly two attempts, no cached failure to reuse.
    efforts_seen.clear()

    async def stub_always_empty(self, request):
        efforts_seen.append(request.reasoning_effort)
        if False:
            yield {}  # pragma: no cover - makes this an async generator
        return

    monkeypatch.setattr(ProviderConfigService, "chat_stream_events", stub_always_empty)

    result2 = await call_with_reasoning_ladder(_build_req, _parse, first_effort="high", label="test")

    assert efforts_seen == ["high", "low"]
    assert result2 is None


@pytest.mark.asyncio
async def test_run_flow_integrity_pipeline_calls_run_unit_mapping_once(tmp_path, monkeypatch):
    """Bug 1: the /run endpoint must trigger exactly one run_unit_mapping execution — it no longer
    calls run_unit_mapping directly AND via run_unit_verdicts (which calls it internally)."""
    import domain.business_flow_integrity as bfi
    import domain.business_flow_integrity._map as map_mod
    from api.doc_graph import FlowIntegrityRunBody, run_flow_integrity_pipeline

    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()

    call_count = {"n": 0}

    async def counting_run_unit_mapping(db, cluster_id, snapshot_id, provider_id=None):
        call_count["n"] += 1
        return []

    async def noop(*args, **kwargs):
        return None

    async def fake_findings(*args, **kwargs):
        return {"ok": True}

    try:
        db = get_db()
        cluster_id = f"cluster-{new_id()}"
        snap_id = f"snap-{new_id()}"
        now = utc_now_iso()

        # A bd_business_flows row is required so the endpoint's has_bf gate takes the
        # run_unit_mapping/run_unit_verdicts branch.
        await db.execute(
            "INSERT INTO bd_business_flows (id, cluster_id, doc_id, sub_ix, block_key, name, description, ordinal, origin, model_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"bf:{new_id()}", cluster_id, "doc1", 1, "blk1", "Some Flow", "desc", 1, "fallback", None, now),
        )
        await db.commit()

        # Stub out every heavy pipeline step except run_unit_verdicts (real, unmocked) so this
        # test isolates the exact regression: run_unit_mapping must be invoked exactly once.
        monkeypatch.setattr(bfi, "build_code_flow", noop)
        monkeypatch.setattr(bfi, "align_bd_to_code", noop)
        monkeypatch.setattr(bfi, "run_flow_verdicts", noop)
        monkeypatch.setattr(bfi, "build_route_segments", noop)
        monkeypatch.setattr(bfi, "generate_executive_summary", noop)
        monkeypatch.setattr(bfi, "get_flow_integrity_findings", fake_findings)
        monkeypatch.setattr(map_mod, "run_unit_mapping", counting_run_unit_mapping)

        await run_flow_integrity_pipeline(cluster_id, snap_id, body=FlowIntegrityRunBody(provider_id=None))

        assert call_count["n"] == 1
    finally:
        await close_db()
