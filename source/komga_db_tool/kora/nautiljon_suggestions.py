"""Optional evidence; never changes the original Komga suggestion or its selection."""
from dataclasses import replace
import csv
from .constants import KORA_GENRES
from .suggestions import KORA_GENRE_ALIASES
from .tag_logic import normalize_slug
from .nautiljon_matching import NautiljonGenreMatcher


EXTRA_ALIASES = {
    "biographique": ("documentaire-biographie",),
    "autobiographie": ("documentaire-biographie",),
}
THEMES = {
    "sport": "sport-arts-martiaux", "sport-arts-martiaux": "sport-arts-martiaux",
    "guerre": "guerre-militaire", "militaire": "guerre-militaire",
    "crime": "policier-crime", "espionnage": "espionnage",
}


def additional_nautiljon_suggestion(suggestion, record, client, matcher=None):
    result = {"status": "indisponible", "candidates": []}
    try:
        matcher = matcher or NautiljonGenreMatcher(client)
        row, identity = matcher.find(record)
        if row is None:
            return replace(suggestion, nautiljon={**result, "status": "aucun résultat", "message": identity["reason"]})
        candidate = client.get_series(row["url_fiche"])
        result.update(title=candidate.series_title, url=candidate.source_url, identity=identity)
        if not identity["safe"]:
            result.update(status="à vérifier", message=" ; ".join(identity["conflicts"]) or identity["reason"])
        else:
            evidence = {}
            for field in ("genres", "themes"):
                for label in candidate.raw.get(field, []):
                    slug = normalize_slug(label)
                    matches = ((slug,) if slug in KORA_GENRES else EXTRA_ALIASES.get(slug, KORA_GENRE_ALIASES.get(slug, ()))) if field == "genres" else ((THEMES[slug],) if slug in THEMES else ())
                    for match in matches:
                        evidence.setdefault(match, []).append({"source": f"Nautiljon {field}", "value": label})
            result.update(status="prêt" if evidence else "aucun genre compatible", candidates=[
                {"slug": slug, "evidence": values} for slug, values in evidence.items()
            ], genres=candidate.raw.get("genres", []), themes=candidate.raw.get("themes", []))
        mirror = getattr(client, "catalog_mirror", None)
        result["catalog_status"] = mirror.status if mirror else "CSV local"
    except (OSError, ValueError, RuntimeError, LookupError, csv.Error) as exc:
        result["message"] = str(exc)
    return replace(suggestion, nautiljon=result)


def nautiljon_only_suggestion(record, client, *, current_genres=None, has_pending=False, matcher=None):
    from .suggestions import suggest_series_genres, GenreCandidate, SuggestionEvidence
    from .constants import MAX_KORA_GENRES
    base = suggest_series_genres(replace(record, genres=[], tags=[]), current_genres=current_genres, has_pending=has_pending)
    result = additional_nautiljon_suggestion(base, record, client, matcher)
    candidates = tuple(GenreCandidate(row["slug"], 100, tuple(SuggestionEvidence(item["value"], item["source"], 100) for item in row["evidence"])) for row in (result.nautiljon or {}).get("candidates", []))
    selected = list(result.current_genres)
    overflow = []
    for candidate in candidates:
        if candidate.slug not in selected:
            if len(selected) < MAX_KORA_GENRES:
                selected.append(candidate.slug)
            else:
                overflow.append(candidate.slug)
    return replace(result, proposal_source="nautiljon", komga_genres=tuple(record.genres), komga_tags=tuple(record.tags), suggested_genres=tuple(selected), candidates=candidates, overflow_genres=tuple(overflow))


def merged_suggestion(record, client=None, *, current_genres=None, has_pending=False, matcher=None, error=""):
    from .suggestions import suggest_series_genres, GenreCandidate, SuggestionEvidence
    from .constants import MAX_KORA_GENRES
    base = suggest_series_genres(record, current_genres=current_genres, has_pending=has_pending)
    result = additional_nautiljon_suggestion(base, record, client, matcher) if client is not None else replace(
        base, nautiljon={"status": "indisponible", "message": error or "CSV Nautiljon non configuré", "candidates": []})
    evidence = {candidate.slug: list(candidate.evidence) for candidate in base.candidates}
    for row in (result.nautiljon or {}).get("candidates", []):
        for item in row["evidence"]:
            value = SuggestionEvidence(item["value"], item["source"], 100 if item["source"].endswith("genres") else 80)
            bucket = evidence.setdefault(row["slug"], [])
            if value not in bucket:
                bucket.append(value)
    candidates = [GenreCandidate(slug, max(item.score for item in values), tuple(values)) for slug, values in evidence.items()]
    # Strong explicit genres first; agreement between both sources breaks ties.
    candidates.sort(key=lambda candidate: (-candidate.score, -len({"nautiljon" if e.source.startswith("Nautiljon") else "local" for e in candidate.evidence}), KORA_GENRES.index(candidate.slug)))
    selected, overflow = list(result.current_genres), []
    for candidate in candidates:
        if candidate.slug not in selected:
            (selected if len(selected) < MAX_KORA_GENRES else overflow).append(candidate.slug)
    return replace(result, proposal_source="combined", suggested_genres=tuple(selected), candidates=tuple(candidates), overflow_genres=tuple(overflow))
