from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from .api import HttpError, KomgaApi
from .runtime import MemoryCache


class CachedKomgaService:
    def __init__(
        self,
        api: KomgaApi,
        cache: MemoryCache,
        *,
        content_ttl_seconds: float = 300,
        thumbnail_ttl_seconds: float = 900,
    ):
        self.api = api
        self.cache = cache
        self.content_ttl_seconds = max(1.0, float(content_ttl_seconds))
        self.thumbnail_ttl_seconds = max(1.0, float(thumbnail_ttl_seconds))

    def __getattr__(self, name: str) -> Any:
        return getattr(self.api, name)

    def libraries(self, force: bool = False) -> list[Any]:
        key = "komga:libraries"
        if force:
            self.cache.invalidate(key)
        return self.cache.get_or_load(key, self.api.libraries, ttl_seconds=600)

    def series(
        self,
        library_id: str | None = None,
        search: str = "",
        page_size: int = 500,
        force: bool = False,
    ) -> list[Any]:
        key = f"komga:series:{library_id or '*'}:{search.strip().casefold()}:{page_size}"
        if force:
            self.cache.invalidate(key)
            self.cache.invalidate("komga:inventory-pool")
        return self.cache.get_or_load(
            key,
            lambda: self.api.series(library_id, search=search, page_size=page_size),
            ttl_seconds=self.content_ttl_seconds,
        )

    def books(
        self,
        library_id: str | None = None,
        series_id: str | None = None,
        search: str = "",
        page_size: int = 1000,
        direct_series_only: bool = False,
        timeout: int | None = None,
        force: bool = False,
    ) -> list[Any]:
        key = (
            f"komga:books:{library_id or '*'}:{series_id or '*'}:"
            f"{search.strip().casefold()}:{page_size}:"
            f"direct={bool(direct_series_only)}:timeout={timeout or '*'}"
        )
        if force:
            self.cache.invalidate(key)
            self.cache.invalidate("komga:inventory-pool")
        return self.cache.get_or_load(
            key,
            lambda: self.api.books(
                library_id=library_id,
                series_id=series_id,
                search=search,
                page_size=page_size,
                direct_series_only=direct_series_only,
                timeout=timeout,
            ),
            ttl_seconds=self.content_ttl_seconds,
        )

    def thumbnail_bytes(
        self,
        target_type: str,
        target_id: str,
        force: bool = False,
    ) -> bytes:
        key = f"komga:thumbnail:{target_type}:{target_id}"
        if force:
            self.cache.invalidate(key)
        return self.cache.get_or_load(
            key,
            lambda: self.api.thumbnail_bytes(target_type, target_id),
            ttl_seconds=self.thumbnail_ttl_seconds,
        )

    def _cached_read(self, operation: str, arguments: dict[str, Any], loader: Any) -> Any:
        key = "komga:" + operation + ":" + json.dumps(arguments, sort_keys=True, ensure_ascii=False)
        # Callers may annotate a row (e.g. its parent title); never mutate the
        # shared cache or another request's response in doing so.
        return deepcopy(self.cache.get_or_load(key, loader, ttl_seconds=self.content_ttl_seconds))

    def series_page(self, **kwargs: Any) -> dict[str, Any]:
        return self._cached_read("series_page", kwargs, lambda: self.api.series_page(**kwargs))

    def books_page(self, **kwargs: Any) -> dict[str, Any]:
        return self._cached_read("books_page", kwargs, lambda: self.api.books_page(**kwargs))

    def browse_series(self, series_id: str) -> dict[str, Any]:
        """Short-lived display cache; write/preview guards still use get_series."""
        return self._cached_read("series_detail", {"id": series_id}, lambda: self.api.get_series(series_id))

    def invalidate_content(self) -> None:
        self.cache.invalidate("komga:")

    def book_inventory_snapshot(self, library_id: str) -> Any:
        from .inventory_cache import BookInventorySnapshot

        # Shared between per-request Web wrappers, isolated by connection via
        # the existing outer cache. An invalidation discards the whole pool,
        # including snapshots/views still being built by obsolete requests.
        pool = self.cache.get_or_load("komga:inventory-pool", lambda: MemoryCache(max_entries=2),
                                      ttl_seconds=86400)
        return pool.get_or_load(
            library_id,
            # Read the underlying API directly: deriving a new snapshot from
            # old cached rows would silently extend their freshness window.
            lambda: BookInventorySnapshot.load(self.api, library_id),
            ttl_seconds=self.content_ttl_seconds,
        )

    def update_series_metadata(self, series_id: str, metadata: dict[str, Any]) -> Any:
        result = self.api.update_series_metadata(series_id, metadata)
        self.invalidate_content()
        return result

    def update_book_metadata(self, book_id: str, metadata: dict[str, Any]) -> Any:
        result = self.api.update_book_metadata(book_id, metadata)
        self.invalidate_content()
        return result

    def update_books_metadata_batch(self, updates_by_book_id: dict[str, dict[str, Any]]) -> Any:
        result = self.api.update_books_metadata_batch(updates_by_book_id)
        self.invalidate_content()
        return result

    def add_thumbnail(self, target_type: str, target_id: str, file_path: str) -> Any:
        result = self.api.add_thumbnail(target_type, target_id, file_path)
        self.cache.invalidate(f"komga:thumbnail:{target_type}:{target_id}")
        return result

    def select_thumbnail(self, target_type: str, target_id: str, thumbnail_id: str) -> Any:
        result = self.api.select_thumbnail(target_type, target_id, thumbnail_id)
        self.cache.invalidate(f"komga:thumbnail:{target_type}:{target_id}")
        return result


class KoraSharedApiAdapter:
    def __init__(self, api: Any):
        self.api = api

    def test(self) -> str:
        return self.api.test()

    def libraries(self) -> list[Any]:
        from .kora.models import LibraryItem

        return [
            LibraryItem(id=item.id, name=item.name, raw=item.raw)
            for item in self.api.libraries()
        ]

    def series(
        self,
        library_id: str | None = None,
        search: str = "",
        page_size: int = 500,
    ) -> list[Any]:
        from .kora.models import SeriesItem

        result = []
        for item in self.api.series(
            library_id=library_id,
            search=search,
            page_size=page_size,
        ):
            library = item.raw.get("library")
            if not isinstance(library, dict):
                library = {}
            result.append(
                SeriesItem(
                    id=item.id,
                    library_id=item.library_id,
                    library_name=str(library.get("name") or ""),
                    title=item.title,
                    book_count=int(item.book_count or 0),
                    metadata=item.metadata,
                    raw=item.raw,
                )
            )
        return result

    def get_series(self, series_id: str) -> dict[str, Any]:
        return self.api.get_series(series_id)

    def update_series_metadata(
        self,
        series_id: str,
        metadata: dict[str, Any],
    ) -> Any:
        result = self.api.update_series_metadata(series_id, metadata)
        if hasattr(self.api, "invalidate_content"):
            self.api.invalidate_content()
        return result


class SeriesFixApiAdapter:
    def __init__(self, api: Any):
        self.api = api

    def test(self) -> str:
        return self.api.test()

    def libraries(self) -> list[Any]:
        from .tools import series_fix

        return series_fix.extract_libraries([item.raw for item in self.api.libraries()])

    def series(self, library_id: str) -> list[Any]:
        from .tools import series_fix

        rows = self.api.series(library_id=library_id)
        return series_fix.extract_series(
            [item.raw for item in rows],
            library_filter=library_id,
            strict_library=False,
        )

    def books_for_series(self, series_id: str) -> list[Any]:
        from .tools import series_fix

        rows = self.api.books(series_id=series_id)
        return series_fix.extract_books(
            [item.raw for item in rows],
            series_id=series_id,
            strict_series=False,
        )

    def update_series_title(
        self,
        series_id: str,
        title: str,
        title_sort: str | None = None,
        lock_title: bool = True,
    ) -> None:
        from .tools.series_fix import clean_spaces

        clean_title = clean_spaces(title)
        clean_sort = clean_spaces(title_sort if title_sort is not None else title)
        if not clean_title or not clean_sort:
            raise ValueError("Titre ou titre de tri vide")
        payload: dict[str, Any] = {"title": clean_title, "titleSort": clean_sort}
        if lock_title:
            payload.update({"titleLock": True, "titleSortLock": True})
        try:
            self.api.update_series_metadata(series_id, payload)
        except HttpError as exc:
            if not lock_title or exc.status != 400:
                raise
            self.api.update_series_metadata(
                series_id,
                {"title": clean_title, "titleSort": clean_sort},
            )

    def update_series_sort_title(
        self,
        series_id: str,
        title_sort: str,
        lock_sort: bool = True,
    ) -> None:
        from .tools.series_fix import clean_spaces

        clean_sort = clean_spaces(title_sort)
        if not clean_sort:
            raise ValueError("Titre de tri vide")
        payload: dict[str, Any] = {"titleSort": clean_sort}
        if lock_sort:
            payload["titleSortLock"] = True
        try:
            self.api.update_series_metadata(series_id, payload)
        except HttpError as exc:
            if not lock_sort or exc.status != 400:
                raise
            self.api.update_series_metadata(series_id, {"titleSort": clean_sort})


class LightNovelKomgaApiAdapter:
    def __init__(self, api: Any):
        self.api = api

    def test(self) -> str:
        return self.api.test()

    def libraries(self) -> list[Any]:
        from .tools import lightnovel_queue

        return lightnovel_queue.extract_libraries(
            [item.raw for item in self.api.libraries()]
        )

    def series(self, library_id: str) -> list[Any]:
        from .tools import lightnovel_queue

        rows = self.api.series(library_id=library_id)
        return lightnovel_queue.extract_series(
            [item.raw for item in rows],
            library_filter=library_id,
            strict_library=False,
        )
