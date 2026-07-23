from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


class BackupManager:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save_operation_backup(self, rows: list[dict[str, Any]], prefix: str = "komga_kora_backup") -> tuple[Path, Path]:
        stamp = _stamp()
        json_path = self.root / f"{prefix}_{stamp}.json"
        csv_path = self.root / f"{prefix}_{stamp}_summary.csv"
        with json_path.open("w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2, sort_keys=True)
        self._write_summary(csv_path, rows)
        return json_path, csv_path

    def _write_summary(self, path: Path, rows: list[dict[str, Any]]) -> None:
        fieldnames = [
            "series_id",
            "library_name",
            "title",
            "before_kora_genres",
            "after_kora_genres",
            "before_tags_count",
            "after_tags_count",
            "source",
            "note",
        ]
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                before_tags = row.get("metadata_before", {}).get("tags", [])
                after_tags = row.get("planned_metadata_after", {}).get("tags", [])
                writer.writerow({
                    "series_id": row.get("series_id", ""),
                    "library_name": row.get("library_name", ""),
                    "title": row.get("title", ""),
                    "before_kora_genres": " | ".join(row.get("before_kora_genres", [])),
                    "after_kora_genres": " | ".join(row.get("after_kora_genres", [])),
                    "before_tags_count": len(before_tags) if isinstance(before_tags, list) else "",
                    "after_tags_count": len(after_tags) if isinstance(after_tags, list) else "",
                    "source": row.get("source", ""),
                    "note": row.get("note", ""),
                })
