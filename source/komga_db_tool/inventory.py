"""Targeted Komga inventory reads, shared by the desktop and Web clients."""
from __future__ import annotations

from typing import Any, Callable
from copy import deepcopy
from time import perf_counter
from .book_explorer import book_explorer_row
from .inventory_cache import BookInventorySnapshot

from .api import BookItem, HttpError, SeriesItem

NATIVE_BOOK_SORTS = {"added_at": "createdDate", "title": "metadata.title", "release_date": "metadata.releaseDate"}


def inventory_page(
    api: Any,
    library_id: str,
    *,
    query: str = "",
    added_since: Any = None,
    language: str = "",
    series_status: str = "ALL",
    source_filter: str = "all",
    missing_field: str = "",
    empty_summary: bool = False,
    hide_chapter_series: bool = False,
    sort_field: str = "added_at",
    descending: bool = True,
    page: int = 0,
    page_size: int = 100,
) -> dict[str, Any]:
    # These views map exactly to a Komga sort and have no residual filter.
    # Natural-number and series-title sorts retain the established local rules.
    native_sort = {"added_at": "createdDate", "title": "metadata.title", "release_date": "metadata.releaseDate"}.get(sort_field)
    can_page = callable(getattr(getattr(api, "api", api), "books_page", None))
    if can_page and native_sort and not any((
        query.strip(), added_since, language, missing_field, empty_summary,
        hide_chapter_series, series_status.upper() not in {"", "ALL"},
        source_filter.casefold() not in {"", "all"},
    )):
        result = native_book_page(api, library_id, page=page, page_size=page_size,
                                  sort_field=sort_field, descending=descending)
        return result
    started = perf_counter()
    snapshot_reader = getattr(api, "book_inventory_snapshot", None)
    snapshot = snapshot_reader(library_id) if callable(snapshot_reader) else BookInventorySnapshot.load(api, library_id)
    snapshot_ready = perf_counter()
    sorted_rows = snapshot.view(
        sort_field=sort_field,
        descending=descending,
        query=query,
        added_since=added_since,
        language=language,
        series_status=series_status,
        source_filter=source_filter,
        missing_field=missing_field,
        empty_summary=empty_summary,
        hide_chapter_series=hide_chapter_series,
    )
    view_ready = perf_counter()
    safe_page_size = max(25, min(500, int(page_size)))
    filtered_total = len(sorted_rows)
    total_pages = max(1, (filtered_total + safe_page_size - 1) // safe_page_size)
    safe_page = max(0, min(int(page), total_pages - 1))
    page_start = safe_page * safe_page_size
    page_rows = deepcopy(list(sorted_rows[page_start : page_start + safe_page_size]))
    page_ready = perf_counter()
    return {
        "total": len(snapshot.rows),
        "filtered_total": filtered_total,
        "hidden": len(snapshot.rows) - filtered_total,
        "page": safe_page,
        "size": safe_page_size,
        "total_pages": total_pages,
        "first": safe_page == 0,
        "last": safe_page >= total_pages - 1,
        "load_mode": "filtered_inventory",
        "rows": page_rows,
        "timings_ms": {
            "inventory": round((snapshot_ready - started) * 1000, 3),
            "filter_sort": round((view_ready - snapshot_ready) * 1000, 3),
            "page_copy": round((page_ready - view_ready) * 1000, 3),
        },
    }


def native_book_page(api: Any, library_id: str, *, page: int, page_size: int, sort_field: str, descending: bool) -> dict[str, Any]:
    from .book_explorer import book_explorer_row

    size = max(25, min(500, int(page_size)))
    requested = max(0, int(page))
    kwargs = dict(library_id=library_id, page=requested, page_size=size,
                  sort=f"{NATIVE_BOOK_SORTS[sort_field]},{'desc' if descending else 'asc'}")
    result = api.books_page(**kwargs)
    total = int(result.get("total") or 0)
    pages = max(1, int(result.get("total_pages") or 0))
    if requested >= pages:
        kwargs["page"] = pages - 1
        result = api.books_page(**kwargs)
        total = int(result.get("total") or 0)
        pages = max(1, int(result.get("total_pages") or 0))
    books = list(result.get("items") or [])
    parents = parent_series(api, books, library_id)
    current = int(result.get("page") or 0)
    return {
        "total": total, "filtered_total": total, "hidden": 0,
        "page": current, "size": size, "total_pages": pages,
        "first": current == 0, "last": bool(result.get("last", current + 1 >= pages)),
        "load_mode": "komga_page",
        "rows": [book_explorer_row(book, parents.get(book.series_id)) for book in books],
    }


def series_record(raw: dict[str, Any]) -> SeriesItem:
    meta = raw.get("metadata") or {}
    return SeriesItem(
        id=str(raw.get("id") or ""),
        library_id=str(raw.get("libraryId") or (raw.get("library") or {}).get("id") or ""),
        title=str(meta.get("title") or raw.get("name") or raw.get("title") or ""),
        book_count=str(raw.get("booksCount", raw.get("bookCount", ""))),
        metadata=meta,
        raw=raw,
    )


def book_record(raw: dict[str, Any]) -> BookItem:
    meta = raw.get("metadata") or {}
    return BookItem(
        id=str(raw.get("id") or ""),
        library_id=str(raw.get("libraryId") or (raw.get("library") or {}).get("id") or ""),
        series_id=str(raw.get("seriesId") or (raw.get("series") or {}).get("id") or ""),
        title=str(meta.get("title") or raw.get("name") or ""),
        number=str(meta.get("number", meta.get("numberSort", ""))),
        series_title=str(raw.get("seriesTitle") or ""),
        metadata=meta,
        raw=raw,
    )


def parent_series(api: Any, books: list[BookItem], library_id: str = "", *, fresh: bool = False) -> dict[str, SeriesItem]:
    """Read only distinct parents; never load the complete series inventory."""
    result: dict[str, SeriesItem] = {}
    reader = api.get_series if fresh else getattr(api, "browse_series", api.get_series)
    for book in books:
        series_id = str(book.series_id or "")
        if not series_id or series_id in result:
            continue
        try:
            series = series_record(reader(series_id))
        except HttpError as exc:
            if exc.status == 404:
                continue
            raise
        if series.id != series_id:
            raise ValueError("La série retournée par Komga ne correspond pas à la cible demandée")
        if library_id and series.library_id and series.library_id != library_id:
            raise ValueError("La série parente appartient à une autre bibliothèque")
        result[series_id] = series
    return result


def selected_books(api: Any, library_id: str, book_ids: list[str], cancelled: Callable[[], bool]) -> tuple[list[BookItem], dict[str, SeriesItem]]:
    """Fresh targeted reads before analysis, with explicit library checks."""
    books: list[BookItem] = []
    for book_id in dict.fromkeys(str(value) for value in book_ids if value):
        if cancelled():
            break
        book = book_record(api.get_book(book_id))
        if book.id != book_id or not book.series_id:
            raise ValueError("Le tome retourné par Komga ne correspond pas à la cible demandée")
        if book.library_id and book.library_id != library_id:
            raise ValueError("Un tome sélectionné appartient à une autre bibliothèque")
        books.append(book)
    parents = parent_series(api, books, library_id, fresh=True)
    for book in books:
        parent = parents.get(book.series_id)
        if not book.library_id and (parent is None or parent.library_id != library_id):
            raise ValueError("Impossible de vérifier la bibliothèque du tome sélectionné")
    return books, parents
