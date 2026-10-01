from __future__ import annotations

import csv
from pathlib import Path
from threading import RLock
from typing import Any

from .bedetheque import (
    BedethequeCandidate,
    BedethequeSearchResult,
    _fold,
    candidate_to_dict,
    title_similarity,
)

REQUIRED_COLUMNS = {"serieTitle", "serieUrl", "tomesCount"}


def _status(value: str) -> str:
    folded = _fold(value)
    if "finie" in folded or "one shot" in folded or "terminee" in folded:
        return "ENDED"
    if "cours" in folded:
        return "ONGOING"
    if "abandon" in folded:
        return "ABANDONED"
    if "pause" in folded or "hiatus" in folded:
        return "HIATUS"
    return ""


def _language(value: str) -> str:
    folded = _fold(value)
    return {
        "francais": "fr",
        "anglais": "en",
        "japonais": "ja",
        "allemand": "de",
        "espagnol": "es",
        "italien": "it",
        "portugais": "pt",
        "neerlandais": "nl",
    }.get(folded, "")


def _positive_int(value: Any) -> int | None:
    try:
        number = int(str(value or "").strip())
    except ValueError:
        return None
    return number if number >= 0 else None


class BedethequeCsvClient:
    _cache_lock = RLock()
    _cache: dict[str, tuple[tuple[int, int], list[dict[str, str]]]] = {}

    def __init__(self, csv_path: str, *, mirror: bool = True):
        from .catalog_mirror import CatalogMirror
        self.csv_path = str(Path(csv_path).expanduser())
        self.catalog_mirror = CatalogMirror(csv_path, "bedetheque") if mirror else None

    def _rows(self) -> list[dict[str, str]]:
        path = self.catalog_mirror.resolve(self._read_rows) if self.catalog_mirror else Path(self.csv_path)
        return self._read_rows(path)

    def _read_rows(self, path: Path) -> list[dict[str, str]]:
        if not path.is_file():
            raise FileNotFoundError(f"CSV Bedetheque introuvable : {path}")
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
        key = str(path.resolve())
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached and cached[0] == stamp:
                return cached[1]
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, delimiter=";", strict=True)
            columns = {str(name or "").strip() for name in (reader.fieldnames or [])}
            missing = sorted(REQUIRED_COLUMNS - columns)
            if missing:
                raise ValueError(
                    "CSV Bedetheque invalide : colonne(s) obligatoire(s) absente(s) : "
                    + ", ".join(missing)
                )
            raw_rows = list(reader)
            if any(None in row or any(value is None for value in row.values()) for row in raw_rows):
                raise ValueError("CSV Bedetheque incomplet : nombre de colonnes incohérent")
            rows = [
                {str(key): str(value or "") for key, value in row.items()}
                for row in raw_rows
            ]
        if not rows:
            raise ValueError("CSV Bedetheque invalide : aucune série disponible")
        if not any(row.get("serieTitle", "").strip() and row.get("serieUrl", "").strip() for row in rows):
            raise ValueError(
                "CSV Bedetheque invalide : aucune série ne possède un titre et une URL"
            )
        with self._cache_lock:
            self._cache[key] = (stamp, rows)
        return rows

    def test(self) -> str:
        return f"CSV Bedetheque : {len(self._rows())} série(s) — {self.catalog_mirror.status if self.catalog_mirror else 'fichier validé'}"

    def language_for_url(self, url: str) -> str:
        """Return the normalized edition language for one exact CSV URL."""
        return _language(self._row_for_url(url).get("language", ""))

    def language_map(self) -> dict[str, str]:
        """Return exact normalized URL -> language mappings for cleanup scans."""
        out: dict[str, str] = {}
        for row in self._rows():
            url = row.get("serieUrl", "").strip().rstrip("/").casefold()
            language = _language(row.get("language", ""))
            if url and language:
                out[url] = language
        return out

    def search(self, query: str) -> list[BedethequeSearchResult]:
        folded_query = _fold(query).strip()
        if not folded_query:
            return []
        candidates: list[tuple[float, dict[str, str]]] = []
        for row in self._rows():
            title = row.get("serieTitle", "")
            folded_title = _fold(title)
            if folded_query == folded_title:
                score = 2.0
            elif folded_query in folded_title:
                score = 1.2 + min(len(folded_query) / max(len(folded_title), 1), 0.7)
            else:
                score = title_similarity(query, title)
                if score < 0.55:
                    continue
            candidates.append((score, row))
        candidates.sort(key=lambda item: (-item[0], item[1].get("serieTitle", "").casefold()))
        return [
            BedethequeSearchResult(
                kind="serie",
                title=row.get("serieTitle", ""),
                url=row.get("serieUrl", ""),
                source="bedetheque_csv",
            )
            for _score, row in candidates[:50]
        ]

    def _row_for_url(self, url: str) -> dict[str, str]:
        normalized = str(url or "").strip().rstrip("/").casefold()
        return next(
            (
                row
                for row in self._rows()
                if row.get("serieUrl", "").strip().rstrip("/").casefold() == normalized
            ),
            {},
        )

    def scrape_series(self, url: str) -> BedethequeCandidate:
        row = self._row_for_url(url)
        if not row:
            raise LookupError(f"Série absente du CSV Bedetheque : {url}")
        title = row.get("serieTitle", "")
        metadata: dict[str, Any] = {
            "title": title,
            "links": [{"label": "Bedetheque", "url": row.get("serieUrl", "")}],
        }
        status = _status(row.get("publicationStatus", ""))
        if status:
            metadata["status"] = status
        total = _positive_int(row.get("tomesCount"))
        if total is not None:
            metadata["totalBookCount"] = total
        language = _language(row.get("language", ""))
        if language:
            metadata["language"] = language
        genre = row.get("genre", "").strip()
        if genre and _fold(genre) != "non defini":
            metadata["genres"] = [genre]
        return BedethequeCandidate(
            source_url=row.get("serieUrl", ""),
            series_title=title,
            series_metadata=metadata,
            raw={"csv_row": row, "albums": [], "source": "bedetheque_csv"},
        )

    def scrape(self, url: str, album_number: str = "") -> BedethequeCandidate:
        return self.scrape_series(url)

    @staticmethod
    def candidate_to_dict(candidate: BedethequeCandidate) -> dict[str, Any]:
        return candidate_to_dict(candidate)
