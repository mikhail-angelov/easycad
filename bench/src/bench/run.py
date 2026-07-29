"""`bench run` — generate through a backend, save every phase to disk (§2.8, §7).

Five phases each hit the disk so a verdict can be recomputed for free later.
This module owns generation (`run`); grading lives in `grade.py`.

Per-turn layout (§7):
    runs/<id>/<scenario>/attempt-N/turn-M/
        code.py  raw_response.txt  out.step  out.stl  facts.json  gen.json
"""

from __future__ import annotations

import datetime as dt
import importlib.metadata as md
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from easycad_geom.facts import TESSELLATION, compute_facts

from . import paths
from .backend import Backend, TurnResult, make_backend
from .schema import Scenario, all_scenario_ids, load_scenario
from .validate import is_validated


def _git_sha(cwd: Path) -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd,
                              capture_output=True, text=True).stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _ver(pkg: str) -> str:
    try:
        return md.version(pkg)
    except Exception:  # noqa: BLE001
        return "unknown"


def _select_scenarios(which: str, ids: list[str] | None) -> list[Scenario]:
    if ids:
        chosen = ids
    else:
        chosen = all_scenario_ids()
    out = []
    for sid in chosen:
        sc = load_scenario(sid)
        if which == "complete" and not sc.is_complete:
            continue
        if which == "open" and sc.is_complete:
            continue
        out.append(sc)
    return out


def _manifest(run_id: str, backend: Backend, scenarios: list[Scenario],
              attempts: int, seed: int, mode: str) -> dict:
    return {
        "run_id": run_id,
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "mode": mode,                       # derived from backend (§6.2)
        "backend": {"name": backend.name, "config": backend.config},
        "attempts": attempts,
        "sampling_seed": seed,
        "tessellation": TESSELLATION,
        "toolchain": {"cadquery": _ver("cadquery"), "trimesh": _ver("trimesh"),
                      "OCP": _ver("cadquery-ocp")},
        # easycad_geom ships in this same repo, so the app git sha pins it too.
        "git_sha_app": _git_sha(paths.REPO_ROOT),
        "git_sha_easycad_geom": _git_sha(paths.REPO_ROOT),
        "concurrency": 1,                   # runs are sequential (§6.3)
        "retry_policy": "none",             # harness adds no retries; the API may internally
        "scenario_ids": [s.id for s in scenarios],
        "status": "running",
        "cost_usd": 0.0,
        # Provenance the harness CANNOT confirm through the public API, recorded
        # honestly by source (§6.3). model/provider are what the harness REQUESTED;
        # the server may override them (trial keys force their own model), and no
        # response field echoes the actual per-call model. temperature is NOT a
        # ChatRequest parameter, so an operator value is an assertion the harness
        # never sent. None of this is API-confirmable, so it never certifies
        # compliance — it is reported for audit only.
        "provenance": {
            "model_requested": getattr(backend, "model", None),
            "provider_requested": getattr(backend, "provider", None),
            "model_confirmed": None,        # public API does not echo actual model
            "system_prompt": None,
            "system_prompt_sha256": None,
            "sampling_temperature_asserted": None,
            "worker_image_digest": None,
            "note": ("model/provider are requested values; the server may override them "
                     "(e.g. trial keys) and the API does not confirm the actual model. "
                     "temperature is operator-asserted — the public API has no temperature "
                     "parameter, so the harness never set it. Pin the build via git_sha_app."),
        },
    }


def cmd_run(args) -> int:
    if args.attempts < 1:
        print("--attempts must be >= 1")
        return 2
    if args.max_cost < 0:
        print("--max-cost must be >= 0")
        return 2
    if getattr(args, "cost_per_turn", 0.0) < 0:
        print("--cost-per-turn must be >= 0")
        return 2
    # A hard ceiling needs a cost signal. The product API supplies none, so a
    # product run with a finite --max-cost must carry a --cost-per-turn estimate,
    # or explicitly opt out with --max-cost 0 (uncapped). Otherwise the "ceiling"
    # would be silently inert (§7).
    if (args.backend == "product" and args.max_cost > 0
            and getattr(args, "cost_per_turn", 0.0) <= 0):
        print("product run with --max-cost needs --cost-per-turn <usd> to arm the "
              "ceiling (the API returns no cost), or --max-cost 0 to run uncapped")
        return 2
    # Read+decode the prompt file up front — before any run directory exists — so
    # a missing, unreadable or non-UTF-8 file fails cleanly instead of leaving an
    # opaque, manifest-less run dir. Stash the text for _record_known_config.
    spf = getattr(args, "system_prompt_file", None)
    if spf:
        try:
            args.system_prompt_text = Path(spf).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"--system-prompt-file unreadable ({spf}): {exc}")
            return 2

    scenarios = _select_scenarios(args.set, args.ids)
    if not scenarios:
        print("no scenarios selected")
        return 1

    backend = _backend_from_args(args)
    # Seconds + a short random token: two runs of the same set in one minute must
    # not share a directory and interleave their artifacts/manifest.
    import secrets
    run_id = (dt.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
              + f"_{args.set}_{secrets.token_hex(2)}")
    run_dir = paths.RUNS / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    # Mode is derived from the backend, never operator-set: a real product run
    # must not be labellable "selftest"/"offline" while looking compliant.
    mode = "selftest" if backend.name == "reference" else "product"
    manifest = _manifest(run_id, backend, scenarios, args.attempts, args.seed, mode)
    _record_known_config(manifest, args)
    # `product_metric_compliant` is a strong claim — only assert it for the
    # things the harness can actually vouch for: a real product run, enforced
    # artifact integrity, AND provenance it can certify (operator-supplied
    # prompt/sampling). A git-sha-anchored run is still valid, but the harness
    # can't confirm a (possibly remote) server matches that sha, so it isn't
    # self-certified. Reasons are recorded so the bare boolean never misleads.
    # Compliance asserts ONLY what the harness can verify: a real product run and
    # enforced artifact integrity. Provenance (model/prompt/temperature/worker) is
    # NOT API-confirmable, so it is recorded (above) but never gates this flag —
    # gating on unconfirmable assertions would let false provenance certify a run
    # (§8: never certify what you can't verify).
    unverified = bool(getattr(args, "allow_unverified_artifacts", False))
    manifest["artifact_integrity"] = "unverified-allowed" if unverified else "verified"
    notes: list[str] = []
    if backend.name != "product":
        notes.append("not a product run")
    if unverified:
        notes.append("artifact integrity not enforced (--allow-unverified-artifacts)")
    manifest["product_metric_compliant"] = not notes
    manifest["compliance_scope"] = ("verifies product path + artifact integrity only; "
                                    "provenance is operator-asserted, not API-confirmed")
    manifest["compliance_notes"] = notes
    if backend.name == "product" and notes:
        print("⚠ NOT product-metric compliant: " + "; ".join(notes))

    # Reachable for product only when the operator explicitly chose --max-cost 0
    # (the finite-cap-without-estimate case is refused above). Say it's uncapped.
    if backend.name == "product" and args.max_cost <= 0:
        print("⚠ running uncapped: --max-cost 0 and the API reports no per-request cost.")
    # Reproducibility: warn when a product manifest will carry null prompt/sampling
    # (§6.3). Not fatal — git_sha_app anchors it — but the operator should know.
    if backend.name == "product" and not getattr(args, "system_prompt_file", None):
        print("⚠ manifest reproducibility: system prompt / sampling not recorded "
              "(not exposed by the public API). Pass --system-prompt-file / --temperature "
              "to pin them, or rely on git_sha_app.")

    budget = Budget(cap=args.max_cost, per_turn=getattr(args, "cost_per_turn", 0.0))
    status = "complete"
    gate_on = backend.name != "reference"   # reference backend self-tests, no gate
    try:
        for sc in scenarios:
            if gate_on and sc.is_complete and not is_validated(sc):
                _mark_skipped(run_dir / sc.id, "skipped_unvalidated")
                print(f"skip {sc.id}: unvalidated reference (§4.3) → skipped_unvalidated")
                continue
            for attempt in range(1, args.attempts + 1):
                if not budget.can_afford_turn():   # can't even start the next attempt
                    status = "partial"
                    break
                if _run_attempt(backend, sc, run_dir, attempt, budget):
                    status = "partial"             # ceiling hit mid-attempt
                    break
            if status == "partial":
                print(f"⚠ est. cost ${budget.spent:.2f} reached --max-cost "
                      f"${args.max_cost:.2f} — partial run")
                break
    finally:
        # Always persist the manifest, even if the loop aborts — a partial run
        # with a manifest is recoverable; one without is opaque.
        manifest["cost_usd"] = round(budget.spent, 4)
        manifest["status"] = status
        (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        print(f"\nrun written to {run_dir}")

    # Auto-grade unless asked not to, so `bench run` yields a summary directly.
    if not args.no_grade:
        from .grade import grade_run, print_summary
        summary = grade_run(run_dir)
        print_summary(summary)
    return 0


def _record_known_config(manifest: dict, args) -> None:
    """Record operator-asserted provenance for audit. These are ASSERTIONS about
    a self-hosted deployment — the harness cannot confirm them through the public
    API — so they never certify compliance, only aid a human reading the run."""
    prov = manifest["provenance"]
    spf = getattr(args, "system_prompt_file", None)
    text = getattr(args, "system_prompt_text", None)   # pre-read in cmd_run
    if spf and text is not None:
        import hashlib
        prov["system_prompt"] = text
        prov["system_prompt_sha256"] = hashlib.sha256(text.encode()).hexdigest()
        prov["system_prompt_source"] = f"operator-supplied: {spf}"
    temp = getattr(args, "temperature", None)
    if temp is not None:
        prov["sampling_temperature_asserted"] = temp
    digest = getattr(args, "worker_image_digest", None)
    if digest:
        prov["worker_image_digest"] = digest


def _backend_from_args(args) -> Backend:
    if args.backend == "reference":
        return make_backend("reference")
    # The repair loop lives on the server (EASYCAD_MAX_REPAIR); bench can't detect
    # it over HTTP, so the operator declares it here and we stamp it in the manifest
    # config. Unset → enabled=null (undeclared), NOT False — never claim off blindly.
    ql = getattr(args, "quality_loop", None)
    config = {"quality_loop": {"enabled": {"on": True, "off": False}.get(ql)}}
    return make_backend("product", base_url=args.url, provider=args.provider,
                        model=args.model, est_cost_per_turn=getattr(args, "cost_per_turn", 0.0),
                        allow_unverified=getattr(args, "allow_unverified_artifacts", False),
                        config=config)


def _mark_skipped(sdir: Path, reason: str) -> None:
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "skipped.json").write_text(json.dumps({"reason": reason}))


@dataclass
class Budget:
    """A hard spend ceiling reserved per turn (§7). `per_turn` is the operator's
    estimate; 0 disables the cap (no cost signal available)."""
    cap: float
    per_turn: float
    spent: float = 0.0

    def can_afford_turn(self) -> bool:
        # cap <= 0 = explicitly uncapped; per_turn <= 0 = no cost signal.
        return self.cap <= 0 or self.per_turn <= 0 or self.spent + self.per_turn <= self.cap

    def charge(self, amount: float) -> None:
        self.spent += amount


def _run_attempt(backend: Backend, sc: Scenario, run_dir: Path, attempt: int,
                 budget: "Budget") -> bool:
    """Run one attempt. Returns True if the cost ceiling was hit mid-attempt."""
    # Starting a session is itself a public-API call (e.g. /api/session/reset).
    # A down or 5xx server must be recorded as this scenario's generation_error,
    # not crash the whole run before the manifest is written (§2.6).
    try:
        session = backend.start_session(sc)
    except Exception as exc:  # noqa: BLE001
        tdir = run_dir / sc.id / f"attempt-{attempt}" / "turn-1"
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / "gen.json").write_text(json.dumps(
            {"error": {"stage": "generate", "message": f"session start failed: {exc}"}}, indent=2))
        print(f"  {sc.id} a{attempt}: session start failed — {exc}")
        return False
    hit_cap = False
    try:
        for i, turn in enumerate(sc.turns, 1):
            # Reserve before each turn, not once per attempt: a multi-turn attempt
            # spends per turn and would otherwise overshoot the ceiling.
            if not budget.can_afford_turn():
                hit_cap = True
                break
            try:
                tr = session.send(turn.prompt)
            except Exception as exc:  # noqa: BLE001 — send() should never raise; safety net
                tr = TurnResult(None, "", f"unexpected send() error: {exc}", "generate")
            budget.charge(tr.cost_usd)
            tdir = run_dir / sc.id / f"attempt-{attempt}" / f"turn-{i}"
            tdir.mkdir(parents=True, exist_ok=True)
            if tr.code:
                (tdir / "code.py").write_text(tr.code)
            (tdir / "raw_response.txt").write_text(tr.raw_response or "")
            gen = {"error": None, "cost_usd": tr.cost_usd, "latency_ms": tr.latency_ms,
                   "internal_retries": tr.internal_retries}
            if tr.error:
                gen["error"] = {"stage": tr.error_stage or "execute", "message": tr.error}
                (tdir / "gen.json").write_text(json.dumps(gen, indent=2))
                print(f"  {sc.id} a{attempt} t{i}: FAIL ({tr.error_stage}) {tr.error[:60]}")
                break   # a failed turn ends the attempt; later turns can't build on it
            import hashlib
            if tr.step_bytes:
                (tdir / "out.step").write_bytes(tr.step_bytes)
                gen["step_sha256"] = hashlib.sha256(tr.step_bytes).hexdigest()
            if tr.stl_bytes:
                (tdir / "out.stl").write_bytes(tr.stl_bytes)
                gen["stl_sha256"] = hashlib.sha256(tr.stl_bytes).hexdigest()
            if tr.step_bytes and tr.stl_bytes:
                facts = compute_facts(tdir / "out.step", tdir / "out.stl")
            else:
                facts = {"error": "missing STEP or STL artifact"}
            (tdir / "facts.json").write_text(json.dumps(facts, indent=2))
            (tdir / "gen.json").write_text(json.dumps(gen, indent=2))
            print(f"  {sc.id} a{attempt} t{i}: generated ({tr.latency_ms} ms)")
    finally:
        session.close()
    return hit_cap
