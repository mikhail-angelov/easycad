"""Daily crash sink + digest (SPEC21 W2).

The app is the **sole writer** of a per-day JSONL file under `EASYCAD_CRASH_DIR`
(defaults to `/data/crashes`, inside the app's existing `./data` mount — no new
volume, no worker volume: the worker runs read-only and stays stateless). Each
crash is **one** newline-terminated `os.write` to an `O_APPEND` fd, serialized by
a module lock; that is sufficient for the single-process app. Crash logging must
never break a request, so `record` no-ops (warning once) on an unwritable dir.

Retention is **count-based** — keep the newest 3 dated files — not an age window,
which would disagree on quiet days when no file is created. The daily digest is
sent lazily, **at-most-once**, claimed by a single atomic marker file.
"""

import glob
import json
import logging
import os
import re
import threading
import time
from pathlib import Path

log = logging.getLogger("easycad")

_lock = threading.Lock()
_warned = False  # warn-once on an unwritable dir, so a broken mount can't spam

# Length caps — one crash line stays small and bounded.
MAX_MSG = 500
MAX_TB = 2000

# Secret scrubbing (mandatory): never persist BYOK keys or bearer tokens, even if
# a traceback/message happens to embed one. Conservative, prefix-anchored.
_SCRUB = [
    re.compile(r"(sk-or-v1-|sk-ant-|sk-|gsk_|ds-)[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"),
]

RETENTION = 3


def crash_dir() -> Path:
    """Resolve the crash dir fresh each call (so tests can point it at a tmp dir).
    Default `/data/crashes` when the app's `/data` mount exists, else `./crashlog`
    for local dev."""
    env = (os.getenv("EASYCAD_CRASH_DIR") or "").strip()
    if env:
        return Path(env)
    return Path("/data/crashes") if os.path.isdir("/data") else Path("./crashlog")


def _reports_dir() -> Path:
    return crash_dir() / "reports"


def _utc_date() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def scrub_text(s: str) -> str:
    """Remove credential-shaped values before writing diagnostic text to disk."""
    for p in _SCRUB:
        s = p.sub("<redacted>", s)
    return s


def ensure_dir() -> None:
    """Best-effort create the crash dir on boot (record also creates lazily)."""
    try:
        crash_dir().mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass


def record(event: dict) -> None:
    """Append one scrubbed, length-capped JSON line to `crashes-<UTC-date>.jsonl`.
    Never raises: a missing/unwritable dir logs a single warning and no-ops."""
    global _warned
    try:
        ev = dict(event)
        if ev.get("exc_message"):
            ev["exc_message"] = scrub_text(str(ev["exc_message"]))[:MAX_MSG]
        if ev.get("traceback_tail"):
            ev["traceback_tail"] = scrub_text(str(ev["traceback_tail"]))[:MAX_TB]
        line = (json.dumps(ev, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        d = crash_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"crashes-{_utc_date()}.jsonl"
        with _lock:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            try:
                os.write(fd, line)
            finally:
                os.close(fd)
    except Exception as exc:  # noqa: BLE001 — crash logging must never break a request
        if not _warned:
            _warned = True
            log.warning("crashlog.record disabled (dir unwritable?): %s", exc)


# ── Daily digest ──────────────────────────────────────────────────────────────


def _signature(ev: dict) -> str:
    """Group key: exc class + top traceback frame for a genuine bug; the coded
    `code` (server_busy/…) for an operational 5xx that carries no exception."""
    cls = ev.get("exc_class")
    if cls:
        tail = ev.get("traceback_tail") or ""
        frame = ""
        for ln in tail.splitlines():
            s = ln.strip()
            if s.startswith("File "):
                frame = s
        return f"{cls} @ {frame}" if frame else cls
    return f"[{ev.get('service', 'app')}] {ev.get('code') or ev.get('status') or 'operational'}"


def build_digest(date: str) -> tuple[int, str, str]:
    """Return `(n_crashes, subject, body)` for `date`. A zero-crash day yields a
    one-line heartbeat so operator silence is never ambiguous.

    Aggregates the JSONL **streaming, line by line** — memory is bounded by the
    number of distinct signatures (one representative event each), NOT the total
    crash count, so a bad day's large file can't spike memory in the request path.
    """
    path = crash_dir() / f"crashes-{date}.jsonl"
    n = 0
    by_service: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    groups: dict[str, dict] = {}
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    n += 1
                    svc, knd = ev.get("service", "app"), ev.get("kind", "error")
                    by_service[svc] = by_service.get(svc, 0) + 1
                    by_kind[knd] = by_kind.get(knd, 0) + 1
                    sig = _signature(ev)
                    g = groups.get(sig)
                    if g is None:
                        groups[sig] = {"count": 1, "first": ev.get("ts"), "last": ev.get("ts"), "rep": ev}
                    else:
                        g["count"] += 1
                        g["last"] = ev.get("ts")
        except OSError:
            pass

    subject = f"text2part: daily crash report {date} — {n} crashes"
    if n == 0:
        return 0, subject, f"0 crashes on {date}. All quiet.\n"

    lines = [f"{n} crashes on {date}.", ""]
    lines.append("By service: " + ", ".join(f"{k}={v}" for k, v in sorted(by_service.items())))
    lines.append("By kind:    " + ", ".join(f"{k}={v}" for k, v in sorted(by_kind.items())))
    lines.append("")
    lines.append("Top signatures:")
    for sig, g in sorted(groups.items(), key=lambda kv: kv[1]["count"], reverse=True):
        rep = g["rep"]
        lines.append(f"  [{g['count']}×] {sig}")
        lines.append(
            f"        first={g['first']} last={g['last']} "
            f"trace={rep.get('trace_id', '-')} {rep.get('method', '')} {rep.get('path', '')}"
        )
        msg = (rep.get("exc_message") or rep.get("error") or "").strip()
        if msg:
            lines.append(f"        {msg[:200]}")
    lines.append("")
    lines.append(f"Full detail: crashes-{date}.jsonl (look up by trace_id).")
    return n, subject, "\n".join(lines) + "\n"


def claim_report(date: str) -> bool | None:
    """Atomically claim `date`'s report with one `O_CREAT|O_EXCL` create of
    `reports/<date>.sent`. Never raises. Returns:

      True  — this caller won the claim (across concurrent requests + restarts).
      False — already claimed (a definitive "someone sent it").
      None  — a TRANSIENT FS error (dir briefly unwritable): the caller must NOT
              mark the day done, so a later request retries instead of losing the
              report until the next UTC day.
    """
    try:
        rd = _reports_dir()
        rd.mkdir(parents=True, exist_ok=True)
        fd = os.open(rd / f"{date}.sent", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.close(fd)
        return True
    except FileExistsError:
        return False
    except Exception:  # noqa: BLE001 — transient; signal "retry", don't claim
        return None


def _prune_newest(pattern: str, keep: int) -> None:
    """Sort dated files by name and delete all but the newest `keep`."""
    files = sorted(glob.glob(pattern))
    for f in (files[:-keep] if keep > 0 else files):
        try:
            os.remove(f)
        except OSError:
            pass


def apply_retention(keep: int = RETENTION) -> None:
    """Count-based retention for BOTH sinks, independently: keep the newest `keep`
    dated crash files, and the newest `keep` `.sent` markers.

    Markers are pruned on their OWN count — not relative to the oldest crash file —
    because a healthy app that never crashes creates no crash file yet still writes
    one heartbeat marker per day; coupling them would let markers grow unbounded.
    A marker is only ever consulted on its own UTC day (restart/concurrency dedupe),
    so keeping a few recent ones is ample."""
    try:
        _prune_newest(str(crash_dir() / "crashes-*.jsonl"), keep)
        _prune_newest(str(_reports_dir() / "*.sent"), keep)
    except Exception:  # noqa: BLE001 — retention must never break a request
        pass


def _reset_for_tests() -> None:
    global _warned
    _warned = False
