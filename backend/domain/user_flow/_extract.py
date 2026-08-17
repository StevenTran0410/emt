"""Deterministic extraction of raw cells and blocks from customer Excel workbooks (TICKET U1).

Calibrated to the real customer test scenario files:
- openpyxl data_only=True with error formula filtering
- Verbatim preservation of Japanese text, half-width kana, circled digits
- Vertical merge propagation with vspan_from, anchor-only horizontal merges
- Block segmentation at >=2 empty rows
- Oversize block sub-splitting (<=40 rows) with 3-row context propagation
"""
from __future__ import annotations

import datetime
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import openpyxl

ERROR_CELL_VALUES = {
    "#N/A",
    "#REF!",
    "#VALUE!",
    "#NAME?",
    "#DIV/0!",
    "#NULL!",
    "#NUM!",
}

OVERSIZE_ROW_LIMIT = 80
OVERSIZE_CHAR_LIMIT = 6000
CHUNK_ROW_SIZE = 40
CONTEXT_ROW_COUNT = 3


@dataclass
class RawCell:
    col: int
    value: str
    vspan_from: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"col": self.col, "value": self.value}
        if self.vspan_from is not None:
            d["vspan_from"] = self.vspan_from
        return d


@dataclass
class RawRow:
    row_ix: int
    indent_col: int
    cells: list[RawCell] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_ix": self.row_ix,
            "indent_col": self.indent_col,
            "cells": [c.to_dict() for c in self.cells],
        }


@dataclass
class Block:
    block_ix: int
    chunk_ix: int
    row_start: int
    row_end: int
    rows: list[RawRow] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_ix": self.block_ix,
            "chunk_ix": self.chunk_ix,
            "row_start": self.row_start,
            "row_end": self.row_end,
            "rows": [r.to_dict() for r in self.rows],
        }


@dataclass
class SheetExtract:
    sheet: str
    hidden: bool
    n_rows: int
    n_cols: int
    blocks: list[Block] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sheet": self.sheet,
            "hidden": self.hidden,
            "n_rows": self.n_rows,
            "n_cols": self.n_cols,
            "blocks": [b.to_dict() for b in self.blocks],
        }


@dataclass
class WorkbookExtract:
    file_hash: str
    sheets: list[SheetExtract] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_hash": self.file_hash,
            "sheets": [s.to_dict() for s in self.sheets],
        }


def coerce_cell_value(val: Any) -> str | None:
    """Coerce cell value to string; drop error formulas and empty values; truncate > 2000 chars."""
    if val is None:
        return None
    if isinstance(val, str):
        s = val.strip()
        if not s or s in ERROR_CELL_VALUES:
            return None
        if len(s) > 2000:
            s = s[:2000] + "…[TRUNCATED]"
        return s
    if isinstance(val, (datetime.datetime, datetime.date, datetime.time)):
        s = val.isoformat().strip()
        if not s or s in ERROR_CELL_VALUES:
            return None
        if len(s) > 2000:
            s = s[:2000] + "…[TRUNCATED]"
        return s
    if isinstance(val, (int, float, bool)):
        s = str(val).strip()
        if not s or s in ERROR_CELL_VALUES:
            return None
        if len(s) > 2000:
            s = s[:2000] + "…[TRUNCATED]"
        return s
    s = str(val).strip()
    if not s or s in ERROR_CELL_VALUES:
        return None
    if len(s) > 2000:
        s = s[:2000] + "…[TRUNCATED]"
    return s


def extract_sheet(ws: Any) -> SheetExtract:
    """Extract raw cells, handle merges, and segment into blocks for a single worksheet."""
    hidden = ws.sheet_state != "visible"
    max_row = ws.max_row or 0
    max_col = ws.max_column or 0

    if max_row == 0 or max_col == 0:
        return SheetExtract(
            sheet=ws.title,
            hidden=hidden,
            n_rows=0,
            n_cols=0,
            blocks=[],
        )

    # 1. Read all anchor cells
    anchor_cells: dict[tuple[int, int], str] = {}
    for r_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        for c_idx, val in enumerate(row, start=1):
            cval = coerce_cell_value(val)
            if cval is not None:
                anchor_cells[(r_idx, c_idx)] = cval

    # 2. Handle merged cells: vertical merges propagate down anchor column; horizontal anchor-only
    propagated_cells: dict[tuple[int, int], tuple[str, int]] = {}
    for rng in ws.merged_cells.ranges:
        if rng.min_row < rng.max_row:
            # Vertical merge: anchor value is at (rng.min_row, rng.min_col)
            anchor_val = anchor_cells.get((rng.min_row, rng.min_col))
            if anchor_val is not None:
                for r in range(rng.min_row + 1, rng.max_row + 1):
                    # Propagate down the anchor column with vspan_from = rng.min_row
                    propagated_cells[(r, rng.min_col)] = (anchor_val, rng.min_row)

    # 3. Build RawRows
    row_map: dict[int, RawRow] = {}
    for r in range(1, max_row + 1):
        # Compute indent_col on the anchor-only view
        anchor_cols = [c for (row, c) in anchor_cells if row == r]
        indent_col = min(anchor_cols) if anchor_cols else 0

        # Collect cells in row r
        row_cols = sorted(
            set(
                [c for (row, c) in anchor_cells if row == r]
                + [c for (row, c) in propagated_cells if row == r]
            )
        )
        if not row_cols:
            continue

        raw_cells: list[RawCell] = []
        for c in row_cols:
            if (r, c) in anchor_cells:
                raw_cells.append(RawCell(col=c, value=anchor_cells[(r, c)], vspan_from=None))
            elif (r, c) in propagated_cells:
                val, vspan = propagated_cells[(r, c)]
                raw_cells.append(RawCell(col=c, value=val, vspan_from=vspan))

        if raw_cells:
            row_map[r] = RawRow(row_ix=r, indent_col=indent_col, cells=raw_cells)

    # 4. Block segmentation: split at runs of >= 2 empty rows
    non_empty_row_indices = sorted(row_map.keys())
    if not non_empty_row_indices:
        return SheetExtract(
            sheet=ws.title,
            hidden=hidden,
            n_rows=max_row,
            n_cols=max_col,
            blocks=[],
        )

    raw_blocks: list[list[RawRow]] = []
    current_block: list[RawRow] = [row_map[non_empty_row_indices[0]]]

    for prev_r, curr_r in zip(non_empty_row_indices[:-1], non_empty_row_indices[1:]):
        gap = curr_r - prev_r - 1
        if gap >= 2:
            raw_blocks.append(current_block)
            current_block = [row_map[curr_r]]
        else:
            current_block.append(row_map[curr_r])

    if current_block:
        raw_blocks.append(current_block)

    # 5. Oversize sub-splitting
    blocks: list[Block] = []
    block_counter = 1
    for raw_rows in raw_blocks:
        total_chars = sum(len(c.value) for r in raw_rows for c in r.cells)
        n_data_rows = len(raw_rows)

        if n_data_rows > OVERSIZE_ROW_LIMIT or total_chars > OVERSIZE_CHAR_LIMIT:
            num_chunks = (n_data_rows + CHUNK_ROW_SIZE - 1) // CHUNK_ROW_SIZE
            context_rows = raw_rows[: min(CONTEXT_ROW_COUNT, n_data_rows)]
            for chunk_idx in range(num_chunks):
                start_i = chunk_idx * CHUNK_ROW_SIZE
                end_i = min((chunk_idx + 1) * CHUNK_ROW_SIZE, n_data_rows)
                chunk_data_rows = raw_rows[start_i:end_i]

                if chunk_idx == 0:
                    chunk_rows = chunk_data_rows
                else:
                    chunk_rows = list(context_rows) + list(chunk_data_rows)

                r_start = min(r.row_ix for r in chunk_rows)
                r_end = max(r.row_ix for r in chunk_rows)

                blocks.append(
                    Block(
                        block_ix=block_counter,
                        chunk_ix=chunk_idx,
                        row_start=r_start,
                        row_end=r_end,
                        rows=chunk_rows,
                    )
                )
            block_counter += 1
        else:
            r_start = min(r.row_ix for r in raw_rows)
            r_end = max(r.row_ix for r in raw_rows)
            blocks.append(
                Block(
                    block_ix=block_counter,
                    chunk_ix=0,
                    row_start=r_start,
                    row_end=r_end,
                    rows=raw_rows,
                )
            )
            block_counter += 1

    return SheetExtract(
        sheet=ws.title,
        hidden=hidden,
        n_rows=max_row,
        n_cols=max_col,
        blocks=blocks,
    )


def extract_workbook(path: str) -> WorkbookExtract:
    """Extract raw sheets, rows, and blocks from an Excel workbook (.xlsx)."""
    file_bytes = Path(path).read_bytes()
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    wb = openpyxl.load_workbook(path, data_only=True)
    sheets: list[SheetExtract] = []
    for ws in wb.worksheets:
        sheets.append(extract_sheet(ws))

    return WorkbookExtract(file_hash=file_hash, sheets=sheets)
