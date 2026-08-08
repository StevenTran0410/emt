"""Build pipeline mixin for DocGraphService."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from infrastructure.db.database import get_db
from shared.logger import logger
from shared.utils import utc_now_iso

from .._graph_model import build_assertions_and_projection
from .._llm_citation import run_llm_citation_tier, run_llm_citation_tier_stream
from .._markdown_parser import parse_markdown_report
from .._mismatch import detect_mismatches
from ..types import (
    PARSER_VERSION,
    BuildDocGraphRequest,
    DocGraphMismatch,
    DocGraphSummary,
    ParsedDoc,
)


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
    ) -> tuple[str, Path, list[Path], list[tuple[Path, str, str]], str]:
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
