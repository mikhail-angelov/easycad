"""`python -m bench <command>` — CLI entry point (bench-SPEC §7)."""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    # allow_abbrev=False: a removed/mistyped flag (e.g. --mode) must error, not
    # silently abbreviate to another option (--model).
    p = argparse.ArgumentParser(prog="bench", description="EasyCAD quality harness",
                                allow_abbrev=False)
    sub = p.add_subparsers(dest="cmd", required=True)

    # spec — build expected/ from references
    sp = sub.add_parser("spec", help="execute references, write expected/")
    sp.add_argument("scenario", nargs="?", help="scenario id (default: all complete)")
    sp.add_argument("--check", action="store_true", help="CI: rebuild and fail on drift")
    sp.add_argument("--require-validation", action="store_true",
                    help="with --check: also fail scenarios lacking a current validation.json (release gate)")
    sp.add_argument("--no-render", action="store_true", help="skip PNG renders")

    # validate — human acceptance
    va = sub.add_parser("validate", help="record human acceptance of a reference (§4.3)")
    va.add_argument("scenario")
    va.add_argument("--accept", action="store_true", help="write validation.json")
    va.add_argument("--validator", help="validator id (default: $USER)")

    # run — generate through a backend (allow_abbrev=False: --mode must not
    # silently abbreviate to --model now that the flag is removed).
    rn = sub.add_parser("run", help="generate through a backend and save all phases",
                        allow_abbrev=False)
    rn.add_argument("--set", choices=["complete", "open", "all"], default="complete")
    rn.add_argument("--ids", nargs="*", help="explicit scenario ids")
    rn.add_argument("--attempts", type=int, default=1, help="attempts per scenario (>= 1)")
    rn.add_argument("--max-cost", type=float, default=5.0, help="stop when est. cost reaches this (>= 0)")
    rn.add_argument("--cost-per-turn", type=float, default=0.0,
                    help="per-generation cost estimate (usd); arms --max-cost for the product API")
    rn.add_argument("--backend", choices=["reference", "product"], default="reference")
    # No --mode: the manifest mode is derived from the backend so a real product
    # run can't be mislabelled selftest/offline while looking compliant (§6.2).
    rn.add_argument("--url", default="http://127.0.0.1:8852", help="product API base url")
    rn.add_argument("--provider", help="LLM provider override")
    rn.add_argument("--model", help="LLM model override")
    rn.add_argument("--seed", type=int, default=0, help="surface-sampling seed (§6.3)")
    rn.add_argument("--system-prompt-file", help="record this exact system prompt in the manifest (self-hosted)")
    rn.add_argument("--temperature", type=float, help="record the sampling temperature in the manifest")
    rn.add_argument("--worker-image-digest", help="record the worker image digest in the manifest (self-hosted)")
    rn.add_argument("--allow-unverified-artifacts", action="store_true",
                    help="accept a STEP/STL with no server SHA (older server); default fails closed")
    rn.add_argument("--no-grade", action="store_true", help="do not auto-grade after run")

    # grade — recompute verdicts from a saved run
    gr = sub.add_parser("grade", help="regrade a saved run (no generation)")
    gr.add_argument("run", help="path to runs/<id>")

    # report — markdown summary
    rp = sub.add_parser("report", help="markdown report for a run")
    rp.add_argument("run")
    rp.add_argument("--compare", help="baseline summary.json to diff against")
    rp.add_argument("--out", help="also write markdown to this path")

    # schema — validate all scenarios (CI)
    sub.add_parser("schema", help="validate every scenario.yaml (CI gate)")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "spec":
        from .spec import cmd_spec
        return cmd_spec(args)
    if args.cmd == "validate":
        from .validate import cmd_validate
        return cmd_validate(args)
    if args.cmd == "run":
        from .run import cmd_run
        return cmd_run(args)
    if args.cmd == "grade":
        from .grade import cmd_grade
        return cmd_grade(args)
    if args.cmd == "report":
        from .report import cmd_report
        return cmd_report(args)
    if args.cmd == "schema":
        from .schema import validate_all
        ids = validate_all()
        print(f"✓ {len(ids)} scenario(s) valid: {', '.join(ids)}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
