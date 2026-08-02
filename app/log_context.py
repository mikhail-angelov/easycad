"""Per-request logging context (SPEC21 W1).

One `ContextVar[dict]` carries `trace_id`, `session_id`, `user`, and `version`
for the life of a request; a `logging.Filter` stamps those four fields onto
every `LogRecord`, so **every** existing call site (`log.error`, `easycad.llm`,
`cadquery_exec`, uvicorn's access/error loggers) is enriched with no change at
the site. A `LoggerAdapter` was rejected precisely because it would not reach
those.

`configure_logging()` owns logging explicitly via `dictConfig` — `basicConfig`
only touches root and is a no-op once uvicorn has installed its own
`propagate=False` handlers, so a root-only filter would never see request/access
lines. The context `Filter` is attached to the shared **handler** so it stamps
every record routed there regardless of the emitting logger.
"""

import contextvars
import logging
import logging.config

_FIELDS = ("trace_id", "session_id", "user", "version")

# The default dict is used for log lines emitted OUTSIDE a request (e.g. at
# boot). It is never mutated — each request `set`s a fresh dict — so boot-time
# defaults can't be polluted by request state.
_DEFAULTS = {k: "-" for k in _FIELDS}

_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar("log_context", default=_DEFAULTS)

# `… [trace=… user=… v=…] message`. `session_id` is stamped on the record (for
# the crash JSONL) but kept out of the default line to keep it readable.
FORMAT = "%(asctime)s %(levelname)s %(name)s [trace=%(trace_id)s user=%(user)s v=%(version)s] %(message)s"


def set_context(**fields) -> contextvars.Token:
    """Start a request context; returns a token the caller MUST pass to
    `reset_context` in a `finally` (a thread-pool task can be reused, so without
    the reset a later request could inherit this caller's identity)."""
    d = dict(_DEFAULTS)
    d.update({k: v for k, v in fields.items() if v is not None})
    return _ctx.set(d)


def update_context(**fields) -> None:
    """Fill in fields discovered mid-request (e.g. `session_id`). No-op outside a
    request so the shared `_DEFAULTS` dict is never mutated."""
    d = _ctx.get()
    if d is _DEFAULTS:
        return
    d.update({k: v for k, v in fields.items() if v is not None})


def reset_context(token: contextvars.Token) -> None:
    _ctx.reset(token)


def current() -> dict:
    return _ctx.get()


class ContextFilter(logging.Filter):
    """Stamp the current context onto a record — but only fields the record does
    not already carry, so an explicit `extra={"trace_id": …}` (used by the app's
    exception handler, which runs after the ContextVar has been reset) wins."""

    def filter(self, record: logging.LogRecord) -> bool:
        d = _ctx.get()
        for k in _FIELDS:
            if not hasattr(record, k):
                setattr(record, k, d.get(k, "-"))
        return True


def configure_logging(level: str = "INFO", fmt: str = FORMAT) -> None:
    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"context": {"()": ContextFilter}},
        "formatters": {"ctx": {"format": fmt}},
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "ctx",
                "filters": ["context"],
            },
        },
        "root": {"level": level, "handlers": ["default"]},
        # Own uvicorn's loggers too — they install their own handlers with
        # propagate=False, so a root-only config never sees access/error lines.
        # uvicorn.access is silenced (WARNING): its line is emitted AFTER the
        # request context is reset (so it would log trace=-) and merely duplicates
        # the richer, in-context `_access_log` middleware line (method/path/status/
        # duration, static skipped). Genuine access-logger warnings still surface.
        "loggers": {
            "uvicorn": {"level": level, "handlers": ["default"], "propagate": False},
            "uvicorn.error": {"level": level, "handlers": ["default"], "propagate": False},
            "uvicorn.access": {"level": "WARNING", "handlers": ["default"], "propagate": False},
        },
    })
