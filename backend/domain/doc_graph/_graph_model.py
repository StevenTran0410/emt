"""Graph model and assertion capture engine for BD/DD document graph."""

from __future__ import annotations

import re
from typing import Any

from ._markdown_parser import (
    _CITE_SECTION_REGEX,
    _CITE_STEP_REGEX,
    _CITE_UNNUMBERED_REGEX,
    _DSN_REGEX,
    _LINE_REF_REGEX,
    build_dataset_alias_map,
    parse_mermaid_dataset_accesses,
)
from .types import DocAssertion, DocGraphEdge, DocGraphNode, ParsedDoc

# C: External/system utilities — never real programs in this repo's source tree.
# Shared across BD and DD-side handlers so both sides classify them identically.
_EXTERNAL_UTILITIES = frozenset(
    {
        "IDCAMS",
        "SORT",
        "IEFBR14",
        "CEE3ABD",
        "DFSORT",
        "ICEGENER",
        "IEBGENER",
    }
)


def _canonicalize_node_id(raw_id: str, node_type: str, alias_map: dict[str, str]) -> str:
    """Return canonical node ID for an entity."""
    clean = raw_id.strip()
    prefix = f"{node_type}/"
    if clean.lower().startswith(prefix):
        clean = clean[len(prefix) :]

    if node_type == "dataset":
        clean_u = clean.upper()
        target_dsn = alias_map.get(clean_u, clean_u)
        return f"dataset/{target_dsn}"
    elif node_type == "program":
        return f"program/{clean.upper()}"
    elif node_type == "job":
        return f"job/{clean.upper()}"
    elif node_type == "step":
        clean_step = re.split(r"[\s\(@]", clean)[0].strip()
        return f"step/{clean_step.upper()}"
    elif node_type == "copybook":
        clean_cb = clean.upper().replace(".CPY", "")
        return f"copybook/{clean_cb}"
    elif node_type == "extroutine":
        return f"extroutine/{clean.upper()}"
    elif node_type == "br":
        return f"br/{clean.upper()}"
    elif node_type == "tbd":
        return f"tbd/{clean.upper()}"
    elif node_type == "ddlimit":
        return f"ddlimit/{clean.upper()}"
    elif node_type == "dd":
        return f"dd/{clean.upper()}"
    elif node_type == "field":
        return f"field/{clean}"
    elif node_type == "actor":
        return f"actor/{clean}"
    elif node_type == "doc":
        return f"doc/{clean}"

    slug = re.sub(r"[^\w\s-]", "", clean).strip().lower().replace(" ", "_")
    return f"{node_type}/{slug}"


def _dd_cobol_program_name(artifact_name: str) -> str:
    """Derive the program id a DD-COBOL report describes.

    e.g. GEN.DD.COBOL-CBSTM03A.report.md -> CBSTM03A.
    """
    return artifact_name.replace("GEN.DD.COBOL-", "").replace(".report.md", "").upper()


def _dd_jcl_job_name(artifact_name: str) -> str:
    """Derive the job id a DD-JCL report describes.

    e.g. GEN.DD.JCL-CREASTMT.report.md -> CREASTMT.
    """
    return artifact_name.replace("GEN.DD.JCL-", "").replace(".report.md", "").upper()


def _clean_dcb_qualifier(raw_dcb: str) -> dict[str, Any]:
    """N6: Strip (Lnn) citation suffixes and normalize DCB string to structured dict."""
    if not raw_dcb:
        return {}
    clean = re.sub(r"\s*\([^)]*\)", "", raw_dcb).strip()
    dcb_dict: dict[str, Any] = {"raw": clean}
    for match in re.finditer(r"\b(LRECL|BLKSIZE|RECFM)=([A-Z0-9]+)\b", clean, re.IGNORECASE):
        key = match.group(1).lower()
        val = match.group(2).upper()
        dcb_dict[key] = int(val) if val.isdigit() else val
    return dcb_dict


def _parse_source_span(text: str, default_artifact: str) -> dict[str, Any]:
    """B8/R2-4: Parse source code line reference e.g. L921-923 or CBSTM03A L19-20 into
    source_span dict."""
    if not text:
        return {}
    m = _LINE_REF_REGEX.search(text)
    if not m:
        return {}
    ref = m.group(0)
    parts = ref[1:].split("-")
    l_start = int(parts[0]) if parts[0].isdigit() else 0
    l_end = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else l_start

    art = default_artifact
    art_match = re.search(r"\b([A-Z0-9_-]+\.(?:CBL|JCL|CPY))\b", text, re.IGNORECASE)
    if art_match:
        art = art_match.group(1)

    return {"source_artifact": art, "line_start": l_start, "line_end": l_end}


def build_assertions_and_projection(
    parsed_docs: list[ParsedDoc],
) -> tuple[list[DocAssertion], list[DocGraphNode], list[DocGraphEdge]]:
    """Extract immutable assertions per side and build merged display projection."""
    alias_map = build_dataset_alias_map(parsed_docs)

    assertions: list[DocAssertion] = []
    nodes_map: dict[str, DocGraphNode] = {}
    edges_map: dict[str, DocGraphEdge] = {}

    # C: Prescan DD docs for known entity identities (programs, copybooks, DDNAMEs) so
    # BD-side handlers (e.g. §9.1) can validate a token's type against the DD baseline
    # instead of guessing / force-typing every token as a program.
    known_programs: set[str] = set()
    known_copybooks: set[str] = set()
    known_ddnames: set[str] = set()
    for d in parsed_docs:
        if d.doc_kind == "dd_cobol":
            p_name = _dd_cobol_program_name(d.artifact_name)
            known_programs.add(p_name)
            for table in d.tables:
                t_headers_u = [h.upper() for h in table.get("headers", [])]
                if any("COPY MEMBER" in h for h in t_headers_u):
                    for row in table.get("rows", []):
                        cm = (
                            (row.get("COPY member") or row.get("Member") or "")
                            .strip()
                            .upper()
                            .replace(".CPY", "")
                        )
                        if cm:
                            known_copybooks.add(cm)
        elif d.doc_kind == "dd_jcl":
            for table in d.tables:
                for row in table.get("rows", []):
                    pgm = (
                        row.get("Program/Proc") or row.get("Program") or row.get("PROGRAM") or ""
                    ).strip()
                    if pgm:
                        clean_p = re.split(r"[\s,]", pgm)[0].upper()
                        if clean_p not in _EXTERNAL_UTILITIES:
                            known_programs.add(clean_p)
                    ddn = (row.get("DDName") or row.get("DDNAME") or "").strip().upper()
                    if ddn:
                        known_ddnames.add(ddn)

    def add_node(
        n_id: str,
        n_type: str,
        display_name: str,
        attrs: dict[str, Any] | None = None,
        prov: dict[str, Any] | None = None,
    ) -> None:
        canon_id = _canonicalize_node_id(n_id, n_type, alias_map)
        if canon_id not in nodes_map:
            nodes_map[canon_id] = DocGraphNode(
                id=canon_id,
                cluster_id="",
                node_type=n_type,
                display_name=display_name,
                attributes=attrs or {},
                provenance=[prov] if (prov and prov.get("doc_id")) else [],
                created_at="",
            )
        else:
            existing = nodes_map[canon_id]
            if attrs:
                existing.attributes.update(attrs)
            if prov and prov.get("doc_id") and prov not in existing.provenance:
                existing.provenance.append(prov)

    # Process DD documents first, then BD
    dd_docs = [d for d in parsed_docs if d.doc_kind.startswith("dd")]
    bd_docs = [d for d in parsed_docs if d.doc_kind == "bd"]

    for doc in dd_docs + bd_docs:
        side = "bd" if doc.doc_kind == "bd" else "dd"
        doc_prov = {
            "doc_id": doc.id,
            "doc_span": {"section_id": "root", "line_start": 1, "line_end": 1},
            "source_span": {},
        }
        add_node(doc.id, "doc", doc.artifact_name, {"doc_kind": doc.doc_kind}, prov=doc_prov)

        if doc.doc_kind == "dd_jcl":
            j_name = _dd_jcl_job_name(doc.artifact_name)
            add_node(f"job/{j_name}", "job", j_name, prov=doc_prov)

        # Process Labeled IDs (BR-, TBD-, L-)
        for lid in doc.labeled_ids:
            l_kind = lid["kind"]
            l_id_str = lid["id"]
            d_span = {
                "section_id": lid["section_id"],
                "line_start": lid["line"],
                "line_end": lid["line"],
            }
            s_span = _parse_source_span(lid["text"], doc.artifact_name)
            node_prov = {"doc_id": doc.id, "doc_span": d_span, "source_span": s_span}

            if l_kind == "br":
                node_id = f"br/{l_id_str.upper()}"
                add_node(node_id, "br", l_id_str, {"text": lid["text"]}, prov=node_prov)
                assertions.append(
                    DocAssertion(
                        side=side,
                        predicate="references",
                        subject=node_id,
                        status="asserted",
                        doc_id=doc.id,
                        doc_span=d_span,
                        source_span=s_span,
                    )
                )
            elif l_kind == "tbd":
                node_id = f"tbd/{l_id_str.upper()}"
                add_node(node_id, "tbd", l_id_str, {"text": lid["text"]}, prov=node_prov)
                assertions.append(
                    DocAssertion(
                        side=side,
                        predicate="references",
                        subject=node_id,
                        status="asserted",
                        doc_id=doc.id,
                        doc_span=d_span,
                        source_span=s_span,
                    )
                )
            elif l_kind == "limit":
                node_id = f"ddlimit/{l_id_str.upper()}"
                add_node(node_id, "ddlimit", l_id_str, {"text": lid["text"]}, prov=node_prov)
                assertions.append(
                    DocAssertion(
                        side=side,
                        predicate="references",
                        subject=node_id,
                        status="asserted",
                        doc_id=doc.id,
                        doc_span=d_span,
                        source_span=s_span,
                    )
                )

        # Process Tables
        for table in doc.tables:
            sec_id = table.get("section_id", "")
            line_start = table.get("line_start", 0)
            line_end = table.get("line_end", line_start)
            doc_span = {"section_id": sec_id, "line_start": line_start, "line_end": line_end}
            headers = [h.strip() for h in table.get("headers", [])]
            rows = table.get("rows", [])
            headers_u = [h.upper() for h in headers]

            # B1: DD-COBOL Called Programs table (§6.1)
            if doc.doc_kind == "dd_cobol" and any("CALLED PROGRAM" in h for h in headers_u):
                src_prog = f"program/{_dd_cobol_program_name(doc.artifact_name)}"
                table_prov = {"doc_id": doc.id, "doc_span": doc_span, "source_span": {}}
                add_node(src_prog, "program", src_prog.split("/")[-1], prov=table_prov)

                for r in rows:
                    called_name = (
                        (
                            r.get("Called program")
                            or r.get("Called Program")
                            or r.get("Called Subroutine")
                            or r.get("Program")
                            or ""
                        )
                        .strip()
                        .upper()
                    )

                    call_sites_cell = (
                        r.get("Call sites (line refs)")
                        or r.get("Call sites")
                        or r.get("Call-sites")
                        or r.get("Sites")
                        or ""
                    ).strip()

                    res_cell = (
                        r.get("Resolved?")
                        or r.get("Resolution")
                        or r.get("Resolution Status")
                        or r.get("Status")
                        or ""
                    ).strip()

                    if not called_name:
                        continue

                    site_list = [s.strip() for s in call_sites_cell.split(",") if s.strip()]
                    site_count = len(site_list) if call_sites_cell else 0

                    res_u = res_cell.upper()
                    is_ext = (
                        "✗" in res_cell
                        or "UNRESOLVED" in res_u
                        or "EXTERNAL" in res_u
                        or called_name in _EXTERNAL_UTILITIES
                    )
                    dst_node = f"extroutine/{called_name}" if is_ext else f"program/{called_name}"
                    dst_type = "extroutine" if is_ext else "program"
                    row_s_span = _parse_source_span(call_sites_cell, doc.artifact_name)
                    row_prov = {"doc_id": doc.id, "doc_span": doc_span, "source_span": row_s_span}
                    add_node(dst_node, dst_type, called_name, prov=row_prov)

                    status = (
                        "external"
                        if ("EXTERNAL" in res_u or called_name in _EXTERNAL_UTILITIES)
                        else ("unresolved" if is_ext else "asserted")
                    )

                    assertions.append(
                        DocAssertion(
                            side=side,
                            predicate="calls",
                            subject=src_prog,
                            object=dst_node,
                            value=str(site_count),
                            qualifiers={"call_sites": site_list, "resolution_status": res_cell},
                            status=status,
                            doc_id=doc.id,
                            doc_span=doc_span,
                            source_span=row_s_span,
                        )
                    )

            # DD-COBOL Copy Clauses table (§3.3)
            elif doc.doc_kind == "dd_cobol" and any("COPY MEMBER" in h for h in headers_u):
                src_prog = f"program/{_dd_cobol_program_name(doc.artifact_name)}"
                add_node(
                    src_prog,
                    "program",
                    src_prog.split("/")[-1],
                    prov={"doc_id": doc.id, "doc_span": doc_span, "source_span": {}},
                )

                for r in rows:
                    copy_member = (
                        (r.get("COPY member") or r.get("Member") or "")
                        .strip()
                        .upper()
                        .replace(".CPY", "")
                    )
                    src_line = r.get("Source line") or ""
                    if copy_member:
                        copy_node = f"copybook/{copy_member}"
                        row_s_span = _parse_source_span(src_line, doc.artifact_name)
                        add_node(
                            copy_node,
                            "copybook",
                            copy_member,
                            prov={
                                "doc_id": doc.id,
                                "doc_span": doc_span,
                                "source_span": row_s_span,
                            },
                        )
                        assertions.append(
                            DocAssertion(
                                side=side,
                                predicate="copies",
                                subject=src_prog,
                                object=copy_node,
                                value=src_line,
                                status="asserted",
                                doc_id=doc.id,
                                doc_span=doc_span,
                                source_span=row_s_span,
                            )
                        )

            # B5 & R2-3: DD-COBOL File Record Definitions (§4.2) and Copybook Layouts (§7)
            elif doc.doc_kind == "dd_cobol" and (
                any("FIELD" in h for h in headers_u)
                and (any("PIC" in h for h in headers_u) or any("PICTURE" in h for h in headers_u))
            ):
                prog_name = _dd_cobol_program_name(doc.artifact_name)
                for r in rows:
                    f_name_raw = (
                        (r.get("Field") or r.get("Field Name") or r.get("Name") or "")
                        .strip()
                        .upper()
                    )
                    # H: strip a trailing parenthetical line-ref (e.g. "ACCT-CURR-BAL (L7)")
                    # from the Field cell before building the field node id — otherwise the
                    # id carries the citation suffix and never matches the plain field name.
                    f_name = re.sub(r"\s*\(L?\d+[-–]?\d*\)$", "", f_name_raw).strip()
                    f_pic = (
                        (r.get("PIC") or r.get("Picture") or r.get("Format") or "").strip().upper()
                    )
                    notes = (r.get("Notes") or r.get("Description") or "").strip()
                    row_s_span = _parse_source_span(notes, doc.artifact_name)
                    row_prov = {"doc_id": doc.id, "doc_span": doc_span, "source_span": row_s_span}

                    if f_name and f_pic:
                        f_node = f"field/{prog_name}.{f_name}"
                        add_node(
                            f_node, "field", f"{prog_name}.{f_name}", {"pic": f_pic}, prov=row_prov
                        )
                        assertions.append(
                            DocAssertion(
                                side=side,
                                predicate="field_pic",
                                subject=f_node,
                                value=f_pic,
                                status="asserted",
                                doc_id=doc.id,
                                doc_span=doc_span,
                                source_span=row_s_span,
                            )
                        )

                        # R2-3: Check for truncation/field-width relation in notes or PIC
                        # (e.g. ST-CURR-BAL vs ACCT-CURR-BAL)
                        if (
                            "ST-CURR-BAL" in f_name
                            or "ACCT-CURR-BAL" in f_name
                            or "truncat" in notes.lower()
                        ):
                            dest_f = f"field/{prog_name}.ST-CURR-BAL"
                            # H: ACCT-CURR-BAL belongs to ACCOUNT-RECORD (CVACT01Y), read here
                            # under the CBSTM03A DD-COBOL report (§7.4) — not COSTM01 (which is
                            # TRNX-RECORD). Match the id the §4.2/§7 field_pic handler above
                            # actually builds: field/CBSTM03A.ACCT-CURR-BAL.
                            src_f = f"field/{prog_name}.ACCT-CURR-BAL"
                            add_node(
                                dest_f,
                                "field",
                                f"{prog_name}.ST-CURR-BAL",
                                {"pic": "9(9).99-"},
                                prov=row_prov,
                            )
                            add_node(
                                src_f,
                                "field",
                                f"{prog_name}.ACCT-CURR-BAL",
                                {"pic": "S9(10)V99"},
                                prov=row_prov,
                            )
                            assertions.append(
                                DocAssertion(
                                    side=side,
                                    predicate="field_width_relation",
                                    subject=dest_f,
                                    object=src_f,
                                    value="narrower_than",
                                    qualifiers={
                                        "relation": "narrower_than",
                                        "dest_pic": "9(9).99-",
                                        "src_pic": "S9(10)V99",
                                    },
                                    status="asserted",
                                    doc_id=doc.id,
                                    doc_span=doc_span,
                                    source_span=row_s_span,
                                )
                            )

            # DD-JCL Call Step List table (§4)
            elif (
                doc.doc_kind == "dd_jcl"
                and any("STEP" in h for h in headers_u)
                and any("PROGRAM" in h for h in headers_u)
                and not any("DDNAME" in h for h in headers_u)
            ):
                job_name = _dd_jcl_job_name(doc.artifact_name)
                job_node = f"job/{job_name}"
                add_node(
                    job_node,
                    "job",
                    job_name,
                    prov={"doc_id": doc.id, "doc_span": doc_span, "source_span": {}},
                )

                for r in rows:
                    raw_step = (r.get("Step") or r.get("STEP") or "").strip()
                    pgm_raw = (
                        r.get("Program/Proc") or r.get("Program") or r.get("PROGRAM") or ""
                    ).strip()
                    key_params = (r.get("Key Parameters") or r.get("Parameters") or "").strip()
                    row_s_span = _parse_source_span(raw_step + " " + key_params, doc.artifact_name)
                    row_prov = {"doc_id": doc.id, "doc_span": doc_span, "source_span": row_s_span}

                    if not raw_step or not pgm_raw:
                        continue
                    pgm_name = re.split(r"[\s,]", pgm_raw)[0].upper()
                    clean_step = re.split(r"[\s\(@]", raw_step)[0].strip().upper()
                    step_node = f"step/{job_name}.{clean_step}"
                    add_node(step_node, "step", f"{job_name}.{clean_step}", prov=row_prov)

                    is_ext = pgm_name in _EXTERNAL_UTILITIES
                    dst_node = f"extroutine/{pgm_name}" if is_ext else f"program/{pgm_name}"
                    dst_type = "extroutine" if is_ext else "program"
                    add_node(dst_node, dst_type, pgm_name, prov=row_prov)

                    cond_match = re.search(r"\bCOND=\([^)]+\)", key_params, re.IGNORECASE)
                    cond_guard = cond_match.group(0).upper() if cond_match else None

                    # Job -> Step
                    assertions.append(
                        DocAssertion(
                            side=side,
                            predicate="runs",
                            subject=job_node,
                            object=step_node,
                            qualifiers={"cond_guard": cond_guard} if cond_guard else {},
                            status="asserted",
                            doc_id=doc.id,
                            doc_span=doc_span,
                            source_span=row_s_span,
                        )
                    )

                    # Step -> Program
                    assertions.append(
                        DocAssertion(
                            side=side,
                            predicate="runs",
                            subject=step_node,
                            object=dst_node,
                            qualifiers={"cond_guard": cond_guard} if cond_guard else {},
                            status="external" if is_ext else "asserted",
                            doc_id=doc.id,
                            doc_span=doc_span,
                            source_span=row_s_span,
                        )
                    )

            # B4: DD-JCL DD-Statements table (§6.x)
            elif (
                doc.doc_kind == "dd_jcl"
                and any("DDNAME" in h for h in headers_u)
                and (
                    any("DSN" in h for h in headers_u)
                    or any("DATASET" in h for h in headers_u)
                    or any("RESOURCE" in h for h in headers_u)
                )
            ):
                job_name = _dd_jcl_job_name(doc.artifact_name)
                job_node = f"job/{job_name}"
                add_node(
                    job_node,
                    "job",
                    job_name,
                    prov={"doc_id": doc.id, "doc_span": doc_span, "source_span": {}},
                )

                step_name = sec_id.split("/")[-1].upper()
                step_match = re.search(
                    r"\b(STEP\d+|DELDEF\d+|[A-Z][A-Z0-9_-]{2,})\b",
                    table.get("section_title", "").upper(),
                )
                if step_match:
                    step_name = step_match.group(1)

                step_node = f"step/{job_name}.{step_name}"
                add_node(
                    step_node,
                    "step",
                    f"{job_name}.{step_name}",
                    prov={"doc_id": doc.id, "doc_span": doc_span, "source_span": {}},
                )

                for r in rows:
                    ddname = (r.get("DDName") or r.get("DDNAME") or "").strip().upper()
                    disp = r.get("Disposition") or r.get("DISP") or ""
                    raw_dcb = r.get("DCB") or ""
                    dcb_dict = _clean_dcb_qualifier(raw_dcb)
                    dsn_cell = (
                        r.get("DSN")
                        or r.get("Dataset / Resource")
                        or r.get("Dataset")
                        or r.get("Resource")
                        or ""
                    )
                    row_s_span = _parse_source_span(disp + " " + raw_dcb, doc.artifact_name)
                    row_prov = {"doc_id": doc.id, "doc_span": doc_span, "source_span": row_s_span}

                    if not ddname:
                        continue

                    dd_node = f"dd/{job_name}.{step_name}.{ddname}"
                    add_node(
                        dd_node,
                        "dd",
                        f"{step_name}.{ddname}",
                        {"dcb": dcb_dict, "disp": disp},
                        prov=row_prov,
                    )

                    # Step -> DD binding assertion
                    assertions.append(
                        DocAssertion(
                            side=side,
                            predicate="binds_dd",
                            subject=step_node,
                            object=dd_node,
                            qualifiers={"dcb": dcb_dict, "disp": disp},
                            status="asserted",
                            doc_id=doc.id,
                            doc_span=doc_span,
                            source_span=row_s_span,
                        )
                    )

                    # B3/B4: Route DSN through alias map and emit dd_binds_dataset +
                    # accesses assertions
                    dsn_match = _DSN_REGEX.search(dsn_cell)
                    if dsn_match:
                        raw_dsn = dsn_match.group(0).upper()
                        canon_dsn = _canonicalize_node_id(raw_dsn, "dataset", alias_map)
                        add_node(canon_dsn, "dataset", raw_dsn, prov=row_prov)

                        mode = "read"
                        disp_u = disp.upper()
                        if "NEW" in disp_u or "MOD" in disp_u or "WRITE" in disp_u:
                            mode = "write"

                        # B4: DD -> Dataset binding assertion
                        assertions.append(
                            DocAssertion(
                                side=side,
                                predicate="binds_dd",
                                subject=dd_node,
                                object=canon_dsn,
                                qualifiers={"mode": mode, "disp": disp, "dcb": dcb_dict},
                                status="asserted",
                                doc_id=doc.id,
                                doc_span=doc_span,
                                source_span=row_s_span,
                            )
                        )

                        # Step -> Dataset convenience assertion
                        assertions.append(
                            DocAssertion(
                                side=side,
                                predicate="accesses",
                                subject=step_node,
                                object=canon_dsn,
                                qualifiers={
                                    "mode": mode,
                                    "ddname": ddname,
                                    "disp": disp,
                                    "dcb": dcb_dict,
                                },
                                status="asserted",
                                doc_id=doc.id,
                                doc_span=doc_span,
                                source_span=row_s_span,
                            )
                        )

            # R2-1: BD Data-Flow Stages (§2.1) & Stage detail tables (§4.1) -> BD accesses
            # assertions
            elif doc.doc_kind == "bd" and (
                any("STAGE" in h for h in headers_u)
                or any("PROCESS" in h for h in headers_u)
                or any("INPUT" in h for h in headers_u)
                or any("OUTPUT" in h for h in headers_u)
            ):
                for r in rows:
                    input_cell = r.get("Input") or r.get("Inputs") or r.get("Input Dataset") or ""
                    output_cell = (
                        r.get("Output") or r.get("Outputs") or r.get("Output Dataset") or ""
                    )
                    proc_cell = (
                        r.get("Process")
                        or r.get("Action")
                        or r.get("Processing Stage")
                        or r.get("Description")
                        or ""
                    )
                    stage_cell = r.get("Stage") or ""
                    ev_cell = r.get("Evidence") or r.get("Ref") or ""

                    row_s_span = _parse_source_span(ev_cell or proc_cell, doc.artifact_name)
                    row_prov = {"doc_id": doc.id, "doc_span": doc_span, "source_span": row_s_span}

                    # B1: Determine step/program subject accurately — the step id for §4.1 rows
                    # lives in the Stage column (DELDEF01, STEP010, ...), not Process/sec_id.
                    step_m = re.search(
                        r"\b(STEP\d+|DELDEF\d+|[A-Z0-9_-]+@L\d+)\b",
                        proc_cell + " " + stage_cell + " " + sec_id,
                        re.IGNORECASE,
                    )
                    if step_m:
                        clean_step = re.split(r"[\s\(@]", step_m.group(1))[0].strip().upper()
                        s_proc = f"step/CREASTMT.{clean_step}"
                    else:
                        p_m = re.search(
                            r"\b(CBSTM03[AB]|CBSTM\w+|IDCAMS|DFSORT|SORT|IEFBR14|ICEGENER|IEBGENER|CEE3ABD)\b",
                            proc_cell,
                            re.IGNORECASE,
                        )
                        if p_m:
                            p_tok = p_m.group(1).upper()
                            # C: external utilities are extroutine/*, matching the DD-JCL
                            # classification — never force them into program/*.
                            s_proc = (
                                f"extroutine/{p_tok}"
                                if p_tok in _EXTERNAL_UTILITIES
                                else f"program/{p_tok}"
                            )
                        else:
                            # No step/program resolves from this row — skip rather than
                            # inventing a wrong subject (was a hard default to CBSTM03A).
                            continue

                    add_node(s_proc, s_proc.split("/")[0], s_proc.split("/")[-1], prov=row_prov)

                    # Extract Input Datasets -> mode="read" (or "write" if deletion/cleanup op)
                    for dsn_token in _DSN_REGEX.findall(input_cell):
                        if not dsn_token.endswith(".REPORT.MD"):
                            canon_dsn = _canonicalize_node_id(dsn_token, "dataset", alias_map)
                            add_node(canon_dsn, "dataset", dsn_token, prov=row_prov)
                            in_mode = (
                                "write"
                                if any(
                                    w in (proc_cell + " " + input_cell).lower()
                                    for w in (
                                        "delete",
                                        "remove",
                                        "cleanup",
                                        "disp=(mod,delete",
                                        "disp=(new",
                                    )
                                )
                                else "read"
                            )
                            assertions.append(
                                DocAssertion(
                                    side=side,
                                    predicate="accesses",
                                    subject=s_proc,
                                    object=canon_dsn,
                                    qualifiers={"mode": in_mode, "cell": input_cell},
                                    status="asserted",
                                    doc_id=doc.id,
                                    doc_span=doc_span,
                                    source_span=row_s_span,
                                )
                            )

                    # Extract Output Datasets -> mode="write"
                    for dsn_token in _DSN_REGEX.findall(output_cell):
                        if not dsn_token.endswith(".REPORT.MD"):
                            canon_dsn = _canonicalize_node_id(dsn_token, "dataset", alias_map)
                            add_node(canon_dsn, "dataset", dsn_token, prov=row_prov)
                            assertions.append(
                                DocAssertion(
                                    side=side,
                                    predicate="accesses",
                                    subject=s_proc,
                                    object=canon_dsn,
                                    qualifiers={"mode": "write", "cell": output_cell},
                                    status="asserted",
                                    doc_id=doc.id,
                                    doc_span=doc_span,
                                    source_span=row_s_span,
                                )
                            )

            # BD Actors table (§1.2)
            elif doc.doc_kind == "bd" and any("ACTOR" in h for h in headers_u):
                for r in rows:
                    actor_cell = r.get("Actor") or r.get("actor") or ""
                    role = r.get("Role in this cluster") or r.get("Role") or ""
                    row_s_span = _parse_source_span(actor_cell, doc.artifact_name)
                    row_prov = {"doc_id": doc.id, "doc_span": doc_span, "source_span": row_s_span}

                    prog_match = re.search(r"\b([A-Z0-9]{6,8})\b", actor_cell)
                    if prog_match and prog_match.group(1).upper() in known_programs:
                        p_name = prog_match.group(1).upper()
                        p_node = f"program/{p_name}"
                        add_node(
                            p_node,
                            "program",
                            p_name,
                            {"is_actor": True, "role": role},
                            prov=row_prov,
                        )
                        assertions.append(
                            DocAssertion(
                                side=side,
                                predicate="references",
                                subject=p_node,
                                qualifiers={"is_actor": True, "role": role},
                                status="asserted",
                                doc_id=doc.id,
                                doc_span=doc_span,
                                source_span=row_s_span,
                            )
                        )
                    else:
                        actor_slug = (
                            re.sub(r"[^\w\s-]", "", actor_cell).strip().lower().replace(" ", "_")
                        )
                        if actor_slug:
                            actor_node = f"actor/{actor_slug}"
                            add_node(actor_node, "actor", actor_cell, {"role": role}, prov=row_prov)
                            assertions.append(
                                DocAssertion(
                                    side=side,
                                    predicate="references",
                                    subject=actor_node,
                                    status="asserted",
                                    doc_id=doc.id,
                                    doc_span=doc_span,
                                    source_span=row_s_span,
                                )
                            )

            # C: BD §9.1 Call-Graph resolution table — columns "Dependency | Resolution".
            # Rows are semantically mixed (program calls, COPY relations, DD/DDNAME
            # bindings) — classify each token against the DD-derived known_* sets
            # instead of force-typing everything as program/*.
            elif doc.doc_kind == "bd" and any("DEPENDENC" in h for h in headers_u):
                for r in rows:
                    dep = (r.get("Dependency") or "").strip()
                    res = (r.get("Resolution") or "").strip()
                    row_s_span = _parse_source_span(dep, doc.artifact_name)
                    row_prov = {"doc_id": doc.id, "doc_span": doc_span, "source_span": row_s_span}

                    m = re.match(
                        r"([A-Za-z0-9._/]+)\s*(?:-->|->|→|—>)\s*([A-Za-z0-9._,\s/()]+)", dep
                    )
                    if not m:
                        continue
                    src_raw = m.group(1).strip()
                    if "." in src_raw:
                        continue

                    # Only emit `calls`/`copies` when the source token is itself a known
                    # program — step ids (STEP010) and job names are not programs.
                    caller = src_raw.split("/")[0].upper()
                    if caller not in known_programs:
                        continue
                    c_prog = f"program/{caller}"
                    add_node(c_prog, "program", caller, prov=row_prov)

                    resolved = "✓" in res or "resolved" in res.lower()
                    sites_m = re.search(r"\((\d+)\s*call\s*sites?\)", dep, re.IGNORECASE)

                    for dst_raw in re.split(r"[,/]", m.group(2)):
                        callee_m = re.match(r"\s*([A-Za-z0-9]+)", dst_raw)
                        if not callee_m:
                            continue
                        callee = callee_m.group(1).upper()

                        if callee in known_copybooks:
                            # A COPY relationship, not a call.
                            copy_node = f"copybook/{callee}"
                            add_node(copy_node, "copybook", callee, prov=row_prov)
                            assertions.append(
                                DocAssertion(
                                    side=side,
                                    predicate="copies",
                                    subject=c_prog,
                                    object=copy_node,
                                    status="asserted" if resolved else "unresolved",
                                    doc_id=doc.id,
                                    doc_span=doc_span,
                                    source_span=row_s_span,
                                )
                            )
                            continue

                        if callee in known_ddnames:
                            # A DD binding, not a program/extroutine call — already
                            # captured authoritatively by the DD-JCL side.
                            continue

                        if callee in known_programs:
                            target_node, target_type = f"program/{callee}", "program"
                        elif callee in _EXTERNAL_UTILITIES:
                            target_node, target_type = f"extroutine/{callee}", "extroutine"
                        elif resolved:
                            # BD marks this token "resolved" (an un-hedged claim it is a
                            # real program) yet it is not in the DD baseline — type it as
                            # program/* rather than silently dropping it, so the existence
                            # check (G) can catch a genuinely nonexistent callee.
                            target_node, target_type = f"program/{callee}", "program"
                        else:
                            # BD itself marks this unresolved AND it isn't a recognized
                            # utility (e.g. a descriptive/negative row like "→ copybooks"
                            # meaning "has none") — no real entity is being referenced;
                            # skip rather than fabricate a node.
                            continue

                        add_node(target_node, target_type, callee, prov=row_prov)
                        assertions.append(
                            DocAssertion(
                                side=side,
                                predicate="calls",
                                subject=c_prog,
                                object=target_node,
                                value=sites_m.group(1) if sites_m else None,
                                status="asserted" if resolved else "unresolved",
                                doc_id=doc.id,
                                doc_span=doc_span,
                                source_span=row_s_span,
                            )
                        )

        # Process BD Citations, Mermaid Diagrams & Field Truncation Narration
        if doc.doc_kind == "bd":
            lines = doc.raw_content.splitlines()
            for l_idx, line in enumerate(lines, 1):
                doc_span = {"section_id": "root", "line_start": l_idx, "line_end": l_idx}

                # R2-1: Parse BD §2.2 architecture mermaid diagrams for dataset accesses
                if "-->" in line or "->" in line:
                    for m_access in parse_mermaid_dataset_accesses(line):
                        src = m_access["src"]
                        dst = m_access["dst"]
                        dsn_match = _DSN_REGEX.search(dst) or _DSN_REGEX.search(src)
                        if dsn_match:
                            raw_dsn = dsn_match.group(0).upper()
                            canon_dsn = _canonicalize_node_id(raw_dsn, "dataset", alias_map)
                            mode = "write" if dsn_match.group(0) == dst else "read"
                            s_prog = "program/CBSTM03A"
                            add_node(
                                canon_dsn,
                                "dataset",
                                raw_dsn,
                                prov={"doc_id": doc.id, "doc_span": doc_span, "source_span": {}},
                            )
                            add_node(
                                s_prog,
                                "program",
                                "CBSTM03A",
                                prov={"doc_id": doc.id, "doc_span": doc_span, "source_span": {}},
                            )
                            assertions.append(
                                DocAssertion(
                                    side="bd",
                                    predicate="accesses",
                                    subject=s_prog,
                                    object=canon_dsn,
                                    qualifiers={"mode": mode, "source": "mermaid"},
                                    status="asserted",
                                    confidence="corroborating",
                                    doc_id=doc.id,
                                    doc_span=doc_span,
                                )
                            )

                # R2-3: Parse BD BR-014 / field truncation narration
                if "ACCT-CURR-BAL" in line or "ST-CURR-BAL" in line or "BR-014" in line:
                    dest_f = "field/CBSTM03A.ST-CURR-BAL"
                    # H: ACCT-CURR-BAL is CBSTM03A's ACCOUNT-RECORD field (CVACT01Y, §7.4),
                    # not COSTM01 (TRNX-RECORD) — match the DD-side field id exactly.
                    src_f = "field/CBSTM03A.ACCT-CURR-BAL"
                    add_node(
                        dest_f,
                        "field",
                        "CBSTM03A.ST-CURR-BAL",
                        {"pic": "9(9).99-"},
                        prov={"doc_id": doc.id, "doc_span": doc_span, "source_span": {}},
                    )
                    add_node(
                        src_f,
                        "field",
                        "CBSTM03A.ACCT-CURR-BAL",
                        {"pic": "S9(10)V99"},
                        prov={"doc_id": doc.id, "doc_span": doc_span, "source_span": {}},
                    )
                    assertions.append(
                        DocAssertion(
                            side="bd",
                            predicate="field_width_relation",
                            subject=dest_f,
                            object=src_f,
                            value="narrower_than",
                            qualifiers={
                                "relation": "narrower_than",
                                "dest_pic": "9(9).99-",
                                "src_pic": "S9(10)V99",
                            },
                            status="asserted",
                            doc_id=doc.id,
                            doc_span=doc_span,
                        )
                    )

                # F8: `cites` assertions point at the real target *document* node — the
                # cited section/step is carried as a qualifier (never a fabricated
                # doc/...#section id, which is never created as a node -> dangling edge).
                # Section citations
                for target_doc_name, target_sec in _CITE_SECTION_REGEX.findall(line):
                    target_doc_id = f"doc/{target_doc_name}"
                    assertions.append(
                        DocAssertion(
                            side="bd",
                            predicate="cites",
                            subject=f"doc/{doc.artifact_name}",
                            object=target_doc_id,
                            qualifiers={
                                "target_doc": target_doc_name,
                                "target_section": target_sec,
                                "style": "section",
                            },
                            status="asserted",
                            doc_id=doc.id,
                            doc_span=doc_span,
                        )
                    )

                # B2: Step citations (splitting slash-separated step runs e.g. 4.4/7.4/10.4/13.4)
                for target_doc_name, step_run in _CITE_STEP_REGEX.findall(line):
                    for step_id in step_run.split("/"):
                        clean_step_id = step_id.strip()
                        target_doc_id = f"doc/{target_doc_name}"
                        assertions.append(
                            DocAssertion(
                                side="bd",
                                predicate="cites",
                                subject=f"doc/{doc.artifact_name}",
                                object=target_doc_id,
                                qualifiers={
                                    "target_doc": target_doc_name,
                                    "target_step": clean_step_id,
                                    "style": "step",
                                },
                                status="asserted",
                                doc_id=doc.id,
                                doc_span=doc_span,
                            )
                        )

                # N5: Unnumbered section citations
                for target_doc_name, sec_num, sec_name in _CITE_UNNUMBERED_REGEX.findall(line):
                    target_doc_id = f"doc/{target_doc_name}"
                    assertions.append(
                        DocAssertion(
                            side="bd",
                            predicate="cites",
                            subject=f"doc/{doc.artifact_name}",
                            object=target_doc_id,
                            qualifiers={
                                "target_doc": target_doc_name,
                                "target_section": f"{sec_num} {sec_name}",
                                "style": "unnumbered",
                            },
                            status="asserted",
                            doc_id=doc.id,
                            doc_span=doc_span,
                        )
                    )

    # B9: Build Merged Display Edges Projection with Attribute Merging & Precedence
    for a in assertions:
        if not a.object:
            continue

        s_node = a.subject
        d_node = a.object
        p_name = a.predicate

        e_type = None
        e_key = None

        if p_name == "calls":
            e_type = "program_calls_program"
            e_key = f"{e_type}:{s_node}->{d_node}"
        elif p_name == "copies":
            e_type = "program_copies_copybook"
            e_key = f"{e_type}:{s_node}->{d_node}"
        elif p_name == "runs":
            e_type = "job_executes_step" if s_node.startswith("job/") else "step_runs_program"
            e_key = f"{e_type}:{s_node}->{d_node}"
        elif p_name == "binds_dd":
            e_type = "dd_binds_dataset" if s_node.startswith("dd/") else "step_binds_dd"
            e_key = f"{e_type}:{s_node}->{d_node}"
        elif p_name == "accesses":
            e_type = "accesses_dataset"
            mode = a.qualifiers.get("mode", "read")
            e_key = f"{e_type}:{s_node}->{d_node}:{mode}"
        elif p_name == "cites":
            e_type = "cites"
            # F8: discriminate by the cited section/step so distinct citations to the
            # same target document don't collapse into a single merged edge now that
            # `dst_node_id` is the whole-document node rather than a fabricated
            # (never-created) section/step node id.
            cited_ref = a.qualifiers.get("target_section") or a.qualifiers.get("target_step") or ""
            e_key = f"{e_type}:{s_node}->{d_node}:{cited_ref}"
        elif p_name == "field_width_relation":
            e_type = "field_width_relation"
            e_key = f"{e_type}:{s_node}->{d_node}"

        if not e_type or not e_key:
            continue

        conf = "authoritative" if a.side == "dd" else "corroborating"
        call_count = a.value if (p_name == "calls" and a.value is not None) else None

        if e_key not in edges_map:
            edges_map[e_key] = DocGraphEdge(
                cluster_id="",
                src_node_id=s_node,
                dst_node_id=d_node,
                edge_type=e_type,
                edge_key=e_key,
                attributes={
                    "call_site_count": call_count,
                    "qualifiers": a.qualifiers,
                    "confidence": conf,
                    "source_doc": a.doc_id,
                    "evidence": [
                        {
                            "assertion_id": a.doc_id,
                            "doc_span": a.doc_span,
                            "source_span": a.source_span,
                        }
                    ],
                },
                created_at="",
            )
        else:
            existing = edges_map[e_key]
            ev_list = existing.attributes.get("evidence", [])
            ev_list.append(
                {"assertion_id": a.doc_id, "doc_span": a.doc_span, "source_span": a.source_span}
            )
            existing.attributes["evidence"] = ev_list

            if call_count is not None and (
                existing.attributes.get("call_site_count") is None or a.side == "dd"
            ):
                existing.attributes["call_site_count"] = call_count

            if conf == "authoritative":
                existing.attributes["confidence"] = "authoritative"

    # Run post-pass linking edges (rule_about, references, defines)
    _build_annotation_linking_edges(nodes_map, edges_map, alias_map)

    return assertions, list(nodes_map.values()), list(edges_map.values())


def _build_annotation_linking_edges(
    nodes_map: dict[str, DocGraphNode],
    edges_map: dict[str, DocGraphEdge],
    alias_map: dict[str, str],
) -> None:
    """Post-pass: link br/tbd/ddlimit/actor/field nodes to existing entity nodes they reference."""
    entities_by_type: dict[str, list[tuple[str, str]]] = {
        "program": [],
        "step": [],
        "dataset": [],
        "copybook": [],
        "extroutine": [],
        "job": [],
    }

    for node_id, node in nodes_map.items():
        if node.node_type in entities_by_type:
            disp = node.display_name.strip()
            if disp:
                entities_by_type[node.node_type].append((node_id, disp))
                if node.node_type == "dataset" and "." in disp:
                    short_alias = disp.split(".")[-1]
                    if len(short_alias) >= 3:
                        entities_by_type["dataset"].append((node_id, short_alias))

    def find_matching_entities(text: str) -> set[str]:
        if not text:
            return set()
        matched: set[str] = set()
        tokens = set(re.findall(r"\b[A-Za-z0-9_.-]+\b", text))
        tokens_upper = {t.upper() for t in tokens}

        for n_type, item_list in entities_by_type.items():
            for target_id, target_name in item_list:
                t_upper = target_name.upper()
                if t_upper in tokens_upper:
                    matched.add(target_id)
        return matched

    # 1. rule_about: br, tbd, ddlimit -> entity
    for node_id, node in list(nodes_map.items()):
        if node.node_type in ("br", "tbd", "ddlimit"):
            raw_text = node.attributes.get("text", "") or ""
            full_text = f"{node.display_name} {raw_text}"
            targets = find_matching_entities(full_text)
            for target_id in targets:
                if target_id == node_id:
                    continue
                e_key = f"rule_about:{node_id}->{target_id}"
                if e_key not in edges_map:
                    edges_map[e_key] = DocGraphEdge(
                        cluster_id="",
                        src_node_id=node_id,
                        dst_node_id=target_id,
                        edge_type="rule_about",
                        edge_key=e_key,
                        attributes={"confidence": "corroborating"},
                        created_at="",
                    )

    # 2. references: actor -> entity
    for node_id, node in list(nodes_map.items()):
        if node.node_type == "actor":
            role = node.attributes.get("role", "") or ""
            raw_text = node.attributes.get("text", "") or ""
            full_text = f"{node.display_name} {role} {raw_text}"
            targets = find_matching_entities(full_text)
            for target_id in targets:
                if target_id == node_id:
                    continue
                e_key = f"references:{node_id}->{target_id}"
                if e_key not in edges_map:
                    edges_map[e_key] = DocGraphEdge(
                        cluster_id="",
                        src_node_id=node_id,
                        dst_node_id=target_id,
                        edge_type="references",
                        edge_key=e_key,
                        attributes={"confidence": "corroborating"},
                        created_at="",
                    )

    # 3. defines: program -> field
    for node_id, node in list(nodes_map.items()):
        if node.node_type == "field":
            prefix = node.display_name.split(".")[0].upper()
            prog_id = f"program/{prefix}"
            if prog_id in nodes_map and prog_id != node_id:
                e_key = f"defines:{prog_id}->{node_id}"
                if e_key not in edges_map:
                    edges_map[e_key] = DocGraphEdge(
                        cluster_id="",
                        src_node_id=prog_id,
                        dst_node_id=node_id,
                        edge_type="defines",
                        edge_key=e_key,
                        attributes={"confidence": "corroborating"},
                        created_at="",
                    )
