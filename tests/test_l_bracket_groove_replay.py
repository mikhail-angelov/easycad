"""Deterministic replay for the ambiguous R12 groove in the L-bracket drawing."""
from __future__ import annotations

import asyncio
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.ai_generation as ai
import app.main as main
from app.models import DraftSpecification, DrawingAnalysis, SpecificationQuestion


ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "llm_features" / "l_bracket_groove_replay.json"
OUT = ROOT / "artifacts" / "l_bracket_groove_replay"


class _ReplayResponse:
    status_code = 200
    text = ""

    def __init__(self, tool_calls: list[dict]):
        self._payload = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": tool_calls}}]}

    def json(self) -> dict:
        return self._payload


class _ReplayAsyncClient:
    responses: list[_ReplayResponse] = []
    requests: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url: str, *, headers: dict, json: dict) -> _ReplayResponse:
        self.__class__.requests.append(json)
        if not self.__class__.responses:
            raise AssertionError("The replay received an unexpected planner request")
        return self.__class__.responses.pop(0)


def _tool_calls(specification: dict) -> list[dict]:
    """Encode the golden draft as the exact provider tool-call sequence."""
    calls = [("set_draft_metadata", {"title": specification["title"], "units": specification["units"]})]
    calls.extend(("add_dimension", item) for item in specification["dimensions"])
    calls.extend((f"add_{item['type']}", item) for item in specification["features"])
    calls.extend(("add_assumption", item) for item in specification["assumptions"])
    calls.extend(("add_question", item) for item in specification["questions"])
    calls.extend(("add_annotation", item) for item in specification["annotations"])
    calls.append(("finish_draft", {}))
    return [
        {"id": f"call_{index}", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}
        for index, (name, arguments) in enumerate(calls, start=1)
    ]


class LBracketGrooveReplayTests(unittest.TestCase):
    def test_user_clarification_replays_golden_tool_calls_and_exports_stl(self):
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        analysis = ai.normalize_drawing_analysis(fixture["vision_response"])
        initial = DraftSpecification.model_validate(fixture["golden_specification"])
        initial.analysis = DrawingAnalysis.model_validate(analysis)
        groove = next(item for item in initial.features if item.id == "top_groove")
        groove.status = "needs_input"
        groove.placement.plane = "XZ"  # the alternate, valid-but-wrong reading
        initial.questions = [
            SpecificationQuestion(
                id="top_groove_direction",
                field_id="top_groove",
                prompt="Should the R12 groove run across the 60 mm span or through the 28 mm upright thickness?",
                alternatives=["along the 60 mm span", "through the 28 mm thickness"],
            )
        ]

        calls = _tool_calls(fixture["golden_specification"])
        self.assertEqual([call["function"]["name"] for call in calls], fixture["expected_tool_names"])
        groove_call = next(call for call in calls if json.loads(call["function"]["arguments"]).get("id") == "top_groove")
        self.assertEqual(groove_call["function"]["name"], "add_cylinder")
        groove_arguments = json.loads(groove_call["function"]["arguments"])
        self.assertEqual(groove_arguments["operation"], "cut")
        self.assertEqual(groove_arguments["target"], "base_body")
        self.assertEqual(groove_arguments["parameters"], {"radius": "groove_radius", "height": "upright_thickness"})
        self.assertEqual(groove_arguments["placement"], {"plane": "YZ", "origin": [0, "width_mid", "overall_height"]})
        upright_dimension = next(
            json.loads(call["function"]["arguments"])
            for call in calls
            if call["function"]["name"] == "add_dimension"
            and json.loads(call["function"]["arguments"])["id"] == "upright_thickness"
        )
        self.assertEqual(upright_dimension["value"], 28)

        initial_calls = _tool_calls(initial.model_dump(mode="json"))
        self.assertIn("add_question", [call["function"]["name"] for call in initial_calls])
        _ReplayAsyncClient.responses = [_ReplayResponse(initial_calls), _ReplayResponse(calls)]
        _ReplayAsyncClient.requests = []

        async def replay_planner(*args, **kwargs):
            return await ai.plan_draft_specification(*args, **kwargs)

        with patch.dict(os.environ, {"OPEN_ROUTER_PLANNER_MODEL": ""}), patch.object(ai.httpx, "AsyncClient", _ReplayAsyncClient), patch.object(main, "plan_draft_specification", replay_planner):
            initial = asyncio.run(ai.plan_draft_specification(analysis, "", "replay-key"))
            self.assertEqual(initial.questions[0].id, "top_groove_direction")
            client = TestClient(main.app)
            validation = client.post(
                "/api/specifications/validate",
                json={
                    "specification": initial.model_dump(mode="json"),
                    "clarifications": {"top_groove_direction": fixture["user_clarification"]},
                },
            )

        self.assertEqual(validation.status_code, 200, validation.text)
        payload = validation.json()
        self.assertTrue(payload["valid"], payload.get("diagnostics"))
        self.assertEqual(len(_ReplayAsyncClient.requests), 2)
        planner_request = _ReplayAsyncClient.requests[1]
        prompt = planner_request["messages"][0]["content"]
        self.assertIn("when a groove or cut is only described as centred", prompt)
        planner_context = json.loads(planner_request["messages"][1]["content"])
        self.assertEqual(planner_context["user_inputs"]["clarifications"], {"top_groove_direction": fixture["user_clarification"]})

        replanned = payload["specification"]
        self.assertEqual(replanned["questions"], [])
        final_groove = next(item for item in replanned["features"] if item["id"] == "top_groove")
        self.assertEqual(final_groove["placement"]["plane"], "YZ")
        self.assertEqual(final_groove["parameters"]["height"], "upright_thickness")
        self.assertEqual(final_groove["placement"]["origin"], [0, "width_mid", "overall_height"])

        build = client.post("/api/specifications/build", json=replanned)
        self.assertEqual(build.status_code, 200, build.text)
        self.assertEqual(build.json()["status"], "success", build.json().get("diagnostics"))
        export = client.post("/api/projects/export?format=stl", json={"project": build.json()["project"], "parameters": {}})
        self.assertEqual(export.status_code, 200, export.text)
        self.assertEqual(export.headers["content-type"], "model/stl")
        OUT.mkdir(parents=True, exist_ok=True)
        stl_path = OUT / "l_bracket_groove.stl"
        stl_path.write_bytes(export.content)
        self.assertGreater(stl_path.stat().st_size, 100)


if __name__ == "__main__":
    unittest.main()
