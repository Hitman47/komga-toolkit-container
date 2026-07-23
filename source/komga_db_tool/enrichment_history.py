from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


def default_enrichment_history_path() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.getenv("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.getenv("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / "KomgaToolkit" / "enrichment_history.sqlite"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class EnrichmentHistoryStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_enrichment_history_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS enrichment_search_history (
                    source TEXT NOT NULL,
                    series_id TEXT NOT NULL,
                    series_title TEXT NOT NULL DEFAULT '',
                    searched_at TEXT NOT NULL,
                    PRIMARY KEY (source, series_id)
                );
                CREATE INDEX IF NOT EXISTS idx_enrichment_history_source_date
                ON enrichment_search_history(source, searched_at);
                CREATE TABLE IF NOT EXISTS book_enrichment_history (
                    source TEXT NOT NULL,
                    book_id TEXT NOT NULL,
                    series_id TEXT NOT NULL DEFAULT '',
                    book_title TEXT NOT NULL DEFAULT '',
                    enriched_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT '',
                    fields TEXT NOT NULL DEFAULT '',
                    confidence TEXT NOT NULL DEFAULT '',
                    source_ref TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (source, book_id)
                );
                CREATE INDEX IF NOT EXISTS idx_book_enrichment_history_book_date
                ON book_enrichment_history(book_id, enriched_at);
                """
            )

    def record_search(
        self,
        source: str,
        series_id: str,
        series_title: str = "",
        searched_at: datetime | None = None,
    ) -> None:
        source = str(source or "").strip().casefold()
        series_id = str(series_id or "").strip()
        if not source or not series_id:
            return
        timestamp = (searched_at or _utc_now()).astimezone(timezone.utc).isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO enrichment_search_history(source, series_id, series_title, searched_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source, series_id) DO UPDATE SET
                    series_title=excluded.series_title,
                    searched_at=excluded.searched_at
                """,
                (source, series_id, str(series_title or ""), timestamp),
            )

    def last_searches(self, source: str, series_ids: Iterable[str] | None = None) -> dict[str, datetime]:
        source = str(source or "").strip().casefold()
        ids = [str(value).strip() for value in (series_ids or []) if str(value).strip()]
        query = "SELECT series_id, searched_at FROM enrichment_search_history WHERE source = ?"
        params: list[str] = [source]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            query += f" AND series_id IN ({placeholders})"
            params.extend(ids)
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        result: dict[str, datetime] = {}
        for row in rows:
            parsed = _parse_timestamp(row["searched_at"])
            if parsed is not None:
                result[str(row["series_id"])] = parsed
        return result

    def last_searches_any_source(self, series_ids: Iterable[str] | None = None) -> dict[str, datetime]:
        ids = [str(value).strip() for value in (series_ids or []) if str(value).strip()]
        query = """
            SELECT series_id, MAX(searched_at) AS searched_at
            FROM enrichment_search_history
        """
        params: list[str] = []
        if ids:
            placeholders = ",".join("?" for _ in ids)
            query += f" WHERE series_id IN ({placeholders})"
            params.extend(ids)
        query += " GROUP BY series_id"
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        result: dict[str, datetime] = {}
        for row in rows:
            parsed = _parse_timestamp(row["searched_at"])
            if parsed is not None:
                result[str(row["series_id"])] = parsed
        return result

    def searched_within_days(self, source: str, series_id: str, days: int, now: datetime | None = None) -> bool:
        if int(days or 0) <= 0:
            return False
        searched_at = self.last_searches(source, [series_id]).get(str(series_id or ""))
        if searched_at is None:
            return False
        reference = (now or _utc_now()).astimezone(timezone.utc)
        return searched_at >= reference - timedelta(days=int(days))

    def searched_within_days_any_source(
        self,
        series_id: str,
        days: int,
        now: datetime | None = None,
    ) -> bool:
        if int(days or 0) <= 0:
            return False
        searched_at = self.last_searches_any_source([series_id]).get(str(series_id or ""))
        if searched_at is None:
            return False
        reference = (now or _utc_now()).astimezone(timezone.utc)
        return searched_at >= reference - timedelta(days=int(days))

    def record_book_enrichment(
        self,
        source: str,
        book_id: str,
        *,
        series_id: str = "",
        book_title: str = "",
        status: str = "",
        fields: Iterable[str] | None = None,
        confidence: str = "",
        source_ref: str = "",
        enriched_at: datetime | None = None,
    ) -> None:
        source = str(source or "").strip().casefold()
        book_id = str(book_id or "").strip()
        if not source or not book_id:
            return
        timestamp = (enriched_at or _utc_now()).astimezone(timezone.utc).isoformat(timespec="seconds")
        field_text = ";".join(dict.fromkeys(str(field or "").strip() for field in (fields or []) if str(field or "").strip()))
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO book_enrichment_history(
                    source, book_id, series_id, book_title, enriched_at,
                    status, fields, confidence, source_ref
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, book_id) DO UPDATE SET
                    series_id=excluded.series_id,
                    book_title=excluded.book_title,
                    enriched_at=excluded.enriched_at,
                    status=excluded.status,
                    fields=excluded.fields,
                    confidence=excluded.confidence,
                    source_ref=excluded.source_ref
                """,
                (
                    source,
                    book_id,
                    str(series_id or ""),
                    str(book_title or ""),
                    timestamp,
                    str(status or ""),
                    field_text,
                    str(confidence or ""),
                    str(source_ref or ""),
                ),
            )

    def last_book_enrichments(self, book_ids: Iterable[str] | None = None) -> dict[str, dict[str, str]]:
        ids = [str(value).strip() for value in (book_ids or []) if str(value).strip()]
        query = """
            SELECT source, book_id, series_id, book_title, enriched_at,
                   status, fields, confidence, source_ref
            FROM book_enrichment_history
        """
        params: list[str] = []
        if ids:
            placeholders = ",".join("?" for _ in ids)
            query += f" WHERE book_id IN ({placeholders})"
            params.extend(ids)
        query += " ORDER BY enriched_at DESC"
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        result: dict[str, dict[str, str]] = {}
        for row in rows:
            book_id = str(row["book_id"])
            if book_id in result:
                continue
            result[book_id] = {key: str(row[key] or "") for key in row.keys()}
        return result


def format_search_timestamp(value: datetime | None) -> str:
    if value is None:
        return "Jamais"
    return value.astimezone().strftime("%Y-%m-%d %H:%M")
