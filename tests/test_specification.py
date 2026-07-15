from __future__ import annotations

import unittest
import asyncio
import json
from unittest.mock import AsyncMock, patch

import app.ai_generation as ai
from app.feature_compiler import planner_operation_types
from app.models import DraftSpecification, SpecificationAssumption, SpecificationDimension, SpecificationFeature
from pydantic import ValidationError as PydanticValidationError
from app.specification import (
    SpecificationValidationError,
    apply_specification_edits,
    project_from_specification,
    validate_specification,
)


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

    def test_missing_critical_dimension_blocks_build(self):
        specification = complete_specification()
        specification.dimensions[0].status = "needs_input"
        with self.assertRaisesRegex(SpecificationValidationError, "length requires input"):
            validate_specification(specification)

    def test_assumption_requires_explicit_acceptance(self):
        specification = complete_specification()
        specification.assumptions = [SpecificationAssumption(id="wall_guess", value=3, rationale="not shown")]
        with self.assertRaisesRegex(SpecificationValidationError, "wall_guess"):
            validate_specification(specification)
        accepted = apply_specification_edits(specification, {}, ["wall_guess"], "")
        validate_specification(accepted)

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
        with patch.object(ai, "_chat_json", AsyncMock(return_value=response)) as chat:
            draft = asyncio.run(ai.plan_draft_specification({"features": []}, "", "key"))
        self.assertEqual(draft.questions[0].field_id, "width")
        prompt = chat.await_args.args[2]["messages"][0]["content"]
        self.assertIn("Do not return CAD code", prompt)
        self.assertIn("trusted compiler types", prompt)
        self.assertIn("body and groove are observations", prompt)
        self.assertIn("never use offset, center, position, depth, or centered_on_width", prompt)

    def test_draft_planner_preserves_the_complete_vision_analysis_for_replanning(self):
        response = {"title": "Plate", "dimensions": [], "features": [], "assumptions": [], "questions": [], "annotations": []}
        analysis = {"views": [{"id": "front"}], "dimensions": [{"id": "length", "value": 40}], "features": [], "uncertainties": [{"id": "depth"}]}
        with patch.object(ai, "_chat_json", AsyncMock(return_value=response)):
            draft = asyncio.run(ai.plan_draft_specification(analysis, "", "key"))
        self.assertEqual(draft.analysis.model_dump(mode="json"), analysis)

    def test_draft_planner_does_not_allow_provider_analysis_to_replace_vision_analysis(self):
        response = {
            "title": "Plate",
            "analysis": {"dimensions": {"wrong_shape": 40}},
            "dimensions": [], "features": [], "assumptions": [], "questions": [], "annotations": [],
        }
        analysis = {"views": [], "dimensions": [{"id": "length", "value": 40}], "features": [], "uncertainties": []}
        with patch.object(ai, "_chat_json", AsyncMock(return_value=response)):
            draft = asyncio.run(ai.plan_draft_specification(analysis, "", "key"))
        self.assertEqual(draft.analysis.model_dump(mode="json"), analysis)

    def test_complete_replan_request_constrains_feature_types_and_carries_all_context(self):
        response = {"title": "Plate", "dimensions": [], "features": [], "assumptions": [], "questions": [], "annotations": []}
        analysis = {"views": [{"id": "front"}], "dimensions": [{"id": "length", "value": 40}], "features": [{"id": "base", "type": "body"}], "uncertainties": []}
        previous = complete_specification()
        user_inputs = {
            "dimension_values": {"length": 42},
            "accepted_feature_ids": ["base"],
            "accepted_assumption_ids": [],
            "clarifications": {"width_question": "The width is 30 mm."},
        }
        with patch.object(ai, "_chat_json", AsyncMock(return_value=response)) as chat:
            asyncio.run(
                ai.plan_draft_specification(
                    analysis,
                    "",
                    "key",
                    previous_specification=previous,
                    user_inputs=user_inputs,
                )
            )

        payload = chat.await_args.args[2]
        prompt = payload["messages"][0]["content"]
        request_context = json.loads(payload["messages"][1]["content"])
        self.assertIn(f"trusted compiler types: {planner_operation_types()}", prompt)
        self.assertIn("body and groove are observations, not valid feature types", prompt)
        self.assertIn("Return a complete replacement DraftSpecification, not a patch", prompt)
        self.assertIn("accepted_assumption_ids and accepted_feature_ids are explicit user approvals", prompt)
        self.assertIn("Do not return a question that is answered", prompt)
        self.assertEqual(request_context["drawing_analysis"], analysis)
        self.assertEqual(request_context["previous_specification"], previous.model_dump(mode="json"))
        self.assertEqual(request_context["user_inputs"], user_inputs)
        self.assertTrue(payload["tools"][0]["function"]["strict"])
        placement_schema = payload["tools"][0]["function"]["parameters"]["$defs"]["FeaturePlacement"]
        self.assertFalse(placement_schema["additionalProperties"])
        self.assertNotIn("offset", placement_schema["properties"])

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
        with patch.object(ai, "_chat_json", AsyncMock(return_value=response)):
            draft = asyncio.run(ai.plan_draft_specification({"features": []}, "", "key"))

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
        with patch.object(ai, "_chat_json", AsyncMock(return_value=response)):
            draft = asyncio.run(ai.plan_draft_specification({"features": []}, "", "key"))

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
        draft = ai.DraftSpecification.model_validate(ai.normalize_draft_specification_payload(payload))
        self.assertIsNone(draft.features[1].placement.origin)
        self.assertEqual(draft.dimensions[0].source, "drawing")
        self.assertEqual(draft.questions[0].field_id, "main_body")
        self.assertEqual(draft.annotations[0].label, "Outer length 80mm")

    def test_confirmed_specification_compiles_to_trusted_feature_graph(self):
        project = project_from_specification(complete_specification())
        self.assertEqual(project.cad.source_kind, "compiled")
        self.assertIn("# feature:base", project.cad.source)


if __name__ == "__main__":
    unittest.main()
