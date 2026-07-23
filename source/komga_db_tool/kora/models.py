from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AuthConfig:
    mode: str = "api_key"  # api_key | basic | none
    api_key: str = ""
    username: str = ""
    password: str = ""


@dataclass(frozen=True)
class LibraryItem:
    id: str
    name: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SeriesItem:
    id: str
    library_id: str
    library_name: str
    title: str
    book_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SeriesRecord:
    id: str
    library_id: str
    library_name: str
    title: str
    book_count: int
    tags: list[str]
    genres: list[str]
    kora_genres: list[str]
    kora_tags: list[str]
    tags_lock: bool
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PendingChange:
    series_id: str
    library_name: str
    title: str
    new_kora_genres: list[str]
    source: str = "manual"
    note: str = ""


@dataclass(frozen=True)
class CsvImportChange:
    series_id: str
    title: str
    library_name: str
    kora_genres: list[str]
    source_file: str
    raw_row: dict[str, str] = field(default_factory=dict)
