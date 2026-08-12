"""ISPF/TSO adapter: .pfd menu panels, .clist TSO CLIST procedures, .ipf IPF
screen panels. Regex/line-based (no ANTLR) - dataset-fitted to the
Source_HSBMENU5 dialect (mirrors the shape of _jcl/extract.py and
_cobol/extract.py, but simple enough to need only one extraction pass)."""
from __future__ import annotations

import re
from typing import Any, NamedTuple

# ──── .pfd (ISPF menu panel) ────────────────────────────────────────────────


class PfdMenuOptionFact(NamedTuple):
    option: str
    target: str
    line: int
    disabled: bool


class PfdExtractionResult(NamedTuple):
    menu_options: list[PfdMenuOptionFact]


# )PROC &SEL=TRANS(TRUNC(&OPT,'.') <val>,'CMD(<target>)' ...) dispatch line;
# a leading '/*' marks an option disabled (commented out) without closing it,
# since each dispatch pair already lives on its own line in this dialect.
_PFD_TRANS_RE = re.compile(
    r"^\s*(?P<disabled>/\*\s*)?(?P<opt>[A-Za-z0-9]+),'CMD\((?P<target>[A-Za-z0-9_$#@]+)\)'"
)


def extract_pfd_facts(content: str) -> PfdExtractionResult:
    """Extract )PROC...&SEL TRANS(...) option->CMD(clist) dispatch pairs."""
    menu_options: list[PfdMenuOptionFact] = []
    if not content:
        return PfdExtractionResult(menu_options=[])
    in_proc = False
    for idx, raw in enumerate(content.splitlines(), 1):
        stripped = raw.strip()
        if stripped == ")PROC":
            in_proc = True
            continue
        if stripped == ")END":
            in_proc = False
            continue
        if not in_proc:
            continue
        m = _PFD_TRANS_RE.match(raw)
        if not m:
            continue
        menu_options.append(
            PfdMenuOptionFact(
                option=m.group("opt"),
                target=m.group("target").upper(),
                line=idx,
                disabled=bool(m.group("disabled")),
            )
        )
    return PfdExtractionResult(menu_options=menu_options)


# ──── .clist (TSO CLIST procedure) ──────────────────────────────────────────


class ClistCallFact(NamedTuple):
    member: str
    line: int


class ClistSubmitFact(NamedTuple):
    member: str
    line: int


class ClistAllocateFact(NamedTuple):
    dd: str
    dataset: str | None  # None for DATASET(*) dummy allocation
    line: int


class ClistGuardFact(NamedTuple):
    text: str
    line: int


class ClistExtractionResult(NamedTuple):
    calls: list[ClistCallFact]
    submits: list[ClistSubmitFact]
    allocates: list[ClistAllocateFact]
    guards: list[ClistGuardFact]


_CALL_RE = re.compile(r"^\s*CALL\s+'([^']+)'", re.IGNORECASE)
_SUBMIT_RE = re.compile(r"^\s*SUBMIT\s+'([^']+)'", re.IGNORECASE)
_ALLOCATE_RE = re.compile(r"^\s*ALLOCATE\s+FILE\(([^)]+)\)\s+DATASET\(([^)]*)\)", re.IGNORECASE)
_IF_RE = re.compile(r"^\s*IF\s+\S", re.IGNORECASE)


def _member(lib_ref: str) -> str:
    """Strip the mainframe library/dataset prefix: CC0.ELIB(HNIXLOT) -> HNIXLOT."""
    m = re.search(r"\(([^)]+)\)", lib_ref)
    return (m.group(1) if m else lib_ref).upper()


def extract_clist_facts(content: str) -> ClistExtractionResult:
    """Extract CALL/SUBMIT/ALLOCATE/IF-guard statements from a TSO CLIST.

    Skips whole-line comments (line starts with '/*') rather than tracking
    nested block-comment state: the real source has a file whose header '/*'
    is never closed with a matching '*/', yet its interior SUBMIT is a live
    statement. This mirrors the JCL extractor's per-line '//* -> skip'
    heuristic (see _jcl/extract.py) instead of a stateful comment parser.
    """
    calls: list[ClistCallFact] = []
    submits: list[ClistSubmitFact] = []
    allocates: list[ClistAllocateFact] = []
    guards: list[ClistGuardFact] = []
    if not content:
        return ClistExtractionResult(calls, submits, allocates, guards)

    for idx, raw in enumerate(content.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("/*"):
            continue
        m = _CALL_RE.match(raw)
        if m:
            calls.append(ClistCallFact(member=_member(m.group(1)), line=idx))
            continue
        m = _SUBMIT_RE.match(raw)
        if m:
            submits.append(ClistSubmitFact(member=_member(m.group(1)), line=idx))
            continue
        m = _ALLOCATE_RE.match(raw)
        if m:
            dd = m.group(1).upper()
            ds_raw = m.group(2).strip()
            dataset = None if ds_raw == "*" else ds_raw.strip("'").upper()
            allocates.append(ClistAllocateFact(dd=dd, dataset=dataset, line=idx))
            continue
        if _IF_RE.match(raw):
            guards.append(ClistGuardFact(text=stripped, line=idx))

    return ClistExtractionResult(calls, submits, allocates, guards)


# ──── .ipf (IPF screen panel) ───────────────────────────────────────────────


class IpfFieldFact(NamedTuple):
    ordinal: int
    name: str
    field_type: str
    length: int
    parm: int
    cursor: bool
    line: int


class IpfExtractionResult(NamedTuple):
    fields: list[IpfFieldFact]


# <ACTION> field row: "<ord> <name> <TYPE>(<len>) INIT(\PARM<n>) [CURSOR] ;"
_IPF_FIELD_RE = re.compile(
    r"^\s*(?P<ord>\d+)\s+(?P<name>\S+)\s+(?P<type>[A-Z]+)\((?P<len>\d+)\)\s+"
    r"INIT\(\\PARM(?P<parm>\d+)\)(?P<cursor>\s+CURSOR)?"
)


def extract_ipf_facts(content: str) -> IpfExtractionResult:
    """Extract the <ACTION> section's ordinal field->PARM binding list.

    Panel<->program binding is via the COBOL MDA fields - resolving that is
    out of scope here (see ticket); a field-binding fact is enough so the
    .ipf isn't an orphan in the graph.
    """
    fields: list[IpfFieldFact] = []
    if not content:
        return IpfExtractionResult(fields=[])
    in_action = False
    for idx, raw in enumerate(content.splitlines(), 1):
        stripped = raw.strip()
        if stripped == "<ACTION>":
            in_action = True
            continue
        if stripped in ("<ATTR>", "<END>"):
            in_action = False
            continue
        if not in_action:
            continue
        m = _IPF_FIELD_RE.match(raw)
        if not m:
            continue
        fields.append(
            IpfFieldFact(
                ordinal=int(m.group("ord")),
                name=m.group("name"),
                field_type=m.group("type"),
                length=int(m.group("len")),
                parm=int(m.group("parm")),
                cursor=bool(m.group("cursor")),
                line=idx,
            )
        )
    return IpfExtractionResult(fields=fields)


# ──── source_facts builders (dict shape mirrors *_enrichment_facts in _cobol/_jcl) ──


def _stem(rel_path: str) -> str:
    return rel_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def _diag(rel_path: str, language: str) -> dict[str, Any]:
    return {
        "rel_path": rel_path,
        "language": language,
        "status": "ok",
        "error_count": 0,
        "first_error": None,
        "elapsed_ms": 0,
        "extractor_ver": "1.0.0",
    }


def build_pfd_enrichment_facts(content: str, rel_path: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Panel-level source_facts row for a .pfd menu (menu_option facts live as edges)."""
    if not content or not content.strip():
        return [], _diag(rel_path, "pfd")
    fact = {
        "fact_type": "panel",
        "semantic_key": f"panel/{_stem(rel_path)}",
        "occurrence_ix": 0,
        "parent_key": None,
        "name": _stem(rel_path),
        "value": None,
        "attributes": {"kind": "ispf_menu"},
        "line_start": 1,
        "line_end": len(content.splitlines()),
        "extractor": "pfd_regex",
        "extractor_ver": "1.0.0",
    }
    return [fact], _diag(rel_path, "pfd")


def build_clist_enrichment_facts(content: str, rel_path: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """clist/allocate/guard source_facts rows (calls/submits become edges, not facts)."""
    if not content or not content.strip():
        return [], _diag(rel_path, "clist")
    stem = _stem(rel_path)
    parent_key = f"clist/{stem}"
    res = extract_clist_facts(content)
    facts: list[dict[str, Any]] = [
        {
            "fact_type": "clist",
            "semantic_key": parent_key,
            "occurrence_ix": 0,
            "parent_key": None,
            "name": stem,
            "value": None,
            "attributes": {},
            "line_start": 1,
            "line_end": len(content.splitlines()),
            "extractor": "clist_regex",
            "extractor_ver": "1.0.0",
        }
    ]
    occ: dict[str, int] = {}
    for a in res.allocates:
        key = f"allocate/{a.dd}"
        ix = occ.get(key, 0)
        occ[key] = ix + 1
        facts.append(
            {
                "fact_type": "allocate",
                "semantic_key": key,
                "occurrence_ix": ix,
                "parent_key": parent_key,
                "name": a.dd,
                "value": a.dataset,
                "attributes": {"dsn": a.dataset or "*"},
                "line_start": a.line,
                "line_end": a.line,
                "extractor": "clist_regex",
                "extractor_ver": "1.0.0",
            }
        )
    for g in res.guards:
        facts.append(
            {
                "fact_type": "guard",
                "semantic_key": f"guard/{g.line}",
                "occurrence_ix": 0,
                "parent_key": parent_key,
                "name": "IF",
                "value": g.text,
                "attributes": {},
                "line_start": g.line,
                "line_end": g.line,
                "extractor": "clist_regex",
                "extractor_ver": "1.0.0",
            }
        )
    return facts, _diag(rel_path, "clist")


def build_ipf_enrichment_facts(content: str, rel_path: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """panel + field_binding source_facts rows for an .ipf screen panel."""
    if not content or not content.strip():
        return [], _diag(rel_path, "ipf")
    stem = _stem(rel_path)
    parent_key = f"panel/{stem}"
    res = extract_ipf_facts(content)
    facts: list[dict[str, Any]] = [
        {
            "fact_type": "panel",
            "semantic_key": parent_key,
            "occurrence_ix": 0,
            "parent_key": None,
            "name": stem,
            "value": None,
            "attributes": {"kind": "ipf_screen", "field_count": len(res.fields)},
            "line_start": 1,
            "line_end": len(content.splitlines()),
            "extractor": "ipf_regex",
            "extractor_ver": "1.0.0",
        }
    ]
    for f in res.fields:
        facts.append(
            {
                "fact_type": "field_binding",
                "semantic_key": f"field/{f.ordinal}",
                "occurrence_ix": 0,
                "parent_key": parent_key,
                "name": f.name,
                "value": f"PARM{f.parm}",
                "attributes": {"type": f.field_type, "len": f.length, "cursor": f.cursor},
                "line_start": f.line,
                "line_end": f.line,
                "extractor": "ipf_regex",
                "extractor_ver": "1.0.0",
            }
        )
    return facts, _diag(rel_path, "ipf")
