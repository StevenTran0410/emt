"""Line-based deterministic parser for Mermaid state diagrams and flowcharts in BD reports."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .types import MermaidBlock

_STATE_DEF_REGEX = re.compile(r"^([A-Za-z0-9_]+)\s*:\s*(.+)$")
_STATE_CHOICE_REGEX = re.compile(r"^state\s+([A-Za-z0-9_]+)\s+<<choice>>", re.IGNORECASE)
_STATE_TRANS_REGEX = re.compile(r"^(\[\*\]|[A-Za-z0-9_]+)\s*-->\s*(\[\*\]|[A-Za-z0-9_]+)(?:\s*:\s*(.+))?$")
_NOTE_START_REGEX = re.compile(r"^note\s+(right|left)\s+of\s+([A-Za-z0-9_]+)", re.IGNORECASE)

_FLOW_EDGE_REGEX = re.compile(
    r"^([A-Za-z0-9_]+)(?:\[\"(.+?)\"\])?\s*-->\s*(?:\|([^|]+)\|)?\s*([A-Za-z0-9_]+)(?:\[\"(.+?)\"\])?$"
)
_FLOW_NODE_SOLO_REGEX = re.compile(r"^([A-Za-z0-9_]+)\[\"(.+?)\"\]$")
_EDGE_LABEL_MARK_REGEX = re.compile(r"^([A-Za-z0-9_]+)\s*([✅❌])?$")


@dataclass
class MermaidNode:
    local_id: str
    display_label: str
    binding: str | None
    binding_type: str | None
    node_kind: str  # step | decision | event | asset
    doc_line: int
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class MermaidEdge:
    src_local_id: str
    dst_local_id: str
    edge_kind: str  # transition | dependency
    label: str | None
    guard_text: str | None
    edge_type: str | None
    resolution_mark: str | None
    doc_line: int
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class MermaidNote:
    target_local_id: str
    text: str
    doc_line: int


@dataclass
class MermaidStateResult:
    nodes: list[MermaidNode] = field(default_factory=list)
    edges: list[MermaidEdge] = field(default_factory=list)
    choice_node_ids: list[str] = field(default_factory=list)
    notes: list[MermaidNote] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


@dataclass
class MermaidFlowchartResult:
    nodes: list[MermaidNode] = field(default_factory=list)
    edges: list[MermaidEdge] = field(default_factory=list)
    subgraphs: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


def _extract_binding(label: str) -> tuple[str | None, str | None, str]:
    """Extract (binding, binding_type, default_kind) from state node label."""
    # Check event ID pattern e.g. (HSBMENU5-Event-Init) or (FHNIXLOT-Event-Normal-1)
    ev_match = re.search(r"\((([A-Za-z0-9_-]+)-Event-([A-Za-z0-9_-]+))\)", label)
    if ev_match:
        return ev_match.group(1).strip(), "event", "event"

    # Check program binding pattern e.g. (HSBMENU5) or (PHNIXLOT -> HNDM001N)
    prog_match = re.search(r"\(([^)]+)\)", label)
    if prog_match:
        raw_prog = prog_match.group(1).strip()
        # Avoid generic descriptions like "Normal / FKey events"
        if not re.search(r"\bevents?\b|\bnormal\b", raw_prog, re.IGNORECASE):
            return raw_prog, "program", "step"

    return None, None, "step"


def parse_state_diagram(block: MermaidBlock) -> MermaidStateResult:
    """Parse mermaid stateDiagram-v2 block into nodes, transitions, choice nodes, and notes."""
    res = MermaidStateResult()
    raw_lines = block.text.splitlines()

    in_note = False
    note_target = ""
    note_lines: list[str] = []
    note_start_doc_line = 0

    known_nodes: dict[str, MermaidNode] = {}

    for idx, raw_line in enumerate(raw_lines):
        doc_line = block.fence_line + 1 + idx
        line = raw_line.strip()

        if not line:
            continue

        if in_note:
            if line.lower() == "end note":
                in_note = False
                res.notes.append(
                    MermaidNote(
                        target_local_id=note_target,
                        text="\n".join(note_lines),
                        doc_line=note_start_doc_line,
                    )
                )
                note_lines = []
                note_target = ""
            else:
                note_lines.append(raw_line)
            continue

        # Header/Dialect line
        if line.startswith("stateDiagram") or line.startswith("```"):
            continue

        # Styling/Class definitions
        if (
            line.startswith("style ")
            or line.startswith("class ")
            or line.startswith("classDef ")
        ):
            continue

        # Choice node declaration: state Decision1 <<choice>>
        c_match = _STATE_CHOICE_REGEX.match(line)
        if c_match:
            choice_id = c_match.group(1)
            res.choice_node_ids.append(choice_id)
            node = MermaidNode(
                local_id=choice_id,
                display_label=choice_id,
                binding=None,
                binding_type=None,
                node_kind="decision",
                doc_line=doc_line,
            )
            known_nodes[choice_id] = node
            continue

        # Note start: note right of Decision1
        n_match = _NOTE_START_REGEX.match(line)
        if n_match:
            in_note = True
            note_target = n_match.group(2)
            note_start_doc_line = doc_line
            note_lines = []
            continue

        # State definition line: Step1: Step1 Display ... <br/>(HSBMENU5)
        s_match = _STATE_DEF_REGEX.match(line)
        if s_match and "-->" not in line:
            local_id = s_match.group(1)
            label = s_match.group(2).strip()
            binding, binding_type, node_kind = _extract_binding(label)
            node = MermaidNode(
                local_id=local_id,
                display_label=label,
                binding=binding,
                binding_type=binding_type,
                node_kind=node_kind,
                doc_line=doc_line,
            )
            known_nodes[local_id] = node
            continue

        # Transition line: Step1 --> Step2: Enter OPT
        t_match = _STATE_TRANS_REGEX.match(line)
        if t_match:
            src = t_match.group(1)
            dst = t_match.group(2)
            trans_label = t_match.group(3).strip() if t_match.group(3) else None

            # Handle transition with inline binding e.g. [*] --> Step1: Step1 Display ...<br/>(HSBMENU5)
            if trans_label and trans_label.startswith(f"{dst} "):
                # Inline state definition inside transition
                binding, binding_type, node_kind = _extract_binding(trans_label)
                if dst not in known_nodes and dst != "[*]":
                    known_nodes[dst] = MermaidNode(
                        local_id=dst,
                        display_label=trans_label,
                        binding=binding,
                        binding_type=binding_type,
                        node_kind=node_kind,
                        doc_line=doc_line,
                    )

            # Record edge if non-pseudo
            if src != "[*]" or dst != "[*]":
                is_decision_src = src in res.choice_node_ids or "decision" in src.lower()
                res.edges.append(
                    MermaidEdge(
                        src_local_id=src,
                        dst_local_id=dst,
                        edge_kind="transition",
                        label=trans_label,
                        guard_text=trans_label if is_decision_src else None,
                        edge_type=None,
                        resolution_mark=None,
                        doc_line=doc_line,
                    )
                )

                # Ensure src/dst are registered as nodes if not already known
                if src != "[*]" and src not in known_nodes:
                    kind = "decision" if src in res.choice_node_ids else "step"
                    known_nodes[src] = MermaidNode(
                        local_id=src,
                        display_label=src,
                        binding=None,
                        binding_type=None,
                        node_kind=kind,
                        doc_line=doc_line,
                    )
                if dst != "[*]" and dst not in known_nodes:
                    kind = "decision" if dst in res.choice_node_ids else "step"
                    known_nodes[dst] = MermaidNode(
                        local_id=dst,
                        display_label=dst,
                        binding=None,
                        binding_type=None,
                        node_kind=kind,
                        doc_line=doc_line,
                    )
            continue

        # Unrecognized line inside block
        res.diagnostics.append(f"Unrecognized state line at doc_line {doc_line}: {line}")

    res.nodes = list(known_nodes.values())
    return res


def parse_flowchart(block: MermaidBlock) -> MermaidFlowchartResult:
    """Parse mermaid flowchart / graph block into asset nodes and dependency edges."""
    res = MermaidFlowchartResult()
    raw_lines = block.text.splitlines()
    known_nodes: dict[str, MermaidNode] = {}

    for idx, raw_line in enumerate(raw_lines):
        doc_line = block.fence_line + 1 + idx
        line = raw_line.strip()

        if not line:
            continue

        # Header line
        if (
            line.startswith("flowchart")
            or line.startswith("graph ")
            or line.startswith("```")
        ):
            continue

        # Styling / Class definitions
        if (
            line.startswith("style ")
            or line.startswith("class ")
            or line.startswith("classDef ")
            or line.startswith("subgraph")
            or line == "end"
        ):
            continue

        # Edge line: N2["HNDM001N<br/>(JCL)"] --> |JCL_EXEC✅| N15["HNDM130<br/>(COBOL)"]
        e_match = _FLOW_EDGE_REGEX.match(line)
        if e_match:
            src_id = e_match.group(1)
            src_label = e_match.group(2)
            edge_lbl = e_match.group(3)
            dst_id = e_match.group(4)
            dst_label = e_match.group(5)

            # Process src node
            if src_id not in known_nodes:
                name, asset_type = _parse_flow_node_label(src_id, src_label)
                known_nodes[src_id] = MermaidNode(
                    local_id=src_id,
                    display_label=src_label or src_id,
                    binding=name,
                    binding_type=asset_type,
                    node_kind="asset",
                    doc_line=doc_line,
                )

            # Process dst node
            if dst_id not in known_nodes:
                name, asset_type = _parse_flow_node_label(dst_id, dst_label)
                known_nodes[dst_id] = MermaidNode(
                    local_id=dst_id,
                    display_label=dst_label or dst_id,
                    binding=name,
                    binding_type=asset_type,
                    node_kind="asset",
                    doc_line=doc_line,
                )

            # Process edge label (edge_type + resolution_mark)
            edge_type = None
            resolution_mark = None
            if edge_lbl:
                lbl_match = _EDGE_LABEL_MARK_REGEX.match(edge_lbl.strip())
                if lbl_match:
                    edge_type = lbl_match.group(1)
                    resolution_mark = lbl_match.group(2)
                else:
                    edge_type = edge_lbl.strip()

            res.edges.append(
                MermaidEdge(
                    src_local_id=src_id,
                    dst_local_id=dst_id,
                    edge_kind="dependency",
                    label=edge_lbl,
                    guard_text=None,
                    edge_type=edge_type,
                    resolution_mark=resolution_mark,
                    doc_line=doc_line,
                )
            )
            continue

        # Solo node definition line: N1["PHNIXLOT<br/>(CLIST)"]
        n_match = _FLOW_NODE_SOLO_REGEX.match(line)
        if n_match:
            node_id = n_match.group(1)
            label = n_match.group(2)
            if node_id not in known_nodes:
                name, asset_type = _parse_flow_node_label(node_id, label)
                known_nodes[node_id] = MermaidNode(
                    local_id=node_id,
                    display_label=label,
                    binding=name,
                    binding_type=asset_type,
                    node_kind="asset",
                    doc_line=doc_line,
                )
            continue

        res.diagnostics.append(f"Unrecognized flowchart line at doc_line {doc_line}: {line}")

    res.nodes = list(known_nodes.values())
    return res


def _parse_flow_node_label(local_id: str, label: str | None) -> tuple[str, str | None]:
    """Parse node name and asset type from label like 'HNDM001N<br/>(JCL)'."""
    if not label:
        return local_id, None

    # Handle HTML tags like <br/> or <br>
    clean = label.replace("<br/>", "<br>").replace("<BR/>", "<br>")
    parts = clean.split("<br>")
    name = parts[0].strip()

    asset_type = None
    if len(parts) > 1:
        t_match = re.search(r"\(([^)]+)\)", parts[1])
        if t_match:
            asset_type = t_match.group(1).strip().lower()

    return name, asset_type
