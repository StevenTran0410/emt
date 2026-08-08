"""Golden extraction unit tests for JCL parser."""
from pathlib import Path
from domain.structural_graph._jcl import extract_jcl_facts

CARDDEMO_JCL_PATH = Path(r"d:\Emt\emt_data\carddemo\app\jcl\CREASTMT.JCL")


def test_creastmt_jcl_extraction():
    content = CARDDEMO_JCL_PATH.read_text(encoding="utf-8")
    res = extract_jcl_facts(content)

    assert res.job_name == "CREASTMT"

    # Assert EXEC statements
    exec_map = {e.step_name: e.program_name for e in res.execs}
    assert exec_map.get("DELDEF01") == "IDCAMS"
    assert exec_map.get("STEP010") == "SORT"
    assert exec_map.get("STEP020") == "IDCAMS"
    assert exec_map.get("STEP030") == "IEFBR14"
    assert exec_map.get("STEP040") == "CBSTM03A"

    # Assert DD DSN bindings for STEP040
    step40_dds = {d.dd_name: d.dsn for d in res.dds if d.step_name == "STEP040"}
    assert step40_dds.get("STEPLIB") == "AWS.M2.CARDDEMO.LOADLIB"
    assert step40_dds.get("TRNXFILE") == "AWS.M2.CARDDEMO.TRXFL.VSAM.KSDS"
    assert step40_dds.get("XREFFILE") == "AWS.M2.CARDDEMO.CARDXREF.VSAM.KSDS"
    assert step40_dds.get("ACCTFILE") == "AWS.M2.CARDDEMO.ACCTDATA.VSAM.KSDS"
    assert step40_dds.get("CUSTFILE") == "AWS.M2.CARDDEMO.CUSTDATA.VSAM.KSDS"
    assert step40_dds.get("STMTFILE") == "AWS.M2.CARDDEMO.STATEMNT.PS"
    assert step40_dds.get("HTMLFILE") == "AWS.M2.CARDDEMO.STATEMNT.HTML"
