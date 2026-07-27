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

## What's here (M0)

10 single-turn `complete` scenarios (`scenarios/`), the measurers + grader with
golden-fixture tests, and `spec`/`validate`/`run`/`grade`/`report`. No repair
loop, no open scenarios yet — see SPEC §14 for M1/M2.
