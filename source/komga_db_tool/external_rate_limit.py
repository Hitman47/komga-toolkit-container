from __future__ import annotations

import random
import threading
import time
from typing import Any, Callable, Dict


# Anti-ban / politeness guard for external sources.
# Mandatory, hardcoded, non-zero minimums by design. Do not expose a "disable" option.
EXTERNAL_SOURCE_RATE_LIMITS = {
    "bedetheque": {"delay": 2.0, "jitter": 0.5, "error_pause": 10.0},
    "mangabaka": {"delay": 1.0, "jitter": 0.3, "error_pause": 10.0},
    "manga_news": {"delay": 0.25, "jitter": 0.1, "error_pause": 5.0},
    "comicvine": {"delay": 1.2, "jitter": 0.4, "error_pause": 10.0},
}
EXTERNAL_SOURCE_MIN_DELAY_SECONDS = 0.25
EXTERNAL_SOURCE_STOP_HTTP_CODES = {403, 429}


class ExternalSourceBlocked(Exception):
    """Raised when an external provider returns a blocking/rate-limit signal."""


class RateLimitedSourceClient:
    """Proxy enforcing mandatory delays before external provider calls.

    The proxy is intentionally attached at the GUI layer so all Bedetheque/MangaBaka
    workflows share the same pacing: manual search, automatic search, batch, update
    with link and release tracking.
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
        self._jitter = max(0.0, float(limits["jitter"]))
        self._error_pause = max(EXTERNAL_SOURCE_MIN_DELAY_SECONDS, float(limits["error_pause"]))

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._client, name)
        if not callable(attr) or name.startswith("_"):
            return attr

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            self._wait_before_call(name)
            try:
                return attr(*args, **kwargs)
            except Exception as exc:
                if self._is_blocking_error(exc):
                    self._pause_after_blocking_error(name, exc)
                    raise ExternalSourceBlocked(f"{self._provider} a répondu par une erreur de blocage/rate-limit: {exc}") from exc
                raise

        return wrapped

    def _wait_before_call(self, operation: str) -> None:
        lock = self._state.setdefault("lock", threading.Lock())
        now = time.monotonic()
        with lock:
            next_allowed = float(self._state.get("next_allowed", 0.0))
            delay = max(0.0, next_allowed - now)
            jitter = random.uniform(0.0, self._jitter) if self._jitter else 0.0
            self._state["next_allowed"] = max(now, next_allowed) + self._delay + jitter
        if delay > 0:
            self._notify(f"Pause anti-ban {self._provider} {delay:.1f}s avant {operation}", 0, 0)
            time.sleep(delay)

    def _pause_after_blocking_error(self, operation: str, exc: Exception) -> None:
        self._notify(f"{self._provider} bloque ou limite les requêtes — pause {self._error_pause:.0f}s puis arrêt ({operation})", 0, 0)
        time.sleep(self._error_pause)

    @staticmethod
    def _is_blocking_error(exc: Exception) -> bool:
        code = getattr(exc, "code", None)
        if code in EXTERNAL_SOURCE_STOP_HTTP_CODES:
            return True
        text = str(exc)
        return any(f"HTTP {code}" in text or f"HTTP Error {code}" in text for code in EXTERNAL_SOURCE_STOP_HTTP_CODES)
