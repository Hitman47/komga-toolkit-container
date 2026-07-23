from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class BackupManager:
    root: str
    session_dir: str = field(init=False)
    manifest_path: str = field(init=False)

    def __post_init__(self) -> None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_dir = os.path.abspath(os.path.join(self.root, stamp))
        self.manifest_path = os.path.join(self.session_dir, "manifest.csv")
        for sub in ("session", "operations", "csv_exports", "json_exports", "audit", "rollback"):
            os.makedirs(os.path.join(self.session_dir, sub), exist_ok=True)
        with open(self.manifest_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["timestamp", "kind", "target_type", "target_id", "path", "note"])
            writer.writeheader()

    def _stamp(self) -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    def record(self, kind: str, target_type: str, target_id: str, path: str, note: str = "") -> None:
        with open(self.manifest_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["timestamp", "kind", "target_type", "target_id", "path", "note"])
            writer.writerow({
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "kind": kind,
                "target_type": target_type,
                "target_id": target_id,
                "path": os.path.relpath(path, self.session_dir),
                "note": note,
            })

    def save_json(self, kind: str, target_type: str, target_id: str, data: Any, note: str = "") -> str:
        safe_id = (target_id or "no-id").replace("/", "_").replace("\\", "_")
        folder = "operations" if kind == "operation" else "session"
        path = os.path.join(self.session_dir, folder, f"{self._stamp()}_{target_type}_{safe_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        self.record(kind, target_type, target_id, path, note)
        return path


    def save_audit(self, target_type: str, target_id: str, data: Any, note: str = "") -> str:
        """Write a complete audit event for a metadata write attempt.

        Audit files are append-only JSON snapshots intended for diagnosis and
        future rollback tooling. They include old metadata, payload, response or
        error, and the source workflow when provided by the caller.
        """
        safe_id = (target_id or "no-id").replace("/", "_").replace("\\", "_")
        path = os.path.join(self.session_dir, "audit", f"{self._stamp()}_{target_type}_{safe_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        self.record("audit", target_type, target_id, path, note)
        return path

    def save_rollback_candidate(self, target_type: str, target_id: str, current_metadata: Any, note: str = "") -> str:
        """Persist current metadata in a rollback-specific folder.

        This does not perform rollback yet; it makes every write operation
        restorable by a later assisted rollback UI.
        """
        safe_id = (target_id or "no-id").replace("/", "_").replace("\\", "_")
        path = os.path.join(self.session_dir, "rollback", f"{self._stamp()}_{target_type}_{safe_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(current_metadata, f, ensure_ascii=False, indent=2, sort_keys=True)
        self.record("rollback", target_type, target_id, path, note)
        return path

    def export_csv(self, filename: str, rows: List[Dict[str, Any]]) -> str:
        path = os.path.join(self.session_dir, "csv_exports", filename)
        fieldnames: List[str] = []
        for row in rows:
            for key in row.keys():
                if key not in fieldnames:
                    fieldnames.append(key)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames or ["empty"])
            writer.writeheader()
            for row in rows:
                writer.writerow({k: _csv_value(v) for k, v in row.items()})
        self.record("export", "csv", filename, path, f"{len(rows)} ligne(s)")
        return path


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def list_rollback_records(root: str) -> List[Dict[str, Any]]:
    """Return rollback snapshots recorded under a backup root.

    Records are read from each session manifest when available. A conservative
    fallback scans rollback/*.json so older/incomplete sessions remain usable.
    """
    records: List[Dict[str, Any]] = []
    root_abs = os.path.abspath(root or "")
    if not root_abs or not os.path.isdir(root_abs):
        return records
    for session_name in sorted(os.listdir(root_abs), reverse=True):
        session_dir = os.path.join(root_abs, session_name)
        if not os.path.isdir(session_dir):
            continue
        manifest = os.path.join(session_dir, "manifest.csv")
        seen_paths: set[str] = set()
        if os.path.exists(manifest):
            try:
                with open(manifest, newline="", encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        if row.get("kind") != "rollback":
                            continue
                        rel_path = row.get("path") or ""
                        abs_path = os.path.abspath(os.path.join(session_dir, rel_path))
                        if not abs_path.startswith(os.path.abspath(session_dir)) or not os.path.exists(abs_path):
                            continue
                        seen_paths.add(abs_path)
                        records.append({
                            "session": session_name,
                            "timestamp": row.get("timestamp") or "",
                            "target_type": row.get("target_type") or _target_type_from_rollback_filename(abs_path),
                            "target_id": row.get("target_id") or _target_id_from_rollback_filename(abs_path),
                            "note": row.get("note") or "",
                            "path": rel_path,
                            "abs_path": abs_path,
                        })
            except Exception:
                pass
        rollback_dir = os.path.join(session_dir, "rollback")
        if not os.path.isdir(rollback_dir):
            continue
        for filename in sorted(os.listdir(rollback_dir), reverse=True):
            if not filename.endswith(".json"):
                continue
            abs_path = os.path.abspath(os.path.join(rollback_dir, filename))
            if abs_path in seen_paths:
                continue
            records.append({
                "session": session_name,
                "timestamp": "",
                "target_type": _target_type_from_rollback_filename(abs_path),
                "target_id": _target_id_from_rollback_filename(abs_path),
                "note": "rollback snapshot",
                "path": os.path.relpath(abs_path, session_dir),
                "abs_path": abs_path,
            })
    return records


def load_rollback_snapshot(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data
    raise ValueError("Rollback snapshot JSON must be an object")


def _target_type_from_rollback_filename(path: str) -> str:
    name = os.path.basename(path)
    if "_series_" in name:
        return "series"
    if "_book_" in name:
        return "book"
    return ""


def _target_id_from_rollback_filename(path: str) -> str:
    name = os.path.basename(path)
    stem = name[:-5] if name.endswith(".json") else name
    for marker in ("_series_", "_book_"):
        if marker in stem:
            return stem.split(marker, 1)[1]
    return ""
