from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional


PROVIDER_ALIASES = {
    "anilist": "ANILIST",
    "ani_list": "ANILIST",
    "ani list": "ANILIST",
    "bangumi": "BANGUMI",
    "bookwalker": "BOOK_WALKER",
    "book_walker": "BOOK_WALKER",
    "book walker": "BOOK_WALKER",
    "comicvine": "COMIC_VINE",
    "comic_vine": "COMIC_VINE",
    "comic vine": "COMIC_VINE",
    "hentag": "HENTAG",
    "kodansha": "KODANSHA",
    "mangabaka": "MANGA_BAKA",
    "manga_baka": "MANGA_BAKA",
    "manga baka": "MANGA_BAKA",
    "mangadex": "MANGA_DEX",
    "manga_dex": "MANGA_DEX",
    "manga dex": "MANGA_DEX",
    "mangaupdates": "MANGA_UPDATES",
    "manga_updates": "MANGA_UPDATES",
    "manga updates": "MANGA_UPDATES",
    "myanimelist": "MAL",
    "my_anime_list": "MAL",
    "mal": "MAL",
    "nautiljon": "NAUTILJON",
    "viz": "VIZ",
    "webtoon": "WEBTOONS",
    "webtoons": "WEBTOONS",
    "yenpress": "YEN_PRESS",
    "yen_press": "YEN_PRESS",
    "yen press": "YEN_PRESS",
}

KNOWN_PROVIDERS = tuple(sorted(set(PROVIDER_ALIASES.values())))
RESULT_CONTAINER_KEYS = {"results", "data", "content", "items", "matches", "metadata"}


@dataclass(frozen=True)
class KomfSourceResult:
    provider: str
    provider_series_id: str
    title: str
    media_type: str = ""
    volume_count: str = ""
    details: str = ""
    raw: Any = None


def normalize_provider(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    key = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    return PROVIDER_ALIASES.get(key, PROVIDER_ALIASES.get(key.replace("_", ""), raw.upper()))


def _iter_result_dicts(data: Any, parent_provider: Optional[str] = None) -> Iterable[tuple[dict[str, Any], str]]:
    if isinstance(data, list):
        for item in data:
            yield from _iter_result_dicts(item, parent_provider)
        return
    if not isinstance(data, dict):
        return

    provider = normalize_provider(
        data.get("provider") or data.get("metadataProvider") or data.get("source") or parent_provider
    )
    for key in RESULT_CONTAINER_KEYS:
        value = data.get(key)
        if isinstance(value, (list, dict)):
            yield from _iter_result_dicts(value, provider)

    for key, value in data.items():
        if key in RESULT_CONTAINER_KEYS or not isinstance(value, (list, dict)):
            continue
        grouped_provider = normalize_provider(key)
        if grouped_provider in KNOWN_PROVIDERS:
            yield from _iter_result_dicts(value, grouped_provider)

    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    result_id = (
        data.get("providerSeriesId")
        or data.get("resultId")
        or data.get("metadataId")
        or data.get("externalId")
        or data.get("seriesId")
        or data.get("id")
        or data.get("slug")
    )
    title = data.get("title") or data.get("name") or metadata.get("title") or metadata.get("name")
    if provider and result_id is not None and title:
        yield data, provider


def parse_komf_results(data: Any) -> list[KomfSourceResult]:
    results: list[KomfSourceResult] = []
    seen: set[tuple[str, str, str]] = set()
    for item, provider in _iter_result_dicts(data):
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        result_id = str(
            item.get("providerSeriesId")
            or item.get("resultId")
            or item.get("metadataId")
            or item.get("externalId")
            or item.get("seriesId")
            or item.get("id")
            or item.get("slug")
            or ""
        )
        title = str(item.get("title") or item.get("name") or metadata.get("title") or metadata.get("name") or "")
        dedupe = (provider, result_id, title)
        if not result_id or not title or dedupe in seen:
            continue
        seen.add(dedupe)

        media_type = str(
            item.get("mediaType")
            or item.get("type")
            or metadata.get("mediaType")
            or metadata.get("type")
            or ""
        )
        volume_count = str(
            item.get("volumeCount")
            or item.get("volumes")
            or item.get("totalBookCount")
            or metadata.get("volumeCount")
            or metadata.get("totalBookCount")
            or ""
        )
        details = []
        for key in ("status", "year", "releaseYear", "publisher"):
            value = item.get(key) if item.get(key) not in (None, "") else metadata.get(key)
            if value not in (None, "", [], {}):
                details.append(f"{key}: {value}")
        results.append(
            KomfSourceResult(
                provider=provider,
                provider_series_id=result_id,
                title=title,
                media_type=media_type,
                volume_count=volume_count,
                details=" | ".join(details),
                raw=item,
            )
        )
    return results


def parse_provider_names(data: Any) -> list[str]:
    providers: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, str):
            provider = normalize_provider(value)
            if provider:
                providers.add(provider)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            direct = value.get("provider") or value.get("name") or value.get("id")
            if isinstance(direct, str):
                providers.add(normalize_provider(direct))
            for key, child in value.items():
                if normalize_provider(key) in KNOWN_PROVIDERS:
                    providers.add(normalize_provider(key))
                if isinstance(child, (list, dict)):
                    visit(child)

    visit(data)
    return sorted(provider for provider in providers if provider)
