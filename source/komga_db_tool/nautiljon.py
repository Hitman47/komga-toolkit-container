from __future__ import annotations

import csv
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Iterable

from .author_cleanup import author_entries, author_fold_key
from .bedetheque import _fold, title_similarity
from .catalog_mirror import CatalogMirror


REQUIRED_COLUMNS = {"url_fiche", "titre", "genres", "themes"}
NAUTILJON_LINK_LABEL = "Nautiljon"
AUTOMATCH_MIN_SCORE = 0.90
AUTOMATCH_MIN_MARGIN = 0.05
_CHAPTER_SUFFIX = re.compile(r"\s*\(\s*chap\s*\)\s*$", re.IGNORECASE)
_VOLUME_DETAIL = re.compile(r"^\s*(\d+)\s*(?:\(([^)]*)\))?\s*$")


def _csv_value(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.casefold() in {"n/a", "na", "non renseigné", "non renseigne", "-"} else text


def _vf_status(value: Any) -> str:
    folded = _fold(_csv_value(value)).strip()
    return {
        "termine": "ENDED",
        "en cours": "ONGOING",
        "abandonne": "ABANDONED",
        "en pause": "HIATUS",
        "en attente": "HIATUS",
    }.get(folded, "")


def nautiljon_vf_metadata(row: dict[str, str]) -> tuple[dict[str, Any], list[str]]:
    """Only publish VF release facts that are internally consistent.

    A published-volume count is not a final total while publication is ongoing.
    VO figures and zero-valued VF list entries are never substituted for VF data.
    """
    metadata: dict[str, Any] = {}
    warnings: list[str] = []
    detail = _csv_value(row.get("nb_vol_vf_detail"))
    match = _VOLUME_DETAIL.fullmatch(detail) if detail else None
    detail_count = int(match.group(1)) if match else None
    detail_status = _vf_status(match.group(2)) if match else ""
    list_text = _csv_value(row.get("nb_vol_vf_liste"))
    list_count = int(list_text) if list_text.isdecimal() else None
    explicit_statuses = [
        _vf_status(row.get(key)) for key in ("statut_vf", "statut_vf_liste")
    ]
    statuses = {value for value in [detail_status, *explicit_statuses] if value}
    if len(statuses) > 1:
        warnings.append("Statuts VF contradictoires dans le CSV : statut et total ignorés.")
        return metadata, warnings
    if detail_count is not None and list_count is not None and detail_count != list_count:
        warnings.append("Nombres de tomes VF contradictoires dans le CSV : total ignoré.")
        detail_count = None
    status = next(iter(statuses), "")
    if status:
        metadata["status"] = status
    if status == "ENDED":
        final_count = detail_count if detail_count is not None else list_count
        if final_count is not None and final_count > 0 and (detail_count is not None or list_count is not None):
            # A list count of zero is a known scraper failure, not a confirmed total.
            if not any("contradictoires" in warning for warning in warnings):
                metadata["totalBookCount"] = final_count
    return metadata, warnings


def nautiljon_credited_authors(row: dict[str, str]) -> list[dict[str, str]]:
    """Expose source credits without assuming that they belong to every Komga book."""
    authors: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for column, role in (("scenariste", "writer"), ("dessinateur", "penciller")):
        name = _csv_value(row.get(column))
        key = (name.casefold(), role)
        if name and key not in seen:
            authors.append({"name": name, "role": role})
            seen.add(key)
    return authors


def merge_nautiljon_authors(existing: Any, incoming: Any) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Append only genuinely missing name/role credits; preserve book authors.

    Two-part names with reversed order or accent/case differences count as the
    same person for this comparison. Existing spellings and roles are untouched.
    """
    current = author_entries(existing)
    merged = list(current)
    added: list[dict[str, str]] = []

    def identity(entry: dict[str, str]) -> tuple[tuple[str, ...], str]:
        parts = tuple(author_fold_key(entry["name"]).split())
        return (tuple(sorted(parts)) if len(parts) == 2 else parts, entry["role"].casefold())

    seen = {identity(entry) for entry in current}
    for entry in author_entries(incoming):
        key = identity(entry)
        if key in seen:
            continue
        seen.add(key)
        merged.append(entry)
        added.append(entry)
    return merged, added


def proposed_nautiljon_series_metadata(current: dict[str, Any], candidate: "NautiljonCandidate") -> dict[str, Any]:
    proposed = {
        "tags": merge_nautiljon_tags(current.get("tags"), candidate.series_metadata.get("tags") or []),
        "links": merge_nautiljon_links(current.get("links"), candidate.source_url),
    }
    # VF publication information must not overwrite an English/Japanese edition.
    language = str(current.get("language") or "").strip().casefold()
    if language in {"", "fr", "fra", "fr-fr"}:
        for field in ("status", "totalBookCount"):
            if field in candidate.series_metadata:
                proposed[field] = candidate.series_metadata[field]
    return proposed


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

    def __init__(self, csv_path: str, *, mirror: bool = True):
        self.csv_path = str(Path(csv_path).expanduser())
        self.catalog_mirror = CatalogMirror(csv_path, "nautiljon") if mirror else None

    def _rows(self) -> list[dict[str, str]]:
        path = self.catalog_mirror.resolve(self._read_rows) if self.catalog_mirror else Path(self.csv_path)
        return self._read_rows(path)

    def _read_rows(self, path: Path) -> list[dict[str, str]]:
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
            reader = csv.DictReader(stream, delimiter=";", strict=True)
            columns = {str(name or "").strip() for name in (reader.fieldnames or [])}
            missing = sorted(REQUIRED_COLUMNS - columns)
            if missing:
                raise ValueError(
                    "CSV Nautiljon invalide : colonne(s) obligatoire(s) absente(s) : "
                    + ", ".join(missing)
                )
            raw_rows = list(reader)
            if any(None in row or any(value is None for value in row.values()) for row in raw_rows):
                raise ValueError("CSV Nautiljon incomplet : nombre de colonnes incohérent")
            rows = [
                {str(name): str(value or "") for name, value in row.items()}
                for row in raw_rows
                if str(row.get("titre") or "").strip() and str(row.get("url_fiche") or "").strip()
            ]
        if not rows:
            raise ValueError("CSV Nautiljon invalide : aucune série avec titre et URL")
        with self._cache_lock:
            self._cache[key] = (stamp, rows)
        return rows

    def test(self) -> str:
        if self.catalog_mirror:
            self.catalog_mirror.resolve(self._read_rows, refresh=True)
        return f"CSV Nautiljon : {len(self._rows())} série(s) — {self.catalog_mirror.status if self.catalog_mirror else 'fichier validé'}"

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
        vf_metadata, release_warnings = nautiljon_vf_metadata(row)
        authors = nautiljon_credited_authors(row)
        source_url = row.get("url_fiche", "").strip()
        return NautiljonCandidate(
            source_url=source_url,
            series_title=row.get("titre", "").strip(),
            series_metadata={
                "tags": tags,
                "links": [{"label": NAUTILJON_LINK_LABEL, "url": source_url}],
                **vf_metadata,
            },
            raw={
                "source": "nautiljon_csv",
                "csv_row": row,
                "genres": genres,
                "themes": themes,
                "authors": authors,
                "release_warnings": release_warnings,
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
