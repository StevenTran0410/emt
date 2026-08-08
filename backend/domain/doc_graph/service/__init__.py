"""DocGraphService implementation split into Build, Query, and Export mixins."""

from ._build import _BuildMixin
from ._export import _ExportMixin
from ._queries import _QueryMixin


class DocGraphService(_BuildMixin, _QueryMixin, _ExportMixin):
    """Facade for doc_graph build, query, and export operations."""

    pass
