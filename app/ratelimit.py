"""In-memory fixed-window rate limiting (SPEC13).

Single-instance only (SPEC13 assumes one app container). Not shared across
processes; a multi-instance deployment would need Redis.
"""

import threading
import time


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: float) -> bool:
        """Return True if `key` is under `limit` within the current window."""
        now = time.time()
        with self._lock:
            start, count = self._hits.get(key, (now, 0))
            if now - start >= window_seconds:
                start, count = now, 0
            count += 1
            self._hits[key] = (start, count)
            return count <= limit

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
