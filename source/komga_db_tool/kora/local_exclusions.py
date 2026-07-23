from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Protocol

from .constants import LOCAL_EXCLUSIONS_FILENAME


class HasSeriesIdentity(Protocol):
    id: str
    title: str
    library_name: str


def default_local_exclusions_path() -> Path:
    override = os.getenv("KORA_LOCAL_EXCLUSIONS_PATH")
    if override:
        return Path(override)
    return Path.cwd() / LOCAL_EXCLUSIONS_FILENAME


DEFAULT_TITLE_RULES = [
    {"pattern": "(Univers)", "match_type": "suffix", "enabled": True},
    *[
        {"pattern": title, "match_type": "exact", "enabled": True}
        for title in (
            "Action",
            "Aventure",
            "BD",
            "Comédie",
            "Comics",
            "Drame",
            "Fantasy",
            "Horreur",
            "Mangas",
            "Non-Fiction",
            "Romance",
            "SF",
            "Thriller",
        )
    ],
]
DEFAULT_EXCLUDED_LIBRARY_NAMES = ["Divers", "Magazines"]


def _empty_payload() -> dict[str, Any]:
    return {
        "version": 2,
        "excluded_series_ids": {},
        "title_rules": [dict(rule) for rule in DEFAULT_TITLE_RULES],
        "excluded_library_names": list(DEFAULT_EXCLUDED_LIBRARY_NAMES),
    }


class LocalExclusionsStore:
    """Small local-only exclusion store for temporary Kora workflow skips.

    The store never writes to Komga. It only keeps Komga series IDs in a JSON
    file located in the project directory when the bundled launcher scripts are
    used.
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_local_exclusions_path()

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return _empty_payload()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return _empty_payload()
        if not isinstance(data, dict):
            return _empty_payload()
        data.setdefault("version", 2)
        if not isinstance(data.get("excluded_series_ids"), dict):
            data["excluded_series_ids"] = {}
        if not isinstance(data.get("title_rules"), list):
            data["title_rules"] = [dict(rule) for rule in DEFAULT_TITLE_RULES]
        if not isinstance(data.get("excluded_library_names"), list):
            data["excluded_library_names"] = list(DEFAULT_EXCLUDED_LIBRARY_NAMES)
        return data

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 2,
            "excluded_series_ids": data.get("excluded_series_ids") or {},
            "title_rules": data.get("title_rules") or [],
            "excluded_library_names": data.get("excluded_library_names") or [],
        }
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)

    def ids(self) -> set[str]:
        return {str(x) for x in self.load()["excluded_series_ids"].keys()}

    def is_excluded(self, series_id: str) -> bool:
        return str(series_id or "") in self.ids()

    def entries(self) -> dict[str, dict[str, Any]]:
        return dict(self.load()["excluded_series_ids"])

    def title_rules(self) -> list[dict[str, Any]]:
        return [dict(rule) for rule in self.load()["title_rules"] if isinstance(rule, dict)]

    def excluded_library_names(self) -> list[str]:
        return [str(name) for name in self.load()["excluded_library_names"] if str(name).strip()]

    def set_excluded_library_names(self, names: Iterable[str]) -> None:
        data = self.load()
        data["excluded_library_names"] = sorted(
            {str(name).strip() for name in names if str(name).strip()},
            key=str.casefold,
        )
        self.save(data)

    def add_title_rule(self, pattern: str, match_type: str = "contains") -> bool:
        pattern = str(pattern or "").strip()
        match_type = str(match_type or "contains").strip().lower()
        if not pattern or match_type not in {"exact", "contains", "suffix", "regex"}:
            return False
        if match_type == "regex":
            re.compile(pattern, re.IGNORECASE)
        data = self.load()
        rule = {"pattern": pattern, "match_type": match_type, "enabled": True}
        key = (pattern.casefold(), match_type)
        if any(
            (str(item.get("pattern", "")).casefold(), str(item.get("match_type", ""))) == key
            for item in data["title_rules"]
            if isinstance(item, dict)
        ):
            return False
        data["title_rules"].append(rule)
        self.save(data)
        return True

    def remove_title_rules(self, indexes: Iterable[int]) -> int:
        data = self.load()
        rules = data["title_rules"]
        selected = {int(index) for index in indexes}
        remaining = [rule for index, rule in enumerate(rules) if index not in selected]
        removed = len(rules) - len(remaining)
        if removed:
            data["title_rules"] = remaining
            self.save(data)
        return removed

    def set_title_rule_enabled(self, index: int, enabled: bool) -> bool:
        data = self.load()
        rules = data["title_rules"]
        if index < 0 or index >= len(rules) or not isinstance(rules[index], dict):
            return False
        rules[index]["enabled"] = bool(enabled)
        self.save(data)
        return True

    @staticmethod
    def _title_matches_rule(title: str, rule: dict[str, Any]) -> bool:
        if not bool(rule.get("enabled", True)):
            return False
        pattern = str(rule.get("pattern") or "").strip()
        match_type = str(rule.get("match_type") or "contains").strip().lower()
        if not pattern:
            return False
        folded_title = str(title or "").strip().casefold()
        folded_pattern = pattern.casefold()
        if match_type == "exact":
            return folded_title == folded_pattern
        if match_type == "suffix":
            return folded_title.endswith(folded_pattern)
        if match_type == "regex":
            try:
                return re.search(pattern, str(title or ""), re.IGNORECASE) is not None
            except re.error:
                return False
        return folded_pattern in folded_title

    def matching_rule(self, title: str) -> dict[str, Any] | None:
        return next(
            (rule for rule in self.title_rules() if self._title_matches_rule(title, rule)),
            None,
        )

    def exclusion_reason(self, record: HasSeriesIdentity) -> str:
        if self.is_excluded(record.id):
            return "manual"
        rule = self.matching_rule(record.title)
        if rule:
            return f"rule:{rule.get('match_type')}:{rule.get('pattern')}"
        return ""

    def add(self, series_id: str, title: str, library_name: str, reason: str = "manual") -> None:
        if not series_id:
            return
        data = self.load()
        data["excluded_series_ids"][str(series_id)] = {
            "title": str(title or ""),
            "library": str(library_name or ""),
            "reason": str(reason or "manual"),
            "created_at": date.today().isoformat(),
        }
        self.save(data)

    def add_many(self, records: Iterable[HasSeriesIdentity], reason: str = "manual") -> int:
        count = 0
        data = self.load()
        excluded = data["excluded_series_ids"]
        for record in records:
            series_id = str(record.id or "")
            if not series_id:
                continue
            excluded[series_id] = {
                "title": str(record.title or ""),
                "library": str(record.library_name or ""),
                "reason": str(reason or "manual"),
                "created_at": date.today().isoformat(),
            }
            count += 1
        self.save(data)
        return count

    def remove(self, series_id: str) -> bool:
        data = self.load()
        existed = data["excluded_series_ids"].pop(str(series_id or ""), None) is not None
        if existed:
            self.save(data)
        return existed

    def remove_many(self, series_ids: Iterable[str]) -> int:
        data = self.load()
        removed = 0
        excluded = data["excluded_series_ids"]
        for series_id in series_ids:
            if excluded.pop(str(series_id or ""), None) is not None:
                removed += 1
        if removed:
            self.save(data)
        return removed

    def filter_records(self, records: Iterable[HasSeriesIdentity], include_excluded: bool = False) -> list[HasSeriesIdentity]:
        rows = list(records)
        if include_excluded:
            return rows
        return [record for record in rows if not self.exclusion_reason(record)]
