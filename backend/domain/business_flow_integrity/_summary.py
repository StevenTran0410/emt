"""Executive Summary generation for Phase 3 Business Flow Integrity (TICKET P3-9, P3-10)."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

from domain.model_connector.types import ChatMessage, ChatRequest
from domain.business_flow_integrity._llm import call_with_reasoning_ladder
from domain.business_flow_integrity._queries import get_e2e_flow_map, get_flow_integrity_findings
from shared.utils import new_id, utc_now_iso

logger = logging.getLogger("codespectra.bfi.summary")


async def _persist_executive_summary(
    db: Any, cluster_id: str, snapshot_id: str, summary: dict[str, Any], origin: str
) -> None:
    """Store the executive summary so an LLM-blocked machine can read it back (delete+insert one row)."""
    await db.execute(
        "DELETE FROM business_flow_llm_output WHERE cluster_id=? AND snapshot_id=? AND kind='summary'",
        (cluster_id, snapshot_id),
    )
    await db.execute(
        "INSERT INTO business_flow_llm_output (id, cluster_id, snapshot_id, kind, ref_id, content, origin, created_at) VALUES (?, ?, ?, 'summary', '*', ?, ?, ?)",
        (new_id(), cluster_id, snapshot_id, json.dumps(summary), origin, utc_now_iso()),
    )
    await db.commit()


async def load_executive_summary(db: Any, cluster_id: str, snapshot_id: str) -> dict[str, Any] | None:
    """Read the persisted executive summary, or None if none stored."""
    async with db.execute(
        "SELECT content FROM business_flow_llm_output WHERE cluster_id=? AND snapshot_id=? AND kind='summary' LIMIT 1",
        (cluster_id, snapshot_id),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    try:
        return json.loads(row["content"])
    except Exception:
        return None


class ExecutiveSummaryResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    overall_verdict: Literal["PASS", "FAIL"] = Field(description="PASS or FAIL overall verdict for business flow integrity")
    headline: str = Field(description="One sentence high-level executive summary of flow integrity status")
    key_risks: list[str] = Field(description="Bullet points detailing critical flow risks and contradictions")
    coverage_note: str = Field(description="Summary of unindexed or unverified BD steps and external boundaries")
    recommendation: str = Field(description="Actionable next steps for documentation alignment")


def _deterministic_overall_verdict(findings: dict[str, Any]) -> Literal["PASS", "FAIL"]:
    """FIX 6: clamp overall_verdict to the SAME calibration numbers the fallback summary derives
    from — the LLM (or fallback template) may only write the explanation text, never the verdict
    itself, so a PASS headline can no longer coexist with a FAIL-worthy calibration."""
    biz = findings.get("business")
    cal = biz.get("calibration", {}) if biz else findings.get("calibration", {})
    calibration_pass = bool(cal.get("calibration_pass", False))
    broken_count = cal.get("broken_count", 0) or 0
    return "PASS" if (calibration_pass and broken_count == 0) else "FAIL"


_SUMMARY_SYSTEM_PROMPT = """You are a senior enterprise software architect evaluating a Business Flow Integrity audit report for a legacy system migration.
You will receive a JSON summary of the alignment findings between Basic Design (BD) flow documentation and extracted source code routes.

Rules:
1. Frame this report as an executive evaluation of end-to-end BD-to-source traceability.
2. Focus ONLY on flow integrity (step ordering, route abstraction faithfulness, contradictions, missing code routes, coverage caveats).
3. Do NOT evaluate numeric value correctness or database schemas.
4. Cite specific BD references and source files as concrete examples for every contradiction, omission, and unknown you mention — name the actual business flow, step, and program file (e.g. "Step 'Route to coil entry' in flow 'Coil Lot Entry' is backed by HSBMENU5.pfd -> PHNIXLOT.clist -> HNIXLOT.cbl").
5. Respond ONLY in English.
6. Output MUST be a SINGLE JSON object with EXACTLY these top-level keys — no wrapper object, no renamed keys, no extra keys:
{
  "overall_verdict": "PASS" or "FAIL",
  "headline": "one-sentence executive summary",
  "key_risks": ["short risk bullet", "short risk bullet"],
  "coverage_note": "one paragraph on untraced/unverified BD steps and external boundaries",
  "recommendation": "actionable next steps"
}
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
    biz = findings.get("business")
    if biz:
        cal = biz.get("calibration", {})
        match_cnt = cal.get("match_count", 0)
        total_units = cal.get("total_units", 0)
        # Coverage % is Backed/Total (SPEC: "the number is the number") — NOT the resolved-only
        # calibration_pass gate percentage, which excludes untraced (UNKNOWN) units from the
        # denominator and would overstate coverage to a non-technical reader.
        match_pct = (match_cnt / total_units * 100.0) if total_units > 0 else 0.0
        calibration_pass = cal.get("calibration_pass", False)
        per_flow = biz.get("per_flow", [])
        matched = biz.get("matched_units", [])
        contradicted = biz.get("contradicted_units", [])
        unknown = biz.get("unknown_units", [])

        verdict_str = "PASS" if calibration_pass else "FAIL"
        headline = (
            f"Business Flow Integrity MVP coverage is {match_pct:.1f}% ({match_cnt}/{total_units} units backed across {len(per_flow)} flows). "
            f"Demonstrates end-to-end BD-to-source traceability."
        )

        key_risks: list[str] = []
        for c in contradicted[:5]:
            f_name = c.get("flow_name", "Flow")
            u_name = c.get("unit_name", "Step")
            reason = c.get("reason", "Contradiction in source code.")
            key_risks.append(f"Contradiction in flow '{f_name}', step '{u_name}': {reason}")

        if not key_risks:
            key_risks.append("No hard contradictions detected among traced business steps.")

        backed_examples: list[str] = []
        for m in matched[:2]:
            bindings = (m.get("segment") or {}).get("bindings") or []
            b_str = " -> ".join(bindings) if bindings else "source code"
            backed_examples.append(f"Step '{m.get('unit_name')}' in flow '{m.get('flow_name')}' is backed by {b_str}")

        ex_str = f" (e.g. {'; '.join(backed_examples)})" if backed_examples else ""
        coverage_note = (
            f"{len(matched)} units are backed by source code{ex_str}. "
            f"Additionally, {len(unknown)} units are not yet traced under current MVP route-altitude rules."
        )

        recommendation = (
            "Align BD documentation with source code implementation, resolve documented contradictions, "
            "and extend route-altitude parsers."
        )

        return {
            "overall_verdict": verdict_str,
            "headline": headline,
            "key_risks": key_risks,
            "coverage_note": coverage_note,
            "recommendation": recommendation,
        }

    # Legacy fallback path
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
    # FIX 6: the deterministic clamp applies whether or not an LLM ever runs — the offline
    # fallback's own naive calibration_pass-only verdict has the same contradiction-blind flaw.
    fallback["overall_verdict"] = _deterministic_overall_verdict(findings)

    if not provider_id:
        return fallback

    biz = findings.get("business")
    if biz:
        cal = biz.get("calibration", {})
        total_units = cal.get("total_units", 0)
        match_cnt = cal.get("match_count", 0)
        # Coverage % = Backed/Total (see fallback path above) — not the resolved-only calibration gate.
        coverage_pct = (match_cnt / total_units * 100.0) if total_units > 0 else 0.0
        prompt_payload = {
            "coverage_percentage": round(coverage_pct, 1),
            "total_units": total_units,
            "matched_units": match_cnt,
            "per_flow_rollups": [
                {
                    "flow_name": f.get("flow_name"),
                    "steps_matched": f.get("steps_matched"),
                    "steps_total": f.get("steps_total"),
                    "status": f.get("status"),
                    "section_name": f.get("section_name"),
                }
                for f in biz.get("per_flow", [])
            ],
            "sample_backed_examples": [
                {
                    "flow_name": m.get("flow_name"),
                    "unit_name": m.get("unit_name"),
                    "bindings": m.get("segment", {}).get("bindings", []) if m.get("segment") else [],
                    "rel_paths": m.get("segment", {}).get("rel_paths", []) if m.get("segment") else [],
                    "reason": m.get("reason"),
                }
                for m in biz.get("matched_units", [])[:5]
            ],
            "sample_contradictions": [
                {
                    "flow_name": c.get("flow_name"),
                    "unit_name": c.get("unit_name"),
                    "reason": c.get("reason"),
                    "file": ((c.get("segment") or {}).get("rel_paths") or [None])[0],
                }
                for c in biz.get("contradicted_units", [])[:5]
            ],
            "untraced_units_count": len(biz.get("unknown_units", [])),
        }
    else:
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

    def _build_req(effort: str) -> ChatRequest:
        return ChatRequest(
            provider_id=provider_id,
            messages=[
                ChatMessage(role="system", content=_SUMMARY_SYSTEM_PROMPT),
                ChatMessage(
                    role="user",
                    content=f"Please analyze these Business Flow Integrity audit findings and generate an executive summary JSON:\n{json.dumps(prompt_payload, indent=2)}",
                ),
            ],
            stream=True,
            max_completion_tokens=50000,
            temperature=0.0,
            json_mode=True,
            reasoning_effort=effort,
        )

    def _parse_summary(text: str) -> dict[str, Any]:
        parsed = _parse_summary_json(text)
        validated = ExecutiveSummaryResponse.model_validate(parsed)
        return validated.model_dump()

    # Prose task (no judgment) — open at low reasoning; one same-tier retry on empty/invalid output.
    ladder_res = await call_with_reasoning_ladder(
        _build_req, _parse_summary, first_effort="low", label="BFI Executive Summary"
    )
    result, origin = (ladder_res[0], "llm") if ladder_res is not None else (fallback, "fallback")

    # FIX 6: the LLM (or fallback template) writes explanation only — overall_verdict is always
    # the deterministic calibration outcome, never a free-form string that can contradict it.
    result["overall_verdict"] = _deterministic_overall_verdict(findings)

    # Real generation pass (provider set) — persist so an LLM-blocked machine reads it back.
    await _persist_executive_summary(db, cluster_id, snapshot_id, result, origin)
    return result

