"""Streaming LLM lifecycle: telemetry, empty replies, and disconnect cancellation."""

import asyncio
from types import SimpleNamespace

import pytest

import app.llm as llm
import app.main as main


class _Stream:
    def __init__(self, chunks, request_id="ds-request-1"):
        self._chunks = iter(chunks)
        self.response = SimpleNamespace(headers={"x-request-id": request_id})

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._chunks)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _Client:
    def __init__(self, chunks):
        self.closed = False
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._chunks = chunks

    async def _create(self, **_kwargs):
        return _Stream(self._chunks)

    async def close(self):
        self.closed = True


def _chunk(content=None, *, finish_reason=None, reasoning=None, usage=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            delta=SimpleNamespace(content=content, reasoning_content=reasoning),
            finish_reason=finish_reason,
        )],
        usage=usage,
    )


def test_stream_completion_collects_content_and_safe_metadata(monkeypatch, caplog):
    client = _Client([
        _chunk("result = "),
        _chunk("part", reasoning="think"),
        _chunk(finish_reason="stop", usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5)),
    ])
    monkeypatch.setattr(llm, "make_async_client", lambda *_args, **_kwargs: client)

    result = asyncio.run(llm.stream_completion(
        [{"role": "user", "content": "x"}], "deepseek", None,
        temperature=0, max_tokens=8, operation="generate",
        prompt_for_log="use sk-abcdef0123456789 please",
    ))

    assert result.content == "result = part"
    assert result.finish_reason == "stop"
    assert result.request_id == "ds-request-1"
    assert result.reasoning_chars == 5
    assert result.total_tokens == 5
    assert client.closed is True
    assert "llm.stream event" in caplog.text
    assert "content_preview='result = '" in caplog.text
    assert "reasoning_chars=5" in caplog.text
    assert "think" not in caplog.text
    assert "<redacted>" in caplog.text
    assert "sk-abcdef" not in caplog.text


def test_stream_completion_rejects_empty_content_without_retry(monkeypatch, caplog):
    client = _Client([_chunk(finish_reason="insufficient_system_resource")])
    monkeypatch.setattr(llm, "make_async_client", lambda *_args, **_kwargs: client)

    with pytest.raises(llm.LLMEmptyResponse):
        asyncio.run(llm.stream_completion(
            [{"role": "user", "content": "x"}], "deepseek", None,
            temperature=0, max_tokens=8, operation="generate", prompt_for_log="x",
        ))
    assert client.closed is True
    assert "content_chars=0" in caplog.text
    assert "finish_reasons=['insufficient_system_resource']" in caplog.text


def test_stream_logs_reasoning_only_response_without_exposing_reasoning(monkeypatch, caplog):
    client = _Client([_chunk(reasoning="private reasoning"), _chunk(finish_reason="length")])
    monkeypatch.setattr(llm, "make_async_client", lambda *_args, **_kwargs: client)

    with pytest.raises(llm.LLMEmptyResponse):
        asyncio.run(llm.stream_completion(
            [{"role": "user", "content": "x"}], "deepseek", None,
            temperature=0, max_tokens=8, operation="generate", prompt_for_log="x",
        ))

    assert "content_chars=0" in caplog.text
    assert "reasoning_chars=17" in caplog.text
    assert "finish_reasons=['length']" in caplog.text
    assert "private reasoning" not in caplog.text


def test_stream_logs_scrub_prompt_content_and_provider_errors(monkeypatch, caplog):
    prompt_secret = "Authorization: Token token-value-123456"
    content_secret = "password=not-for-logs"
    client = _Client([_chunk(content_secret), _chunk(finish_reason="stop")])
    monkeypatch.setattr(llm, "make_async_client", lambda *_args, **_kwargs: client)

    asyncio.run(llm.stream_completion(
        [{"role": "user", "content": "x"}], "deepseek", None,
        temperature=0, max_tokens=8, operation="generate", prompt_for_log=prompt_secret,
    ))

    assert prompt_secret not in caplog.text
    assert content_secret not in caplog.text
    assert caplog.text.count("<redacted>") >= 2

    async def fail_create(**_kwargs):
        raise RuntimeError("api_key=also-not-for-logs")

    client.chat.completions.create = fail_create
    with pytest.raises(llm.LLMError):
        asyncio.run(llm.stream_completion(
            [{"role": "user", "content": "x"}], "deepseek", None,
            temperature=0, max_tokens=8, operation="generate", prompt_for_log="x",
        ))
    assert "also-not-for-logs" not in caplog.text


def test_stream_logs_all_event_metadata_but_previews_only_the_first_events(monkeypatch, caplog):
    client = _Client([_chunk("a"), _chunk("b"), _chunk("c"), _chunk("d"), _chunk(finish_reason="stop")])
    monkeypatch.setattr(llm, "make_async_client", lambda *_args, **_kwargs: client)

    asyncio.run(llm.stream_completion(
        [{"role": "user", "content": "x"}], "deepseek", None,
        temperature=0, max_tokens=8, operation="generate", prompt_for_log="x",
    ))

    assert "event=5" in caplog.text
    assert "event=4 choices=1 content_chars=1 content_preview='<omitted>'" in caplog.text


def test_provider_error_scrubs_the_log_and_client_message(caplog):
    error = main._provider_error("LLM error", RuntimeError("password=not-for-logs"))

    assert error.status_code == 502
    assert "not-for-logs" not in caplog.text
    assert "not-for-logs" not in error.detail["message"]


def test_disconnect_cancels_inflight_llm_task():
    cancelled = asyncio.Event()

    async def slow_call():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    request = SimpleNamespace(is_disconnected=lambda: _true())
    async def run():
        with pytest.raises(main.HTTPException) as exc:
            await main._await_llm(request, "generate", slow_call())
        return exc.value

    exc = asyncio.run(run())

    assert exc.status_code == 499
    assert cancelled.is_set()


def test_completed_llm_call_does_not_wait_for_disconnect_poll():
    request = SimpleNamespace(is_disconnected=lambda: _false())

    assert asyncio.run(main._await_llm(request, "generate", _result("done"))) == "done"


async def _true():
    return True


async def _false():
    return False


async def _result(value):
    return value


def test_async_client_has_no_timeout_or_implicit_retries(monkeypatch):
    monkeypatch.setenv("DEEP_SEEK_KEY", "test-key")
    client = llm.make_async_client("deepseek")
    try:
        assert client.timeout is None
        assert client.max_retries == 0
    finally:
        asyncio.run(client.close())


def test_sync_validation_client_keeps_configured_timeout(monkeypatch):
    seen = {}
    monkeypatch.setenv("DEEP_SEEK_KEY", "test-key")
    monkeypatch.setattr(llm, "LLM_TIMEOUT", 42)
    monkeypatch.setattr(llm, "OpenAI", lambda **kwargs: seen.update(kwargs) or object())

    llm.make_client("deepseek")

    assert seen["timeout"] == 42
    assert seen["max_retries"] == 0


def test_empty_generation_maps_to_a_rephrase_error():
    error = main._empty_response_error()

    assert error.status_code == 422
    assert error.detail["code"] == "empty_response"
    assert "rephrase" in error.detail["message"].lower()
