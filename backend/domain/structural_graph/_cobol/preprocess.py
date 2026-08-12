"""Fixed-format COBOL column normalizer.

Normalizes fixed-format ANSI-85 COBOL code by discarding columns 1-6 (sequence numbers),
handling column 7 indicator characters (*, /, D, -), slicing columns 8-72 for program text,
and stitching continuation lines.

Preserves 1-to-1 line mapping so ANTLR token line numbers match original file physical line numbers.
"""
from __future__ import annotations

import re

# IDENTIFICATION-DIVISION comment-entry paragraphs. Their bodies are free-form
# comment text (e.g. "AUTHOR. AWS."), not COBOL. The grammars-v4 Cobol85 grammar
# assumes such entries were removed by a full preprocessor; left in, they abort the
# parse before it reaches PROCEDURE DIVISION (mismatched input). Matched at the
# start of a normalized line so genuine data items are never blanked.
_COMMENT_ENTRY_PARAGRAPHS = (
    "AUTHOR.",
    "INSTALLATION.",
    "DATE-WRITTEN.",
    "DATE-COMPILED.",
    "SECURITY.",
    "REMARKS.",
)


def _strip_floating_comment(code_area: str) -> str:
    """Strip a GnuCOBOL-style '*>' floating inline comment from a code-area slice.

    Real mainframe sources in this corpus use '*>' anywhere in Area A/B (not just
    column 7) as a trailing comment marker, typically followed by half-width
    Japanese katakana annotations. Left in place, that text reaches the ANTLR
    lexer, which has no token rule for it and aborts with a token recognition
    error for every character - poisoning the whole downstream parse. Scanned
    outside quoted literals so a literal that happens to contain '*>' survives.
    """
    in_quote: str | None = None
    for i, ch in enumerate(code_area):
        if in_quote:
            if ch == in_quote:
                in_quote = None
            continue
        if ch in ("'", '"'):
            in_quote = ch
            continue
        if ch == "*" and code_area[i + 1 : i + 2] == ">":
            return code_area[:i]
    return code_area


def normalize_cobol_text(raw_text: str, allow_debug: bool = False) -> str:
    """Normalize raw COBOL source code into ANTLR-parseable text.

    Each line in the returned text corresponds 1-to-1 with the line in raw_text.
    Comment lines and debug lines are replaced with blank lines to preserve line numbers.
    Continuation lines ('-' in col 7) are appended to the preceding non-blank line, leaving
    the continuation line blank to preserve line mapping.
    """
    lines = raw_text.splitlines()

    raw_normalized: list[tuple[str, str]] = []

    for line in lines:
        if len(line) < 7:
            raw_normalized.append(("", ""))
            continue

        indicator = line[6]

        # Column 7 indicator checks:
        # '*' or '/': Comment line -> blank line
        if indicator in ("*", "/"):
            raw_normalized.append(("", indicator))
            continue

        # 'D' or 'd': Debug line -> blank unless debug enabled
        if indicator in ("D", "d") and not allow_debug:
            raw_normalized.append(("", indicator))
            continue

        # Take Area A + Area B: columns 8 to 72 (0-indexed slice 7:72)
        code_area = _strip_floating_comment(line[7:72])
        raw_normalized.append((code_area, indicator))

    final_lines = [item[0] for item in raw_normalized]

    # Stitch continuation lines
    for i in range(1, len(raw_normalized)):
        text, ind = raw_normalized[i]
        if ind == "-":
            prev_idx = i - 1
            while prev_idx >= 0 and final_lines[prev_idx] == "":
                prev_idx -= 1
            if prev_idx >= 0:
                cont_text = text.lstrip()
                if cont_text.startswith("'") or cont_text.startswith('"'):
                    cont_text = cont_text[1:]
                final_lines[prev_idx] += cont_text
                final_lines[i] = ""

    # Blank single-line comment-entry paragraphs (see note above). Multi-line
    # comment entries are a documented limitation of this MVP normalizer.
    for i, ln in enumerate(final_lines):
        stripped = ln.lstrip().upper()
        if any(stripped.startswith(p) for p in _COMMENT_ENTRY_PARAGRAPHS):
            final_lines[i] = ""

    return "\n".join(final_lines)


_IDENTIFICATION_DIVISION_RE = re.compile(r"\b(?:IDENTIFICATION|ID)\s+DIVISION\b", re.IGNORECASE)


def is_copybook_shaped(raw_text: str) -> bool:
    """True for bare-copybook COBOL source: no IDENTIFICATION/ID DIVISION header.

    Every real COBOL-85 program opens with one; COPY members (copybooks) are
    plain record layouts and never do, regardless of file extension (.cpy,
    .cob, .dcl, or even .cbl). Content-sniffed rather than extension-based so
    misnamed copybooks (e.g. shipped as .cob) are still detected correctly.
    """
    if not raw_text or not raw_text.strip():
        return False
    normalized = normalize_cobol_text(raw_text)
    return _IDENTIFICATION_DIVISION_RE.search(normalized) is None
