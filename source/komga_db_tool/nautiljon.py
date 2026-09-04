from __future__ import annotations

import csv
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Iterable

from .bedetheque import _fold, title_similarity


REQUIRED_COLUMNS = {"url_fiche", "titre", "genres", "themes"}
NAUTILJON_LINK_LABEL = "Nautiljon"
AUTOMATCH_MIN_SCORE = 0.90
AUTOMATCH_MIN_MARGIN = 0.05
_CHAPTER_SUFFIX = re.compile(r"\s*\(\s*chap\s*\)\s*$", re.IGNORECASE)


def clean_nautiljon_query(value: Any) -> str:
    """Remove Komga's chapter marker for lookup only."""
    return _CHAPTER_SUFFIX.sub("", str(value or "")).strip()


def split_nautiljon_taxonomy(value: Any) -> list[str]:
    """Split Nautiljon taxonomy values without breaking hyphenated labels."""
    result: list[str] = []
    seen: set[str] = set()
    for item in str(value or "").split(" - "):
        label = item.strip()
        key = label.casefold()
        if not label or key in {"n/a", "na", "non renseigne", "non renseigné"} or key in seen:
            continue
        seen.add(key)
        result.append(label)
    return result


def merge_nautiljon_tags(existing: Any, incoming: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    values = list(existing) if isinstance(existing, (list, tuple, set)) else []
    values.extend(incoming)
    for value in values:
        label = str(value or "").strip()
        key = label.casefold()
        if label and key not in seen:
            seen.add(key)
            result.append(label)
    return result


def merge_nautiljon_links(existing: Any, url: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    target_url = str(url or "").strip()
    replaced = False
    for value in existing if isinstance(existing, list) else []:
        if not isinstance(value, dict):
            continue
        row = {"label": str(value.get("label") or ""), "url": str(value.get("url") or "")}
        if row["label"].strip().casefold() == NAUTILJON_LINK_LABEL.casefold():
            if not replaced and target_url:
                result.append({"label": NAUTILJON_LINK_LABEL, "url": target_url})
                replaced = True
            continue
        result.append(row)
    if target_url and not replaced:
        result.append({"label": NAUTILJON_LINK_LABEL, "url": target_url})
    return result


@dataclass(frozen=True)
class NautiljonSearchResult:
    title: str
    url: str
    source: str = "nautiljon_csv"
    match_score: float = 0.0
    alternate_title: str = ""
    original_title: str = ""
    year: str = ""
    origin: str = ""
    media_type: str = ""


@dataclass(frozen=True)
class NautiljonCandidate:
    source_url: str
    series_title: str
    series_metadata: dict[str, Any]
    raw: dict[str, Any]


def public_nautiljon_candidate(value: Any) -> dict[str, Any]:
    return asdict(value) if hasattr(value, "__dataclass_fields__") else dict(value)


class NautiljonCsvClient:
    """Strictly local Nautiljon catalog reader. No network fallback exists."""

    _cache_lock = RLock()
    _cache: dict[str, tuple[tuple[int, int], list[dict[str, str]]]] = {}

    def __init__(self, csv_path: str):
        self.csv_path = str(Path(csv_path).expanduser())

    def _rows(self) -> list[dict[str, str]]:
        path = Path(self.csv_path)
        if not path.is_file():
            raise FileNotFoundError(f"CSV Nautiljon introuvable : {path}")
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
        key = str(path.resolve())
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached and cached[0] == stamp:
                return cached[1]
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, delimiter=";")
            columns = {str(name or "").strip() for name in (reader.fieldnames or [])}
            missing = sorted(REQUIRED_COLUMNS - columns)
            if missing:
                raise ValueError(
                    "CSV Nautiljon invalide : colonne(s) obligatoire(s) absente(s) : "
                    + ", ".join(missing)
                )
            rows = [
                {str(name): str(value or "") for name, value in row.items()}
                for row in reader
                if str(row.get("titre") or "").strip() and str(row.get("url_fiche") or "").strip()
            ]
        if not rows:
            raise ValueError("CSV Nautiljon invalide : aucune série avec titre et URL")
        with self._cache_lock:
            self._cache[key] = (stamp, rows)
        return rows

    def test(self) -> str:
        return f"CSV Nautiljon : {len(self._rows())} série(s)"

    @staticmethod
    def _title_values(row: dict[str, str]) -> list[str]:
        # Do not blindly split alternate titles: '/' can be part of a title.
        return [
            str(row.get("titre") or "").strip(),
            str(row.get("titre_alternatif") or "").strip(),
            str(row.get("titre_original") or "").strip(),
        ]

    def search(self, query: str, limit: int = 50) -> list[NautiljonSearchResult]:
        cleaned_query = clean_nautiljon_query(query)
        folded_query = _fold(cleaned_query).strip()
        if not folded_query:
            return []
        candidates: list[tuple[float, dict[str, str]]] = []
        for row in self._rows():
            scores: list[float] = []
            for value in self._title_values(row):
                if not value:
                    continue
                folded_value = _fold(value)
                if folded_query == folded_value:
                    score = 1.0
                elif folded_query in folded_value:
                    score = min(0.98, 0.82 + 0.16 * len(folded_query) / max(len(folded_value), 1))
                else:
                    score = title_similarity(cleaned_query, value)
                scores.append(score)
            score = max(scores, default=0.0)
            if score >= 0.45:
                candidates.append((score, row))
        candidates.sort(key=lambda item: (-item[0], item[1].get("titre", "").casefold(), item[1].get("url_fiche", "")))
        return [
            NautiljonSearchResult(
                title=row.get("titre", ""),
                url=row.get("url_fiche", ""),
                match_score=round(score, 6),
                alternate_title=row.get("titre_alternatif", ""),
                original_title=row.get("titre_original", ""),
                year=row.get("annee_vo", ""),
                origin=row.get("origine", ""),
                media_type=row.get("type_detail", "") or row.get("type_liste", ""),
            )
            for score, row in candidates[: max(1, int(limit))]
        ]

    def _row_for_url(self, url: str) -> dict[str, str]:
        normalized = str(url or "").strip().rstrip("/").casefold()
        return next(
            (row for row in self._rows() if row.get("url_fiche", "").strip().rstrip("/").casefold() == normalized),
            {},
        )

    def get_series(self, url: str) -> NautiljonCandidate:
        row = self._row_for_url(url)
        if not row:
            raise LookupError(f"Série absente du CSV Nautiljon : {url}")
        genres = split_nautiljon_taxonomy(row.get("genres"))
        themes = split_nautiljon_taxonomy(row.get("themes"))
        tags = merge_nautiljon_tags([], [*genres, *themes])
        source_url = row.get("url_fiche", "").strip()
        return NautiljonCandidate(
            source_url=source_url,
            series_title=row.get("titre", "").strip(),
            series_metadata={
                "tags": tags,
                "links": [{"label": NAUTILJON_LINK_LABEL, "url": source_url}],
            },
            raw={
                "source": "nautiljon_csv",
                "csv_row": row,
                "genres": genres,
                "themes": themes,
            },
        )

    def scrape_series(self, url: str) -> NautiljonCandidate:
        return self.get_series(url)


def select_nautiljon_automatch(
    results: list[NautiljonSearchResult],
    *,
    min_score: float = AUTOMATCH_MIN_SCORE,
    min_margin: float = AUTOMATCH_MIN_MARGIN,
) -> tuple[NautiljonSearchResult | None, str, float, float]:
    if not results:
        return None, "Aucun résultat", 0.0, 0.0
    best = results[0]
    second = results[1].match_score if len(results) > 1 else 0.0
    if best.match_score < min_score:
        return None, "Score insuffisant", best.match_score, second
    if best.match_score < 1.0 and best.match_score - second < min_margin:
        return None, "Résultats trop proches", best.match_score, second
    if best.match_score == second:
        return None, "Correspondance exacte ambiguë", best.match_score, second
    return best, "Correspondance sûre", best.match_score, second
