"""Per-request logging context for the worker (SPEC21 W1) — trace-only mirror.

Same shape as the app's `app/log_context.py`, but the worker has no user
identity or session: it logs `trace_id` (seeded from the inbound `X-Trace-Id`)
and `version` (from the baked `EASYCAD_VERSION` env) only, so a worker log line
correlates to the app request that spawned it.
"""

import contextvars
import logging
import logging.config
import os

_FIELDS = ("trace_id", "version")
_DEFAULTS = {"trace_id": "-", "version": (os.getenv("EASYCAD_VERSION") or "unknown").strip() or "unknown"}

_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar("worker_log_context", default=_DEFAULTS)

VERSION = _DEFAULTS["version"]
FORMAT = "%(asctime)s %(levelname)s %(name)s [trace=%(trace_id)s v=%(version)s] %(message)s"


def set_context(trace_id: str | None) -> contextvars.Token:
    d = dict(_DEFAULTS)
    if trace_id:
        d["trace_id"] = trace_id
    return _ctx.set(d)


def reset_context(token: contextvars.Token) -> None:
    _ctx.reset(token)


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        d = _ctx.get()
        for k in _FIELDS:
            if not hasattr(record, k):
                setattr(record, k, d.get(k, "-"))
        return True


def configure_logging(level: str = "INFO") -> None:
    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"context": {"()": ContextFilter}},
        "formatters": {"ctx": {"format": FORMAT}},
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "ctx",
                "filters": ["context"],
            },
        },
        "root": {"level": level, "handlers": ["default"]},
        # uvicorn.access silenced (WARNING): the worker's own `_trace_context`
        # middleware emits the access line in-context (with the trace), so uvicorn's
        # post-reset duplicate would only add trace=- noise.
        "loggers": {
            "uvicorn": {"level": level, "handlers": ["default"], "propagate": False},
            "uvicorn.error": {"level": level, "handlers": ["default"], "propagate": False},
            "uvicorn.access": {"level": "WARNING", "handlers": ["default"], "propagate": False},
        },
    })
