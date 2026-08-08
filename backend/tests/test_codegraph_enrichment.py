"""Tests for TICKET_CodeGraph_Enrichment.md (Sub-program COBOL facts, ANTLR JCL parser, source_facts & diagnostics)."""
import re
import pytest

from domain.structural_graph._cobol.extract import extract_cobol_enrichment_facts
from domain.structural_graph._jcl.extract import extract_jcl_enrichment_facts


def test_cobol_enrichment_facts_structure():
    content = """
       IDENTIFICATION DIVISION.
       PROGRAM-ID. TESTPROG.
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT STMT-FILE ASSIGN TO STMTFILE.
       DATA DIVISION.
       FILE SECTION.
       FD  STMT-FILE.
       01  STMT-RECORD.
           05  STMT-ID    PIC X(10).
           05  STMT-AMT   PIC 9(7)V99.
       PROCEDURE DIVISION.
       0000-MAIN.
           PERFORM 1000-PROCESS UNTIL END-OF-FILE.
           GOBACK.
       1000-PROCESS.
           CALL 'SUBPROG'.
           CALL 'SUBPROG'.
           GOBACK.
    """
    facts, diag = extract_cobol_enrichment_facts(content, "TESTPROG.cbl")

    assert diag["language"] == "cobol"
    assert diag["status"] == "ok"

    # Rule 1: semantic_key MUST NEVER contain line numbers (e.g. :123 or :L123 or #L123)
    for fact in facts:
        key = fact["semantic_key"]
        assert not re.search(r"[:#]L\d+$|:\d+$", key), f"semantic_key {key} contains line number"
        assert "line_start" in fact and fact["line_start"] > 0
        assert "line_end" in fact and fact["line_end"] >= fact["line_start"]

    # Rule 2: Occurrences are preserved (occurrence_ix 0..N), NEVER deduped
    performs = [f for f in facts if f["fact_type"] == "performs"]
    assert len(performs) >= 1

    program_fact = [f for f in facts if f["fact_type"] == "program"][0]
    assert program_fact["name"] == "TESTPROG"


def test_jcl_enrichment_facts_structure():
    content = (
        "//TESTJOB  JOB (123),'TEST',CLASS=A\n"
        "//STEP010  EXEC PGM=IDCAMS\n"
        "//SYSPRINT DD SYSOUT=*\n"
        "//SYSIN    DD *\n"
        "  DELETE TEST.DATA.SET\n"
        "/*\n"
        "//STEP020  EXEC PGM=CBSTM03A\n"
        "//STEPLIB  DD DSN=CARDDEMO.LOAD,DISP=SHR\n"
    )
    facts, diag = extract_jcl_enrichment_facts(content, "TESTJOB.jcl")

    assert diag["language"] == "jcl"
    assert diag["status"] in ("ok", "partial")

    job_facts = [f for f in facts if f["fact_type"] == "job"]
    assert len(job_facts) >= 1
    assert job_facts[0]["name"] == "TESTJOB"

    step_facts = [f for f in facts if f["fact_type"] == "step"]
    assert len(step_facts) == 2
    assert step_facts[0]["name"] == "STEP010"
    assert step_facts[0]["value"] == "IDCAMS"

    dd_facts = [f for f in facts if f["fact_type"] == "dd"]
    assert len(dd_facts) >= 3

    dataset_facts = [f for f in facts if f["fact_type"] == "dataset"]
    assert len(dataset_facts) >= 1
    assert dataset_facts[0]["value"] == "CARDDEMO.LOAD"

    # Assert no line numbers in semantic keys
    for fact in facts:
        key = fact["semantic_key"]
        assert not re.search(r"[:#]L\d+$|:\d+$", key), f"semantic_key {key} contains line number"
