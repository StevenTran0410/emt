"""LLM tier v1: Citation-Support Verification engine for BD/DD document graph."""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncGenerator
from typing import Any

from infrastructure.db.database import get_db
from shared.logger import logger
from shared.utils import utc_now_iso

from ..model_connector.service import ProviderConfigService
from ..model_connector.types import ChatMessage, ChatRequest
from .types import BuildDocGraphRequest, DocAssertion, DocGraphMismatch, ParsedDoc

SYSTEM_PROMPT = (
    "You are a documentation auditor. You judge ONLY whether the provided DD (Detailed Design) "
    "text supports the provided BD (Basic Design) claim. You must use NO outside knowledge and "
    "quote verbatim from the given texts. If the DD text is insufficient, report INSUFFICIENT — "
    "insufficient is NOT contradiction. Output JSON only."
)


def _parse_llm_json(text: str | None) -> dict[str, Any]:
    """Tolerantly parse JSON output from LLM, handling fences or surrounding prose."""
    if not text:
        raise ValueError("empty LLM content")
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if t.count("```") >= 2 else t.strip("`")
        if t.lstrip().lower().startswith("json"):
            t = t.lstrip()[4:]
    a, b = t.find("{"), t.rfind("}")
    if a != -1 and b != -1 and b > a:
        t = t[a : b + 1]
    return json.loads(t)


def _extract_bd_claim_text(doc: ParsedDoc, line_start: int, line_end: int) -> str:
    lines = doc.raw_content.splitlines()
    if not lines:
        return ""
    idx = max(0, line_start - 1)
    s_idx = max(0, idx - 2)
    e_idx = min(len(lines), line_end + 2)
    return "\n".join(lines[s_idx:e_idx]).strip()


def _extract_dd_span_text(
    doc: ParsedDoc, target_section: str | None, target_step: str | None
) -> tuple[str, int, bool]:
    lines = doc.raw_content.splitlines()
    if not lines:
        return "", 1, False

    line_start = 1
    extracted = ""

    headings = doc.section_map.headings
    steps = doc.section_map.steps

    if target_section:
        sec_clean = target_section.lower().strip()
        matched_h = None
        for h in headings:
            h_sec = str(h.get("section_id", "")).lower().strip()
            h_title = str(h.get("title", "")).lower().strip()
            if sec_clean in (h_sec, h_title) or h_sec.endswith(sec_clean):
                matched_h = h
                break

        if matched_h:
            line_start = matched_h.get("line_start", 1)
            line_end = matched_h.get("line_end", len(lines))
            extracted = "\n".join(lines[line_start - 1 : line_end]).strip()

    elif target_step:
        step_clean = target_step.lower().strip()
        matched_st = None
        for st in steps:
            st_id = str(st.get("step_id", "")).lower().strip()
            if step_clean == st_id or step_clean in st_id:
                matched_st = st
                break

        if matched_st:
            line_start = matched_st.get("line_start", 1)
            line_end = matched_st.get("line_end", len(lines))
            extracted = "\n".join(lines[line_start - 1 : line_end]).strip()

    if not extracted:
        extracted = "\n".join(lines[:100]).strip()

    truncated = False
    if len(extracted) > 6000:
        extracted = extracted[:6000]
        truncated = True

    return extracted, line_start, truncated


async def run_llm_citation_tier(
    parsed_docs: list[ParsedDoc],
    assertions: list[DocAssertion],
    cluster_id: str,
    req: BuildDocGraphRequest,
) -> list[DocGraphMismatch]:
    """Run evidence-bound citation support verification tier using LLM (bounded parallel)."""
    mismatches: list[DocGraphMismatch] = []
    async for event in run_llm_citation_tier_stream(parsed_docs, assertions, cluster_id, req):
        if event.get("type") == "llm_finding" and event.get("mismatch"):
            mismatches.append(DocGraphMismatch(**event["mismatch"]))
    return mismatches


async def run_llm_citation_tier_stream(
    parsed_docs: list[ParsedDoc],
    assertions: list[DocAssertion],
    cluster_id: str,
    req: BuildDocGraphRequest,
) -> AsyncGenerator[dict[str, Any], None]:
    """Async generator yielding progress and finding SSE events during LLM tier execution."""
    if not req.llm_enabled:
        logger.info("[doc_graph] LLM tier disabled via request; skipping citation-support tier")
        return

    service = ProviderConfigService()
    provider_id = req.llm_provider_id
    model_id: str | None = None

    if provider_id:
        try:
            cfg = await service.get_by_id(provider_id)
            model_id = cfg.model_id
        except Exception:
            logger.warning(
                f"[doc_graph] Requested provider {provider_id} not found; skipping LLM tier"
            )
            return
    else:
        configs = await service.list_all()
        chat_cfg = next((c for c in configs if not c.capabilities.embeddings), None)
        if not chat_cfg:
            logger.info("[doc_graph] no LLM provider; skipping citation-support tier")
            return
        provider_id = chat_cfg.id
        model_id = chat_cfg.model_id

    doc_map = {d.artifact_name: d for d in parsed_docs}
    for d in parsed_docs:
        doc_map[d.id] = d

    # Filter resolved BD citations
    bd_cites = [a for a in assertions if a.side == "bd" and a.predicate == "cites"]
    citation_items: list[dict[str, Any]] = []

    for a in bd_cites:
        target_doc_name = a.qualifiers.get("target_doc")
        if not target_doc_name:
            continue
        target_doc = doc_map.get(target_doc_name) or doc_map.get(f"doc/{target_doc_name}")
        bd_doc = doc_map.get(a.doc_id) or doc_map.get(a.doc_id.replace("doc/", ""))
        if not target_doc or not bd_doc:
            continue

        target_sec = a.qualifiers.get("target_section")
        target_step = a.qualifiers.get("target_step")
        l_start = a.doc_span.get("line_start", 1)
        l_end = a.doc_span.get("line_end", 1)

        bd_claim_text = _extract_bd_claim_text(bd_doc, l_start, l_end)
        dd_span_text, dd_line_start, truncated = _extract_dd_span_text(
            target_doc, target_sec, target_step
        )

        if not bd_claim_text or not dd_span_text:
            continue

        cache_raw = f"{bd_claim_text}:{dd_span_text}:{model_id or 'default'}"
        cache_key = hashlib.sha256(cache_raw.encode("utf-8")).hexdigest()

        citation_items.append({
            "assertion": a,
            "bd_doc": bd_doc,
            "target_doc": target_doc,
            "target_sec": target_sec,
            "target_step": target_step,
            "l_start": l_start,
            "bd_claim_text": bd_claim_text,
            "dd_span_text": dd_span_text,
            "dd_line_start": dd_line_start,
            "truncated": truncated,
            "cache_key": cache_key,
        })

    total_citations = len(citation_items)
    if total_citations == 0:
        return

    yield {"type": "llm_progress", "done": 0, "total": total_citations}

    db = get_db()
    cached_verdicts: dict[str, dict[str, Any]] = {}
    misses: list[dict[str, Any]] = []

    # Pass 1: Check cache sequentially
    for item in citation_items:
        ckey = item["cache_key"]
        async with db.execute(
            "SELECT verdict_json FROM doc_graph_llm_cache WHERE key=?", (ckey,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                try:
                    cached_verdicts[ckey] = json.loads(row["verdict_json"])
                except Exception:
                    misses.append(item)
            else:
                misses.append(item)

    # Pass 2: Concurrently execute LLM calls for misses using Semaphore(6)
    sem = asyncio.Semaphore(6)
    fresh_verdicts: dict[str, dict[str, Any]] = {}

    async def fetch_worker(item: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        async with sem:
            ref_label = item["target_sec"] or item["target_step"] or "Section"
            user_prompt = (
                f"BD Claim (Doc: {item['bd_doc'].artifact_name}, Line {item['l_start']}):\n"
                f'"{item["bd_claim_text"]}"\n\n'
                f"Cited DD Span (Doc: {item['target_doc'].artifact_name}, Ref: {ref_label}):\n"
                f'"{item["dd_span_text"]}"\n\n'
                "Judge whether the DD span supports the BD claim. Output JSON only with fields: "
                "verdict (FULL_SUPPORT|PARTIAL_SUPPORT|CONTRADICTED|INSUFFICIENT), bd_quote, "
                "dd_quote, uncovered_subclaims, rationale, confidence."
            )

            try:
                chat_req = ChatRequest(
                    provider_id=provider_id,
                    model_id=model_id,
                    messages=[
                        ChatMessage(role="system", content=SYSTEM_PROMPT),
                        ChatMessage(role="user", content=user_prompt),
                    ],
                    max_completion_tokens=20000,
                    temperature=0.0,
                    json_mode=True,
                )
                resp = await service.chat(chat_req)
                verdict_data = _parse_llm_json(resp.content)
            except Exception as e:
                l_start = item["l_start"]
                logger.warning(
                    f"[doc_graph] LLM tier call/parse error for citation at line {l_start}: {e}"
                )
                verdict_data = {
                    "verdict": "NEEDS_REVIEW",
                    "bd_quote": item["bd_claim_text"][:100],
                    "dd_quote": "",
                    "uncovered_subclaims": [],
                    "rationale": f"LLM evaluation failed or returned invalid output: {e}",
                    "confidence": "low",
                }
            return item, verdict_data

    if misses:
        worker_results = await asyncio.gather(*(fetch_worker(m) for m in misses))
        now = utc_now_iso()
        for item, v_data in worker_results:
            fresh_verdicts[item["cache_key"]] = v_data
            await db.execute(
                "INSERT OR REPLACE INTO doc_graph_llm_cache (key, verdict_json, created_at) "
                "VALUES (?, ?, ?)",
                (item["cache_key"], json.dumps(v_data), now),
            )
        await db.commit()

    # Pass 3: Adjudicate all citations sequentially & yield progress/findings
    mismatch_count = 0
    for idx, item in enumerate(citation_items, 1):
        ckey = item["cache_key"]
        verdict_data = cached_verdicts.get(ckey) or fresh_verdicts.get(ckey) or {}

        verdict = str(verdict_data.get("verdict", "")).upper()
        bd_quote = str(verdict_data.get("bd_quote", ""))
        dd_quote = str(verdict_data.get("dd_quote", ""))
        rationale = str(verdict_data.get("rationale", ""))
        conf = str(verdict_data.get("confidence", "medium")).lower()
        uncovered = verdict_data.get("uncovered_subclaims") or []

        yield {"type": "llm_progress", "done": idx, "total": total_citations}

        if verdict == "FULL_SUPPORT" and conf != "low":
            continue

        if verdict == "CONTRADICTED":
            mm_type = "contradicted_citation"
            sev = "error"
        elif verdict == "PARTIAL_SUPPORT":
            mm_type = "partial_support"
            sev = "warning"
        elif verdict == "INSUFFICIENT":
            mm_type = "unsupported_claim"
            sev = "warning"
        else:
            mm_type = "needs_review"
            sev = "info"

        a = item["assertion"]
        target_doc = item["target_doc"]
        target_sec = item["target_sec"]
        target_step = item["target_step"]
        l_start = item["l_start"]
        dd_line_start = item["dd_line_start"]
        truncated = item["truncated"]

        bd_loc = {
            "doc": a.doc_id,
            "section": a.doc_span.get("section_id"),
            "line": l_start,
        }
        dd_loc = {
            "doc": f"doc/{target_doc.artifact_name}",
            "section": target_sec or target_step,
            "line": dd_line_start,
        }

        target_ref = target_sec or target_step or ""
        fp_raw = (
            f"llm:{mm_type}:{a.doc_id}:{l_start}:{target_doc.artifact_name}:"
            f"{target_ref}:{mismatch_count}"
        )
        fp = hashlib.sha256(fp_raw.encode("utf-8")).hexdigest()
        mismatch_count += 1

        evidence_entry = {
            "bd_quote": bd_quote,
            "dd_quote": dd_quote,
            "verdict": verdict,
            "confidence": conf,
            "uncovered_subclaims": uncovered,
            "provider_id": provider_id,
            "model_id": model_id,
            "truncated": truncated,
        }

        mismatch_obj = DocGraphMismatch(
            cluster_id=cluster_id,
            fingerprint=fp,
            mismatch_type=mm_type,
            severity=sev,
            derivation="llm",
            bd_location=bd_loc,
            dd_location=dd_loc,
            description=rationale or f"LLM citation verdict: {verdict}",
            evidence=[evidence_entry],
            confidence=conf,
            created_at=utc_now_iso(),
        )

        yield {"type": "llm_finding", "mismatch": mismatch_obj.model_dump()}
