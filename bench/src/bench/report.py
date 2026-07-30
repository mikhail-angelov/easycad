"""`bench report` — markdown summary of a run, optionally vs a baseline (§5, §7).

The headline is not the table but the flipped scenarios (§7): pass→fail is what
gets read every day; the overall percent, once a week.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import stats


def _load_summary(run_dir: Path) -> dict:
    s = run_dir / "summary.json"
    if not s.exists():
        from .grade import grade_run
        return grade_run(run_dir)
    return json.loads(s.read_text())


def _verdict_map(summary: dict) -> dict[str, str]:
    return {r["id"]: r["verdict"] for r in summary.get("scenarios", [])}


def cmd_report(args) -> int:
    run_dir = Path(args.run)
    summary = _load_summary(run_dir)
    sp = summary["scenario_pass_rate"]
    lines: list[str] = []
    b = summary.get("backend", {}) or {}
    lines.append(f"# bench report — {summary.get('run_id')}")
    lines.append("")
    lines.append(f"- backend: **{b.get('name')}** · mode: {summary.get('mode')} "
                 f"· model: {summary.get('model') or '—'} · status: {summary.get('status')}")
    if b.get("name") == "reference":
        lines.append("- ⚠️ reference backend — pipeline self-test, **not** a product measurement")
    lines.append("")
    lines.append(f"**scenario_pass_rate**: {stats.format_rate(sp['k'], sp['n'])}")
    op = summary.get("open_pass_rate_judge") or {"k": 0, "n": 0}
    if op["n"]:
        jd = summary.get("judge") or {}
        jm = jd.get("model", "?")
        gate = jd.get("family_gate", "")
        flag = f" · ⚠️ {gate}" if gate in ("same-family-override", "generator-unknown-override") else ""
        lines.append(f"**open_pass_rate@judge**: {stats.format_rate(op['k'], op['n'])} "
                     f"(judge: {jm} · requested ids, unattested{flag})")
        lines.append("> ⚠️ EXPERIMENTAL — automatic vision-judge grading contradicts "
                     "bench-SPEC §2.3/§5.4 (human blind-review). Not the SPEC metric.")
    if summary.get("unjudged"):
        lines.append(f"**unjudged** (run `bench judge`): {', '.join(summary['unjudged'])}")
    st = summary["stability"]
    if st["n"]:
        lines.append(f"**stability**: {round(100 * st['same'] / st['n'])}% (n={st['n']})")
    if summary["skipped_unvalidated"]:
        lines.append(f"**skipped_unvalidated**: {', '.join(summary['skipped_unvalidated'])}")
    if summary.get("not_run"):
        lines.append(f"**not_run** (partial): {', '.join(summary['not_run'])}")
    if summary.get("status") == "partial":
        lines.append("> ⚠️ partial run — pass_rate is over attempted scenarios only")
    lines.append("")

    if summary["primary_failures"]:
        lines.append("## Primary failures")
        for k, v in summary["primary_failures"].items():
            lines.append(f"- {k}: {v}")
        lines.append("")

    if args.compare:
        base = json.loads(Path(args.compare).read_text())
        lines += _compare_section(base, summary)

    lines.append("## Scenarios")
    lines.append("")
    lines.append("| id | spec | verdict | primary_failure |")
    lines.append("|----|------|---------|-----------------|")
    for r in summary.get("scenarios", []):
        lines.append(f"| {r['id']} | {r.get('spec') or '—'} | {r['verdict']} "
                     f"| {r.get('primary_failure') or ''} |")

    report = "\n".join(lines) + "\n"
    print(report)
    if args.out:
        Path(args.out).write_text(report)
        print(f"\n(written to {args.out})")
    return 0


def _compare_section(base: dict, summary: dict) -> list[str]:
    now = _verdict_map(summary)
    old = _verdict_map(base)
    flipped_up = [sid for sid, v in now.items() if v == "pass" and old.get(sid) == "fail"]
    flipped_down = [sid for sid, v in now.items() if v == "fail" and old.get(sid) == "pass"]
    out = ["## Flipped vs baseline", ""]
    out.append(f"- fail→pass: {', '.join(flipped_up) or 'none'}")
    out.append(f"- **pass→fail: {', '.join(flipped_down) or 'none'}**")
    bsp = base.get("scenario_pass_rate", {})
    if bsp:
        out.append(f"- baseline pass_rate: {stats.format_rate(bsp.get('k', 0), bsp.get('n', 0))}")
    out.append("")
    return out
