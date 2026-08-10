"""JCL fact extractor (joining continuation lines and extracting EXEC and DD statements)."""
from __future__ import annotations

import re
from typing import Any, NamedTuple

from antlr4.error.ErrorListener import ErrorListener

from .generated.JCLParserListener import JCLParserListener


class JclExecFact(NamedTuple):
    job_name: str | None
    step_name: str
    program_name: str
    line: int
    evidence_lines: list[int]
    is_proc: bool = False


class JclDdFact(NamedTuple):
    job_name: str | None
    step_name: str
    dd_name: str
    dsn: str
    line: int
    evidence_lines: list[int]


class JclExtractionResult(NamedTuple):
    job_name: str | None
    execs: list[JclExecFact]
    dds: list[JclDdFact]


def extract_jcl_facts(content: str) -> JclExtractionResult:
    """Extract EXEC PGM=, EXEC PROC=, and DD ... DSN= facts from JCL source content."""
    if not content or not content.strip():
        return JclExtractionResult(job_name=None, execs=[], dds=[])

    lines = content.splitlines()

    # 1. Join continuation lines & strip comments
    statements: list[tuple[str, list[int]]] = []
    current_text = ""
    current_lines: list[int] = []
    in_instream = False

    for idx, raw_line in enumerate(lines, 1):
        line = raw_line.rstrip()

        if in_instream:
            if line.startswith("/*") or (
                line.startswith("//")
                and not line.startswith("//*")
                and " DD " not in line.upper()
                and (" EXEC " in line.upper() or " JOB " in line.upper() or len(line.strip()) > 2)
            ):
                in_instream = False
            else:
                continue

        if line.startswith("//*"):
            continue
        if not line.startswith("//"):
            continue
        if " DD *" in line.upper() or " DD DATA" in line.upper():
            in_instream = True

        # Continuation line: starts with // followed by spaces
        is_continuation = bool(re.match(r"^//\s+", line))
        if is_continuation and current_text:
            param_part = line[2:].strip()
            current_text += " " + param_part
            current_lines.append(idx)
        else:
            if current_text:
                statements.append((current_text, current_lines))
            current_text = line
            current_lines = [idx]

    if current_text:
        statements.append((current_text, current_lines))

    job_name: str | None = None
    current_step: str = "JOB"
    execs: list[JclExecFact] = []
    dds: list[JclDdFact] = []

    for text, lnums in statements:
        # Check JOB card
        job_match = re.match(r"^//([A-Za-z0-9#$@]+)\s+JOB\b", text, re.IGNORECASE)
        if job_match:
            job_name = job_match.group(1).upper()
            continue

        # Check EXEC statement
        exec_match = re.match(r"^//([A-Za-z0-9#$@]+)?\s+EXEC\s+(.*)", text, re.IGNORECASE)
        if exec_match:
            step_name = exec_match.group(1).upper() if exec_match.group(1) else "STEP"
            rest = exec_match.group(2).strip()
            first_token = re.split(r"[\s,]", rest)[0].strip()

            if first_token.upper().startswith("PGM="):
                pgm_name = first_token[4:].upper()
                current_step = step_name
                execs.append(
                    JclExecFact(
                        job_name=job_name,
                        step_name=step_name,
                        program_name=pgm_name,
                        line=lnums[0],
                        evidence_lines=lnums,
                        is_proc=False,
                    )
                )
                continue
            elif first_token:
                proc_name = (
                    first_token[5:].upper()
                    if first_token.upper().startswith("PROC=")
                    else first_token.upper()
                )
                current_step = step_name
                execs.append(
                    JclExecFact(
                        job_name=job_name,
                        step_name=step_name,
                        program_name=proc_name,
                        line=lnums[0],
                        evidence_lines=lnums,
                        is_proc=True,
                    )
                )
                continue

        # Check DD DSN=
        dd_match = re.match(r"^//([A-Za-z0-9#$@]+)\s+DD\b", text, re.IGNORECASE)
        dsn_match = re.search(r"\bDSN=([^\s,]+)", text, re.IGNORECASE)
        if dd_match and dsn_match:
            dd_name = dd_match.group(1).upper()
            dsn_val = dsn_match.group(1).upper()
            dds.append(
                JclDdFact(
                    job_name=job_name,
                    step_name=current_step,
                    dd_name=dd_name,
                    dsn=dsn_val,
                    line=lnums[0],
                    evidence_lines=lnums,
                )
            )

    return JclExtractionResult(job_name=job_name, execs=execs, dds=dds)


class JclEnrichmentListener(JCLParserListener):
    def __init__(self, rel_path: str):
        self.rel_path = rel_path
        self.facts: list[dict[str, Any]] = []
        self.job_name = "JOB"
        self.step_name = "STEP"
        self.occurrence_counts: dict[str, int] = {}

    def _add_fact(
        self,
        fact_type: str,
        semantic_key: str,
        parent_key: str | None,
        name: str | None,
        value: str | None,
        attributes: dict[str, Any],
        line_start: int,
        line_end: int,
    ) -> None:
        occ = self.occurrence_counts.get(semantic_key, 0)
        self.occurrence_counts[semantic_key] = occ + 1
        self.facts.append(
            {
                "fact_type": fact_type,
                "semantic_key": semantic_key,
                "occurrence_ix": occ,
                "parent_key": parent_key,
                "name": name,
                "value": value,
                "attributes": attributes,
                "line_start": line_start,
                "line_end": line_end,
                "extractor": "jcl_antlr",
                "extractor_ver": "1.0.0",
            }
        )

    def enterJobCard(self, ctx: Any) -> None:
        if ctx.jobName():
            self.job_name = ctx.jobName().getText().upper()
            key = f"job/{self.job_name}"
            self._add_fact("job", key, None, self.job_name, None, {}, ctx.start.line, ctx.stop.line)

    def enterExecPgmStatement(self, ctx: Any) -> None:
        s_name = ctx.stepName().getText().upper() if ctx.stepName() else "STEP"
        self.step_name = s_name
        pgm_name = ctx.keywordOrSymbolic().getText().upper() if ctx.keywordOrSymbolic() else ""
        step_key = f"step/{self.job_name}.{self.step_name}"
        parent_key = f"job/{self.job_name}"
        self._add_fact(
            "step",
            step_key,
            parent_key,
            self.step_name,
            pgm_name,
            {"type": "pgm"},
            ctx.start.line,
            ctx.stop.line,
        )

    def enterExecProcStatement(self, ctx: Any) -> None:
        s_name = ctx.stepName().getText().upper() if ctx.stepName() else "STEP"
        self.step_name = s_name
        proc_name = ctx.keywordOrSymbolic().getText().upper() if ctx.keywordOrSymbolic() else ""
        step_key = f"step/{self.job_name}.{self.step_name}"
        parent_key = f"job/{self.job_name}"
        self._add_fact(
            "step",
            step_key,
            parent_key,
            self.step_name,
            proc_name,
            {"type": "proc"},
            ctx.start.line,
            ctx.stop.line,
        )

    def enterExecParmCOND(self, ctx: Any) -> None:
        cond_str = ctx.getText()
        step_key = f"step/{self.job_name}.{self.step_name}"
        self._add_fact(
            "cond_gate",
            step_key,
            step_key,
            "COND",
            cond_str,
            {},
            ctx.start.line,
            ctx.stop.line,
        )

    def enterDdStatement(self, ctx: Any) -> None:
        dd_name = ctx.ddName().getText().upper() if ctx.ddName() else ""
        dd_key = f"dd/{self.job_name}.{self.step_name}.{dd_name}"
        parent_key = f"step/{self.job_name}.{self.step_name}"
        self._add_fact("dd", dd_key, parent_key, dd_name, None, {}, ctx.start.line, ctx.stop.line)

    def enterDdParmDSNAME(self, ctx: Any) -> None:
        if ctx.datasetName():
            dsn = ctx.datasetName().getText().upper()
            dsn_key = f"dataset/{dsn}"
            step_key = f"step/{self.job_name}.{self.step_name}"
            self._add_fact("dataset", dsn_key, step_key, dsn, dsn, {}, ctx.start.line, ctx.stop.line)

    def enterDdParmDISP(self, ctx: Any) -> None:
        disp_str = ctx.getText()
        step_key = f"step/{self.job_name}.{self.step_name}"
        self._add_fact("disp", step_key, step_key, "DISP", disp_str, {}, ctx.start.line, ctx.stop.line)

    def enterDdParmASTERISK(self, ctx: Any) -> None:
        step_key = f"step/{self.job_name}.{self.step_name}"
        self._add_fact("instream", step_key, step_key, "DD*", None, {}, ctx.start.line, ctx.stop.line)

    def enterProcStatement(self, ctx: Any) -> None:
        p_name = ctx.procName().getText().upper() if ctx.procName() else "PROC"
        key = f"proc/{p_name}"
        self._add_fact("proc_def", key, f"job/{self.job_name}", p_name, None, {}, ctx.start.line, ctx.stop.line)

    def enterSetStatement(self, ctx: Any) -> None:
        set_text = ctx.getText()
        key = f"step/{self.job_name}.{self.step_name}"
        self._add_fact("set", key, key, "SET", set_text, {}, ctx.start.line, ctx.stop.line)

    def enterIfStatement(self, ctx: Any) -> None:
        if_text = ctx.getText()
        key = f"step/{self.job_name}.{self.step_name}"
        self._add_fact("if_gate", key, key, "IF", if_text, {}, ctx.start.line, ctx.stop.line)

    def enterDefineSymbolicParameter(self, ctx: Any) -> None:
        sym_text = ctx.getText()
        key = f"job/{self.job_name}"
        self._add_fact("symbolic_def", key, key, "SYMBOLIC", sym_text, {}, ctx.start.line, ctx.stop.line)


class _SilentJclErrorListener(ErrorListener):
    def __init__(self) -> None:
        self.error_count = 0
        self.first_error: str | None = None

    def syntaxError(self, recognizer: Any, offendingSymbol: Any, line: int, column: int, msg: str, e: Any) -> None:
        self.error_count += 1
        if self.first_error is None:
            self.first_error = f"line {line}:{column} {msg}"


def extract_jcl_enrichment_facts(
    content: str, rel_path: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract full JCL facts and diagnostic record using ANTLR parser."""
    import time

    from antlr4 import CommonTokenStream, InputStream, ParseTreeWalker
    from antlr4.atn.PredictionMode import PredictionMode

    from .generated.JCLLexer import JCLLexer
    from .generated.JCLParser import JCLParser

    t0 = time.perf_counter()
    if not content or not content.strip():
        diag = {
            "rel_path": rel_path,
            "language": "jcl",
            "status": "ok",
            "error_count": 0,
            "first_error": None,
            "elapsed_ms": 0,
            "extractor_ver": "1.0.0",
        }
        return [], diag

    normalized = "\n".join(l.rstrip() for l in content.splitlines()) + "\n"
    err_listener = _SilentJclErrorListener()
    facts: list[dict[str, Any]] = []
    status = "ok"

    try:
        lexer = JCLLexer(InputStream(normalized))
        lexer.removeErrorListeners()
        lexer.addErrorListener(err_listener)

        tokens = CommonTokenStream(lexer)
        parser = JCLParser(tokens)
        parser.removeErrorListeners()
        parser.addErrorListener(err_listener)
        parser._interp.predictionMode = PredictionMode.SLL

        tree = parser.startRule()
        listener = JclEnrichmentListener(rel_path)
        ParseTreeWalker().walk(listener, tree)
        facts = listener.facts

        if err_listener.error_count > 0:
            status = "partial" if len(facts) > 0 else "failed"
    except Exception as ex:
        status = "failed"
        if err_listener.first_error is None:
            err_listener.first_error = str(ex)

    t1 = time.perf_counter()
    diag = {
        "rel_path": rel_path,
        "language": "jcl",
        "status": status,
        "error_count": err_listener.error_count,
        "first_error": err_listener.first_error,
        "elapsed_ms": int((t1 - t0) * 1000),
        "extractor_ver": "1.0.0",
    }
    return facts, diag

