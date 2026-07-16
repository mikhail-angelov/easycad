from __future__ import annotations

import unittest
import asyncio
import json
from unittest.mock import AsyncMock, patch

import app.ai_generation as ai
from app.feature_compiler import OPERATION_CONTRACTS, draft_specification_operation_types
from app.models import (
    DraftSpecification,
    SpecificationAnnotation,
    SpecificationAssumption,
    SpecificationDimension,
    SpecificationFeature,
    SpecificationQuestion,
)
from pydantic import ValidationError as PydanticValidationError
from app.draft_builder import DraftBuilder
from app.specification import (
    SpecificationValidationError,
    apply_specification_edits,
    project_from_specification,
    validate_specification,
)
from app.runner import run_project
from tests.provider_payloads import normalize_draft_specification_payload


def complete_specification() -> DraftSpecification:
    return DraftSpecification(
        dimensions=[
            SpecificationDimension(id="length", label="Length", value=40, status="confirmed"),
            SpecificationDimension(id="width", label="Width", value=30, status="confirmed"),
            SpecificationDimension(id="height", label="Height", value=5, status="confirmed"),
        ],
        features=[
            SpecificationFeature(
                id="base",
                label="Base",
                type="box",
                operation="add",
                parameters={"length": "length", "width": "width", "height": "height"},
                critical_fields=["length", "width", "height"],
                status="confirmed",
            )
        ],
    )


class SpecificationTests(unittest.TestCase):
    def test_tool_call_log_summary_shows_name_and_compact_arguments(self):
        summary = ai._response_log_summary(
            "",
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {"function": {"name": "add_dimension", "arguments": '{"id":"width","value":60,"unit":"mm"}'}},
                                {"function": {"name": "finish_draft", "arguments": "{}"}},
                            ]
                        }
                    }
                ]
            },
        )
        self.assertEqual(summary, "tools=[add_dimension(id='width', value=60, unit='mm'); finish_draft()]")

    def test_complete_specification_resolves_values(self):
        values = validate_specification(complete_specification())
        self.assertEqual(values["length"], 40)

    def test_nonempty_feature_placement_satisfies_a_generic_placement_requirement(self):
        specification = complete_specification()
        specification.features[0].placement = {"origin": [0, 0, 0], "plane": "XY"}
        specification.features[0].critical_fields.append("placement")
        validate_specification(specification)

    def test_unknown_placement_field_blocks_validation_before_build(self):
        specification = complete_specification()
        with self.assertRaisesRegex(PydanticValidationError, "offset"):
            SpecificationFeature.model_validate({**specification.features[0].model_dump(), "placement": {"reference": "base", "offset": [0, 0, 20]}})

    def test_draft_planner_schema_forbids_unknown_placement_fields(self):
        placement_schema = DraftSpecification.model_json_schema()["$defs"]["FeaturePlacement"]
        self.assertFalse(placement_schema["additionalProperties"])
        self.assertNotIn("offset", placement_schema["properties"])

    def test_draft_tool_schema_has_a_strict_variant_for_every_compiler_contract(self):
        schema = ai.submit_draft_specification_tool_schema()["properties"]["specification"]
        variants = schema["$defs"]["SpecificationFeature"]["oneOf"]
        by_type = {variant["properties"]["type"]["const"]: variant for variant in variants}
        self.assertEqual(set(by_type), set(OPERATION_CONTRACTS))
        for feature_type, contract in OPERATION_CONTRACTS.items():
            with self.subTest(feature_type=feature_type):
                variant = by_type[feature_type]
                self.assertFalse(variant["additionalProperties"])
                self.assertEqual(variant["properties"]["operation"]["enum"], list(contract.allowed_operations))
                parameters = variant["properties"]["parameters"]
                self.assertFalse(parameters["additionalProperties"])
                self.assertEqual(parameters["required"], list(contract.required_parameters))
                self.assertEqual(set(parameters["properties"]), set(contract.parameter_names))
                self.assertEqual("profile" in variant["required"], contract.requires_profile)
                self.assertEqual("pattern" in variant["required"], contract.requires_pattern)
                if contract.requires_profile:
                    self.assertIn("REQUIRED", variant["properties"]["profile"]["description"])

    def test_contract_validation_rejects_missing_profile_and_unknown_parameter(self):
        specification = complete_specification()
        specification.features.append(
            SpecificationFeature(
                id="raised_logo",
                label="Raised logo",
                type="extrude",
                operation="add",
                target="base",
                parameters={"distance": 2, "depth_mm": 2},
                status="confirmed",
            )
        )
        with self.assertRaisesRegex(SpecificationValidationError, "unsupported parameter"):
            validate_specification(specification)

        specification.features[-1].parameters = {"distance": 2}
        with self.assertRaisesRegex(SpecificationValidationError, "requires a profile"):
            validate_specification(specification)

    def test_profile_critical_field_is_satisfied_by_structured_profile(self):
        specification = complete_specification()
        specification.features.append(
            SpecificationFeature(
                id="hex_head",
                label="Hex head",
                type="extrude",
                operation="add",
                target="base",
                parameters={"distance": 12},
                profile={"type": "polyline", "dimensions": {}, "points": [[1, 0], [0, 1], [-1, 0]]},
                critical_fields=["parameters.distance", "profile.points"],
                status="confirmed",
            )
        )
        validate_specification(specification)

    def test_profile_and_pattern_are_preserved_in_the_trusted_graph(self):
        specification = DraftSpecification(
            dimensions=[
                SpecificationDimension(id="length", label="Length", value=40, status="confirmed"),
                SpecificationDimension(id="width", label="Width", value=30, status="confirmed"),
                SpecificationDimension(id="height", label="Height", value=5, status="confirmed"),
                SpecificationDimension(id="hole_diameter", label="Hole diameter", value=4, status="confirmed"),
                SpecificationDimension(id="hole_depth", label="Hole depth", value=8, status="confirmed"),
                SpecificationDimension(id="count", label="Count", value=3, status="confirmed"),
                SpecificationDimension(id="pitch", label="Pitch", value=10, status="confirmed"),
            ],
            features=[
                SpecificationFeature(id="base", label="Base", type="box", operation="add", parameters={"length": "length", "width": "width", "height": "height"}, status="confirmed"),
                SpecificationFeature(
                    id="holes", label="Holes", type="hole_pattern", operation="pattern", target="base",
                    parameters={"depth": "hole_depth"}, profile={"type": "circle", "dimensions": {"diameter": "hole_diameter"}},
                    pattern={"type": "linear", "count": "count", "pitch": "pitch", "axis": "X"},
                    placement={"origin": [5, 15, -1]}, status="confirmed",
                ),
            ],
        )
        project = project_from_specification(specification)
        holes = project.feature_graph.operations[1]
        self.assertEqual(holes.profile.type, "circle")
        self.assertEqual(holes.pattern.type, "linear")

    def test_missing_critical_dimension_blocks_build(self):
        specification = complete_specification()
        specification.dimensions[0].status = "needs_input"
        with self.assertRaisesRegex(SpecificationValidationError, "length requires input"):
            validate_specification(specification)

    def test_empty_feature_graph_blocks_build(self):
        with self.assertRaisesRegex(SpecificationValidationError, "at least one supported feature"):
            validate_specification(DraftSpecification())

    def test_assumption_requires_explicit_acceptance(self):
        specification = complete_specification()
        specification.assumptions = [SpecificationAssumption(id="wall_guess", value=3, rationale="not shown")]
        with self.assertRaisesRegex(SpecificationValidationError, "wall_guess"):
            validate_specification(specification)
        accepted = apply_specification_edits(specification, {}, ["wall_guess"], "")
        validate_specification(accepted)

    def test_unknown_acceptance_ids_are_rejected(self):
        specification = complete_specification()
        with self.assertRaisesRegex(SpecificationValidationError, "Unknown assumption 'missing'"):
            apply_specification_edits(specification, {}, ["missing"], "")
        with self.assertRaisesRegex(SpecificationValidationError, "Unknown feature 'missing'"):
            apply_specification_edits(specification, {}, [], "", accepted_feature_ids=["missing"])

    def test_draft_builder_requires_metadata_and_cross_namespace_ids(self):
        builder = DraftBuilder({})
        self.assertTrue(builder.add_dimension({"id": "base", "label": "Length", "value": 40})["ok"])
        result = builder.add_feature(
            {"id": "base", "label": "Base", "type": "box", "operation": "add", "parameters": {"length": 1, "width": 1, "height": 1}}
        )
        self.assertFalse(result["ok"])
        self.assertIn("duplicate", result["message"])
        with self.assertRaisesRegex(ValueError, "set_draft_metadata"):
            builder.finish()

        self.assertFalse(builder.set_metadata({"title": "Plate", "units": "inch"})["ok"])
        self.assertFalse(builder.set_metadata({"title": "Plate", "units": "mm", "extra": True})["ok"])
        self.assertTrue(builder.set_metadata({"title": "Plate", "units": "mm"})["ok"])
        self.assertFalse(builder.set_metadata({"title": "Renamed plate", "units": "mm"})["ok"])

    def test_text_feature_accepts_text_dimension_and_signed_cut_distance(self):
        specification = DraftSpecification(
            dimensions=[
                SpecificationDimension(id="length", label="Length", value=40, status="confirmed"),
                SpecificationDimension(id="width", label="Width", value=30, status="confirmed"),
                SpecificationDimension(id="height", label="Height", value=5, status="confirmed"),
                SpecificationDimension(id="content", label="Content", value="EASY", status="confirmed"),
                SpecificationDimension(id="size", label="Size", value=8, status="confirmed"),
                SpecificationDimension(id="depth", label="Depth", value=-1, status="confirmed"),
                SpecificationDimension(id="text_x", label="Text X", value=10, status="confirmed"),
                SpecificationDimension(id="text_y", label="Text Y", value=10, status="confirmed"),
                SpecificationDimension(id="text_z", label="Text Z", value=5, status="confirmed"),
            ],
            features=[
                SpecificationFeature(
                    id="base", label="Base", type="box", operation="add",
                    parameters={"length": "length", "width": "width", "height": "height"}, status="confirmed",
                ),
                SpecificationFeature(
                    id="label", label="Label", type="text", operation="cut", target="base",
                    parameters={"content": "content", "size": "size", "distance": "depth"}, status="confirmed",
                    placement={"origin": ["text_x", "text_y", "text_z"]},
                )
            ],
        )
        values = validate_specification(specification)
        self.assertEqual(values["depth"], -1)
        project = project_from_specification(specification)
        self.assertEqual(project.parameters["content"].type, "text")
        self.assertIn("# feature:label", project.cad.source)
        self.assertEqual(run_project(project, {}, fmt="stl")["status"], "success")

    def test_text_dimension_is_rejected_in_numeric_feature_field(self):
        specification = complete_specification()
        specification.dimensions[0].value = "forty"
        with self.assertRaisesRegex(SpecificationValidationError, "text dimension 'length' where a numeric value is required"):
            validate_specification(specification)

    def test_units_and_review_references_are_validated(self):
        with self.assertRaises(PydanticValidationError):
            DraftSpecification(units="inch")
        specification = complete_specification()
        specification.questions = [SpecificationQuestion(id="missing", field_id="unknown", prompt="Enter value")]
        specification.annotations = [SpecificationAnnotation(id="marker", field_id="unknown", x=0.5, y=0.5, label="Unknown")]
        with self.assertRaisesRegex(SpecificationValidationError, "unknown review item"):
            validate_specification(specification)

    def test_unknown_reference_and_unsupported_feature_are_reported(self):
        specification = complete_specification()
        specification.features.append(
            SpecificationFeature(
                id="bad_cut",
                label="Bad cut",
                type="freeform",
                operation="cut",
                target="missing",
                status="confirmed",
            )
        )
        with self.assertRaisesRegex(SpecificationValidationError, "unsupported operation"):
            validate_specification(specification)

    def test_second_additive_feature_requires_existing_body_target(self):
        specification = complete_specification()
        specification.features.append(SpecificationFeature(id="extra", label="Extra", type="box", operation="add", status="confirmed"))
        with self.assertRaisesRegex(SpecificationValidationError, "must target the existing body"):
            validate_specification(specification)

    def test_conflict_and_cyclic_expression_block_build(self):
        specification = complete_specification()
        specification.dimensions[0].status = "conflicted"
        specification.dimensions.extend(
            [
                SpecificationDimension(id="a", label="A", expression="b + 1", status="confirmed"),
                SpecificationDimension(id="b", label="B", expression="a + 1", status="confirmed"),
            ]
        )
        with self.assertRaisesRegex(SpecificationValidationError, "Could not resolve derived dimensions"):
            validate_specification(specification)

    def test_draft_planner_forbids_cad_and_returns_questions(self):
        response = {
            "title": "Plate",
            "dimensions": [{"id": "length", "label": "Length", "value": 40, "status": "confirmed", "critical": True}],
            "features": [],
            "assumptions": [],
            "questions": [{"id": "width_question", "field_id": "width", "prompt": "Enter width"}],
            "annotations": [],
        }
        with patch.object(ai, "_run_draft_builder", AsyncMock(return_value=DraftSpecification.model_validate(response))) as builder:
            draft = asyncio.run(ai.plan_draft_specification({"features": []}, "", "key"))
        self.assertEqual(draft.questions[0].field_id, "width")
        prompt = builder.await_args.args[2]["messages"][0]["content"]
        self.assertIn("Do not return CAD code", prompt)
        self.assertIn("draft-compatible compiler types", prompt)
        self.assertIn("body and groove are observations", prompt)
        self.assertIn("Geometry interpretation rules", prompt)
        self.assertIn("A workplane is the plane of the feature profile", prompt)
        self.assertIn("circular features are concentric", prompt)
        self.assertIn("same complete placement origin", prompt)
        self.assertIn("needs_input or an assumed proposal with a question", prompt)
        self.assertIn("through/сквозное is a confirmed instruction", prompt)
        self.assertIn("never use offset, center, position, depth, or centered_on_width", prompt)
        self.assertNotIn("L-bracket drawing", prompt)
        self.assertNotIn("hex-head bolt", prompt)
        self.assertNotIn("groove_center_y", prompt)

    def test_draft_planner_preserves_the_complete_vision_analysis_for_replanning(self):
        response = {"title": "Plate", "dimensions": [], "features": [], "assumptions": [], "questions": [], "annotations": []}
        analysis = {"views": [{"id": "front"}], "dimensions": [{"id": "length", "value": 40}], "features": [], "uncertainties": [{"id": "depth"}]}
        with patch.object(ai, "_run_draft_builder", AsyncMock(return_value=DraftSpecification.model_validate(response))) as builder:
            draft = asyncio.run(ai.plan_draft_specification(analysis, "", "key"))
        self.assertEqual(builder.await_args.args[3], analysis)
        self.assertEqual(draft.title, "Plate")

    def test_draft_planner_does_not_restart_after_the_provider_turn_limit(self):
        with patch.object(
            ai,
            "_run_draft_builder",
            AsyncMock(side_effect=ai.GenerationError("draft_specification", "Planner exceeded tool-call limit")),
        ) as builder:
            with self.assertRaisesRegex(ai.GenerationError, "tool-call limit"):
                asyncio.run(ai.plan_draft_specification({"features": []}, "", "key"))

        builder.assert_awaited_once()
        self.assertRegex(builder.await_args.kwargs["planner_run_id"], r"^[0-9a-f]{12}$")
        self.assertEqual(builder.await_args.kwargs["planner_mode"], "initial")

    def test_draft_builder_stops_after_two_provider_turns(self):
        def response(name, arguments):
            return type("Response", (), {
                "status_code": 200,
                "text": "",
                "json": lambda self: {"choices": [{"message": {"tool_calls": [{"id": name, "function": {"name": name, "arguments": json.dumps(arguments)}}]}}]},
            })()

        class Client:
            responses = [
                response("set_draft_metadata", {"title": "Plate", "units": "mm"}),
                response("add_box", {"id": "base", "label": "Base", "type": "box", "operation": "add", "target": None, "parameters": {"length": 1, "width": 1, "height": 1}, "placement": {"plane": "XY", "origin": [0, 0, 0]}, "status": "confirmed", "critical_fields": [], "confidence": 1, "evidence": [], "alternatives": {}}),
                response("finish_draft", {}),
            ]
            posts = 0

            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
            async def post(self, *args, **kwargs):
                type(self).posts += 1
                return type(self).responses.pop(0)

        with patch.object(ai.httpx, "AsyncClient", lambda **kwargs: Client()):
            with self.assertRaisesRegex(ai.GenerationError, "2-turn limit") as error:
                asyncio.run(ai._run_draft_builder("url", "key", {"messages": [], "model": "test"}, {"features": []}, planner_run_id="run123", planner_mode="initial"))
        self.assertEqual(Client.posts, 2)
        self.assertEqual(error.exception.detail["max_provider_turns"], 2)
        self.assertEqual(error.exception.detail["planner_run_id"], "run123")

    def test_draft_planner_does_not_allow_provider_analysis_to_replace_vision_analysis(self):
        response = {
            "title": "Plate",
            "analysis": {"dimensions": {"wrong_shape": 40}},
            "dimensions": [], "features": [], "assumptions": [], "questions": [], "annotations": [],
        }
        analysis = {"views": [], "dimensions": [{"id": "length", "value": 40}], "features": [], "uncertainties": []}
        expected = DraftSpecification.model_validate({**response, "analysis": analysis})
        with patch.object(ai, "_run_draft_builder", AsyncMock(return_value=expected)):
            draft = asyncio.run(ai.plan_draft_specification(analysis, "", "key"))
        self.assertEqual(draft.analysis.model_dump(mode="json"), analysis)

    def test_complete_replan_request_constrains_feature_types_and_carries_all_context(self):
        response = {"title": "Plate", "dimensions": [], "features": [], "assumptions": [], "questions": [], "annotations": []}
        analysis = {"views": [{"id": "front"}], "dimensions": [{"id": "length", "value": 40}], "features": [{"id": "base", "type": "body"}], "uncertainties": []}
        previous = complete_specification()
        previous.assumptions = [SpecificationAssumption(id="wall_guess", value=3, rationale="not shown")]
        user_inputs = {
            "dimension_values": {"length": 42},
            "accepted_feature_ids": ["base"],
            "accepted_assumption_ids": [],
            "clarifications": {"width_question": "The width is 30 mm."},
        }
        with patch.object(ai, "_run_draft_builder", AsyncMock(return_value=complete_specification())) as builder:
            asyncio.run(
                ai.plan_draft_specification(
                    analysis,
                    "",
                    "key",
                    previous_specification=previous,
                    user_inputs=user_inputs,
                )
            )

        payload = builder.await_args.args[2]
        prompt = payload["messages"][0]["content"]
        request_context = json.loads(payload["messages"][1]["content"])
        self.assertIn(f"draft-compatible compiler types: {draft_specification_operation_types()}", prompt)
        self.assertIn("body and groove are observations, not valid feature types", prompt)
        self.assertIn("Return a complete replacement DraftSpecification, not a patch", prompt)
        self.assertIn("never delete an existing item or return an empty graph", prompt)
        self.assertIn("accepted_assumption_ids and accepted_feature_ids are explicit user approvals", prompt)
        self.assertIn("accepted assumption is an authoritative answer", prompt)
        self.assertIn("accepted feature is an authoritative approval", prompt)
        self.assertIn("clarification linked to a question overrides an earlier proposal", prompt)
        self.assertIn("Do not return a question that is answered", prompt)
        self.assertIn("Return every previous question that is still unresolved", prompt)
        self.assertEqual(request_context["drawing_analysis"], analysis)
        self.assertEqual(request_context["previous_specification"], previous.model_dump(mode="json"))
        self.assertEqual(request_context["user_inputs"], user_inputs)
        self.assertEqual(builder.await_args.kwargs["required_dimension_ids"], {item.id for item in previous.dimensions})
        self.assertEqual(builder.await_args.kwargs["required_feature_ids"], {item.id for item in previous.features})
        self.assertEqual(builder.await_args.kwargs["required_assumption_ids"], {item.id for item in previous.assumptions})
        self.assertEqual(builder.await_args.kwargs["preserved_dimensions"]["length"]["value"], 42)
        self.assertEqual(builder.await_args.kwargs["preserved_features"]["base"]["status"], "confirmed")
        tool_names = {tool["function"]["name"] for tool in payload["tools"]}
        self.assertIn("set_draft_metadata", tool_names)
        self.assertIn("add_box", tool_names)
        self.assertIn("finish_draft", tool_names)

    def test_draft_planner_normalizes_known_provider_field_variants(self):
        response = {
            "title": "Open Box",
            "units": "millimeters",
            "dimensions": [{"id": "height", "label": "Height", "value": 30, "status": "confirmed", "critical": True, "evidence": "front view"}],
            "features": [],
            "assumptions": [{"id": "cavity_depth", "description": "Cavity depth follows the bottom thickness."}],
            "questions": [{"id": "rim_side", "question": "Which side has the rim?", "related_features": ["rim"]}],
            "annotations": [{"id": "height_note", "x": 0.5, "y": 0.2, "text": "Height 30 mm", "links_to": "height"}],
        }
        draft = DraftSpecification.model_validate(normalize_draft_specification_payload(response))

        self.assertEqual(draft.units, "mm")
        self.assertEqual(draft.dimensions[0].evidence, ["front view"])
        self.assertEqual(draft.assumptions[0].rationale, "Cavity depth follows the bottom thickness.")
        self.assertEqual(draft.questions[0].field_id, "rim")
        self.assertEqual(draft.annotations[0].label, "Height 30 mm")

    def test_draft_normalizes_multi_link_annotations_and_provider_question_fields(self):
        response = {
            "title": "Bracket",
            "dimensions": [{"id": "width", "label": "Width", "value": 60, "status": "confirmed"}],
            "features": [{"id": "hole", "label": "Hole", "type": "hole", "operation": "cut", "status": "needs_input"}],
            "assumptions": [{"id": "hole_center", "description": "Hole is centered", "affects": ["hole"]}],
            "questions": [{"id": "hole_position", "description": "Where is the hole?", "required_for": ["hole"]}],
            "annotations": [{"id": "overview", "x": 0.5, "y": 0.5, "text": "Width and hole", "links_to": ["width", "hole"]}],
        }
        draft = DraftSpecification.model_validate(normalize_draft_specification_payload(response))

        self.assertEqual(draft.annotations[0].field_id, "width")
        self.assertEqual(draft.annotations[0].field_ids, ["width", "hole"])
        self.assertEqual(draft.questions[0].field_id, "hole")
        self.assertEqual(draft.questions[0].prompt, "Where is the hole?")
        self.assertEqual(draft.assumptions[0].affected_ids, ["hole"])

    def test_draft_normalizes_deepseek_strict_tool_variants(self):
        payload = {
            "title": "Open Box with Rim", "units": "millimeters",
            "dimensions": [{"id": "outer_length", "label": "Outer Length", "value": 80, "source": "front_section", "evidence": "Dimension line"}],
            "features": [
                {"id": "main_body", "label": "Main Body", "type": "extrude", "operation": "add", "parameters": {"length": 80}, "placement": {"origin": [0, 0, 0]}, "evidence": "Front view"},
                {"id": "outer_fillets", "label": "Outer Fillets", "type": "fillet", "operation": "modify", "target": "main_body", "parameters": {"radius": 5}, "placement": None, "evidence": "Bottom view"},
            ],
            "assumptions": [{"id": "assumption_1", "description": "Rim side is not explicit."}],
            "questions": [{"id": "q1", "question": "Which short side has the rim?", "related_feature": "main_body"}],
            "annotations": [{"id": "ann1", "x": 0.5, "y": 0.1, "text": "Outer length 80mm", "links_to": "outer_length"}],
        }
        draft = ai.DraftSpecification.model_validate(normalize_draft_specification_payload(payload))
        self.assertIsNone(draft.features[1].placement.origin)
        self.assertEqual(draft.dimensions[0].source, "drawing")
        self.assertEqual(draft.questions[0].field_id, "main_body")
        self.assertEqual(draft.annotations[0].label, "Outer length 80mm")

    def test_draft_normalizes_deepseek_parameters_wrapper(self):
        payload = {
            "parameters": {
                "title": "Plate",
                "dimensions": [],
                "features": [{"id": "base", "label": "Base", "type": "box", "operation": "add"}],
                "assumptions": [], "questions": [], "annotations": [],
            }
        }
        draft = DraftSpecification.model_validate(normalize_draft_specification_payload(payload))
        self.assertEqual(draft.features[0].id, "base")

    def test_draft_normalizes_deepseek_draft_specification_wrapper(self):
        payload = {"draft_specification": {"title": "Plate", "dimensions": [], "features": [{"id": "base", "label": "Base", "type": "box", "operation": "add"}], "assumptions": [], "questions": [], "annotations": []}}
        draft = DraftSpecification.model_validate(normalize_draft_specification_payload(payload))
        self.assertEqual(draft.features[0].id, "base")

    def test_confirmed_specification_compiles_to_trusted_feature_graph(self):
        project = project_from_specification(complete_specification())
        self.assertEqual(project.cad.source_kind, "compiled")
        self.assertIn("# feature:base", project.cad.source)


if __name__ == "__main__":
    unittest.main()
