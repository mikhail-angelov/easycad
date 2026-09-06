## 2026-08-04 — Disconnect-aware async LLM calls

### Goal

Keep an in-flight LLM request cancellable when the browser disconnects without making normal FastAPI/TestClient requests hang.

### Golden path

1. Run the provider coroutine as one `asyncio` task.
2. Poll that task with a short `asyncio.wait(..., timeout=0.1)`.
3. Between polls, call `request.is_disconnected()`; on disconnect, cancel and await the provider task, then return the 499 API error.
4. Make all `generate_code` and `triage` test doubles `async def`, matching their production contract.

### Verification

`tests/test_llm_streaming.py` covers both completed and disconnected calls. The full app suite passed: `294 passed, 24 skipped`.

### Failure pattern avoided

A separately spawned task that loops over `request.is_disconnected()` can fail to finish after `Task.cancel()` under Starlette/TestClient, so awaiting that task blocks a successful LLM request indefinitely.

### Ruled-out approaches

- Tried a background disconnect task plus `await asyncio.gather()` after cancellation; normal request tests hung because the Starlette disconnect check did not terminate the task.
- Tried retaining synchronous LLM test stubs; that required a mixed sync/async production seam and hid contract mismatches.

## 2026-08-04 — CadQuery text needs a font in the worker image

### Goal

Make generated engraving and embossing work in the isolated production worker.

### Golden path

1. Install `fontconfig` and `fonts-dejavu-core` in `worker/Dockerfile`.
2. Require `font="DejaVu Sans"` in every generated `Workplane.text(...)` call.
3. Build the worker image and verify the text operation inside that image, not only in the macOS development environment.

### Verification

`docker build -f worker/Dockerfile -t easycad-worker:text-font .` succeeded. In
that image, `fc-match Arial` resolves to DejaVu Sans and an 8 mm, 1 mm-deep
CadQuery engraving executed successfully.

### Failure pattern avoided

The `python:3.11-slim` worker image has no fontconfig database or fonts by
default. CadQuery then raises `AttributeError: 'NoneType' object has no attribute
'FontName'` for every `.text(...)` call, even though the same code works locally.

### Ruled-out approaches

- Tried the engraving in the original worker image; it failed because no font
  could be resolved, not because of face placement or the CadQuery text API.

## 2026-09-06 — Recipe selection must survive skipped triage

### Goal

Keep specialised recipes available on initial requests, refinement-off turns and
variations without another provider call.

### Golden path

1. Select recipes at the shared `generate_code` boundary. Preserve explicit
   triage tags: `None` enables fallback, `[]` opts out.
2. Capture generator messages with external completion stubbed; exercise initial,
   refinement-off and variations requests through the API.
3. Include plain boxes, clearance holes, whole-part thread negation and partially
   threaded bolts in routing regressions.
4. Measure execution and geometry separately in live comparisons.

### Verification

The initial-request regression failed on the missing recipe before the fix and
passed after it. Recipe tests: 44 passed. Full app suite: 333 passed, 24 platform
skips. A/B conditions and limits: `docs/thread-recipe-routing-2026-09-06.md`.

### Failure pattern avoided

Choosing recipes only inside optional triage silently disables them on common
generation paths. Successful export also does not establish correct dimensions
or a watertight mesh, even with an executable recipe.

### Ruled-out approaches

- Existing triage-only selection failed the first-request regression.
- Broad `винт` matching selected propellers and spiral stairs; ambiguous fastener
  words now remain with triage.
- Whole-request negation dropped partially threaded bolts; smooth sections are
  handled separately and covered by regression cases.

### Notes

In this SOCKS-proxy environment, installing ordinary requirements left API client
construction failing. Installing
`uv pip install --python .venv-poc/bin/python 'httpx[socks]'` fixed it, and the
full suite passed. The experiment's mesh connectivity analysis needed `networkx`.

## 2026-09-06 — Match geometry measurements to the exporter's shape scope

### Goal

Verify geometry feedback for single- and multi-body models without assuming
that a Workplane's first value or Python wrapper describes the exported model.

### Golden path

1. Check the installed CadQuery export implementation and Workplane iterator.
   In 2.8.0, export accepts a Shape or collects the entire iterable into a compound.
2. Materialise the export shape once; measure that same shape. Do not fuse bodies
   or force a single-solid requirement just to simplify measurements.
3. Exercise the real isolated `execute()` boundary. Compare known dimensions
   against both feedback and independently parsed STL vertices.
4. Test the next chat request with only external completion stubbed, to verify
   delivery of measured context without spending LLM tokens.

### Verification

`CADQUERY_WORKER_TIMEOUT_SECONDS=120 XDG_CACHE_HOME=$PWD/.cache PYTHONDONTWRITEBYTECODE=1
.venv-poc/bin/python -m pytest tests/test_cadquery_exec.py tests/test_spec20.py -q`:
18 passed. Cases include a drilled plate, washers, a pocketed enclosure and lid,
multiple stack items, and raw shapes.

### Failure pattern avoided

`result.val()` can under-report the exported geometry. Python wrapper classes
can also differ from the underlying topology: in this environment `shell(-2)`
returned a `Compound` wrapper around a `Solid`, which Workplane iteration
expanded into a `Shell`. Count solids in the export shape, not presumed solids
from source operations. This measurement fix does not repair that library behavior.

### Ruled-out approaches

- Measuring only `.val()` failed both multi-body dimension and raw-shape feedback
  regressions while STL export succeeded.
- Assuming a shell-built enclosure plus lid exports as two solids failed the
  topology checks. A rectangular `cutBlind(-18)` pocket preserved a solid under
  export iteration and passed the same enclosure checks.
