"""Script to port JCLLexer.g4 from Java actions to Python 3 actions."""

import re
from pathlib import Path

HERE = Path(__file__).parent


def fix_python_indentation(code_str: str) -> str:
    lines = [l.strip() for l in code_str.splitlines() if l.strip() and l.strip() != "}"]
    out_lines = []
    current_indent = 0

    for l in lines:
        if l.startswith("elif ") or l.startswith("else:") or l.startswith("except ") or l.startswith("finally:"):
            indent = max(0, current_indent - 4)
        else:
            indent = current_indent

        out_lines.append(" " * indent + l)

        if l.endswith(":"):
            current_indent = indent + 4

    return "\n".join(out_lines)


def port_jcl_lexer(java_g4_text: str) -> str:
    text = java_g4_text.replace("\r\n", "\n")

    members_new = """@lexer::members {
dlmVals = []
dlmString = None
myMode = 0
inExecPgmMode = False
}"""

    # 1. Replace @lexer::members block first
    text = re.sub(r"@lexer::members\s*\{.*?\}", members_new, text, flags=re.DOTALL)

    # 2. Replace specific multi-line switch blocks
    block63_pattern = r"switch\(_modeStack\.peek\(\)\)\s*\{.*?default :\s*popMode\(\);\s*break;\s*\}"
    block63_py = """top = self._modeStack[-1] if self._modeStack else None
if top in (self.KYWD_VAL_MODE, self.DCB_MODE):
    self.popMode()
    self.popMode()
elif top == self.DLM_MODE:
    self.dlmVals = [self.dlmString]
    self.popMode()
    self.popMode()
else:
    self.popMode()"""

    block64_pattern = r"switch\(_modeStack\.peek\(\)\)\s*\{.*?default :\s*break;\s*\}"
    block64_py = """top = self._modeStack[-1] if self._modeStack else None
if top in (self.JOB_PROGRAMMER_NAME_MODE, self.JOBGROUP_PROGRAMMER_NAME_MODE):
    self.type = self.QUOTED_STRING_PROGRAMMER_NAME
elif top == self.DLM_MODE:
    self.dlmString += self.text"""

    text = re.sub(block63_pattern, block63_py, text, flags=re.DOTALL)
    text = re.sub(block64_pattern, block64_py, text, flags=re.DOTALL)

    out = []
    i = 0
    n = len(text)

    while i < n:
        if text[i : i + 15] == "@lexer::members":
            start_brace = text.find("{", i)
            depth = 1
            idx = start_brace + 1
            while idx < n and depth > 0:
                if text[idx] == "{":
                    depth += 1
                elif text[idx] == "}":
                    depth -= 1
                idx += 1
            out.append(text[i:idx])
            i = idx
            continue

        if text[i] == "{" and (i == 0 or text[i - 1] != "\\"):
            depth = 1
            idx = i + 1
            while idx < n and depth > 0:
                if text[idx] == "{":
                    depth += 1
                elif text[idx] == "}":
                    depth -= 1
                idx += 1

            body = text[i + 1 : idx - 1]
            rest_of_text = text[idx:]
            is_predicate = rest_of_text.lstrip().startswith("?")

            # Port body
            cleaned = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)

            code = cleaned
            code = code.replace("dlmVals = new java.util.ArrayList();", "self.dlmVals = []")
            code = code.replace('dlmVals.add("/*");', 'self.dlmVals.append("/*")')
            code = code.replace('dlmVals.add("//");', 'self.dlmVals.append("//")')
            code = code.replace("dlmVals.add(getText());", "self.dlmVals.append(self.text)")
            code = code.replace("dlmVals.add(dlmString);", "self.dlmVals.append(self.dlmString)")
            code = code.replace('dlmVals.contains("//")', '"//" in self.dlmVals')
            code = code.replace('dlmVals.contains("/*")', '"/*" in self.dlmVals')
            code = code.replace("dlmVals.contains(getText())", "self.text in self.dlmVals")
            code = code.replace("dlmVals.contains(self.text)", "self.text in self.dlmVals")

            code = code.replace("dlmString = new String();", 'self.dlmString = ""')
            code = code.replace(
                "dlmString = dlmString.concat(getText());",
                "self.dlmString += self.text",
            )

            code = code.replace("myMode = DEFAULT_MODE;", "self.myMode = 0")
            code = code.replace("myMode = DATA_MODE;", "self.myMode = self.DATA_MODE")
            code = code.replace("inExecPgmMode = false;", "self.inExecPgmMode = False")
            code = code.replace("inExecPgmMode = true;", "self.inExecPgmMode = True")
            code = code.replace("!inExecPgmMode", "not self.inExecPgmMode")

            code = code.replace("getCharPositionInLine()", "self.column")
            code = code.replace("getText().length()", "len(self.text)")
            code = code.replace("getText()", "self.text")
            code = code.replace("_modeStack.clear();", "self._modeStack.clear()")
            code = code.replace("mode(myMode);", "self.mode(self.myMode);")

            # Specific Java if/else in KYWD_VAL_RPAREN
            code = code.replace(
                "if (_modeStack.peek() == DCB_PAREN_MODE) {",
                "top = self._modeStack[-1] if self._modeStack else None\nif top == self.DCB_PAREN_MODE:",
            )
            code = code.replace("} else {", "else:")
            code = code.replace("popMode();", "self.popMode()")
            code = re.sub(r"\bpushMode\((.*?)\);", r"self.pushMode(\1)", code)
            code = re.sub(r"\bsetType\((.*?)\);", r"self.type = self.\1", code)
            code = code.replace("&&", "and").replace("||", "or")

            # Format code with proper Python indentation
            code = fix_python_indentation(code)

            if not code.strip():
                code = "pass"

            if is_predicate:
                out.append(f"{{{code.strip()}}}")
            else:
                out.append(f"{{\n{code}\n}}")
            i = idx
        else:
            out.append(text[i])
            i += 1

    return "".join(out)


def main() -> None:
    raw_file = HERE / "JCLLexer_raw.g4"
    if not raw_file.exists():
        raw_file = HERE / "JCLLexer.g4"
    content = raw_file.read_text(encoding="utf-8")
    ported = port_jcl_lexer(content)
    (HERE / "JCLLexer.g4").write_text(ported, encoding="utf-8")
    print(f"Ported {len(ported.splitlines())} lines to JCLLexer.g4")


if __name__ == "__main__":
    main()
