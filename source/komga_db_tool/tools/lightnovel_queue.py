#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Komf Light Novel Queue UI

Outil manuel pour traiter une file de séries Komga avec Komf.
- Mode simulation activé par défaut.
- Mode manuel : recherche Komf, sélection explicite, puis POST /komga/identify.
- En mode réel manuel, l'application est mise en file sans confirmation et l'UI passe immédiatement à la tâche suivante.
- Un onglet dédié affiche la progression détaillée des applications Komf en attente, en cours, terminées ou en échec.
- Mode Auto Identify v2 : traitement par lot d'une sélection via POST /komga/match/library/{libraryId}/series/{seriesId}.
- Le mode Auto Identify ne lance jamais le matching global de bibliothèque.
- Aucune écriture en simulation.

Dépendance externe unique : PySide6.
Installation : python -m pip install PySide6
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import traceback
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib import error, parse, request

from PySide6.QtCore import Qt, QThreadPool, Signal, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QFrame,
    QScrollArea,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QMenu,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..runtime import SecretRedactor
from ..qt_tasks import Worker

APP_TITLE = "Komf Queue - Light Novels"
APP_VERSION = "0.4.7-final"
DEFAULT_KOMGA_URL = "http://192.168.1.30:25600"
DEFAULT_KOMF_URL = "http://192.168.1.30:8085"
KOMF_FAST_TIMEOUT_SECONDS = 10
KOMF_MATCH_TIMEOUT_SECONDS = 180


class HttpError(RuntimeError):
    def __init__(self, method: str, url: str, status: Optional[int], body: str):
        self.method = method
        self.url = url
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status or '?'} {method} {url}\n{body}")

def compact_error_text(text: str, limit: int = 900) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"

def is_recoverable_komf_search_error(exc: HttpError) -> bool:
    """Erreurs provider Komf à afficher proprement sans planter l'UI.

    Exemple réel : Komf renvoie HTTP 500 parce qu'AniList renvoie HTTP 403
    avec « API temporarily disabled ». Ce n'est pas une erreur du script :
    c'est un provider externe indisponible et Komf remonte cela comme 500.
    """
    body = exc.body or ""
    folded = fold_text(body)
    if exc.status and exc.status >= 500:
        return True
    if any(token in folded for token in (
        "anilist",
        "graphql.anilist.co",
        "clientrequestexception",
        "temporarily disabled",
        "severe stability issues",
    )):
        return True
    return False

def komf_search_error_payload(path: str, query: Dict[str, Any], exc: HttpError) -> Dict[str, Any]:
    return {
        "error": "komf_search_failed",
        "status": exc.status,
        "method": exc.method,
        "url": exc.url,
        "path": path,
        "query": query,
        "body": exc.body,
        "summary": compact_error_text(exc.body),
    }


class ApiClient:
    def __init__(self, base_url: str, api_key: Optional[str] = None, timeout: int = 30):
        self.base_url = normalize_base_url(base_url)
        self.api_key = (api_key or "").strip()
        self.timeout = timeout

    def get(self, path: str, query: Optional[Dict[str, Any]] = None, timeout: Optional[int] = None) -> Any:
        return self.request("GET", path, query=query, timeout=timeout)

    def post(
        self,
        path: str,
        body: Optional[Any] = None,
        query: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> Any:
        return self.request("POST", path, body=body, query=query, timeout=timeout)

    def request(
        self,
        method: str,
        path: str,
        body: Optional[Any] = None,
        query: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> Any:
        if not path.startswith("/"):
            path = "/" + path
        url = self.base_url + path
        if query:
            clean_query = {k: v for k, v in query.items() if v is not None and v != ""}
            if clean_query:
                url += "?" + parse.urlencode(clean_query, doseq=True)

        data = None
        headers = {
            "Accept": "application/json",
            "User-Agent": f"komf-lightnovel-queue-ui/{APP_VERSION}",
        }
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = request.Request(url, data=data, headers=headers, method=method)
        try:
            effective_timeout = timeout if timeout is not None else self.timeout
            with request.urlopen(req, timeout=effective_timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                if not raw.strip():
                    return None
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return raw
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise HttpError(method, url, exc.code, raw) from exc
        except error.URLError as exc:
            raise HttpError(method, url, None, str(exc)) from exc
        except TimeoutError as exc:
            effective_timeout = timeout if timeout is not None else self.timeout
            raise HttpError(method, url, None, f"Timeout après {effective_timeout}s: {exc}") from exc


def normalize_base_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise ValueError("URL vide")
    if not re.match(r"^https?://", url, re.I):
        url = "http://" + url
    return url.rstrip("/")


def clean_search_title(title: str) -> str:
    """Nettoyage volontairement léger : on ne veut pas déformer les titres.

    Komga peut stocker certains titres avec l'article de tri à la fin,
    par exemple "Fille De La Plage (La)". Ces suffixes sont utiles pour le
    tri, mais ils dégradent fortement la recherche Komf/provider. On les
    retire uniquement du champ de recherche, jamais des métadonnées Komga.
    """
    text = title or ""
    text = re.sub(r"\[[^\]]*\]", " ", text)
    text = re.sub(r"\((?:light\s*novel|ln|novel|roman|tome|vol\.?|volume)[^)]*\)", " ", text, flags=re.I)
    text = re.sub(r"\b(?:light\s*novel|ln|novel|roman|tome|vol\.?|volume)\b", " ", text, flags=re.I)

    # Articles déplacés en fin de titre pour le tri :
    # "Nom (The)", "Nom (La)", "Nom (Les)", etc.
    # On ne retire que le dernier bloc parenthésé et uniquement s'il contient
    # un article très court et connu, afin de ne pas supprimer des sous-titres
    # ou années utiles comme "(2018)".
    text = re.sub(
        r"\s*\((?:the|a|an|le|la|les|l[’']|l|un|une|des|du|de la|de l[’']|de l|el|los|las)\)\s*$",
        "",
        text,
        flags=re.I,
    )

    text = re.sub(r"\s+", " ", text).strip(" -_.,;:")
    return text or title


def fold_text(text: str) -> str:
    """Texte normalisé pour les filtres locaux : minuscules + sans accents."""
    normalized = unicodedata.normalize("NFKD", text or "")
    without_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return without_accents.lower()


def unique_nonempty(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    output: List[str] = []
    for value in values:
        text = re.sub(r"\s+", " ", (value or "").strip(" -_.,;:"))
        if not text:
            continue
        key = fold_text(text)
        if key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output


def build_search_variants(title: str) -> List[str]:
    """
    Komf / les providers sont beaucoup moins bons avec les titres LN très longs.
    L'extension Komf semble souvent réussir grâce à des variantes plus courtes.
    On ne choisit rien automatiquement : on élargit seulement la recherche.
    """
    base = clean_search_title(title)
    variants: List[str] = [title, base]

    # Retirer les marqueurs courants sans toucher au sens du titre.
    stripped = re.sub(r"\b(?:light\s*novel|novel|ln|roman)\b", " ", base, flags=re.I)
    variants.append(stripped)

    # Couper les sous-titres après séparateurs fréquents.
    for sep in (" - ", " – ", " — ", ": "):
        if sep in base:
            left = base.split(sep, 1)[0]
            if len(left.split()) >= 2:
                variants.append(left)

    # Variante spécifique utile pour les titres LN anglais très longs :
    # "Title by the X - Subtitle..." -> "Title by the X".
    m = re.match(r"^(.{8,120}?\bby\b[^:;\-–—]{3,80})(?:\s*[-–—:]\s+.+)$", base, flags=re.I)
    if m:
        variants.append(m.group(1))

    # Retirer les numéros/volumes résiduels.
    no_volume = re.sub(r"\b(?:vol\.?|volume|tome)\s*\d+\b", " ", base, flags=re.I)
    no_volume = re.sub(r"\b\d+\s*(?:livres?|books?)\b", " ", no_volume, flags=re.I)
    variants.append(no_volume)

    # Certains providers préfèrent le titre sans apostrophe typographique ou ponctuation finale.
    simplified = base.replace("’", "'").replace("`", "'")
    simplified = re.sub(r"[!?]+$", "", simplified).strip()
    variants.append(simplified)

    return unique_nonempty(variants)


def safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return ", ".join(safe_str(x) for x in value if safe_str(x))
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def truthy_nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


IMAGE_KEYWORDS = ("cover", "thumbnail", "image", "poster", "picture", "avatar")
COUNT_KEYS = (
    "totalBookCount",
    "totalBooks",
    "bookCount",
    "booksCount",
    "numberOfBooks",
    "volumeCount",
    "volumesCount",
    "totalVolumeCount",
    "totalVolumes",
    "volumes",
)

URL_KEYS = (
    "url",
    "href",
    "link",
    "links",
    "siteUrl",
    "site_url",
    "externalUrl",
    "external_url",
    "providerUrl",
    "provider_url",
    "pageUrl",
    "page_url",
    "sourceUrl",
    "source_url",
    "webUrl",
    "web_url",
)

MEDIA_KEYS = (
    "type",
    "mediaType",
    "media_type",
    "format",
    "category",
    "kind",
    "contentType",
    "content_type",
    "workType",
    "work_type",
)


def looks_like_url(value: str) -> bool:
    return bool(re.match(r"^https?://", value or "", flags=re.I))


def encode_url_for_request(url: str) -> str:
    """Encode proprement une URL image avant urllib.

    Certaines URL de couverture renvoyées par Komga/Komf/providers contiennent
    des espaces, des caractères Unicode, des crochets, des esperluettes HTML
    ou d'autres caractères spéciaux dans le chemin. urllib est beaucoup moins
    tolérant qu'un navigateur : on normalise donc l'URL sans casser les
    séparateurs de query string.
    """
    text = html.unescape((url or "").strip())
    if not text:
        return ""
    if text.startswith("//"):
        text = "https:" + text
    if not looks_like_url(text):
        return text

    try:
        parts = parse.urlsplit(text)
        path = parse.quote(parse.unquote(parts.path), safe="/:@%+")
        query = parse.quote(parse.unquote(parts.query), safe="=&?/:;+,%@")
        fragment = parse.quote(parse.unquote(parts.fragment), safe="=&?/:;+,%@")
        return parse.urlunsplit((parts.scheme, parts.netloc, path, query, fragment))
    except Exception:
        return text


def is_probable_image_url(value: str) -> bool:
    if not looks_like_url(value):
        return False
    lower = value.lower()
    return (
        any(ext in lower for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"))
        or any(token in lower for token in ("cover", "thumbnail", "image", "img", "cdn", "media"))
    )


def _iter_nested_values(data: Any, parent_key: str = "") -> Iterable[Tuple[str, Any]]:
    if isinstance(data, dict):
        for key, value in data.items():
            key_text = safe_str(key)
            yield key_text, value
            yield from _iter_nested_values(value, key_text)
    elif isinstance(data, list):
        for item in data:
            yield from _iter_nested_values(item, parent_key)


def extract_cover_url(data: Any) -> str:
    """Essaie de trouver une URL de couverture dans les réponses Komga/Komf.

    Les providers Komf ne renvoient pas tous les mêmes clés. On privilégie
    les champs explicitement liés à une image/couverture et on évite de
    confondre avec l'URL de la fiche web du provider.
    """
    if not isinstance(data, (dict, list)):
        return ""

    def from_value(value: Any, key_hint: str = "") -> str:
        if isinstance(value, str):
            if looks_like_url(value) and (any(k in key_hint.lower() for k in IMAGE_KEYWORDS) or is_probable_image_url(value)):
                return value
            return ""
        if isinstance(value, dict):
            for subkey in ("url", "href", "src", "imageUrl", "thumbnailUrl", "coverUrl"):
                candidate = value.get(subkey)
                if isinstance(candidate, str) and looks_like_url(candidate):
                    return candidate
            return extract_cover_url(value)
        if isinstance(value, list):
            for item in value:
                candidate = from_value(item, key_hint)
                if candidate:
                    return candidate
        return ""

    if isinstance(data, dict):
        for key in (
            "thumbnailUrl",
            "thumbnail_url",
            "coverUrl",
            "cover_url",
            "imageUrl",
            "image_url",
            "posterUrl",
            "poster_url",
            "thumbnail",
            "cover",
            "image",
            "poster",
        ):
            if key in data:
                candidate = from_value(data.get(key), key)
                if candidate:
                    return candidate

    for key, value in _iter_nested_values(data):
        lower = key.lower()
        if any(token in lower for token in IMAGE_KEYWORDS):
            candidate = from_value(value, key)
            if candidate:
                return candidate

    for _key, value in _iter_nested_values(data):
        if isinstance(value, str) and is_probable_image_url(value):
            return value

    return ""


def extract_volume_count(data: Any) -> str:
    """Extrait un nombre de tomes/livres si le provider le fournit."""
    if not isinstance(data, (dict, list)):
        return ""

    def normalize_count(value: Any) -> str:
        if value is None or isinstance(value, bool):
            return ""
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return ""
            if re.fullmatch(r"\d+(?:\.\d+)?", text):
                return str(int(float(text)))
            return text
        if isinstance(value, list):
            return str(len(value)) if value else ""
        return ""

    dicts: List[Dict[str, Any]] = []
    if isinstance(data, dict):
        dicts.append(data)
        meta = data.get("metadata")
        if isinstance(meta, dict):
            dicts.append(meta)

    for dct in dicts:
        for key in COUNT_KEYS:
            if key in dct:
                count = normalize_count(dct.get(key))
                if count:
                    return count

    lower_keys = {k.lower() for k in COUNT_KEYS}
    for key, value in _iter_nested_values(data):
        if key in COUNT_KEYS or key.lower() in lower_keys:
            count = normalize_count(value)
            if count:
                return count

    return ""



def is_provider_page_url(value: str) -> bool:
    """URL de fiche provider, pas une image/couverture."""
    if not looks_like_url(value):
        return False
    return not is_probable_image_url(value)


def extract_provider_url(data: Any) -> str:
    """Extrait l'URL cliquable de la fiche provider si Komf la fournit."""
    if not isinstance(data, (dict, list)):
        return ""

    def from_value(value: Any, key_hint: str = "") -> str:
        lower_hint = key_hint.lower()
        if any(token in lower_hint for token in IMAGE_KEYWORDS):
            return ""
        if isinstance(value, str):
            return value if is_provider_page_url(value) else ""
        if isinstance(value, dict):
            for subkey in URL_KEYS:
                candidate = value.get(subkey)
                if isinstance(candidate, str) and is_provider_page_url(candidate):
                    return candidate
            return extract_provider_url(value)
        if isinstance(value, list):
            for item in value:
                candidate = from_value(item, key_hint)
                if candidate:
                    return candidate
        return ""

    if isinstance(data, dict):
        # Priorité aux champs directs : ce sont généralement les liens affichés par Komf.
        for key in URL_KEYS:
            if key in data:
                candidate = from_value(data.get(key), key)
                if candidate:
                    return candidate
        metadata = data.get("metadata")
        if isinstance(metadata, dict):
            for key in URL_KEYS:
                if key in metadata:
                    candidate = from_value(metadata.get(key), key)
                    if candidate:
                        return candidate

    for key, value in _iter_nested_values(data):
        lower = key.lower()
        if any(token in lower for token in ("url", "href", "link")) and not any(img in lower for img in IMAGE_KEYWORDS):
            candidate = from_value(value, key)
            if candidate:
                return candidate
    return ""


def normalize_media_label(value: Any) -> str:
    text = safe_str(value).strip()
    if not text:
        return ""
    folded = fold_text(text).replace("_", " ").replace("-", " ")
    folded = re.sub(r"\s+", " ", folded)

    # Ordre volontaire : "light novel" doit passer avant "novel".
    if "light novel" in folded or "lightnovel" in folded:
        return "Light Novel"
    if re.search(r"\bln\b", folded):
        return "Light Novel"
    if "web novel" in folded or "webnovel" in folded:
        return "Web Novel"
    if "novel" in folded or folded in {"novel", "book"}:
        return "Novel"
    if "manga" in folded:
        return "Manga"
    if "manhwa" in folded:
        return "Manhwa"
    if "manhua" in folded:
        return "Manhua"
    if "webtoon" in folded:
        return "Webtoon"
    if "comic" in folded:
        return "Comic"
    return ""


def extract_media_type(data: Any, title: str = "") -> str:
    """Détecte rapidement si le résultat est manga, light novel, novel, etc."""
    for candidate in (title,):
        label = normalize_media_label(candidate)
        if label:
            return label

    if isinstance(data, dict):
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        for source in (data, metadata):
            for key in MEDIA_KEYS:
                if key in source:
                    label = normalize_media_label(source.get(key))
                    if label:
                        return label

            # Certains providers placent le type dans les tags/genres/categories.
            for key in ("tags", "genres", "categories", "subjects"):
                value = source.get(key)
                if isinstance(value, (list, tuple, set)):
                    for entry in value:
                        label = normalize_media_label(entry)
                        if label:
                            return label
                else:
                    label = normalize_media_label(value)
                    if label:
                        return label

    for key, value in _iter_nested_values(data):
        if key in MEDIA_KEYS or key.lower() in {k.lower() for k in MEDIA_KEYS}:
            label = normalize_media_label(value)
            if label:
                return label

    return ""


def display_volume_count(value: str) -> str:
    """Affichage compact : pas de 'non fourni' partout dans la grille."""
    return value.strip() if value and value.strip() else "—"

def fetch_image_bytes(url: str, api_key: str = "", timeout: int = 15) -> bytes:
    headers = {
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "User-Agent": f"komf-lightnovel-queue-ui/{APP_VERSION}",
    }
    if api_key:
        headers["X-API-Key"] = api_key
    req = request.Request(encode_url_for_request(url), headers=headers, method="GET")
    with request.urlopen(req, timeout=timeout) as resp:
        return resp.read(8 * 1024 * 1024)


@dataclass
class LibraryItem:
    id: str
    name: str
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SeriesItem:
    id: str
    name: str
    library_id: str
    book_count: str = ""
    summary: str = ""
    publisher: str = ""
    links: List[Any] = field(default_factory=list)
    cover_url: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class KomfResult:
    provider: str
    provider_series_id: str
    title: str
    details: str
    volume_count: str = ""
    media_type: str = ""
    provider_url: str = ""
    cover_url: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class KomfSearchOutput:
    results: List[KomfResult]
    raw: Any
    searched_url_hint: str
    query: str
    library_id: str = ""
    attempted_queries: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

class ApplyJob:
    job_id: int
    series: SeriesItem
    result: KomfResult
    payload: Dict[str, str]
    status: str = "en attente"
    detail: str = "En attente de traitement Komf"
    response: Any = None
    error_trace: str = ""


PROVIDER_KEY_MAP = {
    "mangaupdates": "MANGA_UPDATES",
    "manga_updates": "MANGA_UPDATES",
    "manga updates": "MANGA_UPDATES",
    "mal": "MAL",
    "myanimelist": "MAL",
    "my_anime_list": "MAL",
    "nautiljon": "NAUTILJON",
    "anilist": "ANILIST",
    "ani_list": "ANILIST",
    "ani list": "ANILIST",
    "yenpress": "YEN_PRESS",
    "yen_press": "YEN_PRESS",
    "yen press": "YEN_PRESS",
    "bookwalker": "BOOK_WALKER",
    "book_walker": "BOOK_WALKER",
    "book walker": "BOOK_WALKER",
    "mangadex": "MANGA_DEX",
    "manga_dex": "MANGA_DEX",
    "manga dex": "MANGA_DEX",
    "bangumi": "BANGUMI",
    "webtoons": "WEBTOONS",
    "webtoon": "WEBTOONS",
    "mangabaka": "MANGA_BAKA",
    "manga_baka": "MANGA_BAKA",
    "manga baka": "MANGA_BAKA",
    "comicvine": "COMIC_VINE",
    "comic_vine": "COMIC_VINE",
    "comic vine": "COMIC_VINE",
    "kodansha": "KODANSHA",
    "viz": "VIZ",
    "hentag": "HENTAG",
}


def normalize_provider(provider: Any) -> str:
    raw = safe_str(provider).strip()
    if not raw:
        return ""
    key = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    if key in PROVIDER_KEY_MAP:
        return PROVIDER_KEY_MAP[key]
    compact = key.replace("_", "")
    if compact in PROVIDER_KEY_MAP:
        return PROVIDER_KEY_MAP[compact]
    return raw.upper()


def extract_libraries(data: Any) -> List[LibraryItem]:
    items = data if isinstance(data, list) else data.get("content", []) if isinstance(data, dict) else []
    libraries: List[LibraryItem] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        lib_id = safe_str(item.get("id"))
        name = safe_str(item.get("name") or item.get("title") or lib_id)
        if lib_id:
            libraries.append(LibraryItem(id=lib_id, name=name, raw=item))
    return libraries


def extract_series(data: Any, library_filter: Optional[str] = None, strict_library: bool = False) -> List[SeriesItem]:
    if isinstance(data, dict):
        items = data.get("content") or data.get("data") or data.get("items") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []

    series: List[SeriesItem] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        sid = safe_str(item.get("id"))
        lib_obj = item.get("library") if isinstance(item.get("library"), dict) else {}
        # Ne pas accepter silencieusement des séries sans libraryId quand on veut
        # charger une bibliothèque précise : sinon un filtre ignoré côté API peut
        # afficher toute l'instance Komga.
        lib_id = safe_str(item.get("libraryId") or item.get("library_id") or lib_obj.get("id") or "")
        if library_filter:
            if lib_id:
                if lib_id != library_filter:
                    continue
            elif strict_library:
                continue
        name = safe_str(
            metadata.get("title")
            or item.get("name")
            or item.get("title")
            or metadata.get("titleSort")
            or sid
        )
        publisher = safe_str(metadata.get("publisher") or metadata.get("publisherName"))
        links = metadata.get("links") or metadata.get("link") or []
        if isinstance(links, dict):
            links = [links]
        book_count = safe_str(item.get("bookCount") or item.get("booksCount") or item.get("numberOfBooks") or "")
        summary = safe_str(metadata.get("summary"))
        if sid:
            series.append(
                SeriesItem(
                    id=sid,
                    name=name,
                    library_id=lib_id or (library_filter or ""),
                    book_count=book_count,
                    summary=summary,
                    publisher=publisher,
                    links=links if isinstance(links, list) else [],
                    cover_url=extract_cover_url(item),
                    raw=item,
                )
            )
    series.sort(key=lambda s: s.name.lower())
    return series


def is_audio_result_title(title: str) -> bool:
    """Résultat provider à masquer : audio explicitement indiqué entre parenthèses."""
    return bool(re.search(r"\(\s*audio\s*\)", title or "", flags=re.I))


def _iter_possible_result_dicts(data: Any, parent_provider: Optional[str] = None) -> Iterable[Tuple[Dict[str, Any], Optional[str]]]:
    if isinstance(data, list):
        for item in data:
            yield from _iter_possible_result_dicts(item, parent_provider)
        return

    if not isinstance(data, dict):
        return

    provider_here = data.get("provider") or data.get("metadataProvider") or data.get("source") or parent_provider

    # Cas racine classique : {"results": [...]} / {"data": [...]} / {"content": [...]}
    for key in ("results", "data", "content", "items", "matches", "metadata"):
        value = data.get(key)
        if isinstance(value, (list, dict)):
            yield from _iter_possible_result_dicts(value, provider_here)

    # Cas provider -> liste de résultats : {"MANGA_UPDATES": [...], "NAUTILJON": [...]}
    for key, value in data.items():
        if key in {"results", "data", "content", "items", "matches", "metadata"}:
            continue
        if isinstance(value, (list, dict)):
            mapped = normalize_provider(key)
            if mapped and mapped != key.upper() or key.lower() in PROVIDER_KEY_MAP:
                yield from _iter_possible_result_dicts(value, mapped)

    # Cas objet résultat.
    possible_id = (
        data.get("providerSeriesId")
        or data.get("resultId")
        or data.get("seriesId")
        or data.get("metadataId")
        or data.get("externalId")
        or data.get("id")
        or data.get("slug")
    )
    possible_title = data.get("title") or data.get("name")
    nested_metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    possible_title = possible_title or nested_metadata.get("title") or nested_metadata.get("name")
    if possible_id is not None and possible_title is not None:
        yield data, safe_str(provider_here) if provider_here else None


def parse_komf_results(data: Any) -> List[KomfResult]:
    results: List[KomfResult] = []
    seen: set[Tuple[str, str, str]] = set()
    for item, parent_provider in _iter_possible_result_dicts(data):
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        provider = normalize_provider(item.get("provider") or item.get("metadataProvider") or item.get("source") or parent_provider)
        provider_series_id = safe_str(
            item.get("providerSeriesId")
            or item.get("resultId")
            or item.get("seriesId")
            or item.get("metadataId")
            or item.get("externalId")
            or item.get("id")
            or item.get("slug")
        )
        title = safe_str(item.get("title") or item.get("name") or metadata.get("title") or metadata.get("name"))
        if not provider or not provider_series_id or not title:
            continue
        if is_audio_result_title(title):
            continue
        bits = []
        for key in ("type", "mediaType", "status", "year", "releaseYear", "publisher", "url"):
            value = item.get(key) if key in item else metadata.get(key)
            if truthy_nonempty(value):
                bits.append(f"{key}: {safe_str(value)}")
        if metadata:
            for key in ("status", "publisher", "releaseDate", "totalBookCount"):
                value = metadata.get(key)
                if truthy_nonempty(value) and not any(b.startswith(f"{key}:") for b in bits):
                    bits.append(f"{key}: {safe_str(value)}")
        details = " | ".join(bits)
        dedupe = (provider, provider_series_id, title)
        if dedupe in seen:
            continue
        seen.add(dedupe)
        results.append(
            KomfResult(
                provider=provider,
                provider_series_id=provider_series_id,
                title=title,
                details=details,
                volume_count=extract_volume_count(item),
                media_type=extract_media_type(item, title),
                provider_url=extract_provider_url(item),
                cover_url=extract_cover_url(item),
                raw=item,
            )
        )
    return results


def provider_for_identify(provider: str) -> str:
    """Provider enum attendu par Komf côté /identify."""
    normalized = normalize_provider(provider)
    aliases = {
        "ANI_LIST": "ANILIST",
        "ANILIST": "ANILIST",
    }
    return aliases.get(normalized, normalized)


class KomgaApi:
    def __init__(self, url: str, api_key: str):
        self.client = ApiClient(url, api_key=api_key)

    def test(self) -> str:
        data = self.client.get("/api/v1/libraries")
        libraries = extract_libraries(data)
        return f"Komga OK — {len(libraries)} bibliothèque(s) accessibles"

    def libraries(self) -> List[LibraryItem]:
        return extract_libraries(self.client.get("/api/v1/libraries"))

    def series(self, library_id: str) -> List[SeriesItem]:
        if not library_id:
            return []

        last_error: Optional[Exception] = None
        attempts: List[str] = []

        def keep_only_selected(data: Any, source: str, strict: bool = True) -> Optional[List[SeriesItem]]:
            items = extract_series(data, library_filter=library_id, strict_library=strict)
            attempts.append(f"{source}: {len(items)} série(s) gardée(s)")
            if items:
                for item in items:
                    item.library_id = library_id
                return items
            return None

        # 1) Endpoint scoped par bibliothèque, si disponible sur l'instance.
        # Comme le chemin contient libraryId, on accepte aussi les réponses qui
        # ne répètent pas libraryId dans chaque série.
        scoped_paths = (
            f"/api/v1/libraries/{parse.quote(library_id)}/series",
            f"/api/v1/library/{parse.quote(library_id)}/series",
        )
        for path in scoped_paths:
            try:
                data = self.client.get(path, query={"unpaged": "true", "sort": "metadata.titleSort,asc"})
                selected = keep_only_selected(data, f"GET {path}", strict=False)
                if selected is not None:
                    return selected
            except Exception as exc:
                last_error = exc
                attempts.append(f"GET {path}: échec")

        # 2) Endpoint actuel : POST /api/v1/series/list.
        # On reste strict : si la réponse ne contient pas de libraryId exploitable,
        # on ne l'accepte pas. C'est volontaire pour ne jamais afficher toute
        # l'instance quand une seule bibliothèque est sélectionnée.
        post_bodies = [
            {"libraryIds": [library_id]},
            {"libraryId": library_id},
            {"library_ids": [library_id]},
            {"library_id": library_id},
            {"library_id": [library_id]},
            {"libraries": [library_id]},
            {"condition": {"libraryIds": [library_id]}},
            {"filters": {"libraryIds": [library_id]}},
        ]
        for body in post_bodies:
            try:
                data = self.client.post(
                    "/api/v1/series/list",
                    body=body,
                    query={"unpaged": "true", "sort": "metadata.titleSort,asc"},
                )
                selected = keep_only_selected(data, f"POST /api/v1/series/list body={body!r}", strict=True)
                if selected is not None:
                    return selected
            except Exception as exc:
                last_error = exc
                attempts.append(f"POST /api/v1/series/list body={body!r}: échec")

        # 3) Fallback GET déprécié. Strict aussi : s'il ignore le filtre,
        # on refuse de remplir l'UI avec toutes les séries.
        get_queries = (
            {"library_id": library_id, "unpaged": "true", "sort": "metadata.titleSort,asc"},
            {"libraryId": library_id, "unpaged": "true", "sort": "metadata.titleSort,asc"},
        )
        for query in get_queries:
            try:
                data = self.client.get("/api/v1/series", query=query)
                selected = keep_only_selected(data, f"GET /api/v1/series query={query!r}", strict=True)
                if selected is not None:
                    return selected
            except Exception as exc:
                last_error = exc
                attempts.append(f"GET /api/v1/series query={query!r}: échec")

        details = "\n".join(attempts[-20:]) or "aucune tentative exploitable"
        if last_error:
            raise RuntimeError(
                "Impossible de charger uniquement la bibliothèque sélectionnée. "
                "J'ai refusé de retourner la liste complète de Komga.\n\n"
                f"libraryId demandé : {library_id}\n"
                f"Tentatives :\n{details}\n\n"
                f"Dernière erreur : {last_error}"
            )
        raise RuntimeError(
            "Impossible de charger uniquement la bibliothèque sélectionnée. "
            "Aucune réponse exploitable avec libraryId vérifiable.\n\n"
            f"libraryId demandé : {library_id}\nTentatives :\n{details}"
        )


class KomfApi:
    """Client Komf robuste.

    Selon la version de Komf et l'extension utilisée, les endpoints peuvent être
    exposés en /komga/..., /metadata/... ou /api/... . L'outil teste ces variantes
    pour chercher, identifier manuellement et lancer l'auto-identify par série.
    """

    SEARCH_PATHS = (
        # Chez ton installation, l'endpoint /komga/search est celui qui répond.
        # Les variantes /metadata/* restent en fallback, mais ne doivent plus
        # bloquer l'UI ni passer avant le chemin fonctionnel.
        "/komga/search",
        "/metadata/search",
        "/api/komga/metadata/search",
        "/api/metadata/search",
    )
    IDENTIFY_PATHS = (
        "/komga/identify",
        "/metadata/identify",
        "/api/komga/metadata/identify",
        "/api/metadata/identify",
    )
    MATCH_SERIES_PATHS = (
        "/komga/match/library/{library_id}/series/{series_id}",
        "/metadata/match/library/{library_id}/series/{series_id}",
        "/api/komga/match/library/{library_id}/series/{series_id}",
        "/api/metadata/match/library/{library_id}/series/{series_id}",
    )
    PROVIDER_PATHS = (
        "/komga/providers",
        "/metadata/providers",
        "/api/komga/metadata/providers",
        "/api/metadata/providers",
    )

    def __init__(self, url: str):
        # Timeout court uniquement pour Komf : si un endpoint fallback ne répond pas,
        # on passe vite au suivant au lieu d'attendre 30 secondes.
        self.client = ApiClient(url, timeout=KOMF_FAST_TIMEOUT_SECONDS)

    def _url_hint(self, path: str, query: Optional[Dict[str, Any]] = None) -> str:
        hint = self.client.base_url + path
        if query:
            clean_query = {k: v for k, v in query.items() if v is not None and v != ""}
            if clean_query:
                hint += "?" + parse.urlencode(clean_query, doseq=True)
        return hint

    def _get_first_working(self, paths: Iterable[str], query: Optional[Dict[str, Any]] = None) -> Tuple[Any, str]:
        errors: List[str] = []
        for path in paths:
            try:
                data = self.client.get(path, query=query)
                return data, self._url_hint(path, query)
            except HttpError as exc:
                if exc.status in (404, 405) or exc.status is None:
                    errors.append(f"{path}: HTTP {exc.status or 'timeout/connexion'} — {exc.body}")
                    continue
                raise
        raise RuntimeError("Aucun endpoint Komf compatible n'a répondu. Tentatives : " + "; ".join(errors))

    def test(self) -> str:
        data, hint = self._get_first_working(self.PROVIDER_PATHS)
        count = len(data) if isinstance(data, list) else len(data.keys()) if isinstance(data, dict) else 1
        return f"Komf OK — providers visibles : {count} ({hint})"

    def _search_queries(self, name: str, library_id: Optional[str], series_id: Optional[str]) -> List[Tuple[str, Dict[str, Any]]]:
        """Construit des variantes de contexte Komf pour survivre aux providers cassés.

        Le premier essai garde le comportement historique : name + libraryId + seriesId.
        Si Komf plante côté provider (cas AniList 403 remonté en HTTP 500), on retente
        automatiquement avec moins de contexte. Cela évite qu'un provider externe
        indisponible bloque toute la recherche manuelle.
        """
        attempts: List[Tuple[str, Dict[str, Any]]] = []

        def add(label: str, query: Dict[str, Any]) -> None:
            clean = {k: v for k, v in query.items() if v is not None and v != ""}
            signature = tuple(sorted(clean.items()))
            if any(tuple(sorted(existing.items())) == signature for _label, existing in attempts):
                return
            attempts.append((label, clean))

        full_query: Dict[str, Any] = {"name": name}
        if library_id:
            full_query["libraryId"] = library_id
        if series_id:
            full_query["seriesId"] = series_id
        add("contexte complet", full_query)

        if library_id and series_id:
            add("sans seriesId", {"name": name, "libraryId": library_id})
        if series_id:
            add("recherche globale", {"name": name})
        elif library_id:
            add("recherche globale", {"name": name})

        return attempts

    def _search_one(self, name: str, library_id: Optional[str] = None, series_id: Optional[str] = None) -> Tuple[Any, str, List[KomfResult]]:
        errors: List[str] = []
        last_data: Any = None
        last_hint = ""
        last_error_payload: Optional[Dict[str, Any]] = None
        query_attempts = self._search_queries(name, library_id, series_id)

        for path in self.SEARCH_PATHS:
            endpoint_missing = False
            for query_label, query in query_attempts:
                try:
                    data = self.client.get(path, query=query)
                    hint = self._url_hint(path, query)
                    parsed = parse_komf_results(data)
                    last_data = data
                    last_hint = hint
                    if parsed:
                        if errors:
                            hint += "\nEssais précédents : " + " | ".join(errors[-8:])
                        return data, hint, parsed
                    errors.append(f"{path} [{query_label}]: 0 résultat parsé")
                except HttpError as exc:
                    if exc.status in (404, 405) or exc.status is None:
                        errors.append(
                            f"{path} [{query_label}]: HTTP {exc.status or 'timeout/connexion'} — "
                            f"{compact_error_text(exc.body)}"
                        )
                        endpoint_missing = True
                        break
                    if is_recoverable_komf_search_error(exc):
                        payload = komf_search_error_payload(path, query, exc)
                        last_error_payload = payload
                        last_data = payload
                        last_hint = self._url_hint(path, query)
                        errors.append(
                            f"{path} [{query_label}]: HTTP {exc.status or '?'} récupérable — "
                            f"{compact_error_text(exc.body)}"
                        )
                        continue
                    raise
            if endpoint_missing:
                continue

        if last_hint:
            suffix = "\nEndpoints/variantes essayés : " + " | ".join(errors[-20:]) if errors else ""
            return last_data, last_hint + suffix, []
        if last_error_payload is not None:
            return last_error_payload, "Endpoints/variantes essayés : " + " | ".join(errors[-20:]), []
        raise RuntimeError("Aucun endpoint de recherche Komf compatible. Tentatives : " + " | ".join(errors))

    def search_raw(
        self,
        name: str,
        library_id: Optional[str] = None,
        use_variants: bool = True,
        series_id: Optional[str] = None,
    ) -> KomfSearchOutput:
        queries = build_search_variants(name) if use_variants else unique_nonempty([name])
        all_results: List[KomfResult] = []
        raw_by_query: List[Dict[str, Any]] = []
        hints: List[str] = []
        warnings: List[str] = []
        seen: set[Tuple[str, str, str]] = set()

        for q in queries:
            data, hint, parsed = self._search_one(q, library_id=library_id, series_id=series_id)
            hints.append(hint)
            attempt: Dict[str, Any] = {
                "query": q,
                "url": hint,
                "result_count_after_parsing": len(parsed),
                "raw": data,
            }
            if isinstance(data, dict) and data.get("error") == "komf_search_failed":
                summary = safe_str(data.get("summary"))
                if summary:
                    warnings.append(f"{q}: {summary}")
                attempt["warning"] = summary
            raw_by_query.append(attempt)
            for result in parsed:
                key = (result.provider, result.provider_series_id, result.title)
                if key in seen:
                    continue
                seen.add(key)
                all_results.append(result)
            if len(all_results) >= 50:
                break

        raw = {"attempted_queries": queries, "attempts": raw_by_query, "warnings": warnings}
        return KomfSearchOutput(
            results=all_results,
            raw=raw,
            searched_url_hint="\n".join(hints),
            query=name,
            library_id=library_id or "",
            attempted_queries=queries,
            warnings=warnings,
        )

    def search(self, name: str, library_id: Optional[str] = None) -> List[KomfResult]:
        return self.search_raw(name, library_id=library_id).results

    def identify(self, library_id: str, series_id: str, provider: str, provider_series_id: str) -> Any:
        payload = {
            "libraryId": library_id,
            "seriesId": series_id,
            "provider": provider_for_identify(provider),
            "providerSeriesId": provider_series_id,
        }
        errors: List[str] = []
        for path in self.IDENTIFY_PATHS:
            try:
                return self.client.post(path, body=payload)
            except HttpError as exc:
                if exc.status in (404, 405) or exc.status is None:
                    errors.append(f"{path}: HTTP {exc.status or 'timeout/connexion'} — {exc.body}")
                    continue
                raise
        raise RuntimeError("Aucun endpoint d'identification Komf compatible. Tentatives : " + " | ".join(errors))

    def match_series(self, library_id: str, series_id: str) -> Dict[str, Any]:
        """Lance l'auto-identify Komf pour une seule série.

        Contrairement à /identify, cet endpoint ne reçoit pas de provider explicite :
        Komf utilise l'ordre et les providers configurés pour la bibliothèque.
        On appelle uniquement la variante par série, jamais le matching global de bibliothèque.

        Important : cet appel peut être lent. Komf interroge les providers
        configurés et peut mettre bien plus de 10 secondes sur une série.
        On garde donc le timeout court pour les tests/recherches, mais on donne
        un timeout dédié beaucoup plus long au matching réel.
        """
        library_id = safe_str(library_id).strip()
        series_id = safe_str(series_id).strip()
        if not library_id or not series_id:
            raise ValueError("libraryId et seriesId sont obligatoires pour l'auto identify")

        quoted_library = parse.quote(library_id, safe="")
        quoted_series = parse.quote(series_id, safe="")
        errors: List[str] = []
        timeout_errors: List[str] = []
        for template in self.MATCH_SERIES_PATHS:
            path = template.format(library_id=quoted_library, series_id=quoted_series)
            try:
                response = self.client.post(path, timeout=KOMF_MATCH_TIMEOUT_SECONDS)
                return {
                    "url": self._url_hint(path),
                    "response": response,
                    "timeoutSeconds": KOMF_MATCH_TIMEOUT_SECONDS,
                }
            except HttpError as exc:
                label = f"{path}: HTTP {exc.status or 'timeout/connexion'} — {exc.body}"
                if exc.status is None:
                    timeout_errors.append(label)
                    errors.append(label)
                    if template == self.MATCH_SERIES_PATHS[0]:
                        raise RuntimeError(
                            "L'endpoint Auto Identify Komf par série semble être le bon, "
                            f"mais il n'a pas terminé avant {KOMF_MATCH_TIMEOUT_SECONDS}s.\n\n"
                            "Endpoint : " + self._url_hint(path) + "\n"
                            "Ce n'est probablement pas un endpoint incompatible : Komf est lent, "
                            "un provider bloque, ou le traitement continue côté serveur après timeout. "
                            "Vérifie les logs Komf/Komga avant de relancer en masse.\n\n"
                            "Erreur brute : " + label
                        ) from exc
                    continue
                if exc.status in (404, 405):
                    errors.append(label)
                    continue
                raise
        if timeout_errors:
            raise RuntimeError(
                "Auto Identify Komf interrompu par timeout. Tentatives : " + " | ".join(errors)
            )
        raise RuntimeError("Aucun endpoint d'auto identify Komf compatible. Tentatives : " + " | ".join(errors))


class ResizableScrollArea(QScrollArea):
    """QScrollArea avec signal de redimensionnement.

    Le QSplitter redimensionne bien la zone de covers, mais un QGridLayout ne
    redistribue pas automatiquement les widgets déjà placés dans de nouvelles
    colonnes. Ce signal permet de relayout la grille sans reconstruire les
    cartes ni recharger les images.
    """

    resized = Signal()

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self.resized.emit()


class MainWindow(QMainWindow):
    def __init__(
        self,
        komga_api_provider: Optional[Callable[[], Any]] = None,
        komf_api_provider: Optional[Callable[[], Any]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_TITLE} v{APP_VERSION}")
        self.resize(1500, 900)
        self.komga_api_provider = komga_api_provider
        self.komf_api_provider = komf_api_provider

        self.thread_pool = QThreadPool.globalInstance()
        # Garder des références explicites aux QRunnable actifs.
        # Sans ça, PySide peut perdre les wrappers Python avant la livraison
        # des signaux result/error, ce qui provoque des blocages silencieux.
        self.active_workers = set()
        self.libraries: List[LibraryItem] = []
        self.series_items: List[SeriesItem] = []
        self.queue_items: List[SeriesItem] = []
        self.current_queue_index: int = -1
        self.current_results: List[KomfResult] = []
        self.selected_result_index: Optional[int] = None
        self.result_cards: List[QFrame] = []
        self.last_simulated_key: Optional[Tuple[str, str, str]] = None
        self.last_komf_raw: Any = None
        self.last_komf_search_hint: str = ""
        # Invalide les recherches Komf encore en cours quand on change de tâche,
        # pour éviter qu'une réponse ancienne remplace les résultats de la série courante.
        self.search_generation: int = 0
        self.image_cache: Dict[str, Optional[bytes]] = {}
        self.apply_job_sequence: int = 0
        self.apply_jobs_pending: List[ApplyJob] = []
        self.apply_jobs_all: List[ApplyJob] = []
        self.apply_job_running: Optional[ApplyJob] = None
        self.apply_jobs_done: int = 0
        self.apply_jobs_failed: int = 0
        self.auto_identify_running: bool = False
        self.series_cover_selected_ids: set[str] = set()
        # Cartes de covers : sélection verrouillée par ID stable, pas par index.
        # Les maps permettent aussi de ne mettre à jour que les cartes impactées.
        self.series_card_widgets_by_id: Dict[str, QFrame] = {}
        self.series_cover_labels_by_id: Dict[str, QLabel] = {}
        self.series_cover_urls_by_id: Dict[str, List[str]] = {}
        self.series_cover_requested_ids: set[str] = set()
        self.series_card_visible_ids: List[str] = []
        self.series_cards_current_columns: int = 0
        self.series_cards_dirty: bool = True

        self._build_ui()
        if self.komga_api_provider or self.komf_api_provider:
            self._configure_shared_connection_ui()
        self._update_apply_button_state()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)

        self.tabs = QTabWidget()
        main.addWidget(self.tabs, 1)

        self._build_connection_tab()
        self._build_series_tab()
        self._build_processing_tab()
        self._build_apply_queue_tab()
        self._build_log_tab()

    def _build_connection_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        box = QGroupBox("Connexion")
        form = QFormLayout(box)
        self.komga_url = QLineEdit(DEFAULT_KOMGA_URL)
        self.komf_url = QLineEdit(DEFAULT_KOMF_URL)
        self.komga_api_key = QLineEdit()
        self.komga_api_key.setEchoMode(QLineEdit.Password)
        self.save_api_key = QCheckBox("Clé API conservée uniquement pour la session")
        self.save_api_key.setChecked(False)
        self.save_api_key.setEnabled(False)
        form.addRow("Komga URL", self.komga_url)
        form.addRow("Komf URL", self.komf_url)
        form.addRow("Komga API key", self.komga_api_key)
        form.addRow("", self.save_api_key)
        layout.addWidget(box)

        row = QHBoxLayout()
        self.btn_test_komga = QPushButton("Tester Komga")
        self.btn_test_komf = QPushButton("Tester Komf")
        self.btn_load_libraries = QPushButton("Charger bibliothèques")
        self.btn_save_config = QPushButton("Paramètres actifs pour la session")
        row.addWidget(self.btn_test_komga)
        row.addWidget(self.btn_test_komf)
        row.addWidget(self.btn_load_libraries)
        row.addWidget(self.btn_save_config)
        row.addStretch(1)
        layout.addLayout(row)

        info = QLabel(
            "Mode prévu : Light novels, traitement manuel ou Auto Identify v2. "
            "Le mode manuel appelle /komga/identify sur le résultat sélectionné ; "
            "le mode auto appelle /komga/match/.../series/... pour chaque série sélectionnée."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        layout.addStretch(1)

        self.btn_test_komga.clicked.connect(self.test_komga)
        self.btn_test_komf.clicked.connect(self.test_komf)
        self.btn_load_libraries.clicked.connect(self.load_libraries)
        self.btn_save_config.clicked.connect(self.save_local_config)

        self.tabs.addTab(tab, "1. Connexion")

    def _build_series_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        top = QHBoxLayout()
        self.library_combo = QComboBox()
        self.btn_load_series = QPushButton("Charger séries de la bibliothèque")
        self.filter_text = QLineEdit()
        self.filter_text.setPlaceholderText("Filtrer par titre...")
        self.filter_missing_summary = QCheckBox("Sans résumé")
        self.filter_missing_publisher = QCheckBox("Sans éditeur")
        self.filter_missing_links = QCheckBox("Sans lien")
        self.filter_weak_metadata = QCheckBox("Metadata faible")
        self.filter_weak_metadata.setToolTip("Affiche les séries sans résumé OU sans éditeur OU sans lien")
        self.btn_apply_filter = QPushButton("Filtrer")
        self.btn_reset_filter = QPushButton("Réinitialiser")
        top.addWidget(QLabel("Bibliothèque"))
        top.addWidget(self.library_combo, 2)
        top.addWidget(self.btn_load_series)
        top.addWidget(self.filter_text, 2)
        top.addWidget(self.filter_missing_summary)
        top.addWidget(self.filter_missing_publisher)
        top.addWidget(self.filter_missing_links)
        top.addWidget(self.filter_weak_metadata)
        top.addWidget(self.btn_apply_filter)
        top.addWidget(self.btn_reset_filter)
        layout.addLayout(top)

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        series_header = QHBoxLayout()
        series_header.addWidget(QLabel("Séries Komga"))
        series_header.addStretch(1)
        series_header.addWidget(QLabel("Vue"))
        self.series_view_mode = QComboBox()
        self.series_view_mode.addItems(["Tableau", "Covers"])
        self.series_view_mode.setToolTip("Permet de sélectionner les séries soit en tableau, soit avec les couvertures Komga.")
        self.series_count_label = QLabel("0 série")
        series_header.addWidget(self.series_view_mode)
        series_header.addWidget(self.series_count_label)
        left_layout.addLayout(series_header)

        self.series_table = QTableWidget(0, 7)
        self.series_table.setHorizontalHeaderLabels(["Titre", "Livres", "Résumé", "Éditeur", "Liens", "État", "ID"])
        self.series_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.series_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.series_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.series_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.series_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        left_layout.addWidget(self.series_table, 1)

        self.series_cards_scroll = ResizableScrollArea()
        self.series_cards_scroll.setWidgetResizable(True)
        self.series_cards_container = QWidget()
        self.series_cards_layout = QGridLayout(self.series_cards_container)
        self.series_cards_layout.setContentsMargins(8, 8, 8, 8)
        self.series_cards_layout.setSpacing(12)
        self.series_cards_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.series_cards_scroll.setWidget(self.series_cards_container)
        self.series_cards_scroll.hide()
        left_layout.addWidget(self.series_cards_scroll, 1)

        self.series_cover_load_timer = QTimer(self)
        self.series_cover_load_timer.setSingleShot(True)
        self.series_cover_load_timer.setInterval(80)
        self.series_cover_load_timer.timeout.connect(self.load_visible_series_covers)
        self.series_cards_scroll.verticalScrollBar().valueChanged.connect(lambda *_: self.schedule_visible_series_cover_loads())

        self.series_cards_reflow_timer = QTimer(self)
        self.series_cards_reflow_timer.setSingleShot(True)
        self.series_cards_reflow_timer.setInterval(60)
        self.series_cards_reflow_timer.timeout.connect(lambda: self.reflow_series_cards_if_needed(force=False))
        self.series_cards_scroll.resized.connect(self.schedule_series_cards_reflow)

        self.series_filter_timer = QTimer(self)
        self.series_filter_timer.setSingleShot(True)
        self.series_filter_timer.setInterval(180)
        self.series_filter_timer.timeout.connect(self.populate_series_table)

        left_buttons = QHBoxLayout()
        self.btn_add_selected = QPushButton("Ajouter sélection à la file")
        self.btn_add_all_visible = QPushButton("Ajouter visibles à la file")
        self.btn_auto_identify_selected_series = QPushButton("Auto identify sélection")
        self.btn_auto_identify_visible_series = QPushButton("Auto identify visibles")
        self.btn_clear_series_selection = QPushButton("Désélectionner tout")
        self.btn_auto_identify_selected_series.setToolTip("Lance l'auto-identify Komf sur les lignes sélectionnées, une série à la fois.")
        self.btn_auto_identify_visible_series.setToolTip("Lance l'auto-identify Komf sur toutes les lignes visibles après filtre.")
        self.btn_clear_series_selection.setToolTip("Vide explicitement la sélection des covers et du tableau des séries.")
        left_buttons.addWidget(self.btn_add_selected)
        left_buttons.addWidget(self.btn_add_all_visible)
        left_buttons.addWidget(self.btn_auto_identify_selected_series)
        left_buttons.addWidget(self.btn_auto_identify_visible_series)
        left_buttons.addWidget(self.btn_clear_series_selection)
        left_buttons.addStretch(1)
        left_layout.addLayout(left_buttons)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("File d'attente"))
        self.queue_table = QTableWidget(0, 4)
        self.queue_table.setHorizontalHeaderLabels(["#", "Titre", "État", "ID"])
        self.queue_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.queue_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.queue_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        right_layout.addWidget(self.queue_table, 1)
        qbuttons1 = QHBoxLayout()
        self.btn_remove_queue = QPushButton("Retirer")
        self.btn_clear_queue = QPushButton("Vider")
        self.btn_save_queue = QPushButton("Sauver file")
        self.btn_load_queue = QPushButton("Charger file")
        qbuttons1.addWidget(self.btn_remove_queue)
        qbuttons1.addWidget(self.btn_clear_queue)
        qbuttons1.addWidget(self.btn_save_queue)
        qbuttons1.addWidget(self.btn_load_queue)
        right_layout.addLayout(qbuttons1)
        qbuttons2 = QHBoxLayout()
        self.btn_queue_prev = QPushButton("Précédente")
        self.btn_queue_current = QPushButton("Ouvrir sélection")
        self.btn_queue_next = QPushButton("Suivante")
        qbuttons2.addWidget(self.btn_queue_prev)
        qbuttons2.addWidget(self.btn_queue_current)
        qbuttons2.addWidget(self.btn_queue_next)
        right_layout.addLayout(qbuttons2)
        qbuttons3 = QHBoxLayout()
        self.btn_auto_identify_selected_queue = QPushButton("Auto identify sélection file")
        self.btn_auto_identify_all_queue = QPushButton("Auto identify toute la file")
        self.btn_auto_identify_selected_queue.setToolTip("Lance l'auto-identify Komf sur les lignes sélectionnées de la file.")
        self.btn_auto_identify_all_queue.setToolTip("Lance l'auto-identify Komf sur toute la file, une série à la fois.")
        qbuttons3.addWidget(self.btn_auto_identify_selected_queue)
        qbuttons3.addWidget(self.btn_auto_identify_all_queue)
        right_layout.addLayout(qbuttons3)
        splitter.addWidget(right)
        splitter.setSizes([900, 600])
        splitter.splitterMoved.connect(lambda *_: self.schedule_series_cards_reflow())

        self.btn_load_series.clicked.connect(self.load_series)
        self.series_view_mode.currentTextChanged.connect(lambda *_: self.update_series_view_mode())
        self.btn_apply_filter.clicked.connect(self.populate_series_table)
        self.btn_reset_filter.clicked.connect(self.reset_series_filters)
        self.filter_text.returnPressed.connect(self.populate_series_table)
        self.filter_text.textChanged.connect(lambda *_: self.request_populate_series_table())
        for checkbox in (
            self.filter_missing_summary,
            self.filter_missing_publisher,
            self.filter_missing_links,
            self.filter_weak_metadata,
        ):
            checkbox.stateChanged.connect(lambda *_: self.populate_series_table())
        self.btn_add_selected.clicked.connect(self.add_selected_series_to_queue)
        self.btn_add_all_visible.clicked.connect(self.add_all_visible_to_queue)
        self.btn_auto_identify_selected_series.clicked.connect(self.auto_identify_selected_series)
        self.btn_auto_identify_visible_series.clicked.connect(self.auto_identify_visible_series)
        self.btn_clear_series_selection.clicked.connect(self.clear_series_selection)
        self.btn_remove_queue.clicked.connect(self.remove_selected_queue_item)
        self.btn_clear_queue.clicked.connect(self.clear_queue)
        self.btn_save_queue.clicked.connect(self.save_queue)
        self.btn_load_queue.clicked.connect(self.load_queue)
        self.btn_queue_prev.clicked.connect(self.queue_prev)
        self.btn_queue_current.clicked.connect(self.open_selected_queue_item)
        self.btn_queue_next.clicked.connect(self.queue_next)
        self.btn_auto_identify_selected_queue.clicked.connect(self.auto_identify_selected_queue_items)
        self.btn_auto_identify_all_queue.clicked.connect(self.auto_identify_all_queue_items)
        self.queue_table.itemDoubleClicked.connect(lambda *_: self.open_selected_queue_item())

        self.tabs.addTab(tab, "2. Séries / file")

    def _build_processing_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        current_box = QGroupBox("Série courante")
        grid = QGridLayout(current_box)

        self.current_cover = QLabel("Aucune\ncouverture")
        self.current_cover.setAlignment(Qt.AlignCenter)
        self.current_cover.setFixedSize(110, 160)
        self.current_cover.setStyleSheet("border: 1px solid #555; background: #222; color: #aaa;")

        self.current_title = QLabel("Aucune série sélectionnée")
        self.current_title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.current_id = QLabel("")
        self.current_id.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.current_books = QLabel("")
        self.current_books.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.search_query = QLineEdit()
        self.search_query.setPlaceholderText("Titre à chercher dans Komf")
        self.use_library_id_for_search = QCheckBox("Utiliser libraryId")
        self.use_library_id_for_search.setChecked(True)
        self.use_library_id_for_search.setToolTip("Décoche pour tester la recherche Komf sans configuration spécifique de bibliothèque.")
        self.use_search_variants = QCheckBox("Variantes")
        self.use_search_variants.setChecked(True)
        self.use_search_variants.setToolTip("Essaie aussi des titres raccourcis : utile pour les light novels aux titres longs.")
        self.btn_clean_query = QPushButton("Nettoyer titre")
        self.btn_search = QPushButton("Rechercher dans Komf")
        self.btn_show_raw_search = QPushButton("Réponse brute")
        self.btn_show_raw_search.setEnabled(False)

        grid.addWidget(self.current_cover, 0, 0, 5, 1)
        grid.addWidget(QLabel("Titre"), 0, 1)
        grid.addWidget(self.current_title, 0, 2, 1, 5)
        grid.addWidget(QLabel("Tomes Komga"), 1, 1)
        grid.addWidget(self.current_books, 1, 2, 1, 5)
        grid.addWidget(QLabel("IDs"), 2, 1)
        grid.addWidget(self.current_id, 2, 2, 1, 5)
        grid.addWidget(QLabel("Recherche"), 3, 1)
        grid.addWidget(self.search_query, 3, 2)
        grid.addWidget(self.use_library_id_for_search, 3, 3)
        grid.addWidget(self.use_search_variants, 3, 4)
        grid.addWidget(self.btn_clean_query, 3, 5)
        grid.addWidget(self.btn_search, 3, 6)
        grid.addWidget(QLabel("Debug"), 4, 1)
        grid.addWidget(self.btn_show_raw_search, 4, 2, 1, 5)
        layout.addWidget(current_box)

        self.search_status = QLabel("Aucune recherche lancée.")
        self.search_status.setWordWrap(True)
        layout.addWidget(self.search_status)

        result_header = QHBoxLayout()
        result_title = QLabel("Résultats Komf — mode manuel : aucun résultat n'est choisi automatiquement")
        self.result_view_mode = QComboBox()
        self.result_view_mode.addItems(["Grille", "Tableau"])
        self.result_count_label = QLabel("0 résultat")
        result_header.addWidget(result_title)
        result_header.addStretch(1)
        result_header.addWidget(QLabel("Vue"))
        result_header.addWidget(self.result_view_mode)
        result_header.addWidget(self.result_count_label)
        layout.addLayout(result_header)

        results_and_side = QSplitter(Qt.Horizontal)
        layout.addWidget(results_and_side, 1)

        results_area = QWidget()
        results_area_layout = QVBoxLayout(results_area)
        results_area_layout.setContentsMargins(0, 0, 0, 0)

        self.results_grid_scroll = QScrollArea()
        self.results_grid_scroll.setWidgetResizable(True)
        self.results_grid_container = QWidget()
        self.results_grid_layout = QGridLayout(self.results_grid_container)
        self.results_grid_layout.setContentsMargins(8, 8, 8, 8)
        self.results_grid_layout.setSpacing(12)
        self.results_grid_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.results_grid_scroll.setWidget(self.results_grid_container)
        results_area_layout.addWidget(self.results_grid_scroll, 1)

        self.results_table = QTableWidget(0, 7)
        self.results_table.setHorizontalHeaderLabels(["Couverture", "Provider", "Titre", "Type", "Tomes", "Provider Series ID", "Détails"])
        self.results_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.results_table.setSelectionMode(QTableWidget.SingleSelection)
        self.results_table.verticalHeader().setDefaultSectionSize(170)
        self.results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.results_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.results_table.hide()
        self.results_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.results_table.customContextMenuRequested.connect(self.show_results_table_context_menu)
        results_area_layout.addWidget(self.results_table, 1)
        results_and_side.addWidget(results_area)

        side = QGroupBox("Validation / tâche")
        side.setMinimumWidth(380)
        side.setMaximumWidth(520)
        side_layout = QVBoxLayout(side)

        self.processing_queue_label = QLabel("File : aucune série ouverte")
        self.processing_queue_label.setWordWrap(True)
        side_layout.addWidget(self.processing_queue_label)

        nav = QHBoxLayout()
        self.btn_processing_prev = QPushButton("◀ Tâche précédente")
        self.btn_processing_next = QPushButton("Tâche suivante ▶")
        nav.addWidget(self.btn_processing_prev)
        nav.addWidget(self.btn_processing_next)
        side_layout.addLayout(nav)

        self.selected_result_summary = QLabel("Sélection : aucun résultat")
        self.selected_result_summary.setWordWrap(True)
        self.selected_result_summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        side_layout.addWidget(self.selected_result_summary)

        self.apply_queue_status = QLabel("Applications Komf : 0 en attente / 0 en cours / 0 OK / 0 échec")
        self.apply_queue_status.setWordWrap(True)
        self.apply_queue_status.setStyleSheet("color: #cccccc;")
        side_layout.addWidget(self.apply_queue_status)

        side_search = QHBoxLayout()
        self.btn_side_search = QPushButton("Rechercher dans Komf")
        side_search.addWidget(self.btn_side_search)
        side_layout.addLayout(side_search)

        side_validation = QHBoxLayout()
        self.btn_side_simulate = QPushButton("Simuler")
        self.btn_side_apply = QPushButton("Appliquer réellement")
        side_validation.addWidget(self.btn_side_simulate)
        side_validation.addWidget(self.btn_side_apply)
        side_layout.addLayout(side_validation)

        self.operation_output = QTextEdit()
        self.operation_output.setReadOnly(True)
        self.operation_output.setMinimumHeight(260)
        self.operation_output.setPlaceholderText("La simulation et le résultat d'application seront affichés ici.")
        side_layout.addWidget(self.operation_output, 1)

        side_hint = QLabel(
            "Le journal complet reste dans l'onglet Journal. Ici, seuls la simulation, "
            "l'application et la tâche courante sont affichés."
        )
        side_hint.setWordWrap(True)
        side_hint.setStyleSheet("color: #aaaaaa;")
        side_layout.addWidget(side_hint)
        results_and_side.addWidget(side)
        results_and_side.setSizes([1150, 420])

        actions = QHBoxLayout()
        self.simulation_mode = QCheckBox("Mode simulation — aucune écriture")
        self.simulation_mode.setChecked(True)
        self.btn_simulate = QPushButton("Simuler le résultat sélectionné")
        self.btn_apply = QPushButton("Appliquer réellement le résultat sélectionné")
        self.btn_skip = QPushButton("Ignorer cette série")
        actions.addWidget(self.simulation_mode)
        actions.addWidget(self.btn_simulate)
        actions.addWidget(self.btn_apply)
        actions.addWidget(self.btn_skip)
        actions.addStretch(1)
        layout.addLayout(actions)

        warning = QLabel(
            "Sécurité : en mode simulation, aucune écriture n'est faite. "
            "En mode réel manuel, l'application est mise en file sans confirmation et l'UI passe immédiatement à la tâche suivante. "
            "L'Auto Identify v2 appelle uniquement /match/library/{libraryId}/series/{seriesId}, jamais /match/library/{libraryId}."
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)

        self.btn_clean_query.clicked.connect(self.clean_current_query)
        self.btn_search.clicked.connect(self.search_current_series)
        self.btn_side_search.clicked.connect(self.search_current_series)
        self.btn_show_raw_search.clicked.connect(self.show_last_raw_search)
        self.search_query.returnPressed.connect(self.search_current_series)
        self.results_table.itemSelectionChanged.connect(self.on_result_selection_changed)
        self.result_view_mode.currentTextChanged.connect(lambda *_: self.update_results_view_mode())
        self.simulation_mode.toggled.connect(self._update_apply_button_state)
        self.btn_simulate.clicked.connect(self.simulate_selected_result)
        self.btn_side_simulate.clicked.connect(self.simulate_selected_result)
        self.btn_apply.clicked.connect(self.apply_selected_result)
        self.btn_side_apply.clicked.connect(self.apply_selected_result)
        self.btn_skip.clicked.connect(self.skip_current_series)
        self.btn_processing_prev.clicked.connect(self.queue_prev)
        self.btn_processing_next.clicked.connect(self.queue_next)

        self.tabs.addTab(tab, "3. Traitement")

    def _build_apply_queue_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        top = QHBoxLayout()
        top.addWidget(QLabel("Progression de la file d'attente de traitement Komf"))
        top.addStretch(1)
        self.apply_progress_status = QLabel("0 en attente / 0 en cours / 0 OK / 0 échec")
        self.apply_progress_status.setStyleSheet("color: #cccccc;")
        top.addWidget(self.apply_progress_status)
        layout.addLayout(top)

        self.apply_jobs_table = QTableWidget(0, 8)
        self.apply_jobs_table.setHorizontalHeaderLabels([
            "#",
            "Statut",
            "Série Komga",
            "Provider",
            "Résultat choisi",
            "Provider Series ID",
            "Détail",
            "Series ID",
        ])
        self.apply_jobs_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.apply_jobs_table.setSelectionMode(QTableWidget.SingleSelection)
        self.apply_jobs_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.apply_jobs_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.apply_jobs_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.apply_jobs_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.apply_jobs_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.apply_jobs_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.apply_jobs_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.apply_jobs_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.apply_jobs_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeToContents)
        layout.addWidget(self.apply_jobs_table, 1)

        bottom = QHBoxLayout()
        self.btn_refresh_apply_jobs = QPushButton("Rafraîchir")
        self.btn_open_apply_job_series = QPushButton("Ouvrir la série sélectionnée")
        bottom.addWidget(self.btn_refresh_apply_jobs)
        bottom.addWidget(self.btn_open_apply_job_series)
        bottom.addStretch(1)
        layout.addLayout(bottom)

        hint = QLabel(
            "Cet onglet suit les appels Komf /komga/identify mis en file. "
            "Les traitements restent séquentiels : un seul appel Komf est exécuté à la fois."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #aaaaaa;")
        layout.addWidget(hint)

        self.btn_refresh_apply_jobs.clicked.connect(self.populate_apply_jobs_table)
        self.btn_open_apply_job_series.clicked.connect(self.open_selected_apply_job_series)
        self.apply_jobs_table.itemDoubleClicked.connect(lambda *_: self.open_selected_apply_job_series())

        self.tabs.addTab(tab, "4. Traitements Komf")

    def _build_log_tab(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        top = QHBoxLayout()
        top.addWidget(QLabel("Journal d'exécution"))
        top.addStretch(1)
        self.btn_clear_log = QPushButton("Vider le journal")
        top.addWidget(self.btn_clear_log)
        layout.addLayout(top)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(240)
        layout.addWidget(self.log, 1)
        self.btn_clear_log.clicked.connect(self.log.clear)
        self.tabs.addTab(tab, "5. Journal")


    # ---------- Helpers UI ----------

    def log_info(self, message: str) -> None:
        if hasattr(self, "log"):
            known = [self.komga_api_key.text()] if hasattr(self, "komga_api_key") else []
            self.log.append(SecretRedactor.redact(message, known))

    def log_json(self, title: str, data: Any) -> None:
        if hasattr(self, "log"):
            known = [self.komga_api_key.text()] if hasattr(self, "komga_api_key") else []
            payload = f"\n{title}\n{json.dumps(data, ensure_ascii=False, indent=2)}"
            self.log.append(SecretRedactor.redact(payload, known))

    def set_operation_output(self, title: str, body: str = "") -> None:
        if not hasattr(self, "operation_output"):
            return
        text = title.strip()
        if body.strip():
            text += "\n\n" + body.strip()
        self.operation_output.setPlainText(text)

    def format_result_summary(self, result: Optional[KomfResult] = None) -> str:
        s = self.current_series()
        if not s:
            return "Sélection : aucune série ouverte"
        if result is None:
            return "Sélection : aucun résultat"
        lines = [
            f"Série : {s.name}",
            f"Résultat : {result.title}",
            f"Provider : {result.provider}",
            f"Type : {result.media_type or 'non indiqué'}",
            f"Provider ID : {result.provider_series_id}",
        ]
        if result.volume_count and result.volume_count.strip():
            lines.append(f"Tomes provider : {result.volume_count.strip()}")
        if result.provider_url:
            lines.append(f"Lien : {result.provider_url}")
        return "\n".join(lines)

    def update_processing_queue_status(self) -> None:
        if not hasattr(self, "processing_queue_label"):
            return
        total = len(self.queue_items)
        if not total or self.current_queue_index < 0:
            text = "File : aucune série ouverte"
        else:
            text = f"File : tâche {self.current_queue_index + 1} / {total}"
            s = self.current_series()
            if s:
                text += f"\n{s.name}"
        self.processing_queue_label.setText(text)
        if hasattr(self, "btn_processing_prev"):
            self.btn_processing_prev.setEnabled(total > 0 and self.current_queue_index > 0)
        if hasattr(self, "btn_processing_next"):
            self.btn_processing_next.setEnabled(total > 0 and self.current_queue_index < total - 1)

    def update_selected_result_summary(self) -> None:
        if not hasattr(self, "selected_result_summary"):
            return
        self.selected_result_summary.setText(self.format_result_summary(self.selected_result()))

    def apply_job_counts(self) -> Tuple[int, int, int, int]:
        pending = len(getattr(self, "apply_jobs_pending", []))
        running = 1 if getattr(self, "apply_job_running", None) is not None else 0
        done = getattr(self, "apply_jobs_done", 0)
        failed = getattr(self, "apply_jobs_failed", 0)
        return pending, running, done, failed

    def update_apply_queue_status(self) -> None:
        pending, running, done, failed = self.apply_job_counts()
        text = f"Applications Komf : {pending} en attente / {running} en cours / {done} OK / {failed} échec"
        if hasattr(self, "apply_queue_status"):
            self.apply_queue_status.setText(text)
        if hasattr(self, "apply_progress_status"):
            self.apply_progress_status.setText(f"{pending} en attente / {running} en cours / {done} OK / {failed} échec")
        self.populate_apply_jobs_table()

    def apply_job_status_for_display(self, job: ApplyJob) -> str:
        status = fold_text(job.status)
        if status == "ok":
            return "OK"
        if status in {"echec", "échec"}:
            return "Échec"
        if status == "en cours":
            return "En cours"
        return "En attente"

    def populate_apply_jobs_table(self) -> None:
        if not hasattr(self, "apply_jobs_table"):
            return
        selected_job_id: Optional[int] = None
        selected_rows = sorted({idx.row() for idx in self.apply_jobs_table.selectedIndexes()})
        if selected_rows:
            item = self.apply_jobs_table.item(selected_rows[0], 0)
            if item is not None:
                try:
                    selected_job_id = int(item.text())
                except ValueError:
                    selected_job_id = None

        self.apply_jobs_table.setRowCount(0)
        jobs = list(getattr(self, "apply_jobs_all", []))
        for row, job in enumerate(jobs):
            self.apply_jobs_table.insertRow(row)
            values = [
                str(job.job_id),
                self.apply_job_status_for_display(job),
                job.series.name,
                job.result.provider,
                job.result.title,
                job.result.provider_series_id,
                job.detail,
                job.series.id,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(safe_str(value))
                if col == 1:
                    status_key = fold_text(value)
                    if status_key == "ok":
                        item.setText("✅ OK")
                    elif status_key == "echec":
                        item.setText("❌ Échec")
                    elif status_key == "en cours":
                        item.setText("⏳ En cours")
                    else:
                        item.setText("⏸️ En attente")
                self.apply_jobs_table.setItem(row, col, item)
            if selected_job_id is not None and job.job_id == selected_job_id:
                self.apply_jobs_table.selectRow(row)

    def selected_apply_job(self) -> Optional[ApplyJob]:
        if not hasattr(self, "apply_jobs_table"):
            return None
        rows = sorted({idx.row() for idx in self.apply_jobs_table.selectedIndexes()})
        if not rows:
            return None
        item = self.apply_jobs_table.item(rows[0], 0)
        if item is None:
            return None
        try:
            job_id = int(item.text())
        except ValueError:
            return None
        for job in getattr(self, "apply_jobs_all", []):
            if job.job_id == job_id:
                return job
        return None

    def open_selected_apply_job_series(self) -> None:
        job = self.selected_apply_job()
        if job is None:
            return
        for index, item in enumerate(self.queue_items):
            if item.id == job.series.id:
                self.current_queue_index = index
                self.populate_queue_table()
                self.refresh_current_series_display()
                self.tabs.setCurrentIndex(2)
                return
        QMessageBox.information(self, "Série introuvable", "Cette série n'est plus présente dans la file principale.")

    def make_operation_payload_text(self, action: str, s: SeriesItem, r: KomfResult, payload: Dict[str, str], response: Any = None) -> str:
        lines = [
            f"Action : {action}",
            f"Série Komga : {s.name}",
            f"Tomes Komga : {s.book_count or 'non renseigné'}",
            f"Résultat choisi : {r.title}",
            f"Provider : {r.provider}",
            f"Type : {r.media_type or 'non indiqué'}",
            f"Provider Series ID : {r.provider_series_id}",
        ]
        if r.provider_url:
            lines.append(f"Lien provider : {r.provider_url}")
        lines.append("\nPayload /identify :")
        lines.append(json.dumps(payload, ensure_ascii=False, indent=2))
        if response is not None:
            lines.append("\nRéponse Komf :")
            lines.append(json.dumps(response, ensure_ascii=False, indent=2) if not isinstance(response, str) else response)
        return "\n".join(lines)

    def make_cover_label(self, text: str = "Aucune\ncouverture", width: int = 110, height: int = 160) -> QLabel:
        label = QLabel(text)
        label.setAlignment(Qt.AlignCenter)
        label.setFixedSize(width, height)
        label.setWordWrap(True)
        label.setStyleSheet("border: 1px solid #555; background: #222; color: #aaa;")
        return label

    def set_cover_placeholder(self, label: QLabel, text: str = "Aucune\ncouverture") -> None:
        label.clear()
        label.setText(text)
        label.setAlignment(Qt.AlignCenter)

    def apply_image_to_label(self, label: QLabel, data: bytes) -> bool:
        image = QImage()
        if not data or not image.loadFromData(data):
            return False
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            return False
        scaled = pixmap.scaled(label.width(), label.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        label.clear()
        label.setPixmap(scaled)
        label.setAlignment(Qt.AlignCenter)
        return True

    def resolve_image_url(self, url: str, default_base: str = "") -> str:
        url = html.unescape((url or "").strip())
        if not url:
            return ""
        if looks_like_url(url) or url.startswith("//"):
            return encode_url_for_request(url)
        if url.startswith("/"):
            base = normalize_base_url(default_base or self.komf_url.text())
            return encode_url_for_request(base + url)
        return encode_url_for_request(url)

    def komga_series_thumbnail_url(self, series_id: str) -> str:
        """URL stable de couverture Komga basée sur l'ID, indépendante du titre.

        On évite ainsi les URL ou slugs fragiles quand un titre contient des
        caractères comme '-', '&', apostrophes, crochets, etc.
        """
        return normalize_base_url(self.komga_url.text()) + f"/api/v1/series/{parse.quote(series_id, safe='')}/thumbnail"

    def load_image_into_label(
        self,
        url: Any,
        label: QLabel,
        *,
        default_base: str = "",
        fallback_text: str = "Aucune\ncouverture",
        guard: Optional[Callable[[], bool]] = None,
    ) -> None:
        raw_urls = list(url) if isinstance(url, (list, tuple)) else [url]
        resolved_urls: List[str] = []
        seen_urls: set[str] = set()
        for raw_url in raw_urls:
            resolved = self.resolve_image_url(safe_str(raw_url), default_base=default_base)
            if resolved and resolved not in seen_urls:
                seen_urls.add(resolved)
                resolved_urls.append(resolved)

        if not resolved_urls:
            self.set_cover_placeholder(label, fallback_text)
            return

        # Essaie tous les candidats en cache avant de lancer un chargement réseau.
        for resolved in resolved_urls:
            if resolved not in self.image_cache:
                continue
            cached = self.image_cache[resolved]
            if cached and self.apply_image_to_label(label, cached):
                return

        self.set_cover_placeholder(label, "Chargement…")
        komga_base = ""
        try:
            komga_base = normalize_base_url(self.komga_url.text())
        except Exception:
            pass

        def done(payload: Tuple[str, bytes, str]) -> None:
            if guard is not None and not guard():
                return
            resolved, data, _errors = payload
            if resolved and data:
                self.image_cache[resolved] = data
                if self.apply_image_to_label(label, data):
                    return
            for candidate in resolved_urls:
                self.image_cache.setdefault(candidate, None)
            self.set_cover_placeholder(label, fallback_text)

        def fetch() -> Tuple[str, bytes, str]:
            errors: List[str] = []
            for resolved in resolved_urls:
                try:
                    api_key = self.komga_api_key.text().strip() if komga_base and resolved.startswith(komga_base) else ""
                    data = fetch_image_bytes(resolved, api_key=api_key)
                    if data:
                        return resolved, data, ""
                except Exception as exc:
                    errors.append(f"{resolved}: {exc}")
            return "", b"", "\n".join(errors)

        self.run_worker(
            "Chargement image",
            fetch,
            done,
            show_log=False,
            show_error_popup=False,
        )

    def clear_results_grid(self) -> None:
        while self.results_grid_layout.count():
            item = self.results_grid_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.result_cards = []

    def clear_series_cards_grid(self) -> None:
        if not hasattr(self, "series_cards_layout"):
            return
        while self.series_cards_layout.count():
            item = self.series_cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.series_card_widgets_by_id.clear()
        self.series_cover_labels_by_id.clear()
        self.series_cover_urls_by_id.clear()
        self.series_cover_requested_ids.clear()
        self.series_card_visible_ids = []
        self.series_cards_current_columns = 0

    def series_card_style(self, selected: bool = False) -> str:
        border = "#45c46f" if selected else "#444"
        background = "#203728" if selected else "#2b2b2b"
        return (
            f"QFrame {{ border: 2px solid {border}; border-radius: 8px; "
            f"background: {background}; padding: 6px; }}"
            "QLabel { border: none; background: transparent; color: #eeeeee; }"
        )

    def result_card_style(self, selected: bool = False) -> str:
        border = "#f6a21a" if selected else "#444"
        background = "#3a3a3a" if selected else "#2b2b2b"
        return (
            f"QFrame {{ border: 2px solid {border}; border-radius: 8px; "
            f"background: {background}; padding: 6px; }}"
            "QLabel { border: none; background: transparent; color: #eeeeee; }"
        )

    def make_series_card(self, index: int, series: SeriesItem) -> QFrame:
        selected = series.id in self.series_cover_selected_ids
        frame = QFrame()
        frame.setFixedSize(190, 315)
        frame.setCursor(Qt.PointingHandCursor)
        frame.setStyleSheet(self.series_card_style(selected))
        frame.setProperty("series_id", series.id)
        frame.setProperty("series_index", index)

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        cover = self.make_cover_label("Image\nnon chargée", width=150, height=215)
        cover.setStyleSheet("border: 1px solid #555; background: #202020; color: #aaa;")
        layout.addWidget(cover, 0, Qt.AlignHCenter)

        title = QLabel(series.name)
        title.setWordWrap(True)
        title.setAlignment(Qt.AlignCenter)
        title.setFixedHeight(48)
        layout.addWidget(title)

        meta_bits = []
        if series.book_count:
            meta_bits.append(f"Livres : {series.book_count}")
        status = safe_str(series.raw.get("_queue_status", ""))
        if status and status != "—":
            meta_bits.append(status)
        meta = QLabel(" | ".join(meta_bits) if meta_bits else "—")
        meta.setAlignment(Qt.AlignCenter)
        meta.setStyleSheet("color: #cccccc;")
        layout.addWidget(meta)

        # Les events utilisent l'ID stable de série, pas l'index visible.
        # Ça évite les sélections/désélections parasites quand la grille est
        # reconstruite ou filtrée pendant que l'utilisateur manipule les covers.
        for clickable in (frame, cover, title, meta):
            clickable.setContextMenuPolicy(Qt.CustomContextMenu)
            clickable.customContextMenuRequested.connect(
                lambda pos, sid=series.id, w=clickable: self.show_series_card_context_menu_for_id(sid, w.mapToGlobal(pos))
            )
            clickable.mousePressEvent = lambda event, sid=series.id: self.handle_series_card_mouse_press_for_id(event, sid)
            clickable.mouseReleaseEvent = lambda event, sid=series.id: self.handle_series_card_mouse_release_for_id(event, sid)

        cover_urls: List[str] = []
        if series.id:
            # La miniature Komga par ID est stable, généralement plus légère et
            # indépendante des caractères problématiques dans les titres.
            cover_urls.append(self.komga_series_thumbnail_url(series.id))
        if series.cover_url:
            cover_urls.append(series.cover_url)

        self.series_card_widgets_by_id[series.id] = frame
        self.series_cover_labels_by_id[series.id] = cover
        self.series_cover_urls_by_id[series.id] = cover_urls
        return frame

    def make_result_card(self, index: int, result: KomfResult) -> QFrame:
        frame = QFrame()
        frame.setFixedSize(190, 335)
        frame.setCursor(Qt.PointingHandCursor)
        frame.setStyleSheet(self.result_card_style(False))

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        cover = self.make_cover_label("Aucune\ncouverture", width=150, height=215)
        cover.setStyleSheet("border: 1px solid #555; background: #202020; color: #aaa;")
        layout.addWidget(cover, 0, Qt.AlignHCenter)

        title = QLabel(result.title)
        title.setWordWrap(True)
        title.setAlignment(Qt.AlignCenter)
        title.setFixedHeight(42)
        layout.addWidget(title)

        provider = QPushButton(result.provider)
        provider.setCursor(Qt.PointingHandCursor if result.provider_url else Qt.ArrowCursor)
        provider.setEnabled(bool(result.provider_url))
        provider.setToolTip(result.provider_url or "Aucun lien provider fourni")
        provider.setStyleSheet(
            "font-weight: bold; color: white; background: #1f1f1f; "
            "border-radius: 4px; padding: 4px;"
        )
        provider.clicked.connect(lambda _checked=False, r=result, idx=index: self.open_result_provider_url(r, idx))
        layout.addWidget(provider)

        if result.media_type:
            media = QLabel(result.media_type)
            media.setAlignment(Qt.AlignCenter)
            media.setStyleSheet(
                "color: #ffffff; background: #39445a; border-radius: 4px; "
                "padding: 3px; font-weight: bold;"
            )
            layout.addWidget(media)
            self.bind_result_context_menu(media, index)
            media.mousePressEvent = lambda event, idx=index: self.handle_result_card_mouse_press(event, idx)

        if result.volume_count and result.volume_count.strip():
            count = QLabel(f"Tomes : {result.volume_count.strip()}")
            count.setAlignment(Qt.AlignCenter)
            count.setStyleSheet("color: #cccccc;")
            layout.addWidget(count)
            self.bind_result_context_menu(count, index)
            count.mousePressEvent = lambda event, idx=index: self.handle_result_card_mouse_press(event, idx)

        for clickable in (frame, cover, title):
            self.bind_result_context_menu(clickable, index)
            clickable.mousePressEvent = lambda event, idx=index: self.handle_result_card_mouse_press(event, idx)
        provider.setContextMenuPolicy(Qt.CustomContextMenu)
        provider.customContextMenuRequested.connect(
            lambda pos, idx=index, widget=provider: self.show_result_context_menu(idx, widget.mapToGlobal(pos))
        )
        self.load_image_into_label(
            result.cover_url,
            cover,
            default_base=self.komf_url.text(),
            fallback_text="Aucune\ncouverture",
        )
        return frame

    def bind_result_context_menu(self, widget: QWidget, index: int) -> None:
        widget.setContextMenuPolicy(Qt.CustomContextMenu)
        widget.customContextMenuRequested.connect(
            lambda pos, idx=index, w=widget: self.show_result_context_menu(idx, w.mapToGlobal(pos))
        )

    def handle_result_card_mouse_press(self, event: Any, index: int) -> None:
        if event.button() in (Qt.LeftButton, Qt.RightButton):
            self.select_result_index(index)
            event.accept()

    def _event_global_xy(self, event: Any) -> Tuple[int, int]:
        try:
            point = event.globalPosition().toPoint()
            return int(point.x()), int(point.y())
        except Exception:
            try:
                point = event.globalPos()
                return int(point.x()), int(point.y())
            except Exception:
                return 0, 0

    def handle_series_card_mouse_press(self, event: Any, index: int) -> None:
        # Compatibilité avec d'anciens appels internes éventuels.
        items = self.visible_series()
        if 0 <= index < len(items):
            self.handle_series_card_mouse_press_for_id(event, items[index].id)

    def handle_series_card_mouse_release(self, event: Any, index: int) -> None:
        # Compatibilité avec d'anciens appels internes éventuels.
        items = self.visible_series()
        if 0 <= index < len(items):
            self.handle_series_card_mouse_release_for_id(event, items[index].id)

    def handle_series_card_mouse_press_for_id(self, event: Any, series_id: str) -> None:
        if event.button() == Qt.LeftButton:
            frame = self.series_card_widgets_by_id.get(series_id)
            if frame is not None:
                frame.setProperty("press_xy", self._event_global_xy(event))
            event.accept()
        elif event.button() == Qt.RightButton:
            # Clic droit = menu contextuel uniquement. Il ne doit jamais remplacer
            # ou réduire la sélection existante : les seules désélections
            # autorisées sont un reclic gauche sur la cover ou le bouton
            # "Désélectionner tout".
            event.accept()

    def handle_series_card_mouse_release_for_id(self, event: Any, series_id: str) -> None:
        if event.button() != Qt.LeftButton:
            return
        frame = self.series_card_widgets_by_id.get(series_id)
        press_xy = frame.property("press_xy") if frame is not None else None
        release_xy = self._event_global_xy(event)
        if isinstance(press_xy, tuple) and len(press_xy) == 2:
            dx = abs(int(release_xy[0]) - int(press_xy[0]))
            dy = abs(int(release_xy[1]) - int(press_xy[1]))
            # Si l'utilisateur fait glisser la vue pour scroller, on ne touche pas
            # à la sélection. C'était une cause probable de désélection involontaire.
            if dx > 8 or dy > 8:
                event.accept()
                return
        self.toggle_series_cover_selection_by_id(series_id)
        event.accept()

    def show_series_card_context_menu(self, index: int, global_pos: Any) -> None:
        # Compatibilité avec d'anciens appels internes éventuels.
        items = self.visible_series()
        if not (0 <= index < len(items)):
            return
        self.show_series_card_context_menu_for_id(items[index].id, global_pos)

    def show_series_card_context_menu_for_id(self, series_id: str, global_pos: Any) -> None:
        series = self.series_item_by_id(series_id)
        if not series:
            return

        # Le clic droit ne doit jamais désélectionner les covers déjà choisies.
        # Si aucune cover n'est sélectionnée, on ajoute uniquement la cover ciblée
        # pour que le menu contextuel reste immédiatement exploitable. Si une
        # sélection existe déjà, elle reste strictement inchangée.
        if not self.series_cover_selected_ids:
            self.add_series_cover_selection_by_id(series.id)

        selected_count = len(self.selected_series_items())
        menu = QMenu(self)
        add_action = menu.addAction(f"Ajouter la sélection à la file ({selected_count})")
        auto_action = menu.addAction(f"Auto identify sélection ({selected_count})")
        open_action = menu.addAction("Ouvrir dans le traitement manuel")
        chosen = menu.exec(global_pos)
        if chosen == add_action:
            self.add_selected_series_to_queue()
        elif chosen == auto_action:
            self.auto_identify_selected_series()
        elif chosen == open_action:
            if self._add_to_queue(series):
                self.populate_queue_table()
            self.current_queue_index = next((i for i, item in enumerate(self.queue_items) if item.id == series.id), self.current_queue_index)
            self.populate_queue_table()
            self.refresh_current_series_display()
            self.tabs.setCurrentIndex(2)

    def series_item_by_id(self, series_id: str) -> Optional[SeriesItem]:
        for item in self.series_items:
            if item.id == series_id:
                return item
        for item in self.queue_items:
            if item.id == series_id:
                return item
        return None

    def toggle_series_cover_selection(self, index: int) -> None:
        # Compatibilité avec d'anciens appels internes éventuels.
        items = self.visible_series()
        if not (0 <= index < len(items)):
            return
        self.toggle_series_cover_selection_by_id(items[index].id)

    def toggle_series_cover_selection_by_id(self, series_id: str) -> None:
        if series_id in self.series_cover_selected_ids:
            self.series_cover_selected_ids.remove(series_id)
        else:
            self.series_cover_selected_ids.add(series_id)
        self.update_single_series_card_selection_style(series_id)

    def add_series_cover_selection_by_id(self, series_id: str) -> None:
        """Ajoute une cover à la sélection sans jamais retirer les autres."""
        if not series_id or series_id in self.series_cover_selected_ids:
            return
        self.series_cover_selected_ids.add(series_id)
        self.update_single_series_card_selection_style(series_id)

    def select_only_series_cover_card(self, index: int) -> None:
        # Compatibilité avec d'anciens appels internes éventuels.
        items = self.visible_series()
        if not (0 <= index < len(items)):
            return
        self.select_only_series_cover_card_by_id(items[index].id)

    def select_only_series_cover_card_by_id(self, series_id: str) -> None:
        """Remplacement complet de sélection, conservé pour compatibilité interne.

        Ne pas appeler depuis le clic droit : l'utilisateur a demandé que les
        covers déjà sélectionnées ne soient jamais désélectionnées sauf reclic
        gauche ou bouton "Désélectionner tout".
        """
        previous_ids = set(self.series_cover_selected_ids)
        self.series_cover_selected_ids = {series_id}
        for changed_id in previous_ids | {series_id}:
            self.update_single_series_card_selection_style(changed_id)

    def update_single_series_card_selection_style(self, series_id: str) -> None:
        widget = self.series_card_widgets_by_id.get(series_id)
        if widget is not None:
            widget.setStyleSheet(self.series_card_style(series_id in self.series_cover_selected_ids))
        self.update_series_count_label()

    def update_series_count_label(self) -> None:
        if not hasattr(self, "series_count_label"):
            return
        visible_count = len(self.visible_series())
        selected_count = len(self.selected_series_ids_from_cover_view())
        suffix = f" — {selected_count} sélectionnée(s)" if selected_count else ""
        self.series_count_label.setText(f"{visible_count} série(s){suffix}")

    def update_series_card_selection_styles(self) -> None:
        if not hasattr(self, "series_cards_layout"):
            return
        for series_id, widget in list(self.series_card_widgets_by_id.items()):
            widget.setStyleSheet(self.series_card_style(series_id in self.series_cover_selected_ids))
        self.update_series_count_label()

    def clear_series_selection(self) -> None:
        """Vide explicitement la sélection des séries, y compris la sélection par covers."""
        selected_ids = set(self.series_cover_selected_ids)
        self.series_cover_selected_ids.clear()
        if hasattr(self, "series_table"):
            self.series_table.clearSelection()
        for series_id in selected_ids:
            self.update_single_series_card_selection_style(series_id)
        self.update_series_count_label()
        self.log_info("Sélection des séries vidée.")

    def show_result_context_menu(self, index: int, global_pos: Any) -> None:
        if not (0 <= index < len(self.current_results)):
            return
        self.select_result_index(index)
        result = self.current_results[index]

        menu = QMenu(self)
        apply_action = menu.addAction("Appliquer réellement")
        apply_action.setEnabled(bool(self.current_series() and result and not self.simulation_mode.isChecked()))
        simulate_action = menu.addAction("Simuler")
        open_action = menu.addAction("Ouvrir le lien provider")
        open_action.setEnabled(bool(result.provider_url))

        chosen = menu.exec(global_pos)
        if chosen == apply_action:
            self.apply_selected_result()
        elif chosen == simulate_action:
            self.simulate_selected_result()
        elif chosen == open_action:
            self.open_result_provider_url(result, index)

    def show_results_table_context_menu(self, pos: Any) -> None:
        row = self.results_table.rowAt(pos.y())
        if row < 0 or row >= len(self.current_results):
            return
        self.select_result_index(row)
        self.show_result_context_menu(row, self.results_table.viewport().mapToGlobal(pos))

    def open_result_provider_url(self, result: KomfResult, index: Optional[int] = None) -> None:
        if index is not None:
            self.select_result_index(index)
        if not result.provider_url:
            self.log_info(f"ℹ️ Aucun lien provider disponible pour {result.provider} — {result.title}")
            return
        opened = QDesktopServices.openUrl(QUrl(result.provider_url))
        if opened:
            self.log_info(f"🌐 Lien provider ouvert : {result.provider_url}")
        else:
            self.log_info(f"⚠️ Impossible d'ouvrir le lien provider : {result.provider_url}")

    def update_results_view_mode(self) -> None:
        if not hasattr(self, "results_grid_scroll") or not hasattr(self, "results_table"):
            return
        grid_mode = self.result_view_mode.currentText() == "Grille"
        self.results_grid_scroll.setVisible(grid_mode)
        self.results_table.setVisible(not grid_mode)

    def select_result_index(self, index: Optional[int], *, sync_table: bool = True) -> None:
        if index is None or not (0 <= index < len(self.current_results)):
            self.selected_result_index = None
        else:
            self.selected_result_index = index

        if sync_table and hasattr(self, "results_table"):
            self.results_table.blockSignals(True)
            self.results_table.clearSelection()
            if self.selected_result_index is not None:
                self.results_table.selectRow(self.selected_result_index)
            self.results_table.blockSignals(False)

        self.update_result_selection_styles()
        self.update_selected_result_summary()
        self.last_simulated_key = None
        self._update_apply_button_state()

    def update_result_selection_styles(self) -> None:
        for idx, card in enumerate(getattr(self, "result_cards", [])):
            card.setStyleSheet(self.result_card_style(idx == self.selected_result_index))

    def run_worker(
        self,
        label: str,
        fn: Callable[..., Any],
        on_result: Callable[[Any], None],
        *,
        show_log: bool = True,
        show_error_popup: bool = True,
    ) -> None:
        if show_log:
            self.log_info(f"⏳ {label}...")
        worker = Worker(fn)
        try:
            worker.setAutoDelete(False)
        except Exception:
            pass
        self.active_workers.add(worker)

        def safe_result(result: Any) -> None:
            try:
                on_result(result)
            except Exception:
                self.on_worker_error(label, traceback.format_exc(), show_popup=show_error_popup)

        def cleanup() -> None:
            self.active_workers.discard(worker)
            if show_log:
                self.log_info(f"ℹ️ {label} terminé")

        worker.signals.result.connect(safe_result)
        worker.signals.error.connect(lambda e: self.on_worker_error(label, e, show_popup=show_error_popup, log_error=show_log))
        worker.signals.finished.connect(cleanup)
        self.thread_pool.start(worker)

    def on_worker_error(self, label: str, trace: str, *, show_popup: bool = True, log_error: bool = True) -> None:
        if log_error:
            self.log_info(f"❌ {label} a échoué :\n{trace}")
        if label.startswith("Application Komf"):
            self.set_operation_output("❌ Application échouée", trace)
        elif label.startswith("Recherche Komf"):
            self.set_operation_output("❌ Recherche échouée", trace)
        elif label.startswith("Auto identify Komf"):
            self.set_operation_output("❌ Auto identify échoué", trace)
        if label.startswith("Auto identify Komf"):
            self.auto_identify_running = False
            self.update_auto_identify_buttons()
        if show_popup:
            QMessageBox.critical(self, "Erreur", f"{label} a échoué. Détail dans le journal.")

    def komga_api(self) -> KomgaApi:
        if self.komga_api_provider:
            return self.komga_api_provider()
        return KomgaApi(self.komga_url.text(), self.komga_api_key.text())

    def komf_api(self) -> KomfApi:
        if self.komf_api_provider:
            return self.komf_api_provider()
        return KomfApi(self.komf_url.text())

    def current_library_id(self) -> str:
        idx = self.library_combo.currentIndex()
        if idx < 0:
            return ""
        return safe_str(self.library_combo.itemData(idx))

    def selected_series_rows(self) -> List[int]:
        rows = sorted({idx.row() for idx in self.series_table.selectedIndexes()})
        return rows

    def selected_series_ids_from_table(self) -> set[str]:
        ids: set[str] = set()
        for row in self.selected_series_rows():
            s = self.series_from_visible_row(row)
            if s and s.id:
                ids.add(s.id)
        return ids

    def selected_series_ids_from_cover_view(self) -> set[str]:
        # Sélection verrouillée : un filtre ou une reconstruction de grille ne doit
        # pas désélectionner silencieusement les covers déjà choisies.
        known_ids = {s.id for s in self.series_items}
        return {sid for sid in self.series_cover_selected_ids if sid in known_ids}

    def current_series_selection_ids(self) -> set[str]:
        if hasattr(self, "series_view_mode") and self.series_view_mode.currentText() == "Covers":
            return self.selected_series_ids_from_cover_view()
        return self.selected_series_ids_from_table()

    def queue_selected_rows(self) -> List[int]:
        return sorted({idx.row() for idx in self.queue_table.selectedIndexes()})

    def current_series(self) -> Optional[SeriesItem]:
        if 0 <= self.current_queue_index < len(self.queue_items):
            return self.queue_items[self.current_queue_index]
        return None

    # ---------- Config ----------

    def _load_local_config(self) -> None:
        return

    def _configure_shared_connection_ui(self) -> None:
        self.komga_url.setText("Connexion Komga fournie par Komga Toolkit")
        self.komf_url.setText("Connexion Komf fournie par Komga Toolkit")
        self.komga_api_key.clear()
        self.save_api_key.setChecked(False)
        for widget in (
            self.komga_url,
            self.komf_url,
            self.komga_api_key,
            self.save_api_key,
            self.btn_save_config,
        ):
            widget.setEnabled(False)

    def _config_payload(self) -> Dict[str, Any]:
        cfg: Dict[str, Any] = {
            "komga_url": self.komga_url.text().strip(),
            "komf_url": self.komf_url.text().strip(),
            "save_api_key": self.save_api_key.isChecked(),
        }
        if self.save_api_key.isChecked():
            cfg["api_key"] = self.komga_api_key.text().strip()
        return cfg

    def _save_local_config_silent(self) -> bool:
        return True

    def save_local_config(self) -> None:
        if self._save_local_config_silent():
            self.log_info("Paramètres conservés uniquement pour cette session")

    # ---------- Actions connexion ----------

    def test_komga(self) -> None:
        def done(result: str) -> None:
            self.log_info(f"✅ {result}")
            self._save_local_config_silent()

        self.run_worker("Test Komga", lambda: self.komga_api().test(), done)

    def test_komf(self) -> None:
        def done(result: str) -> None:
            self.log_info(f"✅ {result}")
            self._save_local_config_silent()

        self.run_worker("Test Komf", lambda: self.komf_api().test(), done)

    def load_libraries(self) -> None:
        def done(libs: List[LibraryItem]) -> None:
            self.libraries = libs
            self.library_combo.blockSignals(True)
            self.library_combo.clear()
            for lib in libs:
                self.library_combo.addItem(f"{lib.name} — {lib.id}", lib.id)
            self.library_combo.blockSignals(False)

            # Préselection light novels si le nom aide.
            for i, lib in enumerate(libs):
                n = lib.name.lower()
                if "light" in n or "novel" in n or re.search(r"\bln\b", n):
                    self.library_combo.setCurrentIndex(i)
                    break

            if libs:
                names = ", ".join(f"{lib.name} ({lib.id})" for lib in libs[:10])
                self.log_info(f"✅ {len(libs)} bibliothèque(s) chargée(s) : {names}")
            else:
                self.log_info("⚠️ Komga a répondu, mais aucune bibliothèque n'a été extraite de la réponse.")
            self._save_local_config_silent()
            self.tabs.setCurrentIndex(1)

        self.run_worker("Chargement des bibliothèques", lambda: self.komga_api().libraries(), done)

    # ---------- Series ----------

    def load_series(self) -> None:
        library_id = self.current_library_id()
        if not library_id:
            QMessageBox.warning(self, "Bibliothèque", "Charge et sélectionne une bibliothèque d'abord.")
            return

        def done(items: List[SeriesItem]) -> None:
            self.series_items = items
            known_ids = {series.id for series in items}
            self.series_cover_selected_ids = {sid for sid in self.series_cover_selected_ids if sid in known_ids}
            self.series_cards_dirty = True
            self.populate_series_table()
            self.log_info(f"✅ {len(items)} série(s) chargée(s)")

        self.run_worker("Chargement des séries", lambda: self.komga_api().series(library_id), done)

    def visible_series(self) -> List[SeriesItem]:
        text = fold_text(self.filter_text.text().strip())
        output: List[SeriesItem] = []
        for s in self.series_items:
            if text and text not in fold_text(s.name):
                continue
            missing_summary = not truthy_nonempty(s.summary)
            missing_publisher = not truthy_nonempty(s.publisher)
            missing_links = not truthy_nonempty(s.links)
            if self.filter_weak_metadata.isChecked() and not (missing_summary or missing_publisher or missing_links):
                continue
            if self.filter_missing_summary.isChecked() and not missing_summary:
                continue
            if self.filter_missing_publisher.isChecked() and not missing_publisher:
                continue
            if self.filter_missing_links.isChecked() and not missing_links:
                continue
            output.append(s)
        return output

    def reset_series_filters(self) -> None:
        self.filter_text.clear()
        for checkbox in (
            self.filter_missing_summary,
            self.filter_missing_publisher,
            self.filter_missing_links,
            self.filter_weak_metadata,
        ):
            checkbox.setChecked(False)
        self.populate_series_table()

    def active_filter_label(self) -> str:
        parts: List[str] = []
        if self.filter_text.text().strip():
            parts.append(f"texte={self.filter_text.text().strip()!r}")
        if self.filter_missing_summary.isChecked():
            parts.append("sans résumé")
        if self.filter_missing_publisher.isChecked():
            parts.append("sans éditeur")
        if self.filter_missing_links.isChecked():
            parts.append("sans lien")
        if self.filter_weak_metadata.isChecked():
            parts.append("metadata faible")
        return ", ".join(parts) if parts else "aucun filtre"

    def request_populate_series_table(self) -> None:
        # Les filtres texte peuvent générer une reconstruction complète à chaque
        # caractère. On temporise légèrement pour éviter le lag UI.
        timer = getattr(self, "series_filter_timer", None)
        if timer is not None:
            timer.start()
        else:
            self.populate_series_table()

    def populate_series_table(self) -> None:
        items = self.visible_series()
        if hasattr(self, "series_count_label"):
            selected_count = len(self.selected_series_ids_from_cover_view())
            suffix = f" — {selected_count} sélectionnée(s)" if selected_count else ""
            self.series_count_label.setText(f"{len(items)} série(s){suffix}")
        self.series_table.blockSignals(True)
        self.series_table.setRowCount(0)
        for row, s in enumerate(items):
            self.series_table.insertRow(row)
            self.series_table.setItem(row, 0, QTableWidgetItem(s.name))
            self.series_table.setItem(row, 1, QTableWidgetItem(s.book_count))
            self.series_table.setItem(row, 2, QTableWidgetItem("oui" if truthy_nonempty(s.summary) else "non"))
            self.series_table.setItem(row, 3, QTableWidgetItem(s.publisher or "non"))
            self.series_table.setItem(row, 4, QTableWidgetItem("oui" if truthy_nonempty(s.links) else "non"))
            self.series_table.setItem(row, 5, QTableWidgetItem(safe_str(s.raw.get("_queue_status", "—"))))
            self.series_table.setItem(row, 6, QTableWidgetItem(s.id))
        self.series_table.blockSignals(False)

        # Ne pas reconstruire la grille de covers quand elle est masquée : c'était
        # coûteux, et cela relançait inutilement des chargements d'images.
        self.series_cards_dirty = True
        if hasattr(self, "series_view_mode") and self.series_view_mode.currentText() == "Covers":
            self.populate_series_cards(items)
        self.log_info(f"Filtre : {len(items)} série(s) visible(s) / {len(self.series_items)} — {self.active_filter_label()}")

    def series_card_column_count(self) -> int:
        viewport_width = 1000
        try:
            viewport_width = max(300, self.series_cards_scroll.viewport().width())
        except Exception:
            pass
        # Carte fixe 190px + espacement 12px + marge de sécurité.
        return max(1, min(12, viewport_width // 205))

    def populate_series_cards(self, items: Optional[List[SeriesItem]] = None) -> None:
        if not hasattr(self, "series_cards_layout"):
            return
        items = self.visible_series() if items is None else items
        self.clear_series_cards_grid()
        self.series_card_visible_ids = [series.id for series in items]
        self.series_cards_current_columns = self.series_card_column_count()
        for index, series in enumerate(items):
            card = self.make_series_card(index, series)
            self.series_cards_layout.addWidget(card, index // self.series_cards_current_columns, index % self.series_cards_current_columns)
        self.series_cards_dirty = False
        self.update_series_card_selection_styles()
        self.schedule_visible_series_cover_loads()

    def schedule_series_cards_reflow(self) -> None:
        if not hasattr(self, "series_cards_reflow_timer"):
            self.reflow_series_cards_if_needed(force=False)
            return
        if hasattr(self, "series_view_mode") and self.series_view_mode.currentText() != "Covers":
            return
        self.series_cards_reflow_timer.start()

    def reflow_series_cards_if_needed(self, force: bool = False) -> None:
        if not hasattr(self, "series_cards_layout"):
            return
        if hasattr(self, "series_view_mode") and self.series_view_mode.currentText() != "Covers":
            return
        new_columns = self.series_card_column_count()
        if not force and new_columns == self.series_cards_current_columns:
            return
        ids = list(self.series_card_visible_ids)
        if not ids:
            return

        # Déplace les widgets existants : pas de destruction/recréation, donc pas
        # de rechargement des covers et pas de perte de sélection.
        while self.series_cards_layout.count():
            self.series_cards_layout.takeAt(0)
        for index, series_id in enumerate(ids):
            card = self.series_card_widgets_by_id.get(series_id)
            if card is not None:
                self.series_cards_layout.addWidget(card, index // new_columns, index % new_columns)
        self.series_cards_current_columns = new_columns
        self.update_series_card_selection_styles()
        self.schedule_visible_series_cover_loads()

    def schedule_visible_series_cover_loads(self) -> None:
        timer = getattr(self, "series_cover_load_timer", None)
        if timer is not None:
            timer.start()
        else:
            self.load_visible_series_covers()

    def load_visible_series_covers(self) -> None:
        if not hasattr(self, "series_cards_scroll") or self.series_view_mode.currentText() != "Covers":
            return
        top = self.series_cards_scroll.verticalScrollBar().value()
        bottom = top + self.series_cards_scroll.viewport().height()
        margin = 700
        for series_id, frame in list(self.series_card_widgets_by_id.items()):
            if series_id in self.series_cover_requested_ids:
                continue
            geometry = frame.geometry()
            card_top = geometry.y()
            card_bottom = card_top + geometry.height()
            if card_bottom < top - margin or card_top > bottom + margin:
                continue
            label = self.series_cover_labels_by_id.get(series_id)
            urls = self.series_cover_urls_by_id.get(series_id, [])
            if label is None:
                continue
            self.series_cover_requested_ids.add(series_id)
            self.load_image_into_label(
                urls,
                label,
                default_base=self.komga_url.text(),
                fallback_text="Aucune\ncouverture",
                guard=lambda sid=series_id, lbl=label: self.series_cover_labels_by_id.get(sid) is lbl,
            )

    def update_series_view_mode(self) -> None:
        if not hasattr(self, "series_cards_scroll"):
            return
        mode = self.series_view_mode.currentText()
        if mode == "Covers":
            table_ids = self.selected_series_ids_from_table()
            if table_ids:
                # Union, pas remplacement : la sélection cover est verrouillée et
                # ne doit pas être effacée par une sélection tableau partielle.
                self.series_cover_selected_ids |= table_ids
            self.series_table.hide()
            self.series_cards_scroll.show()
            if self.series_cards_dirty or not self.series_card_widgets_by_id:
                self.populate_series_cards()
            else:
                self.update_series_card_selection_styles()
                self.schedule_visible_series_cover_loads()
        else:
            self.series_cards_scroll.hide()
            self.series_table.show()
            ids = set(self.series_cover_selected_ids)
            self.series_table.clearSelection()
            for row, series in enumerate(self.visible_series()):
                if series.id in ids:
                    self.series_table.selectRow(row)

    def series_from_visible_row(self, row: int) -> Optional[SeriesItem]:
        items = self.visible_series()
        if 0 <= row < len(items):
            return items[row]
        return None

    def add_selected_series_to_queue(self) -> None:
        added = 0
        for s in self.selected_series_items():
            if s and self._add_to_queue(s):
                added += 1
        self.populate_queue_table()
        self.log_info(f"✅ {added} série(s) ajoutée(s) à la file")

    def add_all_visible_to_queue(self) -> None:
        added = 0
        for s in self.visible_series():
            if self._add_to_queue(s):
                added += 1
        self.populate_queue_table()
        self.log_info(f"✅ {added} série(s) visible(s) ajoutée(s) à la file")

    def _add_to_queue(self, series: SeriesItem) -> bool:
        if any(q.id == series.id for q in self.queue_items):
            return False
        self.queue_items.append(series)
        return True

    def populate_queue_table(self) -> None:
        self.queue_table.setRowCount(0)
        for row, s in enumerate(self.queue_items):
            self.queue_table.insertRow(row)
            marker = "▶" if row == self.current_queue_index else str(row + 1)
            self.queue_table.setItem(row, 0, QTableWidgetItem(marker))
            self.queue_table.setItem(row, 1, QTableWidgetItem(s.name))
            self.queue_table.setItem(row, 2, QTableWidgetItem(safe_str(s.raw.get("_queue_status", "en attente"))))
            self.queue_table.setItem(row, 3, QTableWidgetItem(s.id))
        if 0 <= self.current_queue_index < self.queue_table.rowCount():
            self.queue_table.selectRow(self.current_queue_index)
        self.update_processing_queue_status()

    def remove_selected_queue_item(self) -> None:
        rows = self.queue_selected_rows()
        if not rows:
            return
        for row in reversed(rows):
            if 0 <= row < len(self.queue_items):
                del self.queue_items[row]
        if self.current_queue_index >= len(self.queue_items):
            self.current_queue_index = len(self.queue_items) - 1
        self.populate_queue_table()
        self.refresh_current_series_display()

    def clear_queue(self) -> None:
        if self.queue_items and QMessageBox.question(self, "Vider", "Vider toute la file ?") != QMessageBox.Yes:
            return
        self.queue_items.clear()
        self.current_queue_index = -1
        self.populate_queue_table()
        self.refresh_current_series_display()

    def save_queue(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Sauver la file", "komf_queue.json", "JSON (*.json)")
        if not path:
            return
        data = [s.raw | {"_tool_id": s.id, "_tool_name": s.name, "_tool_library_id": s.library_id} for s in self.queue_items]
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.log_info(f"✅ File sauvegardée : {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", str(exc))

    def load_queue(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Charger une file", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            items: List[SeriesItem] = []
            for raw in data:
                if not isinstance(raw, dict):
                    continue
                s = SeriesItem(
                    id=safe_str(raw.get("_tool_id") or raw.get("id")),
                    name=safe_str(raw.get("_tool_name") or raw.get("name") or raw.get("title") or raw.get("id")),
                    library_id=safe_str(raw.get("_tool_library_id") or raw.get("libraryId") or self.current_library_id()),
                    cover_url=extract_cover_url(raw),
                    raw=raw,
                )
                if s.id:
                    items.append(s)
            self.queue_items = items
            self.current_queue_index = 0 if items else -1
            self.populate_queue_table()
            self.refresh_current_series_display()
            self.log_info(f"✅ File chargée : {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", str(exc))

    # ---------- Auto Identify v2 ----------

    def selected_series_items(self) -> List[SeriesItem]:
        selected_ids = self.current_series_selection_ids()
        if not selected_ids:
            return []
        source = self.series_items if self.series_view_mode.currentText() == "Covers" else self.visible_series()
        return self.unique_series_items(s for s in source if s.id in selected_ids)

    def selected_queue_items(self) -> List[SeriesItem]:
        items: List[SeriesItem] = []
        for row in self.queue_selected_rows():
            if 0 <= row < len(self.queue_items):
                items.append(self.queue_items[row])
        return self.unique_series_items(items)

    def unique_series_items(self, items: Iterable[SeriesItem]) -> List[SeriesItem]:
        output: List[SeriesItem] = []
        seen: set[str] = set()
        for item in items:
            if not item or not item.id or item.id in seen:
                continue
            seen.add(item.id)
            output.append(item)
        return output

    def auto_identify_selected_series(self) -> None:
        self.auto_identify_series_items(self.selected_series_items(), "sélection de la liste des séries")

    def auto_identify_visible_series(self) -> None:
        self.auto_identify_series_items(self.unique_series_items(self.visible_series()), "séries visibles après filtre")

    def auto_identify_selected_queue_items(self) -> None:
        self.auto_identify_series_items(self.selected_queue_items(), "sélection de la file")

    def auto_identify_all_queue_items(self) -> None:
        self.auto_identify_series_items(self.unique_series_items(self.queue_items), "toute la file")

    def auto_identify_series_items(self, items: List[SeriesItem], source_label: str) -> None:
        items = self.unique_series_items(items)
        if not items:
            QMessageBox.warning(self, "Auto identify", "Aucune série sélectionnée pour l'auto identify.")
            return
        if self.auto_identify_running:
            QMessageBox.warning(self, "Auto identify", "Un auto identify est déjà en cours.")
            return

        dry_run = self.simulation_mode.isChecked() if hasattr(self, "simulation_mode") else True
        current_library_id = self.current_library_id()
        jobs: List[Tuple[SeriesItem, str]] = [(s, s.library_id or current_library_id) for s in items]
        missing_library = [s.name for s, library_id in jobs if not library_id]
        if missing_library:
            QMessageBox.warning(
                self,
                "Auto identify",
                "Certaines séries n'ont pas de libraryId exploitable. Charge une bibliothèque ou recharge la file.\n\n"
                + "\n".join(missing_library[:20]),
            )
            return

        mode_text = "SIMULATION — aucune écriture" if dry_run else "RÉEL — Komf modifiera Komga selon ta configuration Komf"
        message = (
            f"Lancer Auto Identify v2 sur {len(items)} série(s) ?\n\n"
            f"Source : {source_label}\n"
            f"Mode : {mode_text}\n\n"
            "Endpoint utilisé pour chaque série :\n"
            "POST /komga/match/library/{libraryId}/series/{seriesId}\n\n"
            "Important : le script ne lance pas le matching global /match/library/{libraryId}."
        )
        if QMessageBox.question(self, "Auto identify v2", message) != QMessageBox.Yes:
            return

        self.tabs.setCurrentIndex(2)
        if dry_run:
            planned = []
            for s, library_id in jobs:
                payload = {
                    "libraryId": library_id,
                    "seriesId": s.id,
                    "seriesTitle": s.name,
                    "endpoint": f"/komga/match/library/{library_id}/series/{s.id}",
                }
                planned.append(payload)
                s.raw["_queue_status"] = "auto simulation"
            self.populate_series_table()
            self.populate_queue_table()
            self.log_json("🧪 Auto Identify v2 — simulation, aucune écriture", planned)
            self.set_operation_output(
                "🧪 Auto Identify v2 — simulation",
                f"{len(planned)} série(s) seraient traitées une par une.\n\n"
                + json.dumps(planned, ensure_ascii=False, indent=2),
            )
            return

        for s in items:
            s.raw["_queue_status"] = "auto en cours"
        self.populate_series_table()
        self.populate_queue_table()
        self.set_operation_output(
            "⏳ Auto Identify v2 en cours",
            f"{len(items)} série(s) à traiter depuis : {source_label}\n"
            "Chaque série est traitée via l'endpoint Komf par série, dans le worker de fond.",
        )
        self.log_info(f"⏳ Auto Identify v2 réel lancé sur {len(items)} série(s) — {source_label}")
        self.auto_identify_running = True
        self.update_auto_identify_buttons()

        def do_batch() -> List[Dict[str, Any]]:
            api = self.komf_api()
            batch_results: List[Dict[str, Any]] = []
            total = len(jobs)
            for pos, (s, library_id) in enumerate(jobs, start=1):
                entry: Dict[str, Any] = {
                    "index": pos,
                    "total": total,
                    "seriesTitle": s.name,
                    "seriesId": s.id,
                    "libraryId": library_id,
                }
                try:
                    result = api.match_series(library_id, s.id)
                    entry.update({
                        "ok": True,
                        "url": result.get("url"),
                        "response": result.get("response"),
                    })
                except Exception:
                    entry.update({
                        "ok": False,
                        "error": traceback.format_exc(),
                    })
                batch_results.append(entry)
            return batch_results

        def done(results: List[Dict[str, Any]]) -> None:
            self.auto_identify_running = False
            by_id = {safe_str(r.get("seriesId")): r for r in results}
            ok_count = 0
            fail_count = 0
            for s in items:
                result = by_id.get(s.id)
                if not result:
                    s.raw["_queue_status"] = "auto inconnu"
                    continue
                if result.get("ok"):
                    ok_count += 1
                    s.raw["_queue_status"] = "auto appliqué"
                else:
                    fail_count += 1
                    s.raw["_queue_status"] = "auto erreur"
            self.populate_series_table()
            self.populate_queue_table()
            self.update_auto_identify_buttons()
            self.log_json("✅ Auto Identify v2 terminé", results)
            failures = [r for r in results if not r.get("ok")]
            failure_text = ""
            if failures:
                failure_text = "\n\nÉchecs :\n" + "\n\n".join(
                    f"- {safe_str(r.get('seriesTitle'))}\n{safe_str(r.get('error'))}" for r in failures[:10]
                )
            self.set_operation_output(
                "✅ Auto Identify v2 terminé" if not failures else "⚠️ Auto Identify v2 terminé avec erreurs",
                f"Réussites : {ok_count}\nÉchecs : {fail_count}\nTotal : {len(results)}"
                + failure_text,
            )

        self.run_worker("Auto identify Komf par série", do_batch, done)

    def update_auto_identify_buttons(self) -> None:
        enabled = not self.auto_identify_running
        for name in (
            "btn_auto_identify_selected_series",
            "btn_auto_identify_visible_series",
            "btn_auto_identify_selected_queue",
            "btn_auto_identify_all_queue",
        ):
            button = getattr(self, name, None)
            if button is not None:
                button.setEnabled(enabled)

    # ---------- Navigation file ----------

    def open_selected_queue_item(self) -> None:
        rows = self.queue_selected_rows()
        if rows:
            self.current_queue_index = rows[0]
            self.populate_queue_table()
            self.refresh_current_series_display()
            self.tabs.setCurrentIndex(2)

    def queue_prev(self) -> None:
        if not self.queue_items:
            return
        self.current_queue_index = max(0, self.current_queue_index - 1 if self.current_queue_index >= 0 else 0)
        self.populate_queue_table()
        self.refresh_current_series_display()
        self.tabs.setCurrentIndex(2)

    def queue_next(self, _checked: bool = False, *, auto_search: bool = True) -> None:
        if not self.queue_items:
            return
        previous_index = self.current_queue_index
        self.current_queue_index = min(
            len(self.queue_items) - 1,
            self.current_queue_index + 1 if self.current_queue_index >= 0 else 0,
        )
        self.populate_queue_table()
        self.refresh_current_series_display()
        self.tabs.setCurrentIndex(2)

        # Quand on avance réellement dans la file, on lance directement la recherche
        # pour la nouvelle série. Si on est déjà sur la dernière tâche, rien n'est lancé.
        if auto_search and self.current_queue_index != previous_index and self.current_series():
            self.log_info(f"🔁 Recherche automatique après passage à la tâche suivante : {self.current_series().name}")
            self.search_current_series()

    def refresh_current_series_display(self) -> None:
        s = self.current_series()
        self.search_generation += 1
        self.results_table.setRowCount(0)
        self.clear_results_grid()
        self.current_results = []
        self.selected_result_index = None
        self.last_komf_raw = None
        self.last_komf_search_hint = ""
        self.update_processing_queue_status()
        self.update_selected_result_summary()
        if hasattr(self, "operation_output"):
            self.operation_output.clear()
        if hasattr(self, "btn_show_raw_search"):
            self.btn_show_raw_search.setEnabled(False)
        if hasattr(self, "search_status"):
            self.search_status.setText("Aucune recherche lancée.")
        self.last_simulated_key = None
        self._update_apply_button_state()
        if not s:
            self.current_title.setText("Aucune série sélectionnée")
            self.current_id.setText("")
            self.current_books.setText("")
            self.search_query.setText("")
            self.set_cover_placeholder(self.current_cover, "Aucune\nsérie")
            self.update_processing_queue_status()
            self.update_selected_result_summary()
            return

        self.current_title.setText(s.name)
        self.current_id.setText(f"libraryId={s.library_id} | seriesId={s.id}")
        self.current_books.setText(s.book_count or "non renseigné")
        self.search_query.setText(clean_search_title(s.name))

        cover_candidates: List[str] = []
        try:
            cover_candidates.append(self.komga_series_thumbnail_url(s.id))
        except Exception:
            pass
        if s.cover_url:
            cover_candidates.append(s.cover_url)
        self.load_image_into_label(
            cover_candidates,
            self.current_cover,
            default_base=self.komga_url.text(),
            fallback_text="Aucune\ncouverture",
        )
        self.update_processing_queue_status()
        self.update_selected_result_summary()

    def clean_current_query(self) -> None:
        self.search_query.setText(clean_search_title(self.search_query.text()))

    # ---------- Traitement ----------

    def search_current_series(self) -> None:
        s = self.current_series()
        if not s:
            QMessageBox.warning(self, "Série", "Ouvre une série dans la file d'abord.")
            return
        raw_name = self.search_query.text().strip()
        name = clean_search_title(raw_name)
        if name != raw_name:
            self.search_query.setText(name)
        if not name:
            QMessageBox.warning(self, "Recherche", "Le champ de recherche est vide.")
            return

        library_id = (s.library_id or self.current_library_id()) if self.use_library_id_for_search.isChecked() else None
        library_text = library_id or "sans libraryId"
        variants = build_search_variants(name) if self.use_search_variants.isChecked() else unique_nonempty([name])
        self.search_status.setText(f"Recherche en cours : {name!r} ({library_text})…")
        self.btn_show_raw_search.setEnabled(False)
        self.set_operation_output("Recherche en cours", f"Titre : {name}\nBibliothèque : {library_text}")
        self.log_info(f"🔎 Recherche Komf demandée : {name!r} — {library_text}")
        if self.use_search_variants.isChecked():
            self.log_info("Variantes testées : " + " | ".join(variants))

        self.search_generation += 1
        generation = self.search_generation
        series_id_at_launch = s.id

        def done(output: KomfSearchOutput) -> None:
            current = self.current_series()
            if generation != self.search_generation or not current or current.id != series_id_at_launch:
                self.log_info(f"ℹ️ Résultat de recherche ignoré : la tâche courante a changé depuis le lancement ({name!r}).")
                return
            results = output.results
            self.current_results = results
            self.last_komf_raw = output.raw
            self.last_komf_search_hint = output.searched_url_hint
            self.btn_show_raw_search.setEnabled(True)
            self.populate_results_table()
            self.last_simulated_key = None
            self._update_apply_button_state()
            if output.warnings:
                self.log_info("⚠️ Avertissement(s) Komf/provider :\n" + "\n".join(output.warnings[:6]))

            if results:
                typed = sum(1 for result in results if result.media_type)
                linked = sum(1 for result in results if result.provider_url)
                suffix = []
                if typed:
                    suffix.append(f"{typed} avec type")
                if linked:
                    suffix.append(f"{linked} avec lien")
                if output.warnings:
                    suffix.append("recherche partielle : provider en erreur")
                suffix_text = " — " + ", ".join(suffix) if suffix else ""
                self.search_status.setText(
                    f"{len(results)} résultat(s) trouvé(s) pour {name!r} — {library_text}.{suffix_text}"
                )
                self.log_info(f"✅ Recherche Komf : {len(results)} résultat(s) pour {name!r} — {library_text}{suffix_text}")
            else:
                if output.warnings:
                    detail = "\n".join(output.warnings[:8])
                    self.search_status.setText(
                        "Recherche Komf bloquée ou incomplète côté provider externe. "
                        "Le script n'a pas planté ; consulte 'Réponse brute' pour le détail. "
                        "Si AniList apparaît dans l'erreur, désactive ce provider dans Komf le temps que son API revienne."
                    )
                    self.set_operation_output(
                        "⚠️ Recherche Komf sans résultat exploitable",
                        detail + "\n\nEssais effectués :\n" + output.searched_url_hint,
                    )
                else:
                    self.search_status.setText(
                        "Aucun résultat Komf. Aucune action n'a été faite. "
                        "Essaie un titre plus court/officiel, décoche 'Utiliser libraryId', ou regarde la réponse brute."
                    )
                self.log_info(f"⚠️ Recherche Komf : 0 résultat pour {name!r} — {library_text}")
                self.log_info("URL(s) testée(s) :\n" + output.searched_url_hint)

        self.run_worker(
            "Recherche Komf",
            lambda: self.komf_api().search_raw(
                name,
                library_id=library_id,
                use_variants=self.use_search_variants.isChecked(),
                series_id=s.id,
            ),
            done,
        )

    def populate_results_table(self) -> None:
        self.selected_result_index = None
        self.result_count_label.setText(f"{len(self.current_results)} résultat(s)")
        self.results_table.blockSignals(True)
        self.results_table.setRowCount(0)
        self.results_table.clearSelection()
        self.results_table.blockSignals(False)
        self.clear_results_grid()

        if not self.current_results:
            self.results_table.insertRow(0)
            self.results_table.setRowHeight(0, 70)
            self.results_table.setItem(0, 0, QTableWidgetItem("—"))
            self.results_table.setItem(0, 1, QTableWidgetItem("—"))
            self.results_table.setItem(0, 2, QTableWidgetItem("Aucun résultat"))
            self.results_table.setItem(0, 3, QTableWidgetItem("—"))
            self.results_table.setItem(0, 4, QTableWidgetItem("—"))
            self.results_table.setItem(0, 5, QTableWidgetItem("—"))
            self.results_table.setItem(0, 6, QTableWidgetItem("Regarde 'Réponse brute' pour voir ce que Komf a réellement renvoyé."))

            empty = QLabel("Aucun résultat\n\nEssaie un titre plus court/officiel, ou décoche 'Utiliser libraryId'.")
            empty.setAlignment(Qt.AlignCenter)
            empty.setWordWrap(True)
            empty.setMinimumHeight(180)
            empty.setStyleSheet("border: 1px dashed #555; color: #aaa; background: #242424; padding: 16px;")
            self.results_grid_layout.addWidget(empty, 0, 0)
            self.update_results_view_mode()
            self.update_selected_result_summary()
            self.set_operation_output("Recherche terminée", "Aucun résultat Komf pour cette requête.")
            return

        viewport_width = 1000
        try:
            viewport_width = max(300, self.results_grid_scroll.viewport().width())
        except Exception:
            pass
        columns = max(1, min(7, viewport_width // 205))

        for row, r in enumerate(self.current_results):
            self.results_table.insertRow(row)
            self.results_table.setRowHeight(row, 170)

            cover_label = self.make_cover_label("Aucune\ncouverture", width=105, height=155)
            self.results_table.setCellWidget(row, 0, cover_label)
            self.load_image_into_label(
                r.cover_url,
                cover_label,
                default_base=self.komf_url.text(),
                fallback_text="Aucune\ncouverture",
            )

            provider_button = QPushButton(r.provider)
            provider_button.setEnabled(bool(r.provider_url))
            provider_button.setToolTip(r.provider_url or "Aucun lien provider fourni")
            provider_button.clicked.connect(lambda _checked=False, result=r, idx=row: self.open_result_provider_url(result, idx))
            self.results_table.setCellWidget(row, 1, provider_button)
            self.results_table.setItem(row, 2, QTableWidgetItem(r.title))
            self.results_table.setItem(row, 3, QTableWidgetItem(r.media_type or "—"))
            self.results_table.setItem(row, 4, QTableWidgetItem(display_volume_count(r.volume_count)))
            self.results_table.setItem(row, 5, QTableWidgetItem(r.provider_series_id))
            self.results_table.setItem(row, 6, QTableWidgetItem(r.details))

            card = self.make_result_card(row, r)
            self.result_cards.append(card)
            self.results_grid_layout.addWidget(card, row // columns, row % columns)

        self.update_results_view_mode()
        self.update_result_selection_styles()
        self.update_selected_result_summary()
        if self.current_results:
            self.set_operation_output("Recherche terminée", f"{len(self.current_results)} résultat(s) trouvé(s). Sélectionne une carte, puis simule ou applique si le mode simulation est décoché.")

    def show_last_raw_search(self) -> None:
        if self.last_komf_raw is None:
            QMessageBox.information(self, "Réponse brute", "Aucune réponse brute disponible. Lance d'abord une recherche Komf.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Réponse brute Komf")
        dialog.resize(1100, 750)
        layout = QVBoxLayout(dialog)
        info = QLabel(self.last_komf_search_hint or "Réponse brute Komf")
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(info)
        text = QTextEdit()
        text.setReadOnly(True)
        if isinstance(self.last_komf_raw, str):
            text.setPlainText(self.last_komf_raw)
        else:
            text.setPlainText(json.dumps(self.last_komf_raw, ensure_ascii=False, indent=2))
        layout.addWidget(text, 1)
        close_btn = QPushButton("Fermer")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)
        dialog.exec()

    def selected_result(self) -> Optional[KomfResult]:
        if self.selected_result_index is not None and 0 <= self.selected_result_index < len(self.current_results):
            return self.current_results[self.selected_result_index]
        rows = sorted({idx.row() for idx in self.results_table.selectedIndexes()})
        if rows and 0 <= rows[0] < len(self.current_results):
            return self.current_results[rows[0]]
        return None

    def on_result_selection_changed(self) -> None:
        rows = sorted({idx.row() for idx in self.results_table.selectedIndexes()})
        if rows and 0 <= rows[0] < len(self.current_results):
            self.select_result_index(rows[0], sync_table=False)
        else:
            self.select_result_index(None, sync_table=False)

    def simulation_key(self, s: SeriesItem, r: KomfResult) -> Tuple[str, str, str]:
        return (s.id, r.provider, r.provider_series_id)

    def build_identify_payload(self, s: SeriesItem, r: KomfResult) -> Dict[str, str]:
        return {
            "libraryId": s.library_id or self.current_library_id(),
            "seriesId": s.id,
            "provider": provider_for_identify(r.provider),
            "providerSeriesId": r.provider_series_id,
        }

    def simulate_selected_result(self) -> None:
        s = self.current_series()
        r = self.selected_result()
        if not s or not r:
            QMessageBox.warning(self, "Simulation", "Sélectionne une série et un résultat Komf.")
            return
        payload = self.build_identify_payload(s, r)
        self.log_json("🧪 SIMULATION — aucune écriture. Payload qui serait envoyé à Komf /komga/identify :", payload)
        extra = []
        if r.media_type:
            extra.append(f"type={r.media_type}")
        if r.provider_url:
            extra.append(f"lien={r.provider_url}")
        extra_text = " | " + " | ".join(extra) if extra else ""
        self.log_info(f"Résultat choisi : {r.provider} — {r.title} — {r.provider_series_id}{extra_text}")
        self.set_operation_output(
            "🧪 Simulation — aucune écriture",
            self.make_operation_payload_text("Simulation", s, r, payload),
        )
        self.last_simulated_key = self.simulation_key(s, r)
        self._update_apply_button_state()

    def _update_apply_button_state(self) -> None:
        if not hasattr(self, "btn_apply"):
            return
        enabled = False
        if not self.simulation_mode.isChecked():
            s = self.current_series()
            r = self.selected_result()
            if s and r:
                status = fold_text(safe_str(s.raw.get("_queue_status", "")))
                already_processing = (
                    status.startswith("en file komf")
                    or status.startswith("application komf en cours")
                    or status.startswith("applique")
                )
                enabled = not already_processing
        self.btn_apply.setEnabled(enabled)
        if hasattr(self, "btn_side_apply"):
            self.btn_side_apply.setEnabled(enabled)
        self.update_apply_queue_status()

    def apply_selected_result(self) -> None:
        s = self.current_series()
        r = self.selected_result()
        if not s or not r:
            return
        if self.simulation_mode.isChecked():
            QMessageBox.warning(self, "Mode simulation", "Décoche le mode simulation pour appliquer réellement.")
            return
        payload = self.build_identify_payload(s, r)
        self.enqueue_apply_job(s, r, payload)

    def enqueue_apply_job(self, s: SeriesItem, r: KomfResult, payload: Dict[str, str]) -> None:
        """Ajoute l'identification Komf à une file séquentielle et libère l'UI immédiatement."""
        self.apply_job_sequence += 1
        job = ApplyJob(
            job_id=self.apply_job_sequence,
            series=s,
            result=r,
            payload=dict(payload),
        )
        self.apply_jobs_pending.append(job)
        self.apply_jobs_all.append(job)
        job.status = "en attente"
        job.detail = "En attente de traitement Komf"
        s.raw["_queue_status"] = f"en file Komf: {r.provider}"
        self.last_simulated_key = None
        self.populate_queue_table()
        self.update_apply_queue_status()
        self.log_json(
            f"📥 Application Komf mise en file #{job.job_id} — passage immédiat à la suite",
            payload,
        )
        self.set_operation_output(
            "📥 Application mise en file Komf",
            self.make_operation_payload_text("Application réelle mise en file", s, r, payload)
            + "\n\n➡️ L'UI passe à la tâche suivante sans attendre la réponse Komf.",
        )
        self.advance_after_enqueue(s)
        self.start_next_apply_job()

    def advance_after_enqueue(self, queued_series: SeriesItem) -> None:
        current_index = self.current_queue_index
        if 0 <= current_index < len(self.queue_items) and self.queue_items[current_index].id == queued_series.id:
            if current_index < len(self.queue_items) - 1:
                self.queue_next()
            else:
                self.log_info(f"📌 Application mise en file pour la dernière tâche : {queued_series.name}")
        self._update_apply_button_state()

    def start_next_apply_job(self) -> None:
        if self.apply_job_running is not None or not self.apply_jobs_pending:
            self.update_apply_queue_status()
            return

        job = self.apply_jobs_pending.pop(0)
        self.apply_job_running = job
        job.status = "en cours"
        job.detail = "Appel Komf /komga/identify en cours"
        job.series.raw["_queue_status"] = f"application Komf en cours: {job.result.provider}"
        self.populate_queue_table()
        self.update_apply_queue_status()
        self.log_info(f"⏳ Application Komf en arrière-plan #{job.job_id} : {job.series.name} → {job.result.provider}")

        def do_apply() -> Tuple[bool, Any, str]:
            try:
                response = self.komf_api().identify(
                    job.payload["libraryId"],
                    job.payload["seriesId"],
                    job.payload["provider"],
                    job.payload["providerSeriesId"],
                )
                return True, response, ""
            except Exception:
                return False, None, traceback.format_exc()

        def done(result: Tuple[bool, Any, str], completed_job: ApplyJob = job) -> None:
            success, response, trace = result
            self.apply_job_running = None
            if success:
                completed_job.status = "OK"
                completed_job.detail = "Application Komf terminée"
                completed_job.response = response
                completed_job.series.raw["_queue_status"] = f"appliqué: {completed_job.result.provider}"
                self.apply_jobs_done += 1
                self.log_json(f"✅ Application Komf effectuée #{completed_job.job_id}", completed_job.payload)
                self.log_info(f"✅ Application Komf terminée : {completed_job.series.name} → {completed_job.result.provider}")
            else:
                completed_job.status = "échec"
                completed_job.detail = (trace.strip().splitlines()[-1] if trace.strip() else "Échec Komf")
                completed_job.error_trace = trace
                completed_job.series.raw["_queue_status"] = f"échec Komf: {completed_job.result.provider}"
                self.apply_jobs_failed += 1
                self.log_info(f"❌ Application Komf échouée #{completed_job.job_id} : {completed_job.series.name}\n{trace}")

            self.populate_queue_table()
            self.update_apply_queue_status()
            self._update_apply_button_state()
            self.start_next_apply_job()

        self.run_worker(
            f"Application Komf /komga/identify #{job.job_id}",
            do_apply,
            done,
            show_log=False,
            show_error_popup=False,
        )

    def skip_current_series(self) -> None:
        s = self.current_series()
        if not s:
            return
        s.raw["_queue_status"] = "ignoré"
        self.populate_queue_table()
        self.log_info(f"⏭️ Série ignorée : {s.name}")
        self.set_operation_output("⏭️ Série ignorée", s.name)
        self.queue_next()

    def closeEvent(self, event: Any) -> None:
        self._save_local_config_silent()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
