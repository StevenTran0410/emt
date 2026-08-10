"""Selective reset of the CodeSpectra DB: wipe all index/graph/doc data, keep config.

Preserves workspace, provider, code-host, app metadata, and repo registration.
Deletes everything else (snapshots, manifest, structural/symbol/code graph,
communities, retrieval index/chunks, doc graph, doc<->code compare, caches).
Backs up the DB file first and VACUUMs afterward. Run with --yes to skip the prompt.
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Tables that hold user setup, not regenerable analysis data. app_metadata holds
# schema_version — wiping it breaks migrations.
PRESERVE = {
    "workspaces",
    "provider_configs",
    "github_accounts",
    "app_metadata",
    "local_repos",
}

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / ".database" / "codespectra.db"
GRAPHS_DIR = REPO_ROOT / ".database" / "graphs"


def main() -> int:
    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}")
        return 1

    if "--yes" not in sys.argv:
        print(f"About to WIPE all analysis data in:\n  {DB_PATH}")
        print(f"Keeping config tables: {', '.join(sorted(PRESERVE))}")
        if input("Type YES to continue: ").strip() != "YES":
            print("Aborted.")
            return 1

    # Back up next to the DB before touching anything.
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = DB_PATH.with_name(f"codespectra.backup-{stamp}.db")
    shutil.copy2(DB_PATH, backup)
    print(f"Backup -> {backup}")

    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
    except sqlite3.OperationalError as e:
        print(f"Cannot open DB ({e}). Close the app if it is running, then retry.")
        return 1

    conn.execute("PRAGMA foreign_keys=OFF")
    tables = [
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    ]
    to_clear = [t for t in tables if t not in PRESERVE and not t.startswith("sqlite_")]

    cur = conn.cursor()
    total = 0
    try:
        for t in sorted(to_clear):
            n = cur.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            cur.execute(f'DELETE FROM "{t}"')
            total += n
            print(f"  cleared {n:>7}  {t}")
        # Drop the dangling pointer to the snapshot we just deleted.
        cur.execute("UPDATE local_repos SET active_snapshot_id=NULL")
        conn.commit()
    except sqlite3.OperationalError as e:
        conn.rollback()
        conn.close()
        print(f"\nFAILED ({e}). DB is unchanged. Close the app if it is running.")
        print(f"Backup kept at: {backup}")
        return 1

    conn.execute("VACUUM")
    conn.commit()
    conn.close()

    # Remove derived per-snapshot graph.json artifacts.
    removed_dirs = 0
    if GRAPHS_DIR.exists():
        for child in GRAPHS_DIR.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
                removed_dirs += 1

    print(f"\nDone. Deleted {total} rows across {len(to_clear)} tables; "
          f"removed {removed_dirs} graph artifact dir(s).")
    print(f"Backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
