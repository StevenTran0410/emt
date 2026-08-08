"""Pre-pass filters: applied to source before AST chunking."""
from __future__ import annotations

import re
from typing import Callable


def _strip_license_header_pass(source: str, language: str) -> tuple[str, str | None]:
    """Strip license/copyright headers from top of file.

    Returns: (modified_source, chunk_type_override).
    Detects SPDX headers or 'Copyright...Licensed under' blocks in first 100 lines.
    """
    lines = source.split("\n")
    cap_lines = min(100, len(lines))

    # SPDX header pattern: SPDX-License-Identifier: <id>
    spdx_pattern = re.compile(r"SPDX-License-Identifier:", re.IGNORECASE)

    # Copyright + Licensed pattern: "Copyright (c)" and "Licensed under" (can be far apart)
    copyright_pattern = re.compile(r"Copyright\s*\(c\)", re.IGNORECASE)
    licensed_pattern = re.compile(r"Licensed\s+under", re.IGNORECASE)

    # Find range to strip: either SPDX line, or Copyright...Licensed block.
    strip_until = 0
    for i in range(cap_lines):
        line = lines[i]
        if spdx_pattern.search(line):
            strip_until = i + 1
            break

    if strip_until == 0:
        # Look for Copyright ... Licensed pattern.
        has_copyright = False
        for i in range(cap_lines):
            if copyright_pattern.search(lines[i]):
                has_copyright = True
                # Scan forward for "Licensed under" within next 20 lines.
                for j in range(i, min(i + 20, cap_lines)):
                    if licensed_pattern.search(lines[j]):
                        strip_until = j + 1
                        break
                break
        if not has_copyright:
            strip_until = 0

    if strip_until > 0:
        return "\n".join(lines[strip_until:]), None
    return source, None


def _detect_barrel_pass(source: str, language: str) -> tuple[str, str | None]:
    """Detect if file is a barrel (>80% re-export statements).

    Returns: (source, 'barrel' if matches else None).
    Checks for re-export patterns like 'export * from', 'pub use', 'from X import Y as Z'.
    """
    lines = source.split("\n")
    lang_key = (language or "").lower()

    # Patterns for different languages.
    if lang_key in {"typescript", "javascript"}:
        export_pattern = re.compile(r"^\s*export\s+\*\s+from\s+", re.IGNORECASE)
        reexport_pattern = re.compile(r"^\s*export\s+", re.IGNORECASE)
    elif lang_key == "rust":
        export_pattern = re.compile(r"^\s*pub\s+use\s+")
        reexport_pattern = re.compile(r"^\s*pub\s+use\s+")
    elif lang_key in {"python"}:
        export_pattern = re.compile(r"^\s*from\s+\S+\s+import\s+\S+\s+as\s+\w+")
        reexport_pattern = re.compile(r"^\s*from\s+\S+\s+import")
    else:
        # No barrel detection for unsupported languages.
        return source, None

    non_blank_count = 0
    export_count = 0

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("//"):
            # Blank or comment — skip.
            continue

        non_blank_count += 1

        if export_pattern.search(line) or (
            "export" in line.lower() and reexport_pattern.search(line)
        ) or reexport_pattern.search(line):
            export_count += 1

    if non_blank_count > 0 and export_count / non_blank_count >= 0.8:
        return "", "barrel"

    return source, None


# List of pre-pass callables, applied in order before AST chunking.
# Each callable: (source: str, language: str) -> (source: str, chunk_type_override: str | None)
_PRE_PASSES: list[Callable[[str, str], tuple[str, str | None]]] = [
    _strip_license_header_pass,
    _detect_barrel_pass,
]
