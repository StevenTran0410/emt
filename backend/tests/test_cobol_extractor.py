"""Golden extraction unit tests for COBOL parser."""
from pathlib import Path
from domain.structural_graph._cobol import extract_cobol_facts

CARDDEMO_CBL_DIR = Path(r"d:\Emt\emt_data\carddemo\app\cbl")


def test_cbstm03a_extraction():
    cbl_path = CARDDEMO_CBL_DIR / "CBSTM03A.CBL"
    content = cbl_path.read_text(encoding="utf-8")
    res = extract_cobol_facts(content)

    assert res.program_id == "CBSTM03A"

    # Assert 13 CALLs to CBSTM03B and 1 to CEE3ABD
    cbstm03b_calls = [c for c in res.calls if c.callee == "CBSTM03B"]
    cee3abd_calls = [c for c in res.calls if c.callee == "CEE3ABD"]

    assert len(cbstm03b_calls) == 13
    assert len(cee3abd_calls) == 1
    assert len(res.calls) == 14

    # Assert 4 COPY statements
    copy_members = [c.member for c in res.copies]
    assert "COSTM01" in copy_members
    assert "CVACT03Y" in copy_members
    assert "CUSTREC" in copy_members
    assert "CVACT01Y" in copy_members
    assert len(res.copies) == 4


def test_cbact01c_sequence_numbered_extraction():
    cbl_path = CARDDEMO_CBL_DIR / "CBACT01C.cbl"
    content = cbl_path.read_text(encoding="utf-8")
    res = extract_cobol_facts(content)

    assert res.program_id == "CBACT01C"
