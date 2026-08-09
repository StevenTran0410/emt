"""Canonical Code Relation Adapter for Doc↔Code Relation Comparison.

Translates code substrate data (source_facts and symbol_graph_edges) into canonical
document graph namespace keys for relational comparison.
"""

from typing import Any, NamedTuple


class CanonicalCodeRelation(NamedTuple):
    snapshot_id: str
    predicate: str
    subject_key: str
    object_key: str
    occurrence_key: str
    rel_path: str
    line_start: int
    line_end: int
    source_store: str  # 'source_facts' or 'symbol_graph_edges'


async def load_canonical_code_relations(
    db: Any,
    snapshot_id: str,
    scope_programs: set[str],
    scope_jobs: set[str],
) -> list[CanonicalCodeRelation]:
    """Extract and normalize all canonical relations from code substrates
    for the given cluster scope.
    """

    # 1. Fetch program names present in snapshot to distinguish program vs extroutine
    async with db.execute(
        "SELECT DISTINCT name FROM source_facts WHERE snapshot_id=? AND fact_type='program'",
        (snapshot_id,),
    ) as cur:
        prog_rows = await cur.fetchall()
    known_programs = {r["name"].strip().upper() for r in prog_rows if r["name"]}

    relations: list[CanonicalCodeRelation] = []

    # 2. Fetch calls, steps, dds from source_facts
    async with db.execute(
        """
        SELECT id, fact_type, semantic_key, parent_key, rel_path, name, value, line_start, line_end
        FROM source_facts
        WHERE snapshot_id=? AND fact_type IN ('call', 'step', 'dd')
        """,
        (snapshot_id,),
    ) as cur:
        facts = [dict(r) for r in await cur.fetchall()]

    for f in facts:
        ft = f["fact_type"]
        skey = f["semantic_key"]
        pkey = f["parent_key"] or ""
        rpath = f["rel_path"]
        lstart = f["line_start"]
        lend = f["line_end"]

        if ft == "call":
            # skey format: call/{prog}.{para}.{callee}
            # subject = program/{prog}
            parts = skey.split("/")
            if len(parts) >= 2:
                prog_para_callee = parts[1]
                sub_parts = prog_para_callee.split(".")
                prog_name = sub_parts[0].upper()
                callee_name = f["name"] or sub_parts[-1].upper()

                if prog_name in scope_programs:
                    sub_key = f"program/{prog_name}"
                    obj_key = (
                        f"program/{callee_name}"
                        if callee_name in known_programs
                        else f"extroutine/{callee_name}"
                    )
                    relations.append(
                        CanonicalCodeRelation(
                            snapshot_id=snapshot_id,
                            predicate="calls",
                            subject_key=sub_key,
                            object_key=obj_key,
                            occurrence_key=skey,
                            rel_path=rpath,
                            line_start=lstart,
                            line_end=lend,
                            source_store="source_facts",
                        )
                    )

        elif ft == "step":
            # skey format: step/{J}.{S}
            # parent_key format: job/{J}
            parts = skey.split("/")
            if len(parts) >= 2:
                job_step = parts[1]
                job_name = job_step.split(".")[0].upper()
                if job_name in scope_jobs:
                    # Relation 1: runs (job -> step)
                    job_key = f"job/{job_name}"
                    step_key = f"step/{job_step}"
                    relations.append(
                        CanonicalCodeRelation(
                            snapshot_id=snapshot_id,
                            predicate="runs",
                            subject_key=job_key,
                            object_key=step_key,
                            occurrence_key=skey,
                            rel_path=rpath,
                            line_start=lstart,
                            line_end=lend,
                            source_store="source_facts",
                        )
                    )

                    # Relation 2: runs (step -> program)
                    pgm_val = f["value"] or f["name"]
                    if pgm_val:
                        pgm_name = pgm_val.strip().upper()
                        pgm_obj_key = (
                            f"program/{pgm_name}"
                            if pgm_name in known_programs
                            else f"extroutine/{pgm_name}"
                        )
                        relations.append(
                            CanonicalCodeRelation(
                                snapshot_id=snapshot_id,
                                predicate="runs",
                                subject_key=step_key,
                                object_key=pgm_obj_key,
                                occurrence_key=f"{skey}->program/{pgm_name}",
                                rel_path=rpath,
                                line_start=lstart,
                                line_end=lend,
                                source_store="source_facts",
                            )
                        )

        elif ft == "dd":
            # skey format: dd/{J}.{S}.{DD}
            # parent_key format: step/{J}.{S}
            if pkey.startswith("step/"):
                step_part = pkey.split("/", 1)[1]
                job_name = step_part.split(".")[0].upper()
                if job_name in scope_jobs:
                    step_key = f"step/{step_part}"
                    dd_key = skey
                    relations.append(
                        CanonicalCodeRelation(
                            snapshot_id=snapshot_id,
                            predicate="binds_dd",
                            subject_key=step_key,
                            object_key=dd_key,
                            occurrence_key=skey,
                            rel_path=rpath,
                            line_start=lstart,
                            line_end=lend,
                            source_store="source_facts",
                        )
                    )

    # 3. Fetch copies from symbol_graph_edges
    async with db.execute(
        """
        SELECT src_symbol, dst_symbol
        FROM symbol_graph_edges
        WHERE snapshot_id=? AND edge_type='copies'
        """,
        (snapshot_id,),
    ) as cur:
        copy_edges = [dict(r) for r in await cur.fetchall()]

    for edge in copy_edges:
        src = edge["src_symbol"]
        dst = edge["dst_symbol"]
        src_name = src.split("::")[-1].upper() if "::" in src else src.upper()
        dst_name = dst.split("::")[-1].upper() if "::" in dst else dst.upper()

        if src_name in scope_programs:
            relations.append(
                CanonicalCodeRelation(
                    snapshot_id=snapshot_id,
                    predicate="copies",
                    subject_key=f"program/{src_name}",
                    object_key=f"copybook/{dst_name}",
                    occurrence_key=f"{src}->{dst}",
                    rel_path="",
                    line_start=0,
                    line_end=0,
                    source_store="symbol_graph_edges",
                )
            )

    return relations
