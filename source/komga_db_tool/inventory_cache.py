"""Bounded, read-only inventory snapshots and filtered views.

Page callers must copy their slice before exposing records to an editor. This
cache is for browsing only; mutation guards always read fresh Komga metadata.
"""
from __future__ import annotations

import json
from typing import Any

from .book_explorer import book_explorer_row, filter_book_rows, sort_book_rows
from .runtime import MemoryCache


class BookInventorySnapshot:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = tuple(rows)
        # Views only hold references into one snapshot, not duplicate metadata.
        self._views = MemoryCache(max_entries=8)

    @classmethod
    def load(cls, api: Any, library_id: str) -> "BookInventorySnapshot":
        series = api.series(library_id=library_id, page_size=500)
        books = api.books(library_id=library_id, page_size=500)
        parents = {str(row.id): row for row in series}
        return cls([book_explorer_row(book, parents.get(str(getattr(book, "series_id", "") or "")))
                    for book in books])

    def view(self, *, sort_field: str, descending: bool, **filters: Any) -> tuple[dict[str, Any], ...]:
        key = json.dumps({"filters": filters, "sort": sort_field, "descending": bool(descending)},
                         sort_keys=True, ensure_ascii=False,
                         default=lambda value: value.isoformat())
        return self._views.get_or_load(
            key,
            lambda: tuple(sort_book_rows(filter_book_rows(self.rows, **filters), sort_field, descending)),
            # A view cannot outlive its parent snapshot in the browsing pool.
            ttl_seconds=86400,
        )
