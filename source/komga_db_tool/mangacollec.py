from __future__ import annotations

import csv
import io
import json
import os
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class MangaCollecRelease:
    series: str
    volume: str
    release_date: str


def normalize_title(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold().replace("&", " et ")
    tokens = re.sub(r"[^a-z0-9]+", " ", text).split()
    articles = {"a", "an", "l", "la", "le", "les", "the", "un", "une"}
    while len(tokens) > 1 and tokens[0] in articles:
        tokens.pop(0)
    while len(tokens) > 1 and tokens[-1] in articles:
        tokens.pop()
    return " ".join(tokens)


def title_similarity(left: Any, right: Any) -> float:
    a = normalize_title(left)
    b = normalize_title(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _row_value(row: dict[str, Any], *names: str) -> str:
    folded = {str(key).strip().casefold(): value for key, value in row.items()}
    for name in names:
        value = folded.get(name.casefold())
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _parse_rows(data: bytes, filename: str = "") -> list[dict[str, Any]]:
    text = data.decode("utf-8-sig")
    suffix = Path(filename).suffix.casefold()
    if suffix == ".json" or (not suffix and text.lstrip().startswith(("[", "{"))):
        payload = json.loads(text)
        if isinstance(payload, dict):
            payload = payload.get("releases") or payload.get("rows") or payload.get("data")
        if not isinstance(payload, list):
            raise ValueError("Le JSON MangaCollec doit contenir une liste de sorties")
        return [row for row in payload if isinstance(row, dict)]
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";"
    return list(csv.DictReader(io.StringIO(text), delimiter=delimiter))


def parse_export(data: bytes, filename: str = "") -> tuple[list[MangaCollecRelease], dict[str, Any]]:
    if not data:
        raise ValueError("Fichier MangaCollec vide")
    rows = _parse_rows(data, filename)
    releases: list[MangaCollecRelease] = []
    seen: set[tuple[str, str, str]] = set()
    invalid = duplicates = 0
    for row in rows:
        series = _row_value(row, "serie", "série", "series")
        volume = _row_value(row, "tome", "volume", "number", "numero", "numéro")
        release_date = _row_value(row, "date", "release_date", "releaseDate")
        try:
            parsed_date = date.fromisoformat(release_date)
        except ValueError:
            invalid += 1
            continue
        if not series or not volume:
            invalid += 1
            continue
        key = (normalize_title(series), volume.casefold(), parsed_date.isoformat())
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        releases.append(MangaCollecRelease(series, volume, parsed_date.isoformat()))
    if not releases:
        raise ValueError("Aucune sortie MangaCollec valide trouvée dans le fichier")
    releases.sort(key=lambda item: (item.release_date, normalize_title(item.series), _volume_sort_key(item.volume)))
    return releases, {
        "input_rows": len(rows),
        "release_count": len(releases),
        "series_count": len({normalize_title(item.series) for item in releases}),
        "duplicates": duplicates,
        "invalid": invalid,
        "date_min": releases[0].release_date,
        "date_max": releases[-1].release_date,
    }


def _volume_sort_key(value: str) -> tuple[int, float, str]:
    match = re.search(r"\d+(?:[.,]\d+)?", str(value or ""))
    if not match:
        return (1, 0.0, str(value or "").casefold())
    try:
        return (0, float(match.group(0).replace(",", ".")), str(value or "").casefold())
    except ValueError:
        return (1, 0.0, str(value or "").casefold())


def _alternate_titles(metadata: dict[str, Any]) -> list[str]:
    values = metadata.get("alternateTitles") or []
    if not isinstance(values, list):
        values = [values]
    result: list[str] = []
    for value in values:
        title = value.get("title") if isinstance(value, dict) else value
        if str(title or "").strip():
            result.append(str(title).strip())
    return result


def _series_identity(row: Any) -> tuple[str, str, dict[str, Any], list[str]]:
    if isinstance(row, dict):
        series_id = str(row.get("id") or "")
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        title = str(metadata.get("title") or row.get("title") or row.get("name") or "")
    else:
        series_id = str(getattr(row, "id", "") or "")
        metadata = getattr(row, "metadata", {}) or {}
        title = str(metadata.get("title") or getattr(row, "title", "") or getattr(row, "name", "") or "")
    names = [title, str(metadata.get("titleSort") or ""), *_alternate_titles(metadata)]
    return series_id, title, metadata, [name for name in names if name]


class MangaCollecStore:
    def __init__(self, root: str | Path = ".komga_db_tool_cache/mangacollec") -> None:
        self.root = Path(root)
        self.catalog_path = self.root / "catalog.json"
        self.mappings_path = self.root / "mappings.json"

    @staticmethod
    def _atomic_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)

    def import_bytes(self, data: bytes, filename: str = "") -> dict[str, Any]:
        releases, summary = parse_export(data, filename)
        payload = {
            "source_filename": Path(filename).name,
            "summary": summary,
            "releases": [asdict(item) for item in releases],
        }
        self._atomic_json(self.catalog_path, payload)
        return self.status()

    def import_file(self, path: str | Path) -> dict[str, Any]:
        source = Path(path)
        return self.import_bytes(source.read_bytes(), source.name)

    def _catalog_payload(self) -> dict[str, Any]:
        if not self.catalog_path.is_file():
            return {}
        payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}

    def releases(self) -> list[MangaCollecRelease]:
        rows = self._catalog_payload().get("releases") or []
        result: list[MangaCollecRelease] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                result.append(MangaCollecRelease(
                    str(row.get("series") or ""),
                    str(row.get("volume") or ""),
                    date.fromisoformat(str(row.get("release_date") or "")).isoformat(),
                ))
            except ValueError:
                continue
        return result

    def status(self) -> dict[str, Any]:
        payload = self._catalog_payload()
        stat = self.catalog_path.stat() if self.catalog_path.is_file() else None
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        return {
            "configured": stat is not None,
            "filename": str(payload.get("source_filename") or ""),
            "updated_at": stat.st_mtime if stat is not None else 0,
            **summary,
            "mapping_count": len(self.mappings()),
        }

    def mappings(self) -> dict[str, dict[str, str]]:
        if not self.mappings_path.is_file():
            return {}
        payload = json.loads(self.mappings_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}

    def save_mapping(self, source_title: str, series_id: str, komga_title: str = "") -> dict[str, str]:
        key = normalize_title(source_title)
        if not key or not series_id:
            raise ValueError("Série MangaCollec et série Komga requises")
        mappings = self.mappings()
        mappings[key] = {
            "source_title": str(source_title).strip(),
            "series_id": str(series_id).strip(),
            "komga_title": str(komga_title).strip(),
        }
        self._atomic_json(self.mappings_path, mappings)
        return mappings[key]

    def delete_mapping(self, source_title: str) -> bool:
        mappings = self.mappings()
        removed = mappings.pop(normalize_title(source_title), None) is not None
        if removed:
            self._atomic_json(self.mappings_path, mappings)
        return removed

    def grouped_releases(self, *, today: date | None = None) -> dict[str, list[MangaCollecRelease]]:
        floor = today or date.today()
        grouped: dict[str, list[MangaCollecRelease]] = {}
        for item in self.releases():
            if date.fromisoformat(item.release_date) < floor:
                continue
            grouped.setdefault(normalize_title(item.series), []).append(item)
        for values in grouped.values():
            values.sort(key=lambda item: (item.release_date, _volume_sort_key(item.volume)))
        return grouped

    def match_rows(self, komga_series: Iterable[Any]) -> list[dict[str, Any]]:
        grouped = self.grouped_releases()
        mappings = self.mappings()
        candidates = [_series_identity(row) for row in komga_series]
        by_id = {series_id: (title, names) for series_id, title, _metadata, names in candidates if series_id}
        rows: list[dict[str, Any]] = []
        for key, releases in grouped.items():
            source_title = releases[0].series
            saved = mappings.get(key) or {}
            saved_id = str(saved.get("series_id") or "")
            if saved_id and saved_id in by_id:
                title, _names = by_id[saved_id]
                best_id, best_title, best_score, status = saved_id, title, 1.0, "Validé manuellement"
            else:
                scored: list[tuple[float, str, str]] = []
                for series_id, title, _metadata, names in candidates:
                    score = max((title_similarity(source_title, name) for name in names), default=0.0)
                    scored.append((score, series_id, title))
                scored.sort(key=lambda item: (item[0], item[2].casefold()), reverse=True)
                best_score, best_id, best_title = scored[0] if scored else (0.0, "", "")
                runner_up = scored[1][0] if len(scored) > 1 else 0.0
                exact_count = sum(1 for score, _id, _title in scored if score == 1.0)
                if best_score == 1.0 and exact_count == 1:
                    status = "Exact automatique"
                elif best_score >= 0.90 and best_score - runner_up >= 0.05:
                    status = "Match sûr automatique"
                else:
                    best_id, best_title, best_score = "", "", 0.0
                    status = "Sans correspondance sûre"
            rows.append({
                "source_title": source_title,
                "releases": [asdict(item) for item in releases],
                "next_volume": releases[0].volume,
                "next_date": releases[0].release_date,
                "komga_series_id": best_id,
                "komga_title": best_title,
                "score": round(best_score, 3),
                "status": status,
                "usable": status in {"Validé manuellement", "Exact automatique", "Match sûr automatique"},
            })
        return sorted(rows, key=lambda row: (row["next_date"], normalize_title(row["source_title"])))

    def next_releases_by_series(self, komga_series: Iterable[Any]) -> dict[str, dict[str, Any]]:
        """Return only mutually safe automatic or manually validated matches."""
        series_rows = list(komga_series)
        matched: dict[str, list[dict[str, Any]]] = {}
        for row in self.match_rows(series_rows):
            series_id = str(row.get("komga_series_id") or "")
            if not series_id or not row.get("usable"):
                continue
            matched.setdefault(series_id, []).append(row)
        result: dict[str, dict[str, Any]] = {}
        for series_id, rows in matched.items():
            row = min(rows, key=lambda item: (str(item.get("next_date") or ""), _volume_sort_key(str(item.get("next_volume") or ""))))
            release = MangaCollecRelease(
                str(row.get("source_title") or ""),
                str(row.get("next_volume") or ""),
                str(row.get("next_date") or ""),
            )
            result[series_id] = {
                "release": release,
                "match_status": str(row.get("status") or ""),
                "source_key": normalize_title(release.series),
                "score": float(row.get("score") or 0.0),
            }
        return result

    def next_release_for_series(self, series: Any) -> tuple[MangaCollecRelease | None, str, str]:
        series_id, title, _metadata, names = _series_identity(series)
        grouped = self.grouped_releases()
        mappings = self.mappings()
        mapped_keys = [key for key, value in mappings.items() if str(value.get("series_id") or "") == series_id]
        mapped = [(grouped[key][0], key) for key in mapped_keys if key in grouped]
        if mapped:
            release, key = min(mapped, key=lambda item: (item[0].release_date, _volume_sort_key(item[0].volume)))
            return release, "Validé manuellement", key
        exact_keys = [
            key for key in grouped
            if any(normalize_title(name) == key for name in names)
        ]
        if exact_keys:
            release, key = min(
                ((grouped[key][0], key) for key in exact_keys),
                key=lambda item: (item[0].release_date, _volume_sort_key(item[0].volume)),
            )
            return release, "Exact automatique", key
        return None, ("Correspondance ambiguë" if len(exact_keys) > 1 else "Aucune correspondance validée"), ""
