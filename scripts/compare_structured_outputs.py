"""Compare direct DeepSeek strict tools with OpenRouter JSON Schema on one recorded analysis."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app.main  # noqa: E402  Loads .env without printing secrets.
from app.models import DraftSpecification  # noqa: E402
from tests.provider_payloads import normalize_draft_specification_payload  # noqa: E402

SCHEMA = DraftSpecification.model_json_schema()
SCHEMA["required"] = ["title", "units", "dimensions", "features", "assumptions", "questions", "annotations"]
for name in ("dimensions", "features"):
    SCHEMA["properties"][name]["minItems"] = 1
PROMPT = "Convert this drawing analysis into the supplied DraftSpecification schema. Return unknown critical geometry as questions; do not return CAD code."


async def request(name: str, url: str, key: str, payload: dict, analysis: dict) -> dict:
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(url, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json=payload)
    except httpx.HTTPError as exc:
        return {"adapter": name, "status_code": None, "elapsed_ms": round((time.perf_counter() - started) * 1000), "transport_error": str(exc)}
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    result = {"adapter": name, "status_code": response.status_code, "elapsed_ms": elapsed_ms}
    if response.status_code >= 400:
        result["error"] = response.text[:500]
        return result
    body = response.json()
    result["usage"] = body.get("usage", {})
    message = body["choices"][0]["message"]
    raw = message.get("tool_calls", [{}])[0].get("function", {}).get("arguments") if message.get("tool_calls") else message.get("content")
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        result["tool_arguments"] = parsed
        canonical_analysis = {"views": analysis.get("views", []), "dimensions": [], "features": analysis.get("features", []), "uncertainties": []}
        draft = DraftSpecification.model_validate({**normalize_draft_specification_payload(parsed), "analysis": canonical_analysis})
        result["draft_schema_valid"] = True
        result["dimension_count"] = len(draft.dimensions)
        result["feature_count"] = len(draft.features)
        result["node_count"] = len(draft.dimensions) + len(draft.features)
        result["question_count"] = len(draft.questions)
    except Exception as exc:
        result["draft_schema_valid"] = False
        result["validation_error"] = str(exc)[:500]
    return result


async def main() -> None:
    records = [json.loads(line) for line in (ROOT / "logs/llm_responses.jsonl").read_text().splitlines()]
    decoder = json.JSONDecoder()
    analyses = []
    for record in records:
        if record["stage"] != "vision_analysis":
            continue
        try:
            analyses.append(decoder.raw_decode(record["content"])[0] if isinstance(record["content"], str) else record["content"])
        except json.JSONDecodeError:
            continue
    analysis = next(item for item in reversed(analyses) if item.get("title") == "Open Box with Rim")
    messages = [{"role": "system", "content": PROMPT}, {"role": "user", "content": json.dumps({"drawing_analysis": analysis})}]
    deepseek = {
        "model": os.environ.get("DEEP_SEEK_MODEL", "deepseek-chat"), "messages": messages,
        "tools": [{"type": "function", "function": {"name": "submit_draft_specification", "description": "Return the draft specification.", "parameters": SCHEMA, "strict": True}}],
        "tool_choice": {"type": "function", "function": {"name": "submit_draft_specification"}},
    }
    openrouter = {
        "model": os.environ["OPEN_ROUTER_MODEL"], "messages": messages,
        "max_tokens": 20000,
        "response_format": {"type": "json_schema", "json_schema": {"name": "draft_specification", "strict": True, "schema": SCHEMA}},
    }
    results = await asyncio.gather(
        request("deepseek_strict_tool", os.environ.get("DEEP_SEEK_BASE_URL", "https://api.deepseek.com/chat/completions"), os.environ["DEEP_SEEK_KEY"], deepseek, analysis),
        request("openrouter_json_schema", "https://openrouter.ai/api/v1/chat/completions", os.environ["OPEN_ROUTER_KEY"], {**openrouter, "model": os.environ.get("OPEN_ROUTER_STRUCTURED_MODEL", "google/gemma-4-26b-a4b-it")}, analysis),
    )
    report = json.dumps(results, indent=2, sort_keys=True)
    output = ROOT / "artifacts" / "structured-output-comparison.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    asyncio.run(main())
