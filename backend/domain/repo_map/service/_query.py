"""Repo map read-side queries: summary, symbol listing, and CSV export."""
import json

from infrastructure.db.database import get_db
from shared.errors import NotFoundError

from ..types import (
    ExtractMode,
    ExtractSource,
    RepoMapCsvResponse,
    RepoMapSummary,
    SymbolKind,
    SymbolRecord,
    SymbolsResponse,
)
from ._records import _SYMBOL_COLS


async def _get_summary(snapshot_id: str) -> RepoMapSummary:
    q_maps = "SELECT * FROM repo_maps WHERE snapshot_id=?"
    async with get_db().execute(q_maps, (snapshot_id,)) as cur:
        row = await cur.fetchone()
    if row is None:
        raise NotFoundError("RepoMap", snapshot_id)
    return RepoMapSummary(
        snapshot_id=row["snapshot_id"],
        total_symbols=row["total_symbols"],
        files_indexed=row["files_indexed"],
        parse_failures=row["parse_failures"],
        extract_mode=ExtractMode(row["extract_mode"]),
        language_breakdown=json.loads(row["language_breakdown"] or "{}"),
        kind_breakdown=json.loads(row["kind_breakdown"] or "{}"),
        generated_at=row["generated_at"],
    )


async def _list_symbols(
    snapshot_id: str,
    limit: int = 500,
    path_prefix: str | None = None,
) -> SymbolsResponse:
    if path_prefix:
        query = f"""
            SELECT {_SYMBOL_COLS} FROM code_symbols
            WHERE snapshot_id=? AND rel_path LIKE ?
            ORDER BY rel_path ASC, line_start ASC
            LIMIT ?
        """
        params = (snapshot_id, f"{path_prefix}%", limit)
    else:
        query = f"""
            SELECT {_SYMBOL_COLS} FROM code_symbols
            WHERE snapshot_id=?
            ORDER BY rel_path ASC, line_start ASC
            LIMIT ?
        """
        params = (snapshot_id, limit)
    async with get_db().execute(query, params) as cur:
        rows = await cur.fetchall()
    return SymbolsResponse(
        snapshot_id=snapshot_id,
        symbols=[
            SymbolRecord(
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
            )
            for r in rows
        ],
    )


async def _export_csv(snapshot_id: str, exclude_tests: bool = True) -> RepoMapCsvResponse:
    q_exists = "SELECT 1 FROM repo_maps WHERE snapshot_id=?"
    async with get_db().execute(q_exists, (snapshot_id,)) as cur:
        exists = await cur.fetchone()
    if exists is None:
        raise NotFoundError("RepoMap", snapshot_id)

    async with get_db().execute(
        """
        SELECT DISTINCT rel_path, language, name, kind, line_start, line_end,
               parent_name, signature, extract_source
        FROM code_symbols
        WHERE snapshot_id=?
        ORDER BY rel_path ASC, line_start ASC, name ASC
        """,
        (snapshot_id,),
    ) as cur:
        rows = await cur.fetchall()

    if exclude_tests:
        def _is_test_path(rel_path: str) -> bool:
            low = rel_path.lower()
            return (
                "/test/" in low
                or "/tests/" in low
                or low.endswith("_test.py")
                or ".spec." in low
                or ".test." in low
            )

        rows = [r for r in rows if not _is_test_path(r["rel_path"])]

    def _esc(v: object | None) -> str:
        s = "" if v is None else str(v)
        s = s.replace('"', '""')
        return f'"{s}"'

    header = [
        "snapshot_id",
        "rel_path",
        "language",
        "name",
        "kind",
        "line_start",
        "line_end",
        "parent_name",
        "signature",
        "extract_source",
    ]
    lines = [",".join(header)]
    for r in rows:
        lines.append(
            ",".join(
                [
                    _esc(snapshot_id),
                    _esc(r["rel_path"]),
                    _esc(r["language"]),
                    _esc(r["name"]),
                    _esc(r["kind"]),
                    _esc(r["line_start"]),
                    _esc(r["line_end"]),
                    _esc(r["parent_name"]),
                    _esc(r["signature"]),
                    _esc(r["extract_source"]),
                ]
            )
        )

    return RepoMapCsvResponse(
        snapshot_id=snapshot_id,
        row_count=len(rows),
        csv="\n".join(lines) + ("\n" if lines else ""),
    )
