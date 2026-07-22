# EasyCAD Spec 10: Deterministic Geometry Plugins (Thread as the First Plugin)

## Status

Proposed. Builds on the minimal 3-endpoint flow and Spec 9's feature roster
and scoped refinement (`spec9.md`, implemented). Directly motivated by a
capability gap found and partially closed in the same working session as
this spec: adding a deterministic `polygon` profile
(`app/feature_compiler.py`, `app/ai_generation.py`, 2026-07-19) made a true
hexagonal bolt head buildable, but real thread geometry remains unbuildable
through any of today's 19 hardcoded primitive types
(`OPERATION_CONTRACTS`, `app/feature_compiler.py:68-88`) — the "M16 x
38mm" callout is disclosed today only as flat engraved/cut text, never as
physical helical geometry.

## Goal

Two things, inseparable in practice:

1. Real, deterministic, printable thread geometry, driven by a scoped
   freeform instruction ("add M12 thread at the bottom of the bolt for
   30mm") — 2-4 clean numeric parameters resolved by the planner, all
   actual geometry computed deterministically. Same division of labor as
   Spec 9's `polygon` profile: the LLM chooses *which* parameters, never
   computes coordinates or curve math itself.
2. A plugin boundary so thread — and future similarly-specialized
   generators (engraving, knurling, ...) — can be developed, tested, and
   risk-gated independently of the core box/cylinder/hole primitive set
   and of each other, instead of growing `feature_compiler.py`'s single
   `OPERATION_CONTRACTS` dict and if/elif dispatch chain in place.

These are inseparable because a real feasibility spike run this session
(see below) showed thread generation cannot safely share the core
primitives' failure-handling assumptions: it needs its own timeout budget
and its own geometry-sanity verification, or it will either stall a
request or silently corrupt the model while reporting success. A plugin
boundary is the mechanism that lets one risky feature type carry stricter
rules than `box`/`cylinder` without complicating either.

## Motivating evidence

A same-session spike (`cq.Wire.makeHelix` + a swept V-groove profile,
already-installed CadQuery 2.8.0, zero new dependencies) built two
external threads:

- M12, pitch 1.75mm, length 30mm (17 turns): built in **3.9s**. Inspecting
  the STL with `numpy-stl` found only **18 triangles total**, a bounding
  box of `z:[15, 30]` instead of the expected `[0, 30]`, a **negative**
  computed volume, and numpy-stl's own manifold check reported *"mesh is
  not closed."* CadQuery raised no exception and reported one solid — the
  boolean cut silently produced a fragment, not a thread. This is exactly
  the failure class the user has repeatedly flagged this session: a
  plausible-looking, non-erroring result that is not actually correct
  geometry.
- M16, pitch 2.0mm, length 38mm (19 turns — a modest increase over the
  M12 case): built in **90.9s**. Non-linear, unpredictable scaling from
  a neighboring parameter set is itself disqualifying for a live
  request/response cycle.
- `app/runner.py:22-30` (`WORKER_TIMEOUT_SECONDS`, default 35s, hard
  clamp `[5, 120]`) already times the **entire** worker subprocess call —
  every feature in the draft compiled and built in one script, one
  timeout. A slow or broken thread feature today would either blow this
  budget and raise `RunnerError("worker_timeout", ...)`, or (worse, per
  the M12 case above) return *successfully* with silently wrong geometry.
  Either way, `_build_or_fallback` (`app/main.py`) would retry against
  `fallback_draft`, which discards **every** feature down to a generic
  box — a working hex head and shank would be thrown away because of one
  risky feature elsewhere in the same draft.
- **The sandbox cannot run thread-generation source, and this is
  deliberate, not an oversight.** `app/validator.py:11` forbids
  `ast.FunctionDef` outright, and `app/validator.py:101-103` already
  contains `DISCOURAGED_SOURCE_PATTERNS = {"helix": "Helical/thread
  geometry is not allowed in this prototype; use a plain cylinder"}` — a
  named, deliberate prior rejection of exactly this feature. The worker's
  `exec` namespace (`worker/cadquery_worker.py:28-34`) is
  `{"cq": cq, "PARAMETERS": parameters, "math": math, "int": int,
  "__builtins__": {}}` — no `cq_warehouse`, no way to import anything
  (`ast.Import`/`ast.ImportFrom` are also forbidden), no builtins beyond
  the four names listed. A hand-rolled `def _plugin_...(): ...` block
  (an earlier draft of this spec's Part B) cannot pass `validate_source`
  at all, independent of whether its geometry is even correct.
  Loosening `FORBIDDEN_NODES`/`DISCOURAGED_SOURCE_PATTERNS` broadly would
  fix this but would also widen what *every* LLM-influenced core
  primitive is allowed to generate — a much bigger blast radius than one
  plugin needs. Part B below instead adds exactly one new allowed name
  (a trusted dispatch call), leaving every existing restriction on
  LLM-influenced generated text untouched.
- `draft_operation_contract_descriptions()` and
  `draft_specification_operation_types()` (`app/feature_compiler.py:93-108`)
  are already *derived generically* from `OPERATION_CONTRACTS` — the
  planner-facing type list and per-type prompt text need no separate
  plugin-specific hook if a plugin's contract is merged into the same
  registry.
- `numpy`/`numpy-stl` were removed from `requirements.txt` earlier this
  session after the pixel-brightness panel-layout heuristic that used them
  was replaced with an LLM classification call (see
  `docs/AI_LEARNED.md`). This spec reintroduces `numpy-stl` as a main-app
  dependency for a different, better-justified reason: real manifold and
  volume verification of risky plugin output (Part C), not a brittle pixel
  heuristic.

## Non-goals

- Not a general third-party plugin marketplace, loader, or sandboxed
  extension API with its own versioning and stability guarantees.
  Plugins here are first-party Python modules living in this repo,
  imported at process start. "Plugin" names an internal seam for
  maintainability and risk isolation, not an external extension surface.
- Not a full standards-complete thread implementation. v1 targets
  external, single-start, ISO metric coarse threads only (the
  overwhelming majority of "add M*n* thread" requests). Internal threads
  (tapped holes/nuts), fine-pitch variants, and multi-start threads are
  future work; the plugin interface should not preclude them.
- Not a fully-specified engraving plugin. Part E exists only to show the
  plugin interface generalizes past thread, at the same sketch-level of
  detail Spec 9 gave its own risk section — a real engraving spec is
  future work, and today's flat cut/emboss `text` feature type is
  untouched by this spec.
- No change to the existing box/cylinder/hole/... primitives' compilation
  path or failure handling. They keep today's single-expression,
  whole-draft-fallback behavior exactly as-is. Only plugin-registered
  types opt into Part C's stricter per-feature envelope.
- No geometric constraint solver (unchanged position from Spec 9).

## Language

New CONTEXT.md terms this spec would introduce:

- **Feature plugin**: a self-contained, import-light Python module
  (`app/plugins/*.py`, no `fastapi`/`pydantic` dependency — the worker
  subprocess imports it too, see Part B) that registers one feature
  type's contract, prompt guidance, a trusted geometry-building function,
  and (for a *risky* plugin) its own build timeout and geometry verifier
  — instead of a new hardcoded branch in `feature_compiler.py`.
- **Trusted plugin code vs. generated text**: a plugin's own module
  (`app/plugins/thread.py`, using `cq.Wire.makeHelix`/`cq_warehouse`/
  whatever it needs) is ordinary, first-party, code-reviewed Python,
  imported normally by the worker at process start — it is never
  LLM-influenced, never emitted as generated source text, and never
  passed through `validate_source`. Only the *call* to it (one line, in
  the per-request generated script) is LLM-influenced-adjacent and stays
  inside the existing AST allowlist. This is the distinction Part B relies
  on: the sandbox exists to constrain what LLM-chosen *parameter values*
  can make generated text do, not to constrain first-party code the
  planner never writes a single character of.
- **Plugin contract**: the same `OperationContract` dataclass core
  primitives already use (allowed operations, required/optional
  parameters, profile and pattern support), plus one field only a plugin
  is expected to populate (`literal_parameters`, Part A, for genuine
  non-dimension enum values like thread's `hand`) — registered through a
  different path and, optionally, given a stricter build envelope, but
  not a parallel contract language.
- **Risky plugin**: one that declares `max_build_seconds` and/or
  `verify_build`. Its feature is attempted in an isolated pre-flight
  (Part C) against a minimal real fixture, with its own timeout and a real
  (not just "did it raise") geometry check, before the main draft build
  ever runs. A plugin that declares neither is treated exactly like a core
  primitive.
- **Per-feature fallback**: dropping only the one feature that actually
  failed — whether caught early by the isolated pre-flight, or only later
  during the real full-draft build once the worker's error names that
  exact feature (Part C) — and marking it `unsupported` with an
  `omission_reason` (Spec 9's existing field, `app/models.py`), instead of
  `fallback_draft`'s current all-or-nothing reduction to a generic box.
  Both paths matter: pre-flight catches most risky-feature failures before
  they can touch the real draft, but does not by itself guarantee the real
  build (with real siblings) will also succeed.

## Part A: Plugin registry

New module `app/plugin_registry.py`:

```python
@dataclass(frozen=True)
class FeaturePlugin:
    type_name: str                     # canonical id, e.g. "thread"
    contract: OperationContract        # same shape as core OPERATION_CONTRACTS entries
    build: Callable[[Any, dict[str, float], "Extents | None"], Any]
        # (cq_module, resolved_own_parameters, resolved_target_extent) -> a CadQuery shape.
        # Trusted first-party code (Part B) -- never generated text, never AST-validated.
    prompt_hint: str                   # one sentence, appended to draft_operation_contract_descriptions()
    resolved_extent: Callable[[SpecificationFeature, dict[str, float]], Extents | None] | None = None
    max_build_seconds: float | None = None   # None => not risky, no isolated pre-flight
    preflight_fixture: Callable[[dict[str, float]], CADProject] | None = None  # see Part C
    verify_build: Callable[[bytes, dict[str, float]], str | None] | None = None
        # (artifact_bytes, resolved_own_parameters) -> a failure reason, or None if OK --
        # takes the resolved parameters too (Part C): a bare bytes-only check cannot compare
        # against a diameter/pitch/length it was never given.

_PLUGINS: dict[str, FeaturePlugin] = {}

def register_plugin(plugin: FeaturePlugin) -> None: ...
def plugin_operation_contracts() -> dict[str, OperationContract]: ...  # merged view for the planner schema
def plugin_for(type_name: str) -> FeaturePlugin | None: ...
```

`app/plugin_registry.py` is a main-app-only module (imported by
`ai_generation.py`/`feature_compiler.py`, which already load
`fastapi`/`pydantic` regardless) — it can use `SpecificationFeature`,
`Extents`, etc. freely. The import-light constraint applies only to each
plugin's own geometry module (e.g. `app/plugins/thread.py`), which the
*worker subprocess* also imports directly — see Part B.

`app/plugins/__init__.py` imports each plugin module (`thread.py`, later
`engraving.py`, ...) so registration happens once at process start —
plain Python import side effects, no dynamic discovery/entry-point
scanning needed for a first-party, in-repo plugin set.

`feature_compiler.py` changes are additive, not replacements:

- `OPERATION_CONTRACTS` stays the source of truth for core primitives.
  `operation_contract(feature_type)` checks `OPERATION_CONTRACTS` first,
  then `plugin_registry.plugin_for(feature_type)`.
- `draft_operation_contract_descriptions()` appends each registered
  plugin's `prompt_hint` after the core lines — the planner sees one
  merged catalogue, exactly as it does today; it has no notion that some
  types are "plugins."
- `draft_specification_operation_types()` merges plugin type names into
  the same comma-joined list already sent to the planner prompt.
- **`_draft_feature_schema()` (`app/ai_generation.py:262-288`) must also
  iterate the merged contract set, not just `OPERATION_CONTRACTS`.** This
  function builds the actual `oneOf` JSON schema passed as `tools=` to the
  provider (`draft_builder_tools`, `ai_generation.py:249,361-363`) — the
  two prompt-text functions above only affect what the planner *reads*;
  this is what the planner is structurally *allowed to call*. Checked this
  session: it currently does `for feature_type, contract in
  OPERATION_CONTRACTS.items():` directly, with no registry indirection at
  all. Without this change `add_thread` would never be a real callable
  tool no matter what the prompt says. Fix: iterate
  `{**OPERATION_CONTRACTS, **plugin_registry.plugin_operation_contracts()}`
  here too — one call site, same pattern as the other two.

This reuses every generic mechanism already confirmed to derive from the
registry (Motivating evidence, above) except `_draft_feature_schema`,
which needs the one explicit change above — no other separate
plugin-aware prompt path to keep in sync.

**Literal (non-dimension) parameters.** `_value()`
(`app/feature_compiler.py:560-567`) treats every string parameter value as
a dimension id to look up, raising `CompilerError(..., "references
unknown parameter")` for anything else — there is no literal-string
concept anywhere in the current parameter language. Thread's `hand`
parameter (`"left"`/`"right"`, Part D) is not a measured dimension and was
never meant to be declared as one. Rather than changing `_value()` itself
(every core primitive relies on "every string is a dimension id" and
should keep relying on it), `OperationContract` gains one new optional
field, used only by the plugin dispatch branch (Part B):

```python
@dataclass(frozen=True)
class OperationContract:
    ...
    literal_parameters: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # parameter name -> its allowed literal string values, e.g. {"hand": ("left", "right")}
```

Two effects, both additive:

- **Schema**: `_parameter_schema` emits `{"enum": [...]}` instead of the
  generic `value_schema` for any parameter name present in
  `literal_parameters` — the planner is structurally restricted to a valid
  literal, not just asked nicely in prose.
- **Compiler**: Part B's plugin-dispatch branch checks
  `key in plugin.contract.literal_parameters` before calling `_value` —
  if so, and the value is one of the declared literals, it emits
  `json.dumps(value)` (a quoted Python string) directly instead of
  resolving it as a dimension id; anything else is a contract violation
  caught the same way a missing required parameter is today.

Core primitives (`box`, `cylinder`, ...) never set `literal_parameters` and
are completely unaffected — `_value()` itself does not change.

## Part B: Trusted plugin dispatch (not generated `def` blocks)

An earlier draft of this spec had a plugin's `compile` function emit a
`def _plugin_...(): ...` block of generated text for `exec` to run. Checked
against the actual sandbox this session and confirmed broken two
independent ways (Motivating evidence): `ast.FunctionDef` is forbidden
(`app/validator.py:11`) and the worker's `exec` namespace has no
`cq_warehouse`, no builtins, and no import mechanism at all
(`worker/cadquery_worker.py:28-34`). Widening either would loosen the
sandbox for every LLM-influenced core primitive, not just thread.

Instead: a plugin's geometry code is trusted, first-party Python that the
**worker imports directly and calls once**, by name, through one small
fixed shim — it never appears as generated text and never goes through
`validate_source` at all. Only a single, plain call expression referencing
that shim appears in the per-request generated script.

**Worker side** (`worker/cadquery_worker.py`) — one addition to the exec
namespace:

```python
from app.plugins import dispatch as plugin_dispatch  # import-light: no fastapi/pydantic

namespace = {
    "cq": cq, "PARAMETERS": parameters, "math": math, "int": int,
    "call_plugin": plugin_dispatch.call,   # new
    "__builtins__": {},
}
```

`app/plugins/dispatch.py` (and everything it imports, e.g.
`app/plugins/thread.py`) must import cleanly with no `fastapi`/`pydantic`
dependency, since the worker subprocess — deliberately minimal today,
`json`/`math`/`sys`/`time`/`traceback`/`pathlib` plus `cadquery`
(`worker/cadquery_worker.py:1-9`) — now imports it too. `plugin_dispatch.call(type_name, own_params)`
looks up the trusted `build` function registered for `type_name` (Part A)
and calls it directly; this is where `cq.Wire.makeHelix`,
`cq_warehouse.thread.IsoThread`, or whatever a plugin actually needs
lives and runs — as ordinary, code-reviewed, repo-committed Python, not a
string the compiler assembled.

**Compiler side** (`app/feature_compiler.py`) — one new branch in
`_compile_primitive`, generic across every plugin (no per-plugin
special-casing needed here, unlike the abandoned `def`-block design). A
plugin's shape is a primitive like any other: it only ever needs its own
`operation.parameters`, exactly like `box`/`cylinder` do today — the
existing, unchanged origin-translate and union/cut/root wiring in
`compile_feature_graph` (`app/feature_compiler.py:260-280`) is what
relates it to its target, not something the plugin call needs to know
about itself:

```python
plugin = plugin_registry.plugin_for(feature_type)
if plugin is not None:
    parts = []
    for key, value in operation.parameters.items():
        literals = plugin.contract.literal_parameters.get(key)
        if literals is not None:
            if value not in literals:
                raise CompilerError(operation.id, f"'{key}' must be one of {literals}")
            parts.append(f"{key}={json.dumps(value)}")
        else:
            parts.append(f"{key}={_value(value, parameter_ids, operation.id)}")
    expression = f'call_plugin({json.dumps(feature_type)}, {{{", ".join(parts)}}})'
    # falls into the same origin-translate / union-cut-intersect wiring every primitive uses
```

Whether a plugin's own geometry additionally needs to *know* something
about its target (thread does, to decide which end is "the bottom" — Part
D) is a target-shape question, addressed there, not a sandboxing question
— it does not change how the call gets past `validate_source`.

**Validator side** (`app/validator.py`) — the one narrow, deliberate
change this spec asks for: add `"call_plugin"` to `ALLOWED_DIRECT_CALLS`
(currently `{"int"}`, `validator.py:105`). Nothing else changes:
`FORBIDDEN_NODES` still forbids `FunctionDef`/`Import`/etc. exactly as
today, and `DISCOURAGED_SOURCE_PATTERNS`'s `"helix"` entry still fires on
any *generated* source that contains that word — it simply never needs to,
because "helix" now only ever appears inside `app/plugins/thread.py`'s own
static, non-generated source, which `validate_source` never sees. The
sandbox's job was always to constrain what LLM-chosen parameter *values*
can make generated text do; a first-party plugin module the planner never
writes a character of was never the thing it needed to constrain.

## Part C: Per-feature safety envelope

This part has two independent halves now, not one — checked this session
and found the original single isolated-pre-flight design covers neither
case completely on its own:

- Thread is a `cut` operation, and `FeatureOperation.
  operation_has_required_relationships` (`app/models.py:220-227`) rejects
  any `cut`/`intersect`/`modify`/`pattern` feature with no `target` —
  "build it alone as a target-less root" (the original Part C draft) is
  not a legal `CADProject` for a plugin whose contract is `cut`-based.
  Isolated verification needs a real, minimal, *valid* fixture instead.
- Even a pre-flight that passes does not guarantee the real full-draft
  build succeeds — the same plugin feature, wired to its *actual* target
  alongside its *actual* siblings, is a different build than the fixture.
  Today, any full-draft failure retries once against `fallback_draft`,
  which discards every feature down to a generic box
  (`app/main.py:_build_or_fallback`) — exactly the outcome this spec's own
  Goal says a working hex head and shank should not suffer for one risky
  feature elsewhere.

**Half 1 — isolated pre-flight, against a real fixture, before the full
draft build.**

Uses `FeaturePlugin.preflight_fixture` (Part A): given resolved
parameters (plain numbers), it returns a small, self-contained, legally
buildable `CADProject` exercising this plugin's geometry — thread's
fixture is a bare root cylinder at the requested nominal diameter, plus
the thread feature itself, `cut`-targeting that cylinder, exactly
satisfying the target-required validator above instead of trying to skip
it.

For each feature whose plugin declares `max_build_seconds` and/or
`verify_build`, before the normal full-draft `run_project` call:

1. Resolve that feature's own parameters to plain numbers (the same
   resolution `resolve_dimension_values`/`concrete_parameters` already do
   elsewhere — no new resolution logic) and call
   `plugin.preflight_fixture(resolved_parameters)` to get a minimal,
   valid, standalone `CADProject`.
2. Call `run_project(fixture_project, {}, fmt="stl", timeout_seconds=
   plugin.max_build_seconds)`. `run_project` and `_run_local`
   (`app/runner.py:85,149`) gain an explicit `timeout_seconds: float |
   None = None` parameter, defaulting to today's module-level
   `WORKER_TIMEOUT_SECONDS` when omitted — checked this session:
   `_run_local` currently reads that module constant directly with no
   parameter at all, and mutating a global per-request is unsafe once two
   requests (a normal build and a risky-plugin pre-flight) can be
   in flight at once under FastAPI's concurrency. An explicit parameter
   threaded through both functions avoids that race entirely; no other
   caller changes since the default preserves today's behavior exactly.
3. On success, run `plugin.verify_build(artifact_bytes, resolved_parameters)`
   — the resolved parameters are now part of the signature (a bare
   `bytes -> reason` check cannot compare against a diameter/pitch/length
   it was never given). For thread: load the STL with `numpy-stl`, confirm
   the mesh is closed/manifold, confirm computed volume is positive and
   within a wide sanity band of the analytic expectation (major-diameter
   cylinder volume minus a plausible groove volume, both computable from
   `resolved_parameters` alone), confirm the bounding box spans the
   requested length. Return a short human-readable reason string on
   failure, `None` on success.
4. On a timeout, a `RunnerError`, or a non-`None` `verify_build` result:
   mark *only this feature* `status="unsupported"` with that reason in
   `omission_reason` (Spec 9's existing field and roster display — no new
   UI) *before* the real full-draft build is attempted, and skip straight
   to Half 2's normal path with this feature already excluded.
5. On success: proceed to the real full-draft build with this feature
   included for real, wired to its real target — not the fixture's.

**Half 2 — a second, narrower fallback rung in `_build_or_fallback`, for
when the real build still fails despite a passing pre-flight.**

`RunnerError` already carries `detail.get("operation_id")` when the
worker can attribute the failure to one feature (`app/runner.py:94-95`;
`worker/cadquery_worker.py`'s `_operation_id_from_traceback`). Today
`_build_or_fallback` (`app/main.py:60-76`) ignores this and retries once
against `fallback_draft`, unconditionally. It gains one new rung, tried
*before* that unconditional one, and scoped to plugin features only (core
primitives keep exactly today's behavior — the Non-goals section's "no
change to existing failure handling" still holds for them):

```python
except (RunnerError, SpecificationValidationError) as exc:
    operation_id = getattr(exc, "detail", {}).get("operation_id")
    failing = next((f for f in draft.features if f.id == operation_id), None)
    if failing is not None and plugin_registry.plugin_for(failing.type) is not None:
        failing.status = "unsupported"
        failing.omission_reason = f"Removed after a build failure: {exc}"
        try:
            project = project_from_specification(draft)
            result = run_project(project, {}, fmt="stl")
            return project, result, draft
        except (RunnerError, SpecificationValidationError):
            pass  # fall through to the existing fallback_draft rung, unchanged
    draft = fallback_draft(draft)
    ...  # existing code, unchanged
```

Only when *this* retry also fails does today's `fallback_draft` rung run.
A working hex head and shank now survive a thread that fails during the
real build, not only one that fails during pre-flight.

This adds one new dependency: `numpy-stl` back into `requirements.txt`
(previously removed — see Motivating evidence — for an unrelated reason).
It adds no new failure mode for non-plugin or non-risky-plugin features: a
plugin that sets neither `max_build_seconds` nor `verify_build` skips Half
1 entirely, and Half 2's new rung only ever triggers for a plugin-typed
feature — both primitives and non-risky plugins are dispatched and fail
exactly as today.

## Part D: Thread — the first plugin

`app/plugins/thread.py`:

- **Contract**: `OperationContract(("cut",), ("nominal_diameter", "length"),
  ("pitch", "hand", "from_end"), literal_parameters={"hand": ("left",
  "right"), "from_end": ("base", "far")})`. `nominal_diameter`, `length`,
  and `pitch` are millimetre dimensions like every other measurement in
  the system (declared dimension ids, resolved the normal way); `hand`
  and `from_end` are literal parameters (Part A) — the planner's tool
  schema restricts each to its own `enum`, and the compiler emits both as
  quoted literals, never a dimension lookup. `hand` defaults to `"right"`
  when omitted; `from_end` defaults to `"far"` (a thread is almost always
  at the tip end, away from a head/flange) — `"base"` is the explicit
  override for the less common case. `from_end` is the parameter the
  Placement rules below actually resolve against; the LLM never computes
  a coordinate, only picks one of two words.
- **Deterministic pitch lookup** (no LLM guessing): a small static ISO
  261 coarse-pitch table shipped in the plugin module —
  `{3: 0.5, 4: 0.7, 5: 0.8, 6: 1.0, 8: 1.25, 10: 1.5, 12: 1.75, 14: 2.0,
  16: 2.0, 18: 2.5, 20: 2.5, 24: 3.0}` — keyed by nominal diameter. A
  user writing "add M12 thread ... for 30mm" gives the planner only
  `nominal_diameter=12`, `length=30`, `target=<existing shank feature id>`;
  the plugin derives `pitch=1.75` itself. An explicit pitch in the prompt
  ("M12x1.25") overrides the table.
- **Geometry**: given the spike's confirmed failure (a hand-rolled V-groove
  sweep silently produced non-manifold fragments, not a genuine kernel
  limitation ruled out by testing multiple approaches), v1 should reach
  for a maintained thread-geometry library — `cq_warehouse.thread`
  (`ExternalThread`/`IsoThread`, built specifically for CadQuery and this
  exact standard) — rather than re-deriving ISO 68-1 sweep math in-repo.
  This needs its own short spike before Part B/C are wired up for real:
  confirm `cq_warehouse` installs cleanly against the pinned CadQuery
  2.8.0, and re-run the same M12/M16 manifold+timing check this session's
  spike ran by hand, this time against `cq_warehouse`'s output.
- **Placement — exact rules, not left to "the same convention as
  cylinder"** (a real gap in the first draft of this spec): a thread's own
  origin/axis is not enough by itself to answer "which end is the bottom"
  or "is 30mm even legal here." The contract instead requires:
  - `target` must resolve to a feature of type `cylinder` (or another
    plugin whose `resolved_extent` reports a single circular axis) — the
    compiler rejects any other target type for a `thread` feature before
    ever reaching the worker, the same way a missing required parameter
    is rejected today.
  - The thread's axis and its two candidate ends come from the *target
    cylinder's own declared placement* — `"base"` is the target's own
    `origin` (a cylinder's origin is already, by this session's
    established convention, the center of its circular cross-section at
    its base), `"far"` is that same point advanced by the target's
    `height` along its axis. Never a separately-declared origin on the
    thread feature itself. A freeform instruction naming an end ("at the
    bottom", "from the tip") maps to the `from_end` parameter, which
    selects which one the planner passes — the LLM picks a word, the
    compiler computes the point.
  - `length` must not exceed the target's own resolved length along that
    axis; a longer request is a contract violation caught before build,
    not a build failure discovered later.
  - The thread feature's own coordinates are computed relative to the
    target's extent boundary (chosen end + inward along the shared axis),
    not the scene's global origin — so it is correct regardless of where
    the target cylinder itself was placed.
- **Wiring into scoped refine**: no new endpoint. "Add M12 thread at the
  bottom of the bolt for 30mm" is an ordinary freeform instruction,
  ideally with the shank feature `@mention`-scoped (Spec 9 Part D) so
  `referenced_feature_ids` already tells the planner which cylinder to
  target; the planner emits one `thread` feature with `target` set to
  that id. No change to `/api/model/refine` or `PromptRequest`.
- **Roster display**: a confirmed thread feature's `resolved_extent`
  defaults to its target cylinder's own extent (a thread doesn't change
  the model's outer bounding box) unless a future revision wants a
  tighter groove-only overlay.

## Part E: Engraving (illustrative second plugin, sketch only)

Included only to check Part A-C generalize past one case, at Spec 9's own
risk-section level of detail — not a commitment to build this next.
Today's `text` feature type (`app/feature_compiler.py`, cut/add
operations) already covers flat text on one named planar face. A future
`engraving` plugin would plausibly differ in needing a *non-planar*
target face (wrapping text or a simple imported outline around a
cylindrical shank, for example) — genuinely new geometry, not a
parameter variant of `text` — which is exactly the kind of specialized,
independently-risky generator this spec's plugin boundary is for. Left
as a placeholder contract with no `compile` implementation in v1.

## Risks / open questions

- **`cq_warehouse` dependency risk is unconfirmed — and now the central
  go/no-go, not a later checkbox** (Rollout order below). This spec
  recommends it over hand-rolled sweep math specifically because the
  hand-rolled version demonstrably produced broken geometry this session
  — but `cq_warehouse`'s own compatibility with the pinned CadQuery 2.8.0,
  its build performance at realistic lengths, its own manifold
  correctness, and — new concern this revision — whether it can be called
  at all from a `import`-light module the worker subprocess loads
  (Part B) are all unverified.
- **Isolated pre-flight now costs up to two extra worker invocations for
  a draft containing a risky plugin feature**: the fixture build (Half 1),
  and, only if the real build still fails despite a passing pre-flight,
  one retry with that feature excluded (Half 2). Acceptable for v1 given
  threads are opt-in (only present when explicitly requested), but worth
  watching if a future risky plugin becomes common in ordinary drafts.
- **`verify_build`'s sanity band is inherently approximate** — "volume
  within a wide band of the analytic expectation" needs an actual
  tolerance chosen from real test cases, not guessed once and left alone.
- **Multi-start or internal (tapped-hole) threads are out of scope** but
  the contract shape (`hand`, future `starts` parameter) should not make
  them structurally harder to add later.
- **`_draft_feature_schema`/`_parameter_schema` are shared code**: the
  merged-registry iteration and the new `literal_parameters` → `enum`
  branch touch the same function every core primitive's tool schema goes
  through. Low risk (both changes are additive — no plugins registered
  means both are no-ops) but worth an explicit regression test that every
  existing primitive's generated schema is byte-for-byte unchanged with
  the registry empty.
- **Verification**: unit tests for the registry merge (`_draft_feature_schema`
  includes a fake test plugin's `oneOf` variant with the right `enum` for
  a literal parameter, not just the prompt-text functions), the trusted
  dispatch call-expression generation with a synthetic plugin, the
  isolated-pre-flight decision table (timeout / verify failure / success)
  against a synthetic `preflight_fixture`, and `_build_or_fallback`'s new
  middle rung (a synthetic plugin feature failing only in the full-draft
  build, with real siblings surviving) — all without needing the real
  worker subprocess or `cq_warehouse`. A real-provider E2E only after the
  library spike below confirms real thread geometry is reliably
  buildable.

## Suggested rollout order

1. **The `cq_warehouse` feasibility spike, first, standalone, before any
   framework code** (moved ahead of Parts A-C this revision — KISS: no
   point building a general plugin mechanism if the one motivating
   plugin can't actually be built). Install it against the pinned
   CadQuery 2.8.0; confirm it can be invoked from an import-light module
   with no `fastapi`/`pydantic` in its import chain (Part B); re-run the
   same M12/M16 manifold+timing check this session's hand-rolled spike
   ran, this time against `cq_warehouse`'s output. Go/no-go point: if it
   cannot reliably build a manifold, correctly-dimensioned thread for a
   handful of common nominal sizes within a few seconds, stop and
   reconsider (a coarser visual-only representation, or staying with
   text-only annotation) rather than building Parts A-D regardless.
2. Part A (registry, including the `_draft_feature_schema` merge and
   `literal_parameters`) + Part B (trusted dispatch) — tested against a
   synthetic fake plugin (no `cq_warehouse` needed yet).
3. Part C (both halves of the safety envelope) — same, testable with a
   synthetic plugin whose `preflight_fixture` deliberately times out or
   fails `verify_build`, and a second synthetic plugin that passes
   pre-flight but fails the full build (to exercise Half 2), before any
   real thread geometry exists.
4. Part D (thread plugin) for real, using whatever the spike in step 1
   confirmed actually works, gated behind the now-tested Parts A-C.
5. Part E (engraving) is future work, not part of this rollout.
