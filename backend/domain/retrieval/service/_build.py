"""RetrievalService mixin: index summary + build (chunking, persistence, BM25 stats)."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from infrastructure.db.database import get_db
from shared.errors import NotFoundError
from shared.logger import logger
from shared.sql_queries import SQL_SELECT_MANIFEST_FILES_BY_SNAPSHOT
from shared.utils import new_id, read_utf8_lenient, utc_now_iso

from ..bm25_scorer import BM25Scorer
from ..chunker_ast import ASTChunk
from ..types import BuildRetrievalIndexRequest, BuildRetrievalIndexResponse, RetrievalSummary
from ._chunking import _chunk_size_for, _compute_content_hash, _normalize_text, _split_chunks, _token_estimate
from ._constants import _AST_LANGS, _WORD, _ast_chunker
from ._db_ops import _apply_incremental_idf_update, _batch_insert_chunks


class _IndexBuildMixin:
    async def summary(self, snapshot_id: str) -> RetrievalSummary:
        db = get_db()
        async with db.execute(
            "SELECT COUNT(*) as c FROM retrieval_chunks WHERE snapshot_id=?", (snapshot_id,)
        ) as cur:
            row = await cur.fetchone()
        chunk_count = int(row["c"]) if row else 0
        async with db.execute(
            "SELECT 1 FROM retrieval_bm25_stats WHERE snapshot_id=?", (snapshot_id,)
        ) as cur:
            has_bm25_stats = (await cur.fetchone()) is not None
        return RetrievalSummary(
            snapshot_id=snapshot_id,
            chunk_count=chunk_count,
            has_bm25_stats=has_bm25_stats,
            built=chunk_count > 0 and has_bm25_stats,
        )

    async def build_index(self, req: BuildRetrievalIndexRequest) -> BuildRetrievalIndexResponse:
        db = get_db()
        async with db.execute(
            "SELECT local_path FROM repo_snapshots WHERE id=?", (req.snapshot_id,)
        ) as cur:
            snap = await cur.fetchone()
        if snap is None:
            raise NotFoundError("RepoSnapshot", req.snapshot_id)
        root = Path(snap["local_path"])
        if not root.exists():
            raise ValueError("Snapshot path does not exist")

        if req.force_rebuild:
            await db.execute("DELETE FROM retrieval_chunks WHERE snapshot_id=?", (req.snapshot_id,))
            await db.execute(
                "DELETE FROM retrieval_indexes WHERE snapshot_id=?", (req.snapshot_id,)
            )
            await db.execute(
                "DELETE FROM retrieval_bm25_stats WHERE snapshot_id=?", (req.snapshot_id,)
            )
        else:
            async with db.execute(
                "SELECT COUNT(*) as c FROM retrieval_chunks WHERE snapshot_id=?",
                (req.snapshot_id,),
            ) as cur:
                row = await cur.fetchone()
            if row and row["c"] > 0:
                # Chunks exist — also check BM25 stats are present (may be missing for old snapshots).
                async with db.execute(
                    "SELECT 1 FROM retrieval_bm25_stats WHERE snapshot_id=?",
                    (req.snapshot_id,),
                ) as cur:
                    bm25_exists = await cur.fetchone()
                if bm25_exists:
                    logger.debug(
                        "[retrieval] snapshot %s already indexed (%d chunks), skipping",
                        req.snapshot_id,
                        row["c"],
                    )
                    return BuildRetrievalIndexResponse(
                        snapshot_id=req.snapshot_id,
                        chunk_count=int(row["c"]),
                        files_indexed=0,
                        generated_at="cached",
                    )
                # BM25 stats missing — rebuild from existing chunks without re-chunking.
                logger.debug(
                    "[retrieval] snapshot %s has %d chunks but no BM25 stats — rebuilding stats only",
                    req.snapshot_id,
                    row["c"],
                )
                async with db.execute(
                    "SELECT content FROM retrieval_chunks WHERE snapshot_id=?",
                    (req.snapshot_id,),
                ) as cur:
                    chunk_rows = await cur.fetchall()
                cached_tokenized_corpus = [_WORD.findall(r["content"].lower()) for r in chunk_rows]
                if cached_tokenized_corpus:
                    avgdl = sum(len(t) for t in cached_tokenized_corpus) / len(cached_tokenized_corpus)
                    idf = BM25Scorer.build_idf(cached_tokenized_corpus, len(cached_tokenized_corpus))
                    now_str = datetime.utcnow().isoformat()
                    await db.execute(
                        """INSERT OR REPLACE INTO retrieval_bm25_stats
                           (snapshot_id, chunk_count, avgdl, idf_json, k1, b, generated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            req.snapshot_id,
                            len(cached_tokenized_corpus),
                            avgdl,
                            json.dumps(idf),
                            2.0,
                            0.75,
                            now_str,
                        ),
                    )
                    await db.commit()
                    logger.info(
                        "BM25 stats rebuilt: %d docs, avgdl=%.1f, vocab=%d",
                        len(cached_tokenized_corpus),
                        avgdl,
                        len(idf),
                    )
                return BuildRetrievalIndexResponse(
                    snapshot_id=req.snapshot_id,
                    chunk_count=int(row["c"]),
                    files_indexed=0,
                    generated_at="rebuilt-bm25",
                )

        async with db.execute(SQL_SELECT_MANIFEST_FILES_BY_SNAPSHOT, (req.snapshot_id,)) as cur:
            files = await cur.fetchall()

        now = utc_now_iso()
        files_indexed = 0
        chunk_count = 0
        tokenized_corpus: list[list[str]] = []
        chunk_hash_map: dict[str, str] = {}  # chunk_id -> content_hash

        # Flush rows per file to avoid holding a very large in-memory list.
        for f in files:
            rel_path = f["rel_path"]
            language = f["language"]
            category = f["category"]
            if category in {"generated", "asset", "secret-risk", "other"}:
                continue
            p = root / rel_path
            if not p.exists() or not p.is_file():
                continue
            text = read_utf8_lenient(p)
            if not text.strip():
                continue
            target_size = _chunk_size_for(category, language)
            lang_key = (language or "").lower()
            if lang_key in _AST_LANGS and category not in {"docs", "config"}:
                # AST chunking: operate on raw source — normalization after.
                ast_chunks = _ast_chunker.chunk(text, lang_key, target_size)
                ast_pieces: list[tuple[ASTChunk | None, str]] = [(c, _normalize_text(c.text)) for c in ast_chunks]
            else:
                ast_pieces = [
                    (None, piece) for piece in _split_chunks(_normalize_text(text), target_size)
                ]
            files_indexed += 1

            file_chunk_rows: list[tuple] = []
            file_index_rows: list[tuple] = []
            file_token_rows: list[tuple] = []
            for i, (ast_chunk, piece) in enumerate(ast_pieces):
                token_est = _token_estimate(piece)
                preview = piece[:500]
                # Extract chunk_type and line range from AST chunk if available
                chunk_type = ast_chunk.chunk_type if ast_chunk else "block"
                start_line = ast_chunk.start_line if ast_chunk else 0
                end_line = ast_chunk.end_line if ast_chunk else 0
                split_part = ast_chunk.split_part if ast_chunk else 0
                split_of = ast_chunk.split_of if ast_chunk else 1
                # Collect tokenized document for BM25 IDF computation
                tokens = _WORD.findall(piece.lower())
                tokenized_corpus.append(tokens)
                chunk_id = new_id()
                content_hash = _compute_content_hash(piece)
                chunk_hash_map[chunk_id] = content_hash  # Track for incremental IDF
                file_chunk_rows.append(
                    (
                        chunk_id,
                        req.snapshot_id,
                        rel_path,
                        language,
                        category,
                        i,
                        piece,
                        token_est,
                        chunk_type,
                        start_line,
                        end_line,
                        split_part,
                        split_of,
                        content_hash,
                        now,
                    )
                )
                # Minimal lexical index row (prefix preview for quick debug/search metadata).
                file_index_rows.append(
                    (
                        new_id(),
                        req.snapshot_id,
                        rel_path,
                        i,
                        preview,
                        now,
                    )
                )
                # Token frequency rows for incremental IDF updates.
                token_freq = Counter(tokens)
                for term, tf in token_freq.items():
                    file_token_rows.append((chunk_id, term, int(tf)))
                chunk_count += 1

            await _batch_insert_chunks(db, file_chunk_rows, file_index_rows)

            # Batch insert token frequencies.
            if file_token_rows:
                await db.executemany(
                    """INSERT OR IGNORE INTO retrieval_chunk_tokens
                       (chunk_id, term, tf) VALUES (?, ?, ?)""",
                    file_token_rows,
                )

        # Compute and store BM25 IDF stats with incremental tracking.
        if tokenized_corpus:
            avgdl = sum(len(t) for t in tokenized_corpus) / len(tokenized_corpus)
            idf, next_index_version = await _apply_incremental_idf_update(
                db, req.snapshot_id, chunk_hash_map, tokenized_corpus, avgdl
            )
            now_str = datetime.utcnow().isoformat()
            await db.execute(
                """INSERT OR REPLACE INTO retrieval_bm25_stats
                   (snapshot_id, chunk_count, avgdl, idf_json, k1, b, index_version, generated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    req.snapshot_id,
                    len(tokenized_corpus),
                    avgdl,
                    json.dumps(idf),
                    2.0,
                    0.75,
                    next_index_version,
                    now_str,
                ),
            )
            logger.info(
                "BM25 stats built: %d docs, avgdl=%.1f, vocab=%d, index_version=%d",
                len(tokenized_corpus),
                avgdl,
                len(idf),
                next_index_version,
            )

        await db.commit()
        return BuildRetrievalIndexResponse(
            snapshot_id=req.snapshot_id,
            chunk_count=chunk_count,
            files_indexed=files_indexed,
            generated_at=now,
        )
