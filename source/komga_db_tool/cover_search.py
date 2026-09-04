from __future__ import annotations

import re
import unicodedata
import json
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict
from urllib import request as urlrequest
from urllib.parse import urlencode, urlparse


def _key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def cover_search_query(series_title: Any, number: Any = "", book_title: Any = "") -> str:
    """Build a concise cover query without repeating equivalent title parts."""
    parts: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        text = str(value or "").strip()
        key = _key(text)
        if text and key and key not in seen:
            parts.append(text)
            seen.add(key)

    add(series_title)
    if str(number or "").strip():
        add(f"tome {str(number).strip()}")
    add(book_title)
    add("couverture")
    return " ".join(parts)


@dataclass(frozen=True)
class CoverImageCandidate:
    id: str
    title: str
    image_url: str
    thumbnail_url: str
    source_url: str
    width: int = 0
    height: int = 0
    provider: str = "duckduckgo_images"

    def public(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DownloadedCover:
    data: bytes
    final_url: str
    content_type: str
    used_fallback: bool = False


def _http_url(value: Any) -> str:
    text = str(value or "").strip()
    parsed = urlparse(text)
    return text if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def download_cover_image(
    image_url: str,
    *,
    thumbnail_url: str = "",
    source_url: str = "",
    timeout: int = 60,
    max_bytes: int = 25 * 1024 * 1024,
    validate_url: Callable[[str], None] | None = None,
) -> DownloadedCover:
    """Download a selected result, retrying common anti-hotlink variants and its thumbnail."""
    original = _http_url(image_url)
    fallback = _http_url(thumbnail_url)
    source = _http_url(source_url)
    if not original:
        raise ValueError("Le résultat sélectionné ne contient pas d'URL image HTTP(S) valide")
    urls = [original]
    if fallback and fallback != original:
        urls.append(fallback)
    errors: list[str] = []
    for candidate_url in urls:
        image_origin = urlparse(candidate_url)
        origin_referer = f"{image_origin.scheme}://{image_origin.netloc}/"
        referers: list[str] = []
        for referer in (source, origin_referer, "https://duckduckgo.com/", ""):
            if referer not in referers:
                referers.append(referer)
        for referer in referers:
            try:
                if validate_url is not None:
                    validate_url(candidate_url)
                headers = {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131 Safari/537.36"
                    ),
                    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
                }
                if referer:
                    headers["Referer"] = referer
                request = urlrequest.Request(candidate_url, headers=headers)
                with urlrequest.urlopen(request, timeout=max(5, int(timeout))) as response:
                    final_url = str(getattr(response, "geturl", lambda: candidate_url)() or candidate_url)
                    if validate_url is not None:
                        validate_url(final_url)
                    content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                    if not content_type.startswith("image/"):
                        raise ValueError("La réponse distante n'est pas une image")
                    data = response.read(max_bytes + 1)
                if not data or len(data) > max_bytes:
                    raise ValueError("Image vide ou supérieure à 25 Mio")
                return DownloadedCover(
                    data=data,
                    final_url=final_url,
                    content_type=content_type,
                    used_fallback=candidate_url != original,
                )
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
    detail = errors[-1] if errors else "échec inconnu"
    raise RuntimeError(f"Impossible de télécharger l'image originale ou sa vignette ({detail})")


class DuckDuckGoCoverSearchClient:
    """User-triggered DuckDuckGo Images reader used by the integrated cover picker."""

    SEARCH_URL = "https://duckduckgo.com/"
    IMAGES_URL = "https://duckduckgo.com/i.js"

    def __init__(self, timeout: int = 30) -> None:
        self.timeout = max(5, int(timeout))

    def search(self, query: str, limit: int = 30) -> list[CoverImageCandidate]:
        text = str(query or "").strip()
        if not text:
            return []
        page_url = f"{self.SEARCH_URL}?{urlencode({'q': text, 'iax': 'images', 'ia': 'images'})}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.7",
        }
        page_req = urlrequest.Request(page_url, headers=headers)
        with urlrequest.urlopen(page_req, timeout=self.timeout) as response:
            page = response.read(2 * 1024 * 1024 + 1)
        if len(page) > 2 * 1024 * 1024:
            raise ValueError("Réponse du moteur de recherche anormalement volumineuse")
        match = re.search(rb"vqd=['\"]?([^&'\"\\]+)", page)
        if not match:
            raise RuntimeError("Le moteur de recherche n'a pas fourni de jeton de résultats")
        vqd = match.group(1).decode("ascii", errors="ignore")
        images_url = f"{self.IMAGES_URL}?{urlencode({
            'q': text,
            'o': 'json',
            'vqd': vqd,
            'f': ',,,',
            'p': '1',
        })}"
        images_req = urlrequest.Request(
            images_url,
            headers={
                **headers,
                "Accept": "application/json",
                "Referer": page_url,
            },
        )
        with urlrequest.urlopen(images_req, timeout=self.timeout) as response:
            body = response.read(5 * 1024 * 1024 + 1)
        if len(body) > 5 * 1024 * 1024:
            raise ValueError("Réponse du moteur de recherche anormalement volumineuse")
        payload = json.loads(body.decode("utf-8", errors="replace"))
        metadata = payload.get("results") if isinstance(payload, dict) else []
        output: list[CoverImageCandidate] = []
        seen: set[str] = set()
        for item in metadata if isinstance(metadata, list) else []:
            if not isinstance(item, dict):
                continue
            image_url = _http_url(item.get("image"))
            thumbnail_url = _http_url(item.get("thumbnail")) or image_url
            source_url = _http_url(item.get("url"))
            if not image_url or image_url in seen:
                continue
            seen.add(image_url)
            output.append(CoverImageCandidate(
                id=f"ddg-{len(output) + 1}",
                title=str(item.get("title") or "Image sans titre").strip(),
                image_url=image_url,
                thumbnail_url=thumbnail_url,
                source_url=source_url,
                width=int(item.get("width") or 0),
                height=int(item.get("height") or 0),
            ))
            if len(output) >= min(60, max(1, int(limit))):
                break
        return output
