from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, Iterable, TypeVar

T = TypeVar("T")


class CancelledError(RuntimeError):
    pass


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise CancelledError("Opération annulée")


class SecretRedactor:
    _assignment = re.compile(
        r"(?i)\b(api[_-]?key|authorization|password|passwd|username|login|token|secret)"
        r"(\s*[:=]\s*)([^\s,;]+)"
    )
    _bearer = re.compile(r"(?i)\b(Basic|Bearer)\s+[A-Za-z0-9+/=_\-.:]+")

    @classmethod
    def redact(cls, value: Any, known_secrets: Iterable[str] = ()) -> str:
        text = str(value)
        for secret in known_secrets:
            if secret and len(secret) >= 4:
                text = text.replace(secret, "[SECRET MASQUÉ]")
        text = cls._bearer.sub(r"\1 [SECRET MASQUÉ]", text)
        return cls._assignment.sub(r"\1\2[SECRET MASQUÉ]", text)


@dataclass
class CacheEntry(Generic[T]):
    value: T
    expires_at: float


class MemoryCache:
    def __init__(self, max_entries: int = 256) -> None:
        self.max_entries = max(1, max_entries)
        self._entries: OrderedDict[str, CacheEntry[Any]] = OrderedDict()
        self._lock = threading.RLock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str, default: Any = None) -> Any:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return default
            if entry.expires_at < now:
                self._entries.pop(key, None)
                self.misses += 1
                return default
            self._entries.move_to_end(key)
            self.hits += 1
            return entry.value

    def set(self, key: str, value: Any, ttl_seconds: float = 300) -> Any:
        with self._lock:
            self._entries[key] = CacheEntry(
                value=value,
                expires_at=time.monotonic() + max(0.1, ttl_seconds),
            )
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
        return value

    def get_or_load(
        self,
        key: str,
        loader: Callable[[], T],
        ttl_seconds: float = 300,
    ) -> T:
        marker = object()
        cached = self.get(key, marker)
        if cached is not marker:
            return cached
        return self.set(key, loader(), ttl_seconds)

    def invalidate(self, prefix: str = "") -> int:
        with self._lock:
            keys = [
                key for key in self._entries
                if not prefix or key == prefix or key.startswith(prefix)
            ]
            for key in keys:
                self._entries.pop(key, None)
            return len(keys)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "entries": len(self._entries),
                "hits": self.hits,
                "misses": self.misses,
            }


@dataclass
class OperationRecord:
    label: str
    target_type: str
    target_id: str
    before: Any
    after: Any
    undo: Callable[[], Any] | None = field(default=None, repr=False)
    created_at: float = field(default_factory=time.time)
    undone: bool = False


class OperationJournal:
    def __init__(self, max_entries: int = 100) -> None:
        self.max_entries = max(1, max_entries)
        self._records: list[OperationRecord] = []
        self._lock = threading.RLock()

    def record(self, operation: OperationRecord) -> OperationRecord:
        with self._lock:
            self._records.append(operation)
            del self._records[:-self.max_entries]
        return operation

    def recent(self) -> list[OperationRecord]:
        with self._lock:
            return list(reversed(self._records))

    def undo_last(self) -> OperationRecord:
        with self._lock:
            operation = next(
                (
                    item for item in reversed(self._records)
                    if not item.undone and item.undo is not None
                ),
                None,
            )
        if operation is None:
            raise LookupError("Aucune opération annulable")
        operation.undo()
        operation.undone = True
        return operation
