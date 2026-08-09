"""Graph build pipeline: extraction cache, import-edge extraction, symbol-graph wiring, scoring."""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
from pathlib import Path
from typing import Any

from infrastructure.db.database import get_db
from shared.logger import logger
from shared.sql_queries import SQL_SELECT_MANIFEST_FILES_BY_SNAPSHOT
from shared.toolchain import detect_cpp_toolchain
from shared.utils import read_utf8_lenient, utc_now_iso

from ..types import BuildGraphRequest, BuildGraphResponse, GraphNodeScore, StructuralGraphSummary
from ._import_parsing import _extract_python_imports, _extract_ts_js_imports
from ._path_resolve import (
    _build_py_suffix_index,
    _is_entrypoint,
    _is_init_file,
    _normalize,
    _resolve_relative_import,
)
from ._scoring import _compute_scores_python, _load_native_graph

# Kill switch for SymbolGraphBuilder wiring
_SYMBOL_GRAPH_BUILDER_ENABLED = os.getenv("SYMBOL_GRAPH_BUILDER_ENABLED", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)

# Kill switch for COBOL + JCL graph extraction
_COBOL_JCL_GRAPH_ENABLED = os.getenv("COBOL_JCL_GRAPH_ENABLED", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)

# Kill switch for CodeGraph facts & diagnostics enrichment
_CODEGRAPH_ENRICH_ENABLED = os.getenv("CODEGRAPH_ENRICH_ENABLED", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)


class _BuildMixin:
    async def build(self, req: BuildGraphRequest) -> BuildGraphResponse:
        db = get_db()
        async with db.execute("SELECT * FROM repo_snapshots WHERE id=?", (req.snapshot_id,)) as cur:
            snap = await cur.fetchone()
        if snap is None:
            from shared.errors import NotFoundError

            raise NotFoundError("RepoSnapshot", req.snapshot_id)

        root = Path(snap["local_path"])
        if not root.exists():
            raise ValueError("Snapshot path does not exist")

        if req.force_rebuild:
            await db.execute("DELETE FROM structural_graph_edges WHERE snapshot_id=?", (req.snapshot_id,))
            await db.execute("DELETE FROM structural_graph_summaries WHERE snapshot_id=?", (req.snapshot_id,))
            await db.execute("DELETE FROM source_facts WHERE snapshot_id=?", (req.snapshot_id,))
            await db.execute("DELETE FROM source_parse_diagnostics WHERE snapshot_id=?", (req.snapshot_id,))
            if _SYMBOL_GRAPH_BUILDER_ENABLED:
                await db.execute("DELETE FROM symbol_graph_edges WHERE snapshot_id=?", (req.snapshot_id,))
        else:
            async with db.execute(
                "SELECT 1 FROM structural_graph_summaries WHERE snapshot_id=? LIMIT 1",
                (req.snapshot_id,),
            ) as cur:
                exists = await cur.fetchone()
            if exists:
                logger.info(
                    "[structural_graph] snapshot %s already built, skipping", req.snapshot_id
                )
                return BuildGraphResponse(summary=await self.summary(req.snapshot_id))

        async with db.execute(SQL_SELECT_MANIFEST_FILES_BY_SNAPSHOT, (req.snapshot_id,)) as cur:
            files = await cur.fetchall()

        file_set = {r["rel_path"] for r in files}
        test_file_set = {r["rel_path"] for r in files if r["category"] == "test"}

        py_suffix_index = _build_py_suffix_index(file_set)

        # ──── Part A: Extraction Cache ────────────────────────────────────────
        from ..extraction_cache import (
            _get_previous_snapshot_id,
            classify_files,
            copy_unchanged_edges,
            copy_unchanged_symbol_edges,
            load_previous_cache,
        )
        from ..extraction_cache import (
            write_cache as write_cache_db,
        )

        prev_cache = await load_previous_cache(db, snap["local_repo_id"], req.snapshot_id)
        prev_snapshot_id = await _get_previous_snapshot_id(db, snap["local_repo_id"], req.snapshot_id)

        with concurrent.futures.ThreadPoolExecutor() as executor:
            cache_result = await classify_files(files, prev_cache, prev_snapshot_id, executor, str(root))

        logger.info(
            "[structural_graph] cache: %d unchanged, %d changed (snapshot %s)",
            len(cache_result.unchanged_paths),
            len(cache_result.changed_files),
            req.snapshot_id,
        )

        # Copy edges for unchanged files from previous snapshot
        if cache_result.previous_snapshot_id and cache_result.unchanged_paths:
            copied = await copy_unchanged_edges(
                db,
                cache_result.previous_snapshot_id,
                req.snapshot_id,
                cache_result.unchanged_paths,
            )
            logger.info("[structural_graph] copied %d edges from cache for unchanged files", copied)

        # Copy symbol graph edges for unchanged files
        if _SYMBOL_GRAPH_BUILDER_ENABLED and cache_result.previous_snapshot_id and cache_result.unchanged_paths:
            copied_symbols = await copy_unchanged_symbol_edges(
                db,
                cache_result.previous_snapshot_id,
                req.snapshot_id,
                cache_result.unchanged_paths,
            )
            logger.info("[structural_graph] copied %d symbol edges from cache for unchanged files", copied_symbols)

        # Extract imports ONLY from changed files
        files_to_process = cache_result.changed_files

        native_graph = _load_native_graph()
        entrypoints: list[str] = []
        total_edges = 0
        external_edges = 0
        edge_inputs: list[tuple[str, str, str, int]] = []
        _edge_rows: list[tuple] = []
        _symbol_edge_rows: list[tuple] = []
        now = utc_now_iso()

        cobol_program_index: dict[str, list[str]] = {}
        copybook_index: dict[str, list[str]] = {}
        cobol_facts_cache: dict[str, Any] = {}
        cobol_enrich_cache: dict[str, tuple[list[dict[str, Any]], dict[str, Any]]] = {}
        if _COBOL_JCL_GRAPH_ENABLED:
            from .._cobol import (
                build_copybook_index,
                build_program_index,
                extract_cobol_all,
                extract_cobol_facts,
            )
            extracted_program_ids: dict[str, str | None] = {}
            for r in files:
                if r["language"] == "cobol" and r["category"] in {"source", "infra"}:
                    src_f = (root / r["rel_path"]).resolve()
                    if src_f.exists() and src_f.is_file():
                        c_content = read_utf8_lenient(src_f)
                        if c_content:
                            if _CODEGRAPH_ENRICH_ENABLED:
                                res, enrich_facts, diag = extract_cobol_all(
                                    c_content, r["rel_path"]
                                )
                                cobol_enrich_cache[r["rel_path"]] = (enrich_facts, diag)
                            else:
                                res = extract_cobol_facts(c_content)
                            extracted_program_ids[r["rel_path"]] = res.program_id
                            cobol_facts_cache[r["rel_path"]] = res

            cobol_program_index = build_program_index(extracted_program_ids)
            copybook_index = build_copybook_index(file_set)

        for r in files_to_process:
            rel_path = r["rel_path"]
            language = r["language"]
            category = r["category"]
            if category not in {"source", "infra"}:
                # Test files are excluded from the structural graph.  They import
                # everything they test, creating noisy cross-community edges that
                # distort Louvain clustering.  Test coverage is tracked separately
                # via manifest_files (category='test') and surfaced in the export.
                continue
            # __init__.py files are namespace markers with no structural content.
            # Excluding them prevents singleton communities for every package dir.
            if _is_init_file(rel_path):
                continue
            if _is_entrypoint(rel_path):
                entrypoints.append(rel_path)
            src = (root / rel_path).resolve()
            if not src.exists() or not src.is_file():
                continue

            content = read_utf8_lenient(src)
            if not content:
                continue

            imports: list[str] = []
            if language == "python":
                imports = _extract_python_imports(content)
            elif language in {"typescript", "javascript"}:
                imports = _extract_ts_js_imports(content)
            elif _COBOL_JCL_GRAPH_ENABLED and language == "cobol":
                from .._cobol import extract_cobol_facts, resolve_cobol_calls, resolve_cobol_copies
                from .._legacy_edges import convert_resolved_edges_to_rows

                res = cobol_facts_cache.get(rel_path) or extract_cobol_facts(content)
                c_calls = resolve_cobol_calls(rel_path, res.program_id, [c._asdict() for c in res.calls], cobol_program_index)
                c_copies = resolve_cobol_copies(rel_path, res.program_id, [c._asdict() for c in res.copies], copybook_index)

                c_rows = convert_resolved_edges_to_rows(req.snapshot_id, c_calls + c_copies, now)
                edge_inputs.extend(c_rows.file_edge_inputs)
                _edge_rows.extend(c_rows.file_edge_rows)
                _symbol_edge_rows.extend(c_rows.symbol_edge_rows)
                total_edges += len(c_rows.file_edge_inputs)
                external_edges += sum(1 for inp in c_rows.file_edge_inputs if inp[3] == 1)
                continue
            elif _COBOL_JCL_GRAPH_ENABLED and language == "jcl":
                from .._jcl import extract_jcl_facts, resolve_jcl_dds, resolve_jcl_execs
                from .._legacy_edges import convert_resolved_edges_to_rows

                j_res = extract_jcl_facts(content)
                j_execs = resolve_jcl_execs(rel_path, j_res.execs, cobol_program_index)
                j_dds = resolve_jcl_dds(rel_path, j_res.dds)

                j_rows = convert_resolved_edges_to_rows(req.snapshot_id, j_execs + j_dds, now)
                edge_inputs.extend(j_rows.file_edge_inputs)
                _edge_rows.extend(j_rows.file_edge_rows)
                _symbol_edge_rows.extend(j_rows.symbol_edge_rows)
                total_edges += len(j_rows.file_edge_inputs)
                external_edges += sum(1 for inp in j_rows.file_edge_inputs if inp[3] == 1)
                continue

            if not imports:
                continue

            for imp in imports:
                dst_path: str | None = None
                is_external = True
                if imp.startswith("."):
                    dst_path = _resolve_relative_import(rel_path, imp, file_set)
                    is_external = dst_path is None
                else:
                    # Python module-like imports may map to local path.
                    # Use suffix index so imports resolve regardless of source-root prefix.
                    py_guess = _normalize(imp.replace(".", "/") + ".py")
                    resolved_path = py_suffix_index.get(py_guess)
                    if not resolved_path:
                        # 'from domain.pkg import X' targets the package dir, not a .py file.
                        # Fall back to the package's __init__.py — but we exclude __init__.py
                        # from the graph (they are namespace markers with no structural content),
                        # so this fallback is intentionally left as external/unresolved.
                        pass
                    if resolved_path and not _is_init_file(resolved_path):
                        dst_path = resolved_path
                        is_external = False

                total_edges += 1
                if is_external:
                    external_edges += 1
                dst_store = dst_path if dst_path else imp
                edge_inputs.append((rel_path, dst_store, "import", int(is_external)))
                _edge_rows.append(
                    (req.snapshot_id, rel_path, dst_store, "import", int(is_external), now, 1.0, "import_statement")
                )

        # Batch-insert all edges in one round-trip instead of one await per edge.
        await db.executemany(
            """
            INSERT INTO structural_graph_edges
            (snapshot_id, src_path, dst_path, edge_type, is_external, created_at, confidence_score, resolution_method)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _edge_rows,
        )

        # ──── Part A2: CodeGraph Enrichment (source_facts + source_parse_diagnostics) ────
        if _CODEGRAPH_ENRICH_ENABLED:
            _fact_rows: list[tuple] = []
            _diag_rows: list[tuple] = []
            from .._cobol.extract import extract_cobol_enrichment_facts
            from .._jcl.extract import extract_jcl_enrichment_facts

            for r in files:
                rel_path = r["rel_path"]
                lang = r["language"]
                if r["category"] not in {"source", "infra"}:
                    continue
                src = (root / rel_path).resolve()
                if not src.exists() or not src.is_file():
                    continue
                c_text = read_utf8_lenient(src)
                if not c_text:
                    continue

                if lang == "cobol":
                    cached_enrich = cobol_enrich_cache.get(rel_path)
                    if cached_enrich is not None:
                        facts, diag = cached_enrich
                    else:
                        facts, diag = extract_cobol_enrichment_facts(c_text, rel_path)
                    for f in facts:
                        _fact_rows.append(
                            (
                                req.snapshot_id,
                                rel_path,
                                "cobol",
                                f["fact_type"],
                                f["semantic_key"],
                                f["occurrence_ix"],
                                f.get("parent_key"),
                                f.get("name"),
                                f.get("value"),
                                json.dumps(f.get("attributes", {})),
                                f["line_start"],
                                f["line_end"],
                                f.get("extractor", "cobol_antlr"),
                                f.get("extractor_ver", "1.0.0"),
                                now,
                            )
                        )
                    _diag_rows.append(
                        (
                            req.snapshot_id,
                            diag["rel_path"],
                            "cobol",
                            diag["status"],
                            diag["error_count"],
                            diag.get("first_error"),
                            diag.get("elapsed_ms", 0),
                            diag.get("extractor_ver", "1.0.0"),
                            now,
                        )
                    )
                    if diag.get("has_exec_sql") or diag.get("has_exec_cics"):
                        _diag_rows.append(
                            (
                                req.snapshot_id,
                                rel_path,
                                "cobol",
                                "skipped_unsupported",
                                0,
                                "EXEC SQL/CICS content parsing",
                                0,
                                "1.0.0",
                                now,
                            )
                        )
                elif lang == "jcl":
                    facts, diag = extract_jcl_enrichment_facts(c_text, rel_path)
                    for f in facts:
                        _fact_rows.append(
                            (
                                req.snapshot_id,
                                rel_path,
                                "jcl",
                                f["fact_type"],
                                f["semantic_key"],
                                f["occurrence_ix"],
                                f.get("parent_key"),
                                f.get("name"),
                                f.get("value"),
                                json.dumps(f.get("attributes", {})),
                                f["line_start"],
                                f["line_end"],
                                f.get("extractor", "jcl_antlr"),
                                f.get("extractor_ver", "1.0.0"),
                                now,
                            )
                        )
                    _diag_rows.append(
                        (
                            req.snapshot_id,
                            diag["rel_path"],
                            "jcl",
                            diag["status"],
                            diag["error_count"],
                            diag.get("first_error"),
                            diag.get("elapsed_ms", 0),
                            diag.get("extractor_ver", "1.0.0"),
                            now,
                        )
                    )
                    _diag_rows.append(
                        (
                            req.snapshot_id,
                            rel_path,
                            "jcl",
                            "skipped_unsupported",
                            0,
                            "symbolic resolution & PROC expansion",
                            0,
                            "1.0.0",
                            now,
                        )
                    )

            if _fact_rows:
                await db.executemany(
                    """
                    INSERT INTO source_facts
                    (snapshot_id, rel_path, language, fact_type, semantic_key, occurrence_ix, parent_key, name, value, attributes, line_start, line_end, extractor, extractor_ver, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    _fact_rows,
                )
                logger.info("[structural_graph] source_facts: inserted %d rows", len(_fact_rows))

            if _diag_rows:
                await db.executemany(
                    """
                    INSERT INTO source_parse_diagnostics
                    (snapshot_id, rel_path, language, status, error_count, first_error, elapsed_ms, extractor_ver, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    _diag_rows,
                )
                logger.info("[structural_graph] source_parse_diagnostics: inserted %d rows", len(_diag_rows))

        # Wire SymbolGraphBuilder into the pipeline
        if _SYMBOL_GRAPH_BUILDER_ENABLED:
            from ..symbol_graph import SymbolGraphBuilder

            # Build symbol edges from changed files only (reuse extraction cache)
            symbol_sources: dict[str, str] = {}
            for r in files_to_process:
                rel_path = r["rel_path"]
                category = r["category"]
                # Only process Python/TypeScript source files (not tests)
                if category not in {"source", "infra"}:
                    continue
                if _is_init_file(rel_path):
                    continue
                src = (root / rel_path).resolve()
                if not src.exists() or not src.is_file():
                    continue
                # Only for Python and TypeScript (SymbolGraphBuilder supports these)
                if r["language"] not in {"python", "typescript", "javascript"}:
                    continue
                content = read_utf8_lenient(src)
                if content:
                    symbol_sources[rel_path] = content

            if symbol_sources:
                try:
                    builder = SymbolGraphBuilder()
                    edges = builder.build(symbol_sources)
                    for edge in edges:
                        _symbol_edge_rows.append(
                            (
                                req.snapshot_id,
                                edge.src_symbol,
                                edge.dst_symbol,
                                edge.edge_type,
                                edge.confidence_score,
                                edge.resolution_method,
                                edge.confidence,
                                json.dumps(edge.evidence_lines),
                            )
                        )
                except Exception:
                    logger.exception("[structural_graph] SymbolGraphBuilder failed for snapshot %s", req.snapshot_id)

            if _symbol_edge_rows:
                # Deduplicate symbol edge rows by (snapshot_id, src_symbol, dst_symbol, edge_type)
                deduped_map: dict[tuple[str, str, str | None, str], tuple] = {}
                for row in _symbol_edge_rows:
                    snap, src_sym, dst_sym, edge_t, conf_score, res_meth, conf, ev_json = row
                    key = (snap, src_sym, dst_sym, edge_t)
                    if key in deduped_map:
                        existing = deduped_map[key]
                        try:
                            ev1 = json.loads(existing[7]) if existing[7] else []
                            ev2 = json.loads(ev_json) if ev_json else []
                            merged_ev = sorted(list(set(ev1 + ev2)))
                            merged_json = json.dumps(merged_ev)
                        except Exception:
                            merged_json = ev_json
                        deduped_map[key] = (snap, src_sym, dst_sym, edge_t, max(existing[4], conf_score), res_meth, conf, merged_json)
                    else:
                        deduped_map[key] = row
                final_symbol_rows = list(deduped_map.values())

                try:
                    await db.executemany(
                        """
                        INSERT OR IGNORE INTO symbol_graph_edges
                        (snapshot_id, src_symbol, dst_symbol, edge_type, confidence_score, resolution_method, confidence, evidence_lines)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        final_symbol_rows,
                    )
                    logger.info(
                        "[structural_graph] symbol_graph_edges: inserted %d rows for snapshot %s",
                        len(final_symbol_rows),
                        req.snapshot_id,
                    )
                except Exception:
                    logger.exception("[structural_graph] symbol_graph_edges insert failed for snapshot %s", req.snapshot_id)
        else:
            logger.info("[structural_graph] SymbolGraphBuilder wiring disabled via SYMBOL_GRAPH_BUILDER_ENABLED")

        all_nodes = {
            f for f in file_set
            if not _is_init_file(f) and f not in test_file_set
        }
        if native_graph and hasattr(native_graph, "compute_scores"):
            scored_raw = native_graph.compute_scores(sorted(all_nodes), edge_inputs)
        else:
            logger.info(
                "[structural_graph] native compute_scores unavailable; using Python fallback"
            )
            scored_raw = _compute_scores_python(sorted(all_nodes), edge_inputs)
        scored: list[GraphNodeScore] = [GraphNodeScore(**item) for item in scored_raw]
        top_central = scored[:30]

        native_toolchain = detect_cpp_toolchain()
        await db.execute(
            """
            INSERT INTO structural_graph_summaries
            (snapshot_id, total_nodes, total_edges, external_edges, entrypoints, top_central_files, generated_at, native_toolchain)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                req.snapshot_id,
                len(all_nodes),
                total_edges,
                external_edges,
                json.dumps(sorted(set(entrypoints))),
                json.dumps([s.model_dump() for s in top_central]),
                now,
                native_toolchain,
            ),
        )
        await db.commit()

        # Write cache entries for this snapshot
        await write_cache_db(db, req.snapshot_id, cache_result.sha256_map)

        # ──── Part B: Persistent graph.json ──────────────────────────────────
        try:
            from ..graph_json import build_graph_json_payload, write_graph_json

            graph_data = await self.export_graph_json(req.snapshot_id)
            payload = build_graph_json_payload(
                graph_data["nodes"], graph_data["edges"], graph_data.get("communities", [])
            )
            await write_graph_json(req.snapshot_id, self._data_dir, payload)
            logger.info("[structural_graph] graph.json written for snapshot %s", req.snapshot_id)
        except Exception as e:
            logger.warning(
                "[structural_graph] failed to write graph.json for snapshot %s: %s",
                req.snapshot_id,
                e,
            )

        # Fire community detection in the background — does not block build response.
        # WAL mode allows concurrent writes; failure is logged but non-fatal.
        async def _background_communities() -> None:
            try:
                await self.detect_communities(req.snapshot_id)
                logger.info("[structural_graph] community detection done for %s", req.snapshot_id)
                # Regenerate graph.json with community IDs after detection completes
                await self._on_community_detection_complete(req.snapshot_id)
            except Exception as exc:
                logger.warning("[structural_graph] community detection failed: %s", exc)

        asyncio.create_task(_background_communities())

        return BuildGraphResponse(
            summary=StructuralGraphSummary(
                snapshot_id=req.snapshot_id,
                total_nodes=len(all_nodes),
                total_edges=total_edges,
                external_edges=external_edges,
                entrypoints=sorted(set(entrypoints)),
                top_central_files=top_central,
                generated_at=now,
                native_toolchain=native_toolchain,
            )
        )
