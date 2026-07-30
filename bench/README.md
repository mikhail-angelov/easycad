# EasyCAD quality harness (`bench/`)

Measures what fraction of prompts EasyCAD turns into a correct model, what
breaks, and whether a change helped. Full design: [`docs/bench-SPEC.md`](../docs/bench-SPEC.md).

Geometry facts live in the shared [`easycad_geom`](../easycad_geom) package so the
benchmark and the app measure the same way (SPEC §2.7).

## Run it

Everything goes through `python -m bench` with `bench/src` on the path. The
`make` targets set that up:

```sh
make bench ARGS="schema"                       # validate every scenario.yaml
make bench ARGS="spec"                          # build expected/ from references
make bench ARGS="spec --check"                  # CI: fail on drift (PR gate)
make bench ARGS="spec --check --require-validation"   # release gate: also require acceptance
make bench ARGS="validate 001-plate-holes --accept"   # record human acceptance
make bench ARGS="run --set complete --backend reference"   # offline pipeline self-test
make bench ARGS="run --set complete --backend product --url http://127.0.0.1:8852 --cost-per-turn 0.02"
make bench ARGS="grade  bench/runs/<id>"        # recompute verdicts, free, no LLM
make bench ARGS="report bench/runs/<id> --compare bench/baselines/latest.json"
make bench-test                                 # golden-fixture unit tests
```

## The pipeline (SPEC §7)

```
spec     → expected/{turn-N.json, .step, .stl, turn-N/renders/}   references
validate → expected/validation.json                              human acceptance (§4.3)
run      → runs/<id>/<scenario>/attempt-N/turn-M/{code, facts.json, out.step/stl}
grade    → verdict.json + results.jsonl + summary.json           recomputable for free
report   → markdown, highlights flipped scenarios
```

## Backends (SPEC §6.1)

- **`product`** — real generation over the public API (`/api/chat` + STEP export).
  The only backend whose number is a product measurement. Costs money, needs a key.
- **`reference`** — "generates" by running the scenario's own reference. Spends
  nothing and every scenario passes, so it exercises the run→grade→report
  pipeline itself. Recorded in the manifest; **never** a product measurement.

## Invariants that keep the number honest (SPEC §8)

- Unmeasurable = failure, always.
- Coordinate contract (Z-up, on XY, centered) checked first, own failure class.
- A reference is trusted only after human visual validation; an unvalidated
  scenario is `skipped_unvalidated`, never `pass`. Editing `reference.py` resets
  its validation (the hash in `validation.json` stops matching).
- `bench spec --check` runs in CI so a CadQuery/OCCT bump can't silently move
  the metric.

## What's here (M0 + open scenarios)

10 single-turn `complete` scenarios (`scenarios/`), the measurers + grader with
golden-fixture tests, and `spec`/`validate`/`run`/`grade`/`report`/`judge`.

## Open scenarios + the vision judge (M1) — EXPERIMENTAL

> ⚠ **Contradicts the current `bench-SPEC` and is not yet a sanctioned metric.**
> §2.3/§56 deliberately *rejected* automatic open grading — both max-similarity to
> references AND per-scenario functional checks — as a return to per-scenario
> complexity, and §5.4 defines `open_pass_rate` as **blind human review**. This
> feature (vision judge + a small declarative `checks` DSL) is a *proposed*
> alternative: the visual rubric needs no per-scenario code (just prose), which
> weakens the original objection, but it adds a non-deterministic instrument that
> must be calibrated against humans. Its number is reported separately as
> **`open_pass_rate@judge`**, never as the SPEC's human `open_pass_rate`. Adopting
> it as canonical requires an agreed `bench-SPEC` amendment; until then, treat it
> as an exploratory signal, and the human blind-review path (§5.4) stays canonical
> and unbuilt.

`spec: open` scenarios (e.g. `011-phone-stand`) have no ground-truth reference —
everyday objects with no single right answer. They grade on a **hybrid rubric**:

- `checks` — geometric assertions auto-verified from measured facts (`bodies`,
  `largest_dim_mm`, `z_min_mm`, …). Absolute size/topology aren't legible in an
  unscaled render, so they never go to the judge.
- `rubric` — binary VISUAL items a vision model answers from four renders.

The judge is an explicit, cached step so `grade` stays pure/free (§2.8):

```
bench run  --ids 011-phone-stand --backend product --url … --max-cost 0
bench judge runs/<id> --judge-model google/gemma-3-27b-it   # renders + vision-grades + regrades
```

`bench judge` renders four views, sends the visual items to an OpenRouter vision
model (needs `OPEN_ROUTER_KEY`), caches `attempt-N/judge.json`, and stamps the
model in the manifest. Result: `open_pass_rate@judge` (separate from
`scenario_pass_rate`; never mixed). Use a judge from a **different model family**
than the generator (anti-self-confirmation), and validate it against a human
sample before trusting it at scale — a cheap vision model reads gross shape well
but is not an oracle.

**Repair-loop ablation.** The product's in-turn repair loop lives on the server
(`EASYCAD_MAX_REPAIR`); bench can't observe it over HTTP. Declare it so the
manifest records it honestly: run the server with `EASYCAD_MAX_REPAIR=0` and
`bench run … --quality-loop off`, then `=2` with `--quality-loop on`. Unset →
`config.quality_loop.enabled = null` (undeclared), never a blind `false`.
