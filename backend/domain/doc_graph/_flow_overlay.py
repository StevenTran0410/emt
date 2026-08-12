"""BD Flow LLM overlay — Stage 2/3/4: extract, hard-validate, and persist candidate claims.

One LLM call per whitelisted prose chunk (see _flow_prose.py). Everything the model returns is a
*candidate* — nothing here ever touches bd_flow_nodes/bd_flow_edges. Hallucinated edges are worse
than missed ones, so every claim passes a programmatic validator before it earns a P1/P2 tier;
claims that fail are still persisted as tier=REJECTED for audit, never silently dropped.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, ValidationError

from shared.logger import logger
from shared.utils import new_id, utc_now_iso

from ..model_connector.service import ProviderConfigService
from ..model_connector.types import ChatMessage, ChatRequest
from ._flow_prose import ProseChunk, select_prose_chunks
from ._llm_citation import _parse_llm_json  # reuse fence/prose-tolerant JSON extraction only
from .types import ParsedDoc

SCHEMA_VERSION = "bd-flow-prose-v1"
# v6: expanded system prompt with closed enum vocabularies (B1), updated reasoning effort attempt 1 to high (C1).
PROMPT_VERSION = "6"

# Chunk calls are independent, so they already fan out concurrently; this caps how many hit the
# provider at once. Higher = faster (fewer waves over ~20 chunks) but risks provider rate-limits.
_MAX_CONCURRENT_LLM = 10

SubjectObjectType = Literal["program", "step", "event", "dataset", "screen", "external", "fact"]
RelationType = Literal[
    "declares", "invokes", "routes_to", "submits", "reads", "writes", "updates",
    "transfers_to", "validates", "skips", "retries", "does_not_feed", "may_leave_partial",
]
GuardOperator = Literal[
    "eq", "ne", "lt", "le", "gt", "ge", "in", "not_in", "starts_with", "and", "or", "else", "exists"
]
ClaimKind = Literal["node", "edge", "guard_enrichment", "negative_edge", "risk"]
Modality = Literal["explicit", "conditional", "negative", "risk", "unresolved"]

# Structural guard connectives carry no literal value of their own to verify against the quote.
_STRUCTURAL_GUARD_OPERATORS = frozenset({"and", "or", "else", "exists"})

_NEGATION_RE = re.compile(r"\b(no|not|never)\b|does\s+not|without|取止め", re.IGNORECASE)

_RELATIONS = (
    "declares", "invokes", "routes_to", "submits", "reads", "writes", "updates",
    "transfers_to", "validates", "skips", "retries", "does_not_feed", "may_leave_partial",
)
_KINDS = get_args(ClaimKind)
_MODALITIES = get_args(Modality)
_GUARD_OPERATORS = get_args(GuardOperator)
_SUBJECT_OBJECT_TYPES = get_args(SubjectObjectType)

# One-shot shape example. Line 42 is illustrative and NOT in any real chunk — the model must never
# copy it; it only fixes the output SHAPE so the model pattern-matches instead of reasoning freely.
_EXAMPLE = (
    'EXAMPLE (shape only — never reuse its text or line number):\n'
    'Chunk line -> "42: If OPT=1 then control transfers to PHNIXLOT."\n'
    'Registry   -> "N007 | PHNIXLOT | asset"\n'
    'Output     -> {"schema_version":"bd-flow-prose-v1","claims":[{"candidate_id":"c1",'
    '"kind":"guard_enrichment","subject":{"id":"N007","type":"program","mention":"PHNIXLOT"},'
    '"relation":"routes_to","object":null,"guard":{"operator":"eq","operands":["OPT","1"],'
    '"source_text":"If OPT=1"},"citation":{"line_start":42,"line_end":42,'
    '"quote":"If OPT=1 then control transfers to PHNIXLOT."},"modality":"conditional",'
    '"target_fact_id":"N007"}]}'
)

# All invariant instructions live here (not in the per-chunk user message) so the provider caches
# this whole prefix across every chunk call — only the chunk text + registry vary downstream.
_SYSTEM_PROMPT = (
    "You are a deterministic extractor, not an assistant. From ONE short Business-Design prose chunk "
    "you copy structured flow claims that are LITERALLY stated in it. Do not deliberate, do not "
    "reason step by step, do not explain. Emit ONE JSON object and nothing else, immediately.\n\n"
    "Emit a claim ONLY for: a guard/condition on a step, a negative fact (\"X does not ...\"), or a "
    "risk/failure note. Most chunks yield 0-2 claims; many yield none. When unsure, omit — do not guess.\n\n"
    "RULES (mechanical, no reasoning needed):\n"
    "1. quote = verbatim copy from the numbered lines; set line_start/line_end to those lines.\n"
    "2. subject.mention, object.mention, and guard left-hand operand MUST be substrings of quote (or chunk text).\n"
    "3. Reference a registry node ONLY by its alias (left column) in target_fact_id / subject.id "
    "/ object.id. Never invent an id, node, relation, or guard absent from the text.\n"
    "4. Output ONLY a JSON object. No prose, no reasoning.\n\n"
    f"Allowed relations: {', '.join(_RELATIONS)}\n"
    f"Allowed kinds: {', '.join(_KINDS)}\n"
    f"Allowed modalities: {', '.join(_MODALITIES)}\n"
    f"Allowed guard operators: {', '.join(_GUARD_OPERATORS)}\n"
    f"Allowed subject/object entity types: {', '.join(_SUBJECT_OBJECT_TYPES)}\n"
    "(Note: subject/object.type is the entity type, which is different from the registry's node_kind column.)\n\n"
    f"{_EXAMPLE}\n\n"
    f'If nothing qualifies, output exactly {{"schema_version": "{SCHEMA_VERSION}", "claims": []}}.'
)


class ClaimEndpoint(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    id: str
    type: SubjectObjectType
    mention: str


class ClaimGuard(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    operator: GuardOperator
    operands: list[str] = []
    source_text: str = ""


class ClaimCitation(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    line_start: int
    line_end: int
    quote: str


class OverlayClaim(BaseModel):
    """One candidate claim — schema `bd-flow-prose-v1`. Strict: no silent type coercion."""

    model_config = ConfigDict(strict=True, extra="forbid")
    candidate_id: str
    kind: ClaimKind
    subject: ClaimEndpoint
    relation: RelationType
    object: ClaimEndpoint | None = None
    guard: ClaimGuard | None = None
    citation: ClaimCitation
    modality: Modality
    target_fact_id: str | None = None


class RawOverlayEnvelope(BaseModel):
    """Permissive top-level envelope: individual claims are validated one-by-one so a single bad
    claim never discards the rest of the LLM's response. schema_version is advisory only — weak
    models routinely mangle the envelope key, so we key on the presence of a `claims` list, not on
    schema_version. Every claim still passes the hard validator downstream regardless."""

    model_config = ConfigDict(extra="ignore")
    schema_version: str | None = None
    claims: list[dict[str, Any]] = []


def _norm_markdown(s: str) -> str:
    """Strip formatting decoration characters (` * _) and collapse space runs."""
    if not s:
        return ""
    cleaned = re.sub(r"[`*_]", "", s)
    return re.sub(r"\s+", " ", cleaned).strip()


def _hard_validate(
    claim: OverlayClaim,
    chunk: ProseChunk,
    doc_lines: list[str],
    registry_ids: set[str],
    node_binding: dict[str, str],
) -> tuple[str, list[str] | None]:
    """Reject-or-tier a schema-valid claim. Every gate below is independent (all "ANY of" the
    ticket's reject conditions), so a claim can accumulate multiple reject reasons at once."""
    reasons: list[str] = []
    c = claim.citation
    total_lines = len(doc_lines)

    in_range = (
        chunk.line_start <= c.line_start <= c.line_end <= chunk.line_end
        and 1 <= c.line_start <= total_lines
        and 1 <= c.line_end <= total_lines
    )
    if not in_range:
        reasons.append("citation_out_of_chunk_range")

    # Out-of-range spans can't be safely sliced — fail the substring check closed, not open.
    quote_source = "\n".join(doc_lines[c.line_start - 1 : c.line_end]) if in_range else ""
    norm_quote = _norm_markdown(c.quote)
    norm_quote_source = _norm_markdown(quote_source)
    norm_chunk = _norm_markdown(chunk.text)

    if not in_range or c.quote == "" or norm_quote not in norm_quote_source:
        reasons.append("quote_not_exact_substring")

    norm_subj = _norm_markdown(claim.subject.mention)
    if norm_subj not in norm_quote and norm_subj not in norm_chunk:
        reasons.append("subject_mention_not_in_quote")

    if claim.object is not None:
        norm_obj = _norm_markdown(claim.object.mention)
        if norm_obj not in norm_quote and norm_obj not in norm_chunk:
            reasons.append("object_mention_not_in_quote")

    if claim.guard is not None and claim.guard.operator not in _STRUCTURAL_GUARD_OPERATORS:
        if claim.guard.operands:
            norm_op0 = _norm_markdown(claim.guard.operands[0])
            if norm_op0 not in norm_quote and norm_op0 not in norm_chunk:
                reasons.append("guard_operand_not_in_quote")

    # subject.id/object.id absent from the registry is tolerated (new_identifier case) as long as
    # the mention naming it is verified inside the quote above; target_fact_id has no mention to
    # fall back on, so an unknown target_fact_id is always rejected — it can't be a "new" fact.
    if claim.target_fact_id:
        if claim.target_fact_id not in registry_ids:
            reasons.append("target_fact_id_unknown")
        else:
            binding = node_binding.get(claim.target_fact_id)
            known_registry_tokens = set(node_binding.keys()) | set(node_binding.values())
            subj_is_known = claim.subject.id in known_registry_tokens
            obj_is_known = claim.object is not None and claim.object.id in known_registry_tokens

            # When subject/object are tolerated new-identifiers (neither is in the registry),
            # do not require equality to target_fact_id's binding — target_fact_id is known in registry.
            if subj_is_known or obj_is_known:
                matches = binding is not None and (
                    binding == claim.subject.id
                    or (claim.object is not None and binding == claim.object.id)
                )
                if not matches:
                    reasons.append("target_fact_id_binding_mismatch")

    if claim.modality == "negative" and not _NEGATION_RE.search(norm_quote or c.quote):
        reasons.append("negative_modality_without_negation_wording")

    if reasons:
        return "REJECTED", reasons

    # kind in (node, edge) is always review-only P2, even if it would otherwise qualify for P1.
    if claim.kind in ("guard_enrichment", "negative_edge") and claim.modality in (
        "explicit",
        "conditional",
        "negative",
    ):
        return "P1", None
    return "P2", None


def _row_from_claim(
    raw_claim: Any,
    chunk: ProseChunk,
    doc: ParsedDoc,
    doc_lines: list[str],
    cluster_id: str,
    registry_ids: set[str],
    node_binding: dict[str, str],
    model_id: str | None,
    now: str,
    alias_map: dict[str, dict[str, Any]] | None = None,
) -> tuple:
    if not isinstance(raw_claim, dict):
        raw_claim = {}

    try:
        claim = OverlayClaim.model_validate(raw_claim)
    except ValidationError as e:
        kind = str(raw_claim.get("kind")) if raw_claim.get("kind") else "unknown"
        candidate_id = raw_claim.get("candidate_id") or new_id()
        row_id = f"bdflowoverlay:{cluster_id}:{doc.id}:{chunk.line_start}:{chunk.line_end}:{candidate_id}"
        errors_detail = [
            {
                "loc": [str(p) for p in err.get("loc", [])],
                "msg": err.get("msg"),
                "type": err.get("type"),
                "input": str(err.get("input")),
            }
            for err in e.errors()
        ]
        return (
            row_id, cluster_id, doc.id, chunk.line_start, chunk.line_end, chunk.region_kind,
            kind, None, None, None, None, None, None, None,
            "REJECTED", json.dumps([f"schema_validation_failed: {e.error_count()} error(s)", errors_detail]),
            json.dumps(raw_claim), model_id, now,
        )

    if alias_map:
        claim = _dealias_claim(claim, alias_map)
    tier, reasons = _hard_validate(claim, chunk, doc_lines, registry_ids, node_binding)
    row_id = (
        f"bdflowoverlay:{cluster_id}:{doc.id}:{chunk.line_start}:{chunk.line_end}:"
        f"{claim.candidate_id}"
    )
    return (
        row_id, cluster_id, doc.id, chunk.line_start, chunk.line_end, chunk.region_kind,
        claim.kind, claim.relation,
        json.dumps(claim.subject.model_dump()),
        json.dumps(claim.object.model_dump()) if claim.object else None,
        json.dumps(claim.guard.model_dump()) if claim.guard else None,
        json.dumps(claim.citation.model_dump()),
        claim.modality, claim.target_fact_id,
        tier, json.dumps(reasons) if reasons else None,
        json.dumps(raw_claim), model_id, now,
    )


def _build_registry(
    node_rows: list[Any],
) -> tuple[set[str], dict[str, str]]:
    registry_ids = {r["id"] for r in node_rows}
    node_binding = {r["id"]: (r["binding"] or r["id"]) for r in node_rows}
    return registry_ids, node_binding


def _node_sub_ix(node_id: str) -> int | None:
    # id shape: bdflow:{cluster}:{doc_id}:{sub_ix}:... — cluster/doc_id never contain ':'.
    parts = node_id.split(":")
    try:
        return int(parts[3])
    except (IndexError, ValueError):
        return None


def _chunk_registry(
    node_rows: list[Any], chunk: ProseChunk
) -> tuple[dict[str, dict[str, Any]], str, str]:
    """Per-chunk filtered registry with compact aliases. Over-inclusion is harmless; omission hides
    a legitimate attachment target from the model, so filters err on the wide side."""
    kind = chunk.region_kind

    def relevant(r: Any) -> bool:
        if kind == "step_details":
            return _node_sub_ix(r["id"]) == 2 or r["node_kind"] == "asset"
        if kind in ("exec_sequence_footnote", "failure_modes"):
            return r["node_kind"] in ("job_step", "asset")
        if kind == "event_system_behavior":
            if r["node_kind"] == "event":
                return True
            line = r["doc_line_start"] if "doc_line_start" in r.keys() else None
            return (
                _node_sub_ix(r["id"]) == 6
                and line is not None
                and chunk.line_start - 120 <= line <= chunk.line_end + 120
            )
        return True

    filtered = [r for r in node_rows if relevant(r)]
    if len(filtered) < 5:
        filtered = list(node_rows)

    filtered.sort(key=lambda r: r["id"])
    alias_map: dict[str, dict[str, Any]] = {}
    prompt_lines: list[str] = []
    for i, r in enumerate(filtered):
        alias = f"N{i + 1:03d}"
        display = r["binding"] or r["id"].rsplit(":", 1)[-1]
        alias_map[alias] = {"id": r["id"], "binding": r["binding"]}
        prompt_lines.append(f"{alias} | {display} | {r['node_kind']}")

    registry_sha = hashlib.sha256(
        "|".join(f"{r['id']}={r['binding'] or ''}" for r in filtered).encode("utf-8")
    ).hexdigest()
    return alias_map, registry_sha, "\n".join(prompt_lines)


def _dealias_claim(claim: OverlayClaim, alias_map: dict[str, dict[str, Any]]) -> OverlayClaim:
    """Translate compact aliases back to real ids before validation/persistence. subject/object ids
    map to the node's binding (the validator compares them against bindings); target_fact_id maps
    to the real node id. Non-alias values pass through untouched."""
    updates: dict[str, Any] = {}
    if claim.target_fact_id and claim.target_fact_id in alias_map:
        updates["target_fact_id"] = alias_map[claim.target_fact_id]["id"]
    if claim.subject.id in alias_map:
        e = alias_map[claim.subject.id]
        updates["subject"] = claim.subject.model_copy(update={"id": e["binding"] or e["id"]})
    if claim.object is not None and claim.object.id in alias_map:
        e = alias_map[claim.object.id]
        updates["object"] = claim.object.model_copy(update={"id": e["binding"] or e["id"]})
    return claim.model_copy(update=updates) if updates else claim


def _cache_key(
    doc: ParsedDoc, chunk: ProseChunk, provider_id: str, model_id: str | None, registry_sha: str
) -> str:
    chunk_sha = hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
    raw = (
        f"{doc.content_sha256}:{chunk.line_start}-{chunk.line_end}:{chunk_sha}:"
        f"{PROMPT_VERSION}:{SCHEMA_VERSION}:{provider_id}:{model_id or ''}:{registry_sha}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _build_chat_request(
    provider_id: str,
    model_id: str | None,
    chunk: ProseChunk,
    registry_prompt: str,
    reasoning_effort: str | None = None,
) -> ChatRequest:
    numbered = "\n".join(
        f"{chunk.line_start + i}: {line}" for i, line in enumerate(chunk.text.splitlines())
    )
    # Only the variable part goes here; all fixed instructions live in _SYSTEM_PROMPT for cache reuse.
    user_prompt = (
        f"Region kind: {chunk.region_kind}\n"
        f"BD prose chunk (lines {chunk.line_start}-{chunk.line_end}):\n{numbered}\n\n"
        f"Allowed node registry (alias | name | node_kind) relevant to this chunk:\n"
        f"{registry_prompt}"
    )
    return ChatRequest(
        provider_id=provider_id,
        model_id=model_id,
        messages=[
            ChatMessage(role="system", content=_SYSTEM_PROMPT),
            ChatMessage(role="user", content=user_prompt),
        ],
        max_completion_tokens=20000,
        temperature=0.0,
        json_mode=True,
        reasoning_effort=reasoning_effort,
    )


async def _resolve_provider(
    service: ProviderConfigService, provider_id: str | None
) -> tuple[str | None, str | None]:
    if provider_id:
        try:
            cfg = await service.get_by_id(provider_id)
            return provider_id, cfg.model_id
        except Exception:
            logger.warning(
                "[doc_graph] Requested provider %s not found; skipping BD flow prose overlay",
                provider_id,
            )
            return None, None
    configs = await service.list_all()
    chat_cfg = next((c for c in configs if not c.capabilities.embeddings), None)
    if not chat_cfg:
        return None, None
    return chat_cfg.id, chat_cfg.model_id


_INSERT_CLAIM_SQL = """
INSERT INTO bd_flow_overlay_claims
(id, cluster_id, doc_id, chunk_line_start, chunk_line_end, region_kind,
 kind, relation, subject_json, object_json, guard_json, citation_json,
 modality, target_fact_id, tier, reject_reasons, raw_llm_json, model_id, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

OverlayEventHook = Callable[[dict[str, Any]], Awaitable[None]]


def _chunk_key(chunk: ProseChunk) -> str:
    return f"L{chunk.line_start}-{chunk.line_end}"


async def _emit_event(on_event: OverlayEventHook | None, event: dict[str, Any]) -> None:
    """Best-effort activity-stream emit — absent or failing hook must never affect extraction."""
    if on_event is None:
        return
    try:
        await on_event(event)
    except Exception:
        pass


async def run_bd_flow_overlay(
    db: Any,
    parsed_docs: list[ParsedDoc],
    cluster_id: str,
    provider_id: str | None,
    on_event: OverlayEventHook | None = None,
) -> None:
    """LLM prose overlay over whitelisted BD regions. Never mutates bd_flow_nodes/bd_flow_edges —
    everything lands in bd_flow_overlay_claims, tiered P1/P2/REJECTED. Offline-safe: with no
    provider configured this is a no-op after clearing stale rows."""
    # Clear first so a rebuild (or a rebuild with the LLM tier now disabled/misconfigured) never
    # leaves stale claims from a previous run attached to this cluster.
    await db.execute("DELETE FROM bd_flow_overlay_claims WHERE cluster_id=?", (cluster_id,))
    await db.commit()

    bd_docs = [d for d in parsed_docs if d.doc_kind == "bd"]
    if not bd_docs:
        return

    service = ProviderConfigService()
    resolved_provider_id, model_id = await _resolve_provider(service, provider_id)
    if not resolved_provider_id:
        logger.info("[doc_graph] no LLM provider; skipping BD flow prose overlay")
        return

    async with db.execute(
        "SELECT id, binding, node_kind, doc_line_start FROM bd_flow_nodes WHERE cluster_id=?",
        (cluster_id,),
    ) as cur:
        node_rows = await cur.fetchall()
    if not node_rows:
        logger.info(
            "[doc_graph] no BD flow skeleton for cluster %s; skipping prose overlay", cluster_id
        )
        return
    registry_ids, node_binding = _build_registry(node_rows)

    items: list[dict[str, Any]] = []
    doc_lines_by_id: dict[str, list[str]] = {}
    for doc in bd_docs:
        doc_lines_by_id[doc.id] = doc.raw_content.splitlines()
        for chunk in select_prose_chunks(doc):
            alias_map, registry_sha, registry_prompt = _chunk_registry(node_rows, chunk)
            cache_key = _cache_key(doc, chunk, resolved_provider_id, model_id, registry_sha)
            items.append({
                "doc": doc, "chunk": chunk, "cache_key": cache_key,
                "alias_map": alias_map, "registry_prompt": registry_prompt,
            })

    if not items:
        return

    # Pass 1: cache lookup, sequential (safe for a single aiosqlite connection).
    cached: dict[str, tuple[str, str]] = {}
    misses: list[dict[str, Any]] = []
    for item in items:
        async with db.execute(
            "SELECT status, response_json FROM bd_flow_llm_cache WHERE cache_key=?",
            (item["cache_key"],),
        ) as cur:
            row = await cur.fetchone()
        if row:
            cached[item["cache_key"]] = (row["status"], row["response_json"])
        else:
            misses.append(item)

    # Pass 2: concurrent LLM calls for misses, bounded fan-out.
    sem = asyncio.Semaphore(_MAX_CONCURRENT_LLM)
    fresh: dict[str, tuple[str, str]] = {}

    async def _attempt(
        chunk: ProseChunk, registry_prompt: str, effort: str | None, attempt: int = 1
    ) -> tuple[str, str | None]:
        """One provider call -> ('ok', claims_json) | ('empty'|'transport', None) | ('nonjson', text).

        Uses SSE streaming: token/reasoning deltas keep the socket warm, so a long generation never
        trips the provider client's inter-chunk read timeout the way one blocking call would."""
        chunk_key = _chunk_key(chunk)
        await _emit_event(
            on_event,
            {"type": "chunk_start", "chunk": chunk_key, "region": chunk.region_kind, "attempt": attempt},
        )
        req = _build_chat_request(
            resolved_provider_id, model_id, chunk, registry_prompt, reasoning_effort=effort
        )
        try:
            parts: list[str] = []
            final: str | None = None
            async for ev in service.chat_stream_events(req):
                ev_type = ev.get("type")
                if ev_type == "content":
                    text = ev.get("text") or ""
                    parts.append(text)
                    await _emit_event(on_event, {"type": "content", "chunk": chunk_key, "text": text})
                elif ev_type == "thinking":
                    await _emit_event(
                        on_event, {"type": "thinking", "chunk": chunk_key, "text": ev.get("text") or ""}
                    )
                elif ev_type == "done":
                    final = ev.get("content")
            content = (final if final is not None else "".join(parts)).strip()
        except Exception as e:
            logger.warning(
                "[doc_graph] BD overlay LLM stream failed for chunk %s-%s (effort=%s): %s",
                chunk.line_start, chunk.line_end, effort, e,
            )
            return "transport", None

        if not content:
            return "empty", None
        try:
            parsed = _parse_llm_json(content)
        except Exception:
            return "nonjson", content
        # Salvage the claims list wherever it is; a mangled schema_version key no longer discards a
        # response that carried perfectly good claims.
        claims = parsed.get("claims") if isinstance(parsed, dict) else None
        return "ok", json.dumps({"claims": claims if isinstance(claims, list) else []})

    async def fetch(item: dict[str, Any]) -> tuple[str, tuple[str, str] | None]:
        chunk = item["chunk"]
        chunk_key = _chunk_key(chunk)
        async with sem:
            # Attempt 1 uses reasoning effort "high" (bounded by _reasoning_budget to preserve completion tokens).
            outcome, payload = await _attempt(chunk, item["registry_prompt"], "high", attempt=1)
            if outcome != "ok":
                # One retry with a bounded amount of reasoning, for genuinely ambiguous chunks.
                logger.info(
                    "[doc_graph] BD overlay retrying chunk %s-%s at low reasoning effort (was: %s)",
                    chunk.line_start, chunk.line_end, outcome,
                )
                outcome, payload = await _attempt(chunk, item["registry_prompt"], "low", attempt=2)

            if outcome == "ok":
                # Best-effort candidate count (pre hard-validate — final P1/P2/REJECTED tiers are
                # decided in Pass 3); purely for live observability, never affects persistence.
                claim_count = 0
                try:
                    claim_count = len(json.loads(payload).get("claims") or []) if payload else 0
                except Exception:
                    claim_count = 0
                await _emit_event(
                    on_event,
                    {"type": "chunk_done", "chunk": chunk_key, "outcome": "ok", "claims": claim_count},
                )
                return item["cache_key"], ("ok", payload)
            if outcome == "nonjson":
                # Non-JSON even after retry is deterministic garbage — cache 0 claims so we don't
                # re-call it every rebuild.
                logger.info(
                    "[doc_graph] BD overlay non-JSON after retry for chunk %s-%s; treating as 0 claims",
                    chunk.line_start, chunk.line_end,
                )
                await _emit_event(
                    on_event, {"type": "chunk_done", "chunk": chunk_key, "outcome": "nonjson", "claims": 0}
                )
                return item["cache_key"], ("ok", json.dumps({"claims": []}))
            # transport/empty after retry — transient, never cached so a rebuild retries.
            logger.info(
                "[doc_graph] BD overlay %s after retry for chunk %s-%s; will retry on rebuild",
                outcome, chunk.line_start, chunk.line_end,
            )
            await _emit_event(
                on_event, {"type": "chunk_done", "chunk": chunk_key, "outcome": outcome, "claims": 0}
            )
            return item["cache_key"], None

    if misses:
        fetch_results = await asyncio.gather(*(fetch(m) for m in misses))
        now = utc_now_iso()
        for cache_key, result in fetch_results:
            if result is None:
                continue
            fresh[cache_key] = result
            await db.execute(
                "INSERT OR REPLACE INTO bd_flow_llm_cache "
                "(cache_key, response_json, status, created_at) VALUES (?, ?, ?, ?)",
                (cache_key, result[1], result[0], now),
            )
        await db.commit()

    # Pass 3: validate + persist, sequential.
    all_rows: list[tuple] = []
    now = utc_now_iso()
    for item in items:
        status_pair = cached.get(item["cache_key"]) or fresh.get(item["cache_key"])
        if not status_pair or status_pair[0] != "ok":
            continue
        try:
            envelope = RawOverlayEnvelope.model_validate(json.loads(status_pair[1]))
        except Exception:
            continue

        doc = item["doc"]
        chunk = item["chunk"]
        doc_lines = doc_lines_by_id[doc.id]
        for raw_claim in envelope.claims:
            all_rows.append(
                _row_from_claim(
                    raw_claim, chunk, doc, doc_lines, cluster_id, registry_ids, node_binding,
                    model_id, now, alias_map=item["alias_map"],
                )
            )

    if all_rows:
        await db.executemany(_INSERT_CLAIM_SQL, all_rows)
        await db.commit()
