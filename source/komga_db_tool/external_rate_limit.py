from __future__ import annotations

import random
import threading
import time
from typing import Any, Callable, Dict


# Anti-ban / politeness guard for external sources.
# Mandatory, hardcoded, non-zero minimums by design. Do not expose a "disable" option.
EXTERNAL_SOURCE_RATE_LIMITS = {
    # MangaBaka documents two distinct leaky buckets: 30/minute for
    # /series/search and 180/minute for the other GET endpoints. Keep the
    # search bucket slightly below its published ceiling while preserving the
    # existing pacing for detail lookups.
    "mangabaka": {
        "delay": 1.0,
        "search_delay": 2.1,
        "jitter": 0.2,
        "error_pause": 10.0,
        "max_429_retries": 2,
        "max_retry_pause": 60.0,
    },
    "manga_news": {"delay": 0.25, "jitter": 0.1, "error_pause": 5.0},
    "comicvine": {"delay": 1.2, "jitter": 0.4, "error_pause": 10.0},
    # Metron limite les appels à 20/minute : 3,1 s laisse une petite marge.
    "metron": {"delay": 3.1, "jitter": 0.2, "error_pause": 15.0},
}
EXTERNAL_SOURCE_MIN_DELAY_SECONDS = 0.25
EXTERNAL_SOURCE_STOP_HTTP_CODES = {403, 429}


class ExternalSourceBlocked(Exception):
    """Raised when an external provider returns a blocking/rate-limit signal."""


class RateLimitedSourceClient:
    """Proxy enforcing mandatory delays before external provider calls.

    The proxy is intentionally attached at the GUI layer so all network-backed
    source workflows share the same pacing.
    """

    def __init__(
        self,
        provider: str,
        client: Any,
        state: Dict[str, Any],
        notify: Callable[[str, int, int], None],
        *,
        delay_seconds: float | None = None,
    ):
        self._provider = provider
        self._client = client
        self._state = state
        self._notify = notify
        limits = EXTERNAL_SOURCE_RATE_LIMITS[provider]
        configured_delay = limits["delay"] if delay_seconds is None else delay_seconds
        self._delay = max(EXTERNAL_SOURCE_MIN_DELAY_SECONDS, float(configured_delay))
        self._search_delay = max(self._delay, float(limits.get("search_delay", self._delay)))
        self._jitter = max(0.0, float(limits["jitter"]))
        self._error_pause = max(EXTERNAL_SOURCE_MIN_DELAY_SECONDS, float(limits["error_pause"]))
        self._max_429_retries = max(0, int(limits.get("max_429_retries", 0)))
        self._max_retry_pause = max(
            self._error_pause,
            float(limits.get("max_retry_pause", self._error_pause)),
        )

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._client, name)
        if not callable(attr) or name.startswith("_"):
            return attr

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            retry_index = 0
            while True:
                self._wait_before_call(name)
                try:
                    return attr(*args, **kwargs)
                except Exception as exc:
                    status = self._blocking_status(exc)
                    if status == 429 and retry_index < self._max_429_retries:
                        pause = self._retry_pause_seconds(exc, retry_index)
                        retry_index += 1
                        self._notify(
                            f"{self._provider} limite les requêtes — pause {pause:.0f}s "
                            f"puis nouvelle tentative {retry_index}/{self._max_429_retries} ({name})",
                            0,
                            0,
                        )
                        time.sleep(pause)
                        continue
                    if status in EXTERNAL_SOURCE_STOP_HTTP_CODES:
                        # Preserve the previous cool-down on a hard 403. A
                        # 429 has already consumed its retry pauses above.
                        if status == 403:
                            self._pause_after_blocking_error(name, exc)
                        raise ExternalSourceBlocked(
                            f"{self._provider} a répondu par une erreur de blocage/rate-limit: {exc}"
                        ) from exc
                    raise

        return wrapped

    def _wait_before_call(self, operation: str) -> None:
        lock = self._state.setdefault("lock", threading.Lock())
        bucket = "search" if self._provider == "mangabaka" and operation == "search" else "default"
        operation_delay = self._search_delay if bucket == "search" else self._delay
        now = time.monotonic()
        with lock:
            buckets = self._state.setdefault("next_allowed_by_bucket", {})
            legacy_next_allowed = self._state.get("next_allowed", 0.0) if bucket == "default" else 0.0
            next_allowed = float(buckets.get(bucket, legacy_next_allowed))
            delay = max(0.0, next_allowed - now)
            jitter = random.uniform(0.0, self._jitter) if self._jitter else 0.0
            buckets[bucket] = max(now, next_allowed) + operation_delay + jitter
            if bucket == "default":
                self._state["next_allowed"] = buckets[bucket]
        if delay > 0:
            self._notify(f"Pause anti-ban {self._provider} {delay:.1f}s avant {operation}", 0, 0)
            time.sleep(delay)

    def _retry_pause_seconds(self, exc: Exception, retry_index: int) -> float:
        retry_after = self._retry_after_seconds(exc)
        if retry_after is not None:
            return min(self._max_retry_pause, max(EXTERNAL_SOURCE_MIN_DELAY_SECONDS, retry_after))
        return min(self._max_retry_pause, self._error_pause * (2 ** max(0, retry_index)))

    def _pause_after_blocking_error(self, operation: str, exc: Exception) -> None:
        self._notify(f"{self._provider} bloque ou limite les requêtes — pause {self._error_pause:.0f}s puis arrêt ({operation})", 0, 0)
        time.sleep(self._error_pause)

    @staticmethod
    def _is_blocking_error(exc: Exception) -> bool:
        return RateLimitedSourceClient._blocking_status(exc) in EXTERNAL_SOURCE_STOP_HTTP_CODES

    @staticmethod
    def _exception_chain(exc: Exception) -> list[BaseException]:
        chain: list[BaseException] = []
        current: BaseException | None = exc
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            chain.append(current)
            current = current.__cause__ or current.__context__
        return chain

    @staticmethod
    def _blocking_status(exc: Exception) -> int | None:
        for current in RateLimitedSourceClient._exception_chain(exc):
            code = getattr(current, "code", None)
            try:
                parsed = int(code)
            except (TypeError, ValueError):
                parsed = None
            if parsed in EXTERNAL_SOURCE_STOP_HTTP_CODES:
                return parsed
            text = str(current)
            for candidate in EXTERNAL_SOURCE_STOP_HTTP_CODES:
                if f"HTTP {candidate}" in text or f"HTTP Error {candidate}" in text:
                    return candidate
        return None

    @staticmethod
    def _retry_after_seconds(exc: Exception) -> float | None:
        for current in RateLimitedSourceClient._exception_chain(exc):
            value = getattr(current, "retry_after_seconds", None)
            if value in (None, ""):
                headers = getattr(current, "headers", None) or getattr(current, "hdrs", None)
                value = headers.get("Retry-After") if headers is not None else None
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                continue
            if parsed >= 0:
                return parsed
        return None
