"""Backends the harness generates through (bench-SPEC §6.1).

The harness goes through the *public API* (§2.6): parse errors, worker timeouts,
limits and retries are half of real failures and must not bypass the benchmark.

Two backends implement the same Protocol:

* `ProductBackend` — the real thing, over HTTP against a running EasyCAD server.
  This is the only backend whose numbers are a product measurement. It costs
  money and needs a key; an ablation is *this backend with a different config
  flag* (§6.1), never a second implementation.
* `ReferenceBackend` — "generates" by running the scenario's own reference. It
  spends nothing and every scenario passes, so it exercises the run→grade→report
  pipeline itself. Its name is recorded in the manifest; it is never a product
  measurement and the reporter labels it as such.
"""

from __future__ import annotations

import http.cookiejar
import json
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .cadexec import CadError, run_and_export
from .schema import Scenario


@dataclass
class TurnResult:
    code: str | None
    raw_response: str
    error: str | None                 # None on success
    error_stage: str | None = None    # "generate" | "execute" | None
    step_bytes: bytes | None = None
    stl_bytes: bytes | None = None
    cost_usd: float = 0.0
    latency_ms: int = 0
    internal_retries: int = 0


class Session(Protocol):
    def send(self, prompt: str) -> TurnResult: ...
    def close(self) -> None: ...


class Backend(Protocol):
    name: str
    config: dict
    def start_session(self, scenario: Scenario) -> Session: ...


# ── reference backend (offline pipeline check) ───────────────────────────────

class ReferenceSession:
    def __init__(self, scenario: Scenario):
        self.scenario = scenario
        self.turn = 0

    def send(self, prompt: str) -> TurnResult:
        self.turn += 1
        ref = self.scenario.turns[self.turn - 1].reference
        code = (self.scenario.dir / ref).read_text()
        t0 = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            step = Path(tmp) / "m.step"
            stl = Path(tmp) / "m.stl"
            try:
                run_and_export(code, step, stl)
            except CadError as exc:
                return TurnResult(code, code, str(exc), "execute",
                                  latency_ms=int(1000 * (time.time() - t0)))
            return TurnResult(code, code, None, None,
                              step_bytes=step.read_bytes(), stl_bytes=stl.read_bytes(),
                              latency_ms=int(1000 * (time.time() - t0)))

    def close(self) -> None:
        pass


class ReferenceBackend:
    name = "reference"
    config: dict = {"note": "pipeline self-test, not a product measurement"}

    def start_session(self, scenario: Scenario) -> Session:
        return ReferenceSession(scenario)


# ── product backend (real generation, over HTTP) ─────────────────────────────

class ProductSession:
    """Drives one project through the public API, accumulating code turn by turn.

    Each turn: POST /api/chat (auto_refine off, so it generates rather than
    triaging), read the committed step, then fetch its STEP export. STL comes
    back inline as base64; STEP needs the export endpoint (§6.1).
    """

    def __init__(self, backend: "ProductBackend"):
        self.b = backend
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.current_code: str | None = None
        # Prime a session cookie and reset any prior project state.
        self._post("/api/session/reset", {})

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(
            self.b.base_url + path,
            data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        with self.opener.open(req, timeout=self.b.timeout_s) as r:
            return json.loads(r.read().decode())

    def _verify_stl(self, stl_bytes, server_sha) -> None:
        """Verify the inline STL against the server's stl_sha256. Same fail-closed
        contract as the STEP download: no hash ⇒ unverifiable ⇒ error unless the
        operator opted out (§6.1)."""
        if stl_bytes is None:
            return
        import hashlib
        if not server_sha:
            if self.b.allow_unverified:
                return
            raise ValueError("STL missing stl_sha256 — integrity not verifiable "
                             "(pass --allow-unverified-artifacts to accept)")
        if hashlib.sha256(stl_bytes).hexdigest() != server_sha:
            raise ValueError("STL artifact sha256 mismatch (transport corruption)")

    def _get_verified_step(self, step_id) -> bytes:
        """Download the STEP export and verify it against the server's
        X-Content-SHA256. Raises on any transport error or hash mismatch so the
        caller records the turn as a failure rather than measuring a truncated
        or stale artifact (§6.1)."""
        import hashlib
        with self.opener.open(f"{self.b.base_url}/api/export/{step_id}/step",
                              timeout=self.b.timeout_s) as r:
            data = r.read()
            server_sha = r.headers.get("X-Content-SHA256")
        # Fail closed: no server hash ⇒ integrity can't be verified. Accept only
        # if explicitly told to (e.g. an older server that predates the header).
        if not server_sha:
            if self.b.allow_unverified:
                return data
            raise ValueError("STEP artifact missing X-Content-SHA256 header — integrity "
                             "not verifiable (pass --allow-unverified-artifacts to accept)")
        if hashlib.sha256(data).hexdigest() != server_sha:
            raise ValueError("STEP artifact sha256 mismatch (download corrupt or stale)")
        return data

    def send(self, prompt: str) -> TurnResult:
        import base64
        t0 = time.time()

        def elapsed():
            return int(1000 * (time.time() - t0))

        payload = {"prompt": prompt, "current_code": self.current_code,
                   "auto_refine": False}
        if self.b.provider:
            payload["provider"] = self.b.provider
        if self.b.model:
            payload["model"] = self.b.model
        # One protective envelope: every failure mode below — HTTP status, dead
        # socket, non-JSON body, non-dict shape, bad base64, sha mismatch — must
        # become a recorded generation_error, never an exception that aborts the
        # whole run (§2.6). `cost` becomes the estimate the moment a 2xx arrives:
        # a 200 means the call reached the LLM and was billed, so it must be
        # charged even if the body is then unparseable — otherwise repeated
        # malformed-200s slip past --max-cost. urllib raises HTTPError for non-2xx,
        # so reaching the read/parse below already implies a billed 2xx.
        cost = 0.0
        req = urllib.request.Request(
            self.b.base_url + "/api/chat", data=json.dumps(payload).encode(),
            method="POST", headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=self.b.timeout_s) as r:
                cost = self.b.est_cost_per_turn      # 2xx received ⇒ billed
                body = r.read()
        except urllib.error.HTTPError as exc:
            return TurnResult(None, exc.read().decode(errors="replace"),
                              f"HTTP {exc.code}: {exc.reason}", "generate", latency_ms=elapsed())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return TurnResult(None, str(exc), f"transport: {exc}", "generate",
                              cost_usd=cost, latency_ms=elapsed())
        try:
            resp = json.loads(body.decode())
        except (ValueError, UnicodeDecodeError) as exc:
            return TurnResult(None, "", f"malformed response body: {exc}", "generate",
                              cost_usd=cost, latency_ms=elapsed())

        raw = ""
        try:
            if not isinstance(resp, dict):
                return TurnResult(None, str(resp)[:500], "malformed response: not a JSON object",
                                  "generate", cost_usd=cost, latency_ms=elapsed())
            raw = json.dumps(resp)
            step = resp.get("step") or {}
            code = step.get("code")
            self.current_code = code or self.current_code
            if not step.get("success"):
                return TurnResult(code, raw, step.get("error") or "generation failed",
                                  "execute", cost_usd=cost, latency_ms=elapsed())
            stl_b64 = step.get("stl_base64")
            stl_bytes = base64.b64decode(stl_b64) if stl_b64 else None
            self._verify_stl(stl_bytes, step.get("stl_sha256"))
            step_bytes = self._get_verified_step(step.get("id"))
            return TurnResult(code, raw, None, None, step_bytes=step_bytes,
                              stl_bytes=stl_bytes, cost_usd=cost, latency_ms=elapsed())
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            return TurnResult(None, raw, f"artifact download failed: {exc}", "execute",
                              cost_usd=cost, latency_ms=elapsed())
        except Exception as exc:  # noqa: BLE001 — bad base64, sha mismatch, unexpected shape
            return TurnResult(None, raw, f"artifact/response error: {type(exc).__name__}: {exc}",
                              "execute", cost_usd=cost, latency_ms=elapsed())

    def close(self) -> None:
        pass


class ProductBackend:
    def __init__(self, base_url: str, provider: str | None = None,
                 model: str | None = None, timeout_s: int = 180,
                 est_cost_per_turn: float = 0.0, allow_unverified: bool = False,
                 config: dict | None = None):
        self.base_url = base_url.rstrip("/")
        self.provider = provider
        self.model = model
        self.timeout_s = timeout_s
        # The public API returns no per-request cost, so --max-cost can only bite
        # on an operator-supplied estimate. 0 = unknown ⇒ cap disabled.
        self.est_cost_per_turn = est_cost_per_turn
        # Accept a STEP with no X-Content-SHA256 (older server); off = fail closed.
        self.allow_unverified = allow_unverified
        self.name = "product"
        self.config = config or {"quality_loop": {"enabled": False}}

    def start_session(self, scenario: Scenario) -> Session:
        return ProductSession(self)


def make_backend(kind: str, **kw) -> Backend:
    if kind == "reference":
        return ReferenceBackend()
    if kind == "product":
        return ProductBackend(**kw)
    raise ValueError(f"unknown backend {kind!r}")
