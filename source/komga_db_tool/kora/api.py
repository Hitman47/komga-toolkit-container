from __future__ import annotations

from typing import Optional

from ..api import (
    AuthConfig,
    HttpError,
    KomgaApi as CoreKomgaApi,
    normalize_base_url,
    safe_str,
)
from .models import LibraryItem, SeriesItem


class KomgaApi(CoreKomgaApi):
    """Kora domain adapter backed by the shared Komga HTTP client."""

    def __init__(self, url: str, auth: Optional[AuthConfig] = None, timeout: int = 30):
        super().__init__(url, auth=auth, timeout=timeout)

    def libraries(self) -> list[LibraryItem]:
        return [
            LibraryItem(id=item.id, name=item.name, raw=item.raw)
            for item in super().libraries()
        ]

    def series(
        self,
        library_id: str | None = None,
        search: str = "",
        page_size: int = 500,
    ) -> list[SeriesItem]:
        rows = super().series(
            library_id=library_id,
            search=search,
            page_size=page_size,
        )
        result: list[SeriesItem] = []
        for item in rows:
            library = item.raw.get("library")
            if not isinstance(library, dict):
                library = {}
            result.append(
                SeriesItem(
                    id=item.id,
                    library_id=item.library_id,
                    library_name=safe_str(library.get("name")),
                    title=item.title,
                    book_count=item.book_count,
                    metadata=item.metadata,
                    raw=item.raw,
                )
            )
        return result


__all__ = [
    "AuthConfig",
    "HttpError",
    "KomgaApi",
    "normalize_base_url",
    "safe_str",
]
