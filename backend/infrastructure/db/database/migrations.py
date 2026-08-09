"""Sequential schema migrations for the standalone SQLite database.

Consolidated baseline = the CodeSpectra codespectra.db final schema for the kept
indexing/retrieval/providers tables (analysis + qa tables dropped). Generated verbatim
from the source schema so column names/types match the copied domain services exactly.
"""
from typing import Any

_MIGRATIONS: list[dict[str, Any]] = [
    {
        "version": 0,
        "description": "Baseline consolidated schema (kept tables, verbatim from source)",
        "sql": """
CREATE TABLE IF NOT EXISTS app_metadata (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

CREATE TABLE IF NOT EXISTS workspaces (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL UNIQUE,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                settings    TEXT NOT NULL DEFAULT '{}'
            , description TEXT DEFAULT NULL);

CREATE TABLE IF NOT EXISTS provider_configs (
                id           TEXT PRIMARY KEY,
                kind         TEXT NOT NULL,
                display_name TEXT NOT NULL,
                base_url     TEXT NOT NULL,
                model_id     TEXT NOT NULL,
                capabilities TEXT NOT NULL DEFAULT '{}',
                extra        TEXT NOT NULL DEFAULT '{}',
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            );

CREATE TABLE IF NOT EXISTS github_accounts (
                id           TEXT PRIMARY KEY,
                login        TEXT NOT NULL,
                display_name TEXT,
                avatar_url   TEXT,
                access_token TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            );

CREATE TABLE IF NOT EXISTS jobs (
                id           TEXT PRIMARY KEY,
                type         TEXT NOT NULL,
                repo_id      TEXT,
                status       TEXT NOT NULL DEFAULT 'pending',
                steps        TEXT NOT NULL DEFAULT '{}',
                current_step TEXT,
                error        TEXT,
                started_at   TEXT NOT NULL,
                finished_at  TEXT
            );

CREATE TABLE IF NOT EXISTS repo_snapshots (
                id             TEXT PRIMARY KEY,
                local_repo_id  TEXT NOT NULL,
                branch         TEXT,
                commit_hash    TEXT,
                local_path     TEXT NOT NULL,
                status         TEXT NOT NULL DEFAULT 'pending',
                error          TEXT,
                clone_policy   TEXT NOT NULL DEFAULT 'full',
                synced_at      TEXT NOT NULL,
                created_at     TEXT NOT NULL
            , manual_ignores TEXT NOT NULL DEFAULT '[]');

CREATE TABLE IF NOT EXISTS manifest_files (
                id          TEXT PRIMARY KEY,
                snapshot_id TEXT NOT NULL,
                rel_path    TEXT NOT NULL,
                language    TEXT,
                category    TEXT NOT NULL,
                size_bytes  INTEGER NOT NULL,
                mtime_ns    INTEGER NOT NULL,
                checksum    TEXT NOT NULL
            );

CREATE TABLE IF NOT EXISTS code_symbols (
                id          TEXT PRIMARY KEY,
                snapshot_id TEXT NOT NULL,
                rel_path    TEXT NOT NULL,
                language    TEXT,
                name        TEXT NOT NULL,
                kind        TEXT NOT NULL,
                line_start  INTEGER NOT NULL,
                line_end    INTEGER NOT NULL,
                signature   TEXT,
                parent_name TEXT,
                created_at  TEXT NOT NULL
            , extract_source TEXT NOT NULL DEFAULT 'lexical', qualified_name TEXT);

CREATE TABLE IF NOT EXISTS repo_maps (
                snapshot_id        TEXT PRIMARY KEY,
                total_symbols      INTEGER NOT NULL,
                files_indexed      INTEGER NOT NULL,
                parse_failures     INTEGER NOT NULL,
                extract_mode       TEXT NOT NULL,
                language_breakdown TEXT NOT NULL DEFAULT '{}',
                kind_breakdown     TEXT NOT NULL DEFAULT '{}',
                generated_at       TEXT NOT NULL
            );

CREATE TABLE IF NOT EXISTS structural_graph_edges (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id TEXT NOT NULL,
                src_path    TEXT NOT NULL,
                dst_path    TEXT NOT NULL,
                edge_type   TEXT NOT NULL,
                is_external INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL
            , confidence_score REAL NOT NULL DEFAULT 1.0, resolution_method TEXT NOT NULL DEFAULT 'import_statement');

CREATE TABLE IF NOT EXISTS structural_graph_summaries (
                snapshot_id        TEXT PRIMARY KEY,
                total_nodes        INTEGER NOT NULL,
                total_edges        INTEGER NOT NULL,
                external_edges     INTEGER NOT NULL,
                entrypoints        TEXT NOT NULL DEFAULT '[]',
                top_central_files  TEXT NOT NULL DEFAULT '[]',
                generated_at       TEXT NOT NULL,
                native_toolchain   TEXT
            );

CREATE TABLE IF NOT EXISTS retrieval_chunks (
                id             TEXT PRIMARY KEY,
                snapshot_id    TEXT NOT NULL,
                rel_path       TEXT NOT NULL,
                language       TEXT,
                category       TEXT NOT NULL,
                chunk_index    INTEGER NOT NULL,
                content        TEXT NOT NULL,
                token_estimate INTEGER NOT NULL,
                created_at     TEXT NOT NULL
            , chunk_type  TEXT    NOT NULL DEFAULT 'block', start_line  INTEGER NOT NULL DEFAULT 0, end_line    INTEGER NOT NULL DEFAULT 0, content_hash TEXT, split_part INTEGER NOT NULL DEFAULT 0, split_of    INTEGER NOT NULL DEFAULT 1);

CREATE TABLE IF NOT EXISTS retrieval_indexes (
                id              TEXT PRIMARY KEY,
                snapshot_id     TEXT NOT NULL,
                rel_path        TEXT NOT NULL,
                chunk_index     INTEGER NOT NULL,
                lexical_preview TEXT NOT NULL,
                created_at      TEXT NOT NULL
            );

CREATE TABLE IF NOT EXISTS graph_community_members (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id  TEXT    NOT NULL,
                node_path    TEXT    NOT NULL,
                community_id INTEGER NOT NULL,
                hub_score    REAL    NOT NULL DEFAULT 0.0,
                created_at   TEXT    NOT NULL
            );

CREATE TABLE IF NOT EXISTS graph_community_summaries (
                snapshot_id              TEXT    NOT NULL,
                community_id             INTEGER NOT NULL,
                member_count             INTEGER NOT NULL,
                hub_paths                TEXT    NOT NULL DEFAULT '[]',
                modularity_contribution  REAL    NOT NULL DEFAULT 0.0,
                llm_summary              TEXT,
                generated_at             TEXT    NOT NULL, neighbor_community_ids TEXT NOT NULL DEFAULT '[]',
                PRIMARY KEY (snapshot_id, community_id)
            );

CREATE TABLE IF NOT EXISTS retrieval_bm25_stats (
            snapshot_id  TEXT PRIMARY KEY,
            chunk_count  INTEGER NOT NULL,
            avgdl        REAL NOT NULL,
            idf_json     TEXT NOT NULL,
            k1           REAL NOT NULL DEFAULT 2.0,
            b            REAL NOT NULL DEFAULT 0.75,
            generated_at TEXT NOT NULL
        , index_version INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS symbol_graph_edges (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id    TEXT    NOT NULL,
    src_symbol     TEXT    NOT NULL,
    dst_symbol     TEXT    NOT NULL,
    edge_type      TEXT    NOT NULL DEFAULT 'calls',
    confidence     TEXT    NOT NULL DEFAULT 'high',
    evidence_lines TEXT    NOT NULL DEFAULT '[]'
, confidence_score REAL NOT NULL DEFAULT 0.7, resolution_method TEXT NOT NULL DEFAULT 'unknown');

CREATE TABLE IF NOT EXISTS retrieval_chunk_tokens (
    chunk_id TEXT NOT NULL,
    term TEXT NOT NULL,
    tf INTEGER NOT NULL,
    PRIMARY KEY (chunk_id, term)
);

CREATE TABLE IF NOT EXISTS file_extraction_cache (
    snapshot_id  TEXT NOT NULL,
    file_path    TEXT NOT NULL,
    sha256       TEXT NOT NULL,
    extracted_at TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, file_path)
);

CREATE TABLE IF NOT EXISTS "local_repos" (
    id                TEXT PRIMARY KEY,
    workspace_id      TEXT,
    path              TEXT NOT NULL,
    name              TEXT NOT NULL,
    source_type       TEXT NOT NULL DEFAULT 'local_folder',
    is_git_repo       INTEGER NOT NULL DEFAULT 0,
    git_branch        TEXT,
    git_head_hash     TEXT,
    git_remote_url    TEXT,
    has_size_warning  INTEGER NOT NULL DEFAULT 0,
    selected_branch   TEXT,
    active_snapshot_id TEXT,
    sync_mode         TEXT NOT NULL DEFAULT 'latest',
    pinned_ref        TEXT,
    ignore_overrides  TEXT NOT NULL DEFAULT '[]',
    detect_submodules INTEGER NOT NULL DEFAULT 1,
    include_tests     INTEGER NOT NULL DEFAULT 0,
    mode              TEXT NOT NULL DEFAULT 'code_analysis',
    added_at          TEXT NOT NULL,
    last_validated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS name_segment_vocab (
    snapshot_id TEXT NOT NULL,
    segment     TEXT NOT NULL,
    name        TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, segment, name)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_jobs_repo_id ON jobs(repo_id);
CREATE INDEX IF NOT EXISTS idx_jobs_started_at ON jobs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_snapshots_repo ON repo_snapshots(local_repo_id);
CREATE INDEX IF NOT EXISTS idx_manifest_snapshot ON manifest_files(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_manifest_rel_path ON manifest_files(snapshot_id, rel_path);
CREATE INDEX IF NOT EXISTS idx_symbols_snapshot ON code_symbols(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON code_symbols(snapshot_id, rel_path);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON code_symbols(snapshot_id, name);
CREATE INDEX IF NOT EXISTS idx_graph_edges_snapshot ON structural_graph_edges(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_graph_edges_src ON structural_graph_edges(snapshot_id, src_path);
CREATE INDEX IF NOT EXISTS idx_retrieval_chunks_snapshot ON retrieval_chunks(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_retrieval_chunks_path ON retrieval_chunks(snapshot_id, rel_path);
CREATE INDEX IF NOT EXISTS idx_retrieval_index_snapshot ON retrieval_indexes(snapshot_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_graph_comm_members_node
                ON graph_community_members(snapshot_id, node_path);
CREATE INDEX IF NOT EXISTS idx_graph_comm_members_community
                ON graph_community_members(snapshot_id, community_id);
CREATE INDEX IF NOT EXISTS idx_graph_comm_summaries_snapshot
                ON graph_community_summaries(snapshot_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_manifest_unique_path
                ON manifest_files(snapshot_id, rel_path);
CREATE INDEX IF NOT EXISTS idx_sym_edges_snapshot ON symbol_graph_edges(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_sym_edges_src ON symbol_graph_edges(snapshot_id, src_symbol);
CREATE INDEX IF NOT EXISTS idx_symbols_snapshot_path_line
    ON code_symbols(snapshot_id, rel_path, line_start);
CREATE INDEX IF NOT EXISTS idx_symbols_snapshot_name
    ON code_symbols(snapshot_id, name);
CREATE INDEX IF NOT EXISTS idx_manifest_snapshot_path
    ON manifest_files(snapshot_id, rel_path);
CREATE INDEX IF NOT EXISTS idx_retrieval_chunks_snapshot_path
    ON retrieval_chunks(snapshot_id, rel_path);
CREATE INDEX IF NOT EXISTS idx_rct_chunk ON retrieval_chunk_tokens(chunk_id);
CREATE INDEX IF NOT EXISTS idx_extraction_cache_snapshot
    ON file_extraction_cache(snapshot_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_local_repos_path_workspace_mode
    ON local_repos(path, workspace_id, mode);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sym_edges_unique ON symbol_graph_edges(snapshot_id, src_symbol, dst_symbol, edge_type);
CREATE INDEX IF NOT EXISTS idx_symbols_qname ON code_symbols(snapshot_id, qualified_name);
CREATE INDEX IF NOT EXISTS idx_vocab_seg ON name_segment_vocab(snapshot_id, segment);

            INSERT OR IGNORE INTO app_metadata (key, value)
                VALUES ('first_launched_at', datetime('now'));
        """,
    },
    {
        "version": 1,
        "description": "Add doc_graph tables for BD/DD document graph and mismatch detection",
        "sql": """
CREATE TABLE IF NOT EXISTS doc_graph_clusters (
    id            TEXT PRIMARY KEY,
    cluster_name  TEXT NOT NULL,
    source_dir    TEXT NOT NULL,
    bd_path       TEXT NOT NULL,
    snapshot_id   TEXT,
    input_fingerprint TEXT NOT NULL,
    parser_version    INTEGER NOT NULL,
    status        TEXT NOT NULL DEFAULT 'ready',
    generated_at  TEXT,
    created_at    TEXT NOT NULL,
    UNIQUE (source_dir, cluster_name)
);

CREATE TABLE IF NOT EXISTS doc_graph_documents (
    id            TEXT PRIMARY KEY,
    cluster_id    TEXT NOT NULL REFERENCES doc_graph_clusters(id) ON DELETE CASCADE,
    doc_kind      TEXT NOT NULL,
    artifact_name TEXT NOT NULL,
    doc_path      TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    generated_at  TEXT,
    section_map   TEXT,
    created_at    TEXT NOT NULL,
    UNIQUE (cluster_id, doc_kind, artifact_name)
);

CREATE TABLE IF NOT EXISTS doc_graph_assertions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id  TEXT NOT NULL REFERENCES doc_graph_clusters(id) ON DELETE CASCADE,
    side        TEXT NOT NULL,
    predicate   TEXT NOT NULL,
    subject     TEXT NOT NULL,
    object      TEXT,
    value       TEXT,
    qualifiers  TEXT,
    status      TEXT,
    doc_id      TEXT NOT NULL,
    doc_span    TEXT,
    source_span TEXT,
    confidence  TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS doc_graph_nodes (
    id           TEXT NOT NULL,
    cluster_id   TEXT NOT NULL REFERENCES doc_graph_clusters(id) ON DELETE CASCADE,
    node_type    TEXT NOT NULL,
    display_name TEXT NOT NULL,
    attributes   TEXT,
    provenance   TEXT,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (cluster_id, id)
);

CREATE TABLE IF NOT EXISTS doc_graph_edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id  TEXT NOT NULL REFERENCES doc_graph_clusters(id) ON DELETE CASCADE,
    src_node_id TEXT NOT NULL,
    dst_node_id TEXT NOT NULL,
    edge_type   TEXT NOT NULL,
    edge_key    TEXT NOT NULL,
    attributes  TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE (cluster_id, src_node_id, dst_node_id, edge_type, edge_key)
);

CREATE TABLE IF NOT EXISTS doc_graph_mismatches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id    TEXT NOT NULL REFERENCES doc_graph_clusters(id) ON DELETE CASCADE,
    fingerprint   TEXT NOT NULL,
    mismatch_type TEXT NOT NULL,
    severity      TEXT NOT NULL,
    bd_location   TEXT,
    dd_location   TEXT,
    description   TEXT NOT NULL,
    evidence      TEXT,
    confidence    TEXT,
    created_at    TEXT NOT NULL,
    UNIQUE (cluster_id, fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_docg_assertions_lookup ON doc_graph_assertions(cluster_id, side, predicate, subject);
CREATE INDEX IF NOT EXISTS idx_docg_nodes_cluster      ON doc_graph_nodes(cluster_id);
CREATE INDEX IF NOT EXISTS idx_docg_edges_cluster      ON doc_graph_edges(cluster_id);
CREATE INDEX IF NOT EXISTS idx_docg_mismatches_cluster ON doc_graph_mismatches(cluster_id);
        """,
    },
    {
        "version": 2,
        "description": "Add derivation column to doc_graph_mismatches and create doc_graph_llm_cache table",
        "sql": """
ALTER TABLE doc_graph_mismatches ADD COLUMN derivation TEXT NOT NULL DEFAULT 'deterministic';

CREATE TABLE IF NOT EXISTS doc_graph_llm_cache (
    key          TEXT PRIMARY KEY,
    verdict_json TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
        """,
    },
    {
        "version": 3,
        "description": "Add source_facts and source_parse_diagnostics tables for CodeGraph enrichment",
        "sql": """
CREATE TABLE IF NOT EXISTS source_facts (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id    TEXT NOT NULL,
  rel_path       TEXT NOT NULL,
  language       TEXT NOT NULL,
  fact_type      TEXT NOT NULL,
  semantic_key   TEXT NOT NULL,
  occurrence_ix  INTEGER NOT NULL,
  parent_key     TEXT,
  name           TEXT,
  value          TEXT,
  attributes     TEXT NOT NULL DEFAULT '{}',
  line_start     INTEGER NOT NULL,
  line_end       INTEGER NOT NULL,
  extractor      TEXT NOT NULL,
  extractor_ver  TEXT NOT NULL,
  created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_source_facts_snap      ON source_facts(snapshot_id);
CREATE INDEX IF NOT EXISTS ix_source_facts_snap_type ON source_facts(snapshot_id, fact_type);
CREATE INDEX IF NOT EXISTS ix_source_facts_key       ON source_facts(snapshot_id, semantic_key);
CREATE INDEX IF NOT EXISTS ix_source_facts_parent    ON source_facts(snapshot_id, parent_key);

CREATE TABLE IF NOT EXISTS source_parse_diagnostics (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id   TEXT NOT NULL,
  rel_path      TEXT NOT NULL,
  language      TEXT NOT NULL,
  status        TEXT NOT NULL,
  error_count   INTEGER NOT NULL DEFAULT 0,
  first_error   TEXT,
  elapsed_ms    INTEGER NOT NULL DEFAULT 0,
  extractor_ver TEXT NOT NULL,
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_parse_diag_snap ON source_parse_diagnostics(snapshot_id);
        """,
    },
    {
        "version": 4,
        "description": "Add doc_code_relation_comparisons and doc_code_relation_evidence for Structural Link v1",
        "sql": """
CREATE TABLE IF NOT EXISTS doc_code_relation_comparisons (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cluster_id TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  doc_assertion_id INTEGER,
  side TEXT,
  predicate TEXT NOT NULL,
  subject_key TEXT,
  object_key TEXT,
  qualifier_key TEXT,
  endpoint_verdict TEXT NOT NULL,
  multiplicity_verdict TEXT NOT NULL,
  doc_count INTEGER,
  code_count INTEGER,
  eligibility TEXT NOT NULL,
  reason TEXT,
  comparator_version TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS doc_code_relation_evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  comparison_id INTEGER NOT NULL,
  occurrence_key TEXT,
  rel_path TEXT,
  line_start INTEGER,
  line_end INTEGER,
  source_store TEXT,
  match_kind TEXT
);

CREATE INDEX IF NOT EXISTS ix_dcrc ON doc_code_relation_comparisons(cluster_id, snapshot_id);
        """,
    },
]

TARGET_VERSION = len(_MIGRATIONS) - 1
