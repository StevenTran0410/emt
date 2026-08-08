"""Batch persistence and incremental BM25 IDF update helpers used during index builds."""

from __future__ import annotations

import json

from shared.logger import logger

from ..bm25_scorer import BM25Scorer


async def _batch_insert_chunks(
    db,
    chunk_rows: list[tuple],
    index_rows: list[tuple],
) -> None:
    """Flush accumulated chunk and index rows in a single transaction.

    chunk_rows columns: (id, snapshot_id, rel_path, language, category,
                         chunk_index, content, token_estimate, chunk_type,
                         start_line, end_line, split_part, split_of,
                         content_hash, created_at)
    index_rows columns: (id, snapshot_id, rel_path, chunk_index,
                         lexical_preview, created_at)
    """
    if not chunk_rows and not index_rows:
        return
    if chunk_rows:
        await db.executemany(
            """
            INSERT INTO retrieval_chunks
            (id, snapshot_id, rel_path, language, category, chunk_index, content,
             token_estimate, chunk_type, start_line, end_line, split_part, split_of,
             content_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            chunk_rows,
        )
    if index_rows:
        await db.executemany(
            """
            INSERT INTO retrieval_indexes
            (id, snapshot_id, rel_path, chunk_index, lexical_preview, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            index_rows,
        )
    await db.commit()


async def _apply_incremental_idf_update(
    db,
    snapshot_id: str,
    chunk_hash_map: dict[str, str],
    tokenized_corpus: list[list[str]],
    avgdl: float,
) -> tuple[dict, int]:
    """Attempt incremental IDF update via content-hash diff (full rebuild if change ratio too high). Returns (idf_dict, next_index_version)."""
    # Fetch prior BM25 stats
    async with db.execute(
        "SELECT idf_json, index_version FROM retrieval_bm25_stats WHERE snapshot_id=?",
        (snapshot_id,),
    ) as cur:
        prior_row = await cur.fetchone()

    if prior_row is None:
        # No prior index — full rebuild required.
        return BM25Scorer.build_idf(tokenized_corpus, len(tokenized_corpus)), 0

    prior_idf = json.loads(prior_row["idf_json"])
    prior_index_version = prior_row["index_version"] or 0

    # Trigger full rebuild every 50 incremental updates to prevent silent IDF drift.
    if prior_index_version > 0 and prior_index_version % 50 == 0:
        logger.info(
            "[build_index] Forcing full rebuild at index_version=%d to prevent IDF drift",
            prior_index_version,
        )
        return BM25Scorer.build_idf(
            tokenized_corpus, len(tokenized_corpus)
        ), prior_index_version + 1

    # Fetch old hashes and detect changed chunks
    async with db.execute(
        "SELECT id, content_hash FROM retrieval_chunks WHERE snapshot_id=? ORDER BY id",
        (snapshot_id,),
    ) as cur:
        old_rows = await cur.fetchall()

    old_hashes = {row["id"]: row["content_hash"] for row in old_rows}
    changed_chunk_ids = set()
    for chunk_id, new_hash in chunk_hash_map.items():
        old_hash = old_hashes.get(chunk_id)
        if old_hash != new_hash:
            changed_chunk_ids.add(chunk_id)

    # Check if incremental update is feasible
    total_chunks = len(old_hashes)
    if total_chunks == 0:
        return BM25Scorer.build_idf(
            tokenized_corpus, len(tokenized_corpus)
        ), prior_index_version + 1

    change_ratio = len(changed_chunk_ids) / total_chunks
    if change_ratio >= 0.10:
        logger.info(
            "[build_index] Change ratio %.1f%% >= 10%% threshold; full rebuild",
            change_ratio * 100,
        )
        return BM25Scorer.build_idf(
            tokenized_corpus, len(tokenized_corpus)
        ), prior_index_version + 1

    # Incremental IDF: only reuse prior IDF if no chunks changed (rare case)
    logger.debug(
        "[build_index] Incremental path: %.1f%% changed (%d/%d chunks)",
        change_ratio * 100,
        len(changed_chunk_ids),
        total_chunks,
    )

    if not changed_chunk_ids:
        # No changed chunks — reuse prior IDF (optimization for fast re-indexes with zero changes)
        new_idf = prior_idf
        logger.debug("[build_index] No changed chunks; reusing prior IDF")
    else:
        # Some chunks changed — full rebuild is safer than tracking deltas (error-prone position matching).
        new_idf = BM25Scorer.build_idf(tokenized_corpus, len(tokenized_corpus))
        logger.debug("[build_index] Chunks changed; rebuilding IDF from corpus")

    return new_idf, prior_index_version + 1
