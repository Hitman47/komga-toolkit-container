"""Shared, explainable series identity checks. No network or metadata writes."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import hashlib
import json
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

from .bedetheque import title_similarity
from .metadata_quality import build_conservative_search_queries, clean_title_for_search

TITLE_FIELDS = (
    "title", "series_title", "name", "alternateTitles", "alternate_title",
    "original_title", "title_vo", "translated_title", "native_title",
    "romanized_title", "secondary_titles", "titles", "aliases",
    "alternate_titles", "alternative_titles", "nativeTitle", "romaji_title",
    "english_title", "french_title",
    "titre", "titre_alternatif", "titre_original",
)


def mapping(value):
    if isinstance(value, dict):
        return value
    if is_dataclass(value):
        return asdict(value)
    return vars(value) if hasattr(value, "__dict__") else {}


def fingerprint(value):
    return hashlib.sha256(json.dumps(mapping(value), sort_keys=True, ensure_ascii=False,
                                     default=str).encode("utf-8")).hexdigest()


def _values(value):
    if isinstance(value, str):
        if value.strip().casefold() not in {"", "n/a", "none", "null", "-"}:
            yield value.strip()
    elif isinstance(value, dict):
        preferred = value.get("title") or value.get("name") or value.get("value")
        if preferred:
            yield from _values(preferred)
        else:
            for key, nested in value.items():
                if key not in {"label", "language", "type", "id"}:
                    yield from _values(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _values(nested)


def title_evidence(value):
    if isinstance(value, str):
        return [(value, "title")] if value.strip() else []
    root = mapping(value)
    rows, seen = [], set()
    sources = [("", root)] + [(key + ".", mapping(root.get(key)))
                              for key in ("metadata", "series_metadata", "raw")]
    sources.append(("raw.csv_row.", mapping(mapping(root.get("raw")).get("csv_row"))))
    for prefix, data in sources:
        for key in TITLE_FIELDS:
            value = data.get(key)
            if key == "aliases" and isinstance(value, str):
                value = value.splitlines()
            for title in _values(value):
                normalized = normalize_title(title)
                if normalized and normalized not in seen:
                    rows.append((title, prefix + key))
                    seen.add(normalized)
    return rows[:32]


def query_title(value):
    return re.sub(r"\s*\(chap\)\s*$", "", clean_title_for_search(str(value or "")), flags=re.I).strip()


def normalize_title(value):
    text = query_title(value)
    text = unicodedata.normalize("NFKD", text).casefold()
    return " ".join("".join(c if c.isalnum() else " " for c in text
                            if not unicodedata.combining(c)).split())


def match_titles(target, candidate):
    best = {"score": 0.0, "target_title": "", "source_title": "",
            "target_field": "", "source_field": ""}
    for left, left_field in title_evidence(target):
        for right, right_field in title_evidence(candidate):
            # Unicode exact matching matters: the legacy fuzzy matcher is Latin-only.
            a, b = normalize_title(left), normalize_title(right)
            score = 1.0 if a and a == b else title_similarity(
                query_title(left), query_title(right))
            if score > best["score"]:
                best = dict(score=round(score, 6), target_title=left, source_title=right,
                            target_field=left_field, source_field=right_field)
    return best


def _metadata(value):
    root = mapping(value)
    return {**root, **mapping(root.get("metadata")), **mapping(root.get("series_metadata"))}


def _links(value):
    data = _metadata(value)
    urls = [data.get("source_url"), data.get("url")]
    urls += [mapping(row).get("url") for row in data.get("links") or []]
    out = set()
    for url in urls:
        if not url:
            continue
        parts = urlsplit(str(url))
        if parts.hostname:
            # Keep query and fragment: they may identify an edition.
            out.add(urlunsplit(("https", parts.netloc.lower(), parts.path.rstrip("/"),
                               parts.query, parts.fragment)))
    return out


def _authors(value):
    data = _metadata(value)
    names = []
    for row in data.get("authors") or []:
        entry = mapping(row)
        if entry.get("role", "writer") in {"writer", "penciller", "artist", "story", "art"}:
            names.extend(_values(entry.get("name")))
    return {" ".join(sorted(normalize_title(name).split())) for name in names if name}


def _edition(value):
    data = _metadata(value)
    label = data.get("edition_label") or mapping(data.get("raw")).get("edition_label")
    text = " ".join([str(label or ""), str(data.get("title") or data.get("series_title") or data.get("titre") or "")]).casefold()
    return {word for word in ("perfect", "deluxe", "omnibus", "integrale", "intégrale",
                              "bunkoban", "kanzenban", "collector", "light novel", "spin-off", "spinoff") if word in text}


def assess_identity(target, candidate, peers=()):
    result = match_titles(target, candidate)
    conflicts, evidence = [], []
    left_links, right_links = _links(target), _links(candidate)
    linked = bool(left_links & right_links)
    if linked:
        evidence.append("Lien source identique")
    elif any(urlsplit(a).hostname == urlsplit(b).hostname for a in left_links for b in right_links):
        conflicts.append("Lien existant différent sur la même source")
    a, b = _authors(target), _authors(candidate)
    if a and b:
        if a & b:
            evidence.append("Auteur concordant")
        else:
            conflicts.append("Auteurs disponibles non concordants")
    a, b = _edition(target), _edition(candidate)
    if a != b and (a or b):
        conflicts.append("Édition à confirmer")
    candidate_data = mapping(candidate)
    candidate_id = str(candidate_data.get("id") or candidate_data.get("series_id") or candidate_data.get("slug") or "")
    def same_candidate(row):
        data = mapping(row)
        row_id = str(data.get("id") or data.get("series_id") or data.get("slug") or "")
        return bool((_links(row) & right_links) or (candidate_id and row_id == candidate_id
                    and data.get("edition_label", "") == candidate_data.get("edition_label", ""))
                    or fingerprint(row) == fingerprint(candidate))
    other_scores = [match_titles(target, row)["score"] for row in peers if not same_candidate(row)]
    ambiguous = any(score >= 0.90 and score >= result["score"] - 0.08 for score in other_scores)
    if ambiguous and not linked:
        conflicts.append("Plusieurs candidats proches : homonyme ou autre édition possible")
    safe = bool((linked or result["score"] >= 0.90) and not conflicts)
    reason = "Correspondance étayée" if safe else "Identité non confirmée — validation manuelle requise"
    return {**result, "safe": safe, "reason": reason, "evidence": evidence, "conflicts": conflicts}


def identity_text(result):
    return (f"{result['reason']}\nSimilarité : {result['score']:.3f} (pas une probabilité)\n"
            f"Komga [{result['target_field']}] : {result['target_title']}\n"
            f"Source [{result['source_field']}] : {result['source_title']}\n"
            + " ; ".join(result["evidence"] + result["conflicts"]))


def search_queries(query, target=None, limit=3):
    # Known aliases before broad fallback variants; a fixed request budget per search.
    values = [query] + [title for title, _ in title_evidence(target)]
    values += build_conservative_search_queries(query, max_queries=limit)
    rows, seen = [], set()
    for value in values:
        key = normalize_title(value)
        if key and key not in seen:
            rows.append(query_title(value))
            seen.add(key)
    return rows[:limit]


def search_aliases(query, target, search, limit=3):
    rows, seen, attempts = [], set(), []
    for text in search_queries(query, target, limit):
        found = search(text)
        attempts.append({"query": text, "count": len(found)})
        for row in found:
            data = mapping(row)
            key = str(data.get("url") or data.get("source_url") or data.get("id")
                      or data.get("slug") or fingerprint(row))
            key += "|" + str(data.get("edition_label") or "")
            if key not in seen:
                rows.append(row)
                seen.add(key)
        ranked = sorted(rows, key=lambda row: -match_titles(target or query, row)["score"])
        if ranked and assess_identity(target or query, ranked[0], ranked[1:])["safe"]:
            break
    rows.sort(key=lambda row: -match_titles(target or query, row)["score"])
    return {"rows": rows, "attempts": attempts, "used_query": attempts[-1]["query"] if attempts else query}
