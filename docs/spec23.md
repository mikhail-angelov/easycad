# SPEC23 — Verified generation: no silent no-ops, a compact CadQuery surface, facts for agents

Status: **W1 + W2 + W5 IMPLEMENTED** (2026-10-01) · W3–W4 deferred, see §10 · line: spec22→ (follows SPEC22)

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

## 10. Implementation status (2026-10-01)

### Done — W1 and W5

- `app/cq_worker.py` `get_facts()` measures the exported shape: `volume_mm3`, `area_mm2`,
  `bbox_mm`, `center_mm` (volume centroid), `solids`, `faces`, `edges`. It rides the same
  payload as `geometry_info` through the local subprocess, the worker and the zygote, into
  `ExecResult.facts` and `Step.facts`.
- `app/store.py` `same_geometry()` is the single tolerance policy (lengths 1e-3 mm,
  volume/area rel 1e-6, counts exact). Unmeasured on either side → `None`: a missing
  measurement never claims a no-op.
- `_generate_and_step` compares the new facts with the current step's and stores
  `verdict = changed | no_change_detected` on the chat step. It is a valid model, not an
  error: it becomes current, the step is not repaired, and the chat shows an amber
  "the model did not change" line instead of "Step N ✓". Counted as `gen_no_change`.
- The centroid is there because volume, area, bbox and topology all stay identical when a
  hole moves; without it a legitimate move would be flagged
  (`test_moved_feature_with_identical_volume_and_bbox_is_a_change`).
- W5: `facts` and `verdict` are on every `step` in API responses (and so in the SSE final
  event); the app root carries `data-verdict` / `data-facts` in `done` only.
  `docs/automation.md` documents both.
- Tests: `tests/test_spec23.py`, `frontend/src/automation.test.ts`.

### Deviation from §3

"Requests that legitimately change nothing must not be flagged" is not implemented as an
intent check: there is no reliable non-LLM signal for it, and a no-op prompt already goes
through triage. The verdict is a factual statement ("geometry identical"), not an error,
so a deliberate no-op gets an accurate notice and nothing is blocked.

### Done — W2, accepted on a bench A/B

- `app/cq_index.py` holds the single allow-list (46 `Workplane` methods by idiom) and the
  EN/RU synonym map. Signatures come from the installed CadQuery via `inspect`, never by
  hand. The app image has no CadQuery, so the rendered index is committed as
  `app/cq_index.txt` (regenerate: `.venv-poc/bin/python -m app.cq_index`); a test fails on
  drift, on a missing allow-list entry, or above 6000 chars (~1.5k tokens).
- Sent as a second system message on every generation (`app/llm.py`), no flag.
- A/B protocol as in 2026-07-29: `complete` set, `EASYCAD_MAX_REPAIR=0`, same model
  (deepseek-v4-flash), all attempt verdicts aggregated, not the attempt-1 headline.

  | run | attempts | index off | index on |
  |---|---|---|---|
  | 1 (`2026-10-01T04-06-30_complete_4f9b` / `…04-15-39_complete_8bbd`) | 3 | 27/30 | 28/30 |
  | 2 (`2026-10-01T04-25-13_complete_8f28` / `…04-46-12_complete_a1af`) | 7 | 65/70 | 67/70 |
  | total, run 1 fence-corrected (see below) | | **93/100** | **97/100** |

  No scenario got worse; 004-l-bracket 4/7 → 5/7. Non-parser execution errors 1 → 0.
  Median latency 9.8 s → 9.1 s; prompt +~1.4k tokens per generation. p ≈ 0.2 — directional,
  not significant — but consistent across both runs and the opposite of the July result.
- Found by the A/B: every run-1 execution error (3/60, both sides) was the parser, not the
  prompt — the model returned code, a closing fence, then prose, and
  `strip_markdown_fences` only trimmed fences at the very ends. Fixed in `app/llm.py`;
  replaying the three replies, all execute and match the reference. Run 2 has the fix on
  both sides.
- Open, not caused by W2: on 004-l-bracket the model twice per side spent all 16,384
  tokens on reasoning and returned no code (`finish_reason=length` → 422). Equal on both
  sides; a reasoning-budget question for the generator, not for the prompt.

### Deferred — W3, W4

- **W3 (stated-millimetre check):** extracting "the millimetres the user asked for" from
  free text is a heuristic with real false-positive risk, and the generator already sees
  the measured size block each turn. Revisit with a structured target (e.g. from triage)
  rather than regexes.
- **W4 (op list):** the premise "one prompt = one feature" does not hold — the generator
  rewrites the whole program each turn, so a multi-op prompt already runs in one turn.
  Per-op verification would need an op-list contract with little evidence of payoff.
