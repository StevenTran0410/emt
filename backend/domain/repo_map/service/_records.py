"""Symbol row <-> model mapping and qualified-name construction."""
from ..types import ExtractSource, SymbolKind, SymbolRecord


def make_qualified_name(rel_path: str, name: str, parent_name: str | None = None) -> str:
    """Standard FQN identity convention: rel_path::name (for class/function) or rel_path::parent_name.name (for method)."""
    p = f"{parent_name}." if parent_name else ""
    return f"{rel_path}::{p}{name}"


# Column list for code_symbols SELECT queries — avoids pulling all columns when
# only a subset is needed. Explicit columns are cheaper and document intent.
_SYMBOL_COLS = (
    "id, snapshot_id, rel_path, language, name, kind, "
    "line_start, line_end, signature, parent_name, extract_source, qualified_name"
)


def _symbol_record_from_row(r) -> SymbolRecord:
    """Build a SymbolRecord from a code_symbols row, deriving qualified_name for rows predating that column."""
    return SymbolRecord(
        id=r["id"],
        snapshot_id=r["snapshot_id"],
        rel_path=r["rel_path"],
        language=r["language"],
        name=r["name"],
        kind=SymbolKind(r["kind"]),
        line_start=r["line_start"],
        line_end=r["line_end"],
        signature=r["signature"],
        parent_name=r["parent_name"],
        extract_source=ExtractSource(
            r["extract_source"] if r["extract_source"] else "lexical"
        ),
        qualified_name=r["qualified_name"] if "qualified_name" in r.keys() else make_qualified_name(r["rel_path"], r["name"], r["parent_name"]),
    )
