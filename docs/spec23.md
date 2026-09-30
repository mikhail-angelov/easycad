# SPEC23 — Verified generation: no silent no-ops, a compact CadQuery surface, facts for agents

Status: **DESIGN** · 2026-09-30 · line: spec22→ (follows SPEC22)

## 1. Goal & framing

Every step should be *verifiable*, not merely *assumed*. Today, if the generated CadQuery
runs without raising and the resulting geometry is unchanged — a fillet on an edge that is
not there, a boolean that no-ops, a move that cancels itself out — the app reports success:
the viewer redraws the same solid, the chat says done. The user learns to distrust "done",
and the harness cannot see the difference either, because the defect is not in the final
shape but in the *claim* that a step did something.

This spec borrows three mechanisms from a neighbouring open-source project,
[awv-informatik/classcad-ai](https://github.com/awv-informatik/classcad-ai) — an LLM-CAD
monorepo built around a commercial kernel — and applies them to our stack (CadQuery/OCCT,
FastAPI, Preact). Their kernel is closed and key-gated, so no code crosses over; their
*agent engineering* is MIT and directly transferable.

Three claims drive the work:

1. **A step must prove it changed the model** — numbers first (volume, bounding box,
   face/edge counts), computed from geometry we already load.
2. **The model should not recall the CadQuery surface from memory** — give it a compact
   index of what we actually support, plus synonyms, so the in-turn repair loop becomes
   the exception rather than the routine.
3. **A rendered view is not a measurement.** Their verification recipe records six
   successive runs that produced a *mirrored* part with every check passing, because all
   the checks had been derived from one misread image; and auto-framed renders hide scale
   by construction. Our viewer auto-frames too.

### Non-goals

- **No MCP server.** SPEC22 weighed MCP for agent integration and decided against it; that
  decision stands. W5 extends the SPEC22 `data-state` contract instead.
- No migration off CadQuery/OCCT, and no port of their feature tree or rollback bars — our
  step timeline already provides revert.
- No assembly mates, no drawing generation, no image input.
- Not a second quality harness: `bench/` already grades against reference models. We connect
  the app to those measurements; we do not duplicate them.

## 2. What we borrow, precisely

| Mechanism | In classcad-ai | Our landing spot |
|---|---|---|
| Step verification, numbers before pixels | `packages/skill/recipes/verification.md` | `app/cadquery_exec.py`, `easycad_geom/measure.py` |
| One-line-per-method index (~5k tokens) plus BM25F over name/summary/keywords with domain synonyms | `packages/skill/SKILL.md`, `packages/skill/discovery.js` | `app/skills.py`, the generator system prompt in `app/llm.py` |
| The model writes a program and state persists between runs | `packages/script/` | op-list generation in `app/llm.py` |

## 3. W1 — A step must prove it changed the model

### Problem

`app/cadquery_exec.py` distinguishes *failure* well: errors, missing `result`, OCP crashes,
timeouts and transport errors all come back as a populated `error` string instead of
raising, and malformed worker payloads degrade to `worker_unavailable`. What it cannot
distinguish is *success that changed nothing*. `brep_facts()` in `easycad_geom/measure.py`
already computes everything needed (volume, bounding box, solid count, validity), and
`easycad_geom/facts.py` exposes `compute_facts(step_path, stl_path)` for the artefact case
— but nothing in `app/` uses either today.

### Implementation Decisions

- After a successful execution, compute facts for the new model and keep the previous
  step's facts. Both are cheap: the shape is already in memory on the local backend, and
  the artefacts already exist on the worker backend.
- Compare with an explicit float tolerance (`volume_mm3`, bbox extents, solid count). A
  change below tolerance in every field and no change in counts means the step did nothing.
- Return a **distinct status**, not an error: the code is valid, the model is valid, the
  step simply had no effect. Name it once and keep it consistent across API, the SSE stream
  and the UI (suggested: `no_change_detected`, alongside the existing error field).
- The message must be concrete: which facts stayed identical, and one sentence of
  plausible cause ("the requested fillet radius likely already exists on this edge").
- **Do not guess intent here.** Ambiguity is `app/refiner.py`'s job
  (`ready|refine|clarify|invalid`). W1 catches the cases triage passed and the model then
  failed to act on.
- Requests that legitimately change nothing ("leave it as is", "no change needed") must not
  be flagged: the verdict is only raised when the prompt asked for a modification.

### Testing Decisions

- `tests/test_spec23_w1.py`: a fillet on an already-filleted plate returns
  `no_change_detected`; a genuine change returns success; a no-op request passes through
  clean.
- Bench scenario derived from `bench/scenarios/006-fillet-plate`: the second, identical
  turn must be flagged while the graded shape still passes its reference check.
- The full app suite stays green; no new required environment variable, no dependency added.

## 4. W2 — A compact CadQuery surface index, with synonyms

### Problem

The generator prompt describes intent, not the API surface we support; `app/skills.py`
carries prose recipes plus code (the ISO thread recipe is the worked example). The model
therefore picks methods from memory, and every miss is paid for by the in-turn repair
loop — a full extra generation. Repair exists for a reason, but it should be rare.

### Implementation Decisions

- Generate a **one-line-per-entry index** of the CadQuery/OCCT surface we guarantee to
  support: name, signature shape, one-clause purpose, and the idiom it belongs to. Target a
  hard token budget (classcad-ai targets ~5k tokens for 264 methods; ours will be far
  smaller) and assert it in a test so it cannot creep.
- Ship a **synonym map** for the retriever and the prompt: hole↔bore, round↔fillet,
  chamfer↔bevel, split↔slice, thread↔helix, wall↔shell, hollow↔shell, countersink↔cone cut.
  These are the words users type and the words the API uses; today nothing bridges them.
- Keep the index **generated from a single allow-list**, never hand-edited: one source of
  truth for "what we support", reused by the prompt, the retriever and the tests.
- The thread recipe stays as-is: it is a recipe, not an API entry, and it is exactly the
  level of detail the index should *not* try to cover.

### Testing Decisions

- A bench A/B on the same scenarios and the same model: first-pass success and repair rate,
  both reported. The change is accepted only if repair rate drops without quality loss
  (grading is unchanged, so the harness already protects us).
- `tests/test_spec23_w2.py`: the index regenerates deterministically, covers 100% of the
  allow-list, and stays inside the token budget.

## 5. W3 — Numbers before pixels

### Problem

Their failure story is our risk: a render cannot be trusted to catch a mirrored or
silently-rescaled part. `bench/src/bench/grade.py` already has the right instrument —
`surface_deviation` against a reference mesh, marked `required` — but it runs offline in the
harness, never in the loop that talks to the user. And the app has no dimension check at all
today: no code compares the millimetres the user asked for with the millimetres produced.

### Implementation Decisions

- Add a **numeric dimension check** after each step: extract the stated millimetres from the
  request (the triage stage already sees the text), and compare them with
  `easycad_geom/measure.py` results — overall bbox extents, hole diameters where
  identifiable, wall thickness where claimed. This is the check a render cannot provide.
- Reuse the harness's comparison where a scenario has a baseline: `compare.py`
  (`surface_deviation`, `deviation_after_align`) is importable and already tested.
- Treat multi-view renders (front/top/right/iso, fixed camera, framing off) as a **secondary**
  signal only, and only in the bench. Hard rule, stated in the code comment so it survives:
  never let a rendered view be the sole evidence for a dimension.
- Keep tolerance policy in one place; the harness and the app must not drift apart on what
  "same shape" means.

### Testing Decisions

- A deliberately mirrored variant of an existing scenario must fail the numeric check while
  passing a naive visual one — that pair is the regression test for this whole section.
- A scenario with a stated 5 mm wall but a produced 4 mm wall fails on numbers, with or
  without a render.
- Bench baselines: adding a numeric block must not change existing grades.

## 6. W4 — One prompt, several ordered operations

### Problem

One prompt currently equals one feature equals one run. A perfectly ordinary request —
"add two ribs, fillet their tops, add a boss for an M3 screw" — costs three turns, three
round trips, and loses the thread between them.

### Implementation Decisions

- Let the generator emit an **ordered op list** for a single prompt, with a fact assertion
  between ops, each verified under W1. The state already persists between steps in the
  accumulated code, so this is a contract change, not a new execution model.
- Cap ops per turn, and on a failure mid-list **keep what was applied** and report exactly
  where it stopped. Never roll back geometry the user did not ask to roll back — that is
  what the step timeline is for.
- Reuse the existing repair loop per op, not per turn.

### Testing Decisions

- Bench multi-turn scenarios exercised as one prompt: the graded shape must match the
  multi-turn result within the same tolerance.
- A scenario whose second op is impossible stops cleanly after op 1, reports it, and leaves
  a valid model.
- `tests/test_spec23_w4.py` covers the cap, the partial-failure report and the per-op
  verification hook.

## 7. W5 — Expose the verdict and the facts to the browser agent

### Problem

SPEC22 gave agents a way in (PATs) and a way to know *when* we are done (`data-state`,
`data-state-rev`). What it did not give them is a way to know *what happened*: no facts, no
verdict. Verification is precisely the thing a browser agent cannot fabricate for itself —
and the thing most worth trusting about a CAD tool.

### Implementation Decisions

- Extend `frontend/src/automation.ts` with the last step's verdict and facts — volume,
  bounding box, solid count — on the root element, under the same rev-stamped discipline as
  `data-state-rev` (`data-facts`, `data-verdict`).
- The API returns the same block in the generation response, so an agent does not have to
  parse the DOM to get numbers.
- No new auth surface, no new endpoint family: this rides on the existing response and
  session.

### Testing Decisions

- `tests/test_spec23_w5.py`: the verdict and facts appear in the response and in the DOM
  contract, and stay stable across a no-op step (`no_change_detected` visible to an agent).
- The pure-function property of `automation.ts` is preserved: no new dependency, the
  contract stays inspectable in one place.

## 8. Order & acceptance

1. **W1 first.** Smallest change, largest honesty gain, and W4 depends on it.
2. **W3 (numeric half) second.** Same theme, reuses the harness's instrument.
3. **W5 third.** Cheap, and it extends a contract we already committed to in SPEC22.
4. **W2 fourth.** Worth doing only with the bench A/B to prove it, so it comes after the
   measurement path is trustworthy.
5. **W4 last.** It builds on per-op verification.

Acceptance for the whole spec: the app suite stays green, the bench keeps its grades on all
12 scenarios, and a no-op step is never reported as an ordinary success — in the API, in the
SSE stream, in the DOM contract, and therefore in the chat.

## 9. References

- classcad-ai verification recipe (no-op successes, mirrored part, perception vs numbers):
  <https://github.com/awv-informatik/classcad-ai/blob/master/packages/skill/recipes/verification.md>
- classcad-ai discovery module (compact index, BM25F, synonyms):
  <https://github.com/awv-informatik/classcad-ai/blob/master/packages/skill/discovery.js>
- classcad-ai script medium (the model writes a program; state persists):
  <https://github.com/awv-informatik/classcad-ai/blob/master/packages/script/README.md>
- classcad-ai skill index:
  <https://github.com/awv-informatik/classcad-ai/blob/master/packages/skill/SKILL.md>

Licensing note: the ClassCAD kernel behind that project is commercial and key-gated; only
the tooling (`@classcad/skill`, `@buerli.io/ai`) is MIT. This spec borrows *approaches*,
not code.
