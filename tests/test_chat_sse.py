"""SSE progress transport for the interactive chat endpoint."""

import asyncio
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

import app.main as m
from app.cadquery_exec import ExecResult
from app.main import app


BOX = "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 10, 10)\n"


def _events(body: str) -> list[tuple[str, dict]]:
    out = []
    for frame in body.strip().split("\n\n"):
        name, data = frame.split("\n", 1)
        out.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


def test_chat_streams_real_progress_and_its_final_result(monkeypatch):
    async def generate(*_args, **_kwargs):
        return BOX

    async def execute(_request, _code):
        return ExecResult(True, stl_base64="mesh", geometry_info="geometry")

    monkeypatch.setattr(m, "generate_code", generate)
    monkeypatch.setattr(m, "_execute_if_connected", execute)

    response = TestClient(app).post(
        "/api/chat",
        json={"prompt": "add a hole", "auto_refine": False, "current_code": BOX},
        headers={"accept": "text/event-stream", "x-real-ip": "8.8.8.8"},
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    assert [name for name, _data in events] == ["progress", "progress", "progress", "result"]
    assert [data["stage"] for name, data in events if name == "progress"] == [
        "accepted", "generating", "executing",
    ]
    assert events[-1][1]["action"] == "generated"
    assert events[-1][1]["step"]["success"] is True


def test_cancelling_an_sse_stream_cancels_chat_and_progress_waiter(monkeypatch):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_chat(*_args, **_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(m, "_chat_response", slow_chat)

    async def run():
        stream = m._chat_sse(m.ChatRequest(prompt="x"), SimpleNamespace(), object())
        assert "accepted" in await anext(stream)
        await started.wait()
        next_event = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        next_event.cancel()
        with pytest.raises(asyncio.CancelledError):
            await next_event
        await asyncio.sleep(0)
        pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()]
        assert pending == []

    asyncio.run(run())
    assert cancelled.is_set()
