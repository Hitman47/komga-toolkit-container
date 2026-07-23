from __future__ import annotations

import difflib
import html
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib import parse, request

APP_USER_AGENT = "komga-db-tool/0.5.1 bedetheque-adapter"
BASE_URL = "https://www.bedetheque.com"

SERIE_LIST = re.compile(r'<a\s+href="https://www\.bedetheque\.com/(serie-[^"]+?)">.*?libelle">(.*?)\r', re.I | re.S)
REVUE_LIST = re.compile(r'<a\s+href="https://www\.bedetheque\.com/(revue-[^"]+?)">.*?libelle">(.*?)\r', re.I | re.S)
SERIE_QSERIE = re.compile(r'<h1>\s*<a href="serie-[^\.]+\.html">([^"<>]+)</a>', re.I | re.S)
SERIE_GENRE = re.compile(r'<span\s+class="style">(.*?)<', re.I | re.S)
SERIE_RESUME = re.compile(r'<meta\s+name="description"\s+content="(.*?)"\s*/?>', re.I | re.S)
SERIE_STATUS = re.compile(r'<h3>.*?<span><i\s+class="icon-info-sign"></i>(.*?)</span>', re.I | re.S)
SERIE_LANGUE = re.compile(r'class="flag"/>(.*?)</span>', re.I | re.S)
SERIE_COUNT = re.compile(r'class="icon-book"></i>\s*(\d+)', re.I | re.S)
ALBUM_LIST = re.compile(r'<label>([^<]*?)<span\s+class="numa">(.*?)</span.*?<a\s+href="(.*?)".*?title=.+?">(.+?)<', re.I | re.S)
ALBUM_FIRST = re.compile(r'class="titre"\s+href="(.+?)".+?<span class="numa">.*?</span>', re.I | re.S)
ALBUM_TITLE_META = re.compile(r'<meta\s+property="og:title"\s+content="(.*?)"\s*/?>', re.I | re.S)
ALBUM_COVER = re.compile(r'<meta\s+property="og:image"\s+content="(.*?)"\s*/?>', re.I | re.S)
ALBUM_COVER_LEGACY = re.compile(r'<meta\s+property="og:title".*?="https:(.*?)"', re.I | re.S)
ALBUM_RESUME = re.compile(r'<meta\s+name="description"\s+content="(.*?)"', re.I | re.S)

FIELD_PATTERNS = {
    "publisher": re.compile(r'<label>Editeur\s*:\s?</label>(.*?)</', re.I | re.S),
    "collection": re.compile(r'<label>Collection\s*:\s?</label>(?:<a href.+?>)*([^><]+?)<', re.I | re.S),
    "isbn": re.compile(r'<label>.*?ISBN\s*:\s*</label.*?>([^<]*?)</', re.I | re.S),
    "pages": re.compile(r'<label>Planches\s*:\s?</label>(\d*?)</', re.I | re.S),
    "format": re.compile(r'<label>Format\s*:\s?</label>.*?(.+?)</', re.I | re.S),
    "infoEdition": re.compile(r'<em>Info\s.*?dition\s*:\s?</em>\s?(.*?)<', re.I | re.S),
}
DATE_PATTERNS = [
    re.compile(r'<label>D.pot L.gal\s*:\s?</label>(?P<month>[\d|-]{0,2})/?(?P<year>[\d]{2,4})?', re.I | re.S),
    re.compile(r'<label>Achev.*?\s*:\s?</label>(?P<month>[\d|-]{0,2})/?(?P<year>[\d]{2,4})?<', re.I | re.S),
]
AUTHOR_PATTERNS = {
    "writer": re.compile(r'<label>sc.*?nario\s*:</label>(.*?)<label>[^&]', re.I | re.S),
    "penciller": re.compile(r'<label>dessin\s*:</label>(.*?)<label>[^&]', re.I | re.S),
    "colorist": re.compile(r'<label>couleurs\s*:</label>(.*?)<label>[^&]', re.I | re.S),
    "cover": re.compile(r'<label>couverture\s*:</label>(.*?)<label>[^&]', re.I | re.S),
    "letterer": re.compile(r'<label>lettrage\s*:</label>(.*?)<label>[^&]', re.I | re.S),
    "inker": re.compile(r'<label>encrage\s*:</label>(.*?)<label>[^&]', re.I | re.S),
}
AUTHOR_NAME = re.compile(r'">(.*?)</', re.I | re.S)

LANG_MAP = {"fr": "fr", "al": "de", "an": "en", "it": "it", "es": "es", "ne": "nl", "po": "pt", "ja": "ja"}
STATUS_MAP = {
    "finie": "ENDED",
    "termin": "ENDED",
    "one shot": "ENDED",
    "cours": "ONGOING",
    "aband": "ABANDONED",
    "pause": "HIATUS",
    "hiatus": "HIATUS",
}


@dataclass
class BedethequeSearchResult:
    kind: str
    title: str
    url: str
    source: str = "bedetheque"


@dataclass
class BedethequeCandidate:
    source_url: str
    series_title: str = ""
    album_title: str = ""
    album_number: str = ""
    album_url: str = ""
    cover_url: str = ""
    series_metadata: Dict[str, Any] = field(default_factory=dict)
    book_metadata: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^<>]+?>", "", text or "", flags=re.I | re.S)


def _clean(text: Any) -> str:
    if text is None:
        return ""
    out = html.unescape(str(text))
    out = _strip_tags(out)
    out = out.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    out = re.sub(r"\s+", " ", out).strip(" \u00a0-–—:;")
    return out


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in normalized if not unicodedata.combining(c)).lower()


def normalize_volume_number(value: Any) -> str:
    """Normalize Komga/Bedetheque numbering for matching.

    Examples accepted as the same target: ``01``, ``Tome 01``, ``Vol. 1``,
    ``Volume 1``, ``N°1`` and ``#001``. Alphanumeric specials are preserved
    after cleaning so they can still be matched manually.
    """
    text = _fold(str(value or "")).strip()
    text = re.sub(r"\b(?:tome|vol(?:ume)?|livre|book|album|n[°o]?|numero|num|#)\b", " ", text, flags=re.I)
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    if not text:
        return ""
    parts = text.split()
    for part in parts:
        if part.isdigit():
            return str(int(part))
    if text.isdigit():
        return str(int(text))
    return text


def title_similarity(left: str, right: str) -> float:
    a = re.sub(r"[^a-z0-9]+", " ", _fold(left)).strip()
    b = re.sub(r"[^a-z0-9]+", " ", _fold(right)).strip()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def match_album_rows(komga_books: List[Any], bedetheque_albums: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Propose safe Komga book ↔ Bedetheque album matches.

    The result is intentionally descriptive, not authoritative: the UI displays a
    confidence/reason column and lets the user change the selected match before
    applying anything.
    """
    matches: List[Dict[str, Any]] = []
    used_album_indexes: set[int] = set()
    for book_index, book in enumerate(komga_books):
        book_number = normalize_volume_number(getattr(book, "number", "") or (getattr(book, "metadata", {}) or {}).get("number", ""))
        book_title = getattr(book, "title", "") or (getattr(book, "metadata", {}) or {}).get("title", "")

        best_album_index = -1
        best_score = 0.0
        best_reason = "Non matché"

        for album_index, album in enumerate(bedetheque_albums):
            album_number = normalize_volume_number(album.get("number", ""))
            album_title = album.get("title", "")
            score = 0.0
            reason = ""
            if book_number and album_number and book_number == album_number:
                score = 1.0
                reason = "Exact numéro"
            else:
                sim = title_similarity(book_title, album_title)
                if sim >= 0.88:
                    score = sim
                    reason = "Titre proche"
                elif sim >= 0.72:
                    score = sim * 0.8
                    reason = "Ambigu"
            if score > best_score:
                best_score = score
                best_album_index = album_index
                best_reason = reason

        if best_album_index in used_album_indexes and best_reason == "Exact numéro":
            best_reason = "Ambigu"
        if best_album_index >= 0:
            used_album_indexes.add(best_album_index)
        matches.append({
            "book_index": book_index,
            "album_index": best_album_index,
            "confidence": best_reason,
            "score": round(best_score, 3),
            "book_number_norm": book_number,
        })

    # Keep unmatched Bedetheque albums visible for manual decisions.
    for album_index, _album in enumerate(bedetheque_albums):
        if album_index not in used_album_indexes:
            matches.append({
                "book_index": -1,
                "album_index": album_index,
                "confidence": "Album Bedetheque non associé",
                "score": 0.0,
                "book_number_norm": "",
            })
    return matches


def _full_url(url: str) -> str:
    text = (url or "").strip()
    if not text:
        return ""
    if text.startswith("//"):
        return "https:" + text
    if text.startswith("http://") or text.startswith("https://"):
        return text
    if text.startswith("/"):
        return BASE_URL + text
    return BASE_URL + "/" + text


def _is_album_url(url: str) -> bool:
    """Return True for the current Bedetheque album URL families.

    Older pages and some plugin-era code use ``album-*.html`` links, while
    current Bedetheque series pages often expose album pages as
    ``BD-...-123456.html``. The previous adapter accepted only ``album-`` and
    therefore returned an empty album table for many valid series.
    """
    full = _full_url(url)
    if not full:
        return False
    filename = parse.urlsplit(full).path.rsplit("/", 1)[-1].lower()
    return filename.startswith("album-") or filename.startswith("bd-")


def _attr_value(tag: str, attr: str) -> str:
    pattern = r"\b" + re.escape(attr) + r"\s*=\s*([\"'])((?:\\.|(?!\1).)*?)\1"
    m = re.search(pattern, tag or "", flags=re.I | re.S)
    return html.unescape(m.group(2)) if m else ""


def _numa_from_html(fragment: str) -> str:
    m = re.search(r"<span[^>]*class=[\"'][^\"']*numa[^\"']*[\"'][^>]*>(.*?)</span>", fragment or "", flags=re.I | re.S)
    return _clean(m.group(1)) if m else ""


def _extract_first(regex: re.Pattern[str], page: str) -> str:
    m = regex.search(page or "")
    return _clean(m.group(1)) if m else ""


def _album_title_number_from_text(text: str) -> tuple[str, str]:
    """Return (title, number) from common Bedetheque labels/meta titles."""
    raw = _clean(text)
    if not raw:
        return "", ""
    # Common forms: "Série - Tome 1 - Titre", "Série - 1. Titre",
    # "#1. Titre", "T01 - Titre".
    patterns = [
        r"\b(?:Tome|Vol(?:ume)?|Album|N(?:\u00b0|o)?|#|T)\s*0*([0-9]+[A-Za-z]?)\s+(.+)$",
        r"(?:^|[-–—:])\s*(?:Tome|Vol(?:ume)?|Album|N[°o]?|#|T)\s*0*([0-9]+[A-Za-z]?)\s*[-–—:.]\s*(.+)$",
        r"(?:^|[-–—:])\s*0*([0-9]+[A-Za-z]?)\s*[\.)-]\s*(.+)$",
        r"^\s*0*([0-9]{1,3}[A-Za-z]?)\s+(.{2,})$",
    ]
    for pattern in patterns:
        m = re.search(pattern, raw, flags=re.I)
        if m:
            return _clean(m.group(2)), _clean(m.group(1))
    if " - " in raw:
        return _clean(raw.split(" - ")[-1]), ""
    return raw, ""


def _looks_like_bedetheque_block_page(page: str) -> bool:
    folded = _fold(_strip_tags(page or ""))
    return (
        "vous utilisez sans doute un programme" in folded
        and "ip" in folded
        and ("bloquee" in folded or "bloque" in folded)
    )


def _extract_visible_title(page: str) -> str:
    for pattern in (
        r'<h1[^>]*>(.*?)</h1>',
        r'<h2[^>]*>(.*?)</h2>',
        r'<h3[^>]*class="[^"]*titre[^"]*"[^>]*>(.*?)</h3>',
        r'<div[^>]*class="[^"]*titre[^"]*"[^>]*>(.*?)</div>',
    ):
        m = re.search(pattern, page or "", flags=re.I | re.S)
        if m:
            title = _clean(m.group(1))
            if title:
                return title
    return ""


def _parse_date(page: str) -> str:
    for pattern in DATE_PATTERNS:
        m = pattern.search(page or "")
        if not m:
            continue
        year = (m.group("year") or "").strip()
        month = (m.group("month") or "").strip("-/ ")
        if not year:
            continue
        if len(year) == 2:
            year = "20" + year if int(year) < 40 else "19" + year
        if month and month.isdigit() and 1 <= int(month) <= 12:
            return f"{int(year):04d}-{int(month):02d}-01"
        return f"{int(year):04d}-01-01"
    return ""


def _parse_authors(page: str) -> List[Dict[str, str]]:
    authors: List[Dict[str, str]] = []
    seen = set()
    for role, pattern in AUTHOR_PATTERNS.items():
        m = pattern.search(page or "")
        if not m:
            continue
        block = m.group(1)
        names = [_clean(x) for x in AUTHOR_NAME.findall(block)]
        if not names:
            names = [_clean(block)]
        for name in names:
            if not name or name.startswith("<"):
                continue
            key = (name, role)
            if key in seen:
                continue
            seen.add(key)
            authors.append({"name": name, "role": role})
    return authors


class BedethequeClient:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.last_url = ""

    def _read(self, url: str) -> str:
        fixed = _full_url(url)
        self.last_url = fixed
        req = request.Request(
            fixed,
            headers={
                "User-Agent": APP_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.6",
                "Referer": BASE_URL,
            },
        )
        with request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read()
            page = raw.decode("utf-8", errors="replace")
            if _looks_like_bedetheque_block_page(page):
                raise RuntimeError("HTTP 429 Bedetheque: page de blocage anti-scan")
            return page

    def search(self, query: str) -> List[BedethequeSearchResult]:
        q = _fold(query).strip()
        if not q:
            return []
        url = "/search/tout?" + parse.urlencode({"RechTexte": q, "RechWhere": 0})
        page = self._read(url)
        results: List[BedethequeSearchResult] = []
        seen = set()
        for regex, kind in ((SERIE_LIST, "serie"), (REVUE_LIST, "revue")):
            for m in regex.finditer(page):
                path = m.group(1)
                title = _clean(m.group(2))
                full = _full_url(path)
                if not title or full in seen:
                    continue
                seen.add(full)
                results.append(BedethequeSearchResult(kind=kind, title=title, url=full))
        return results

    def scrape_series(self, url: str) -> BedethequeCandidate:
        page = self._read(url)
        candidate = BedethequeCandidate(source_url=_full_url(url))
        candidate.raw["series_page_url"] = candidate.source_url

        series_title = _extract_first(SERIE_QSERIE, page)
        if not series_title:
            h1 = re.search(r"<h1[^>]*>(.*?)</h1>", page, flags=re.I | re.S)
            series_title = _clean(h1.group(1)) if h1 else ""
        candidate.series_title = series_title

        genre = _extract_first(SERIE_GENRE, page)
        summary = _extract_first(SERIE_RESUME, page)
        summary = re.sub(r"Tout sur la série.*?:\s?", "", summary, flags=re.I).strip()
        status_text = _extract_first(SERIE_STATUS, page)
        lang_text = _extract_first(SERIE_LANGUE, page)
        count = _extract_first(SERIE_COUNT, page)

        sm: Dict[str, Any] = {}
        if series_title:
            sm["title"] = series_title
            sm["titleSort"] = series_title
        if genre:
            sm["genres"] = [genre]
        if summary:
            sm["summary"] = summary
        if status_text:
            folded = _fold(status_text)
            for token, status in STATUS_MAP.items():
                if token in folded:
                    sm["status"] = status
                    break
        if lang_text:
            lang_key = _fold(lang_text)[:2]
            if lang_key in LANG_MAP:
                sm["language"] = LANG_MAP[lang_key]
        if count.isdigit():
            sm["totalBookCount"] = int(count)
        sm.setdefault("links", []).append({"label": "Bedetheque", "url": candidate.source_url})
        candidate.series_metadata = sm

        albums = self._albums_from_series_page(page)
        if not albums:
            for listing_url in self._album_listing_urls_from_page(page, candidate.source_url):
                try:
                    listing_page = self._read(listing_url)
                except Exception:
                    continue
                albums = self._albums_from_series_page(listing_page)
                if albums:
                    candidate.raw["album_listing_page_url"] = _full_url(listing_url)
                    break
        candidate.raw["albums"] = albums[:500]
        return candidate

    def scrape_album(self, album_url: str) -> BedethequeCandidate:
        candidate = BedethequeCandidate(source_url=_full_url(album_url), album_url=_full_url(album_url))
        self._scrape_album_into(candidate, album_url)
        return candidate

    def scrape(self, url: str, album_number: str = "") -> BedethequeCandidate:
        candidate = self.scrape_series(url)
        candidate.raw["searched_album_number"] = album_number
        album_url = self._choose_album(candidate.raw.get("albums", []), album_number)
        if album_url:
            candidate.album_url = album_url
            self._scrape_album_into(candidate, album_url)
        return candidate

    def _album_listing_urls_from_page(self, page: str, current_url: str = "") -> List[str]:
        """Find secondary album-listing pages linked from a Bedetheque series page."""
        urls: List[str] = []
        seen = set()
        for m in re.finditer(r"<a\b[^>]*href=([\"'])(.*?)\1[^>]*>", page or "", flags=re.I | re.S):
            href = html.unescape(m.group(2))
            folded = _fold(href)
            if "album" not in folded and "tome" not in folded:
                continue
            full = _full_url(href)
            if not full or full == _full_url(current_url) or full in seen or _is_album_url(full):
                continue
            seen.add(full)
            urls.append(full)
        return urls[:5]

    def _albums_from_series_page(self, page: str) -> List[Dict[str, str]]:
        albums: List[Dict[str, str]] = []
        seen = set()

        def add_album(url: str, number: str = "", title: str = "", context: str = "") -> None:
            full = _full_url(url)
            if not full or full in seen or not _is_album_url(full):
                return
            seen.add(full)

            raw_title = title or ""
            span_number = _numa_from_html(raw_title) or _numa_from_html(context)
            title_without_num = re.sub(r"<span[^>]*class=[\"'][^\"']*numa[^\"']*[\"'][^>]*>.*?</span>", " ", raw_title, flags=re.I | re.S)
            clean_title, clean_number = _album_title_number_from_text(title_without_num or raw_title)
            if not clean_title:
                clean_title, clean_number = _album_title_number_from_text(_attr_value(context, "title"))
            slug = parse.urlsplit(full).path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            slug = re.sub(r"^(?:album|bd)-", "", slug, flags=re.I)
            slug = re.sub(r"-\d+$", "", slug)
            slug_title, slug_number = _album_title_number_from_text(slug.replace("-", " "))
            if not clean_number and slug_number:
                clean_number = slug_number
            if slug_title and not slug_title.isdigit() and (not clean_title or title_similarity(clean_title, slug_title) < 0.45):
                clean_title = slug_title
            if not clean_title:
                clean_title = _clean(slug.replace("-", " "))

            albums.append({
                "number": _clean(number) or span_number or clean_number,
                "title": clean_title or _clean(title_without_num) or _clean(raw_title),
                "url": full,
                "order": str(len(albums)),
            })

        for m in ALBUM_LIST.finditer(page or ""):
            label, numa, url, title = m.group(1), m.group(2), m.group(3), m.group(4)
            add_album(url, _clean(numa) or _clean(label), title, m.group(0))

        # Current Bedetheque pages commonly expose album URLs as BD-*.html,
        # sometimes with class="titre", sometimes with only a title attribute.
        for m in re.finditer(r"<a\b[^>]*href=([\"'])([^\"']*(?:album-|BD-)[^\"']*?\.html(?:#[^\"']*)?)\1[^>]*>(.*?)</a>", page or "", flags=re.I | re.S):
            tag = m.group(0)
            href = m.group(2)
            body = m.group(3)
            title = _attr_value(tag, "title") or body
            context = (page or "")[max(0, m.start() - 250): min(len(page or ""), m.end() + 250)]
            add_album(href, "", title, context)

        # Plugin-compatible fallback for edition blocks where the anchor is
        # surrounded by cover/title/admin markup.
        for m in re.finditer(
            r"class=[\"']couv[\"'].*?<a\b[^>]*href=([\"'])(.*?)\1.*?class=[\"']titre[\"'][^>]*>(.*?)<div\s+class=[\"']album-admin[\"'].*?id=([\"'])bt-album-(.*?)\4",
            page or "",
            flags=re.I | re.S,
        ):
            href = m.group(2)
            title = m.group(3)
            anchor = "#" + _clean(m.group(5)) if m.group(5) else ""
            add_album(href + anchor if anchor and "#" not in href else href, "", title, m.group(0))

        # Last fallback: collect bare album-like URLs. This keeps the UI useful
        # even when Bedetheque changes surrounding markup; the album scrape will
        # then resolve the exact title/metadata when the row is selected.
        for m in re.finditer(r"([\"'])([^\"']*(?:album-|BD-)[^\"']*?\.html(?:#[^\"']*)?)\1", page or "", flags=re.I | re.S):
            context = (page or "")[max(0, m.start() - 150): min(len(page or ""), m.end() + 150)]
            add_album(m.group(2), "", "", context)

        return albums

    def _choose_album(self, albums: List[Dict[str, str]], album_number: str = "") -> str:
        if not albums:
            return ""
        target = (album_number or "").strip().lower()
        if target:
            for item in albums:
                if (item.get("number") or "").strip().lower() == target:
                    return item.get("url", "")
            for item in albums:
                if target and target in (item.get("number") or "").strip().lower():
                    return item.get("url", "")
        return albums[0].get("url", "")

    def _scrape_album_into(self, candidate: BedethequeCandidate, album_url: str) -> None:
        page = self._read(album_url)
        candidate.raw["album_page_url"] = album_url
        title_meta = _extract_first(ALBUM_TITLE_META, page)
        visible_title = _extract_visible_title(page)
        album_title, number = _album_title_number_from_text(title_meta)
        if not album_title:
            album_title, number = _album_title_number_from_text(visible_title)
        candidate.album_title = album_title
        candidate.album_number = number

        bm: Dict[str, Any] = {}
        if album_title:
            bm["title"] = album_title
        if number:
            bm["number"] = number
        summary = _extract_first(ALBUM_RESUME, page)
        if summary:
            bm["summary"] = summary
        release = _parse_date(page)
        if release:
            bm["releaseDate"] = release
        isbn = _extract_first(FIELD_PATTERNS["isbn"], page)
        if isbn:
            bm["isbn"] = isbn
        publisher = _extract_first(FIELD_PATTERNS["publisher"], page)
        if publisher:
            bm["publisher"] = publisher
        pages = _extract_first(FIELD_PATTERNS["pages"], page)
        if pages.isdigit():
            bm["numberOfPages"] = int(pages)
        collection = _extract_first(FIELD_PATTERNS["collection"], page)
        tags = []
        if collection:
            tags.append(collection)
        info_edition = _extract_first(FIELD_PATTERNS["infoEdition"], page)
        if info_edition:
            tags.append(info_edition)
        if tags:
            bm["tags"] = tags
        authors = _parse_authors(page)
        if authors:
            bm["authors"] = authors
        bm.setdefault("links", []).append({"label": "Bedetheque", "url": album_url})
        candidate.book_metadata = bm

        cover = _extract_first(ALBUM_COVER, page)
        if not cover:
            legacy = ALBUM_COVER_LEGACY.search(page or "")
            cover = "https:" + legacy.group(1) if legacy else ""
        candidate.cover_url = _full_url(cover) if cover else ""
        if candidate.cover_url:
            candidate.raw["cover_url"] = candidate.cover_url

    @staticmethod
    def candidate_to_dict(candidate: BedethequeCandidate) -> Dict[str, Any]:
        return {
            "source_url": candidate.source_url,
            "series_title": candidate.series_title,
            "album_title": candidate.album_title,
            "album_number": candidate.album_number,
            "album_url": candidate.album_url,
            "cover_url": candidate.cover_url,
            "series_metadata": candidate.series_metadata,
            "book_metadata": candidate.book_metadata,
            "raw": candidate.raw,
        }
