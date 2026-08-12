"""Build pipeline mixin for DocGraphService."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from infrastructure.db.database import get_db
from shared.logger import logger
from shared.utils import utc_now_iso

from .._flow_extract import extract_bd_flow
from .._flow_overlay import run_bd_flow_overlay
from .._graph_model import build_assertions_and_projection
from .._llm_citation import run_llm_citation_tier, run_llm_citation_tier_stream
from .._markdown_parser import parse_markdown_report
from .._mismatch import detect_mismatches
from ..types import (
    PARSER_VERSION,
    BuildBdFlowOnlyRequest,
    BuildBdFlowOnlyResponse,
    BuildDocGraphRequest,
    DocGraphMismatch,
    DocGraphSummary,
    ParsedDoc,
)


async def _save_bd_flow(db: Any, parsed_docs: list[ParsedDoc], cluster_id: str) -> None:
    """Extract and persist BD flow skeleton for all BD docs in cluster."""
    # Delete once for the whole cluster, not per doc — a second BD doc must not wipe the first's rows.
    await db.execute("DELETE FROM bd_flow_nodes WHERE cluster_id=?", (cluster_id,))
    await db.execute("DELETE FROM bd_flow_edges WHERE cluster_id=?", (cluster_id,))
    for doc in parsed_docs:
        if doc.doc_kind == "bd":
            try:
                flow_res = extract_bd_flow(doc, doc.id, cluster_id)

                if flow_res.nodes:
                    node_rows = [
                        (
                            n["id"], n["cluster_id"], n["doc_id"], n["node_kind"],
                            n["local_id"], n["binding"], n["binding_type"], n["label"],
                            n["ordinal"], n["guard_text"], n["source_locator"],
                            n["provenance_tier"], n["doc_line_start"], n["doc_line_end"],
                            n["attributes"], n["created_at"],
                        )
                        for n in flow_res.nodes
                    ]
                    await db.executemany(
                        """
                        INSERT INTO bd_flow_nodes
                        (id, cluster_id, doc_id, node_kind, local_id, binding, binding_type,
                         label, ordinal, guard_text, source_locator, provenance_tier,
                         doc_line_start, doc_line_end, attributes, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        node_rows,
                    )

                if flow_res.edges:
                    edge_rows = [
                        (
                            e["id"], e["cluster_id"], e["doc_id"], e["src_node_id"],
                            e["dst_node_id"], e["edge_kind"], e["label"], e["guard_text"],
                            e["provenance_tier"], e["doc_line"], e["attributes"], e["created_at"],
                        )
                        for e in flow_res.edges
                    ]
                    await db.executemany(
                        """
                        INSERT INTO bd_flow_edges
                        (id, cluster_id, doc_id, src_node_id, dst_node_id, edge_kind,
                         label, guard_text, provenance_tier, doc_line, attributes, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        edge_rows,
                    )
                await db.commit()
            except Exception as exc:
                logger.warning("[doc_graph] BD flow extraction failed for doc %s: %s", doc.id, exc)


def _canonicalize_dir_path(raw_path: str) -> str:
    path_obj = Path(raw_path).resolve()
    path_str = str(path_obj).replace("\\", "/")
    # Lowercase Windows drive letter e.g. D:/ -> d:/
    if len(path_str) >= 2 and path_str[1] == ":":
        path_str = path_str[0].lower() + path_str[1:]
    return path_str


def _hash_string(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


class _BuildMixin:
    """Build pipeline for DocGraphService."""

    def _prepare_source_files(
        self, source_dir_raw: str
    ) -> tuple[str, Path, list[Path], list[tuple[Path, str, str]], str, str]:
        source_dir = _canonicalize_dir_path(source_dir_raw)
        src_path = Path(source_dir)

        if not src_path.exists() or not src_path.is_dir():
            raise ValueError(f"Source directory does not exist or is not a directory: {source_dir}")

        md_files = sorted(list(src_path.glob("*.md")))
        bd_files = [f for f in md_files if "BD" in f.name.upper()]
        dd_files = [f for f in md_files if "DD" in f.name.upper()]

        if len(bd_files) != 1:
            raise ValueError(
                f"Source directory must contain exactly 1 BD report (found {len(bd_files)})"
            )
        if len(dd_files) < 1:
            raise ValueError(
                f"Source directory must contain at least 1 DD report (found {len(dd_files)})"
            )

        bd_file = bd_files[0]
        dd_cobol_files = [f for f in dd_files if "DD.COBOL" in f.name.upper()]
        dd_jcl_files = [f for f in dd_files if "DD.JCL" in f.name.upper()]
        other_dd_files = [
            f for f in dd_files if f not in dd_cobol_files and f not in dd_jcl_files
        ]

        ordered_files = [bd_file] + dd_cobol_files + dd_jcl_files + other_dd_files

        file_tuples: list[tuple[Path, str, str]] = []
        fingerprint_builder = hashlib.sha256()

        for f_path in ordered_files:
            content = f_path.read_text(encoding="utf-8")
            c_sha = _hash_string(content)
            file_tuples.append((f_path, content, c_sha))
            fingerprint_builder.update(f"{f_path.name}:{c_sha}".encode())

        input_fingerprint = fingerprint_builder.hexdigest()

        bd_content = file_tuples[0][1]
        cluster_name = bd_file.stem
        for line in bd_content.splitlines():
            if line.startswith("# Basic Design —"):
                raw_name = line.replace("# Basic Design —", "").strip()
                cluster_name = re.sub(r"\s+cluster$", "", raw_name, flags=re.IGNORECASE).strip()
                break
            elif line.startswith("# Basic Design"):
                raw_name = line.replace("# Basic Design", "").strip()
                cluster_name = (
                    re.sub(r"\s+cluster$", "", raw_name, flags=re.IGNORECASE).strip()
                    or cluster_name
                )
                break

        return source_dir, bd_file, ordered_files, file_tuples, input_fingerprint, cluster_name

    async def _validate_snapshot_binding(self, db: Any, snapshot_id: str | None) -> None:
        if not snapshot_id or not snapshot_id.strip():
            raise ValueError(
                "snapshot_id is required: doc graph must be bound to a repository snapshot"
            )
        async with db.execute(
            "SELECT 1 FROM repo_snapshots WHERE id=?", (snapshot_id.strip(),)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                raise ValueError(
                    f"Invalid snapshot_id: '{snapshot_id}' does not exist in repo_snapshots"
                )

    async def build_bd_flow_only(
        self, req: BuildBdFlowOnlyRequest
    ) -> BuildBdFlowOnlyResponse:
        """Parse BD file only and extract BD flow graph skeleton without requiring DD files or running legacy assertion pipeline."""
        p = Path(req.bd_path).resolve()
        if not p.exists() or not p.is_file():
            raise ValueError(f"BD file does not exist: {req.bd_path}")
        if p.suffix.lower() != ".md":
            raise ValueError(f"BD file must be a markdown file (.md): {req.bd_path}")
        if "BD" not in p.name.upper():
            raise ValueError(f"File does not appear to be a BD document (filename must contain 'BD'): {p.name}")

        content = p.read_text(encoding="utf-8")
        c_sha = _hash_string(content)
        parsed_doc = parse_markdown_report(str(p), content, c_sha)

        source_dir = _canonicalize_dir_path(str(p.parent))

        # Derive cluster_name using same logic as full build
        cluster_name = p.stem
        for line in content.splitlines():
            if line.startswith("# Basic Design —"):
                raw_name = line.replace("# Basic Design —", "").strip()
                cluster_name = re.sub(r"\s+cluster$", "", raw_name, flags=re.IGNORECASE).strip()
                break
            elif line.startswith("# Basic Design"):
                raw_name = line.replace("# Basic Design", "").strip()
                cluster_name = (
                    re.sub(r"\s+cluster$", "", raw_name, flags=re.IGNORECASE).strip()
                    or cluster_name
                )
                break

        cluster_id = _hash_string(f"{source_dir}:{cluster_name}")
        now = utc_now_iso()
        db = get_db()

        if req.snapshot_id:
            await self._validate_snapshot_binding(db, req.snapshot_id)

        # Upsert cluster row safely: do NOT delete existing row (prevents cascade wipe of docs/assertions)
        async with db.execute(
            "SELECT id FROM doc_graph_clusters WHERE id=?", (cluster_id,)
        ) as cur:
            row = await cur.fetchone()

        if not row:
            await db.execute(
                """
                INSERT INTO doc_graph_clusters
                (id, cluster_name, source_dir, bd_path, snapshot_id, input_fingerprint,
                 parser_version, status, generated_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?)
                """,
                (
                    cluster_id,
                    cluster_name,
                    source_dir,
                    str(p).replace("\\", "/"),
                    req.snapshot_id,
                    c_sha,
                    PARSER_VERSION,
                    now,
                    now,
                ),
            )
            await db.commit()
        elif req.snapshot_id:
            # Existing cluster: (re)bind to the provided snapshot. UPDATE is cascade-safe (no delete).
            await db.execute(
                "UPDATE doc_graph_clusters SET snapshot_id=? WHERE id=?",
                (req.snapshot_id, cluster_id),
            )
            await db.commit()

        # Extract and persist BD flow rows
        await _save_bd_flow(db, [parsed_doc], cluster_id)

        # Count extracted flow nodes and edges
        async with db.execute(
            "SELECT COUNT(*) as cnt FROM bd_flow_nodes WHERE cluster_id=?", (cluster_id,)
        ) as cur:
            node_cnt = (await cur.fetchone())["cnt"]
        async with db.execute(
            "SELECT COUNT(*) as cnt FROM bd_flow_edges WHERE cluster_id=?", (cluster_id,)
        ) as cur:
            edge_cnt = (await cur.fetchone())["cnt"]

        # Run optional LLM overlay phase if enabled
        overlay_counts = None
        if req.llm_enabled:
            try:
                await run_bd_flow_overlay(db, [parsed_doc], cluster_id, req.llm_provider_id)
                # The runner returns nothing — read the tier counts back from the claims table.
                overlay_counts = {"P1": 0, "P2": 0, "REJECTED": 0}
                async with db.execute(
                    "SELECT tier, COUNT(*) as cnt FROM bd_flow_overlay_claims WHERE cluster_id=? GROUP BY tier",
                    (cluster_id,),
                ) as cur:
                    for r in await cur.fetchall():
                        overlay_counts[r["tier"]] = r["cnt"]
            except Exception as exc:
                logger.warning("[doc_graph] BD flow overlay execution failed: %s", exc)

        return BuildBdFlowOnlyResponse(
            cluster_id=cluster_id,
            cluster_name=cluster_name,
            node_count=node_cnt,
            edge_count=edge_cnt,
            overlay_counts=overlay_counts,
        )

    async def build_bd_flow_only_stream(
        self, req: BuildBdFlowOnlyRequest
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Same skeleton build as build_bd_flow_only, but streams the overlay's per-chunk LLM
        activity (thinking/content deltas + outcome) as SSE events instead of only returning final
        counts. Skeleton (deterministic) phase is never streamed — only the overlay phase is."""
        try:
            p = Path(req.bd_path).resolve()
            if not p.exists() or not p.is_file():
                raise ValueError(f"BD file does not exist: {req.bd_path}")
            if p.suffix.lower() != ".md":
                raise ValueError(f"BD file must be a markdown file (.md): {req.bd_path}")
            if "BD" not in p.name.upper():
                raise ValueError(
                    f"File does not appear to be a BD document (filename must contain 'BD'): {p.name}"
                )

            content = p.read_text(encoding="utf-8")
            c_sha = _hash_string(content)
            parsed_doc = parse_markdown_report(str(p), content, c_sha)

            source_dir = _canonicalize_dir_path(str(p.parent))

            # Derive cluster_name using same logic as build_bd_flow_only
            cluster_name = p.stem
            for line in content.splitlines():
                if line.startswith("# Basic Design —"):
                    raw_name = line.replace("# Basic Design —", "").strip()
                    cluster_name = re.sub(r"\s+cluster$", "", raw_name, flags=re.IGNORECASE).strip()
                    break
                elif line.startswith("# Basic Design"):
                    raw_name = line.replace("# Basic Design", "").strip()
                    cluster_name = (
                        re.sub(r"\s+cluster$", "", raw_name, flags=re.IGNORECASE).strip()
                        or cluster_name
                    )
                    break

            cluster_id = _hash_string(f"{source_dir}:{cluster_name}")
            now = utc_now_iso()
            db = get_db()

            if req.snapshot_id:
                await self._validate_snapshot_binding(db, req.snapshot_id)

            async with db.execute(
                "SELECT id FROM doc_graph_clusters WHERE id=?", (cluster_id,)
            ) as cur:
                row = await cur.fetchone()

            if not row:
                await db.execute(
                    """
                    INSERT INTO doc_graph_clusters
                    (id, cluster_name, source_dir, bd_path, snapshot_id, input_fingerprint,
                     parser_version, status, generated_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?)
                    """,
                    (
                        cluster_id,
                        cluster_name,
                        source_dir,
                        str(p).replace("\\", "/"),
                        req.snapshot_id,
                        c_sha,
                        PARSER_VERSION,
                        now,
                        now,
                    ),
                )
                await db.commit()
            elif req.snapshot_id:
                # Existing cluster: (re)bind to the provided snapshot. UPDATE is cascade-safe (no delete).
                await db.execute(
                    "UPDATE doc_graph_clusters SET snapshot_id=? WHERE id=?",
                    (req.snapshot_id, cluster_id),
                )
                await db.commit()

            await _save_bd_flow(db, [parsed_doc], cluster_id)

            async with db.execute(
                "SELECT COUNT(*) as cnt FROM bd_flow_nodes WHERE cluster_id=?", (cluster_id,)
            ) as cur:
                node_cnt = (await cur.fetchone())["cnt"]
            async with db.execute(
                "SELECT COUNT(*) as cnt FROM bd_flow_edges WHERE cluster_id=?", (cluster_id,)
            ) as cur:
                edge_cnt = (await cur.fetchone())["cnt"]

            overlay_counts = None
            if req.llm_enabled:
                queue: asyncio.Queue[dict[str, Any] | object] = asyncio.Queue()
                sentinel = object()

                async def on_event(ev: dict[str, Any]) -> None:
                    await queue.put(ev)

                async def _run_overlay() -> None:
                    try:
                        await run_bd_flow_overlay(
                            db, [parsed_doc], cluster_id, req.llm_provider_id, on_event=on_event
                        )
                    except Exception as exc:
                        # Overlay is best-effort — a stream failure must not break the skeleton build.
                        logger.warning("[doc_graph] BD flow overlay stream failed: %s", exc)
                    finally:
                        await queue.put(sentinel)

                producer = asyncio.create_task(_run_overlay())
                while True:
                    item = await queue.get()
                    if item is sentinel:
                        break
                    yield item
                await producer

                overlay_counts = {"P1": 0, "P2": 0, "REJECTED": 0}
                async with db.execute(
                    "SELECT tier, COUNT(*) as cnt FROM bd_flow_overlay_claims WHERE cluster_id=? GROUP BY tier",
                    (cluster_id,),
                ) as cur:
                    for r in await cur.fetchall():
                        overlay_counts[r["tier"]] = r["cnt"]

            yield {
                "type": "done",
                "cluster_id": cluster_id,
                "node_count": node_cnt,
                "edge_count": edge_cnt,
                "overlay_counts": overlay_counts,
            }
        except Exception as e:
            logger.error(f"[doc_graph] Error in build_bd_flow_only_stream: {e}")
            yield {"type": "error", "message": str(e)}

    async def build(self, req: BuildDocGraphRequest) -> DocGraphSummary:
        """Build doc graph cluster (non-streaming, synchronous result)."""
        (
            source_dir,
            bd_file,
            ordered_files,
            file_tuples,
            input_fingerprint,
            cluster_name,
        ) = self._prepare_source_files(req.source_dir)

        cluster_id = _hash_string(f"{source_dir}:{cluster_name}")
        now = utc_now_iso()
        db = get_db()

        await self._validate_snapshot_binding(db, req.snapshot_id)

        if not req.force_rebuild:
            async with db.execute(
                "SELECT input_fingerprint, parser_version FROM doc_graph_clusters WHERE id=?",
                (cluster_id,),
            ) as cur:
                row = await cur.fetchone()
                if (
                    row
                    and row["input_fingerprint"] == input_fingerprint
                    and row["parser_version"] == PARSER_VERSION
                ):
                    logger.info(
                        "[doc_graph] Cache hit for cluster %s (%s)", cluster_id, cluster_name
                    )
                    return await self.summary(cluster_id)

        logger.info(
            "[doc_graph] Building cluster %s (%s) from %s", cluster_id, cluster_name, source_dir
        )

        parsed_docs: list[ParsedDoc] = [
            parse_markdown_report(str(f_path), content, c_sha)
            for f_path, content, c_sha in file_tuples
        ]

        assertions, nodes, edges = build_assertions_and_projection(parsed_docs)
        for n in nodes:
            n.cluster_id = cluster_id
            n.created_at = now
        for e in edges:
            e.cluster_id = cluster_id
            e.created_at = now

        mismatches = detect_mismatches(assertions, parsed_docs)
        for m in mismatches:
            m.cluster_id = cluster_id
            m.created_at = now

        await db.execute("BEGIN IMMEDIATE")
        try:
            await db.execute("DELETE FROM doc_graph_clusters WHERE id=?", (cluster_id,))
            await db.execute(
                """
                INSERT INTO doc_graph_clusters
                (id, cluster_name, source_dir, bd_path, snapshot_id, input_fingerprint,
                 parser_version, status, generated_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?)
                """,
                (
                    cluster_id,
                    cluster_name,
                    source_dir,
                    str(bd_file),
                    req.snapshot_id,
                    input_fingerprint,
                    PARSER_VERSION,
                    now,
                    now,
                ),
            )

            doc_rows = [
                (
                    f"{cluster_id}:{d.id}",
                    cluster_id,
                    d.doc_kind,
                    d.artifact_name,
                    d.doc_path,
                    d.content_sha256,
                    now,
                    json.dumps(
                        {
                            "headings": d.section_map.headings,
                            "steps": d.section_map.steps,
                        }
                    ),
                    now,
                )
                for d in parsed_docs
            ]
            await db.executemany(
                """
                INSERT INTO doc_graph_documents
                (id, cluster_id, doc_kind, artifact_name, doc_path, content_sha256,
                 generated_at, section_map, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                doc_rows,
            )

            assertion_rows = [
                (
                    cluster_id,
                    a.side,
                    a.predicate,
                    a.subject,
                    a.object,
                    a.value,
                    json.dumps(a.qualifiers),
                    a.status,
                    a.doc_id,
                    json.dumps(a.doc_span),
                    json.dumps(a.source_span),
                    a.confidence,
                    now,
                )
                for a in assertions
            ]
            await db.executemany(
                """
                INSERT INTO doc_graph_assertions
                (cluster_id, side, predicate, subject, object, value, qualifiers, status,
                 doc_id, doc_span, source_span, confidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                assertion_rows,
            )

            node_rows = [
                (
                    n.id,
                    cluster_id,
                    n.node_type,
                    n.display_name,
                    json.dumps(n.attributes),
                    json.dumps(n.provenance),
                    now,
                )
                for n in nodes
            ]
            await db.executemany(
                """
                INSERT INTO doc_graph_nodes
                (id, cluster_id, node_type, display_name, attributes, provenance, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                node_rows,
            )

            edge_rows = [
                (
                    cluster_id,
                    e.src_node_id,
                    e.dst_node_id,
                    e.edge_type,
                    e.edge_key,
                    json.dumps(e.attributes),
                    now,
                )
                for e in edges
            ]
            await db.executemany(
                """
                INSERT INTO doc_graph_edges
                (cluster_id, src_node_id, dst_node_id, edge_type, edge_key, attributes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                edge_rows,
            )

            llm_mismatches = await run_llm_citation_tier(parsed_docs, assertions, cluster_id, req)
            all_mismatches = mismatches + llm_mismatches

            mismatch_rows = [
                (
                    cluster_id,
                    m.fingerprint,
                    m.mismatch_type,
                    m.severity,
                    m.derivation,
                    json.dumps(m.bd_location) if m.bd_location else None,
                    json.dumps(m.dd_location) if m.dd_location else None,
                    m.description,
                    json.dumps(m.evidence) if m.evidence else None,
                    m.confidence,
                    now,
                )
                for m in all_mismatches
            ]
            await db.executemany(
                """
                INSERT INTO doc_graph_mismatches
                (cluster_id, fingerprint, mismatch_type, severity, derivation,
                 bd_location, dd_location, description, evidence, confidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                mismatch_rows,
            )

            await db.commit()
            await _save_bd_flow(db, parsed_docs, cluster_id)
            if req.llm_enabled:
                try:
                    await run_bd_flow_overlay(db, parsed_docs, cluster_id, req.llm_provider_id)
                except Exception as exc:
                    # Overlay is best-effort — the deterministic build must never fail because of it.
                    logger.warning(
                        "[doc_graph] BD flow prose overlay failed for cluster %s: %s", cluster_id, exc
                    )
            logger.info(
                "[doc_graph] Cluster %s built: %d docs, %d assertions, %d nodes, %d edges, "
                "%d mismatches (%d deterministic, %d llm)",
                cluster_id,
                len(parsed_docs),
                len(assertions),
                len(nodes),
                len(edges),
                len(all_mismatches),
                len(mismatches),
                len(llm_mismatches),
            )
        except Exception:
            await db.rollback()
            raise

        return await self.summary(cluster_id)

    async def build_stream(
        self, req: BuildDocGraphRequest
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Build doc graph cluster streaming progress and findings via SSE events."""
        try:
            (
                source_dir,
                bd_file,
                ordered_files,
                file_tuples,
                input_fingerprint,
                cluster_name,
            ) = self._prepare_source_files(req.source_dir)

            cluster_id = _hash_string(f"{source_dir}:{cluster_name}")
            now = utc_now_iso()
            db = get_db()

            await self._validate_snapshot_binding(db, req.snapshot_id)

            if not req.force_rebuild:
                async with db.execute(
                    "SELECT input_fingerprint, parser_version FROM doc_graph_clusters WHERE id=?",
                    (cluster_id,),
                ) as cur:
                    row = await cur.fetchone()
                    if (
                        row
                        and row["input_fingerprint"] == input_fingerprint
                        and row["parser_version"] == PARSER_VERSION
                    ):
                        summary = await self.summary(cluster_id)
                        mms = await self.mismatches(cluster_id)
                        det_mms = [
                            m.model_dump()
                            for m in mms.mismatches
                            if m.derivation == "deterministic"
                        ]
                        yield {
                            "type": "deterministic_done",
                            "summary": summary.model_dump(),
                            "mismatches": det_mms,
                        }
                        yield {"type": "done", "summary": summary.model_dump()}
                        return

            parsed_docs = [
                parse_markdown_report(str(f_path), content, c_sha)
                for f_path, content, c_sha in file_tuples
            ]

            assertions, nodes, edges = build_assertions_and_projection(parsed_docs)
            for n in nodes:
                n.cluster_id = cluster_id
                n.created_at = now
            for e in edges:
                e.cluster_id = cluster_id
                e.created_at = now

            mismatches = detect_mismatches(assertions, parsed_docs)
            for m in mismatches:
                m.cluster_id = cluster_id
                m.created_at = now

            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.execute("DELETE FROM doc_graph_clusters WHERE id=?", (cluster_id,))
                await db.execute(
                    """
                    INSERT INTO doc_graph_clusters
                    (id, cluster_name, source_dir, bd_path, snapshot_id, input_fingerprint,
                     parser_version, status, generated_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?)
                    """,
                    (
                        cluster_id,
                        cluster_name,
                        source_dir,
                        str(bd_file),
                        req.snapshot_id,
                        input_fingerprint,
                        PARSER_VERSION,
                        now,
                        now,
                    ),
                )

                doc_rows = [
                    (
                        f"{cluster_id}:{d.id}",
                        cluster_id,
                        d.doc_kind,
                        d.artifact_name,
                        d.doc_path,
                        d.content_sha256,
                        now,
                        json.dumps(
                            {
                                "headings": d.section_map.headings,
                                "steps": d.section_map.steps,
                            }
                        ),
                        now,
                    )
                    for d in parsed_docs
                ]
                await db.executemany(
                    """
                    INSERT INTO doc_graph_documents
                    (id, cluster_id, doc_kind, artifact_name, doc_path, content_sha256,
                     generated_at, section_map, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    doc_rows,
                )

                assertion_rows = [
                    (
                        cluster_id,
                        a.side,
                        a.predicate,
                        a.subject,
                        a.object,
                        a.value,
                        json.dumps(a.qualifiers),
                        a.status,
                        a.doc_id,
                        json.dumps(a.doc_span),
                        json.dumps(a.source_span),
                        a.confidence,
                        now,
                    )
                    for a in assertions
                ]
                await db.executemany(
                    """
                    INSERT INTO doc_graph_assertions
                    (cluster_id, side, predicate, subject, object, value, qualifiers, status,
                     doc_id, doc_span, source_span, confidence, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    assertion_rows,
                )

                node_rows = [
                    (
                        n.id,
                        cluster_id,
                        n.node_type,
                        n.display_name,
                        json.dumps(n.attributes),
                        json.dumps(n.provenance),
                        now,
                    )
                    for n in nodes
                ]
                await db.executemany(
                    """
                    INSERT INTO doc_graph_nodes
                    (id, cluster_id, node_type, display_name, attributes, provenance, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    node_rows,
                )

                edge_rows = [
                    (
                        cluster_id,
                        e.src_node_id,
                        e.dst_node_id,
                        e.edge_type,
                        e.edge_key,
                        json.dumps(e.attributes),
                        now,
                    )
                    for e in edges
                ]
                await db.executemany(
                    """
                    INSERT INTO doc_graph_edges
                    (cluster_id, src_node_id, dst_node_id, edge_type, edge_key,
                     attributes, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    edge_rows,
                )

                mismatch_rows = [
                    (
                        cluster_id,
                        m.fingerprint,
                        m.mismatch_type,
                        m.severity,
                        m.derivation,
                        json.dumps(m.bd_location) if m.bd_location else None,
                        json.dumps(m.dd_location) if m.dd_location else None,
                        m.description,
                        json.dumps(m.evidence) if m.evidence else None,
                        m.confidence,
                        now,
                    )
                    for m in mismatches
                ]
                await db.executemany(
                    """
                    INSERT INTO doc_graph_mismatches
                    (cluster_id, fingerprint, mismatch_type, severity, derivation,
                     bd_location, dd_location, description, evidence, confidence, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    mismatch_rows,
                )

                await db.commit()
                await _save_bd_flow(db, parsed_docs, cluster_id)
                if req.llm_enabled:
                    try:
                        await run_bd_flow_overlay(db, parsed_docs, cluster_id, req.llm_provider_id)
                    except Exception as exc:
                        # Overlay is best-effort — the deterministic build must never fail because of it.
                        logger.warning(
                            "[doc_graph] BD flow prose overlay failed for cluster %s: %s",
                            cluster_id,
                            exc,
                        )
            except Exception:
                await db.rollback()
                raise

            det_summary = await self.summary(cluster_id)
            yield {
                "type": "deterministic_done",
                "summary": det_summary.model_dump(),
                "mismatches": [m.model_dump() for m in mismatches],
            }

            # LLM Stream Phase
            async for event in run_llm_citation_tier_stream(
                parsed_docs, assertions, cluster_id, req
            ):
                if event.get("type") == "llm_progress":
                    yield event
                elif event.get("type") == "llm_finding" and event.get("mismatch"):
                    m_data = event["mismatch"]
                    m = DocGraphMismatch(**m_data)
                    # Persist LLM finding row incrementally
                    await db.execute(
                        """
                        INSERT OR IGNORE INTO doc_graph_mismatches
                        (cluster_id, fingerprint, mismatch_type, severity, derivation,
                         bd_location, dd_location, description, evidence, confidence, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            cluster_id,
                            m.fingerprint,
                            m.mismatch_type,
                            m.severity,
                            m.derivation,
                            json.dumps(m.bd_location) if m.bd_location else None,
                            json.dumps(m.dd_location) if m.dd_location else None,
                            m.description,
                            json.dumps(m.evidence) if m.evidence else None,
                            m.confidence,
                            now,
                        ),
                    )
                    await db.commit()
                    yield {"type": "llm_finding", "mismatch": m.model_dump()}

            final_summary = await self.summary(cluster_id)
            yield {"type": "done", "summary": final_summary.model_dump()}

        except Exception as e:
            logger.error(f"[doc_graph] Error in build_stream: {e}")
            yield {"type": "error", "message": str(e)}
