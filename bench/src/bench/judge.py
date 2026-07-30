"""LLM-judge grading for `open` scenarios (bench-SPEC §4, M1).

Open scenarios have no ground-truth reference; a HYBRID rubric decides pass:

  - `checks`  — geometric assertions auto-verified from measured facts (facts.json).
                Absolute size and topology are NOT legible in an unscaled render,
                so they never go to the vision judge (calibration finding: a cheap
                vision model reads gross shape well but cannot read millimetres).
  - `rubric`  — binary VISUAL items a vision model answers from four renders.

The judge call is non-deterministic, costs money, and needs a network + key, so it
runs as an explicit `bench judge` step that caches its verdict to
`attempt-N/judge.json`. `grade` then reads that cache and stays pure and free
(§2.8). The judge model is recorded in the manifest for reproducibility (§6.2), and
must be a DIFFERENT model family than the generator (anti-self-confirmation, §4.3).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path

from .render import render_stl
from .schema import CHECK_MEASURES, Scenario, load_scenario

# The judge is an OpenRouter (OpenAI-compatible) vision model by default; both are
# overridable so the harness isn't wired to one provider. Read LAZILY (not at
# import) so values set only in .env — loaded inside cmd_judge — are honoured.
_DEFAULT_JUDGE_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_JUDGE_KEY_ENV = "OPEN_ROUTER_KEY"


def judge_base_url() -> str:
    return os.getenv("BENCH_JUDGE_BASE_URL", _DEFAULT_JUDGE_BASE_URL)


def judge_key_env() -> str:
    return os.getenv("BENCH_JUDGE_KEY_ENV", _DEFAULT_JUDGE_KEY_ENV)

# The fixed instruction skeleton (title + checklist filled per scenario). Hashed
# into the judge provenance so a run records exactly how it was graded (§6.3).
_JUDGE_INSTRUCTION = (
    "You are grading a 3D CAD model (intended object: {title}) against a checklist, "
    "using ONLY the rendered images — four viewpoints of the SAME object. For EACH "
    "item answer strictly true or false from what you can actually SEE. Do not give "
    "the benefit of the doubt, and do NOT judge absolute size (the renders have no "
    "scale bar). Return ONLY a JSON object: "
    '{{"items":[{{"n":1,"pass":true,"why":"<short>"}}, ...]}}.\n\nChecklist:\n{checklist}'
)


def prompt_template_sha() -> str:
    return hashlib.sha256(_JUDGE_INSTRUCTION.encode()).hexdigest()[:16]


def sha256_or_none(path: Path) -> str | None:
    """sha256 of a file's bytes, or None if it's missing — used to bind a cached
    judge verdict to the exact facts.json/out.stl it was computed from."""
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def compute_fact_items(facts: dict, sc: Scenario) -> list[dict]:
    """The DETERMINISTIC items — the three §8 invariants + one per geometric check —
    derived purely from measured facts. Used both when judging and when regrading,
    so `grade` recomputes them from the current `facts.json` and never trusts an
    (editable) cached fact verdict; only the visual items are cache-trusted."""
    cc = facts.get("coordinate_contract") or {}
    items = [
        {"kind": "fact", "text": "valid", "pass": bool(facts.get("valid")),
         "actual": facts.get("valid")},
        {"kind": "fact", "text": "watertight", "pass": bool(facts.get("watertight")),
         "actual": facts.get("watertight")},
        {"kind": "fact", "text": "coordinate_contract", "pass": bool(cc.get("ok")),
         "actual": {"z_min_mm": cc.get("z_min_mm"), "xy_center_mm": cc.get("xy_center_mm")}},
    ]
    items += [eval_check(facts, c) for c in sc.checks]
    return items


def model_family(model: str | None) -> str | None:
    """Coarse model family for the anti-self-confirmation check: the leading
    alphabetic token of the model stem. 'google/gemma-3-27b-it' → 'gemma',
    'deepseek/deepseek-v4-flash' → 'deepseek'."""
    if not model:
        return None
    stem = model.split("/")[-1].lower()
    m = re.match(r"[a-z]+", stem)
    return m.group(0) if m else stem


def _measure(facts: dict, name: str):
    """Pull one scalar measure from a facts.json dict, or None if unavailable."""
    bbox = facts.get("bbox_mm") or []
    cc = facts.get("coordinate_contract") or {}
    return {
        "bodies": facts.get("bodies"),
        "volume_mm3": facts.get("volume_mm3"),
        "largest_dim_mm": max(bbox) if bbox else None,
        "smallest_dim_mm": min(bbox) if bbox else None,
        "x_mm": bbox[0] if len(bbox) > 0 else None,
        "y_mm": bbox[1] if len(bbox) > 1 else None,
        "z_mm": bbox[2] if len(bbox) > 2 else None,
        "z_min_mm": cc.get("z_min_mm"),
        "watertight": facts.get("watertight"),
        "valid": facts.get("valid"),
    }.get(name)


def eval_check(facts: dict, check: dict) -> dict:
    """Evaluate one geometric assertion against measured facts. Unmeasurable
    (measure missing) counts as fail — same invariant as the complete grader (§8)."""
    name, op, val = check.get("measure"), check.get("op"), check.get("value")
    actual = _measure(facts, name)
    ok = False
    if actual is not None:
        try:
            ok = {
                "eq": lambda: actual == val,
                "ge": lambda: actual >= val,
                "le": lambda: actual <= val,
                "gt": lambda: actual > val,
                "lt": lambda: actual < val,
                "between": lambda: val[0] <= actual <= val[1],
                "approx": lambda: abs(actual - val) <= check.get("tol", 0.5),
            }[op]()
        except (TypeError, IndexError, KeyError):
            ok = False
    return {"kind": "fact", "text": f"{name} {op} {val}", "pass": bool(ok), "actual": actual}


def _judge_client():
    from openai import OpenAI
    key_env = judge_key_env()
    key = os.getenv(key_env)
    if not key:
        raise RuntimeError(f"judge needs the {key_env} env var (an OpenRouter key)")
    return OpenAI(base_url=judge_base_url(), api_key=key, timeout=120)


def _parse_items(raw: str, n_items: int) -> dict:
    """Index the model's items by their `n` (1..n_items). Fully fail-closed on a
    malformed response — a non-object body, non-list `items`, or an entry whose
    `n` is not a real int in range (rejects `1.9`, `"1"`, `true`, out-of-range,
    duplicates) is skipped, its rubric item left unanswered → fail. Never raises,
    so one bad line can't abort `bench judge`."""
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return {}
    out: dict = {}
    for it in data["items"]:
        if not isinstance(it, dict):
            continue
        n = it.get("n")
        # `type(n) is int` rejects bool (its subclass) and float/str; range + dedup.
        if type(n) is int and 1 <= n <= n_items and n not in out:
            out[n] = it
    return out


def judge_visual(render_paths: list[Path], rubric: list[str], model: str, title: str,
                 client=None) -> list[dict]:
    """Ask the vision model to grade each VISUAL rubric item from the renders.
    A missing/unparoseable answer for an item counts as fail (fail-closed)."""
    checklist = "\n".join(f"{i}. {t}" for i, t in enumerate(rubric, 1))
    prompt = _JUDGE_INSTRUCTION.format(title=title, checklist=checklist)
    content: list[dict] = [{"type": "text", "text": prompt}]
    for p in render_paths:
        b64 = base64.b64encode(Path(p).read_bytes()).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
    client = client or _judge_client()
    resp = client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": content}],
        temperature=0, max_tokens=800)
    items = _parse_items(resp.choices[0].message.content or "", len(rubric))
    out = []
    for i, text in enumerate(rubric, 1):
        it = items.get(i, {})
        # Strict: only a real JSON `true` passes. A string "false", 0, None, or a
        # missing answer all fail-closed — the model's word must be unambiguous.
        out.append({"kind": "visual", "text": text,
                    "pass": it.get("pass") is True, "why": it.get("why", "")})
    return out


def judge_attempt(sc: Scenario, adir: Path, model: str, client=None,
                  provenance: dict | None = None) -> dict:
    """Grade one open-scenario attempt: auto checks + vision rubric → judge.json.
    Passes iff every check AND every rubric item passes. `provenance` (how the
    judge was configured) is stamped into judge.json so each cached verdict is
    self-describing (§6.3)."""
    prov = {**(provenance or {"model": model}),
            "judged_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    tdir = adir / "turn-1"
    facts_p, stl = tdir / "facts.json", tdir / "out.stl"
    if not facts_p.exists() or not stl.exists():
        return _write(adir, {"verdict": "fail", "primary_failure": "empty_result",
                             "judge": prov, "items": [], "note": "no artifact to judge"})
    facts = json.loads(facts_p.read_text())
    if facts.get("error"):
        return _write(adir, {"verdict": "fail", "primary_failure": "invalid_geometry",
                             "judge": prov, "items": [], "note": facts["error"]})

    # Mandatory geometry invariants + geometric checks (bench-SPEC §8, fail-closed)
    # — open grading bypasses the complete grader, so it must still carry its
    # universal checks: an invalid / non-watertight / off-contract model can never
    # pass just because it has the right body count, size, and a good visual verdict.
    items = compute_fact_items(facts, sc)
    if sc.rubric:
        renders = render_stl(stl, tdir / "renders")
        if renders:
            items += judge_visual(renders, sc.rubric, model, sc.title, client=client)
        else:
            items.append({"kind": "visual", "text": "renders", "pass": False,
                          "why": "no renders produced"})

    first_fail = next((it for it in items if not it["pass"]), None)
    primary = None
    if first_fail:
        primary = "fact_check_fail" if first_fail["kind"] == "fact" else "rubric_fail"
    return _write(adir, {"verdict": "pass" if first_fail is None else "fail",
                         "primary_failure": primary, "judge": prov, "items": items,
                         # Bind the verdict to the exact inputs it was computed from,
                         # so a later STL/facts change makes the cache stale (§6.3).
                         "inputs": {"facts_sha": sha256_or_none(facts_p),
                                    "stl_sha": sha256_or_none(stl)}})


def _write(adir: Path, result: dict) -> dict:
    # Write to a temp sibling then replace, so an API error mid-run can never leave
    # a half-written judge.json (§6.3 — a cache file always describes a real grade).
    tmp = adir / "judge.json.tmp"
    tmp.write_text(json.dumps(result, indent=2))
    tmp.replace(adir / "judge.json")
    return result


def cmd_judge(args) -> int:
    """`bench judge <run> --judge-model <id>`: render + vision-grade every open
    scenario attempt, cache judge.json, record judge provenance, regrade.

    EXPERIMENTAL: automatic grading of open scenarios contradicts bench-SPEC §2.3
    / §5.4 (open is human blind-review). Its metric is reported separately as
    `open_pass_rate@judge`, never as the SPEC's human `open_pass_rate`. See §6.
    """
    from dotenv import load_dotenv
    from . import paths
    load_dotenv(paths.REPO_ROOT / ".env")

    run_dir = Path(args.run)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    model = args.judge_model
    open_ids = [s for s in manifest.get("scenario_ids", []) if load_scenario(s).spec == "open"]

    # Anti-self-confirmation (§4.3): the judge must not be the generator's family.
    # NB: neither id is provider-ATTESTED — `model_requested` is only what the run
    # asked for (the server may substitute, and the public API doesn't echo the
    # model), and the judge model is likewise a request. So the gate is a
    # best-effort check on REQUESTED ids and the result is NEVER marked "verified".
    # An UNKNOWN generator (the common product case) fails CLOSED.
    gen_model = (manifest.get("provenance") or {}).get("model_requested")
    jf, gf = model_family(model), model_family(gen_model)
    unknown_gen = gf is None
    family_gate = "distinct"
    if unknown_gen and not getattr(args, "allow_unknown_generator", False):
        print("✗ generator model not recorded in the manifest — cannot check "
              "judge ≠ generator family. Pass --allow-unknown-generator to grade "
              "anyway (recorded as generator_unknown; result stays unattested).")
        return 2
    if unknown_gen:
        family_gate = "generator-unknown-override"
    elif jf == gf and not getattr(args, "allow_same_family", False):
        print(f"✗ judge family '{jf}' == generator family '{gf}' (requested ids) — "
              "refusing (self-confirmation). Pick a different --judge-model, or pass "
              "--allow-same-family to override deliberately.")
        return 2
    elif jf == gf:
        family_gate = "same-family-override"

    # A prior judge pass already spent money; don't silently re-spend + overwrite.
    already = [a for sid in open_ids for a in (run_dir / sid).glob("attempt-*/judge.json")]
    if already and not getattr(args, "force", False):
        print(f"✗ {len(already)} attempt(s) already judged. Re-judging spends money "
              "and supersedes the cache — pass --force to do it deliberately.")
        return 2

    client = _judge_client()  # fail fast on a missing key before any work

    # Immutable provenance recorded BEFORE any call, so partial/failed runs still
    # describe how their judge.json were produced (§6.3). A per-invocation id lets
    # grade count ONLY this pass's caches, never a stale mix from an earlier model.
    import secrets
    prov = {
        "judge_run_id": time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(3),
        "model": model, "judge_model_requested": model, "family": jf,
        "base_url": judge_base_url(),
        "prompt_template_sha": prompt_template_sha(), "temperature": 0,
        "generator_model_requested": gen_model,
        # Model ids are REQUESTED, not provider-attested (§6.3) — never claim verified.
        "attested": False,
        "family_gate": family_gate,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    manifest["judge"] = prov
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    print("⚠ EXPERIMENTAL: automatic open grading contradicts bench-SPEC §2.3/§5.4 "
          "(human blind-review). Reported as open_pass_rate@judge, not the SPEC metric.")
    n = 0
    for sid in manifest.get("scenario_ids", []):
        sc = load_scenario(sid)
        if sc.spec != "open":
            continue
        for adir in sorted((run_dir / sid).glob("attempt-*")):
            r = judge_attempt(sc, adir, model, client=client, provenance=prov)
            n += 1
            fails = [it["text"] for it in r["items"] if not it["pass"]]
            print(f"  {sid} {adir.name}: {r['verdict']}"
                  + (f"  (failed: {'; '.join(fails)[:80]})" if fails else ""))
    print(f"judged {n} open attempt(s) with {model}")

    from .grade import grade_run, print_summary
    print_summary(grade_run(run_dir))
    return 0
