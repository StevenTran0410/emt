"""Mismatch detection engine for BD/DD assertion comparison."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ._markdown_parser import _slugify
from .types import DocAssertion, DocGraphMismatch, ParsedDoc


def _compute_fingerprint(
    mismatch_type: str, subject: str, bd_loc: dict[str, Any] | None, dd_loc: dict[str, Any] | None
) -> str:
    bd_str = json.dumps(bd_loc or {}, sort_keys=True)
    dd_str = json.dumps(dd_loc or {}, sort_keys=True)
    raw = f"{mismatch_type}:{subject}:{bd_str}:{dd_str}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def detect_mismatches(
    assertions: list[DocAssertion],
    parsed_docs: list[ParsedDoc],
) -> list[DocGraphMismatch]:
    """Compare BD assertions against DD assertions to detect business correctness mismatches."""
    mismatches: list[DocGraphMismatch] = []
    seen_fingerprints: set[str] = set()

    bd_assertions = [a for a in assertions if a.side == "bd"]
    dd_assertions = [a for a in assertions if a.side == "dd"]

    # Build DD entity index
    dd_subjects = {a.subject for a in dd_assertions}
    dd_objects = {a.object for a in dd_assertions if a.object}
    dd_entities = dd_subjects | dd_objects

    doc_map: dict[str, ParsedDoc] = {d.artifact_name: d for d in parsed_docs}

    def _dd_definitive_mode(d: DocAssertion) -> tuple[str | None, bool]:
        """B2: derive DD I/O mode and whether it is definitive (vs ambiguous DISP=SHR/OLD).

        Definitive = DISP contains NEW/MOD (write), or the DDNAME role is an
        unambiguous output (SORTOUT/OUTFILE/OUT*) or input (SORTIN/INFILE/IN*/STEPLIB).
        DISP=SHR/OLD alone is shared access — it does NOT determine I/O direction.
        """
        disp = str(d.qualifiers.get("disp", "") or "").upper()
        if "NEW" in disp or "MOD" in disp:
            return "write", True

        ddname = d.qualifiers.get("ddname") or (
            d.subject.split(".")[-1] if d.subject.startswith("dd/") else ""
        )
        ddname_u = str(ddname).upper()
        if ddname_u == "SORTOUT" or ddname_u.startswith("OUT"):
            return "write", True
        if ddname_u == "SORTIN" or ddname_u.startswith("IN") or ddname_u == "STEPLIB":
            return "read", True

        if "WRITE" in disp:
            return "write", True
        if "SHR" in disp or "OLD" in disp or "READ" in disp:
            return "read", False

        return d.qualifiers.get("mode"), False

    # 1. Existence Check (severity: error)
    for a in bd_assertions:
        entities_to_check = [a.subject]
        if a.object:
            entities_to_check.append(a.object)

        for subj in entities_to_check:
            # B7: Suppress if BD assertion status is external, unresolved, or not_derivable
            if a.status in ("external", "unresolved", "not_derivable"):
                continue

            # B7: Existence check ONLY applies to code entities
            if not subj.startswith(
                ("program/", "dataset/", "step/", "job/", "copybook/", "extroutine/", "dd/")
            ):
                continue

            # G: exact-identity existence check. Safe only after A (full-DSN dataset
            # identity) + C (§9.1/utility typing) removed every substring-masked false
            # entity — a fuzzy `subj.split("/")[-1] in e` match could hide a genuinely
            # wrong id (e.g. program/CBSTM999 vs program/CBSTM03) behind a coincidence.
            if subj not in dd_entities:
                bd_loc = {
                    "doc": a.doc_id,
                    "section": a.doc_span.get("section_id"),
                    "line": a.doc_span.get("line_start"),
                }
                fp = _compute_fingerprint("existence", subj, bd_loc, None)
                if fp not in seen_fingerprints:
                    seen_fingerprints.add(fp)
                    mismatches.append(
                        DocGraphMismatch(
                            cluster_id="",
                            fingerprint=fp,
                            mismatch_type="existence",
                            severity="error",
                            bd_location=bd_loc,
                            dd_location=None,
                            description=(
                                f"BD references entity '{subj}' which is absent from DD "
                                "baseline reports and not hedged as external/unresolved"
                            ),
                            evidence=[{"bd_assertion": subj}],
                            confidence="high",
                            created_at="",
                        )
                    )

    # 2. Broken Citation Check (severity: error)
    for a in bd_assertions:
        if a.predicate == "cites":
            target_doc_name = a.qualifiers.get("target_doc", "")
            target_sec = a.qualifiers.get("target_section")
            target_step = a.qualifiers.get("target_step")
            cite_style = a.qualifiers.get("style", "section")

            target_doc = doc_map.get(target_doc_name)
            if not target_doc:
                bd_loc = {
                    "doc": a.doc_id,
                    "section": a.doc_span.get("section_id"),
                    "line": a.doc_span.get("line_start"),
                }
                fp = _compute_fingerprint("broken_citation", target_doc_name, bd_loc, None)
                if fp not in seen_fingerprints:
                    seen_fingerprints.add(fp)
                    mismatches.append(
                        DocGraphMismatch(
                            cluster_id="",
                            fingerprint=fp,
                            mismatch_type="broken_citation",
                            severity="error",
                            bd_location=bd_loc,
                            dd_location=None,
                            description=(
                                f"BD cites report '{target_doc_name}' which does not "
                                "exist in cluster"
                            ),
                            evidence=[{"target_doc": target_doc_name}],
                            confidence="high",
                            created_at="",
                        )
                    )
                continue

            sec_map = target_doc.section_map
            headings = sec_map.headings if sec_map else []
            steps = sec_map.steps if sec_map else []

            found = False
            if cite_style == "section" and target_sec:
                found = any(
                    h.get("section_id") == target_sec or h.get("title", "").startswith(target_sec)
                    for h in headings
                )
            elif cite_style == "step" and target_step:
                step_parts = target_step.split("/")
                found = all(any(s.get("step_id") == p for s in steps) for p in step_parts)
            elif cite_style == "unnumbered" and target_sec:
                # E: target_sec = "<num> <name>" (e.g. "3 Purpose") — resolve against the
                # real section_map instead of hardcoding found=True for every citation.
                # The citation regex captures only a single hyphenated token of the title
                # (e.g. "Control-Flow" for a heading titled "Control-Flow Narrative (COND
                # semantics)"), so match it as a prefix of the heading's own slug, not
                # only an exact match.
                num, _, name = target_sec.partition(" ")
                cited_slug = _slugify(name)
                sec_prefix = f"{num}/"
                found = any(
                    h.get("section_id", "").startswith(sec_prefix)
                    and h.get("section_id", "")[len(sec_prefix) :].startswith(cited_slug)
                    for h in headings
                )
            if not found and (target_sec or target_step):
                bd_loc = {
                    "doc": a.doc_id,
                    "section": a.doc_span.get("section_id"),
                    "line": a.doc_span.get("line_start"),
                }
                dd_loc = {"doc": target_doc.id}
                target_ref = f"§{target_sec}" if target_sec else f"step {target_step}"
                fp = _compute_fingerprint(
                    "broken_citation", f"{target_doc_name}:{target_ref}", bd_loc, dd_loc
                )
                if fp not in seen_fingerprints:
                    seen_fingerprints.add(fp)
                    mismatches.append(
                        DocGraphMismatch(
                            cluster_id="",
                            fingerprint=fp,
                            mismatch_type="broken_citation",
                            severity="error",
                            bd_location=bd_loc,
                            dd_location=dd_loc,
                            description=(
                                f"BD cites {target_ref} in {target_doc_name}, but "
                                "section/step was not found"
                            ),
                            evidence=[{"target_doc": target_doc_name, "target_ref": target_ref}],
                            confidence="high",
                            created_at="",
                        )
                    )

    # Build step -> program mapping from DD runs assertions
    step_programs_map: dict[str, set[str]] = {}
    for d in dd_assertions:
        if d.predicate == "runs" and d.subject.startswith("step/") and d.object:
            step_programs_map.setdefault(d.subject, set()).add(d.object)

    # 3. Relationship Check (R2-1: Bidirectional mode comparison & unverified classification)
    for a in bd_assertions:
        if a.predicate in ("accesses", "calls", "copies", "runs", "binds_dd"):
            bd_mode = a.qualifiers.get("mode")

            # Match DD assertions for the SAME dataset/object and related step/program
            dd_matches = []
            for d in dd_assertions:
                # F: only compare like-with-like predicates — a BD `calls` claim must not
                # be checked against a DD `accesses`/`binds_dd` row that merely shares
                # an object-name token.
                if d.predicate != a.predicate:
                    continue
                if not d.object or not a.object:
                    continue
                a_dsn = a.object.split("/")[-1]
                d_dsn = d.object.split("/")[-1]
                if a_dsn != d_dsn:
                    continue

                # Match direct subject match, or step running the program
                if d.subject == a.subject:
                    dd_matches.append(d)
                elif a.subject.startswith("program/") and d.subject.startswith("step/"):
                    progs_for_step = step_programs_map.get(d.subject, set())
                    if a.subject in progs_for_step or a.subject.split("/")[-1] in d.subject.upper():
                        dd_matches.append(d)
                elif a.subject.startswith("step/") and d.subject.startswith("program/"):
                    progs_for_step = step_programs_map.get(a.subject, set())
                    if d.subject in progs_for_step or d.subject.split("/")[-1] in a.subject.upper():
                        dd_matches.append(d)
                elif (
                    a.subject.startswith("step/")
                    and d.subject.startswith("step/")
                    and a.subject == d.subject
                ):
                    dd_matches.append(d)

            if not dd_matches:
                # F: BD asserts a relationship DD does not reference at all -> unverified.
                # Widened beyond `accesses`/dataset to cover calls/copies/runs/binds_dd too
                # (previously only dataset accesses were recorded; the rest silently
                # vanished instead of surfacing as a low-confidence coverage gap).
                if a.predicate in ("accesses", "calls", "copies", "runs", "binds_dd") and a.object:
                    bd_loc = {
                        "doc": a.doc_id,
                        "section": a.doc_span.get("section_id"),
                        "line": a.doc_span.get("line_start"),
                    }
                    fp = _compute_fingerprint(
                        "unverified", f"{a.subject}->{a.object}", bd_loc, None
                    )
                    if fp not in seen_fingerprints:
                        seen_fingerprints.add(fp)
                        mismatches.append(
                            DocGraphMismatch(
                                cluster_id="",
                                fingerprint=fp,
                                mismatch_type="unverified",
                                severity="info",
                                bd_location=bd_loc,
                                dd_location=None,
                                description=(
                                    f"BD asserts {a.predicate} '{a.subject}' -> "
                                    f"'{a.object}' which has no corresponding DD "
                                    "baseline statement"
                                ),
                                evidence=[{"bd_assertion": a.subject, "bd_mode": bd_mode}],
                                confidence="low",
                                created_at="",
                            )
                        )
                continue

            for d in dd_matches:
                dd_mode, is_definitive = _dd_definitive_mode(d)
                disp = d.qualifiers.get("disp", "")

                # Contradiction: BD write vs DD read or BD read vs DD write
                if (
                    bd_mode
                    and dd_mode
                    and (
                        (bd_mode == "write" and dd_mode == "read")
                        or (bd_mode == "read" and dd_mode == "write")
                    )
                ):
                    bd_loc = {
                        "doc": a.doc_id,
                        "section": a.doc_span.get("section_id"),
                        "line": a.doc_span.get("line_start"),
                    }
                    dd_loc = {
                        "doc": d.doc_id,
                        "section": d.doc_span.get("section_id"),
                        "line": d.doc_span.get("line_start"),
                    }

                    if is_definitive:
                        fp = _compute_fingerprint(
                            "relationship", f"{a.subject}->{a.object}", bd_loc, dd_loc
                        )
                        if fp not in seen_fingerprints:
                            seen_fingerprints.add(fp)
                            mismatches.append(
                                DocGraphMismatch(
                                    cluster_id="",
                                    fingerprint=fp,
                                    mismatch_type="relationship",
                                    severity="error",
                                    bd_location=bd_loc,
                                    dd_location=dd_loc,
                                    description=(
                                        f"BD claims {bd_mode} access for '{a.subject}' "
                                        f"-> '{a.object}', but DD establishes {dd_mode} mode"
                                    ),
                                    evidence=[
                                        {"bd_mode": bd_mode, "dd_mode": dd_mode, "disp": disp}
                                    ],
                                    confidence="high",
                                    created_at="",
                                )
                            )
                    else:
                        # B2: dd_mode was derived only from an ambiguous DISP=SHR/OLD —
                        # shared access does not establish I/O direction, so this is not
                        # a sound contradiction; downgrade to unverified/info.
                        fp = _compute_fingerprint(
                            "unverified", f"{a.subject}->{a.object}:mode", bd_loc, dd_loc
                        )
                        if fp not in seen_fingerprints:
                            seen_fingerprints.add(fp)
                            mismatches.append(
                                DocGraphMismatch(
                                    cluster_id="",
                                    fingerprint=fp,
                                    mismatch_type="unverified",
                                    severity="info",
                                    bd_location=bd_loc,
                                    dd_location=dd_loc,
                                    description=(
                                        f"BD claims {bd_mode} access for '{a.subject}' "
                                        f"-> '{a.object}'; DD DISP is ambiguous ({disp}) "
                                        f"and does not definitively establish {dd_mode} mode"
                                    ),
                                    evidence=[
                                        {"bd_mode": bd_mode, "dd_mode": dd_mode, "disp": disp}
                                    ],
                                    confidence="low",
                                    created_at="",
                                )
                            )

    # 4. Value Check (severity: warning)
    for a in bd_assertions:
        if a.predicate == "calls" and a.value is not None:
            val_bd = str(a.value).strip()
            if val_bd and val_bd != "0":
                dd_calls = [
                    d
                    for d in dd_assertions
                    if d.predicate == "calls" and d.subject == a.subject and d.object == a.object
                ]
                for d in dd_calls:
                    val_dd = str(d.value).strip() if d.value is not None else ""
                    if val_dd and val_dd != "0" and val_bd != val_dd:
                        bd_loc = {
                            "doc": a.doc_id,
                            "section": a.doc_span.get("section_id"),
                            "line": a.doc_span.get("line_start"),
                        }
                        dd_loc = {
                            "doc": d.doc_id,
                            "section": d.doc_span.get("section_id"),
                            "line": d.doc_span.get("line_start"),
                        }
                        fp = _compute_fingerprint(
                            "value", f"{a.subject}->{a.object}:call_site_count", bd_loc, dd_loc
                        )
                        if fp not in seen_fingerprints:
                            seen_fingerprints.add(fp)
                            mismatches.append(
                                DocGraphMismatch(
                                    cluster_id="",
                                    fingerprint=fp,
                                    mismatch_type="value",
                                    severity="warning",
                                    bd_location=bd_loc,
                                    dd_location=dd_loc,
                                    description=(
                                        f"BD asserts call site count {val_bd} for "
                                        f"'{a.subject}' -> '{a.object}', but DD "
                                        f"establishes {val_dd}"
                                    ),
                                    evidence=[{"bd_val": val_bd, "dd_val": val_dd}],
                                    confidence="high",
                                    created_at="",
                                )
                            )

    # 4b. Value checks for field PIC, field-width relation, and DD DCB. Each compares a
    # BD claim against the DD claim about the SAME subject; agreement (incl. jointly
    # documenting a real source defect) is NOT a mismatch — only disagreement is.
    def _emit_value(
        a: DocAssertion, d: DocAssertion, key: str, vb: str, vd: str, desc: str
    ) -> None:
        bd_loc = {
            "doc": a.doc_id,
            "section": a.doc_span.get("section_id"),
            "line": a.doc_span.get("line_start"),
        }
        dd_loc = {
            "doc": d.doc_id,
            "section": d.doc_span.get("section_id"),
            "line": d.doc_span.get("line_start"),
        }
        fp = _compute_fingerprint("value", key, bd_loc, dd_loc)
        if fp in seen_fingerprints:
            return
        seen_fingerprints.add(fp)
        mismatches.append(
            DocGraphMismatch(
                cluster_id="",
                fingerprint=fp,
                mismatch_type="value",
                severity="warning",
                bd_location=bd_loc,
                dd_location=dd_loc,
                description=desc,
                evidence=[{"bd_val": vb, "dd_val": vd}],
                confidence="high",
                created_at="",
            )
        )

    dd_field_pic = {d.subject: d for d in dd_assertions if d.predicate == "field_pic" and d.value}
    for a in bd_assertions:
        if a.predicate == "field_pic" and a.value and a.subject in dd_field_pic:
            d = dd_field_pic[a.subject]
            vb, vd = str(a.value).strip(), str(d.value).strip()
            if vb and vd and vb != vd:
                _emit_value(
                    a,
                    d,
                    f"{a.subject}:field_pic",
                    vb,
                    vd,
                    f"BD states PIC {vb} for '{a.subject}', but DD establishes {vd}",
                )

    dd_fwr = {
        (d.subject, d.object): d for d in dd_assertions if d.predicate == "field_width_relation"
    }
    for a in bd_assertions:
        if a.predicate == "field_width_relation" and (a.subject, a.object) in dd_fwr:
            d = dd_fwr[(a.subject, a.object)]
            rb = str(a.qualifiers.get("relation", "")).strip()
            rd = str(d.qualifiers.get("relation", "")).strip()
            if rb and rd and rb != rd:
                _emit_value(
                    a,
                    d,
                    f"{a.subject}->{a.object}:field_width",
                    rb,
                    rd,
                    f"BD states width relation '{rb}' for '{a.subject}'->'{a.object}', "
                    f"but DD establishes '{rd}'",
                )

    dd_dcb = {
        d.subject: d for d in dd_assertions if d.predicate == "binds_dd" and d.qualifiers.get("dcb")
    }
    for a in bd_assertions:
        if a.predicate == "binds_dd" and a.qualifiers.get("dcb") and a.subject in dd_dcb:
            d = dd_dcb[a.subject]
            db = json.dumps(a.qualifiers.get("dcb"), sort_keys=True)
            dd = json.dumps(d.qualifiers.get("dcb"), sort_keys=True)
            if db != dd:
                _emit_value(
                    a,
                    d,
                    f"{a.subject}:dcb",
                    db,
                    dd,
                    f"BD states DCB {db} for '{a.subject}', but DD establishes {dd}",
                )

    return mismatches
