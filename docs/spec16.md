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
| 4 | Fact-based repair: feed measured geometry into the repair prompt (§5) | planned |
| 5 | Repair failure-class hint from a 7-class taxonomy (§6) | planned |
| 6 | Clarification policy: act + state assumptions, rewrite `open` rubric (§7) | planned (needs `open` scenarios) |
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

## 5. Planned — fact-based repair (`review.md` P1#2)

Today the repair loop (SPEC: in-turn repair, `app/main.py _generate_and_step`) feeds
back only `{code, error}`. Extend the feedback with **measured geometry**, mirroring
text-to-cad's fact packet — never the bench reference (that would make the metric an
oracle, `bench/SPEC.md §2.5`).

Design nuance (why this is not a one-liner): the repair loop fires on an **execution
failure**, where the *failed* attempt produced no geometry. The useful measured
facts are therefore the **current model's** (`base_code`, before the edit): tell the
model "the model you are editing measures bbox X, volume V, N bodies — your edit must
keep/reach the requested geometry." This gives the repair a measured anchor without
inventing a runtime oracle. Empirically (004×5) repair converts crashes into
executable code but not into *correct* geometry; measured anchoring is the cheapest
lever we have short of a vision oracle, and its payoff must be measured, not assumed.

Ablation: `bench run --fact-repair on/off`.

---

## 6. Planned — repair failure-class taxonomy

`references/repair-loop.md` classifies a failure before fixing it. Adopt the seven
classes as (a) an optional hint injected into the repair prompt and (b) a grader
dimension in bench: source/syntax · geometry-invalidity · fillet/chamfer · scale
(radius/diameter, units) · missing-feature · **selector-fragility** · positioning/
datum. "Selector-fragility" as a first-class category corroborates §4.2.

---

## 7. Planned — clarification policy (needs `open` scenarios)

`SKILL.md` rule: ask **one** focused question only when the missing info makes the
model impossible or is fit/safety/compliance-critical; otherwise **act and list the
assumptions**. This supersedes a "prefer to clarify" reflex.

Bench impact: the `spec: open` rubric must reward "assumptions stated explicitly",
and the current `clarification_rate` metric is dropped. Deferred because bench M0 has
no `open` scenarios yet (arrives with M1, `bench/SPEC.md §14`).

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
