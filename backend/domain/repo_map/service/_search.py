"""Symbol search: LIKE cascade with a bounded-Levenshtein fuzzy fallback."""
from ..types import SymbolRecord
from ._records import _SYMBOL_COLS, _symbol_record_from_row


def levenshtein_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if s1 == s2:
        return 0
    if not s1:
        return len(s2)
    if not s2:
        return len(s1)
    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


async def search_symbols_cascade(
    db, snapshot_id: str, q: str, limit: int = 120
) -> list[SymbolRecord]:
    """Search code symbols using a cascade: LIKE search -> bounded Levenshtein (<=2) fallback.

    Re-verifies all candidates against code_symbols table for snapshot_id before returning.
    """
    q_str = q.strip()
    if not q_str:
        return []

    like = f"%{q_str}%"
    async with db.execute(
        f"""
        SELECT {_SYMBOL_COLS} FROM code_symbols
        WHERE snapshot_id=? AND (name LIKE ? OR rel_path LIKE ?)
        ORDER BY
          CASE WHEN name = ? THEN 0 WHEN name LIKE ? THEN 1 ELSE 2 END,
          rel_path ASC,
          line_start ASC
        LIMIT ?
        """,
        (snapshot_id, like, like, q_str, f"{q_str}%", limit),
    ) as cur:
        rows = await cur.fetchall()

    if rows:
        return [_symbol_record_from_row(r) for r in rows]

    # Stage 2: Bounded Levenshtein fuzzy fallback (edit distance <= 2)
    q_low = q_str.lower()
    async with db.execute(
        "SELECT DISTINCT name FROM code_symbols WHERE snapshot_id=? AND kind NOT IN ('file', 'module')",
        (snapshot_id,),
    ) as cur:
        name_rows = await cur.fetchall()

    candidate_names: list[tuple[int, str]] = []
    for r in name_rows:
        sym_name = r["name"]
        if not sym_name:
            continue
        dist = levenshtein_distance(q_low, sym_name.lower())
        if dist <= 2:
            candidate_names.append((dist, sym_name))

    if not candidate_names:
        return []

    candidate_names.sort(key=lambda x: (x[0], x[1].lower()))

    fuzzy_records: list[SymbolRecord] = []
    seen_ids: set[str] = set()

    for _dist, sym_name in candidate_names:
        async with db.execute(
            f"SELECT {_SYMBOL_COLS} FROM code_symbols WHERE snapshot_id=? AND LOWER(name)=? LIMIT 10",
            (snapshot_id, sym_name.lower()),
        ) as cur:
            r_rows = await cur.fetchall()
            for r in r_rows:
                rec_id = r["id"]
                if rec_id not in seen_ids:
                    seen_ids.add(rec_id)
                    fuzzy_records.append(_symbol_record_from_row(r))
                if len(fuzzy_records) >= limit:
                    break
        if len(fuzzy_records) >= limit:
            break

    return fuzzy_records[:limit]
