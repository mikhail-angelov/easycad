# SPEC16 — Generation-quality borrowings from text-to-cad (text-only)

**Date:** 2026-07-29
**Source studied:** [earthtojake/text-to-cad](https://github.com/earthtojake/text-to-cad)
(MIT). Read the actual `skills/cad/SKILL.md` + `skills/cad/references/*.md`, not
just the README. Analysis lives in the session thread; this spec is the actionable
distillation.

This spec covers only the borrowings that work on a **text-only LLM**. EasyCAD has
**no multimodal model** (default + trial run on DeepSeek text `deepseek-v4-flash`),
so anything that needs a model to *look at a rendered image* is out of scope here.

---

## 1. The correction that reframes everything

The earlier internal proposal (`~/Downloads/text-to-cad-proposal.md`) collapsed two
distinct mechanisms into one "obligatory **visual** loop" and put it at P0. The
source files show they are **separate**:

- **Repair loop** (`references/repair-loop.md`) — fixes by **measured geometry and
  error inspection**, NOT by looking at a picture. Inputs it is allowed to see:
  the command error / stack trace, the `inspect refs --facts --planes --positioning`
  fact packet (bbox, planes, positioning), the existing artifact for comparison,
  parameter values, and topology/selector state. Verbatim: *"Repairs rely on
  measured geometry and error inspection rather than visual snapshots alone."*
- **Snapshot review** (`references/snapshot-review.md`) — a **separate**, later,
  **raster** step: four PNG views (two opposed isometrics + top + front) that a
  reviewer compares against the prompt. This is the part that needs vision.

**Consequence for us:** the fact-based repair loop is text-model-compatible and is
the cheap, proven design — it is exactly what `review.md` P1#2 asked for. The
raster snapshot review is a *different, deferred* layer that we cannot run until we
have a vision model. They must be ablated **separately** (`--fact-repair` vs
`--snapshot`), never as one `quality_loop` flag.

---

## 2. Scope

**In scope (text-only):**

| # | Item | Status |
|---|------|--------|
| 1 | Default-assumptions block in the system prompt (§4.1) | **tried → reverted** (§4.3) |
| 2 | Standard-component dimensions in the prompt (§4.1) | **tried → reverted** (§4.3) |
| 3 | Anti-fragility idioms: operation order, coplanar-face overlap (§4.2) | **tried → reverted** (§4.3) |
| 4 | Fact-based repair: feed measured geometry into the repair prompt (§5) | **descoped — no-op** (§5) |
| 5 | Repair failure-class hint from a 7-class taxonomy (§6) | **implemented — measured net-positive, keep** (§6) |
| 6 | Clarification policy: act + state assumptions, rewrite `open` rubric (§7) | **tried → reverted** (§7) |
| 7 | Standard-component STEP/CadQuery snippet library (§8) | later |

**Items 1–3 were implemented, measured, and reverted — see §4.3.** The borrowed
prompt guidance did NOT transfer; keep this spec as the record of what was tried.

**Explicitly OUT of scope (needs a multimodal model we do not have):**

- Raster **snapshot review** (four-view PNG shown to the model). Deferred until a
  vision-capable model is on the generation path. When revisited, the concrete
  recipe to reuse: two opposed isometric views (e.g. camera `[-1, 1, -0.8]` and its
  negation) guarantee every face appears in ≥1 image, plus orthographic top/front.
- Their agentic-only skills (assemblies/joints, URDF/SRDF/SDF, DXF, SendCutSend,
  implicit CAD) — a browser product, not an agent. See proposal §4.

---

## 3. Coordinate contract — deliberate divergence (unchanged)

text-to-cad defaults origin to the **centre of the part** (good for assemblies/CAD
interchange). EasyCAD keeps its own contract: **Z-up, part sits on XY, `z_min ≈ 0`,
centred on XY** (good for FDM — orientation drives bed adhesion and supports). This
is now stated as a product default in the system prompt (§4.1) so generated models
match the bench coordinate-contract check even when the user does not spell it out.
`bench/SPEC.md §8` and the grader are unchanged.

---

## 4. Implemented now — system prompt (`app/llm.py` `SYSTEM_PROMPT`)

Facts are freely borrowable (MIT); all wording is our own (§9).

### 4.1 Default assumptions + standard components

Added a "Default assumptions" block that applies **only when the request does not
specify**, and requires the model to state any assumed dimension in a comment:
mm units; part on XY with bottom at `Z ≈ 0`, centred on XY; closed positive-volume
solids unless a shell is asked for; enclosure wall 2–3 mm; cosmetic fillet 1–3 mm;
metric clearance holes **M3→3.4 / M4→4.5 / M5→5.5 mm**; and a short table of common
named components (608 bearing 22×8×7; NEMA 17 face 42.3 sq / 31 pitch / Ø22 boss;
Raspberry Pi 4 85×56, holes on 58×49, Ø2.7; 2020 extrusion 20×20, 5 mm slot).

Targets the highest-value failure class: a plausible-looking part with **invented
millimetres**.

### 4.2 Anti-fragility idioms (from `references/build123d-modeling.md`)

The prompt already had a parameters block, the "avoid index selectors" rule, and
"prefer feature ops over booleans". Added the two idioms it lacked, both of which
match our observed failures (e.g. 004-l-bracket crashing on `no result` / broken
chains):

- **Operation order:** base solid → major additions → cuts/holes → `.shell()` →
  through-wall holes → **fillets/chamfers last** (every boolean re-numbers
  selectors; fillets are the most failure-prone step).
- **Through-cuts:** extend a cutting tool ~1 mm **past both faces** it enters and
  exits; a tool face left exactly coincident/coplanar with a target face is a
  classic kernel failure (`BREP_API command not done`).

### 4.3 Measured result — reverted (2026-07-29)

A/B on the full `complete` set, `EASYCAD_MAX_REPAIR=0` (prompt isolated from
repair), `--attempts 3`, aggregated over all 30 attempt-verdicts per side (the
headline `scenario_pass_rate` is attempt-1 only, n=10, too noisy for a subtle
prompt change — `grade.py` uses `graded[1]`):

| | old prompt | new prompt (§4.1+§4.2) |
|---|---|---|
| all-attempts pass | **23/30 = 77%** | **19/30 = 63%** |

Net **−14 pp**, statistically insignificant (two-proportion z ≈ 1.1, p ≈ 0.26) but
**directionally negative with concrete regressions**: 009-slot-plate 3/3→1/3,
010-counterbore 2/3→1/3; no scenario improved. New failures were CadQuery **API
hallucinations** (`'Workplane' object has no attribute 'slot'`, `.hole(counterbore=…)`
instead of `.cboreHole()`), not anything a specific new rule dictated. (One NEW
failure was an infra flake — a local-mode `model.stl` temp-file race, HTTP 500 —
not attributable to the prompt; discounting it still leaves 20/30 = 67% < 77%.)

**Diagnosis:** the existing `SYSTEM_PROMPT` is already long and already carries the
high-value anti-fragility rules (index-selector ban, oversized cuts, feature-ops-
over-booleans). The borrowed idioms are build123d-flavoured and partly redundant;
bundling a large Default-assumptions block on top appears to **dilute** an already-
tuned prompt rather than sharpen it — the classic "more instructions ≠ better."

**Decision:** reverted from `app/llm.py`. Do NOT re-add as a bundle. If revisited,
test **one small, low-conflict addition at a time** against this same harness — the
clearance-hole defaults (M3/M4/M5 → 3.4/4.5/5.5) are the least likely to conflict
and the first candidate. This mirrors the repair-loop lesson: measure, don't assume
a borrowed idea transfers to our stack.

---

## 5. Descoped — fact-based repair is a no-op on our architecture

The `review.md` P1#2 idea was to feed measured geometry into the repair prompt. On
inspection this adds **nothing** here: `base_code` (the model being edited, passed to
`generate_code` unchanged on every repair attempt) **already carries its "Geometry
info" comment block** — `code_with_geometry`, appended by `append_geometry_block`
and stored on each step. So the model already sees the current model's measured
bbox/volume/topology every attempt. And the *failed* attempt produced no geometry to
measure. There is no third source of measured facts short of a runtime oracle (a
reference we don't have; bench-only). **Descoped** — superseded by §6, which injects
a targeted *fix hint* instead of re-feeding geometry the prompt already contains.

---

## 6. Implemented — repair failure-class hint (`app/llm.py _repair_hint`)

`references/repair-loop.md` classifies a failure before fixing it. We map the
measured error text of a failed repair attempt to a short, relevant fix hint,
appended to the repair feedback (`generate_code`). Unknown class → no hint (model
sees the raw error).

**Five classes, not seven — deliberate.** text-to-cad's taxonomy has seven classes,
but four of them (scale/units, missing-feature, positioning/datum, selector-
fragility) are *correctness* failures: the code **executes fine** and produces the
wrong geometry, so they never reach the repair loop and there is no error text to
classify. Only the classes that surface as a runtime error are actionable here:

1. **missing `result`** → "assign the finished model to `result`".
2. **non-existent API** (`AttributeError` / unexpected keyword argument) → "use only
   the real fluent API"; a specific swap is appended ONLY when the error names a
   method we know (`.slot()`→rounded-rectangle cut, counterbore→`.cboreHole`,
   countersink→`.cskHole`), so an unrelated attribute error gets the general hint,
   not irrelevant slot/cbore advice (review P1#2).
3. **NameError** → declare it in the Parameters block.
4. **SyntaxError** → return complete valid Python.
5. **kernel/BREP** (`BREP_API command not done`, `StdFail`, `Standard_…`) → the
   three common causes (fillet radius too large / coincident cut face / unclosed
   profile). This one lists a few causes because the kernel error text is opaque and
   does not disambiguate them; it is the one class where a single instruction is not
   derivable.

**Why this and not the §4 blanket prompt:** the same anti-fragility facts that
measured net-negative when bolted onto the base `SYSTEM_PROMPT` (§4.3) are here
delivered **only on the attempt that actually hit that error** — conditional and
relevant, not permanent prompt bloat. This is the disciplined re-test of those facts.

Tested: `tests/test_repair.py::test_repair_hint_classifies_known_errors`.

**Measured result (2026-07-29) — keep.** Repair path, `MAX_REPAIR=2`, crash-prone
scenarios {004, 005, 009, 010}, `--attempts 5` (20 samples/side), hint OFF (HEAD) vs
ON:

| metric | hint OFF | hint ON |
|---|---|---|
| execution-recovery (crash → executes) | 17/20 (2 `empty_result` crashes survived) | **20/20 — zero crashes** |
| pass rate (20 attempts) | 11/20 = 55% | 12/20 = 60% |
| 009-slot-plate (`.slot()` hallucination) | 3/5 | **5/5** |
| 004-l-bracket | 0/5 | 0/5 |

The hint does exactly its job: the `empty_result` class is **eliminated**
(2 → 0) and 009's API-hallucination class improves. Pass rate is +5 pp, within
noise (n=20) — the rescued 004 executes into geometrically-wrong output, the same
"repair fixes crashes, not correctness" ceiling (no runtime oracle, §5). No
regression, and the hint costs nothing when it doesn't fire (appended only on a
matched error). Unlike §4's blanket prompt (net-negative bloat), this targeted,
conditional delivery is net-neutral-to-positive and removes a whole class of
user-visible "generation failed". **Decision: keep.**

---

## 7. Tried → reverted — clarification policy (needs `open` scenarios to validate)

`SKILL.md` rule: ask **one** focused question only when the missing info makes the
model impossible or is fit/safety/compliance-critical; otherwise **act and list the
assumptions**. Implemented as a triage-prompt change in `app/refiner.py` (bias
`clarify`→`refine`, "prefer acting over asking").

**Measured (triage-verdict A/B, real DeepSeek, temp 0.1, direct `triage()` calls —
bench can't validate this: `ProductBackend` runs with `auto_refine: False` and
bypasses triage entirely). Reverted.** On 7 representative prompts, old vs new:

- Intended shift happened but is **unstable**: a vague "make it nicer" that OLD
  deterministically sent to `clarify` came back `['clarify','refine','ready']` on
  three NEW runs — including `ready`, which would generate the literal vague prompt
  (a worse outcome than the question it replaced).
- **Stable side effect**: "make it a 100 mm sphere" (vs a 50×80×30 box) moved
  `invalid`→`ready` on all 3 runs. Debatable (a full replacement isn't really a
  *contradiction*, so `ready` may even be correct), but the "prefer acting" framing
  demonstrably bled past the clarify/refine boundary into the `invalid` guard, which
  was not intended.

Net: an **unmeasurable** change (bench bypasses triage; triage verdicts are noisy at
temp 0.1) whose benefit is unproven and whose blast radius reaches the `invalid`
guard. Same trap as §4 — reverted rather than shipped on faith.

**Prerequisite to retry:** a way to measure quality on underspecified prompts.
**Prototyped 2026-07-30 (EXPERIMENTAL):** `spec: open` scenarios + a hybrid rubric
graded by an OpenRouter vision judge (`bench judge`, `bench/judge.py`) — see
`bench/README.md`. It yields an automated `open_pass_rate@judge`, but **contradicts
`bench-SPEC` §2.3/§56**, which deliberately rejected automatic open grading; the
canonical human blind-review metric (§5.4) is still unbuilt.

**Decision (2026-07-30): keep it EXPERIMENTAL** — not adopted as canonical, not
reverted. The judge stays as an exploratory tool with its own separate key
(`open_pass_rate@judge`), loud EXPERIMENTAL labels, and the anti-self-confirmation /
provenance / fail-closed guards from the two review passes. bench-SPEC §56 now
carries a non-normative note disclosing it. Making it canonical would require a
calibration pass against a human on 10–20 models AND an agreed bench-SPEC amendment;
until both, `open_pass_rate@judge` is a signal, never the number. Still missing for a full §7 retry regardless: the bench must exercise the
triage/`auto_refine` path (today `ProductBackend` runs `auto_refine: False`) and
score the clarify-vs-act *verdict* itself. §7 stays reverted until then.

---

## 8. Later — standard-component snippet library

A small curated set of CadQuery snippets/bounding boxes for the components in §4.1,
inserted on demand (the browser analogue of their `step-parts` lookup). Bigger than a
prompt edit; parked until §4.1's in-prompt table is shown to be insufficient.

---

## 9. License

MIT. Facts and ideas (clearance values, wall/fillet ranges, validation-step order,
default set, failure taxonomy) are used freely and **reworded** in our own text; no
SKILL.md prose is copied into the product prompt. If we later port any of their
**code** (render/inspection glue), add a `THIRD_PARTY.md` notice with the source.
Their benchmark prompts, if adopted, go in as reworded scenarios citing the original.

---

## 10. Validation

- Prompt changes (§4): **done — see §4.3.** Bench A/B on the `complete` set,
  `MAX_REPAIR=0`, `--attempts 3`, all-attempts aggregation. Result: net −14 pp,
  reverted. Method to reuse for the per-item retest: aggregate the 30 attempt-
  verdicts (not the attempt-1 headline), compare failure-class deltas.
- Fact-repair (§5): `--fact-repair on/off` ablation, `MAX_REPAIR=2`.
- Split the existing `--quality-loop` flag into `--fact-repair` / `--snapshot`
  so on/off states are recorded honestly per mechanism.
