from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class WebJob:
    id: str
    label: str
    channel: str = "web"
    status: str = "queued"
    current: int = 0
    total: int = 0
    message: str = ""
    result: Any = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    cancelled: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "current": self.current,
            "total": self.total,
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class WebJobManager:
    def __init__(self, max_workers: int = 4) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="komga-web")
        self._jobs: dict[str, WebJob] = {}
        self._lock = threading.RLock()

    def submit(
        self,
        label: str,
        action: Callable[[Callable[[int, int, str], None], Callable[[], bool]], Any],
        *,
        channel: str = "web",
    ) -> WebJob:
        job = WebJob(id=uuid.uuid4().hex, label=label, channel=channel)
        with self._lock:
            self._jobs[job.id] = job

        def progress(current: int, total: int, message: str = "") -> None:
            with self._lock:
                job.current = current
                job.total = total
                job.message = message
                job.updated_at = time.time()

        def run() -> None:
            with self._lock:
                job.status = "running"
                job.updated_at = time.time()
            try:
                result = action(progress, lambda: job.cancelled)
                with self._lock:
                    job.result = result
                    job.status = "cancelled" if job.cancelled else "completed"
            except Exception as exc:
                with self._lock:
                    job.error = str(exc)
                    job.status = "failed"
            finally:
                with self._lock:
                    job.updated_at = time.time()

        self._executor.submit(run)
        return job

    def get(self, job_id: str, *, channel: str | None = None) -> WebJob:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None or (channel is not None and job.channel != channel):
            raise LookupError("Tâche introuvable")
        return job

    def list(self, *, channel: str = "web") -> list[dict[str, Any]]:
        with self._lock:
            rows = sorted(
                (item for item in self._jobs.values() if item.channel == channel),
                key=lambda item: item.created_at,
                reverse=True,
            )
        return [row.public() for row in rows[:100]]

    def cancel(self, job_id: str, *, channel: str | None = None) -> WebJob:
        job = self.get(job_id, channel=channel)
        with self._lock:
            if job.status in {"queued", "running"}:
                job.cancelled = True
                job.message = "Annulation demandée"
                job.updated_at = time.time()
        return job

    def consume_result(
        self,
        job_id: str,
        expected_mode: str,
        *,
        channel: str | None = None,
        max_age_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Atomically consume a completed plan so it cannot be applied twice."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or (channel is not None and job.channel != channel):
                raise LookupError("Tâche introuvable")
            if job.status != "completed" or not isinstance(job.result, dict):
                raise RuntimeError("La préparation n'est pas terminée")
            if max_age_seconds is not None and time.time() - job.updated_at > max_age_seconds:
                raise RuntimeError("La prévisualisation a expiré ; relancez l'analyse")
            if job.result.get("mode") != expected_mode:
                raise ValueError("Type de préparation invalide")
            if job.result.get("consumed"):
                raise RuntimeError("Cette préparation a déjà été confirmée")
            result = dict(job.result)
            job.result = {**job.result, "consumed": True}
            job.updated_at = time.time()
            return result


jobs = WebJobManager()
