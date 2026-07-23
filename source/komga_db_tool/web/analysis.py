from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any


def book_paths(book: Any) -> list[str]:
    raw = getattr(book, "raw", {}) if not isinstance(book, dict) else book
    paths: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip().replace("\\", "/")
        if text and text not in paths:
            paths.append(text)

    for key in ("url", "path", "filePath", "file", "filename"):
        add(raw.get(key))
    media = raw.get("media") if isinstance(raw.get("media"), dict) else {}
    for key in ("url", "path", "filePath", "file", "filename"):
        add(media.get(key))
    for item in media.get("files", []) if isinstance(media.get("files"), list) else []:
        if isinstance(item, dict):
            for key in ("url", "path", "filePath", "file", "filename"):
                add(item.get(key))
    return paths


def folder_key(path: str, depth: int = 2) -> str:
    parts = [part for part in str(path).replace("\\", "/").split("/") if part]
    if parts and "." in parts[-1]:
        parts = parts[:-1]
    return "/".join(parts[-depth:]) if parts else ""


def collection_path_suggestions(api: Any, collection_id: str, library_id: str, query: str = "") -> dict[str, Any]:
    collection = api.get_collection(collection_id)
    member_ids = {str(value) for value in collection.get("seriesIds", []) if str(value)}
    if not member_ids:
        member_ids = {str(row.get("id") or "") for row in api.collection_series(collection_id)}
    books = api.books(library_id=library_id or None, page_size=1000)
    seeds: set[str] = set()
    for book in books:
        if book.series_id in member_ids:
            for path in book_paths(book):
                key = folder_key(path)
                if key:
                    seeds.add(key.casefold())
    groups: dict[str, dict[str, Any]] = defaultdict(lambda: {"series_ids": [], "series_titles": {}, "paths": [], "book_count": 0})
    needle = query.strip().casefold()
    for book in books:
        if not book.series_id or book.series_id in member_ids:
            continue
        for path in book_paths(book):
            normalized = path.casefold()
            key = folder_key(path)
            if needle:
                if needle not in normalized:
                    continue
            elif seeds and key.casefold() not in seeds:
                continue
            group = groups[key or "Chemin inconnu"]
            if book.series_id not in group["series_ids"]:
                group["series_ids"].append(book.series_id)
            group["series_titles"][book.series_id] = str((book.raw or {}).get("seriesTitle") or book.series_id)
            if len(group["paths"]) < 50:
                group["paths"].append(path)
            group["book_count"] += 1
    rows = [
        {"name": PurePosixPath(key).name or key, "key": key, **value}
        for key, value in groups.items()
    ]
    rows.sort(key=lambda row: (-len(row["series_ids"]), row["name"].casefold()))
    return {
        "collection_id": collection_id,
        "collection_name": collection.get("name") or collection_id,
        "member_series": len(member_ids),
        "seed_folders": len(seeds),
        "suggestions": rows,
    }
