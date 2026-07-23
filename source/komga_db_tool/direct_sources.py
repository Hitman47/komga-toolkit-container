from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List
from urllib import parse, request


APP_USER_AGENT = "komga-toolkit/0.10.0 direct-metadata"
MANGABAKA_BASE_URL = "https://api.mangabaka.dev"
MANGANEWS_DEFAULT_URL = "http://127.0.0.1:8000"


@dataclass(frozen=True)
class DirectSearchResult:
    source: str
    source_id: str
    title: str
    url: str = ""
    details: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DirectSeriesCandidate:
    source: str
    source_id: str
    title: str
    source_url: str
    metadata: Dict[str, Any]
    cover_url: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


def _clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _base_url(value: str) -> str:
    base = (value or "").strip().rstrip("/")
    if not base:
        raise ValueError("URL de l'API vide")
    if not base.startswith(("http://", "https://")):
        base = "http://" + base
    return base


def _get_json(
    base_url: str,
    path: str,
    params: Dict[str, Any] | None = None,
    timeout: int = 30,
    headers: Dict[str, str] | None = None,
) -> Any:
    query = parse.urlencode(
        [(key, item) for key, value in (params or {}).items() for item in (value if isinstance(value, list) else [value])]
    )
    url = _base_url(base_url) + path
    if query:
        url += "?" + query
    req = request.Request(
        url,
        headers={
            "User-Agent": APP_USER_AGENT,
            "Accept": "application/json",
            **(headers or {}),
        },
    )
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _first_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _first_text(*values: Any) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    output: List[str] = []
    for item in value:
        if isinstance(item, dict):
            text = _first_text(item.get("name"), item.get("title"), item.get("label"))
        else:
            text = _clean_text(item)
        if text and text not in output:
            output.append(text)
    return output


def _alternate_titles(values: List[tuple[str, Any]], primary: str) -> List[Dict[str, str]]:
    output: List[Dict[str, str]] = []
    seen = {primary.casefold()} if primary else set()
    for label, value in values:
        candidates = value if isinstance(value, list) else [value]
        for candidate in candidates:
            title = _clean_text(candidate)
            key = title.casefold()
            if not title or key in seen:
                continue
            seen.add(key)
            output.append({"label": label, "title": title})
    return output


def _links(value: Any, source_label: str, source_url: str) -> List[Dict[str, str]]:
    output: List[Dict[str, str]] = []
    if source_url:
        output.append({"label": source_label, "url": source_url})
    if isinstance(value, dict):
        items = value.items()
    elif isinstance(value, list):
        items = [
            (_first_text(item.get("label"), item.get("title"), item.get("name"), source_label), item.get("url"))
            for item in value
            if isinstance(item, dict)
        ]
    else:
        items = []
    seen = {source_url} if source_url else set()
    for label, url in items:
        clean_url = str(url or "").strip()
        if clean_url.startswith(("http://", "https://")) and clean_url not in seen:
            seen.add(clean_url)
            output.append({"label": _clean_text(label) or source_label, "url": clean_url})
    return output


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else None


def _language_from_text(value: Any) -> str:
    text = _clean_text(value).casefold()
    mappings = {
        "ja": ("japon", "japanese", "manga"),
        "ko": ("corée", "coree", "korea", "korean", "manhwa"),
        "zh": ("chine", "china", "chinese", "manhua"),
        "fr": ("france", "français", "francais", "french"),
        "en": ("anglais", "english", "oel"),
    }
    for language, tokens in mappings.items():
        if any(token in text for token in tokens):
            return language
    return ""


class MangaBakaClient:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def search(self, query: str) -> List[DirectSearchResult]:
        payload = _get_json(
            MANGABAKA_BASE_URL,
            "/v1/series/search",
            {"q": query, "content_rating": ["safe", "suggestive"]},
            self.timeout,
        )
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        results: List[DirectSearchResult] = []
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            source_id = str(item.get("id") or "").strip()
            title = _first_text(item.get("title"), item.get("romanized_title"), item.get("native_title"))
            if not source_id or not title:
                continue
            details = " | ".join(
                value for value in (
                    _clean_text(item.get("type")),
                    _clean_text(item.get("status")),
                    _clean_text(item.get("year")),
                )
                if value
            )
            results.append(
                DirectSearchResult(
                    source="MangaBaka",
                    source_id=source_id,
                    title=title,
                    url=f"https://mangabaka.dev/{source_id}",
                    details=details,
                    raw=item,
                )
            )
        return results

    def fetch(self, result: DirectSearchResult) -> DirectSeriesCandidate:
        payload = _get_json(
            MANGABAKA_BASE_URL,
            f"/v1/series/{parse.quote(result.source_id, safe='')}",
            timeout=self.timeout,
        )
        data = _first_dict(payload.get("data")) if isinstance(payload, dict) and "data" in payload else _first_dict(payload)
        title = _first_text(data.get("title"), data.get("romanized_title"), data.get("native_title"), result.title)
        source_url = result.url or f"https://mangabaka.dev/{result.source_id}"

        metadata: Dict[str, Any] = {}
        if title:
            metadata["title"] = title
            metadata["titleSort"] = title
        summary = _clean_text(data.get("description"))
        if summary:
            metadata["summary"] = summary

        status = _clean_text(data.get("status")).casefold()
        status_map = {
            "releasing": "ONGOING",
            "upcoming": "ONGOING",
            "unknown": "ONGOING",
            "completed": "ENDED",
            "cancelled": "ABANDONED",
            "hiatus": "HIATUS",
        }
        if status in status_map:
            metadata["status"] = status_map[status]

        language = _language_from_text(data.get("type"))
        if language:
            metadata["language"] = language

        publishers = data.get("publishers")
        publisher_names: List[str] = []
        if isinstance(publishers, list):
            preferred = sorted(
                (item for item in publishers if isinstance(item, dict)),
                key=lambda item: 0 if _clean_text(item.get("type")).casefold() == "english" else 1,
            )
            publisher_names = _string_list(preferred)
        if publisher_names:
            metadata["publisher"] = publisher_names[0]

        genres = _string_list(data.get("genres"))
        tags = _string_list(data.get("tags"))
        if genres:
            metadata["genres"] = genres
        if tags:
            metadata["tags"] = tags

        total = _integer(data.get("final_volume"))
        if total is not None:
            metadata["totalBookCount"] = total

        rating = _clean_text(data.get("content_rating")).casefold()
        age_ratings = {"safe": 0, "suggestive": 12, "erotica": 16, "pornographic": 18}
        if rating in age_ratings:
            metadata["ageRating"] = age_ratings[rating]

        alternates = _alternate_titles(
            [
                ("Titre original", data.get("native_title")),
                ("Titre romanisé", data.get("romanized_title")),
                ("Titre alternatif", data.get("secondary_titles")),
            ],
            title,
        )
        if alternates:
            metadata["alternateTitles"] = alternates
        metadata["links"] = _links(data.get("links"), "MangaBaka", source_url)

        cover = _first_dict(data.get("cover"))
        cover_url = ""
        for key in ("x350", "x250", "x150", "raw"):
            value = cover.get(key)
            if isinstance(value, dict):
                cover_url = str(value.get("x1") or value.get("url") or "").strip()
            else:
                cover_url = str(value or "").strip()
            if cover_url:
                break
        return DirectSeriesCandidate(
            source="MangaBaka",
            source_id=result.source_id,
            title=title,
            source_url=source_url,
            metadata=metadata,
            cover_url=cover_url,
            raw=data,
        )


class MangaNewsClient:
    def __init__(self, base_url: str = MANGANEWS_DEFAULT_URL, timeout: int = 30, token: str = ""):
        self.base_url = _base_url(base_url)
        self.timeout = timeout
        self.token = (token or "").strip()

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": "Bearer " + self.token} if self.token else {}

    def health(self) -> bool:
        payload = _get_json(self.base_url, "/health", timeout=self.timeout, headers=self._headers())
        return bool(payload.get("ok")) if isinstance(payload, dict) else False

    def search(self, query: str) -> List[DirectSearchResult]:
        payload = _get_json(
            self.base_url,
            "/search",
            {
                "q": query,
                "kind": "series",
                "mode": "all",
                "limit": 20,
                "enrich": "false",
                "include_editions": "false",
                "prefer_main_series": "true",
                "include_related": "true",
                "include_books": "false",
            },
            self.timeout,
            headers=self._headers(),
        )
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        results: List[DirectSearchResult] = []
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            title = _first_text(item.get("title"), item.get("translated_title"), item.get("title_vo"))
            source_id = _first_text(item.get("slug"), item.get("series_slug"), item.get("url"))
            if not title or not source_id:
                continue
            details = " | ".join(
                value for value in (
                    _clean_text(item.get("media_kind")),
                    _clean_text(item.get("kind")),
                    f"score: {item.get('score')}" if item.get("score") is not None else "",
                )
                if value
            )
            results.append(
                DirectSearchResult(
                    source="Manga-News",
                    source_id=source_id,
                    title=title,
                    url=str(item.get("url") or "").strip(),
                    details=details,
                    raw=item,
                )
            )
        return results

    def fetch(self, result: DirectSearchResult) -> DirectSeriesCandidate:
        slug = _first_text(result.raw.get("slug"), result.raw.get("series_slug"))
        if slug:
            payload = _get_json(
                self.base_url,
                f"/series/{parse.quote(slug, safe='')}",
                {
                    "include_raw_sections": "false",
                    "fields": "title,title_vo,translated_title,summary,publisher_fr,publisher_vo,origin,genres,themes,advisory_age,cover_image,vf,vo,source_url",
                },
                self.timeout,
                headers=self._headers(),
            )
        elif result.url:
            payload = _get_json(
                self.base_url,
                "/series/by-url",
                {
                    "url": result.url,
                    "include_raw_sections": "false",
                    "fields": "title,title_vo,translated_title,summary,publisher_fr,publisher_vo,origin,genres,themes,advisory_age,cover_image,vf,vo,source_url",
                },
                self.timeout,
                headers=self._headers(),
            )
        else:
            raise ValueError("Résultat Manga-News sans slug ni URL")

        data = _first_dict(payload.get("data")) if isinstance(payload, dict) else {}
        title = _first_text(data.get("title"), data.get("translated_title"), data.get("title_vo"), result.title)
        source_url = _first_text(data.get("source_url"), result.url)
        metadata: Dict[str, Any] = {}
        if title:
            metadata["title"] = title
            metadata["titleSort"] = title
        summary = _clean_text(data.get("summary"))
        if summary:
            metadata["summary"] = summary

        edition = _first_dict(data.get("vf")) or _first_dict(data.get("vo"))
        status = _clean_text(edition.get("status")).casefold()
        if any(token in status for token in ("termin", "fini", "complete", "ended")):
            metadata["status"] = "ENDED"
        elif any(token in status for token in ("cours", "ongoing", "releasing")):
            metadata["status"] = "ONGOING"
        elif "hiatus" in status or "pause" in status:
            metadata["status"] = "HIATUS"
        elif "aband" in status or "cancel" in status:
            metadata["status"] = "ABANDONED"

        total = _integer(edition.get("volumes"))
        if total is not None:
            metadata["totalBookCount"] = total

        publisher = _first_text(data.get("publisher_fr"), data.get("publisher_vo"))
        if publisher:
            metadata["publisher"] = publisher
        language = _language_from_text(data.get("origin"))
        if language:
            metadata["language"] = language

        genres = _string_list(data.get("genres"))
        tags = _string_list(data.get("themes"))
        if genres:
            metadata["genres"] = genres
        if tags:
            metadata["tags"] = tags

        age_rating = _integer(data.get("advisory_age"))
        if age_rating is not None:
            metadata["ageRating"] = age_rating

        alternates = _alternate_titles(
            [
                ("Titre original", data.get("title_vo")),
                ("Titre traduit", data.get("translated_title")),
            ],
            title,
        )
        if alternates:
            metadata["alternateTitles"] = alternates
        metadata["links"] = _links([], "Manga-News", source_url)

        return DirectSeriesCandidate(
            source="Manga-News",
            source_id=slug or result.source_id,
            title=title,
            source_url=source_url,
            metadata=metadata,
            cover_url=str(data.get("cover_image") or "").strip(),
            raw=data,
        )
