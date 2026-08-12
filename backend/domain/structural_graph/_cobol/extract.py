"""COBOL fact extractor using ANTLR parsers."""
from __future__ import annotations

import re
from typing import Any, NamedTuple

from antlr4 import CommonTokenStream, InputStream, ParseTreeWalker
from antlr4.atn.PredictionMode import PredictionMode
from antlr4.error.ErrorListener import ErrorListener

from shared.logger import logger

from .generated.Cobol85Lexer import Cobol85Lexer
from .generated.Cobol85Listener import Cobol85Listener
from .generated.Cobol85Parser import Cobol85Parser
from .generated.Cobol85PreprocessorLexer import Cobol85PreprocessorLexer
from .generated.Cobol85PreprocessorListener import Cobol85PreprocessorListener
from .generated.Cobol85PreprocessorParser import Cobol85PreprocessorParser
from .preprocess import is_copybook_shaped, normalize_cobol_text


class CobolCallFact(NamedTuple):
    callee: str
    line: int
    resolution_method: str  # "cobol_call_literal" or "dynamic_call"


class CobolCopyFact(NamedTuple):
    member: str
    line: int
    resolution_method: str  # "cobol_copy_statement"


class CobolExtractionResult(NamedTuple):
    program_id: str | None
    calls: list[CobolCallFact]
    copies: list[CobolCopyFact]


class _PreprocessorListener(Cobol85PreprocessorListener):
    def __init__(self) -> None:
        self.copy_spans: list[tuple[int, int]] = []
        self.copies: list[CobolCopyFact] = []

    def enterCopyStatement(self, ctx: Any) -> None:
        if ctx.copySource():
            member = ctx.copySource().getText().strip("'\"").upper()
            if member:
                self.copies.append(
                    CobolCopyFact(
                        member=member,
                        line=ctx.start.line,
                        resolution_method="cobol_copy_statement",
                    )
                )
        if ctx.start and ctx.stop:
            self.copy_spans.append((ctx.start.start, ctx.stop.stop))


class _MainListener(Cobol85Listener):
    def __init__(self) -> None:
        self.program_id: str | None = None
        self.calls: list[CobolCallFact] = []

    def enterProgramIdParagraph(self, ctx: Any) -> None:
        if ctx.programName():
            self.program_id = ctx.programName().getText().strip("'\"").upper()

    def enterCallStatement(self, ctx: Any) -> None:
        lit = ctx.literal()
        ident = ctx.identifier()
        if lit:
            callee = lit.getText().strip("'\"").upper()
            if callee:
                self.calls.append(
                    CobolCallFact(
                        callee=callee,
                        line=ctx.start.line,
                        resolution_method="cobol_call_literal",
                    )
                )
        elif ident:
            callee = ident.getText().strip("'\"").upper()
            if callee:
                self.calls.append(
                    CobolCallFact(
                        callee=callee,
                        line=ctx.start.line,
                        resolution_method="dynamic_call",
                    )
                )


class _CountingErrorListener(ErrorListener):
    def __init__(self) -> None:
        self.count = 0
        self.first: str | None = None

    def syntaxError(self, recognizer: Any, offendingSymbol: Any, line: int, column: int, msg: str, e: Any) -> None:
        self.count += 1
        if self.first is None:
            self.first = f"L{line}:{column} {msg[:120]}"


class _SilentErrorListener(_CountingErrorListener):
    """Backward compatible alias for _CountingErrorListener."""
    pass


def _run_main_grammar_pass(
    text: str, error_listener: ErrorListener
) -> tuple[str | None, list[CobolCallFact]]:
    """Parse with the Cobol85 grammar and extract PROGRAM-ID + CALL.

    Uses SLL prediction instead of the default ALL(*) full-context prediction. ALL(*)
    can blow up combinatorially on some real programs (e.g. CardDemo CSUTLDTC took 3s+
    and, once CPU contention piled on, dragged past any wall-clock timeout and cascaded
    across files). SLL has no such worst case, so parses stay bounded and fast — no
    thread/process/timeout machinery needed. The default error strategy is kept, so
    COBOL-dialect quirks recover exactly as full LL did; this was verified to yield
    identical PROGRAM-ID/CALL facts across the whole corpus (0 mismatches, 0 files where
    SLL failed to reach the same tree).
    """
    lexer_m = Cobol85Lexer(InputStream(text))
    lexer_m.removeErrorListeners()
    lexer_m.addErrorListener(error_listener)

    tokens_m = CommonTokenStream(lexer_m)
    parser_m = Cobol85Parser(tokens_m)
    parser_m.removeErrorListeners()
    parser_m.addErrorListener(error_listener)
    parser_m._interp.predictionMode = PredictionMode.SLL

    listener_m = _MainListener()
    ParseTreeWalker().walk(listener_m, parser_m.startRule())
    return listener_m.program_id, listener_m.calls


# Bare copybooks (COPY members) have no IDENTIFICATION DIVISION, but Cobol85's
# startRule requires one and aborts immediately without it ("mismatched input
# '01' expecting {ID, IDENTIFICATION}"). Wrapping the DATA DIVISION body in this
# minimal shell lets the same grammar + CobolEnrichmentListener extract
# record/field facts with no grammar changes. Line numbers must be corrected by
# _COPYBOOK_WRAP_LINE_COUNT (the number of header lines) after parsing.
_COPYBOOK_WRAP_HEADER = (
    "IDENTIFICATION DIVISION.\n"
    "PROGRAM-ID. COPYBOOK-SHIM.\n"
    "DATA DIVISION.\n"
    "WORKING-STORAGE SECTION.\n"
)
_COPYBOOK_WRAP_LINE_COUNT = 4


def _parse_copybook_tree(norm_no_copy: str, error_listener: ErrorListener) -> Any:
    """Parse a copybook's normalized body via the synthetic program wrapper."""
    lexer = Cobol85Lexer(InputStream(_COPYBOOK_WRAP_HEADER + norm_no_copy))
    lexer.removeErrorListeners()
    lexer.addErrorListener(error_listener)

    tokens = CommonTokenStream(lexer)
    parser = Cobol85Parser(tokens)
    parser.removeErrorListeners()
    parser.addErrorListener(error_listener)
    parser._interp.predictionMode = PredictionMode.SLL
    return parser.startRule()


def _extract_exec_sql_includes(norm: str) -> list[CobolCopyFact]:
    """Scan multiline EXEC SQL ... END-EXEC blocks for INCLUDE <member>."""
    results: list[CobolCopyFact] = []
    sql_blocks = re.finditer(
        r"EXEC\s+SQL\s+(.*?)\s+END-EXEC", norm, re.IGNORECASE | re.DOTALL
    )
    for m in sql_blocks:
        body = m.group(1).strip()
        inc_match = re.search(r"^\s*INCLUDE\s+['\"]?([A-Za-z0-9_-]+)['\"]?", body, re.IGNORECASE)
        if inc_match:
            member = inc_match.group(1).upper()
            line_no = norm[:m.start()].count("\n") + 1
            results.append(
                CobolCopyFact(
                    member=member,
                    line=line_no,
                    resolution_method="exec_sql_include",
                )
            )
    return results


def extract_cobol_facts(content: str) -> CobolExtractionResult:
    """Extract PROGRAM-ID, CALL, and COPY/SQL-INCLUDE statements from COBOL source code using ANTLR parsers + SQL include scanner."""
    if not content or not content.strip():
        return CobolExtractionResult(program_id=None, calls=[], copies=[])

    norm = normalize_cobol_text(content)
    silent_listener = _CountingErrorListener()

    program_id: str | None = None
    calls: list[CobolCallFact] = []
    copies: list[CobolCopyFact] = []
    antlr_success = False

    # 1. ANTLR Preprocessor Pass for COPY statements
    copy_spans: list[tuple[int, int]] = []
    try:
        lexer_p = Cobol85PreprocessorLexer(InputStream(norm))
        lexer_p.removeErrorListeners()
        lexer_p.addErrorListener(silent_listener)

        tokens_p = CommonTokenStream(lexer_p)
        parser_p = Cobol85PreprocessorParser(tokens_p)
        parser_p.removeErrorListeners()
        parser_p.addErrorListener(silent_listener)
        parser_p._interp.predictionMode = PredictionMode.SLL

        tree_p = parser_p.startRule()
        listener_p = _PreprocessorListener()
        ParseTreeWalker().walk(listener_p, tree_p)

        copies = listener_p.copies
        copy_spans = listener_p.copy_spans
        antlr_success = True
    except Exception:
        pass

    # Always extract multiline EXEC SQL INCLUDE facts and merge with copies
    sql_includes = _extract_exec_sql_includes(norm)
    for inc in sql_includes:
        if not any(c.member == inc.member and c.line == inc.line for c in copies):
            copies.append(inc)

    # Replace COPY statement spans with spaces to preserve line numbers for main pass
    norm_list = list(norm)
    for start, stop in copy_spans:
        for i in range(start, min(stop + 1, len(norm_list))):
            if norm_list[i] != "\n":
                norm_list[i] = " "
    norm_no_copy = "".join(norm_list)

    # 2. ANTLR Main Pass for PROGRAM-ID and CALL statements. Skipped for bare
    # copybooks (no IDENTIFICATION DIVISION) - they have no PROGRAM-ID/CALLs, and
    # startRule() requires IDENTIFICATION DIVISION so it would just fail on them.
    upper_norm = norm_no_copy.upper()
    if not is_copybook_shaped(content) and (
        "IDENTIFICATION" in upper_norm or "PROGRAM-ID" in upper_norm or "CALL" in upper_norm
    ):
        try:
            m_program_id, m_calls = _run_main_grammar_pass(norm_no_copy, silent_listener)
            if m_program_id or m_calls:
                program_id = m_program_id
                calls = m_calls
                antlr_success = True
        except Exception:
            logger.debug(
                "[cobol] main-grammar parse failed; using regex fallback", exc_info=True
            )

    want_pid = program_id is None and "PROGRAM-ID" in upper_norm
    want_calls = not calls and "CALL" in upper_norm
    want_copies = not copies and ("COPY" in upper_norm or "INCLUDE" in upper_norm)
    if not antlr_success or want_pid or want_calls or want_copies:
        fb = _extract_cobol_facts_regex_fallback(norm)
        recovered: list[str] = []
        if program_id is None and fb.program_id:
            program_id = fb.program_id
            recovered.append("program-id")
        if not calls and program_id is not None and fb.calls:
            calls = fb.calls
            recovered.append(f"{len(fb.calls)} call(s)")
        if not copies and fb.copies:
            copies = fb.copies
            recovered.append(f"{len(fb.copies)} copy(s)")
        if recovered:
            logger.debug("[cobol] regex fallback recovered %s ANTLR missed", ", ".join(recovered))

    return CobolExtractionResult(program_id=program_id, calls=calls, copies=copies)


class CobolEnrichmentListener(Cobol85Listener):
    def __init__(self, rel_path: str):
        self.rel_path = rel_path
        self.facts: list[dict[str, Any]] = []
        self.program_id: str | None = None
        self.calls: list[CobolCallFact] = []
        self.current_section: str | None = None
        self.current_paragraph: str | None = None
        self.occurrence_counts: dict[str, int] = {}
        self.ordinal_counts: dict[tuple[str, str], int] = {}
        self.has_exec_sql = False
        self.has_exec_cics = False

    def _next_ordinal(self, kind: str, parent_key: str) -> int:
        k = (kind, parent_key)
        self.ordinal_counts[k] = self.ordinal_counts.get(k, 0) + 1
        return self.ordinal_counts[k]

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
                "extractor": "cobol_antlr",
                "extractor_ver": "1.0.0",
            }
        )

    def _get_current_parent_key(self) -> str:
        pid = self.program_id or "MAIN"
        if self.current_paragraph:
            return f"paragraph/{pid}.{self.current_paragraph}"
        elif self.current_section:
            return f"section/{pid}.{self.current_section}"
        return f"program/{pid}"

    def enterProgramIdParagraph(self, ctx: Any) -> None:
        if ctx.programName():
            self.program_id = ctx.programName().getText().strip("'\"").upper()
            key = f"program/{self.program_id}"
            self._add_fact(
                "program",
                key,
                None,
                self.program_id,
                None,
                {},
                ctx.start.line,
                ctx.stop.line,
            )

    def enterCallStatement(self, ctx: Any) -> None:
        lit = ctx.literal()
        ident = ctx.identifier()
        callee = None
        res_method = "literal"
        if lit:
            callee = lit.getText().strip("'\"").upper()
        elif ident:
            callee = ident.getText().strip("'\"").upper()
            res_method = "dynamic"

        if callee:
            self.calls.append(
                CobolCallFact(
                    callee=callee,
                    line=ctx.start.line,
                    resolution_method="cobol_call_literal" if lit else "dynamic_call",
                )
            )
            pid = self.program_id or "MAIN"
            curr_p = self.current_paragraph or "MAIN"
            key = f"call/{pid}.{curr_p}.{callee}"
            parent = self._get_current_parent_key()
            self._add_fact(
                "call",
                key,
                parent,
                callee,
                None,
                {"resolution": res_method},
                ctx.start.line,
                ctx.stop.line,
            )

    def enterProcedureSection(self, ctx: Any) -> None:
        if ctx.procedureSectionHeader() and ctx.procedureSectionHeader().sectionName():
            sec_name = (
                ctx.procedureSectionHeader().sectionName().getText().strip("'\"").upper()
            )
            self.current_section = sec_name
            self.current_paragraph = None
            pid = self.program_id or "MAIN"
            key = f"section/{pid}.{sec_name}"
            self._add_fact(
                "section",
                key,
                f"program/{pid}",
                sec_name,
                None,
                {},
                ctx.start.line,
                ctx.stop.line,
            )

    def enterParagraph(self, ctx: Any) -> None:
        if ctx.paragraphName():
            p_name = ctx.paragraphName().getText().strip("'\"").upper()
            self.current_paragraph = p_name
            pid = self.program_id or "MAIN"
            key = f"paragraph/{pid}.{p_name}"
            parent = (
                f"section/{pid}.{self.current_section}"
                if self.current_section
                else f"program/{pid}"
            )
            self._add_fact(
                "paragraph",
                key,
                parent,
                p_name,
                None,
                {},
                ctx.start.line,
                ctx.stop.line,
            )

    def enterPerformStatement(self, ctx: Any) -> None:
        thru_target = None
        target = None
        if ctx.performProcedureStatement():
            p_proc = ctx.performProcedureStatement()
            if p_proc.procedureName():
                target = p_proc.procedureName()[0].getText().strip("'\"").upper()
                if len(p_proc.procedureName()) > 1:
                    thru_target = p_proc.procedureName()[1].getText().strip("'\"").upper()

        key = self._get_current_parent_key()
        attrs = {}
        if thru_target:
            attrs["thru"] = thru_target
        self._add_fact(
            "performs",
            key,
            key,
            target,
            target,
            attrs,
            ctx.start.line,
            ctx.stop.line,
        )

    def enterPerformType(self, ctx: Any) -> None:
        parent = self._get_current_parent_key()
        n = self._next_ordinal("loop", parent)
        cond_text = ctx.getText()
        curr_p = self.current_paragraph or "MAIN"
        pid = self.program_id or "MAIN"
        key = f"loop/{pid}.{curr_p}#{n}"
        self._add_fact(
            "loop",
            key,
            parent,
            "PERFORM_LOOP",
            cond_text,
            {},
            ctx.start.line,
            ctx.stop.line,
        )

    def enterSelectClause(self, ctx: Any) -> None:
        if ctx.fileName():
            f_name = ctx.fileName().getText().strip("'\"").upper()
            pid = self.program_id or "MAIN"
            key = f"file/{pid}.{f_name}"
            self._add_fact(
                "file_def",
                key,
                f"program/{pid}",
                f_name,
                None,
                {},
                ctx.start.line,
                ctx.stop.line,
            )

    def enterFileDescriptionEntry(self, ctx: Any) -> None:
        if ctx.fileName():
            fd_name = ctx.fileName().getText().strip("'\"").upper()
            pid = self.program_id or "MAIN"
            key = f"file/{pid}.{fd_name}"
            self._add_fact(
                "fd",
                key,
                f"program/{pid}",
                fd_name,
                None,
                {},
                ctx.start.line,
                ctx.stop.line,
            )

    def enterDataDescriptionEntryFormat1(self, ctx: Any) -> None:
        level_str = ctx.getChild(0).getText() if ctx.getChildCount() > 0 else "01"
        try:
            level = int(level_str)
        except ValueError:
            level = 1

        name = (
            ctx.dataName().getText().strip("'\"").upper()
            if ctx.dataName()
            else "FILLER"
        )
        pid = self.program_id or "MAIN"
        fact_type = "record" if level in (1, 77) else "field"
        key = (
            f"record/{pid}.{name}"
            if fact_type == "record"
            else f"field/{pid}.{name}"
        )

        attrs: dict[str, Any] = {"level": level}
        if ctx.dataPictureClause():
            pic_clause = ctx.dataPictureClause()[0] if isinstance(ctx.dataPictureClause(), list) else ctx.dataPictureClause()
            if hasattr(pic_clause, "pictureString") and pic_clause.pictureString():
                attrs["pic"] = pic_clause.pictureString().getText()
            else:
                attrs["pic"] = pic_clause.getText()
        if ctx.dataUsageClause():
            usage_clause = ctx.dataUsageClause()[0] if isinstance(ctx.dataUsageClause(), list) else ctx.dataUsageClause()
            attrs["usage"] = usage_clause.getText()

        self._add_fact(
            fact_type,
            key,
            f"program/{pid}",
            name,
            None,
            attrs,
            ctx.start.line,
            ctx.stop.line,
        )

    def enterIfStatement(self, ctx: Any) -> None:
        parent = self._get_current_parent_key()
        n = self._next_ordinal("branch", parent)
        curr_p = self.current_paragraph or "MAIN"
        pid = self.program_id or "MAIN"
        key = f"branch/{pid}.{curr_p}#{n}"
        cond_text = ctx.condition().getText() if ctx.condition() else ""
        self._add_fact(
            "branch",
            key,
            parent,
            "IF",
            cond_text,
            {"kind": "if"},
            ctx.start.line,
            ctx.stop.line,
        )

    def enterEvaluateStatement(self, ctx: Any) -> None:
        parent = self._get_current_parent_key()
        n = self._next_ordinal("branch", parent)
        curr_p = self.current_paragraph or "MAIN"
        pid = self.program_id or "MAIN"
        key = f"branch/{pid}.{curr_p}#{n}"
        eval_text = ctx.getText()
        self._add_fact(
            "branch",
            key,
            parent,
            "EVALUATE",
            eval_text,
            {"kind": "evaluate"},
            ctx.start.line,
            ctx.stop.line,
        )

    def enterExecSqlStatement(self, ctx: Any) -> None:
        self.has_exec_sql = True
        curr_p = self.current_paragraph or "MAIN"
        pid = self.program_id or "MAIN"
        key = f"exec_block/{pid}.{curr_p}.sql"
        parent = self._get_current_parent_key()
        self._add_fact(
            "exec_block",
            key,
            parent,
            "EXEC_SQL",
            None,
            {"unparsed": True, "type": "sql"},
            ctx.start.line,
            ctx.stop.line,
        )

    def enterExecCicsStatement(self, ctx: Any) -> None:
        self.has_exec_cics = True
        curr_p = self.current_paragraph or "MAIN"
        pid = self.program_id or "MAIN"
        key = f"exec_block/{pid}.{curr_p}.cics"
        parent = self._get_current_parent_key()
        self._add_fact(
            "exec_block",
            key,
            parent,
            "EXEC_CICS",
            None,
            {"unparsed": True, "type": "cics"},
            ctx.start.line,
            ctx.stop.line,
        )

    def enterGoToStatement(self, ctx: Any) -> None:
        target = ctx.getText().replace("GO TO", "").replace("GOTO", "").replace(".", "").strip().upper()
        curr_p = self.current_paragraph or "MAIN"
        pid = self.program_id or "MAIN"
        key = f"paragraph/{pid}.{curr_p}"
        self._add_fact(
            "goto",
            key,
            key,
            target,
            target,
            {},
            ctx.start.line,
            ctx.stop.line,
        )

    def enterAtEndPhrase(self, ctx: Any) -> None:
        self._emit_handler(ctx, "at_end")

    def enterNotAtEndPhrase(self, ctx: Any) -> None:
        self._emit_handler(ctx, "not_at_end")

    def enterInvalidKeyPhrase(self, ctx: Any) -> None:
        self._emit_handler(ctx, "invalid_key")

    def enterNotInvalidKeyPhrase(self, ctx: Any) -> None:
        self._emit_handler(ctx, "not_invalid_key")

    def enterOnSizeErrorPhrase(self, ctx: Any) -> None:
        self._emit_handler(ctx, "on_size_error")

    def enterUseStatement(self, ctx: Any) -> None:
        self._emit_handler(ctx, "declaratives")

    def _emit_handler(self, ctx: Any, kind: str) -> None:
        parent = self._get_current_parent_key()
        n = self._next_ordinal("handler", parent)
        curr_p = self.current_paragraph or "MAIN"
        pid = self.program_id or "MAIN"
        self._add_fact(
            "handler",
            f"handler/{pid}.{curr_p}#{n}",
            parent,
            kind.upper(),
            None,
            {"kind": kind},
            ctx.start.line,
            ctx.stop.line,
        )


def _walk_copybook_facts(rel_path: str, tree: Any) -> tuple[list[dict[str, Any]], bool, bool]:
    """Walk a copybook's wrapped parse tree and correct for the synthetic header.

    Drops the synthetic "program" fact from the wrapper's fake PROGRAM-ID (a
    copybook is never a callable program) and shifts every fact's line numbers
    back by the wrapper's header-line count so they match the real file.
    """
    listener = CobolEnrichmentListener(rel_path)
    ParseTreeWalker().walk(listener, tree)
    facts: list[dict[str, Any]] = []
    for f in listener.facts:
        if f["fact_type"] == "program":
            continue
        f["line_start"] = max(1, f["line_start"] - _COPYBOOK_WRAP_LINE_COUNT)
        f["line_end"] = max(1, f["line_end"] - _COPYBOOK_WRAP_LINE_COUNT)
        facts.append(f)
    return facts, listener.has_exec_sql, listener.has_exec_cics


def extract_cobol_enrichment_facts(
    content: str, rel_path: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract full sub-program facts and diagnostic record from COBOL code using ANTLR."""
    import time
    t0 = time.perf_counter()
    empty_diag = {
        "rel_path": rel_path,
        "language": "cobol",
        "status": "ok",
        "error_count": 0,
        "first_error": None,
        "elapsed_ms": 0,
        "extractor_ver": "1.0.0",
        "has_exec_sql": False,
        "has_exec_cics": False,
    }
    if not content or not content.strip():
        return [], empty_diag

    norm = normalize_cobol_text(content)

    # Preprocessor pass for COPY spans
    copy_spans: list[tuple[int, int]] = []
    err_p = _CountingErrorListener()
    try:
        lexer_p = Cobol85PreprocessorLexer(InputStream(norm))
        lexer_p.removeErrorListeners()
        lexer_p.addErrorListener(err_p)

        tokens_p = CommonTokenStream(lexer_p)
        parser_p = Cobol85PreprocessorParser(tokens_p)
        parser_p.removeErrorListeners()
        parser_p.addErrorListener(err_p)
        parser_p._interp.predictionMode = PredictionMode.SLL
        tree_p = parser_p.startRule()
        listener_p = _PreprocessorListener()
        ParseTreeWalker().walk(listener_p, tree_p)
        copy_spans = listener_p.copy_spans
    except Exception:
        pass

    norm_list = list(norm)
    for start, stop in copy_spans:
        for i in range(start, min(stop + 1, len(norm_list))):
            if norm_list[i] != "\n":
                norm_list[i] = " "
    norm_no_copy = "".join(norm_list)

    err_m = _CountingErrorListener()
    facts: list[dict[str, Any]] = []
    status = "ok"
    has_exec_sql = False
    has_exec_cics = False

    try:
        if is_copybook_shaped(content):
            tree = _parse_copybook_tree(norm_no_copy, err_m)
            facts, has_exec_sql, has_exec_cics = _walk_copybook_facts(rel_path, tree)
        else:
            lexer = Cobol85Lexer(InputStream(norm_no_copy))
            lexer.removeErrorListeners()
            lexer.addErrorListener(err_m)

            tokens = CommonTokenStream(lexer)
            parser = Cobol85Parser(tokens)
            parser.removeErrorListeners()
            parser.addErrorListener(err_m)
            parser._interp.predictionMode = PredictionMode.SLL

            tree = parser.startRule()
            listener = CobolEnrichmentListener(rel_path)
            ParseTreeWalker().walk(listener, tree)
            facts = listener.facts
            has_exec_sql = listener.has_exec_sql
            has_exec_cics = listener.has_exec_cics

        total_err_count = err_p.count + err_m.count
        first_err = err_m.first or err_p.first
        if total_err_count > 0:
            status = "partial" if len(facts) > 0 else "failed"
    except Exception as ex:
        status = "failed"
        total_err_count = err_p.count + err_m.count + 1
        first_err = err_m.first or err_p.first or str(ex)

    t1 = time.perf_counter()
    diag = {
        "rel_path": rel_path,
        "language": "cobol",
        "status": status,
        "error_count": total_err_count,
        "first_error": first_err,
        "elapsed_ms": int((t1 - t0) * 1000),
        "extractor_ver": "1.0.0",
        "has_exec_sql": has_exec_sql,
        "has_exec_cics": has_exec_cics,
    }
    return facts, diag


def extract_cobol_all(
    content: str, rel_path: str
) -> tuple[CobolExtractionResult, list[dict[str, Any]], dict[str, Any]]:
    """Parse COBOL ONCE, walk tree with both _MainListener and CobolEnrichmentListener.

    Returns (edge_result, enrichment_facts, diag).
    """
    import time

    t0 = time.perf_counter()
    empty_diag = {
        "rel_path": rel_path,
        "language": "cobol",
        "status": "ok",
        "error_count": 0,
        "first_error": None,
        "elapsed_ms": 0,
        "extractor_ver": "1.0.0",
        "has_exec_sql": False,
        "has_exec_cics": False,
    }
    if not content or not content.strip():
        return CobolExtractionResult(program_id=None, calls=[], copies=[]), [], empty_diag

    norm = normalize_cobol_text(content)
    err_p = _CountingErrorListener()

    program_id: str | None = None
    calls: list[CobolCallFact] = []
    copies: list[CobolCopyFact] = []
    facts: list[dict[str, Any]] = []
    antlr_success = False
    status = "ok"
    has_exec_sql = False
    has_exec_cics = False

    # 1. ANTLR Preprocessor Pass for COPY statements (ONCE)
    copy_spans: list[tuple[int, int]] = []
    try:
        lexer_p = Cobol85PreprocessorLexer(InputStream(norm))
        lexer_p.removeErrorListeners()
        lexer_p.addErrorListener(err_p)

        tokens_p = CommonTokenStream(lexer_p)
        parser_p = Cobol85PreprocessorParser(tokens_p)
        parser_p.removeErrorListeners()
        parser_p.addErrorListener(err_p)
        parser_p._interp.predictionMode = PredictionMode.SLL

        tree_p = parser_p.startRule()
        listener_p = _PreprocessorListener()
        ParseTreeWalker().walk(listener_p, tree_p)

        copies = listener_p.copies
        copy_spans = listener_p.copy_spans
        antlr_success = True
    except Exception:
        pass

    # The preprocessor COPY pass does not recognise EXEC SQL INCLUDE, so scan for it
    # separately and merge unconditionally (mirrors extract_cobol_facts). Gating this
    # behind the empty-copies fallback would drop SQL includes from any program that
    # also has ordinary COPY statements.
    for inc in _extract_exec_sql_includes(norm):
        if not any(c.member == inc.member and c.line == inc.line for c in copies):
            copies.append(inc)

    # Replace COPY statement spans with spaces to preserve line numbers
    norm_list = list(norm)
    for start, stop in copy_spans:
        for i in range(start, min(stop + 1, len(norm_list))):
            if norm_list[i] != "\n":
                norm_list[i] = " "
    norm_no_copy = "".join(norm_list)

    # 2. ANTLR Main Pass (ONCE, tree walked by both listeners)
    upper_norm = norm_no_copy.upper()
    err_m = _CountingErrorListener()
    try:
        if is_copybook_shaped(content):
            # Bare copybook: no IDENTIFICATION DIVISION, so startRule() would abort
            # immediately (see _parse_copybook_tree). It has no PROGRAM-ID/CALLs -
            # only its record/field facts matter.
            tree_m = _parse_copybook_tree(norm_no_copy, err_m)
            facts, has_exec_sql, has_exec_cics = _walk_copybook_facts(rel_path, tree_m)
            antlr_success = True
        else:
            lexer_m = Cobol85Lexer(InputStream(norm_no_copy))
            lexer_m.removeErrorListeners()
            lexer_m.addErrorListener(err_m)

            tokens_m = CommonTokenStream(lexer_m)
            parser_m = Cobol85Parser(tokens_m)
            parser_m.removeErrorListeners()
            parser_m.addErrorListener(err_m)
            parser_m._interp.predictionMode = PredictionMode.SLL

            tree_m = parser_m.startRule()

            # Single walk: CobolEnrichmentListener extracts PROGRAM-ID, CALL facts, and enrichment facts
            enrich_listener = CobolEnrichmentListener(rel_path)
            ParseTreeWalker().walk(enrich_listener, tree_m)

            if enrich_listener.program_id or enrich_listener.calls:
                program_id = enrich_listener.program_id
                calls = enrich_listener.calls
                antlr_success = True

            facts = enrich_listener.facts
            has_exec_sql = enrich_listener.has_exec_sql
            has_exec_cics = enrich_listener.has_exec_cics

        total_err_count = err_p.count + err_m.count
        first_err = err_m.first or err_p.first
        if total_err_count > 0:
            status = "partial" if len(facts) > 0 else "failed"
    except Exception as ex:
        status = "failed"
        total_err_count = err_p.count + err_m.count + 1
        first_err = err_m.first or err_p.first or str(ex)

    # 3. Regex Fallback (kept intact to preserve exact edge results)
    want_pid = program_id is None and "PROGRAM-ID" in upper_norm
    want_calls = not calls and "CALL" in upper_norm
    want_copies = not copies and "COPY" in upper_norm
    if not antlr_success or want_pid or want_calls or want_copies or status in ("partial", "failed"):
        fb = _extract_cobol_facts_regex_fallback(norm)
        recovered: list[str] = []
        if program_id is None and fb.program_id:
            program_id = fb.program_id
            recovered.append("program-id")
        if not calls and program_id is not None and fb.calls:
            calls = fb.calls
            recovered.append(f"{len(fb.calls)} call(s)")
        if not copies and fb.copies:
            copies = fb.copies
            recovered.append(f"{len(fb.copies)} copy(s)")
        if recovered:
            logger.debug("[cobol] regex fallback recovered %s ANTLR missed", ", ".join(recovered))

    t1 = time.perf_counter()
    diag = {
        "rel_path": rel_path,
        "language": "cobol",
        "status": status,
        "error_count": total_err_count,
        "first_error": first_err,
        "elapsed_ms": int((t1 - t0) * 1000),
        "extractor_ver": "1.0.0",
        "has_exec_sql": has_exec_sql,
        "has_exec_cics": has_exec_cics,
    }
    return CobolExtractionResult(program_id=program_id, calls=calls, copies=copies), facts, diag


def _extract_cobol_facts_regex_fallback(norm: str) -> CobolExtractionResult:
    """Degraded regex fallback path for COBOL fact extraction when ANTLR parse tree is incomplete."""
    lines = norm.splitlines()
    program_id: str | None = None
    calls: list[CobolCallFact] = []
    copies: list[CobolCopyFact] = []

    pid_match = re.search(r"PROGRAM-ID\s*\.\s*([A-Za-z0-9_-]+)", norm, re.IGNORECASE)
    if pid_match:
        program_id = pid_match.group(1).upper()

    for idx, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line:
            continue

        copy_match = re.search(r"\b(?:COPY|EXEC\s+SQL\s+INCLUDE)\s+['\"]?([A-Za-z0-9_-]+)['\"]?", line, re.IGNORECASE)
        if copy_match:
            copybook = copy_match.group(1).upper()
            method = "exec_sql_include" if "INCLUDE" in copy_match.group(0).upper() else "cobol_copy_statement"
            copies.append(CobolCopyFact(member=copybook, line=idx, resolution_method=method))
            continue

        if re.search(r"\bCALL\b", line, re.IGNORECASE):
            lit_match = re.search(r"\bCALL\s+['\"]([A-Za-z0-9_-]+)['\"]", line, re.IGNORECASE)
            if lit_match:
                callee = lit_match.group(1).upper()
                calls.append(CobolCallFact(callee=callee, line=idx, resolution_method="cobol_call_literal"))
            else:
                var_match = re.search(r"\bCALL\s+([A-Za-z0-9_-]+)", line, re.IGNORECASE)
                if var_match:
                    callee = var_match.group(1).upper()
                    calls.append(CobolCallFact(callee=callee, line=idx, resolution_method="dynamic_call"))

    return CobolExtractionResult(program_id=program_id, calls=calls, copies=copies)

