"""Repo map build/ingestion pipeline: file walk -> symbol extraction -> batched persistence."""
import json
from pathlib import Path

from domain.retrieval.bm25_scorer import split_identifier
from infrastructure.db.database import get_db
from shared.errors import NotFoundError
from shared.logger import logger
from shared.sql_queries import SQL_SELECT_MANIFEST_FILES_BY_SNAPSHOT
from shared.utils import new_id, read_utf8_lenient, utc_now_iso

from .._loaders import _Symbol
from .._normalizer import _dedupe_symbols
from .._walkers_regex import _extract_lexical_symbols, _extract_python_symbols_ast
from ..types import BuildRepoMapRequest, BuildRepoMapResponse, ExtractMode, RepoMapSummary
from ._extraction import _extract_symbols_treesitter, _has_treesitter_support
from ._records import make_qualified_name

# Batch size for executemany inserts into code_symbols.
_SYMBOL_BATCH_SIZE = 100


async def _flush_symbol_batch(db, batch: list[tuple]) -> None:
    """Insert a batch of symbol rows using executemany inside a single transaction.

    Each tuple must match the INSERT column order:
    (id, snapshot_id, rel_path, language, name, kind, line_start, line_end,
     signature, parent_name, extract_source, created_at, qualified_name)
    """
    if not batch:
        return
    await db.executemany(
        """
        INSERT INTO code_symbols
        (id, snapshot_id, rel_path, language, name, kind, line_start, line_end,
         signature, parent_name, extract_source, created_at, qualified_name)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        batch,
    )
    await db.commit()


async def _run_build(req: BuildRepoMapRequest) -> BuildRepoMapResponse:
    db = get_db()
    async with db.execute("SELECT * FROM repo_snapshots WHERE id=?", (req.snapshot_id,)) as cur:
        snap = await cur.fetchone()
    if snap is None:
        raise NotFoundError("RepoSnapshot", req.snapshot_id)

    root = Path(snap["local_path"])
    if not root.exists():
        raise ValueError("Snapshot path does not exist")

    if req.force_rebuild:
        await db.execute("DELETE FROM code_symbols WHERE snapshot_id=?", (req.snapshot_id,))
    else:
        async with db.execute(
            "SELECT COUNT(*) as c FROM code_symbols WHERE snapshot_id=?",
            (req.snapshot_id,),
        ) as cur:
            row = await cur.fetchone()
        if row and row["c"] > 0:
            logger.info(
                "[repo_map] snapshot %s already indexed (%d symbols), skipping",
                req.snapshot_id, row["c"],
            )
            return BuildRepoMapResponse(
                summary=RepoMapSummary(
                    snapshot_id=req.snapshot_id,
                    total_symbols=int(row["c"]),
                    files_indexed=0,
                    parse_failures=0,
                    extract_mode=ExtractMode.HYBRID,
                    language_breakdown={},
                    kind_breakdown={},
                    generated_at="cached",
                )
            )

    async with db.execute(
        SQL_SELECT_MANIFEST_FILES_BY_SNAPSHOT,
        (req.snapshot_id,),
    ) as cur:
        files = await cur.fetchall()

    files_indexed = 0
    parse_failures = 0
    total_symbols = 0
    inserted_keys: set[tuple[str, str, str, int, int, str | None]] = set()
    lang_breakdown: dict[str, int] = {}
    kind_breakdown: dict[str, int] = {}
    used_structural = 0
    now = utc_now_iso()

    # Accumulate rows for batch insert; flushed every _SYMBOL_BATCH_SIZE rows.
    pending_batch: list[tuple] = []
    vocab_batch: list[tuple[str, str, str]] = []

    for row in files:
        rel_path = row["rel_path"]
        language = row["language"]
        category = row["category"]
        if category not in {"source", "test", "infra"}:
            continue

        file_path = (root / rel_path).resolve()
        if not file_path.exists() or not file_path.is_file():
            continue
        content = read_utf8_lenient(file_path)
        if not content:
            continue

        files_indexed += 1
        symbols: list[_Symbol]
        try:
            if language == "python":
                try:
                    symbols = _extract_python_symbols_ast(content)
                    used_structural += 1
                except SyntaxError:
                    symbols = _extract_symbols_treesitter(content, "python")
                    if symbols:
                        used_structural += 1
                    else:
                        parse_failures += 1
                        symbols = _extract_lexical_symbols(content, language)
            else:
                symbols = _extract_symbols_treesitter(content, language or "")
                if symbols:
                    used_structural += 1
                else:
                    if _has_treesitter_support(language):
                        parse_failures += 1
                    symbols = _extract_lexical_symbols(content, language)
        except Exception:
            parse_failures += 1
            continue

        symbols = _dedupe_symbols(symbols)[:2000]
        if not symbols:
            continue

        lang_key = language or "unknown"
        lang_breakdown[lang_key] = lang_breakdown.get(lang_key, 0) + len(symbols)

        for name, kind, line_start, line_end, signature, parent_name, extract_source in symbols:
            dedupe_key = (rel_path, name, kind.value, line_start, line_end, parent_name)
            if dedupe_key in inserted_keys:
                continue
            inserted_keys.add(dedupe_key)
            qname = make_qualified_name(rel_path, name, parent_name)
            pending_batch.append((
                new_id(),
                req.snapshot_id,
                rel_path,
                language,
                name,
                kind.value,
                line_start,
                line_end,
                signature,
                parent_name,
                extract_source.value,
                now,
                qname,
            ))
            total_symbols += 1
            kind_breakdown[kind.value] = kind_breakdown.get(kind.value, 0) + 1

            if kind.value not in ("file", "module"):
                for seg in split_identifier(name):
                    vocab_batch.append((req.snapshot_id, seg, name))

            if len(pending_batch) >= _SYMBOL_BATCH_SIZE:
                await _flush_symbol_batch(db, pending_batch)
                pending_batch = []

    # Flush any remaining rows.
    if pending_batch:
        await _flush_symbol_batch(db, pending_batch)

    if vocab_batch:
        try:
            await db.executemany(
                "INSERT OR IGNORE INTO name_segment_vocab (snapshot_id, segment, name) VALUES (?, ?, ?)",
                vocab_batch,
            )
            await db.commit()
        except Exception:
            logger.exception("Failed to flush name_segment_vocab batch for %s", req.snapshot_id)

    extract_mode = ExtractMode.HYBRID if used_structural > 0 else ExtractMode.LEXICAL

    await db.execute("DELETE FROM repo_maps WHERE snapshot_id=?", (req.snapshot_id,))
    await db.execute(
        """
        INSERT INTO repo_maps
        (snapshot_id, total_symbols, files_indexed, parse_failures, extract_mode,
         language_breakdown, kind_breakdown, generated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            req.snapshot_id,
            total_symbols,
            files_indexed,
            parse_failures,
            extract_mode.value,
            json.dumps(lang_breakdown),
            json.dumps(kind_breakdown),
            now,
        ),
    )
    await db.commit()

    return BuildRepoMapResponse(
        summary=RepoMapSummary(
            snapshot_id=req.snapshot_id,
            total_symbols=total_symbols,
            files_indexed=files_indexed,
            parse_failures=parse_failures,
            extract_mode=extract_mode,
            language_breakdown=lang_breakdown,
            kind_breakdown=kind_breakdown,
            generated_at=now,
        )
    )
