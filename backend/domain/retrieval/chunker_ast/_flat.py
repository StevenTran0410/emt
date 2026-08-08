"""Flat chunking fallback (mirrors _split_chunks in service.py)."""
from __future__ import annotations

from ._types import ASTChunk


def _flat_chunks(source: str, target_size: int) -> list[ASTChunk]:
    """Flat sliding-window fallback for unsupported languages or parse failures."""
    clean = source.replace("\r\n", "\n")
    if len(clean) <= target_size:
        return [ASTChunk(text=clean, chunk_type="file", start_line=0,
                         end_line=clean.count("\n"), language="")]
    overlap = max(120, target_size // 8)
    result: list[ASTChunk] = []
    start = 0
    while start < len(clean):
        end = min(len(clean), start + target_size)
        piece = clean[start:end]
        result.append(ASTChunk(text=piece, chunk_type="block",
                                start_line=clean[:start].count("\n"),
                                end_line=clean[:end].count("\n"),
                                language=""))
        if end >= len(clean):
            break
        start = max(0, end - overlap)
    return result
