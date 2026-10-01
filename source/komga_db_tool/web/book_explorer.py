from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from ..bedetheque import normalize_volume_number, title_similarity
from ..book_explorer import (
    DEFAULT_BOOK_ENRICHMENT_FIELDS,
    book_enrichment_payload,
    book_explorer_row,
    choose_book_source,
    filter_book_rows,
    sort_book_rows,
)
from ..manga_news import manga_news_edition_label_from_url, series_slug_from_manga_news_url
from ..inventory import inventory_page, selected_books
from ..source_books import SourceBookRow, match_source_books, resolved_book_volume_number
from ..book_match_safety import assess_manga_news_book_match


def public_book_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (value.isoformat() if key == "added_at" and value is not None else value)
        for key, value in row.items()
        if key not in {"book", "series"}
    }


def list_book_rows(
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
    result = inventory_page(
        api, library_id, query=query, added_since=added_since, language=language,
        series_status=series_status, source_filter=source_filter, missing_field=missing_field,
        empty_summary=empty_summary, hide_chapter_series=hide_chapter_series,
        sort_field=sort_field, descending=descending, page=page, page_size=page_size,
    )
    return {**result, "rows": [public_book_row(row) for row in result["rows"]]}


def _comicvine_volume_id(url: str) -> str:
    parsed = urlparse(str(url or ""))
    if "comicvine" not in parsed.netloc.casefold():
        return ""
    match = re.search(r"(?:^|/)(?:4050-)?(\d+)(?:/|$)", parsed.path)
    return match.group(1) if match else ""


def _analysis_row(
    row: dict[str, Any],
    *,
    source: str = "",
    source_ref: str = "",
    matched_title: str = "",
    confidence: str = "",
    score: float = 0.0,
    candidate: dict[str, Any] | None = None,
    status: str = "",
    error: str = "",
) -> dict[str, Any]:
    book = row.get("book")
    current = dict(getattr(book, "metadata", {}) or {})
    proposed = dict(candidate or {})
    payload, title_confirmation = book_enrichment_payload(current, proposed)
    needs_confirmation = bool(confidence != "high" or title_confirmation)
    if not status:
        if not proposed:
            status = "Aucune métadonnée source"
        elif not payload:
            status = "Aucun changement"
        elif needs_confirmation:
            status = "Validation utilisateur requise"
        else:
            status = "Prêt — confiance élevée"
    return {
        **public_book_row(row),
        "source": source,
        "source_ref": source_ref,
        "matched_title": matched_title,
        "confidence": confidence,
        "score": round(float(score or 0.0), 3),
        "current_metadata": current,
        "candidate_metadata": proposed,
        "payload": payload,
        "needs_confirmation": needs_confirmation,
        "status": status,
        "error": error,
    }


def _analyze_manga_news(
    rows: list[dict[str, Any]],
    source_url: str,
    client: Any,
) -> list[dict[str, Any]]:
    slug = series_slug_from_manga_news_url(source_url)
    edition_label = manga_news_edition_label_from_url(source_url)
    if not slug:
        return [
            _analysis_row(
                row,
                source="manga_news",
                source_ref=source_url,
                status="Lien Manga News inutilisable",
            )
            for row in rows
        ]
    results: list[dict[str, Any]] = []
    for row in rows:
        try:
            requested_number = resolved_book_volume_number(
                row.get("number", ""),
                row.get("title", ""),
                row.get("series_title", ""),
            )
            if requested_number:
                candidate = client.get_volume_by_number(
                    slug,
                    requested_number,
                    edition_label=edition_label,
                )
            else:
                candidate = client.get_volume_without_number(
                    slug,
                    row.get("title", "") or row.get("series_title", ""),
                    edition_label=edition_label,
                )
            confidence, score, mismatch = assess_manga_news_book_match(row, candidate, requested_number, slug)
            results.append(
                _analysis_row(
                    row,
                    source="manga_news",
                    source_ref=candidate.source_url,
                    matched_title=candidate.title,
                    confidence=confidence,
                    score=score,
                    candidate=candidate.book_metadata if confidence else None,
                    status="Source incohérente — non applicable" if not confidence else "",
                    error=mismatch,
                )
            )
        except Exception as exc:
            results.append(
                _analysis_row(
                    row,
                    source="manga_news",
                    source_ref=source_url,
                    status="Tome Manga News introuvable",
                    error=str(exc),
                )
            )
    return results


def _analyze_bedetheque(
    rows: list[dict[str, Any]],
    source_url: str,
    client: Any,
) -> list[dict[str, Any]]:
    return [
        _analysis_row(
            row,
            source="bedetheque",
            source_ref=source_url,
            status="Métadonnées par tome indisponibles",
            error=(
                "Le CSV Bedetheque contient uniquement des données de série. "
                "Aucune connexion au site ne sera effectuée."
            ),
        )
        for row in rows
    ]


def _analyze_comicvine(
    rows: list[dict[str, Any]],
    source_url: str,
    client: Any,
) -> list[dict[str, Any]]:
    volume_id = _comicvine_volume_id(source_url)
    if not volume_id:
        return [
            _analysis_row(
                row,
                source="comicvine",
                source_ref=source_url,
                status="Lien ComicVine inutilisable",
            )
            for row in rows
        ]
    try:
        issues = client.list_volume_issues(volume_id, limit=500)
    except Exception as exc:
        return [
            _analysis_row(
                row,
                source="comicvine",
                source_ref=source_url,
                status="Erreur de chargement ComicVine",
                error=str(exc),
            )
            for row in rows
        ]
    source_rows = [
        SourceBookRow(
            id=issue.issue_id,
            number=issue.issue_number,
            title=issue.title,
            url=issue.source_url,
            metadata=issue.book_metadata,
            raw=issue,
        )
        for issue in issues
    ]
    matches = match_source_books(
        [row.get("book") for row in rows],
        source_rows,
        series_title=str(rows[0].get("series_title") or "") if rows else "",
    )[: len(rows)]
    results: list[dict[str, Any]] = []
    for row, match in zip(rows, matches):
        source_index = int(match.get("source_index", -1))
        if source_index < 0 or source_index >= len(source_rows):
            results.append(
                _analysis_row(
                    row,
                    source="comicvine",
                    source_ref=source_url,
                    status="Aucune issue correspondante",
                )
            )
            continue
        source_row = source_rows[source_index]
        exact = str(match.get("confidence") or "").casefold().startswith("exact")
        results.append(
            _analysis_row(
                row,
                source="comicvine",
                source_ref=source_row.url,
                matched_title=source_row.title,
                confidence="high" if exact else "ambiguous",
                score=float(match.get("score") or 0.0),
                candidate=source_row.metadata,
            )
        )
    return results


def analyze_book_rows(
    api: Any,
    library_id: str,
    book_ids: list[str],
    requested_source: str,
    clients: dict[str, Any],
    progress: Callable[[int, int, str], None],
    cancelled: Callable[[], bool],
    record_search: Callable[[str, str, str], None] | None = None,
) -> list[dict[str, Any]]:
    books, series_by_id = selected_books(api, library_id, book_ids, cancelled)
    selected = [
        book_explorer_row(book, series_by_id.get(str(getattr(book, "series_id", "") or "")))
        for book in books
    ]
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in selected:
        groups.setdefault(str(row.get("series_id") or ""), []).append(row)

    results: list[dict[str, Any]] = []
    total = len(groups)
    for index, rows in enumerate(groups.values(), start=1):
        if cancelled():
            break
        series = rows[0].get("series")
        progress(index - 1, total, str(getattr(series, "title", "") or ""))
        if series is None:
            results.extend(_analysis_row(row, status="Série parente introuvable") for row in rows)
            continue
        choice = choose_book_source(series, requested_source, DEFAULT_BOOK_ENRICHMENT_FIELDS)
        if not choice.source:
            results.extend(
                _analysis_row(row, status="Source à associer", error=choice.reason)
                for row in rows
            )
            continue
        if record_search is not None:
            record_search(choice.source, str(series.id), str(series.title))
        if choice.source == "manga_news":
            results.extend(_analyze_manga_news(rows, choice.url, clients["manga_news"]))
        elif choice.source == "bedetheque":
            results.extend(_analyze_bedetheque(rows, choice.url, clients["bedetheque"]))
        elif choice.source == "comicvine":
            results.extend(_analyze_comicvine(rows, choice.url, clients["comicvine"]))
    progress(total, total, "Analyse terminée")
    return results
