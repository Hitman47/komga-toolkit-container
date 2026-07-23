from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import LibraryItem, PendingChange, SeriesItem, SeriesRecord
from .tag_logic import extract_kora_genres, extract_kora_tags


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _loads(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CacheStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS libraries (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    excluded INTEGER NOT NULL DEFAULT 0,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    synced_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS series (
                    id TEXT PRIMARY KEY,
                    library_id TEXT NOT NULL,
                    library_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    book_count INTEGER NOT NULL DEFAULT 0,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    genres_json TEXT NOT NULL DEFAULT '[]',
                    kora_genres_json TEXT NOT NULL DEFAULT '[]',
                    kora_tags_json TEXT NOT NULL DEFAULT '[]',
                    tags_lock INTEGER NOT NULL DEFAULT 0,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    synced_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_series_library ON series(library_id);
                CREATE INDEX IF NOT EXISTS idx_series_title ON series(title);
                CREATE INDEX IF NOT EXISTS idx_series_display_order
                    ON series(library_name COLLATE NOCASE, title COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS idx_series_library_display_order
                    ON series(library_id, title COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS idx_libraries_visible
                    ON libraries(excluded, id);
                CREATE TABLE IF NOT EXISTS pending_changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    series_id TEXT NOT NULL UNIQUE,
                    library_name TEXT NOT NULL,
                    title TEXT NOT NULL,
                    new_kora_genres_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                """
            )

    def clear(self) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM series")
            conn.execute("DELETE FROM libraries")
            conn.execute("DELETE FROM pending_changes")

    def upsert_libraries(self, libraries: Iterable[LibraryItem], excluded_names: Iterable[str]) -> None:
        excluded_lower = {x.strip().lower() for x in excluded_names}
        with self.connect() as conn:
            for lib in libraries:
                conn.execute(
                    """
                    INSERT INTO libraries(id, name, excluded, raw_json, synced_at)
                    VALUES(?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name=excluded.name,
                        excluded=excluded.excluded,
                        raw_json=excluded.raw_json,
                        synced_at=excluded.synced_at
                    """,
                    (lib.id, lib.name, 1 if lib.name.strip().lower() in excluded_lower else 0, _json(lib.raw), _now()),
                )

    def upsert_series(self, series: Iterable[SeriesItem], library_names: dict[str, str] | None = None) -> None:
        library_names = library_names or {}
        with self.connect() as conn:
            for item in series:
                meta = item.metadata or {}
                tags = [str(x) for x in meta.get("tags", []) if str(x).strip()] if isinstance(meta.get("tags"), list) else []
                genres = [str(x) for x in meta.get("genres", []) if str(x).strip()] if isinstance(meta.get("genres"), list) else []
                library_name = item.library_name or library_names.get(item.library_id, "")
                kora_genres = extract_kora_genres(tags)
                kora_tags = extract_kora_tags(tags)
                conn.execute(
                    """
                    INSERT INTO series(
                        id, library_id, library_name, title, book_count, tags_json, genres_json,
                        kora_genres_json, kora_tags_json, tags_lock, raw_json, synced_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        library_id=excluded.library_id,
                        library_name=excluded.library_name,
                        title=excluded.title,
                        book_count=excluded.book_count,
                        tags_json=excluded.tags_json,
                        genres_json=excluded.genres_json,
                        kora_genres_json=excluded.kora_genres_json,
                        kora_tags_json=excluded.kora_tags_json,
                        tags_lock=excluded.tags_lock,
                        raw_json=excluded.raw_json,
                        synced_at=excluded.synced_at
                    """,
                    (
                        item.id,
                        item.library_id,
                        library_name,
                        item.title,
                        item.book_count,
                        _json(tags),
                        _json(genres),
                        _json(kora_genres),
                        _json(kora_tags),
                        1 if bool(meta.get("tagsLock")) else 0,
                        _json(item.raw),
                        _now(),
                    ),
                )

    def libraries(self, include_excluded: bool = False) -> list[LibraryItem]:
        sql = "SELECT * FROM libraries"
        if not include_excluded:
            sql += " WHERE excluded=0"
        sql += " ORDER BY name COLLATE NOCASE"
        with self.connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [LibraryItem(id=r["id"], name=r["name"], raw=_loads(r["raw_json"], {})) for r in rows]

    def query_series(
        self,
        library_id: str = "",
        search: str = "",
        genre: str = "",
        no_genre: bool = False,
        has_kora_tags: bool = False,
        no_kora_tags: bool = False,
        multiple_genres: bool = False,
        include_excluded: bool = False,
    ) -> list[SeriesRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if not include_excluded:
            clauses.append("EXISTS (SELECT 1 FROM libraries WHERE libraries.id=series.library_id AND libraries.excluded=0)")
        if library_id:
            clauses.append("library_id=?")
            params.append(library_id)
        if search:
            clauses.append("title LIKE ?")
            params.append(f"%{search}%")
        sql = "SELECT * FROM series"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        if library_id:
            sql += " ORDER BY title COLLATE NOCASE"
        else:
            sql += " ORDER BY library_name COLLATE NOCASE, title COLLATE NOCASE"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        records = [self._record_from_row(r) for r in rows]
        if genre:
            records = [r for r in records if genre in r.kora_genres]
        if no_genre:
            records = [r for r in records if not r.kora_genres]
        if has_kora_tags:
            records = [r for r in records if r.kora_genres or r.kora_tags]
        if no_kora_tags:
            records = [r for r in records if not (r.kora_genres or r.kora_tags)]
        if multiple_genres:
            records = [r for r in records if len(r.kora_genres) > 1]
        return records


    def genre_counts_by_library(self, library_id: str = "", include_excluded: bool = False) -> dict[str, int]:
        """Return counts of series per Kora genre for one library or all included libraries."""
        records = self.query_series(library_id=library_id, include_excluded=include_excluded)
        counts: dict[str, int] = {}
        for record in records:
            for genre in record.kora_genres:
                counts[genre] = counts.get(genre, 0) + 1
        return counts

    def get_pending(self, series_id: str) -> PendingChange | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM pending_changes WHERE series_id=?", (series_id,)).fetchone()
        if row is None:
            return None
        return PendingChange(
            series_id=row["series_id"],
            library_name=row["library_name"],
            title=row["title"],
            new_kora_genres=_loads(row["new_kora_genres_json"], []),
            source=row["source"],
            note=row["note"],
        )

    def get_series(self, series_id: str) -> SeriesRecord | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM series WHERE id=?", (series_id,)).fetchone()
        return self._record_from_row(row) if row else None

    def pending_genres_by_series_id(self) -> dict[str, list[str]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT series_id, new_kora_genres_json FROM pending_changes").fetchall()
        return {r["series_id"]: _loads(r["new_kora_genres_json"], []) for r in rows}

    def _record_from_row(self, row: sqlite3.Row) -> SeriesRecord:
        return SeriesRecord(
            id=row["id"],
            library_id=row["library_id"],
            library_name=row["library_name"],
            title=row["title"],
            book_count=int(row["book_count"] or 0),
            tags=_loads(row["tags_json"], []),
            genres=_loads(row["genres_json"], []),
            kora_genres=_loads(row["kora_genres_json"], []),
            kora_tags=_loads(row["kora_tags_json"], []),
            tags_lock=bool(row["tags_lock"]),
            raw=_loads(row["raw_json"], {}),
        )

    def add_pending(self, change: PendingChange) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO pending_changes(series_id, library_name, title, new_kora_genres_json, source, note, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(series_id) DO UPDATE SET
                    library_name=excluded.library_name,
                    title=excluded.title,
                    new_kora_genres_json=excluded.new_kora_genres_json,
                    source=excluded.source,
                    note=excluded.note,
                    created_at=excluded.created_at
                """,
                (change.series_id, change.library_name, change.title, _json(change.new_kora_genres), change.source, change.note, _now()),
            )

    def pending(self) -> list[PendingChange]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM pending_changes ORDER BY created_at, title COLLATE NOCASE").fetchall()
        return [PendingChange(
            series_id=r["series_id"],
            library_name=r["library_name"],
            title=r["title"],
            new_kora_genres=_loads(r["new_kora_genres_json"], []),
            source=r["source"],
            note=r["note"],
        ) for r in rows]

    def remove_pending(self, series_ids: Iterable[str]) -> None:
        ids = [x for x in series_ids if x]
        if not ids:
            return
        with self.connect() as conn:
            conn.executemany("DELETE FROM pending_changes WHERE series_id=?", [(x,) for x in ids])

    def clear_pending(self) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM pending_changes")
