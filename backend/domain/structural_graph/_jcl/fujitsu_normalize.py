"""Dataset-fitted normalizer for Fujitsu FACOM/XSP JCL dialect.

Fits exactly the 4 known customer JCL files (`\\`-prefixed cards using
JOBG/JOB/EX/FD/PARA/STACK/SYSIN/MSG/CHAM/SAMMCHK/SW/JEND/JGEND/FIN). It is not
a general Fujitsu-dialect parser.

Mechanically rewrites the `\\`-prefixed control cards into IBM-JCL-shaped
`//` statements so the existing embedded ANTLR IBM-JCL grammar (and the
legacy regex extractor) can walk them unmodified, while preserving 1:1
physical line numbering (each Fujitsu statement's rendering replaces only its
own first physical line; continuation/instream lines keep their original
line index). Fujitsu semantics that have no IBM equivalent (COND polarity,
JOBG/PARA/STACK chaining, SYSIN member references) are dropped from the
normalized text and captured instead as sidecar facts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, NamedTuple
import re

# Fujitsu control-statement keywords this dataset actually uses. Anything
# else falls back to the opaque_control/diagnostic path below.
_KNOWN_OPS = {
    "JOB",
    "JOBG",
    "EX",
    "FD",
    "PARA",
    "STACK",
    "SYSIN",
    "MSG",
    "CHAM",
    "SAMMCHK",
    "SW",
    "JEND",
    "JGEND",
    "FIN",
}

# Ops that just mark job-level scope boundaries or run auxiliary utilities;
# they carry no structural meaning for the graph beyond being recorded.
_SCOPE_OPS = {"JEND", "JGEND", "FIN"}

_EXTRACTOR = "jcl_fujitsu_normalizer"
_EXTRACTOR_VER = "1.0.0"

_DD_PLACEHOLDER = "//*"


class FujitsuNormalizeResult(NamedTuple):
    normalized_text: str
    sidecar_facts: list[dict[str, Any]]
    diagnostics: list[str]


def is_fujitsu_jcl(text: str) -> bool:
    """Fujitsu FACOM/XSP cards are `\\`-prefixed; IBM JCL statements start with `//`."""
    for line in text.splitlines():
        if line.strip():
            return line.startswith("\\")
    return False


@dataclass
class _Statement:
    kind: str  # "control" | "comment" | "paragraph" | "data" | "null"
    label: str | None
    op: str | None
    operand: str
    line: int
    continuation_mark: bool = False
    data_lines: list[tuple[int, str]] = field(default_factory=list)


def _split_top_level_commas(text: str) -> list[str]:
    """Split on commas at paren-depth 0 (keeps `(a,b)` groups intact)."""
    parts: list[str] = []
    depth = 0
    buf = ""
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    return parts


def _split_operands(text: str) -> list[str]:
    return [p.strip() for p in _split_top_level_commas(text) if p.strip()]


def _operand_map(text: str) -> tuple[list[str], dict[str, str]]:
    """Split a Fujitsu operand list into (positional values, KEY=value map)."""
    positional: list[str] = []
    keywords: dict[str, str] = {}
    for item in _split_operands(text):
        if "=" in item:
            key, value = item.split("=", 1)
            keywords[key.strip().upper()] = value.strip()
        else:
            positional.append(item)
    return positional, keywords


def _parse_control_line(raw: str, line_no: int) -> _Statement:
    """Parse one `\\`-prefixed physical line into a _Statement."""
    body = raw[1:]
    if body.startswith("*"):
        return _Statement("comment", None, None, body[1:], line_no)

    stripped = body.lstrip()
    if stripped.startswith("/"):
        return _Statement("paragraph", None, None, stripped[1:].strip(), line_no)

    # Column 72 is XSP's continuation/comment marker; columns 73-80 are ignored.
    continuation_mark = len(raw) >= 72 and not raw[71].isspace()
    statement_area = raw[1:71] if continuation_mark else raw[1:72]
    fields = list(re.finditer(r"\S+", statement_area))
    if not fields:
        return _Statement("null", None, None, "", line_no)

    first = fields[0].group(0).upper()
    if first in _KNOWN_OPS:
        label, op, op_match = None, first, fields[0]
    elif len(fields) > 1 and fields[1].group(0).upper() in _KNOWN_OPS:
        label, op, op_match = fields[0].group(0).upper(), fields[1].group(0).upper(), fields[1]
    else:
        # Unrecognized op keyword -- keep the raw first token so callers can
        # still surface something in the opaque_control fact / diagnostic.
        label, op, op_match = None, first, fields[0]

    operand = statement_area[op_match.end():].strip()
    return _Statement("control", label, op, operand, line_no, continuation_mark=continuation_mark)


def _starts_instream(stmt: _Statement) -> bool:
    """True when this FD/SYSIN statement's device type is `*` (inline data follows)."""
    if stmt.kind != "control" or stmt.op not in {"FD", "SYSIN"}:
        return False
    items = _split_operands(stmt.operand)
    if not items or "=" not in items[0]:
        return False
    return items[0].split("=", 1)[1].strip() == "*"


def _parse_statements(text: str) -> list[_Statement]:
    """Join Fujitsu continuations (trailing comma or column-72 marker) and
    capture instream data verbatim, without altering physical line numbers."""
    statements: list[_Statement] = []
    active_instream: _Statement | None = None
    previous: _Statement | None = None

    for line_no, raw in enumerate(text.splitlines(), 1):
        if raw.startswith("\\"):
            active_instream = None
            stmt = _parse_control_line(raw, line_no)
            statements.append(stmt)
            previous = stmt
            if _starts_instream(stmt):
                active_instream = stmt
            continue

        if active_instream is not None:
            active_instream.data_lines.append((line_no, raw))
            continue

        if previous is not None and previous.kind == "control":
            operand_continues = previous.operand.rstrip().endswith(",") or previous.continuation_mark
            if operand_continues:
                continued = raw[:71].strip()
                previous.operand = f"{previous.operand.rstrip(',').rstrip()},{continued.lstrip(',')}"
                previous.continuation_mark = len(raw) >= 72 and not raw[71].isspace()
                continue

        stmt = _Statement("data", None, None, raw, line_no)
        statements.append(stmt)
        previous = stmt

    return statements


def _transform_fd_params(remainder: str, diagnostics: list[str], rel_hint: str) -> str:
    """Map known Fujitsu FD sub-parameters onto IBM DD keywords.

    Unrecognized keywords are passed through unchanged (the IBM grammar's
    `errorChars` fallback tolerates stray tokens) and logged as a diagnostic
    instead of silently dropped.
    """
    out: list[str] = []
    for p in _split_operands(remainder):
        if "=" not in p:
            out.append(p)
            continue
        key, value = p.split("=", 1)
        up = key.strip().upper()
        if up == "FILE":
            out.append("DSN=" + value)
        elif up == "VOL":
            out.append("VOL=SER=" + value)
        elif up in ("CYL", "TRK"):
            # 'CYL=(1,1,RLSE)' -> 'SPACE=(CYL,1,1,RLSE)' (flattened; the IBM
            # grammar accepts a flat paren-list, nesting isn't required).
            inner = value[1:-1] if value.startswith("(") and value.endswith(")") else value
            out.append(f"SPACE=({up},{inner})")
        elif up == "DISP":
            out.append("DISP=" + value)
        elif up == "SOUT":
            out.append("SYSOUT=" + value)
        elif up == "AMP":
            out.append("AMP=" + value)
        elif up == "FCB" and value.startswith("("):
            # Fujitsu overloads FCB(...) to bundle RECFM/LRECL/BLKSIZE; IBM's
            # FCB is a forms-buffer id, not a container -- unwrap the inner
            # attrs as bare top-level DD keywords instead (best-effort).
            out.append(value[1:-1])
        else:
            out.append(p)
            diagnostics.append(f"{rel_hint}: unrecognized FD operand '{p}' passed through as-is")
    return ",".join(out)


def _split_ddname_devtype(rest: str) -> tuple[str, str, str]:
    """'KGFLIB=DA,FILE=LXP.KGFLIB01' -> ('KGFLIB', 'DA', 'FILE=LXP.KGFLIB01')"""
    m = re.match(r"^\s*([A-Za-z0-9#$@]+)\s*=\s*(\S+?)(,(.*))?$", rest)
    if not m:
        return "", "", rest
    ddname, devtype, _, remainder = m.groups()
    return ddname, devtype, (remainder or "")


class _FactCollector:
    """Builds source_facts-shaped dicts, tracking occurrence_ix per semantic_key."""

    def __init__(self) -> None:
        self.facts: list[dict[str, Any]] = []
        self._occurrence_counts: dict[str, int] = {}

    def add(
        self,
        fact_type: str,
        semantic_key: str,
        parent_key: str | None,
        name: str | None,
        value: str | None,
        attributes: dict[str, Any],
        line: int,
    ) -> None:
        occ = self._occurrence_counts.get(semantic_key, 0)
        self._occurrence_counts[semantic_key] = occ + 1
        self.facts.append(
            {
                "fact_type": fact_type,
                "semantic_key": semantic_key,
                "occurrence_ix": occ,
                "parent_key": parent_key,
                "name": name,
                "value": value,
                "attributes": attributes,
                "line_start": line,
                "line_end": line,
                "extractor": _EXTRACTOR,
                "extractor_ver": _EXTRACTOR_VER,
            }
        )


def normalize(text: str) -> FujitsuNormalizeResult:
    lines = text.splitlines()
    physical_count = len(lines)
    statements = _parse_statements(text)

    job_stmt = next((s for s in statements if s.kind == "control" and s.op == "JOB"), None)
    if job_stmt is not None:
        job_positional, _ = _operand_map(job_stmt.operand)
        job_name = job_positional[0].upper() if job_positional else "FUJITSU"
    else:
        job_name = "FUJITSU"
    job_key = f"job/{job_name}"

    output = [_DD_PLACEHOLDER] * physical_count
    collector = _FactCollector()
    diagnostics: list[str] = []

    current_step_key: str = job_key
    step_ordinal = 0

    def set_line(line_no: int, text_line: str) -> None:
        output[line_no - 1] = text_line

    for stmt in statements:
        for data_line, data in stmt.data_lines:
            output[data_line - 1] = data

        if stmt.kind == "comment":
            set_line(stmt.line, "//*")
            continue
        if stmt.kind == "paragraph":
            set_line(stmt.line, "/*")
            continue
        if stmt.kind == "null":
            set_line(stmt.line, "//*")
            continue
        if stmt.kind == "data":
            # Stray non-instream, non-continuation line -- nothing was
            # dropped silently: record it and comment it out.
            set_line(stmt.line, "//*")
            collector.add(
                "opaque_control",
                f"{current_step_key}/opaque/UNCLASSIFIED_DATA/{stmt.line}",
                current_step_key,
                "UNCLASSIFIED_DATA",
                stmt.operand,
                {},
                stmt.line,
            )
            diagnostics.append(f"line {stmt.line}: unclassified data line normalized to comment")
            continue

        # stmt.kind == "control"
        op = stmt.op
        positional, keywords = _operand_map(stmt.operand)

        if op == "JOB":
            set_line(stmt.line, f"//{job_name} JOB")
            continue

        if op == "JOBG":
            set_line(stmt.line, "//*")
            collector.add("job_group", job_key, None, "JOBG", stmt.operand, {}, stmt.line)
            continue

        if op == "EX":
            step_ordinal += 1
            program = positional[0].upper() if positional else ""
            ex_star = program == "*"
            step_name = stmt.label if stmt.label else f"FJ{step_ordinal:06d}"
            step_key = f"step/{job_name}.{step_name}"
            pgm = "FJEXSTAR" if ex_star else (program or "FJUNKNOWN")
            set_line(stmt.line, f"//{step_name} EXEC PGM={pgm}")
            current_step_key = step_key

            if ex_star:
                collector.add(
                    "opaque_control",
                    f"{step_key}/opaque/EX_STAR",
                    step_key,
                    "EX_STAR",
                    None,
                    {"ex_star": True},
                    stmt.line,
                )

            if "COND" in keywords:
                raw_cond = keywords["COND"]
                attrs: dict[str, Any] = {"raw": raw_cond}
                try:
                    n = int(raw_cond)
                except ValueError:
                    n = None
                if n is not None:
                    # Fujitsu positive COND=n means EXECUTE when previous step's
                    # RC <= n (opposite polarity from IBM's skip-if-true COND).
                    attrs["execute_when"] = f"RC<={n}"
                collector.add("cond_gate", step_key, step_key, "COND", raw_cond, attrs, stmt.line)

            if "RSIZE" in keywords:
                collector.add(
                    "opaque_control",
                    f"{step_key}/opaque/RSIZE",
                    step_key,
                    "RSIZE",
                    keywords["RSIZE"],
                    {},
                    stmt.line,
                )
            continue

        if op == "FD":
            ddname, devtype, remainder = _split_ddname_devtype(stmt.operand)
            if not ddname:
                set_line(stmt.line, "//*")
                collector.add(
                    "opaque_control",
                    f"{current_step_key}/opaque/FD_UNPARSED/{stmt.line}",
                    current_step_key,
                    "FD_UNPARSED",
                    stmt.operand,
                    {},
                    stmt.line,
                )
                diagnostics.append(f"line {stmt.line}: FD statement did not match ddname=devtype shape")
                continue

            devtype_u = devtype.upper().rstrip(",")
            is_concat = ddname.upper() == "CF"
            rendered_name = "" if is_concat else ddname

            if devtype_u == "DUMMY":
                extra = _transform_fd_params(remainder, diagnostics, f"line {stmt.line}")
                rendered = f"//{rendered_name} DD DUMMY" + (f",{extra}" if extra else "")
            elif devtype_u == "*":
                rendered = f"//{rendered_name} DD *"
            elif devtype_u == "/":
                # 'I01=/,SW=U02' -- Fujitsu SW-file chaining, no IBM DD
                # equivalent; approximate as DUMMY so the DD at least parses.
                rendered = f"//{rendered_name} DD DUMMY"
                collector.add(
                    "opaque_control",
                    f"{current_step_key}/opaque/SW_CHAIN/{ddname}",
                    current_step_key,
                    "SW",
                    remainder,
                    {},
                    stmt.line,
                )
            else:
                extra = _transform_fd_params(remainder, diagnostics, f"line {stmt.line}")
                rendered = f"//{rendered_name} DD " + (extra if extra else "DUMMY")
            set_line(stmt.line, rendered)
            continue

        if op == "SYSIN":
            file_name = keywords.get("FILE")
            member = keywords.get("MEMBER")
            if file_name and member:
                set_line(stmt.line, f"//SYSIN DD DSN={file_name}({member}),DISP=SHR")
                collector.add(
                    "jcl_member_ref",
                    f"{current_step_key}/member_ref",
                    current_step_key,
                    "SYSIN",
                    f"{file_name}({member})",
                    {"file": file_name, "member": member, "unit": keywords.get("UNIT")},
                    stmt.line,
                )
            else:
                set_line(stmt.line, "//*")
                collector.add(
                    "opaque_control",
                    f"{current_step_key}/opaque/SYSIN/{stmt.line}",
                    current_step_key,
                    "SYSIN",
                    stmt.operand,
                    {},
                    stmt.line,
                )
                diagnostics.append(f"line {stmt.line}: SYSIN statement missing FILE/MEMBER, dropped to comment")
            continue

        if op == "PARA":
            set_line(stmt.line, "//*")
            collector.add("para", current_step_key, current_step_key, "PARA", stmt.operand, {}, stmt.line)
            continue

        if op == "STACK":
            set_line(stmt.line, "//*")
            collector.add(
                "stack",
                f"{current_step_key}/stack",
                current_step_key,
                "STACK",
                stmt.operand,
                {"file": keywords.get("FILE"), "member": keywords.get("MEMBER")},
                stmt.line,
            )
            continue

        if op in _SCOPE_OPS:
            set_line(stmt.line, "//*")
            collector.add(
                "opaque_control",
                f"{job_key}/opaque/{op}",
                job_key,
                op,
                stmt.operand or None,
                {},
                stmt.line,
            )
            current_step_key = job_key
            continue

        if op in {"MSG", "CHAM", "SAMMCHK", "SW"}:
            set_line(stmt.line, "//*")
            collector.add(
                "opaque_control",
                f"{current_step_key}/opaque/{op}/{stmt.line}",
                current_step_key,
                op,
                stmt.operand or None,
                {},
                stmt.line,
            )
            continue

        # Unrecognized op keyword entirely -- nothing dropped silently.
        set_line(stmt.line, "//*")
        collector.add(
            "opaque_control",
            f"{current_step_key}/opaque/UNKNOWN_{op}/{stmt.line}",
            current_step_key,
            op or "UNKNOWN",
            stmt.operand,
            {},
            stmt.line,
        )
        diagnostics.append(f"line {stmt.line}: unrecognized Fujitsu op '{op}' normalized to comment")

    normalized_text = "\n".join(output) + ("\n" if text.endswith("\n") or text else "")
    return FujitsuNormalizeResult(
        normalized_text=normalized_text,
        sidecar_facts=collector.facts,
        diagnostics=diagnostics,
    )
