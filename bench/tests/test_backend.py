"""Product backend robustness (bench-SPEC §2.6, §6.1).

A malformed or hostile API response must become a recorded generation_error, not
an exception that aborts the whole run. STEP downloads must be verified against
the server's content hash.
"""

import base64
import hashlib
import io
import json

import pytest

from bench.backend import ProductBackend, ProductSession
from bench.run import Budget


class _Resp:
    def __init__(self, body: bytes, headers: dict | None = None):
        self._body = body
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Opener:
    """Fake urllib opener: routes /api/chat vs the STEP export by URL/method."""

    def __init__(self, chat=None, step=None):
        self.chat = chat            # _Resp for POST /api/chat
        self.step = step            # _Resp for GET .../step

    def open(self, req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        if url.endswith("/step"):
            if isinstance(self.step, Exception):
                raise self.step
            return self.step
        if isinstance(self.chat, Exception):
            raise self.chat
        return self.chat


def _session(chat=None, step=None) -> ProductSession:
    # Bypass __init__ (which would hit the network) and inject a fake opener.
    s = ProductSession.__new__(ProductSession)
    s.b = ProductBackend("http://x", est_cost_per_turn=0.01)
    s.opener = _Opener(chat, step)
    s.current_code = None
    return s


STL = b"solid\nendsolid\n"


def _chat_ok(step_bytes: bytes, stl_sha=...) -> _Resp:
    step = {"id": 7, "success": True, "code": "result = None",
            "stl_base64": base64.b64encode(STL).decode()}
    # Default: the correct STL hash the current server would send.
    step["stl_sha256"] = hashlib.sha256(STL).hexdigest() if stl_sha is ... else stl_sha
    if step["stl_sha256"] is None:
        del step["stl_sha256"]
    return _Resp(json.dumps({"step": step}).encode())


def test_non_json_response_is_generation_error():
    tr = _session(chat=_Resp(b"<html>502 Bad Gateway</html>")).send("hi")
    assert tr.error is not None and tr.error_stage == "generate"
    assert tr.step_bytes is None            # did not raise


def test_malformed_200_still_charges_cost():
    # A 2xx with an unparseable body means the LLM was billed — the estimate must
    # be charged so repeated malformed 200s can't slip past --max-cost.
    tr = _session(chat=_Resp(b"<html>not json</html>")).send("hi")
    assert tr.error is not None and tr.error_stage == "generate"
    assert tr.cost_usd == 0.01


def test_non_dict_response_is_generation_error():
    tr = _session(chat=_Resp(b"[1, 2, 3]")).send("hi")
    assert "not a JSON object" in tr.error
    assert tr.error_stage == "generate"


def test_http_error_is_generation_error():
    import urllib.error
    exc = urllib.error.HTTPError("http://x/api/chat", 500, "Boom", {}, io.BytesIO(b"err"))
    tr = _session(chat=exc).send("hi")
    assert tr.error_stage == "generate" and "500" in tr.error


def test_step_sha_mismatch_fails_the_turn():
    step_bytes = b"ISO-10303-21;..."
    bad = _Resp(step_bytes, {"X-Content-SHA256": "deadbeef"})   # wrong hash
    tr = _session(chat=_chat_ok(step_bytes), step=bad).send("hi")
    assert tr.error is not None
    assert "mismatch" in tr.error.lower()


def test_step_sha_match_returns_artifact():
    step_bytes = b"ISO-10303-21;..."
    good = _Resp(step_bytes, {"X-Content-SHA256": hashlib.sha256(step_bytes).hexdigest()})
    tr = _session(chat=_chat_ok(step_bytes), step=good).send("hi")
    assert tr.error is None
    assert tr.step_bytes == step_bytes
    assert tr.cost_usd == 0.01              # estimate charged on success


def test_stl_sha_mismatch_fails_the_turn():
    step_bytes = b"ISO-10303-21;..."
    good_step = _Resp(step_bytes, {"X-Content-SHA256": hashlib.sha256(step_bytes).hexdigest()})
    tr = _session(chat=_chat_ok(step_bytes, stl_sha="deadbeef"), step=good_step).send("hi")
    assert tr.error is not None and "stl" in tr.error.lower() and "mismatch" in tr.error.lower()


def test_stl_missing_sha_fails_closed():
    step_bytes = b"ISO-10303-21;..."
    good_step = _Resp(step_bytes, {"X-Content-SHA256": hashlib.sha256(step_bytes).hexdigest()})
    tr = _session(chat=_chat_ok(step_bytes, stl_sha=None), step=good_step).send("hi")
    assert tr.error is not None and "stl" in tr.error.lower() and "missing" in tr.error.lower()


def test_missing_sha_header_fails_closed():
    step_bytes = b"ISO-10303-21;..."
    no_hdr = _Resp(step_bytes, {})           # server did not send X-Content-SHA256
    tr = _session(chat=_chat_ok(step_bytes), step=no_hdr).send("hi")
    assert tr.error is not None
    assert "missing" in tr.error.lower()


def test_missing_sha_header_accepted_with_optout():
    step_bytes = b"ISO-10303-21;..."
    s = _session(chat=_chat_ok(step_bytes), step=_Resp(step_bytes, {}))
    s.b.allow_unverified = True              # opt out for an older server
    tr = s.send("hi")
    assert tr.error is None and tr.step_bytes == step_bytes


def test_artifact_failure_still_charges_cost():
    # Chat succeeded (LLM billed) but the STEP hash is wrong → the turn fails,
    # yet the estimate must still be charged so repeated failures can't slip the cap.
    step_bytes = b"ISO-10303-21;..."
    bad = _Resp(step_bytes, {"X-Content-SHA256": "deadbeef"})
    tr = _session(chat=_chat_ok(step_bytes), step=bad).send("hi")
    assert tr.error is not None
    assert tr.cost_usd == 0.01


def test_budget_reserves_per_turn():
    b = Budget(cap=0.03, per_turn=0.02)
    assert b.can_afford_turn()              # 0.02 <= 0.03
    b.charge(0.02)
    assert not b.can_afford_turn()          # 0.04 would exceed 0.03 → stop before turn 2


def test_budget_zero_estimate_never_caps():
    b = Budget(cap=0.0, per_turn=0.0)
    b.charge(0.0)
    assert b.can_afford_turn()              # no cost signal → cap disabled


def test_budget_uncapped_when_cap_nonpositive():
    b = Budget(cap=0.0, per_turn=0.02)      # --max-cost 0 = explicitly uncapped
    b.charge(100.0)
    assert b.can_afford_turn()


def test_product_run_refuses_cap_without_estimate():
    from bench.cli import build_parser
    from bench.run import cmd_run
    args = build_parser().parse_args(
        ["run", "--backend", "product", "--max-cost", "5", "--ids", "001-plate-holes"])
    assert cmd_run(args) == 2               # finite cap, no --cost-per-turn → refuse
