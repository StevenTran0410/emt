"""Occurrence-level evidence index builder for Phase 5 Source-Aware Verifier (Ticket P5-1)."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from shared.utils import utc_now_iso
from ._resolve import resolve_asset


@dataclass(frozen=True)
class EvidenceOccurrence:
    id: str
    snapshot_id: str
    rel_path: str
    language: str
    kind: str  # 'edge' | 'guard' | 'step'
    edge_type: str | None
    src_binding: str | None
    dst_binding: str | None
    dst_rel_path: str | None
    guard_json: str | None
    occurrence_ix: int
    line_start: int
    line_end: int
    parse_status: str  # 'ok' | 'partial'
    source_fact_id: int | None
    attributes: str
    created_at: str


@dataclass(frozen=True)
class EvidenceIndexResult:
    snapshot_id: str
    total_occurrences: int
    occurrences_by_kind: dict[str, int]


def _detect_language(rel_path: str) -> str:
    lower = rel_path.lower()
    if lower.endswith((".cbl", ".cob")):
        return "cobol"
    if lower.endswith((".jcl", ".prc")):
        return "jcl"
    if lower.endswith(".clist"):
        return "clist"
    if lower.endswith(".pfd"):
        return "pfd"
    if lower.endswith(".ipf"):
        return "ipf"
    return "unknown"


def _asset_type_for_edge(edge_type: str | None) -> str | None:
    if edge_type == "executes":
        return "PROGRAM"
    if edge_type == "calls":
        return "PROGRAM"
    if edge_type == "submits":
        return "JCL"
    if edge_type == "menu_option":
        return "CLIST"
    if edge_type == "shows_panel":
        return "PANEL"
    return None


async def build_evidence_index(db: Any, snapshot_id: str) -> EvidenceIndexResult:
    """Build occurrence-level evidence index for snapshot_id, saving into bfi_evidence_occurrences."""
    # 1. Clear existing evidence occurrences for snapshot_id
    await db.execute("DELETE FROM bfi_evidence_occurrences WHERE snapshot_id=?", (snapshot_id,))
    await db.commit()

    # 2. Fetch parse_status map from source_parse_diagnostics
    parse_status_map: dict[str, str] = {}
    async with db.execute(
        "SELECT rel_path, status, error_count FROM source_parse_diagnostics WHERE snapshot_id=?",
        (snapshot_id,),
    ) as cur:
        diag_rows = await cur.fetchall()

    for r in diag_rows:
        p = r["rel_path"]
        status = r["status"]
        err_cnt = r["error_count"]
        if status != "ok" or err_cnt > 0:
            parse_status_map[p] = "partial"
        elif p not in parse_status_map:
            parse_status_map[p] = "ok"

    # 3. Fetch manifest file paths for resolution
    async with db.execute(
        "SELECT rel_path FROM manifest_files WHERE snapshot_id=?",
        (snapshot_id,),
    ) as cur:
        manifest_rows = await cur.fetchall()
    manifest_paths = [r["rel_path"] for r in manifest_rows]

    # 4. Extract occurrence items from source_facts
    raw_occurrences: list[dict[str, Any]] = []

    async with db.execute(
        "SELECT id, rel_path, language, fact_type, semantic_key, occurrence_ix, parent_key, name, value, attributes, line_start, line_end FROM source_facts WHERE snapshot_id=?",
        (snapshot_id,),
    ) as cur:
        sf_rows = await cur.fetchall()

    for r in sf_rows:
        ft = r["fact_type"]
        rpath = r["rel_path"]
        lang = r["language"] or _detect_language(rpath)
        lstart = r["line_start"]
        lend = r["line_end"]
        skey = r["semantic_key"]
        occ_ix = r["occurrence_ix"]
        parent = r["parent_key"] or ""
        name = r["name"]
        val = r["value"]
        attrs_str = r["attributes"] or "{}"
        try:
            attrs = json.loads(attrs_str)
        except Exception:
            attrs = {}

        if ft == "step":
            # JCL execution step fact
            src_b = name or (skey.rsplit(".", 1)[-1] if "." in skey else skey)
            dst_b = val
            raw_occurrences.append({
                "rel_path": rpath,
                "language": lang,
                "kind": "edge",
                "edge_type": "executes",
                "src_binding": src_b,
                "dst_binding": dst_b,
                "guard_json": None,
                "occurrence_ix": occ_ix,
                "line_start": lstart,
                "line_end": lend,
                "source_fact_id": r["id"],
                "semantic_key": skey,
                "attributes": attrs_str,
            })
        elif ft == "call":
            # COBOL call statement
            src_b = parent.rsplit("/", 1)[-1].rsplit(".", 1)[-1] if "/" in parent else parent
            dst_b = name
            raw_occurrences.append({
                "rel_path": rpath,
                "language": lang,
                "kind": "edge",
                "edge_type": "calls",
                "src_binding": src_b,
                "dst_binding": dst_b,
                "guard_json": None,
                "occurrence_ix": occ_ix,
                "line_start": lstart,
                "line_end": lend,
                "source_fact_id": r["id"],
                "semantic_key": skey,
                "attributes": attrs_str,
            })
        elif ft == "branch":
            # COBOL branch (IF / EVALUATE) guard
            src_b = parent.rsplit("/", 1)[-1] if "/" in parent else parent
            g_obj = {"raw": val, "kind": attrs.get("kind", "branch"), "name": name}
            raw_occurrences.append({
                "rel_path": rpath,
                "language": lang,
                "kind": "guard",
                "edge_type": None,
                "src_binding": src_b,
                "dst_binding": None,
                "guard_json": json.dumps(g_obj),
                "occurrence_ix": occ_ix,
                "line_start": lstart,
                "line_end": lend,
                "source_fact_id": r["id"],
                "semantic_key": skey,
                "attributes": attrs_str,
            })
        elif ft == "cond_gate":
            # JCL cond_gate guard
            src_b = skey.rsplit(".", 1)[-1] if "." in skey else skey
            g_obj = {"raw": val}
            if "execute_when" in attrs:
                g_obj["execute_when"] = attrs["execute_when"]
            raw_occurrences.append({
                "rel_path": rpath,
                "language": lang,
                "kind": "guard",
                "edge_type": None,
                "src_binding": src_b,
                "dst_binding": None,
                "guard_json": json.dumps(g_obj),
                "occurrence_ix": occ_ix,
                "line_start": lstart,
                "line_end": lend,
                "source_fact_id": r["id"],
                "semantic_key": skey,
                "attributes": attrs_str,
            })
        elif ft == "guard":
            # CLIST IF guard
            src_b = rpath.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            g_obj = {"raw": val}
            raw_occurrences.append({
                "rel_path": rpath,
                "language": lang,
                "kind": "guard",
                "edge_type": None,
                "src_binding": src_b,
                "dst_binding": None,
                "guard_json": json.dumps(g_obj),
                "occurrence_ix": occ_ix,
                "line_start": lstart,
                "line_end": lend,
                "source_fact_id": r["id"],
                "semantic_key": skey,
                "attributes": attrs_str,
            })

    # 5. Extract additional edge occurrences from symbol_graph_edges where evidence_lines exists
    seen_edges: set[tuple[str, int, str]] = {
        (item["rel_path"], item["line_start"], item["edge_type"])
        for item in raw_occurrences
        if item["kind"] == "edge" and item["edge_type"]
    }

    async with db.execute(
        "SELECT src_symbol, dst_symbol, edge_type, evidence_lines, resolution_method FROM symbol_graph_edges WHERE snapshot_id=?",
        (snapshot_id,),
    ) as cur:
        sym_rows = await cur.fetchall()

    for r in sym_rows:
        et = r["edge_type"]
        if et not in ("menu_option", "calls", "submits", "shows_panel", "stacks", "executes"):
            continue

        src_sym = r["src_symbol"]
        dst_sym = r["dst_symbol"]
        ev_lines_raw = r["evidence_lines"] or "[]"
        try:
            ev_lines = json.loads(ev_lines_raw)
        except Exception:
            ev_lines = []

        if "::" in src_sym:
            src_file, src_b = src_sym.split("::", 1)
        else:
            src_file, src_b = src_sym, ""

        if "::" in dst_sym:
            dst_b = dst_sym.split("::", 1)[-1]
        else:
            dst_b = dst_sym

        lang = _detect_language(src_file)

        for ln in ev_lines:
            if (src_file, ln, et) in seen_edges:
                continue
            seen_edges.add((src_file, ln, et))

            skey = f"{et}/{src_b}->{dst_b}"
            raw_occurrences.append({
                "rel_path": src_file,
                "language": lang,
                "kind": "edge",
                "edge_type": et,
                "src_binding": src_b,
                "dst_binding": dst_b,
                "guard_json": None,
                "occurrence_ix": 0,
                "line_start": ln,
                "line_end": ln,
                "source_fact_id": None,
                "semantic_key": skey,
                "attributes": json.dumps({"resolution_method": r["resolution_method"]}),
            })

    # 6. Sort deterministically by (rel_path, line_start, occurrence_ix, semantic_key)
    raw_occurrences.sort(
        key=lambda x: (x["rel_path"], x["line_start"], x["occurrence_ix"], x["semantic_key"])
    )

    # 7. Resolve target paths and assign occurrence_ix / id
    occ_counts: dict[tuple[str, str], int] = {}
    now = utc_now_iso()
    rows_to_insert: list[tuple] = []
    occurrences_by_kind: dict[str, int] = {}

    for item in raw_occurrences:
        rpath = item["rel_path"]
        skey = item["semantic_key"]
        key_tuple = (rpath, skey)
        occ_ix = occ_counts.get(key_tuple, 0)
        occ_counts[key_tuple] = occ_ix + 1

        ev_id = f"evocc:{snapshot_id}:{rpath}:{skey}:{occ_ix}"
        dst_b = item["dst_binding"]
        dst_rel_path: str | None = None

        if dst_b:
            clean_dst = dst_b.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            atype = _asset_type_for_edge(item["edge_type"])
            resolve_res = resolve_asset(dst_b, atype, manifest_paths)
            if resolve_res.status == "RESOLVED":
                dst_rel_path = resolve_res.rel_path
            else:
                resolve_stem_res = resolve_asset(clean_dst, atype, manifest_paths)
                if resolve_stem_res.status == "RESOLVED":
                    dst_rel_path = resolve_stem_res.rel_path

        p_status = parse_status_map.get(rpath, "ok")
        kind = item["kind"]
        occurrences_by_kind[kind] = occurrences_by_kind.get(kind, 0) + 1

        rows_to_insert.append((
            ev_id,
            snapshot_id,
            rpath,
            item["language"],
            kind,
            item["edge_type"],
            item["src_binding"],
            dst_b,
            dst_rel_path,
            item["guard_json"],
            occ_ix,
            item["line_start"],
            item["line_end"],
            p_status,
            item["source_fact_id"],
            item["attributes"],
            now,
        ))

    if rows_to_insert:
        await db.executemany(
            """
            INSERT INTO bfi_evidence_occurrences (
                id, snapshot_id, rel_path, language, kind, edge_type,
                src_binding, dst_binding, dst_rel_path, guard_json,
                occurrence_ix, line_start, line_end, parse_status,
                source_fact_id, attributes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows_to_insert,
        )
        await db.commit()

    return EvidenceIndexResult(
        snapshot_id=snapshot_id,
        total_occurrences=len(rows_to_insert),
        occurrences_by_kind=occurrences_by_kind,
    )


async def load_evidence_for_target(
    db: Any, snapshot_id: str, dst_binding_or_rel_path: str
) -> list[EvidenceOccurrence]:
    """Load evidence occurrences for a target binding or rel_path for downstream verifiers."""
    clean_target = dst_binding_or_rel_path.strip()
    target_basename = clean_target.rsplit("/", 1)[-1]
    target_stem = target_basename.rsplit(".", 1)[0]

    async with db.execute(
        """
        SELECT id, snapshot_id, rel_path, language, kind, edge_type,
               src_binding, dst_binding, dst_rel_path, guard_json,
               occurrence_ix, line_start, line_end, parse_status,
               source_fact_id, attributes, created_at
        FROM bfi_evidence_occurrences
        WHERE snapshot_id=? AND (
            rel_path=? OR dst_rel_path=? OR dst_binding=? OR dst_binding=? OR dst_binding=? OR src_binding=?
        )
        ORDER BY rel_path ASC, line_start ASC, occurrence_ix ASC
        """,
        (
            snapshot_id,
            clean_target,
            clean_target,
            clean_target,
            target_basename,
            target_stem,
            clean_target,
        ),
    ) as cur:
        rows = await cur.fetchall()

    return [
        EvidenceOccurrence(
            id=r["id"],
            snapshot_id=r["snapshot_id"],
            rel_path=r["rel_path"],
            language=r["language"],
            kind=r["kind"],
            edge_type=r["edge_type"],
            src_binding=r["src_binding"],
            dst_binding=r["dst_binding"],
            dst_rel_path=r["dst_rel_path"],
            guard_json=r["guard_json"],
            occurrence_ix=r["occurrence_ix"],
            line_start=r["line_start"],
            line_end=r["line_end"],
            parse_status=r["parse_status"],
            source_fact_id=r["source_fact_id"],
            attributes=r["attributes"],
            created_at=r["created_at"],
        )
        for r in rows
    ]
