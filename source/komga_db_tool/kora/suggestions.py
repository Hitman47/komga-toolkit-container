from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from .constants import KORA_GENRES, MAX_KORA_GENRES
from .models import SeriesRecord
from .tag_logic import genre_label, normalize_slug, unique_preserve_order


# Correspondances volontaires et déterministes. Les genres Komga sont prioritaires ;
# les tags ne sont consultés qu'avec un score inférieur.
KORA_GENRE_ALIASES: dict[str, tuple[str, ...]] = {
    "adventure": ("aventure",),
    "biographie": ("documentaire-biographie",),
    "biography": ("documentaire-biographie",),
    "documentary": ("documentaire-biographie",),
    "crime": ("policier-crime",),
    "detective": ("policier-crime",),
    "drama": ("drame",),
    "fantastic": ("fantastique-surnaturel",),
    "fantastique": ("fantastique-surnaturel",),
    "supernatural": ("fantastique-surnaturel",),
    "surnaturel": ("fantastique-surnaturel",),
    "historical": ("historique",),
    "history": ("historique",),
    "horror": ("horreur",),
    "humour": ("comedie",),
    "military": ("guerre-militaire",),
    "war": ("guerre-militaire",),
    "policier": ("policier-crime",),
    "slice-of-life": ("tranche-de-vie",),
    "society": ("societe",),
    "sport": ("sport-arts-martiaux",),
    "sports": ("sport-arts-martiaux",),
    "suspense": ("thriller-suspense",),
    "thriller": ("thriller-suspense",),
    "western": ("western",),
    "young-adult": ("jeunesse",),
    "youth": ("jeunesse",),
}


@dataclass(frozen=True)
class SuggestionEvidence:
    value: str
    source: str
    score: int

    def public(self) -> dict[str, object]:
        return {"value": self.value, "source": self.source, "score": self.score}


@dataclass(frozen=True)
class GenreCandidate:
    slug: str
    score: int
    evidence: tuple[SuggestionEvidence, ...]

    def public(self, *, selected: bool) -> dict[str, object]:
        return {
            "slug": self.slug,
            "label": genre_label(self.slug),
            "score": self.score,
            "selected": selected,
            "evidence": [item.public() for item in self.evidence],
        }


@dataclass(frozen=True)
class SeriesGenreSuggestion:
    series_id: str
    library_name: str
    title: str
    komga_genres: tuple[str, ...]
    komga_tags: tuple[str, ...]
    current_genres: tuple[str, ...]
    suggested_genres: tuple[str, ...]
    candidates: tuple[GenreCandidate, ...]
    overflow_genres: tuple[str, ...]
    has_pending: bool
    over_limit: bool

    @property
    def changed(self) -> bool:
        return self.suggested_genres != self.current_genres and not self.over_limit

    def public(self) -> dict[str, object]:
        selected = set(self.suggested_genres)
        return {
            "series_id": self.series_id,
            "library_name": self.library_name,
            "title": self.title,
            "komga_genres": list(self.komga_genres),
            "komga_tags": list(self.komga_tags),
            "current_genres": list(self.current_genres),
            "suggested_genres": list(self.suggested_genres),
            "candidates": [
                candidate.public(selected=candidate.slug in selected)
                for candidate in self.candidates
            ],
            "overflow_genres": list(self.overflow_genres),
            "has_pending": self.has_pending,
            "over_limit": self.over_limit,
            "changed": self.changed,
        }


def _contains_slug(value: str, token: str) -> bool:
    return bool(re.search(rf"(?:^|-){re.escape(token)}(?:-|$)", value))


def _matches_for_value(value: str, *, source: str) -> list[tuple[str, int]]:
    slug = normalize_slug(value)
    if not slug:
        return []
    exact_score = 100 if source == "genre" else 80
    # Une traduction/alias déclaré est aussi fiable qu'un libellé canonique.
    alias_score = exact_score
    contained_score = 90 if source == "genre" else 70

    if slug in KORA_GENRES:
        return [(slug, exact_score)]
    if slug in KORA_GENRE_ALIASES:
        return [(candidate, alias_score) for candidate in KORA_GENRE_ALIASES[slug]]

    matches: list[tuple[str, int]] = []
    for candidate in KORA_GENRES:
        if _contains_slug(slug, candidate):
            matches.append((candidate, contained_score))
    for token, candidates in KORA_GENRE_ALIASES.items():
        if _contains_slug(slug, token):
            matches.extend((candidate, contained_score) for candidate in candidates)
    return _dedupe_scored(matches)


def _dedupe_scored(values: Iterable[tuple[str, int]]) -> list[tuple[str, int]]:
    scores: dict[str, int] = {}
    for slug, score in values:
        if slug in KORA_GENRES:
            scores[slug] = max(score, scores.get(slug, 0))
    return [(slug, scores[slug]) for slug in KORA_GENRES if slug in scores]


def suggest_genre_for_value(value: str) -> tuple[str, int]:
    """Compatibilité avec l'analyse globale : retourne la meilleure correspondance."""
    matches = _matches_for_value(value, source="genre")
    if not matches:
        return "", 0
    order = {slug: index for index, slug in enumerate(KORA_GENRES)}
    return min(matches, key=lambda item: (-item[1], order[item[0]]))


def suggest_series_genres(
    record: SeriesRecord,
    *,
    current_genres: Iterable[str] | None = None,
    has_pending: bool = False,
    max_genres: int = MAX_KORA_GENRES,
) -> SeriesGenreSuggestion:
    current = tuple(
        value
        for value in unique_preserve_order(
            normalize_slug(item)
            for item in (record.kora_genres if current_genres is None else current_genres)
        )
        if value in KORA_GENRES
    )
    evidence_by_genre: dict[str, list[SuggestionEvidence]] = {}

    for source, values in (("genre", record.genres), ("tag", record.tags)):
        for raw_value in values:
            value = str(raw_value or "").strip()
            lower = value.casefold()
            if not value or lower.startswith(("kora:genre:", "kora:tag:", "kora:taxonomy:")):
                continue
            for slug, score in _matches_for_value(value, source=source):
                evidence = SuggestionEvidence(value=value, source=source, score=score)
                bucket = evidence_by_genre.setdefault(slug, [])
                if evidence not in bucket:
                    bucket.append(evidence)

    candidates = [
        GenreCandidate(
            slug=slug,
            score=max(item.score for item in evidence),
            evidence=tuple(sorted(evidence, key=lambda item: (-item.score, item.source, item.value.casefold()))),
        )
        for slug, evidence in evidence_by_genre.items()
    ]
    taxonomy_order = {slug: index for index, slug in enumerate(KORA_GENRES)}
    candidates.sort(
        key=lambda candidate: (
            -candidate.score,
            -len(candidate.evidence),
            taxonomy_order[candidate.slug],
        )
    )

    selected = list(current)
    overflow: list[str] = []
    for candidate in candidates:
        if candidate.slug in selected:
            continue
        if len(selected) < max_genres:
            selected.append(candidate.slug)
        else:
            overflow.append(candidate.slug)

    return SeriesGenreSuggestion(
        series_id=record.id,
        library_name=record.library_name,
        title=record.title,
        komga_genres=tuple(record.genres),
        komga_tags=tuple(record.tags),
        current_genres=current,
        suggested_genres=tuple(selected),
        candidates=tuple(candidates),
        overflow_genres=tuple(overflow),
        has_pending=has_pending,
        over_limit=len(current) > max_genres,
    )
