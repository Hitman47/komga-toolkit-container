from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .models import CsvImportChange
from .tag_logic import parse_genres_from_csv_cell, validate_genres


def sniff_delimiter(path: str | Path) -> str:
    sample = Path(path).read_text(encoding="utf-8-sig", errors="replace")[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t,")
        return dialect.delimiter
    except Exception:
        # The generated exports are semicolon-delimited.
        return ";" if sample.count(";") >= sample.count(",") else ","


def read_csv_changes(path: str | Path) -> list[CsvImportChange]:
    p = Path(path)
    delimiter = sniff_delimiter(p)
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        if not reader.fieldnames:
            raise ValueError("CSV sans en-tête")
        rows = list(reader)

    changes: list[CsvImportChange] = []
    for row in rows:
        decision = (row.get("import_decision") or row.get("decision") or "").strip().upper()
        if decision and decision not in {"IMPORT_READY", "IMPORT_READY_SOURCE", "IMPORT_READY_FRANCHISE_RULE", "IMPORT_TAG_ONLY"}:
            continue
        series_id = (row.get("series_id") or row.get("id") or "").strip()
        if not series_id:
            continue
        title = (row.get("series_title") or row.get("title") or "").strip()
        library_name = (row.get("library_name") or "").strip()
        genres = []
        for column in ("kora_tags_to_add", "kora_main_genres", "kora_genres", "genres"):
            if row.get(column):
                try:
                    genres = parse_genres_from_csv_cell(row[column])
                except ValueError:
                    # kora_tags_to_add can contain kora:tag:* in addition to genres; try next column.
                    continue
                if genres:
                    break
        if not genres:
            continue
        changes.append(CsvImportChange(
            series_id=series_id,
            title=title,
            library_name=library_name,
            kora_genres=validate_genres(genres),
            source_file=p.name,
            raw_row=dict(row),
        ))
    return changes
