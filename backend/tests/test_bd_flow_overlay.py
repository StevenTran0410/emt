"""Tests for the BD Flow LLM prose overlay (Ticket B2): chunker, hard validator, cache, e2e.

OFFLINE ONLY — every test that talks to an "LLM" monkeypatches ProviderConfigService.chat with a
stub returning canned JSON. No real LLM/network call happens anywhere in this file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from domain.doc_graph._flow_overlay import OverlayClaim, _hard_validate, run_bd_flow_overlay
from domain.doc_graph._flow_prose import ProseChunk, select_prose_chunks
from domain.doc_graph._markdown_parser import parse_markdown_report
from infrastructure.db.database import close_db, get_db, init_db

BD_FILE_PATH = Path(r"d:\Emt\emt_data\input_emt\EMT.BD-HSBMENU5.report.md")
EMT_INPUT_DIR = BD_FILE_PATH.parent
SKIP_REASON = "Real BD file EMT.BD-HSBMENU5.report.md absent"


# ---------------------------------------------------------------------------
# 1. Chunker — hand-derived ground truth against the real BD.
# ---------------------------------------------------------------------------

# Hand derivation (grep '^#' on EMT.BD-HSBMENU5.report.md, see ticket §1 for region descriptions):
#   - '## 5.2. Step Details' (line 812) runs to line 863 before '## 5.3 ...' (865) = 52 lines,
#     over the 40-line cap -> splits on the Step9/Step10 paragraph boundary into 2 chunks.
#   - Two 'Execution Sequence' tables (header row at 907, 1644) each have footnote prose directly
#     below them, bounded by the next heading ('### Condition Code Reference') = 2 chunks.
#   - '# 6. Failure Modes & Error Handling' appears exactly twice (line 1622, 1731) — once per
#     Job Flow sub-report (HNDM001N, HNDM004J) — NOT the unrelated '## 5.3 Key Business Rules &
#     Failure Modes' heading, which is deliberately excluded by the whitelist's exact title match.
#   - '## Event Details' appears exactly 14 times (6 HSBMENU5 events + 8 FHNIXLOT events), matching
#     B1's 14 event nodes; each is short enough to stay a single chunk.
# Total: 2 + 2 + 2 + 14 = 20 chunks.
EXPECTED_CHUNKS = [
    ("step_details", 812, 851),
    ("step_details", 853, 863),
    ("exec_sequence_footnote", 971, 983),
    ("failure_modes", 1622, 1637),
    ("exec_sequence_footnote", 1649, 1654),
    ("failure_modes", 1731, 1741),
    ("event_system_behavior", 2084, 2100),
    ("event_system_behavior", 2180, 2196),
    ("event_system_behavior", 2272, 2288),
    ("event_system_behavior", 2367, 2383),
    ("event_system_behavior", 2461, 2477),
    ("event_system_behavior", 2555, 2570),
    ("event_system_behavior", 2655, 2675),
    ("event_system_behavior", 2765, 2785),
    ("event_system_behavior", 2879, 2899),
    ("event_system_behavior", 2990, 3006),
    ("event_system_behavior", 3092, 3112),
    ("event_system_behavior", 3200, 3218),
    ("event_system_behavior", 3300, 3319),
    ("event_system_behavior", 3401, 3420),
]


@pytest.mark.skipif(not BD_FILE_PATH.exists(), reason=SKIP_REASON)
def test_select_prose_chunks_acceptance_gates():
    content = BD_FILE_PATH.read_text(encoding="utf-8")
    doc = parse_markdown_report(str(BD_FILE_PATH), content, "sha256_dummy")
    lines = content.splitlines()
    assert len(lines) == 3471  # same total as the B1 acceptance test

    chunks = select_prose_chunks(doc)

    assert len(chunks) == 20
    actual = [(c.region_kind, c.line_start, c.line_end) for c in chunks]
    assert actual == EXPECTED_CHUNKS

    for c in chunks:
        assert c.line_end - c.line_start + 1 <= 40
        assert c.line_start <= c.line_end
        # Coordinates must be exact: chunk text is precisely the original doc slice.
        expected_text = "\n".join(lines[c.line_start - 1 : c.line_end])
        assert c.text == expected_text

    region_counts: dict[str, int] = {}
    for kind, _s, _e in actual:
        region_counts[kind] = region_counts.get(kind, 0) + 1
    assert region_counts == {
        "step_details": 2,
        "exec_sequence_footnote": 2,
        "failure_modes": 2,
        "event_system_behavior": 14,
    }


# ---------------------------------------------------------------------------
# 2. Hard validator — adversarial unit tests (synthetic doc text, no corpus needed).
# ---------------------------------------------------------------------------

SYN_LINES = [
    "## Event Details",  # 1
    "",  # 2
    "**Trigger Condition**: The user enters screen TEST01.",  # 3
    "",  # 4
    "**System Behavior**:",  # 5
    "1. The program validates field S05 before continuing.",  # 6
    "2. The system does not skip validation under any circumstance.",  # 7
]
SYN_CHUNK = ProseChunk(
    text="\n".join(SYN_LINES), line_start=1, line_end=7, region_kind="event_system_behavior"
)
SYN_REGISTRY_IDS = {"bdflow:test:doc/x:0:S05", "bdflow:test:doc/x:0:S06"}
SYN_NODE_BINDING = {"bdflow:test:doc/x:0:S05": "S05", "bdflow:test:doc/x:0:S06": "S06"}


def _claim(**overrides) -> OverlayClaim:
    base: dict = dict(
        candidate_id="c1",
        kind="guard_enrichment",
        subject={"id": "S05", "type": "step", "mention": "field S05"},
        relation="validates",
        object=None,
        guard=None,
        citation={
            "line_start": 6,
            "line_end": 6,
            "quote": "1. The program validates field S05 before continuing.",
        },
        modality="conditional",
        target_fact_id=None,
    )
    base.update(overrides)
    return OverlayClaim.model_validate(base)


def test_validator_fabricated_target_fact_id_rejected():
    claim = _claim(target_fact_id="bdflow:test:doc/x:0:DOES_NOT_EXIST")
    tier, reasons = _hard_validate(claim, SYN_CHUNK, SYN_LINES, SYN_REGISTRY_IDS, SYN_NODE_BINDING)
    assert tier == "REJECTED"
    assert "target_fact_id_unknown" in reasons


def test_validator_quote_not_substring_rejected():
    claim = _claim(
        citation={
            "line_start": 6,
            "line_end": 6,
            "quote": "this text does not appear anywhere in the document",
        }
    )
    tier, reasons = _hard_validate(claim, SYN_CHUNK, SYN_LINES, SYN_REGISTRY_IDS, SYN_NODE_BINDING)
    assert tier == "REJECTED"
    assert "quote_not_exact_substring" in reasons


def test_validator_line_out_of_range_rejected():
    claim = _claim(
        citation={
            "line_start": 50,
            "line_end": 51,
            "quote": "1. The program validates field S05 before continuing.",
        }
    )
    tier, reasons = _hard_validate(claim, SYN_CHUNK, SYN_LINES, SYN_REGISTRY_IDS, SYN_NODE_BINDING)
    assert tier == "REJECTED"
    assert "citation_out_of_chunk_range" in reasons


def test_validator_negative_without_negation_wording_rejected():
    claim = _claim(
        modality="negative",
        subject={"id": "TEST01", "type": "screen", "mention": "screen TEST01"},
        citation={
            "line_start": 3,
            "line_end": 3,
            "quote": "**Trigger Condition**: The user enters screen TEST01.",
        },
    )
    tier, reasons = _hard_validate(claim, SYN_CHUNK, SYN_LINES, SYN_REGISTRY_IDS, SYN_NODE_BINDING)
    assert tier == "REJECTED"
    assert "negative_modality_without_negation_wording" in reasons


def test_validator_valid_guard_enrichment_is_p1():
    claim = _claim()
    tier, reasons = _hard_validate(claim, SYN_CHUNK, SYN_LINES, SYN_REGISTRY_IDS, SYN_NODE_BINDING)
    assert tier == "P1"
    assert reasons is None


def test_validator_valid_new_edge_claim_is_p2():
    claim = _claim(kind="edge", relation="invokes", modality="explicit")
    tier, reasons = _hard_validate(claim, SYN_CHUNK, SYN_LINES, SYN_REGISTRY_IDS, SYN_NODE_BINDING)
    assert tier == "P2"
    assert reasons is None


def test_validator_target_fact_id_binding_mismatch_rejected():
    claim = _claim(
        subject={"id": "S06", "type": "step", "mention": "field S05"},
        target_fact_id="bdflow:test:doc/x:0:S05",
    )
    tier, reasons = _hard_validate(claim, SYN_CHUNK, SYN_LINES, SYN_REGISTRY_IDS, SYN_NODE_BINDING)
    assert tier == "REJECTED"
    assert "target_fact_id_binding_mismatch" in reasons


def test_validator_decoration_wrapped_identifier_passes():
    """B3: Markdown backticks/asterisks in raw doc lines normalize during substring check."""
    dec_lines = list(SYN_LINES)
    dec_lines[5] = "1. The program validates field `S05` before continuing."
    dec_chunk = ProseChunk(
        text="\n".join(dec_lines), line_start=1, line_end=7, region_kind="event_system_behavior"
    )
    claim = _claim(
        subject={"id": "S05", "type": "step", "mention": "S05"},
        citation={"line_start": 6, "line_end": 6, "quote": "1. The program validates field S05 before continuing."},
    )
    tier, reasons = _hard_validate(claim, dec_chunk, dec_lines, SYN_REGISTRY_IDS, SYN_NODE_BINDING)
    assert tier == "P1"
    assert reasons is None


def test_validator_no_binding_decision_node_target_fact_id_passes():
    """B2a: Decision nodes with binding=None fall back to node id, preventing binding mismatch."""
    dec_id = "bdflow:test:doc/x:0:DECISION1"
    reg_ids = {"bdflow:test:doc/x:0:S05", dec_id}
    node_binding = {"bdflow:test:doc/x:0:S05": "S05", dec_id: dec_id}
    claim = _claim(
        subject={"id": dec_id, "type": "step", "mention": "field S05"},
        target_fact_id=dec_id,
    )
    tier, reasons = _hard_validate(claim, SYN_CHUNK, SYN_LINES, reg_ids, node_binding)
    assert tier == "P1"
    assert reasons is None


def test_validator_new_identifier_with_real_event_target_fact_id_passes():
    """B2b: Subject id not in registry (new identifier e.g. OPT) paired with real target_fact_id passes."""
    claim = _claim(
        subject={"id": "OPT", "type": "fact", "mention": "field S05"},
        target_fact_id="bdflow:test:doc/x:0:S05",
    )
    tier, reasons = _hard_validate(claim, SYN_CHUNK, SYN_LINES, SYN_REGISTRY_IDS, SYN_NODE_BINDING)
    assert tier == "P1"
    assert reasons is None


def test_validator_subject_mention_in_chunk_header_fallback_passes():
    """B5: Subject mention absent in narrow quote line but present in chunk header passes via fallback."""
    claim = _claim(
        subject={"id": "TEST01", "type": "screen", "mention": "screen TEST01"},
        citation={"line_start": 6, "line_end": 6, "quote": "1. The program validates field S05 before continuing."},
    )
    tier, reasons = _hard_validate(claim, SYN_CHUNK, SYN_LINES, SYN_REGISTRY_IDS, SYN_NODE_BINDING)
    assert tier == "P1"
    assert reasons is None


def test_validator_synthesized_right_hand_operand_passes():
    """B4: Synthesized right-hand guard operand (e.g. 'pressed') passes as long as key operand ('S05') is present."""
    claim = _claim(
        guard={"operator": "eq", "operands": ["S05", "pressed"], "source_text": "field S05"},
    )
    tier, reasons = _hard_validate(claim, SYN_CHUNK, SYN_LINES, SYN_REGISTRY_IDS, SYN_NODE_BINDING)
    assert tier == "P1"
    assert reasons is None


def test_validator_negative_edge_with_negation_wording_is_p1():
    """kind=negative_edge + modality=negative, quote genuinely contains negation wording -> P1."""
    claim = _claim(
        kind="negative_edge",
        modality="negative",
        subject={"id": "S06", "type": "step", "mention": "The system"},
        citation={
            "line_start": 7,
            "line_end": 7,
            "quote": "2. The system does not skip validation under any circumstance.",
        },
    )
    tier, reasons = _hard_validate(claim, SYN_CHUNK, SYN_LINES, SYN_REGISTRY_IDS, SYN_NODE_BINDING)
    assert tier == "P1"
    assert reasons is None


def test_chunk_registry_aliases_and_dealias_roundtrip():
    """Aliases (N001...) shrink the prompt; _dealias_claim maps alias -> binding (subject/object)
    and alias -> real id (target_fact_id) so the validator sees the same values as before."""
    from domain.doc_graph._flow_overlay import _chunk_registry, _dealias_claim

    rows = [
        {"id": f"bdflow:test:doc/x:3:exseq:0:{i}:S0{i}", "binding": f"S0{i}",
         "node_kind": "job_step", "doc_line_start": 900 + i}
        for i in range(1, 6)
    ] + [
        {"id": "bdflow:test:doc/x:1:0:Init1", "binding": None,
         "node_kind": "step", "doc_line_start": 92},
    ]
    chunk = ProseChunk(text="x", line_start=971, line_end=983, region_kind="exec_sequence_footnote")
    alias_map, registry_sha, prompt = _chunk_registry(rows, chunk)

    # Filter keeps only the 5 job_step rows (the sub-report-1 step is irrelevant to a footnote).
    assert len(alias_map) == 5
    assert all(line.split(" | ")[2] == "job_step" for line in prompt.splitlines())
    assert registry_sha and len(registry_sha) == 64

    s05_alias = next(a for a, e in alias_map.items() if e["binding"] == "S05")
    claim = _claim(
        subject={"id": s05_alias, "type": "step", "mention": "field S05"},
        target_fact_id=s05_alias,
    )
    dealiased = _dealias_claim(claim, alias_map)
    assert dealiased.subject.id == "S05"
    assert dealiased.target_fact_id == alias_map[s05_alias]["id"]

    # Non-alias values pass through untouched.
    untouched = _dealias_claim(_claim(), alias_map)
    assert untouched.subject.id == "S05"
    assert untouched.target_fact_id is None


# ---------------------------------------------------------------------------
# 3. Cache behavior — synthetic single-chunk doc, no corpus needed.
# ---------------------------------------------------------------------------

SYN_CACHE_BD_CONTENT = """# Basic Design — Cache Test cluster

# Event Flows

## HSBMENU5-Event-Init

## Event Details

**Trigger Condition**: The user enters screen TEST01.

**System Behavior**:
1. The program validates field S05 before continuing.

## Event Flow Diagram

placeholder
"""


async def _setup_cache_test_db(tmp_path, monkeypatch, cluster_id: str):
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()
    db = get_db()
    await db.execute(
        "INSERT INTO bd_flow_nodes (id, cluster_id, doc_id, node_kind, binding, provenance_tier, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
        (f"bdflow:{cluster_id}:doc/x:0:S05", cluster_id, "doc/TEST.BD-CACHE.report.md", "job_step", "S05", "D2"),
    )
    await db.commit()
    return db


def _make_fake_provider():
    from domain.model_connector.types import ProviderCapabilities, ProviderConfig, ProviderKind

    return ProviderConfig(
        id="fake_provider",
        kind=ProviderKind.OPENAI,
        display_name="Fake Provider",
        base_url="http://fake",
        model_id="fake-model",
        capabilities=ProviderCapabilities(),
    )


def _stream_of(content_fn):
    """Wrap a (self, request) -> content-string function as a chat_stream_events async-gen stub, so
    tests exercise the overlay's streaming path with canned content (no real network). Emits the
    whole string as one 'content' delta before 'done' — enough for on_event to observe a real delta
    without needing per-token granularity; the final content used downstream still comes from
    'done' (unchanged), so this is transparent to every test that doesn't care about deltas."""
    async def _gen(self, request):
        content = content_fn(self, request)
        if content:
            yield {"type": "content", "text": content}
        yield {"type": "done", "content": content, "prompt_tokens": None, "completion_tokens": None}
    return _gen


async def _stream_raises(self, request):
    """chat_stream_events stub that fails before any event (the unreachable yield makes it a gen)."""
    raise RuntimeError("simulated transport failure")
    yield  # noqa: unreachable — marks this coroutine as an async generator


@pytest.mark.asyncio
async def test_bd_flow_overlay_cache_hit_then_prompt_version_bump_misses(tmp_path, monkeypatch):
    import domain.doc_graph._flow_overlay as flow_overlay_mod
    from domain.model_connector.service import ProviderConfigService
    from domain.model_connector.types import ChatResponse

    cluster_id = "cluster-cache-1"
    db = await _setup_cache_test_db(tmp_path, monkeypatch, cluster_id)
    try:
        doc = parse_markdown_report(
            "TEST.BD-CACHE.report.md", SYN_CACHE_BD_CONTENT, "sha_cache_test"
        )
        assert len(select_prose_chunks(doc)) == 1  # exactly one chunk => call count is exact

        call_count = 0

        def _content(self, request):
            nonlocal call_count
            call_count += 1
            return json.dumps({"schema_version": "bd-flow-prose-v1", "claims": []})

        fake_cfg = _make_fake_provider()

        async def mock_list_all(self):
            return [fake_cfg]

        monkeypatch.setattr(ProviderConfigService, "list_all", mock_list_all)
        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _stream_of(_content))

        await run_bd_flow_overlay(db, [doc], cluster_id, None)
        assert call_count == 1

        # Same chunk + same versions -> cache hit, stub not called again.
        await run_bd_flow_overlay(db, [doc], cluster_id, None)
        assert call_count == 1

        # Bump prompt_version -> different cache_key -> miss -> stub called again.
        monkeypatch.setattr(flow_overlay_mod, "PROMPT_VERSION", flow_overlay_mod.PROMPT_VERSION + "-bumped")
        await run_bd_flow_overlay(db, [doc], cluster_id, None)
        assert call_count == 2
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_bd_flow_overlay_transport_error_never_cached(tmp_path, monkeypatch):
    from domain.model_connector.service import ProviderConfigService

    cluster_id = "cluster-cache-2"
    db = await _setup_cache_test_db(tmp_path, monkeypatch, cluster_id)
    try:
        doc = parse_markdown_report(
            "TEST.BD-CACHE.report.md", SYN_CACHE_BD_CONTENT, "sha_cache_test_2"
        )

        fake_cfg = _make_fake_provider()

        async def mock_list_all(self):
            return [fake_cfg]

        monkeypatch.setattr(ProviderConfigService, "list_all", mock_list_all)
        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _stream_raises)

        await run_bd_flow_overlay(db, [doc], cluster_id, None)

        async with db.execute("SELECT COUNT(*) as cnt FROM bd_flow_llm_cache") as cur:
            assert (await cur.fetchone())["cnt"] == 0
        async with db.execute(
            "SELECT COUNT(*) as cnt FROM bd_flow_overlay_claims WHERE cluster_id=?", (cluster_id,)
        ) as cur:
            assert (await cur.fetchone())["cnt"] == 0
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_bd_flow_overlay_missing_schema_version_is_salvaged(tmp_path, monkeypatch):
    """A weak model that mangles/omits schema_version but returns a valid claims list must NOT have
    its whole response discarded — the claims still get validated and persisted."""
    from domain.model_connector.service import ProviderConfigService
    from domain.model_connector.types import ChatResponse

    cluster_id = "cluster-salvage"
    db = await _setup_cache_test_db(tmp_path, monkeypatch, cluster_id)
    try:
        doc = parse_markdown_report("TEST.BD-CACHE.report.md", SYN_CACHE_BD_CONTENT, "sha_salvage")

        def _content(self, request):
            # No schema_version key at all — only claims. Cite the real chunk line the prompt shows;
            # mention = the whole quoted line so the substring gate passes (mirrors the e2e stub).
            ls, quote = _extract_first_chunk_line(request.messages[-1].content)
            payload = {"claims": [{
                "candidate_id": "c1", "kind": "guard_enrichment",
                "subject": {"id": "n/a", "type": "step", "mention": quote},
                "relation": "validates", "object": None, "guard": None,
                "citation": {"line_start": ls, "line_end": ls, "quote": quote},
                "modality": "conditional", "target_fact_id": None,
            }]}
            return json.dumps(payload)

        fake_cfg = _make_fake_provider()

        async def mock_list_all(self):
            return [fake_cfg]

        monkeypatch.setattr(ProviderConfigService, "list_all", mock_list_all)
        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _stream_of(_content))

        await run_bd_flow_overlay(db, [doc], cluster_id, None)
        async with db.execute(
            "SELECT COUNT(*) as cnt FROM bd_flow_overlay_claims WHERE cluster_id=? AND tier!='REJECTED'",
            (cluster_id,),
        ) as cur:
            assert (await cur.fetchone())["cnt"] == 1  # salvaged, not discarded
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_bd_flow_overlay_retries_once_at_low_effort(tmp_path, monkeypatch):
    """A failed first attempt triggers exactly one retry with reasoning_effort='low'; a good
    response on the retry is used."""
    from domain.model_connector.service import ProviderConfigService
    from domain.model_connector.types import ChatResponse

    cluster_id = "cluster-retry"
    db = await _setup_cache_test_db(tmp_path, monkeypatch, cluster_id)
    try:
        doc = parse_markdown_report("TEST.BD-CACHE.report.md", SYN_CACHE_BD_CONTENT, "sha_retry")
        assert len(select_prose_chunks(doc)) == 1

        efforts: list = []

        def _content(self, request):
            efforts.append(request.reasoning_effort)
            if len(efforts) == 1:
                return ""  # first attempt: empty -> triggers one retry
            ls, quote = _extract_first_chunk_line(request.messages[-1].content)
            payload = {"schema_version": "bd-flow-prose-v1", "claims": [{
                "candidate_id": "c1", "kind": "guard_enrichment",
                "subject": {"id": "n/a", "type": "step", "mention": quote},
                "relation": "validates", "object": None, "guard": None,
                "citation": {"line_start": ls, "line_end": ls, "quote": quote},
                "modality": "conditional", "target_fact_id": None,
            }]}
            return json.dumps(payload)

        fake_cfg = _make_fake_provider()

        async def mock_list_all(self):
            return [fake_cfg]

        monkeypatch.setattr(ProviderConfigService, "list_all", mock_list_all)
        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _stream_of(_content))

        await run_bd_flow_overlay(db, [doc], cluster_id, None)

        assert efforts == ["high", "low"]  # reasoning high by default, exactly one retry at low
        async with db.execute(
            "SELECT COUNT(*) as cnt FROM bd_flow_overlay_claims WHERE cluster_id=? AND tier!='REJECTED'",
            (cluster_id,),
        ) as cur:
            assert (await cur.fetchone())["cnt"] == 1  # retry's good claim was used
    finally:
        await close_db()


@pytest.mark.asyncio
async def test_bd_flow_overlay_empty_content_not_cached(tmp_path, monkeypatch):
    """Null/empty model output is transient — it must NOT be cached, so a rebuild retries it."""
    from domain.model_connector.service import ProviderConfigService
    from domain.model_connector.types import ChatResponse

    cluster_id = "cluster-empty"
    db = await _setup_cache_test_db(tmp_path, monkeypatch, cluster_id)
    try:
        doc = parse_markdown_report("TEST.BD-CACHE.report.md", SYN_CACHE_BD_CONTENT, "sha_empty")

        fake_cfg = _make_fake_provider()

        async def mock_list_all(self):
            return [fake_cfg]

        monkeypatch.setattr(ProviderConfigService, "list_all", mock_list_all)
        monkeypatch.setattr(
            ProviderConfigService, "chat_stream_events", _stream_of(lambda self, request: "")
        )

        await run_bd_flow_overlay(db, [doc], cluster_id, None)
        async with db.execute("SELECT COUNT(*) as cnt FROM bd_flow_llm_cache") as cur:
            assert (await cur.fetchone())["cnt"] == 0  # empty response never cached
        async with db.execute(
            "SELECT COUNT(*) as cnt FROM bd_flow_overlay_claims WHERE cluster_id=?", (cluster_id,)
        ) as cur:
            assert (await cur.fetchone())["cnt"] == 0
    finally:
        await close_db()


# ---------------------------------------------------------------------------
# 4. E2E — real DocGraphService.build() against emt_data/input_emt, stubbed provider.
# ---------------------------------------------------------------------------

_NUMBERED_LINE_RE = re.compile(r"^(\d+): (.*)$")


def _extract_first_chunk_line(user_content: str) -> tuple[int, str]:
    """Pull (line_start, exact original line text) out of the prompt built by _build_chat_request,
    so the stub can cite REAL, in-range document text without hardcoding any BD content here."""
    m = re.search(r"lines (\d+)-\d+\):\n(.*?)\n\nAllowed node registry", user_content, re.DOTALL)
    assert m, "prompt format changed — stub can no longer locate the numbered chunk block"
    line_start = int(m.group(1))
    first_line = m.group(2).splitlines()[0]
    lm = _NUMBERED_LINE_RE.match(first_line)
    assert lm
    assert int(lm.group(1)) == line_start
    return line_start, lm.group(2)


@pytest.mark.skipif(not BD_FILE_PATH.exists(), reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_bd_flow_overlay_e2e_stubbed_build(tmp_path, monkeypatch):
    from domain.doc_graph.service import DocGraphService
    from domain.doc_graph.types import BuildDocGraphRequest
    from domain.model_connector.service import ProviderConfigService
    from domain.model_connector.types import ChatResponse
    from shared.utils import new_id, utc_now_iso

    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()

    try:
        db = get_db()
        snap_id = f"snap-{new_id()}"
        await db.execute(
            "INSERT INTO repo_snapshots (id, local_repo_id, local_path, synced_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (snap_id, f"repo-{new_id()}", str(EMT_INPUT_DIR), utc_now_iso(), utc_now_iso()),
        )
        await db.commit()

        fake_cfg = _make_fake_provider()

        async def mock_list_all(self):
            return [fake_cfg]

        def _stub_content(self, request):
            """Deterministic per-chunk stub: 1 valid guard_enrichment (-> P1), 1 valid risk claim
            (-> P2), 1 out-of-range claim (-> REJECTED). Cites real doc text pulled from the
            prompt itself so every P1/P2 claim validates against the ACTUAL BD content."""
            user_content = request.messages[1].content
            line_start, quote_text = _extract_first_chunk_line(user_content)
            claims = [
                {
                    "candidate_id": "c1",
                    "kind": "guard_enrichment",
                    "subject": {"id": "n/a", "type": "step", "mention": quote_text},
                    "relation": "validates",
                    "object": None,
                    "guard": None,
                    "citation": {"line_start": line_start, "line_end": line_start, "quote": quote_text},
                    "modality": "conditional",
                    "target_fact_id": None,
                },
                {
                    "candidate_id": "c2",
                    "kind": "risk",
                    "subject": {"id": "n/a", "type": "step", "mention": quote_text},
                    "relation": "invokes",
                    "object": None,
                    "guard": None,
                    "citation": {"line_start": line_start, "line_end": line_start, "quote": quote_text},
                    "modality": "unresolved",
                    "target_fact_id": None,
                },
                {
                    "candidate_id": "c3",
                    "kind": "edge",
                    "subject": {"id": "n/a", "type": "step", "mention": "x"},
                    "relation": "reads",
                    "object": None,
                    "guard": None,
                    "citation": {"line_start": line_start - 1, "line_end": line_start - 1, "quote": "x"},
                    "modality": "explicit",
                    "target_fact_id": None,
                },
            ]
            return json.dumps({"schema_version": "bd-flow-prose-v1", "claims": claims})

        monkeypatch.setattr(ProviderConfigService, "list_all", mock_list_all)
        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _stream_of(_stub_content))

        service = DocGraphService()
        summary = await service.build(
            BuildDocGraphRequest(
                source_dir=str(EMT_INPUT_DIR), snapshot_id=snap_id, llm_enabled=True
            )
        )
        cluster_id = summary.cluster_id

        # B1 skeleton counts must be UNCHANGED by the overlay pass.
        async with db.execute(
            "SELECT COUNT(*) as cnt FROM bd_flow_nodes WHERE cluster_id=?", (cluster_id,)
        ) as cur:
            node_count = (await cur.fetchone())["cnt"]
        async with db.execute(
            "SELECT COUNT(*) as cnt FROM bd_flow_edges WHERE cluster_id=?", (cluster_id,)
        ) as cur:
            edge_count = (await cur.fetchone())["cnt"]
        assert node_count == 216
        assert edge_count == 210

        overlay = await service.bd_flow_overlay(cluster_id)
        assert overlay["counts"]["P1"] == 20
        assert overlay["counts"]["P2"] == 20
        assert overlay["counts"]["REJECTED"] == 20
        assert len(overlay["claims"]) == 60

        for claim in overlay["claims"]:
            assert claim["tier"] in ("P1", "P2", "REJECTED")
            if claim["tier"] == "REJECTED":
                assert claim["reject_reasons"]
                assert "citation_out_of_chunk_range" in claim["reject_reasons"]
            else:
                assert claim["reject_reasons"] is None

        # Rejected claims are stored too (audit trail), not silently dropped.
        async with db.execute(
            "SELECT COUNT(*) as cnt FROM bd_flow_overlay_claims WHERE cluster_id=? AND tier='REJECTED'",
            (cluster_id,),
        ) as cur:
            assert (await cur.fetchone())["cnt"] == 20
    finally:
        await close_db()


# ---------------------------------------------------------------------------
# 5. B4 — on_event activity hook: real streaming events + purely-additive proof.
# ---------------------------------------------------------------------------

# Columns compared between the with-on_event and without-on_event runs below. id/cluster_id/doc_id/
# created_at are excluded: id and doc_id embed doc-path/candidate-id (identical here, but not the
# point of the proof), cluster_id differs by construction (two clusters, no cache cross-talk), and
# created_at is wall-clock. Every remaining column is the actual claim content.
_CLAIM_COMPARISON_COLUMNS = (
    "chunk_line_start, chunk_line_end, region_kind, kind, relation, subject_json, object_json, "
    "guard_json, citation_json, modality, target_fact_id, tier, reject_reasons, raw_llm_json, model_id"
)


@pytest.mark.skipif(not BD_FILE_PATH.exists(), reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_bd_flow_overlay_on_event_streams_activity_and_is_purely_additive(tmp_path, monkeypatch):
    """B4: on_event is optional activity-stream observability, never a behavior change.
    (1) With a collector, run_bd_flow_overlay emits chunk_start/content/chunk_done for all 20 real
    chunks of the real BD file. (2) The same stub run again on a fresh cluster WITHOUT on_event
    persists byte-identical claims (see _CLAIM_COMPARISON_COLUMNS) — proving the hook adds
    observability only and never touches extraction. Two cluster_ids (not two DBs) are used so the
    second run is a genuine cache miss too: registry_sha (and therefore cache_key) is derived from
    bd_flow_nodes ids, which embed cluster_id, so the two clusters never share a cache entry."""
    from domain.doc_graph.service._build import _save_bd_flow
    from domain.model_connector.service import ProviderConfigService

    db_dir = tmp_path / "db"
    db_dir.mkdir()
    monkeypatch.setenv("CODESPECTRA_DATA_DIR", str(db_dir))
    await init_db()
    try:
        db = get_db()
        bd_content = BD_FILE_PATH.read_text(encoding="utf-8")
        doc = parse_markdown_report(str(BD_FILE_PATH), bd_content, "sha_activity_test")
        assert len(select_prose_chunks(doc)) == 20

        fake_cfg = _make_fake_provider()

        async def mock_list_all(self):
            return [fake_cfg]

        def _content(self, request):
            """Deterministic per-chunk stub: 1 valid guard_enrichment (-> P1), 1 valid risk claim
            (-> P2), 1 out-of-range claim (-> REJECTED) — same shape as the e2e stub above, citing
            real doc text pulled from the prompt so every claim validates against real BD content."""
            ls, quote = _extract_first_chunk_line(request.messages[-1].content)
            claims = [
                {
                    "candidate_id": "c1", "kind": "guard_enrichment",
                    "subject": {"id": "n/a", "type": "step", "mention": quote},
                    "relation": "validates", "object": None, "guard": None,
                    "citation": {"line_start": ls, "line_end": ls, "quote": quote},
                    "modality": "conditional", "target_fact_id": None,
                },
                {
                    "candidate_id": "c2", "kind": "risk",
                    "subject": {"id": "n/a", "type": "step", "mention": quote},
                    "relation": "invokes", "object": None, "guard": None,
                    "citation": {"line_start": ls, "line_end": ls, "quote": quote},
                    "modality": "unresolved", "target_fact_id": None,
                },
                {
                    "candidate_id": "c3", "kind": "edge",
                    "subject": {"id": "n/a", "type": "step", "mention": "x"},
                    "relation": "reads", "object": None, "guard": None,
                    "citation": {"line_start": ls - 1, "line_end": ls - 1, "quote": "x"},
                    "modality": "explicit", "target_fact_id": None,
                },
            ]
            return json.dumps({"schema_version": "bd-flow-prose-v1", "claims": claims})

        monkeypatch.setattr(ProviderConfigService, "list_all", mock_list_all)
        monkeypatch.setattr(ProviderConfigService, "chat_stream_events", _stream_of(_content))

        # ---- run 1: WITH on_event ----
        cluster_with = "cluster-activity-with-events"
        await _save_bd_flow(db, [doc], cluster_with)

        events: list[dict] = []

        async def collector(ev):
            events.append(dict(ev))

        await run_bd_flow_overlay(db, [doc], cluster_with, None, on_event=collector)

        observed_types = {e["type"] for e in events}
        assert {"chunk_start", "content", "chunk_done"} <= observed_types

        chunk_starts = [e for e in events if e["type"] == "chunk_start"]
        content_events = [e for e in events if e["type"] == "content"]
        chunk_dones = [e for e in events if e["type"] == "chunk_done"]

        assert len(chunk_starts) == 20  # 1 per chunk; stub always succeeds on attempt 1 (no retries)
        assert all(e["attempt"] == 1 for e in chunk_starts)
        assert {e["region"] for e in chunk_starts} == {
            "step_details", "exec_sequence_footnote", "failure_modes", "event_system_behavior",
        }
        assert len({e["chunk"] for e in chunk_starts}) == 20  # 20 distinct "L{start}-{end}" keys
        assert all(e["chunk"].startswith("L") and "-" in e["chunk"] for e in chunk_starts)

        assert len(content_events) == 20  # one content delta per successful attempt
        assert all(isinstance(e["text"], str) and e["text"] for e in content_events)

        assert len(chunk_dones) == 20
        assert all(e["outcome"] == "ok" for e in chunk_dones)
        assert all(e["claims"] == 3 for e in chunk_dones)  # stub always returns exactly 3 claims

        # ---- run 2: WITHOUT on_event (fresh cluster, identical stub, no collector arg at all) ----
        cluster_without = "cluster-activity-without-events"
        await _save_bd_flow(db, [doc], cluster_without)
        await run_bd_flow_overlay(db, [doc], cluster_without, None)  # on_event omitted -> default None

        async with db.execute(
            f"SELECT {_CLAIM_COMPARISON_COLUMNS} FROM bd_flow_overlay_claims WHERE cluster_id=? "
            "ORDER BY chunk_line_start, chunk_line_end, kind, relation",
            (cluster_with,),
        ) as cur:
            rows_with = [dict(r) for r in await cur.fetchall()]
        async with db.execute(
            f"SELECT {_CLAIM_COMPARISON_COLUMNS} FROM bd_flow_overlay_claims WHERE cluster_id=? "
            "ORDER BY chunk_line_start, chunk_line_end, kind, relation",
            (cluster_without,),
        ) as cur:
            rows_without = [dict(r) for r in await cur.fetchall()]

        assert len(rows_with) == 60  # 3 claims * 20 chunks
        assert rows_with == rows_without  # on_event is purely additive: zero persisted difference

        for rows in (rows_with, rows_without):
            tiers = [r["tier"] for r in rows]
            assert tiers.count("P1") == 20
            assert tiers.count("P2") == 20
            assert tiers.count("REJECTED") == 20
    finally:
        await close_db()
