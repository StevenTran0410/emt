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
from .preprocess import normalize_cobol_text


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


class _SilentErrorListener(ErrorListener):
    def syntaxError(self, recognizer: Any, offendingSymbol: Any, line: int, column: int, msg: str, e: Any) -> None:
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


def extract_cobol_facts(content: str) -> CobolExtractionResult:
    """Extract PROGRAM-ID, CALL, and COPY statements from COBOL source code using ANTLR parsers."""
    if not content or not content.strip():
        return CobolExtractionResult(program_id=None, calls=[], copies=[])

    norm = normalize_cobol_text(content)
    silent_listener = _SilentErrorListener()

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
        # SLL prediction here too — the preprocessor grammar hits the same ALL(*) blowup
        # (COACTUPC ~3.9s → 0.6s), and SLL yields identical COPY facts across the corpus.
        parser_p._interp.predictionMode = PredictionMode.SLL

        tree_p = parser_p.startRule()
        listener_p = _PreprocessorListener()
        ParseTreeWalker().walk(listener_p, tree_p)

        copies = listener_p.copies
        copy_spans = listener_p.copy_spans
        antlr_success = True
    except Exception:
        pass

    # Replace COPY statement spans with spaces to preserve line numbers for main pass
    norm_list = list(norm)
    for start, stop in copy_spans:
        for i in range(start, min(stop + 1, len(norm_list))):
            if norm_list[i] != "\n":
                norm_list[i] = " "
    norm_no_copy = "".join(norm_list)

    # 2. ANTLR Main Pass for PROGRAM-ID and CALL statements. Runs inline: SLL prediction
    # (see _run_main_grammar_pass) keeps every parse bounded and fast, so there is no
    # pathological case to guard against with a thread/process timeout.
    upper_norm = norm_no_copy.upper()
    if "IDENTIFICATION" in upper_norm or "PROGRAM-ID" in upper_norm or "CALL" in upper_norm:
        try:
            m_program_id, m_calls = _run_main_grammar_pass(norm_no_copy, silent_listener)
            if m_program_id or m_calls:
                program_id = m_program_id
                calls = m_calls
                antlr_success = True
        except Exception:
            # Parse crash — let the regex fallback below recover.
            logger.debug(
                "[cobol] main-grammar parse failed; using regex fallback", exc_info=True
            )

    # Regex is a supplement, not a failure signal. The full grammar legitimately
    # extracts nothing for a keyword that only appears in a comment or data name, and a
    # copybook has no executable CALLs at all — so a bare "CALL"/"COPY" substring is not
    # evidence of incomplete parsing. Run regex only where a fact is plausibly missing,
    # gate CALL recovery on real program context (copybooks stay call-free, killing the
    # false positives the raw CALL regex would otherwise inject), and only log when it
    # actually recovers something, so the log means "regex found what ANTLR missed"
    # rather than firing on every healthy CICS program.
    want_pid = program_id is None and "PROGRAM-ID" in upper_norm
    want_calls = not calls and "CALL" in upper_norm
    want_copies = not copies and "COPY" in upper_norm
    if not antlr_success or want_pid or want_calls or want_copies:
        fb = _extract_cobol_facts_regex_fallback(norm)
        recovered: list[str] = []
        if program_id is None and fb.program_id:
            program_id = fb.program_id
            recovered.append("program-id")
        # program_id is now set iff this is a real program (ANTLR or regex found one);
        # copybooks stay None, so their spurious CALL matches are dropped here.
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
        self.program_id = "MAIN"
        self.current_section: str | None = None
        self.current_paragraph: str | None = None
        self.occurrence_counts: dict[str, int] = {}
        self.branch_count = 0
        self.loop_count = 0
        self.handler_count = 0
        self.exec_count = 0

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
        if self.current_paragraph:
            return f"paragraph/{self.program_id}.{self.current_paragraph}"
        elif self.current_section:
            return f"section/{self.program_id}.{self.current_section}"
        return f"program/{self.program_id}"

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

    def enterProcedureSection(self, ctx: Any) -> None:
        if ctx.procedureSectionHeader() and ctx.procedureSectionHeader().sectionName():
            sec_name = (
                ctx.procedureSectionHeader().sectionName().getText().strip("'\"").upper()
            )
            self.current_section = sec_name
            self.current_paragraph = None
            key = f"section/{self.program_id}.{sec_name}"
            self._add_fact(
                "section",
                key,
                f"program/{self.program_id}",
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
            key = f"paragraph/{self.program_id}.{p_name}"
            parent = (
                f"section/{self.program_id}.{self.current_section}"
                if self.current_section
                else f"program/{self.program_id}"
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
        self.loop_count += 1
        cond_text = ctx.getText()
        curr_p = self.current_paragraph or "MAIN"
        key = f"loop/{self.program_id}.{curr_p}#{self.loop_count}"
        parent = self._get_current_parent_key()
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
            key = f"file/{self.program_id}.{f_name}"
            self._add_fact(
                "file_def",
                key,
                f"program/{self.program_id}",
                f_name,
                None,
                {},
                ctx.start.line,
                ctx.stop.line,
            )

    def enterFileDescriptionEntry(self, ctx: Any) -> None:
        if ctx.fileName():
            fd_name = ctx.fileName().getText().strip("'\"").upper()
            key = f"file/{self.program_id}.{fd_name}"
            self._add_fact(
                "fd",
                key,
                f"program/{self.program_id}",
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
        fact_type = "record" if level in (1, 77) else "field"
        key = (
            f"record/{self.program_id}.{name}"
            if fact_type == "record"
            else f"field/{self.program_id}.{name}"
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
            f"program/{self.program_id}",
            name,
            None,
            attrs,
            ctx.start.line,
            ctx.stop.line,
        )

    def enterIfStatement(self, ctx: Any) -> None:
        self.branch_count += 1
        curr_p = self.current_paragraph or "MAIN"
        key = f"branch/{self.program_id}.{curr_p}#{self.branch_count}"
        cond_text = ctx.condition().getText() if ctx.condition() else ""
        parent = self._get_current_parent_key()
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
        self.branch_count += 1
        curr_p = self.current_paragraph or "MAIN"
        key = f"branch/{self.program_id}.{curr_p}#{self.branch_count}"
        eval_text = ctx.getText()
        parent = self._get_current_parent_key()
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
        self.exec_count += 1
        curr_p = self.current_paragraph or "MAIN"
        key = f"exec_block/{self.program_id}.{curr_p}#{self.exec_count}"
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
        self.exec_count += 1
        curr_p = self.current_paragraph or "MAIN"
        key = f"exec_block/{self.program_id}.{curr_p}#{self.exec_count}"
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
        key = f"paragraph/{self.program_id}.{curr_p}"
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


def extract_cobol_enrichment_facts(
    content: str, rel_path: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract full sub-program facts and diagnostic record from COBOL code using ANTLR."""
    import time
    t0 = time.perf_counter()
    if not content or not content.strip():
        diag = {
            "rel_path": rel_path,
            "language": "cobol",
            "status": "ok",
            "error_count": 0,
            "first_error": None,
            "elapsed_ms": 0,
            "extractor_ver": "1.0.0",
        }
        return [], diag

    norm = normalize_cobol_text(content)

    # Preprocessor pass for COPY spans
    copy_spans: list[tuple[int, int]] = []
    try:
        lexer_p = Cobol85PreprocessorLexer(InputStream(norm))
        tokens_p = CommonTokenStream(lexer_p)
        parser_p = Cobol85PreprocessorParser(tokens_p)
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

    silent_listener = _SilentErrorListener()
    facts: list[dict[str, Any]] = []
    status = "ok"

    try:
        lexer = Cobol85Lexer(InputStream(norm_no_copy))
        lexer.removeErrorListeners()
        lexer.addErrorListener(silent_listener)

        tokens = CommonTokenStream(lexer)
        parser = Cobol85Parser(tokens)
        parser.removeErrorListeners()
        parser.addErrorListener(silent_listener)
        parser._interp.predictionMode = PredictionMode.SLL

        tree = parser.startRule()
        listener = CobolEnrichmentListener(rel_path)
        ParseTreeWalker().walk(listener, tree)
        facts = listener.facts
    except Exception:
        status = "failed"

    t1 = time.perf_counter()
    diag = {
        "rel_path": rel_path,
        "language": "cobol",
        "status": status,
        "error_count": 0,
        "first_error": None,
        "elapsed_ms": int((t1 - t0) * 1000),
        "extractor_ver": "1.0.0",
    }
    return facts, diag


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

        copy_match = re.search(r"\bCOPY\s+['\"]?([A-Za-z0-9_-]+)['\"]?", line, re.IGNORECASE)
        if copy_match:
            copybook = copy_match.group(1).upper()
            copies.append(CobolCopyFact(member=copybook, line=idx, resolution_method="cobol_copy_statement"))
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

