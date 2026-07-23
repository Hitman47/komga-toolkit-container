from __future__ import annotations

import difflib
import os
import re
import unicodedata
from collections import Counter
from pathlib import PurePath, PureWindowsPath
from typing import Any, Callable


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("_", " ")).strip()


def _fold(value: str) -> str:
    value = "".join(ch for ch in unicodedata.normalize("NFKD", _text(value).casefold()) if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _same(left: str, right: str) -> bool:
    a, b = _fold(left), _fold(right)
    return bool(a and b and (a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.94))


def _basename(value: Any) -> str:
    text = str(value or "").replace("\\", "/").rstrip("/")
    return PurePath(text).name if text else ""


def _clean_candidate(value: Any) -> str:
    text = PureWindowsPath(_basename(value)).stem
    text = re.sub(r"^(?:\s*\[[^]]+\])+\s*", "", text)
    text = re.sub(r"\s*(?:\(|\[)(?:(?:19|20)\d{2}|FR|EN|VF|VO|RAW|CBZ|CBR|EPUB|PDF|\d+\s*of\s*\d+)(?:\)|\])\s*$", "", text, flags=re.I)
    text = re.sub(r"\s*(?:[-_. ]+(?:t(?:ome)?|vol(?:ume)?|v|ch(?:apter)?|chapitre|#)\s*\d+(?:[.,]\d+)?)\b.*$", "", text, flags=re.I)
    text = re.sub(r"\s+\d{1,4}\s*$", "", text)
    return _text(text).strip(" -_.")


def _series_folder(raw: dict[str, Any]) -> str:
    folder = raw.get("folder")
    if isinstance(folder, dict):
        for key in ("name", "path"):
            if folder.get(key):
                return _clean_candidate(folder[key])
    for key in ("folderName", "path", "url"):
        if raw.get(key):
            return _clean_candidate(raw[key])
    return ""


def _book_values(book: Any) -> list[str]:
    raw = getattr(book, "raw", {}) or {}
    media = raw.get("media") if isinstance(raw.get("media"), dict) else {}
    return [str(value) for value in (
        getattr(book, "title", ""), raw.get("fileName"), raw.get("filename"), raw.get("path"),
        raw.get("filePath"), media.get("fileName"), media.get("filePath"),
    ) if value]


def _file_consensus(books: list[Any]) -> tuple[str, int, int, list[str]]:
    counts: Counter[str] = Counter()
    display: dict[str, str] = {}
    samples: dict[str, list[str]] = {}
    for book in books:
        candidates = [_clean_candidate(value) for value in _book_values(book)]
        candidate = next((value for value in candidates if value), "")
        key = _fold(candidate)
        if not key:
            continue
        counts[key] += 1
        display.setdefault(key, candidate)
        samples.setdefault(key, []).append(str(getattr(book, "title", "")))
    if not counts:
        return "", 0, 0, []
    key, count = counts.most_common(1)[0]
    return display[key], count, sum(counts.values()), samples[key][:5]


def scan_series_fix(api: Any, library_id: str, mode: str, progress: Callable[[int, int, str], None], cancelled: Callable[[], bool]) -> list[dict[str, Any]]:
    rows = api.series(library_id=library_id)
    proposals: list[dict[str, Any]] = []
    for index, series in enumerate(rows, 1):
        if cancelled():
            break
        raw = getattr(series, "raw", {}) or {}
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else getattr(series, "metadata", {})
        title = _text(metadata.get("title") or getattr(series, "title", ""))
        title_sort = _text(metadata.get("titleSort") or "")
        if mode == "sort":
            if title and title != title_sort:
                proposals.append({"series_id": series.id, "title": title, "current": title_sort, "proposed": title, "payload": {"titleSort": title}, "score": 100, "source": "title"})
        else:
            books = api.books(library_id=library_id, series_id=series.id, direct_series_only=True)
            folder = _series_folder(raw)
            candidate, votes, total, samples = _file_consensus(books)
            if mode == "files":
                proposed, source = candidate, "fichiers"
            elif mode == "folder":
                proposed, source = folder, "dossier"
            else:
                proposed, source = (folder, "dossier") if folder else (candidate, "fichiers")
                if folder and candidate and _same(folder, candidate):
                    proposed, source = folder, "dossier + fichiers"
            if proposed and not _same(title, proposed):
                ratio = votes / total if total else 0
                score = 96 if folder and candidate and _same(folder, candidate) else (88 if source.startswith("dossier") else int(55 + ratio * 30 + min(votes, 5)))
                proposals.append({"series_id": series.id, "title": title, "current": title, "proposed": proposed, "payload": {"title": proposed, "titleSort": proposed}, "score": score, "source": source, "samples": samples})
        progress(index, len(rows), title)
    return sorted(proposals, key=lambda row: (-int(row["score"]), str(row["title"]).casefold()))
