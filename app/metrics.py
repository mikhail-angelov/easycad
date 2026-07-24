"""Tiny in-process metrics for launch observability (SPEC14 hardening).

Single-instance counters (per SPEC13), reset on restart. Not Prometheus — just
enough for an operator to eyeball attempts, successes, failures, operator-key
spend, and latency, and to alert. Exposed via the protected /api/admin/stats
endpoint.
"""

import threading

_lock = threading.Lock()
_counters: dict[str, int] = {}


def incr(name: str, by: int = 1) -> None:
    with _lock:
        _counters[name] = _counters.get(name, 0) + by


def snapshot() -> dict[str, int]:
    with _lock:
        return dict(_counters)


def _reset_for_tests() -> None:
    with _lock:
        _counters.clear()
