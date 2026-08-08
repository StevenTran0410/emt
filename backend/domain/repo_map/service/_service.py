"""RepoMapService: thin class facade wiring build/query/search operations."""
from infrastructure.db.database import get_db

from ..types import (
    BuildRepoMapRequest,
    BuildRepoMapResponse,
    RepoMapCsvResponse,
    RepoMapSummary,
    SymbolsResponse,
)
from ._build import _run_build
from ._query import _export_csv, _get_summary, _list_symbols
from ._search import search_symbols_cascade


class RepoMapService:
    async def build(self, req: BuildRepoMapRequest) -> BuildRepoMapResponse:
        return await _run_build(req)

    async def summary(self, snapshot_id: str) -> RepoMapSummary:
        return await _get_summary(snapshot_id)

    async def symbols(
        self,
        snapshot_id: str,
        limit: int = 500,
        path_prefix: str | None = None,
    ) -> SymbolsResponse:
        return await _list_symbols(snapshot_id, limit, path_prefix)

    async def search(self, snapshot_id: str, q: str, limit: int = 120) -> SymbolsResponse:
        symbols = await search_symbols_cascade(get_db(), snapshot_id, q, limit=limit)
        return SymbolsResponse(snapshot_id=snapshot_id, symbols=symbols)

    async def export_csv(self, snapshot_id: str, exclude_tests: bool = True) -> RepoMapCsvResponse:
        return await _export_csv(snapshot_id, exclude_tests)
