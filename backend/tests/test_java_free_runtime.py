"""Test that generated ANTLR COBOL parsers run in a Java-free environment."""
import os
import subprocess
import sys


def test_antlr_cobol_parser_java_free():
    # Remove Java path if present in PATH to simulate Java-free execution environment
    env = dict(os.environ)
    path_dirs = env.get("PATH", "").split(os.pathsep)
    filtered_dirs = [d for d in path_dirs if "java" not in d.lower() and "jdk" not in d.lower() and "jre" not in d.lower()]
    env["PATH"] = os.pathsep.join(filtered_dirs)

    script = """
import sys
sys.path.insert(0, r'd:\\Emt\\backend')
from domain.structural_graph._cobol import extract_cobol_facts

code = '''       IDENTIFICATION DIVISION.
       PROGRAM-ID. HELLO.
       PROCEDURE DIVISION.
           CALL 'WORLD'.
'''
res = extract_cobol_facts(code)
assert res.program_id == 'HELLO'
assert len(res.calls) == 1
assert res.calls[0].callee == 'WORLD'
print('SUCCESS')
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "SUCCESS" in proc.stdout
