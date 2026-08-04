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

