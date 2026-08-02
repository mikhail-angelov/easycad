"""App build id, resolved once at import (SPEC21 W1).

Images ship no `.git`, so a `git rev-parse` yields nothing in prod — the version
is baked into the image as `ENV EASYCAD_VERSION` (a Dockerfile build arg CI sets
from the git tag/SHA). A runtime env still overrides it; the git fallback keeps
local dev honest; `"unknown"` is the last resort.
"""

import os
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _resolve() -> str:
    env = (os.getenv("EASYCAD_VERSION") or "").strip()
    if env:
        return env
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2, cwd=str(_ROOT),
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:  # noqa: BLE001 — git absent / not a repo / timeout
        pass
    return "unknown"


VERSION = _resolve()
