"""Data types for AST chunking: the public chunk output type and the internal
node-span type produced during traversal.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ASTChunk:
    """A semantically coherent code chunk produced by the AST chunker."""

    text: str
    chunk_type: str              # 'function' | 'class' | 'import_group' | 'block' | 'file' | 'barrel'
    node_names: list[str] = field(default_factory=list)
    start_line: int = 0
    end_line: int = 0
    language: str = ""
    split_part: int = 0          # 0-indexed position among split_of parts of one oversized function/class
    split_of: int = 1            # >1 means this chunk is one piece of a single symbol split for size


@dataclass
class _NodeSpan:
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int
    node_type: str
    name: str          # best-effort symbol name, empty if not determinable
    is_import: bool
    split_part: int = 0
    split_of: int = 1
