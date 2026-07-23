from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..backup import BackupManager, list_rollback_records, load_rollback_snapshot


def _metadata_from_entity(entity: dict[str, Any]) -> dict[str, Any]:
    metadata = entity.get("metadata")
    return dict(metadata) if isinstance(metadata, dict) else dict(entity)


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def metadata_diff(current: dict[str, Any], proposed: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "field": field,
            "current": current.get(field),
            "proposed": proposed.get(field),
            "changed": current.get(field) != proposed.get(field),
        }
        for field in proposed
    ]


@dataclass
class MutationPreview:
    token: str
    kind: str
    target_id: str
    payload: dict[str, Any]
    before: dict[str, Any]
    before_digest: str
    source: str
    created_at: float
    expires_at: float


class WebOperationService:
    """Two-phase mutations shared by every WebUI write workflow."""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self.data_dir = Path(data_dir or os.getenv("KOMGA_TOOLKIT_DATA_DIR") or ".komga_db_tool_cache/web")
        self.backup_root = self.data_dir / "backups"
        self._backup: BackupManager | None = None
        self._previews: dict[str, MutationPreview] = {}
        self._lock = threading.RLock()

    @property
    def backup(self) -> BackupManager:
        with self._lock:
            if self._backup is None:
                self._backup = BackupManager(str(self.backup_root))
            return self._backup

    def _put_preview(
        self,
        *,
        kind: str,
        target_id: str,
        payload: dict[str, Any],
        before: dict[str, Any],
        source: str,
    ) -> MutationPreview:
        now = time.time()
        preview = MutationPreview(
            token=uuid.uuid4().hex,
            kind=kind,
            target_id=target_id,
            payload=dict(payload),
            before=dict(before),
            before_digest=_digest(before),
            source=source,
            created_at=now,
            expires_at=now + 900,
        )
        with self._lock:
            self._previews[preview.token] = preview
            self._purge_locked(now)
        return preview

    def _take_preview(self, token: str) -> MutationPreview:
        now = time.time()
        with self._lock:
            self._purge_locked(now)
            preview = self._previews.pop(token, None)
        if preview is None:
            raise LookupError("Prévisualisation absente ou expirée")
        return preview

    def apply_any(self, api: Any, token: str) -> dict[str, Any]:
        with self._lock:
            preview = self._previews.get(token)
        if preview is None:
            raise LookupError("Prévisualisation absente ou expirée")
        if preview.kind in {"series.metadata", "book.metadata"}:
            return self.apply_metadata(api, token)
        if preview.kind in {"collection.create", "collection.update", "readlist.create", "readlist.update"}:
            return self.apply_resource(api, token)
        raise ValueError("Type de prévisualisation inconnu")

    def _purge_locked(self, now: float) -> None:
        for token in [key for key, value in self._previews.items() if value.expires_at <= now]:
            self._previews.pop(token, None)

    def preview_metadata(
        self,
        api: Any,
        target_type: str,
        target_id: str,
        payload: dict[str, Any],
        source: str = "webui",
    ) -> dict[str, Any]:
        if target_type not in {"series", "book"}:
            raise ValueError("Type de cible metadata invalide")
        if not target_id or not payload:
            raise ValueError("Cible et payload metadata requis")
        entity = api.get_series(target_id) if target_type == "series" else api.get_book(target_id)
        current = _metadata_from_entity(entity)
        preview = self._put_preview(
            kind=f"{target_type}.metadata",
            target_id=target_id,
            payload=payload,
            before=current,
            source=source,
        )
        return {
            "token": preview.token,
            "kind": preview.kind,
            "target_id": target_id,
            "source": source,
            "expires_at": preview.expires_at,
            "diff": metadata_diff(current, payload),
            "changed_fields": [row["field"] for row in metadata_diff(current, payload) if row["changed"]],
        }

    def apply_metadata(self, api: Any, token: str) -> dict[str, Any]:
        preview = self._take_preview(token)
        target_type = preview.kind.split(".", 1)[0]
        entity = api.get_series(preview.target_id) if target_type == "series" else api.get_book(preview.target_id)
        fresh = _metadata_from_entity(entity)
        if _digest(fresh) != preview.before_digest:
            raise RuntimeError("Les métadonnées ont changé depuis la prévisualisation")
        changed = {key: value for key, value in preview.payload.items() if fresh.get(key) != value}
        if not changed:
            return {"status": "unchanged", "target_type": target_type, "target_id": preview.target_id}
        self.backup.save_rollback_candidate(
            target_type,
            preview.target_id,
            fresh,
            f"WebUI avant {preview.source}",
        )
        self.backup.save_json(
            "operation",
            target_type,
            preview.target_id,
            {"current": fresh, "payload": changed, "source": preview.source},
            f"WebUI avant {preview.source}",
        )
        try:
            response = (
                api.update_series_metadata(preview.target_id, changed)
                if target_type == "series"
                else api.update_book_metadata(preview.target_id, changed)
            )
            self.backup.save_audit(
                target_type,
                preview.target_id,
                {"current": fresh, "payload": changed, "response": response, "source": preview.source},
                "WebUI metadata OK",
            )
            return {
                "status": "applied",
                "target_type": target_type,
                "target_id": preview.target_id,
                "fields": list(changed),
                "response": response,
            }
        except Exception as exc:
            self.backup.save_audit(
                target_type,
                preview.target_id,
                {"current": fresh, "payload": changed, "error": str(exc), "source": preview.source},
                "WebUI metadata erreur",
            )
            raise

    def preview_resource(
        self,
        api: Any,
        resource_type: str,
        operation: str,
        target_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if resource_type not in {"collection", "readlist"} or operation not in {"create", "update"}:
            raise ValueError("Opération de ressource invalide")
        if not payload:
            raise ValueError("Payload requis")
        if operation == "update" and not target_id:
            raise ValueError("Identifiant requis pour la mise à jour")
        before: dict[str, Any] = {}
        if operation == "update":
            before = (
                api.get_collection(target_id)
                if resource_type == "collection"
                else api.get_readlist(target_id)
            )
        preview = self._put_preview(
            kind=f"{resource_type}.{operation}",
            target_id=target_id,
            payload=payload,
            before=before,
            source="webui",
        )
        return {
            "token": preview.token,
            "kind": preview.kind,
            "target_id": target_id,
            "payload": payload,
            "diff": metadata_diff(before, payload),
            "expires_at": preview.expires_at,
        }

    def apply_resource(self, api: Any, token: str) -> dict[str, Any]:
        preview = self._take_preview(token)
        if "." not in preview.kind:
            raise ValueError("Prévisualisation de ressource invalide")
        resource_type, operation = preview.kind.split(".", 1)
        if resource_type not in {"collection", "readlist"} or operation not in {"create", "update"}:
            raise ValueError("Type de ressource invalide")
        if operation == "update":
            fresh = (
                api.get_collection(preview.target_id)
                if resource_type == "collection"
                else api.get_readlist(preview.target_id)
            )
            if _digest(fresh) != preview.before_digest:
                raise RuntimeError("La ressource a changé depuis la prévisualisation")
            self.backup.save_json(
                "operation",
                resource_type,
                preview.target_id,
                {"current": fresh, "payload": preview.payload},
                f"WebUI avant {resource_type} update",
            )
        if resource_type == "collection":
            response = (
                api.create_collection(preview.payload)
                if operation == "create"
                else api.update_collection(preview.target_id, preview.payload)
            )
        else:
            response = (
                api.create_readlist(preview.payload)
                if operation == "create"
                else api.update_readlist(preview.target_id, preview.payload)
            )
        return {"status": "applied", "kind": preview.kind, "target_id": preview.target_id, "response": response}

    def rollback_records(self) -> list[dict[str, Any]]:
        rows = list_rollback_records(str(self.backup_root))
        return [
            {
                "id": _digest(row.get("abs_path", ""))[:20],
                "session": row.get("session", ""),
                "timestamp": row.get("timestamp", ""),
                "target_type": row.get("target_type", ""),
                "target_id": row.get("target_id", ""),
                "note": row.get("note", ""),
            }
            for row in rows
        ]

    def rollback_snapshot(self, record_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        for row in list_rollback_records(str(self.backup_root)):
            if _digest(row.get("abs_path", ""))[:20] == record_id:
                return row, load_rollback_snapshot(row["abs_path"])
        raise LookupError("Snapshot rollback introuvable")


def public_candidate(value: Any) -> dict[str, Any]:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, dict):
        return value
    raise TypeError("Candidat source invalide")


operations = WebOperationService()
