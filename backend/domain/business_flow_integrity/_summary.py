"""Executive Summary generation for Phase 3 Business Flow Integrity (TICKET P3-9, P3-10)."""
from __future__ import annotations

import json
import logging
import re
from typing import Any
from pydantic import BaseModel, Field, ValidationError

from domain.model_connector.service import ProviderConfigService
from domain.model_connector.types import ChatMessage, ChatRequest
from domain.business_flow_integrity._queries import get_e2e_flow_map, get_flow_integrity_findings

logger = logging.getLogger("codespectra.bfi.summary")


class ExecutiveSummaryResponse(BaseModel):
    overall_verdict: str = Field(description="PASS or FAIL overall verdict for business flow integrity")
    headline: str = Field(description="One sentence high-level executive summary of flow integrity status")
    key_risks: list[str] = Field(description="Bullet points detailing critical flow risks and contradictions")
    coverage_note: str = Field(description="Summary of unindexed or unverified BD steps and external boundaries")
    recommendation: str = Field(description="Actionable next steps for documentation alignment")


_SUMMARY_SYSTEM_PROMPT = """You are a senior enterprise software architect evaluating a Business Flow Integrity audit report for a legacy system migration.
You will receive a JSON summary of the alignment findings between Basic Design (BD) flow documentation and extracted source code routes.

Rules:
1. Focus ONLY on flow integrity (step ordering, route abstraction faithfulness, contradictions, missing code routes, coverage caveats).
2. Do NOT evaluate numeric value correctness or database schemas.
3. Cite specific BD references and source files as concrete examples for every contradiction, omission, and unknown you mention.
4. Respond ONLY in English.
5. Output MUST strictly match the requested JSON schema.
"""


def _clean_br(text: str | None) -> str:
    """Strip HTML <br/> tags from string for clean LLM prompt and summary formatting."""
    if not text:
        return ""
    return re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE).strip()


def _generate_fallback_summary(
    findings: dict[str, Any], flow_map: dict[str, Any]
) -> dict[str, Any]:
    """Generate a deterministic executive summary from factual findings when LLM is offline/unavailable."""
    cal = findings.get("calibration", {})
    match_pct = cal.get("match_percentage", 0.0)
    calibration_pass = cal.get("calibration_pass", False)
    broken_findings = findings.get("broken_unknown_findings", [])
    code_only_findings = findings.get("code_only_findings", [])
    unknown_count = findings.get("collapsed_unknown_count", 0)

    stale_missing = [f for f in broken_findings if f.get("ai_bucket") == "stale_missing"]

    verdict_str = "PASS" if calibration_pass else "FAIL"
    headline = (
        f"Flow integrity calibration is {match_pct:.1f}% ({verdict_str}) over {cal.get('resolved_units', 0)} resolved units. "
        f"Identified {len(stale_missing)} stale-missing contradictions and {len(code_only_findings)} undocumented code routes."
    )

    key_risks: list[str] = []
    for sm in stale_missing:
        bd_span = _clean_br(sm.get("bd_span", "BD Step"))
        code_fact = sm.get("code_fact")
        code_info = f" (resolved code: {code_fact})" if code_fact and code_fact != "No code route" else ""
        key_risks.append(
            f"Contradiction at {bd_span}{code_info}: {sm.get('reason', 'BD documents asset as missing but code graph resolves it.')}"
        )
    for co in code_only_findings:
        key_risks.append(
            f"Undocumented code asset: {co.get('rel_path', 'source file')} ({co.get('reason', 'Reachable code route omitted from BD.')})"
        )

    if not key_risks:
        key_risks.append("No critical contradictions or undocumented code routes detected.")

    unknown_findings = findings.get("unknown_findings", [])
    unknown_examples: list[str] = []
    for uf in unknown_findings[:3]:
        ref = uf.get("bd_reference") or _clean_br(uf.get("bd_span"))
        if ref and ref not in unknown_examples:
            unknown_examples.append(ref)

    ex_str = f" (e.g. {', '.join(unknown_examples)} — referenced in BD, no source file)" if unknown_examples else ""

    coverage_note = (
        f"{unknown_count} BD steps reference targets with no shipped source file in the repository"
        f"{ex_str} (below route altitude — intra-program paragraphs — or external boundary components)."
    )

    recommendation = (
        "Align BD documentation to document missing code routes, resolve stale-missing contradictions, "
        "and verify external boundary interfaces."
    )

    return {
        "overall_verdict": verdict_str,
        "headline": headline,
        "key_risks": key_risks,
        "coverage_note": coverage_note,
        "recommendation": recommendation,
    }


def _parse_summary_json(content: str) -> dict[str, Any]:
    """Extract JSON object from LLM response text."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return json.loads(text)


async def generate_executive_summary(
    db: Any, cluster_id: str, snapshot_id: str, provider_id: str | None = None
) -> dict[str, Any]:
    """Generate executive summary using LLM if provider_id given, or fallback to deterministic template."""
    findings = await get_flow_integrity_findings(db, cluster_id, snapshot_id)
    flow_map = await get_e2e_flow_map(db, cluster_id, snapshot_id)

    fallback = _generate_fallback_summary(findings, flow_map)

    if not provider_id:
        return fallback

    cal = findings.get("calibration", {})
    stale_missing = [
        f for f in findings.get("broken_unknown_findings", []) if f.get("ai_bucket") == "stale_missing"
    ]
    code_only = findings.get("code_only_findings", [])
    matched_nodes = [
        n for n in flow_map.get("nodes", []) if n.get("data", {}).get("tag") == "DOC_MATCHED"
    ]

    prompt_payload = {
        "calibration": cal,
        "stale_missing_contradictions": [
            {
                "bd_span": _clean_br(f.get("bd_span")),
                "code_fact": f.get("code_fact"),
                "reason": f.get("reason"),
            }
            for f in stale_missing
        ],
        "undocumented_code_omissions": [
            {"rel_path": f.get("rel_path"), "node_kind": f.get("node_kind"), "reason": f.get("reason")}
            for f in code_only
        ],
        "sample_matched_steps": [
            {"label": _clean_br(n.get("data", {}).get("label")), "rel_path": n.get("data", {}).get("rel_path")}
            for n in matched_nodes[:5]
        ],
        "unknown_references_count": findings.get("collapsed_unknown_count", 0),
        "unknown_examples": [
            {
                "bd_reference": f.get("bd_reference"),
                "bd_span": _clean_br(f.get("bd_span")),
                "reason": f.get("reason"),
            }
            for f in findings.get("unknown_findings", [])[:8]
        ],
    }

    req = ChatRequest(
        provider_id=provider_id,
        messages=[
            ChatMessage(role="system", content=_SUMMARY_SYSTEM_PROMPT),
            ChatMessage(
                role="user",
                content=f"Please analyze these Business Flow Integrity audit findings and generate an executive summary JSON:\n{json.dumps(prompt_payload, indent=2)}",
            ),
        ],
        stream=True,
        max_completion_tokens=2048,
        temperature=0.1,
        json_mode=True,
        reasoning_effort="low",
    )

    full_text = ""
    try:
        async for evt in ProviderConfigService().chat_stream_events(req):
            if evt.get("type") == "content":
                full_text += evt.get("text") or ""
    except Exception as e:
        logger.warning(f"BFI Executive Summary LLM stream error: {e}")
        return fallback

    if not full_text.strip():
        logger.warning("BFI Executive Summary LLM returned empty content; using fallback")
        return fallback

    try:
        parsed = _parse_summary_json(full_text)
        validated = ExecutiveSummaryResponse.model_validate(parsed)
        return validated.model_dump()
    except (ValueError, ValidationError) as ve:
        logger.warning(f"BFI Executive Summary LLM parse/validation error: {ve}; using fallback")
        return fallback
