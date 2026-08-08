"""Unit tests for COBOL ANSI-85 fixed format column normalizer."""
from domain.structural_graph._cobol.preprocess import normalize_cobol_text


def test_column_normalizer_basic():
    raw_code = (
        "000100 IDENTIFICATION DIVISION.\n"
        "000200 PROGRAM-ID. TESTPROG.\n"
        "000300* THIS IS A COMMENT LINE\n"
        "000400 WORKING-STORAGE SECTION.\n"
    )
    norm = normalize_cobol_text(raw_code)
    lines = norm.splitlines()

    assert len(lines) == 4
    assert lines[0] == "IDENTIFICATION DIVISION."
    assert lines[1] == "PROGRAM-ID. TESTPROG."
    assert lines[2] == ""  # comment line replaced with blank to preserve line mapping
    assert lines[3] == "WORKING-STORAGE SECTION."


def test_column_normalizer_indicator_characters():
    raw_code = (
        "000100* Comment line with asterisk\n"
        "000200/ Comment line with slash\n"
        "000300D Debug line\n"
        "000400 NORMAL LINE HERE\n"
    )
    norm = normalize_cobol_text(raw_code)
    lines = norm.splitlines()

    assert len(lines) == 4
    assert lines[0] == ""
    assert lines[1] == ""
    assert lines[2] == ""
    assert lines[3] == "NORMAL LINE HERE"


def test_column_normalizer_continuation_line():
    raw_code = (
        "000100       VALUE 'THIS IS A LONG STRING LITERAL THAT CONTINUES ON NEXT \n"
        "000200-             'LINE HERE'.\n"
    )
    norm = normalize_cobol_text(raw_code)
    lines = norm.splitlines()

    assert len(lines) == 1
    assert "THIS IS A LONG STRING LITERAL THAT CONTINUES ON NEXTLINE HERE'." in lines[0]
