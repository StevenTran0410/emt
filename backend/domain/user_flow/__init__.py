"""Phase U: User-Flow Alignment domain package (TICKET UR Lean Re-Architecture).

Exports import_user_flow_xlsx and supporting extractors / structurers / aligners.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.logger import logger
from shared.utils import new_id, utc_now_iso

from ._align import (
    FusedAlignResponse,
    FusedBDMapping,
    FusedCitation,
    FusedClaim,
    FusedStepResult,
    StepAlignAudit,
    StepAlignContext,
    align_user_flow_steps,
    evaluate_align_batch,
)
from ._anchor import (
    AnchorCitation,
    AnchorStepResult,
    SeedHit,
    anchor_user_steps,
    cut_line_window,
    resolve_step_files,
    retrieve_step_snippets,
    seed_literal_hits,
)
from ._detail import (
    CaseItem,
    CellRef,
    DetailCaseItem,
    DetailFlowResult,
    DetailResult,
    DetailStepItem,
    PatchOp,
    StepItem,
    UnitProcessingArtifact,
    compute_uncovered_rows,
    correct_detail_batch,
    extract_detail_batch,
    materialize_cell_text,
    render_cell_line,
)
from ._extract import (
    Block,
    RawCell,
    RawRow,
    SheetExtract,
    WorkbookExtract,
    extract_workbook,
)
from ._mapping import (
    BDContext,
    BDUnit,
    MappingItem,
    StepMappingResult,
    load_bd_context,
    map_user_steps_to_bd,
)
from ._queries import get_user_flow_graph, get_user_flow_report
from ._skeleton import (
    CompletenessArtifact,
    FlowUnit,
    MissingSpanItem,
    OutlineArtifact,
    OutlineFlowItem,
    OutlineSpan,
    build_outline,
    check_step_completeness,
)
from ._structure import (
    BIG_FLOW_ROWS,
    BIG_FLOW_STEPS,
    SheetClassItem,
    StructureResult,
    canonicalize_flow_name,
    classify_sheets,
    structure_user_flow,
)
from ._activity_group import (
    ACTIVITY_PROMPT_VERSION,
    ACTIVITY_SCHEMA_VERSION,
    condense_flow_activities,
)
from ._activity_match import (
    TIER1_PROMPT_VERSION,
    TIER1_SCHEMA_VERSION,
    match_user_activities_to_bd_flows,
)
from ._verdict import (
    LLMCitation as VerdictLLMCitation,
    LLMVerdictBatchResponse,
    LLMVerdictItem,
    StepVerdictContext,
    VerdictRunSummary,
    evaluate_verdict_batch,
    run_user_flow_verdicts,
)


@dataclass
class UserFlowRunResult:
    doc_id: str
    run_id: str
    total_steps: int
    in_scope_steps: int
    anchored_steps: int
    llm_batches_issued: int          # real ALIGN logical batch count
    mapped_steps: int = 0
    physical_llm_attempts: int = 0    # align physical provider attempts (incl. ladder retries)
    import_llm_calls: int = 0         # real import-stage logical calls (from the __import_summary__ artifact)
    total_llm_calls: int = 0          # import + align + tier1 + verdict logical calls
    activities_total: int = 0
    matched_fully: int = 0
    matched_partial: int = 0
    matched_none: int = 0
    tier2_steps_run: int = 0
    tier2_steps_skipped: int = 0
    status: str = "ok"


__all__ = [
    "AnchorCitation",
    "AnchorStepResult",
    "BDContext",
    "BDUnit",
    "BIG_FLOW_ROWS",
    "BIG_FLOW_STEPS",
    "Block",
    "CaseItem",
    "CellRef",
    "CompletenessArtifact",
    "DetailCaseItem",
    "DetailFlowResult",
    "DetailResult",
    "DetailStepItem",
    "FlowUnit",
    "FusedAlignResponse",
    "FusedBDMapping",
    "FusedCitation",
    "FusedClaim",
    "FusedStepResult",
    "MappingItem",
    "MissingSpanItem",
    "OutlineArtifact",
    "OutlineFlowItem",
    "OutlineSpan",
    "PatchOp",
    "RawCell",
    "RawRow",
    "SeedHit",
    "SheetClassItem",
    "SheetExtract",
    "StepAlignAudit",
    "StepAlignContext",
    "StepItem",
    "StepMappingResult",
    "StructureResult",
    "UnitProcessingArtifact",
    "UserFlowRunResult",
    "VerdictLLMCitation",
    "VerdictRunSummary",
    "WorkbookExtract",
    "align_user_flow_steps",
    "anchor_user_steps",
    "build_outline",
    "canonicalize_flow_name",
    "check_step_completeness",
    "classify_sheets",
    "compute_uncovered_rows",
    "correct_detail_batch",
    "cut_line_window",
    "evaluate_align_batch",
    "evaluate_verdict_batch",
    "extract_detail_batch",
    "extract_workbook",
    "get_user_flow_report",
    "import_user_flow_xlsx",
    "load_bd_context",
    "map_user_steps_to_bd",
    "materialize_cell_text",
    "render_cell_line",
    "resolve_step_files",
    "retrieve_step_snippets",
    "run_user_flow_alignment",
    "run_user_flow_verdicts",
    "seed_literal_hits",
    "structure_user_flow",
]


async def import_user_flow_xlsx(
    db: Any,
    path: str | list[str],
    provider_id: str | None = None,
) -> str:
    """Import and structure a customer Excel test/flow scenario workbook or list of workbooks.

    Steps:
    1. Extract raw cells, rows, and blocks via openpyxl (Stage 0).
       If multiple files are provided, sheets are namespaced as `{file_stem}:{sheet_name}`.
    2. Check idempotency: if file_hash exists in user_flow_docs, delete existing records and re-import.
    3. Classify sheets into semantic categories (LLM#1a).
    4. Structure relevant sheets using the lean 4-stage pipeline (Outline -> Detail -> Corrector -> Completeness).
    5. Save flows, steps, cases, and run artifacts to the database.
    """
    raw_paths = [path] if isinstance(path, (str, Path)) else list(path)
    if not raw_paths:
        raise ValueError("At least one workbook path must be provided")

    file_ps = [Path(p) for p in raw_paths]
    for fp in file_ps:
        if not fp.exists():
            raise FileNotFoundError(f"User flow workbook not found: {fp}")

    # Stage 0: Deterministic Extraction
    if len(file_ps) == 1:
        wb_extract = extract_workbook(str(file_ps[0]))
        file_hash = wb_extract.file_hash
        source_name = file_ps[0].name
    else:
        individual_hashes: list[str] = []
        combined_sheets: list[SheetExtract] = []
        for fp in file_ps:
            wb_single = extract_workbook(str(fp))
            individual_hashes.append(wb_single.file_hash)
            tag = fp.stem
            for s in wb_single.sheets:
                namespaced_sheet = SheetExtract(
                    sheet=f"{tag}:{s.sheet}",
                    hidden=s.hidden,
                    n_rows=s.n_rows,
                    n_cols=s.n_cols,
                    blocks=s.blocks,
                )
                combined_sheets.append(namespaced_sheet)
        file_hash = hashlib.sha256(":".join(individual_hashes).encode("utf-8")).hexdigest()
        wb_extract = WorkbookExtract(file_hash=file_hash, sheets=combined_sheets)
        source_name = f"{len(file_ps)} workbooks ({', '.join(fp.name for fp in file_ps)})"

    # Idempotency check: Delete existing doc with same hash
    async with db.execute("SELECT id FROM user_flow_docs WHERE file_hash = ?", (file_hash,)) as cur:
        existing = await cur.fetchone()

    if existing:
        existing_id = existing[0] if isinstance(existing, (tuple, list)) else existing["id"]
        logger.info("Re-importing user flow doc %s (hash %s)", existing_id, file_hash)
        await db.execute("DELETE FROM user_steps WHERE flow_id IN (SELECT id FROM user_flows WHERE doc_id = ?)", (existing_id,))
        await db.execute("DELETE FROM user_flows WHERE doc_id = ?", (existing_id,))
        await db.execute("DELETE FROM user_cases WHERE doc_id = ?", (existing_id,))
        await db.execute("DELETE FROM user_run_artifacts WHERE doc_id = ?", (existing_id,))
        await db.execute("DELETE FROM user_flow_docs WHERE id = ?", (existing_id,))
        await db.commit()

    doc_id = f"ufdoc:{new_id()}"
    now = utc_now_iso()

    # Insert user_flow_docs row
    await db.execute(
        "INSERT INTO user_flow_docs (id, source_name, file_hash, imported_at) VALUES (?, ?, ?, ?)",
        (doc_id, source_name, file_hash, now),
    )

    # Classify sheets
    classifier_art = None
    if provider_id is not None:
        sheet_classes, classifier_art = await classify_sheets(wb_extract, provider_id)
        # Store classifier artifact
        await db.execute(
            "INSERT INTO user_run_artifacts (id, run_id, doc_id, ref_id, payload, created_at) "
            "VALUES (?, ?, ?, 'classifier', ?, ?)",
            (
                f"ura:{new_id()}",
                doc_id,
                doc_id,
                json.dumps({
                    "success": classifier_art.success,
                    "error": classifier_art.error,
                    "retry_count": classifier_art.retry_count,
                    "latency_ms": classifier_art.latency_ms,
                    "classifications": classifier_art.classifications,
                    "agent_raw": classifier_art.agent_raw,
                }, ensure_ascii=False),
                now,
            ),
        )
    else:
        # Heuristic fallback for offline/test mode
        sheet_classes = {}
        for s in wb_extract.sheets:
            name = s.sheet.lower()
            if "想定" in name or "flow" in name or "シナリオ" in name:
                sheet_classes[s.sheet] = "narrative_flow"
            elif "項目" in name or "case" in name or "test" in name:
                sheet_classes[s.sheet] = "case_table"
            elif "メッセージ" in name or "msg" in name:
                sheet_classes[s.sheet] = "message_list"
            elif "一覧" in name or "file" in name:
                sheet_classes[s.sheet] = "inventory"
            elif "遷移" in name or "画面" in name:
                sheet_classes[s.sheet] = "raw_source"
            else:
                sheet_classes[s.sheet] = "admin_or_empty"

    # Filter relevant sheets (narrative_flow, case_table, message_list)
    relevant_blocks_by_sheet: list[tuple[SheetExtract, str]] = []
    for s in wb_extract.sheets:
        cls = sheet_classes.get(s.sheet, "admin_or_empty")
        if cls in ("narrative_flow", "case_table", "message_list"):
            relevant_blocks_by_sheet.append((s, cls))

    # Run Lean Hierarchical Structuring
    struct_res = await structure_user_flow(
        relevant_blocks_by_sheet, provider_id, doc_id=doc_id
    )

    # Persist outline artifact
    if struct_res.outline_artifact:
        await db.execute(
            "INSERT INTO user_run_artifacts (id, run_id, doc_id, ref_id, payload, created_at) "
            "VALUES (?, ?, ?, 'outline', ?, ?)",
            (
                f"ura:{new_id()}",
                doc_id,
                doc_id,
                json.dumps({
                    "success": struct_res.outline_artifact.success,
                    "error": struct_res.outline_artifact.error,
                    "total_flows": struct_res.outline_artifact.total_flows,
                    "repaired_items": struct_res.outline_artifact.repaired_items,
                    "warnings": struct_res.outline_artifact.warnings,
                    "retry_count": struct_res.outline_artifact.retry_count,
                    "latency_ms": struct_res.outline_artifact.latency_ms,
                    "agent_raw": struct_res.outline_artifact.agent_raw,
                }, ensure_ascii=False),
                now,
            ),
        )

    # Persist unit detail/corrector artifacts
    for u_art in struct_res.unit_artifacts:
        await db.execute(
            "INSERT INTO user_run_artifacts (id, run_id, doc_id, ref_id, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"ura:{new_id()}",
                doc_id,
                doc_id,
                f"flow:{u_art.flow_unit_id}",
                json.dumps({
                    "flow_unit_id": u_art.flow_unit_id,
                    "corrected": u_art.corrected,
                    "patches_applied": u_art.patches_applied,
                    "detail_retry_count": u_art.detail_retry_count,
                    "corrector_retry_count": u_art.corrector_retry_count,
                    "detail_latency_ms": u_art.detail_latency_ms,
                    "corrector_latency_ms": u_art.corrector_latency_ms,
                    "warnings": u_art.warnings,
                }, ensure_ascii=False),
                now,
            ),
        )

    # Persist completeness artifact
    if struct_res.completeness_artifact:
        await db.execute(
            "INSERT INTO user_run_artifacts (id, run_id, doc_id, ref_id, payload, created_at) "
            "VALUES (?, ?, ?, 'completeness', ?, ?)",
            (
                f"ura:{new_id()}",
                doc_id,
                doc_id,
                json.dumps({
                    "produced_step_count": struct_res.completeness_artifact.produced_step_count,
                    "total_semantic_rows": struct_res.completeness_artifact.total_semantic_rows,
                    "uncovered_rows_count": struct_res.completeness_artifact.uncovered_rows_count,
                    "missing_spans_count": struct_res.completeness_artifact.missing_spans_count,
                    "recovered_count": struct_res.completeness_artifact.recovered_count,
                    "missing_spans": struct_res.completeness_artifact.missing_spans,
                }, ensure_ascii=False),
                now,
            ),
        )

    # Persist flows
    for f in struct_res.flows:
        await db.execute(
            "INSERT INTO user_flows (id, doc_id, ordinal, name_ja, name_en, kind, sheet, scope_note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f["id"],
                f["doc_id"],
                f["ordinal"],
                f["name_ja"],
                f["name_en"],
                f["kind"],
                f["sheet"],
                f["scope_note"],
                f["created_at"],
            ),
        )

    # Persist steps
    for s in struct_res.steps:
        await db.execute(
            "INSERT INTO user_steps (id, flow_id, ordinal, kind, section_id, text_ja, text_en, trigger_ja, expected_ja, screen_name_ja, in_scope, scope_note, sheet, row_start, row_end, provenance_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                s["id"],
                s["flow_id"],
                s["ordinal"],
                s["kind"],
                s["section_id"],
                s["text_ja"],
                s["text_en"],
                s["trigger_ja"],
                s["expected_ja"],
                s["screen_name_ja"],
                s["in_scope"],
                s["scope_note"],
                s["sheet"],
                s["row_start"],
                s["row_end"],
                s["provenance_json"],
                s["created_at"],
            ),
        )

    # Persist cases
    for c in struct_res.cases:
        await db.execute(
            "INSERT INTO user_cases (id, doc_id, ordinal, screen_name_ja, area_ja, viewpoint_ja, conditions_json, trigger_ja, check_item_ja, expected_ja, link_section_id, sheet, row_ix, raw_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                c["id"],
                c["doc_id"],
                c["ordinal"],
                c["screen_name_ja"],
                c["area_ja"],
                c["viewpoint_ja"],
                c["conditions_json"],
                c["trigger_ja"],
                c["check_item_ja"],
                c["expected_ja"],
                c["link_section_id"],
                c["sheet"],
                c["row_ix"],
                c["raw_json"],
                c["created_at"],
            ),
        )

    # Phase U2 (LLM#5): Activity Condensation stage
    await db.execute(
        "DELETE FROM user_activities WHERE flow_id IN (SELECT id FROM user_flows WHERE doc_id = ?)",
        (doc_id,),
    )

    steps_by_flow: dict[str, list[dict[str, Any]]] = {}
    for s in struct_res.steps:
        steps_by_flow.setdefault(s["flow_id"], []).append(s)

    all_activities: list[dict[str, Any]] = []
    condensation_artifacts: list[dict[str, Any]] = []

    for f in struct_res.flows:
        f_steps = steps_by_flow.get(f["id"], [])
        f_acts, f_art = await condense_flow_activities(db, f, f_steps, provider_id)
        all_activities.extend(f_acts)
        condensation_artifacts.append(f_art)

    for act in all_activities:
        await db.execute(
            "INSERT INTO user_activities (id, flow_id, ordinal, name_en, name_ja, summary_en, member_step_ids_json, sheet_span_json, step_count, action_count, error_rule_count, expectation_count, row_start, row_end, origin, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                act["id"],
                act["flow_id"],
                act["ordinal"],
                act["name_en"],
                act.get("name_ja"),
                act.get("summary_en"),
                act["member_step_ids_json"],
                act["sheet_span_json"],
                act.get("step_count", 0),
                act.get("action_count", 0),
                act.get("error_rule_count", 0),
                act.get("expectation_count", 0),
                act.get("row_start"),
                act.get("row_end"),
                act.get("origin", "llm"),
                act["created_at"],
            ),
        )

    # Record the real import-stage LLM cost so run_user_flow_alignment can report the full pipeline total.
    condensation_logical = sum(1 for art in condensation_artifacts if art.get("origin") == "llm")
    condensation_physical = sum(
        art.get("ladder_meta", {}).get("total_attempts", 1)
        for art in condensation_artifacts
        if art.get("origin") == "llm"
    )
    import_logical = 1 + struct_res.logical_calls + condensation_logical  # classifier + structurer + condensation
    import_physical = (1 + (classifier_art.retry_count if classifier_art else 0)) + struct_res.physical_attempts + condensation_physical
    await db.execute(
        "INSERT INTO user_run_artifacts (id, run_id, doc_id, ref_id, payload, created_at) "
        "VALUES (?, ?, ?, '__import_summary__', ?, ?)",
        (
            f"ura:{new_id()}",
            doc_id,
            doc_id,
            json.dumps({"import_logical_calls": import_logical, "import_physical_attempts": import_physical}),
            utc_now_iso(),
        ),
    )

    await db.commit()
    return doc_id


async def run_user_flow_alignment(
    db: Any,
    doc_id: str,
    cluster_id: str,
    snapshot_id: str,
    provider_id: str | None = None,
) -> UserFlowRunResult:
    """Execute Phase U2 Two-Tier User Flow Alignment pipeline (Tier-1 Activity Matcher + Rescoped Tier-2 Align)."""
    run_id = f"ufrun:{new_id()}"

    # 1. Fetch snapshot root directory
    async with db.execute("SELECT local_path FROM repo_snapshots WHERE id = ?", (snapshot_id,)) as cur:
        snap_row = await cur.fetchone()

    local_path = snap_row[0] if isinstance(snap_row, (tuple, list)) else (snap_row["local_path"] if snap_row else "")

    # 2. Clear prior code anchors & BD mappings for this doc_id
    await db.execute(
        "DELETE FROM user_code_anchors WHERE snapshot_id = ? AND step_id IN ("
        "  SELECT s.id FROM user_steps s JOIN user_flows f ON s.flow_id = f.id WHERE f.doc_id = ?"
        ")",
        (snapshot_id, doc_id),
    )
    await db.execute(
        "DELETE FROM user_bd_mappings WHERE user_step_id IN ("
        "  SELECT s.id FROM user_steps s JOIN user_flows f ON s.flow_id = f.id WHERE f.doc_id = ?"
        ")",
        (doc_id,),
    )

    # 3. Load steps and activities for this doc_id
    async with db.execute(
        "SELECT s.id, s.flow_id, s.ordinal, s.kind, s.section_id, s.text_ja, s.text_en, "
        "       s.trigger_ja, s.expected_ja, s.screen_name_ja, s.in_scope, s.scope_note, "
        "       s.sheet, s.row_start, s.row_end, f.name_ja AS flow_name_ja "
        "FROM user_steps s JOIN user_flows f ON s.flow_id = f.id "
        "WHERE f.doc_id = ? ORDER BY s.ordinal",
        (doc_id,),
    ) as cur:
        step_rows = await cur.fetchall()

    steps = [
        dict(r) if hasattr(r, "keys") else {
            "id": r[0],
            "flow_id": r[1],
            "ordinal": r[2],
            "kind": r[3],
            "section_id": r[4],
            "text_ja": r[5],
            "text_en": r[6],
            "trigger_ja": r[7],
            "expected_ja": r[8],
            "screen_name_ja": r[9],
            "in_scope": r[10],
            "scope_note": r[11],
            "sheet": r[12],
            "row_start": r[13],
            "row_end": r[14],
            "flow_name_ja": r[15],
        }
        for r in step_rows
    ]

    total_steps = len(steps)
    in_scope_steps = [s for s in steps if s["in_scope"] == 1]

    async with db.execute(
        "SELECT id, flow_id, ordinal, name_en, name_ja, summary_en, member_step_ids_json, sheet_span_json, "
        "       step_count, action_count, error_rule_count, expectation_count, row_start, row_end, origin, created_at "
        "FROM user_activities WHERE flow_id IN (SELECT id FROM user_flows WHERE doc_id = ?) ORDER BY ordinal",
        (doc_id,),
    ) as cur:
        act_rows = await cur.fetchall()

    activities = [
        dict(r) if hasattr(r, "keys") else {
            "id": r[0],
            "flow_id": r[1],
            "ordinal": r[2],
            "name_en": r[3],
            "name_ja": r[4],
            "summary_en": r[5],
            "member_step_ids_json": r[6],
            "sheet_span_json": r[7],
            "step_count": r[8],
            "action_count": r[9],
            "error_rule_count": r[10],
            "expectation_count": r[11],
            "row_start": r[12],
            "row_end": r[13],
            "origin": r[14],
            "created_at": r[15],
        }
        for r in act_rows
    ]

    # 4. Phase U2 (LLM#6): Tier-1 Activity Matching stage
    tier1_matches, tier1_artifact = await match_user_activities_to_bd_flows(
        db=db,
        doc_id=doc_id,
        cluster_id=cluster_id,
        activities=activities,
        steps=steps,
        provider_id=provider_id,
        run_id=run_id,
    )

    # Build step-level activity & matched BD flow mapping
    step_activity_map: dict[str, dict[str, Any]] = {}
    for act in activities:
        act_matches = [m for m in tier1_matches if m["activity_id"] == act["id"]]
        match_status = act_matches[0]["match_status"] if act_matches else "NONE"
        matched_bd_ids = [m["bd_flow_id"] for m in act_matches if m.get("bd_flow_id")]
        try:
            m_ids = json.loads(act.get("member_step_ids_json") or "[]")
        except Exception:
            m_ids = []
        for sid in m_ids:
            step_activity_map[sid] = {
                "activity_id": act["id"],
                "match_status": match_status,
                "bd_flow_ids": matched_bd_ids,
            }

    # Partition in-scope steps: FULLY/PARTIAL -> run tier-2 align; NONE -> skip tier-2
    if activities:
        tier2_eval_steps = [
            s for s in in_scope_steps
            if step_activity_map.get(s["id"], {}).get("match_status") in ("FULLY", "PARTIAL")
        ]
        tier2_skipped_steps = [
            s for s in in_scope_steps
            if step_activity_map.get(s["id"], {}).get("match_status") == "NONE" or s["id"] not in step_activity_map
        ]
    else:
        tier2_eval_steps = in_scope_steps
        tier2_skipped_steps = []

    skipped_step_ids = {s["id"] for s in tier2_skipped_steps}

    # Phase U2 tier-2 rescope: step_id -> matched BD flow ids from tier-1. Opt-in only (None when
    # there are no activities at all) so the U-1 style call path stays byte-identical to today.
    step_bd_scope: dict[str, list[str]] | None = (
        {sid: entry.get("bd_flow_ids") or [] for sid, entry in step_activity_map.items()}
        if activities else None
    )

    # 5. Fused Anchor + BD Mapper stage (TICKET UR, Rescoped in U2)
    anchored_count = 0
    mapped_count = 0
    run_summary: dict[str, Any] = {}

    if tier2_eval_steps:
        anchored_count, mapped_count, run_summary = await align_user_flow_steps(
            db=db,
            doc_id=doc_id,
            cluster_id=cluster_id,
            snapshot_id=snapshot_id,
            run_id=run_id,
            user_steps=tier2_eval_steps,
            provider_id=provider_id,
            local_path=Path(local_path) if local_path else Path("."),
            step_bd_scope=step_bd_scope,
        )

    # 6. Stage U4: Verdict verification + Activity & Flow Rollups
    verdict_summary = await run_user_flow_verdicts(
        db=db,
        doc_id=doc_id,
        cluster_id=cluster_id,
        snapshot_id=snapshot_id,
        run_id=run_id,
        provider_id=provider_id,
        skipped_step_ids=skipped_step_ids,
        activities=activities,
        step_bd_scope=step_bd_scope,
    )

    await db.commit()

    # Real call metrics
    align_logical = align_physical = import_logical = verdict_logical = verdict_physical = 0
    tier1_logical = tier1_physical = 0

    if provider_id:
        align_logical = int(run_summary.get("align_logical_batches", 0)) if isinstance(run_summary, dict) else 0
        align_physical = int(run_summary.get("align_physical_attempts", 0)) if isinstance(run_summary, dict) else 0
        verdict_logical = verdict_summary.llm_batches_issued
        verdict_physical = verdict_summary.physical_attempts

        tier1_logical = len(tier1_artifact.get("batch_artifacts", []))
        tier1_physical = sum(
            ba.get("ladder_meta", {}).get("total_attempts", 1)
            for ba in tier1_artifact.get("batch_artifacts", [])
            if ba.get("origin") == "llm"
        )

        async with db.execute(
            "SELECT payload FROM user_run_artifacts WHERE doc_id=? AND ref_id='__import_summary__' ORDER BY created_at DESC LIMIT 1",
            (doc_id,),
        ) as cur:
            imp_row = await cur.fetchone()
        if imp_row:
            try:
                import_logical = int(json.loads(imp_row[0]).get("import_logical_calls", 0))
            except Exception:
                import_logical = 0

    total_logical = import_logical + tier1_logical + align_logical + verdict_logical
    total_physical = tier1_physical + align_physical + verdict_physical

    matched_fully_cnt = sum(
        1 for a in activities
        if any(m["activity_id"] == a["id"] and m["match_status"] == "FULLY" for m in tier1_matches)
    )
    matched_partial_cnt = sum(
        1 for a in activities
        if any(m["activity_id"] == a["id"] and m["match_status"] == "PARTIAL" for m in tier1_matches)
    )
    matched_none_cnt = sum(
        1 for a in activities
        if any(m["activity_id"] == a["id"] and m["match_status"] == "NONE" for m in tier1_matches)
    )

    return UserFlowRunResult(
        doc_id=doc_id,
        run_id=run_id,
        total_steps=total_steps,
        in_scope_steps=len(in_scope_steps),
        anchored_steps=anchored_count,
        mapped_steps=mapped_count,
        llm_batches_issued=tier1_logical + align_logical + verdict_logical,
        physical_llm_attempts=total_physical,
        import_llm_calls=import_logical,
        total_llm_calls=total_logical,
        activities_total=len(activities),
        matched_fully=matched_fully_cnt,
        matched_partial=matched_partial_cnt,
        matched_none=matched_none_cnt,
        tier2_steps_run=len(tier2_eval_steps),
        tier2_steps_skipped=len(tier2_skipped_steps),
        status="ok",
    )
