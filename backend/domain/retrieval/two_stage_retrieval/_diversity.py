"""File-size stats loading + per-file cap / MMR diversity filtering."""
from __future__ import annotations

from infrastructure.db.database import get_db

from ..types import RankedChunk


async def _load_file_size_stats(snapshot_id: str) -> dict[str, tuple[int, int]]:
    """Fetch size_bytes + symbol_count per file in ONE joined query.

    Returns: dict[rel_path, (size_bytes, symbol_count)].

    Signals are orthogonal to chunk count:
      - size_bytes: raw file size — proxy for "long file"
      - symbol_count: number of functions/classes — proxy for "many functions"

    A file that is long AND dense (many symbols) deserves more top-K slots
    than a long but sparse file (e.g. huge generated JSON / fixture).
    """
    db = get_db()
    stats: dict[str, tuple[int, int]] = {}
    async with db.execute(
        """
        SELECT m.rel_path,
               m.size_bytes,
               COUNT(s.id) AS symbol_count
        FROM manifest_files m
        LEFT JOIN code_symbols s
            ON s.snapshot_id = m.snapshot_id AND s.rel_path = m.rel_path
        WHERE m.snapshot_id = ?
        GROUP BY m.rel_path, m.size_bytes
        """,
        (snapshot_id,),
    ) as cur:
        rows = await cur.fetchall()
    for row in rows:
        stats[row["rel_path"]] = (int(row["size_bytes"] or 0), int(row["symbol_count"] or 0))
    return stats


def _compute_file_caps(
    ranked: list[RankedChunk],
    central_files: set[str] | None = None,
    file_total_chunks: dict[str, int] | None = None,
    file_size_stats: dict[str, tuple[int, int]] | None = None,
) -> dict[str, int]:
    """Compute per-file chunk caps from density + centrality + size + symbol density.

    Philosophy: a file that is LONG + has MANY functions + MATCHES the query densely
    + is CENTRAL deserves more top-K slots than a short peripheral file or a long-but-empty
    file (generated fixture / vendored blob).

    Signals:
      - match_count:      chunks from this file in `ranked` (relevance density)
      - central_files:    file in top_central_files (structural importance)
      - file_total_chunks: chunks this file produced (chunking-level size)
      - file_size_stats:  (size_bytes, symbol_count) from manifest+symbols join

    Formula:
      base = 2 (every matching file gets at least 2 slots)
      + log-scale of match_count         (dense matches earn more slots)
      + 2 if central                     (important files earn a boost)
      + 1 if big & match-ratio >= 30%    (big file that matches a lot)
      + 1 if symbol_count >= 10          (function-dense file = info-dense)
      + 1 if size_bytes >= 8000 AND symbol_count >= 5  (long + uses many fns — user's ask)
      - 1 if size_bytes >= 8000 AND symbol_count <= 2  (long but empty — generated/vendored)
      clamped [2, 8]
    """
    import math
    central_files = central_files or set()
    file_total_chunks = file_total_chunks or {}
    file_size_stats = file_size_stats or {}

    match_count: dict[str, int] = {}
    for c in ranked:
        match_count[c.rel_path] = match_count.get(c.rel_path, 0) + 1

    caps: dict[str, int] = {}
    for rel_path, n in match_count.items():
        # log2 density: n=1→1, n=2→1.58, n=4→2.32, n=8→3.17, n=16→4.09
        density_bonus = math.log2(n + 1)
        cap = 2 + density_bonus
        if rel_path in central_files:
            cap += 2
        total = file_total_chunks.get(rel_path, 0)
        if total >= 20 and (n / total) >= 0.3:
            cap += 1

        # Size + symbol signals (smart cap extension).
        size_bytes, symbol_count = file_size_stats.get(rel_path, (0, 0))
        if symbol_count >= 10:
            cap += 1  # function-dense file → information-dense
        if size_bytes >= 8000 and symbol_count >= 5:
            cap += 1  # long + uses many functions — user's ask
        elif size_bytes >= 8000 and symbol_count <= 2:
            cap -= 1  # long but empty — generated / vendored / fixture

        caps[rel_path] = max(2, min(8, int(round(cap))))
    return caps


def _apply_diversity_filter(
    ranked: list[RankedChunk],
    lambda_: float = 0.9,
    central_files: set[str] | None = None,
    file_total_chunks: dict[str, int] | None = None,
    file_size_stats: dict[str, tuple[int, int]] | None = None,
) -> list[RankedChunk]:
    """Light diversity pass: smart per-file cap + soft MMR.

    Tuned 2026-04-24 with smart cap after feedback that a fixed max_per_file
    was blunt. Cap now scales per-file based on:
      - how many of that file's chunks matched the query (density)
      - whether the file is structurally central
      - the file's absolute size and the match ratio

    Stage 1 — Per-file smart cap via _compute_file_caps(). Files with more
      matching content / higher centrality earn more slots (up to 8); small
      or peripheral files get the minimum 2.

    Stage 2 — Soft MMR (λ=0.9). Precomputed path components for perf. Penalty
      is relative to candidate's score so low-score chunks aren't disproportionately
      punished. Always keeps top 5 regardless of MMR score.
    """
    if not ranked or len(ranked) <= 3:
        return ranked

    # Stage 1: Smart per-file cap.
    caps = _compute_file_caps(ranked, central_files, file_total_chunks, file_size_stats)
    file_counts: dict[str, int] = {}
    capped: list[RankedChunk] = []
    for chunk in ranked:
        n = file_counts.get(chunk.rel_path, 0)
        if n >= caps.get(chunk.rel_path, 4):
            continue
        file_counts[chunk.rel_path] = n + 1
        capped.append(chunk)

    # Stage 2: Soft MMR. Precompute path components once — avoids O(k²) set
    # allocations that caused noticeable query delay.
    path_components: list[set[str]] = [set(c.rel_path.split("/")) for c in capped]

    picked: list[RankedChunk] = []
    picked_comps: list[set[str]] = []
    for i, candidate in enumerate(capped):
        if len(picked) < 5:
            picked.append(candidate)
            picked_comps.append(path_components[i])
            continue

        cand_comps = path_components[i]
        max_sim = 0.0
        for pc in picked_comps:
            inter = len(cand_comps & pc)
            if inter == 0:
                continue
            sim = inter / len(cand_comps | pc)
            if sim > max_sim:
                max_sim = sim
                if max_sim >= 0.999:  # early exit — can't get higher
                    break

        # Relative penalty: similarity scales own score, not absolute.
        mmr_score = lambda_ * candidate.score - (1 - lambda_) * max_sim * candidate.score
        if mmr_score > 0:
            picked.append(candidate)
            picked_comps.append(cand_comps)

    return picked
