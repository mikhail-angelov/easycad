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
