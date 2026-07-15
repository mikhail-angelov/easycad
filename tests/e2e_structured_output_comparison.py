"""Real provider comparison using one recorded Gemini vision-analysis fixture."""
from __future__ import annotations

import asyncio
import json
import os
import unittest
from pathlib import Path

from app.main import load_env
from scripts.compare_structured_outputs import request
from app.models import DraftSpecification

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts" / "structured-output-comparison"


def open_box_analysis() -> dict:
    decoder = json.JSONDecoder()
    for line in reversed((ROOT / "logs" / "llm_responses.jsonl").read_text(encoding="utf-8").splitlines()):
        record = json.loads(line)
        if record.get("stage") != "vision_analysis":
            continue
        try:
            value = decoder.raw_decode(record["content"])[0] if isinstance(record["content"], str) else record["content"]
        except json.JSONDecodeError:
            continue
        if value.get("title") == "Open Box with Rim":
            return value
    raise AssertionError("Recorded Gemini Open Box with Rim analysis fixture is unavailable")


class StructuredOutputComparisonE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        load_env()
        if not os.environ.get("DEEP_SEEK_KEY") or not os.environ.get("OPEN_ROUTER_KEY"):
            raise unittest.SkipTest("Missing real-provider keys")
        OUT.mkdir(parents=True, exist_ok=True)
        cls.analysis = open_box_analysis()
        cls.messages = [{"role": "system", "content": "Convert this drawing analysis into the supplied DraftSpecification schema. Return unknown critical geometry as questions; do not return CAD code."}, {"role": "user", "content": json.dumps({"drawing_analysis": cls.analysis})}]

    def test_1_deepseek_strict_tool_writes_result(self):
        schema = DraftSpecification.model_json_schema()
        payload = {"model": os.environ.get("DEEP_SEEK_MODEL", "deepseek-chat"), "messages": self.messages, "tools": [{"type": "function", "function": {"name": "submit_draft_specification", "description": "Return the DraftSpecification.", "parameters": schema, "strict": True}}], "tool_choice": {"type": "function", "function": {"name": "submit_draft_specification"}}}
        result = asyncio.run(request("deepseek_strict_tool", os.environ.get("DEEP_SEEK_BASE_URL", "https://api.deepseek.com/chat/completions"), os.environ["DEEP_SEEK_KEY"], payload, self.analysis))
        (OUT / "deepseek.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        self.assertTrue(result.get("draft_schema_valid"), result)
        for feature in result["tool_arguments"].get("features", []):
            self.assertNotIn("offset", feature.get("placement") or {}, result)

    def test_2_openrouter_strict_schema_writes_result(self):
        payload = {"model": os.environ.get("OPEN_ROUTER_STRUCTURED_MODEL", "google/gemma-4-26b-a4b-it"), "messages": self.messages, "max_tokens": 20000, "response_format": {"type": "json_schema", "json_schema": {"name": "draft_specification", "strict": True, "schema": SCHEMA}}}
        result = asyncio.run(request("openrouter_json_schema", "https://openrouter.ai/api/v1/chat/completions", os.environ["OPEN_ROUTER_KEY"], payload, self.analysis))
        (OUT / "openrouter.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        self.assertTrue(result.get("draft_schema_valid"), result)

    def test_3_compare_saved_feature_nodes(self):
        deepseek = json.loads((OUT / "deepseek.json").read_text(encoding="utf-8"))
        openrouter = json.loads((OUT / "openrouter.json").read_text(encoding="utf-8"))
        comparison = {"deepseek": deepseek, "openrouter": openrouter, "node_count_delta": deepseek["node_count"] - openrouter["node_count"]}
        (OUT / "comparison.json").write_text(json.dumps(comparison, indent=2, sort_keys=True), encoding="utf-8")
        self.assertGreater(deepseek["node_count"], 0)
        self.assertGreater(openrouter["node_count"], 0)


if __name__ == "__main__":
    unittest.main()
