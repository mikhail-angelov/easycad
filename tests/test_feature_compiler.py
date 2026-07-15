from __future__ import annotations

import unittest
import io

import app.main as main
from PIL import Image
from app.feature_compiler import (
    COMPILER_OPERATION_TYPES,
    COMPILER_OPERATION_KINDS,
    CompilerError,
    canonical_operation_type,
    compiler_operation_types,
    compile_feature_graph,
    compile_project_feature_graph,
    pattern_instance_offsets,
    planner_operation_types,
)
from app.models import CADProject, CADSource, FeatureGraph
from app.runner import RunnerError, concrete_parameters, run_project
from app.validator import validate_source


class FeatureCompilerTests(unittest.TestCase):
    def project(self, graph_payload, parameters):
        graph = FeatureGraph.model_validate({"operations": graph_payload})
        source = compile_feature_graph(graph, parameters)
        validate_source(source)
        return CADProject(
            title="Compiled fixture",
            parameters={
                key: {
                    "label": key,
                    "value": value,
                    "type": "text" if isinstance(value, str) else "number",
                    "unit": "" if isinstance(value, str) else "mm",
                }
                for key, value in parameters.items()
            },
            feature_graph=graph,
            cad=CADSource(source=source),
        )

    def test_compiles_additive_and_subtractive_extrusions(self):
        project = self.project(
            [
                {
                    "id": "base",
                    "type": "box",
                    "operation": "add",
                    "parameters": {"length": "length", "width": "width", "height": "height"},
                },
                {
                    "id": "top_block",
                    "type": "extrude",
                    "operation": "add",
                    "target": "base",
                    "profile": {
                        "type": "rectangle",
                        "dimensions": {"width": "block_length", "height": "block_width"},
                    },
                    "parameters": {"distance": "block_height"},
                    "placement": {"origin": ["block_x", "block_y", "height"]},
                },
                {
                    "id": "top_pocket",
                    "type": "extrude",
                    "operation": "cut",
                    "target": "base",
                    "profile": {
                        "type": "rectangle",
                        "dimensions": {"width": "pocket_length", "height": "pocket_width"},
                    },
                    "parameters": {"distance": "pocket_depth"},
                    "placement": {"origin": ["pocket_x", "pocket_y", "height"]},
                },
            ],
            {
                "length": 60,
                "width": 40,
                "height": 10,
                "block_length": 20,
                "block_width": 12,
                "block_height": 10,
                "block_x": 20,
                "block_y": 14,
                "pocket_length": 8,
                "pocket_width": 6,
                "pocket_depth": -5,
                "pocket_x": 26,
                "pocket_y": 17,
            },
        )

        result = run_project(project, {}, fmt="stl")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["bounding_box"], {"x": 60.0, "y": 40.0, "z": 20.0})
        self.assertLess(result["volume_mm3"], 60 * 40 * 10 + 20 * 12 * 10)

    def test_compiles_cylinder_from_planner_catalog(self):
        self.assertEqual(COMPILER_OPERATION_KINDS, tuple(COMPILER_OPERATION_TYPES))
        self.assertIn("cylinder", planner_operation_types())
        self.assertEqual(canonical_operation_type("rectangular_body"), "box")
        self.assertEqual(canonical_operation_type("counterbore_hole"), "counterbore")
        self.assertIn("slot2d", COMPILER_OPERATION_TYPES["slot"])
        self.assertIn("rectangular_body", compiler_operation_types())
        project = self.project(
            [
                {
                    "id": "round_base",
                    "type": "cylinder",
                    "operation": "add",
                    "parameters": {"radius": "radius", "height": "height"},
                }
            ],
            {"radius": 12, "height": 20},
        )

        result = run_project(project, {}, fmt="stl")

        self.assertEqual(result["status"], "success")
        self.assertAlmostEqual(result["bounding_box"]["x"], 24.0)
        self.assertAlmostEqual(result["bounding_box"]["y"], 24.0)
        self.assertAlmostEqual(result["bounding_box"]["z"], 20.0)

    def test_runner_recompiles_graph_and_ignores_request_source(self):
        project = self.project(
            [
                {
                    "id": "base",
                    "type": "box",
                    "operation": "add",
                    "parameters": {"length": "length", "width": "width", "height": "height"},
                }
            ],
            {"length": 20, "width": 10, "height": 5},
        )
        project.cad.source = "import os\nresult = os.system('should-not-run')\n"

        result = run_project(project, {}, fmt="stl")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["bounding_box"], {"x": 20.0, "y": 10.0, "z": 5.0})

    def test_compiles_revolved_polyline_profile(self):
        project = self.project(
            [
                {
                    "id": "shaft",
                    "type": "revolve",
                    "operation": "add",
                    "profile": {
                        "type": "polyline",
                        "points": [[0, 0], ["radius", 0], ["radius", "length"], [0, "length"]],
                    },
                    "parameters": {"angle_deg": 360},
                    "placement": {"plane": "XZ"},
                }
            ],
            {"radius": 8, "length": 30},
        )

        result = run_project(project, {}, fmt="step")
        self.assertEqual(result["status"], "success")
        self.assertAlmostEqual(result["bounding_box"]["x"], 16.0)
        self.assertAlmostEqual(result["bounding_box"]["y"], 16.0)
        self.assertAlmostEqual(result["bounding_box"]["z"], 30.0)

    def test_compiles_holes_counterbores_countersinks_slots_and_pockets(self):
        project = self.project(
            [
                {
                    "id": "plate",
                    "type": "box",
                    "operation": "add",
                    "parameters": {"length": "plate_length", "width": "plate_width", "height": "plate_height"},
                },
                {
                    "id": "hole",
                    "type": "hole",
                    "operation": "cut",
                    "target": "plate",
                    "parameters": {"diameter": "hole_diameter", "depth": "cut_depth"},
                    "placement": {"origin": [10, 10, "cut_start"]},
                },
                {
                    "id": "through_hole",
                    "type": "through_hole",
                    "operation": "cut",
                    "target": "plate",
                    "parameters": {"diameter": "hole_diameter", "depth": "cut_depth"},
                    "placement": {"origin": [20, 10, "cut_start"]},
                },
                {
                    "id": "counterbore",
                    "type": "counterbore",
                    "operation": "cut",
                    "target": "plate",
                    "parameters": {
                        "diameter": "hole_diameter",
                        "depth": "cut_depth",
                        "bore_diameter": "bore_diameter",
                        "bore_depth": "bore_depth",
                    },
                    "placement": {"origin": [35, 10, "cut_start"]},
                },
                {
                    "id": "countersink",
                    "type": "countersink",
                    "operation": "cut",
                    "target": "plate",
                    "parameters": {
                        "diameter": "hole_diameter",
                        "depth": "cut_depth",
                        "sink_diameter": "sink_diameter",
                        "sink_depth": "sink_depth",
                    },
                    "placement": {"origin": [55, 10, "cut_start"]},
                },
                {
                    "id": "slot",
                    "type": "slot",
                    "operation": "cut",
                    "target": "plate",
                    "parameters": {"length": "slot_length", "width": "slot_width", "depth": "cut_depth"},
                    "placement": {"origin": [20, 30, "cut_start"]},
                },
                {
                    "id": "pocket",
                    "type": "pocket",
                    "operation": "cut",
                    "target": "plate",
                    "parameters": {"length": "pocket_length", "width": "pocket_width", "depth": "pocket_depth"},
                    "placement": {"origin": [45, 30, "plate_height"]},
                },
            ],
            {
                "plate_length": 70,
                "plate_width": 45,
                "plate_height": 8,
                "hole_diameter": 5,
                "cut_depth": 10,
                "cut_start": -1,
                "bore_diameter": 9,
                "bore_depth": 3,
                "sink_diameter": 10,
                "sink_depth": 3,
                "slot_length": 18,
                "slot_width": 5,
                "pocket_length": 12,
                "pocket_width": 8,
                "pocket_depth": -3,
            },
        )

        result = run_project(project, {}, fmt="stl")
        self.assertEqual(result["status"], "success")
        self.assertAlmostEqual(result["bounding_box"]["x"], 70.0, places=5)
        self.assertAlmostEqual(result["bounding_box"]["y"], 45.0, places=5)
        self.assertAlmostEqual(result["bounding_box"]["z"], 8.0, places=5)
        self.assertLess(result["volume_mm3"], 70 * 45 * 8 - 500)
        measurements = result["feature_measurements"]
        self.assertLess(measurements["counterbore"]["volume_delta_mm3"], 0)
        self.assertGreater(measurements["counterbore"]["cylindrical_faces_delta"], 0)
        self.assertLess(measurements["countersink"]["volume_delta_mm3"], 0)

    def test_compiles_ribs_and_gussets_on_a_host_body(self):
        project = self.project(
            [
                {
                    "id": "base",
                    "type": "box",
                    "operation": "add",
                    "parameters": {"length": "base_length", "width": "base_width", "height": "base_height"},
                },
                {
                    "id": "rib",
                    "type": "rib",
                    "operation": "add",
                    "target": "base",
                    "parameters": {"length": "rib_length", "thickness": "rib_thickness", "height": "rib_height"},
                    "placement": {"origin": ["rib_x", "rib_y", "rib_z"]},
                },
                {
                    "id": "gusset",
                    "type": "gusset",
                    "operation": "add",
                    "target": "base",
                    "profile": {
                        "type": "polyline",
                        "points": [[0, 0], ["gusset_length", 0], [0, "gusset_height"]],
                    },
                    "parameters": {"width": "gusset_width"},
                    "placement": {"plane": "XZ", "origin": ["gusset_x", "gusset_y", "gusset_z"]},
                },
            ],
            {
                "base_length": 60,
                "base_width": 40,
                "base_height": 6,
                "rib_length": 45,
                "rib_thickness": 4,
                "rib_height": 15,
                "rib_x": 8,
                "rib_y": 18,
                "rib_z": 5.5,
                "gusset_length": 12,
                "gusset_height": 12,
                "gusset_width": 5,
                "gusset_x": 20,
                "gusset_y": 10,
                "gusset_z": 5.5,
            },
        )

        result = run_project(project, {}, fmt="step")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["solid_count"], 1)
        self.assertGreater(result["volume_mm3"], 60 * 40 * 6 + 2000)
        self.assertGreater(result["bounding_box"]["z"], 20)

    def test_compiles_fillets_chamfers_shells_mirrors_and_planar_text(self):
        shell_project = self.project(
            [
                {
                    "id": "box",
                    "type": "box",
                    "operation": "add",
                    "parameters": {"length": "length", "width": "width", "height": "height"},
                },
                {
                    "id": "open_shell",
                    "type": "shell",
                    "operation": "modify",
                    "target": "box",
                    "parameters": {"thickness": "shell_thickness"},
                    "placement": {"reference": ">Z"},
                },
                {
                    "id": "rim_chamfer",
                    "type": "chamfer",
                    "operation": "modify",
                    "target": "open_shell",
                    "parameters": {"distance": "chamfer"},
                    "placement": {"reference": ">Z"},
                },
            ],
            {"length": 50, "width": 35, "height": 20, "shell_thickness": -2, "chamfer": 0.5},
        )
        shell_result = run_project(shell_project, {}, fmt="stl")
        self.assertEqual(shell_result["status"], "success")
        self.assertLess(shell_result["volume_mm3"], 50 * 35 * 20 / 2)
        self.assertLess(shell_result["feature_measurements"]["open_shell"]["volume_delta_mm3"], 0)
        self.assertLess(shell_result["feature_measurements"]["rim_chamfer"]["volume_delta_mm3"], 0)

        mirrored_project = self.project(
            [
                {
                    "id": "rounded_block",
                    "type": "box",
                    "operation": "add",
                    "parameters": {"length": "block_length", "width": "block_width", "height": "block_height"},
                },
                {
                    "id": "edge_fillet",
                    "type": "fillet",
                    "operation": "modify",
                    "target": "rounded_block",
                    "parameters": {"radius": "fillet_radius"},
                    "placement": {"reference": "|Z"},
                },
                {
                    "id": "mirrored",
                    "type": "mirror",
                    "operation": "modify",
                    "target": "edge_fillet",
                    "placement": {"plane": "YZ"},
                },
            ],
            {"block_length": 20, "block_width": 10, "block_height": 6, "fillet_radius": 1},
        )
        mirrored_result = run_project(mirrored_project, {}, fmt="step")
        self.assertEqual(mirrored_result["status"], "success")
        self.assertAlmostEqual(mirrored_result["bounding_box"]["x"], 40.0)
        self.assertLess(mirrored_result["feature_measurements"]["edge_fillet"]["volume_delta_mm3"], 0)
        self.assertGreater(mirrored_result["feature_measurements"]["mirrored"]["volume_delta_mm3"], 0)

        text_project = self.project(
            [
                {
                    "id": "plate",
                    "type": "box",
                    "operation": "add",
                    "parameters": {"length": "plate_length", "width": "plate_width", "height": "plate_height"},
                },
                {
                    "id": "label",
                    "type": "text",
                    "operation": "cut",
                    "target": "plate",
                    "parameters": {"content": "text_content", "size": "text_size", "distance": "text_depth"},
                    "placement": {"origin": ["text_x", "text_y", "plate_height"]},
                },
            ],
            {
                "plate_length": 50,
                "plate_width": 30,
                "plate_height": 8,
                "text_content": "TEST",
                "text_size": 8,
                "text_depth": -1,
                "text_x": 25,
                "text_y": 15,
            },
        )
        text_result = run_project(text_project, {}, fmt="stl")
        self.assertEqual(text_result["status"], "success")
        self.assertLess(text_result["volume_mm3"], 50 * 30 * 8)
        self.assertLess(text_result["feature_measurements"]["label"]["volume_delta_mm3"], 0)

    def test_compiles_linear_and_polar_patterns(self):
        project = self.project(
            [
                {
                    "id": "plate",
                    "type": "box",
                    "operation": "add",
                    "parameters": {"length": "plate_length", "width": "plate_width", "height": "plate_height"},
                },
                {
                    "id": "linear_holes",
                    "type": "hole_pattern",
                    "operation": "pattern",
                    "target": "plate",
                    "profile": {"type": "circle", "dimensions": {"diameter": "hole_diameter"}},
                    "parameters": {"depth": "cut_depth"},
                    "pattern": {"type": "linear", "count": "linear_count", "pitch": "linear_pitch", "axis": "X"},
                    "placement": {"origin": ["linear_x", "linear_y", "cut_start"]},
                },
                {
                    "id": "polar_holes",
                    "type": "perforation_pattern",
                    "operation": "pattern",
                    "target": "plate",
                    "profile": {"type": "circle", "dimensions": {"diameter": "hole_diameter"}},
                    "parameters": {"depth": "cut_depth", "radius": "polar_radius", "start_angle_deg": 0},
                    "pattern": {"type": "polar", "count": "polar_count", "angle_deg": 360, "axis": "Z"},
                    "placement": {"origin": ["polar_x", "polar_y", "cut_start"]},
                },
            ],
            {
                "plate_length": 90,
                "plate_width": 50,
                "plate_height": 8,
                "hole_diameter": 4,
                "cut_depth": 10,
                "cut_start": -1,
                "linear_count": 5,
                "linear_pitch": 12,
                "linear_x": 8,
                "linear_y": 12,
                "polar_count": 6,
                "polar_radius": 10,
                "polar_x": 65,
                "polar_y": 32,
            },
        )

        result = run_project(project, {}, fmt="stl")
        self.assertEqual(result["status"], "success")
        expected_removed = 11 * 3.14159 * 2 * 2 * 8
        self.assertAlmostEqual(90 * 50 * 8 - result["volume_mm3"], expected_removed, delta=5)

    def test_compiles_perforation_patterns_on_declared_rib_faces(self):
        project = self.project(
            [
                {
                    "id": "base",
                    "type": "box",
                    "operation": "add",
                    "parameters": {"length": "body_length", "width": "body_width", "height": "bottom_thickness"},
                },
                {
                    "id": "rib_1",
                    "type": "rib",
                    "operation": "add",
                    "target": "base",
                    "parameters": {"length": "rib_length", "thickness": "rib_thickness", "height": "rib_height"},
                    "placement": {"origin": ["rib_x", "rib_1_y", "rib_z"]},
                },
                {
                    "id": "rib_2",
                    "type": "rib",
                    "operation": "add",
                    "target": "base",
                    "parameters": {"length": "rib_length", "thickness": "rib_thickness", "height": "rib_height"},
                    "placement": {"origin": ["rib_x", "rib_2_y", "rib_z"]},
                },
                {
                    "id": "rib_1_perforation",
                    "type": "perforation_pattern",
                    "operation": "pattern",
                    "target": "rib_1",
                    "profile": {"type": "circle", "dimensions": {"diameter": "hole_diameter"}},
                    "parameters": {"depth": "rib_cut_depth"},
                    "pattern": {"type": "linear", "count": "hole_count", "pitch": "hole_pitch", "axis": "X", "start_margin": "hole_margin"},
                    "placement": {"plane": "XZ", "reference": "rib_1_outer_face", "origin": ["hole_start_x", "rib_1_cut_y", "hole_z"]},
                },
                {
                    "id": "rib_2_perforation",
                    "type": "perforation_pattern",
                    "operation": "pattern",
                    "target": "rib_2",
                    "profile": {"type": "circle", "dimensions": {"diameter": "hole_diameter"}},
                    "parameters": {"depth": "rib_cut_depth"},
                    "pattern": {"type": "linear", "count": "hole_count", "pitch": "hole_pitch", "axis": "X", "start_margin": "hole_margin"},
                    "placement": {"plane": "XZ", "reference": "rib_2_outer_face", "origin": ["hole_start_x", "rib_2_cut_y", "hole_z"]},
                },
            ],
            {
                "body_length": 100,
                "body_width": 50,
                "bottom_thickness": 6,
                "rib_length": 88,
                "rib_thickness": 4,
                "rib_height": 20,
                "rib_x": 6,
                "rib_1_y": 14,
                "rib_2_y": 32,
                "rib_z": 5.5,
                "hole_diameter": 6,
                "hole_count": 5,
                "hole_pitch": 18,
                "hole_margin": 8,
                "hole_start_x": 6,
                "hole_z": 16,
                "rib_cut_depth": 6,
                "rib_1_cut_y": 20,
                "rib_2_cut_y": 38,
            },
        )

        unperforated_volume = 100 * 50 * 6 + 2 * 88 * 4 * 19.5
        stl_result = run_project(project, {}, fmt="stl")
        step_result = run_project(
            project,
            {
                "rib_thickness": 6,
                "hole_diameter": 4,
                "hole_count": 4,
                "hole_pitch": 20,
                "hole_margin": 10,
            },
            fmt="step",
        )
        self.assertEqual(stl_result["status"], "success")
        self.assertEqual(step_result["status"], "success")
        self.assertEqual(stl_result["solid_count"], 1)
        self.assertEqual(stl_result["bounding_box"], {"x": 100.0, "y": 50.0, "z": 25.5})
        removed = unperforated_volume - stl_result["volume_mm3"]
        expected_removed = 10 * 3.14159 * 3 * 3 * 4
        self.assertAlmostEqual(removed, expected_removed, delta=8)
        self.assertNotAlmostEqual(step_result["volume_mm3"], stl_result["volume_mm3"], delta=1)
        for parameter_id in ("rib_thickness", "hole_diameter", "hole_count", "hole_pitch", "hole_margin"):
            self.assertIn(f'p["{parameter_id}"]', project.cad.source)
        pattern_operation = next(
            operation for operation in project.feature_graph.operations if operation.id == "rib_1_perforation"
        )
        baseline_values = concrete_parameters(project, {})
        override_values = concrete_parameters(
            project,
            {"hole_count": 4, "hole_pitch": 20, "hole_margin": 10},
        )
        self.assertEqual(pattern_instance_offsets(pattern_operation, baseline_values), [8, 26, 44, 62, 80])
        self.assertEqual(pattern_instance_offsets(pattern_operation, override_values), [10, 30, 50, 70])
        measurements = stl_result["feature_measurements"]
        self.assertGreater(measurements["rib_1"]["volume_delta_mm3"], 0)
        self.assertGreater(measurements["rib_2"]["volume_delta_mm3"], 0)
        self.assertLess(measurements["rib_1_perforation"]["volume_delta_mm3"], 0)
        self.assertLess(measurements["rib_2_perforation"]["volume_delta_mm3"], 0)
        self.assertEqual(measurements["rib_1_perforation"]["pattern"]["count"], "hole_count")
        self.assertEqual(measurements["rib_1_perforation"]["expected_instance_count"], 5)
        self.assertEqual(measurements["rib_1_perforation"]["cylindrical_faces_delta"], 5)
        self.assertAlmostEqual(measurements["rib_1_perforation"]["measured_pitch"], 18.0)
        self.assertAlmostEqual(measurements["rib_1_perforation"]["measured_start_margin"], 8.0)
        self.assertAlmostEqual(measurements["rib_1_perforation"]["measured_cylinder_diameter"], 6.0, delta=0.2)
        main.validate_feature_measurements(project, stl_result)

    def test_printable_rib_controls(self):
        def rib_project(origin, thickness, minimum):
            return self.project(
                [
                    {
                        "id": "base",
                        "type": "box",
                        "operation": "add",
                        "parameters": {"length": "base_length", "width": "base_width", "height": "base_height"},
                    },
                    {
                        "id": "rib",
                        "type": "rib",
                        "operation": "add",
                        "target": "base",
                        "parameters": {"length": "rib_length", "thickness": "rib_thickness", "height": "rib_height"},
                        "placement": {"origin": origin},
                        "minimum_printable_thickness": minimum,
                    },
                ],
                {
                    "base_length": 40,
                    "base_width": 30,
                    "base_height": 6,
                    "rib_length": 20,
                    "rib_thickness": thickness,
                    "rib_height": 12,
                },
            )

        cases = [
            ("disconnected", [50, 5, 6], 3, 2, False),
            ("zero_contact", [40, 30, 6], 3, 2, False),
            ("too_thin", [10, 10, 5.5], 1, 2, False),
            ("valid", [10, 10, 5.5], 3, 2, True),
        ]
        for name, origin, thickness, minimum, should_pass in cases:
            with self.subTest(name=name):
                project = rib_project(origin, thickness, minimum)
                result = run_project(project, {}, fmt="stl")
                if should_pass:
                    main.validate_feature_measurements(project, result)
                else:
                    with self.assertRaises(RunnerError):
                        main.validate_feature_measurements(project, result)

    def test_renders_four_distinct_nonblank_views(self):
        project = self.project(
            [
                {
                    "id": "base",
                    "type": "box",
                    "operation": "add",
                    "parameters": {"length": "length", "width": "width", "height": "height"},
                },
                {
                    "id": "offset_block",
                    "type": "box",
                    "operation": "add",
                    "target": "base",
                    "parameters": {"length": "block_length", "width": "block_width", "height": "block_height"},
                    "placement": {"origin": ["block_x", "block_y", "height"]},
                },
            ],
            {
                "length": 50,
                "width": 30,
                "height": 5,
                "block_length": 18,
                "block_width": 10,
                "block_height": 16,
                "block_x": 5,
                "block_y": 4,
            },
        )

        result = run_project(project, {}, fmt="stl", render_views=True)
        renders = result["render_artifacts"]
        self.assertEqual(set(renders), {"front", "top", "right", "isometric"})
        self.assertEqual(len(set(renders.values())), 4)
        for view, data in renders.items():
            with self.subTest(view=view):
                self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
                image = Image.open(io.BytesIO(data)).convert("RGB")
                self.assertEqual(image.size, (512, 512))
                nonwhite = sum(1 for pixel in image.get_flattened_data() if pixel != (255, 255, 255))
                self.assertGreater(nonwhite, 1000)
        main.apply_generation_metadata(project, result)
        self.assertEqual(set(project.generation.render_artifacts), set(renders))
        for artifact in project.generation.render_artifacts.values():
            self.assertEqual((artifact.width, artifact.height), (512, 512))
            self.assertTrue(artifact.image_data.startswith("data:image/png;base64,"))
            self.assertEqual(len(artifact.sha256), 64)

    def test_compiler_error_is_scoped_to_operation(self):
        graph = FeatureGraph.model_validate(
            {"operations": [{"id": "bad_body", "type": "box", "operation": "add", "parameters": {}}]}
        )

        with self.assertRaisesRegex(CompilerError, "bad_body.*missing 'length'"):
            compile_feature_graph(graph, set())

    def test_worker_error_is_scoped_to_compiled_operation(self):
        project = self.project(
            [
                {
                    "id": "base",
                    "type": "box",
                    "operation": "add",
                    "parameters": {"length": "length", "width": "width", "height": "height"},
                },
                {
                    "id": "invalid_fillet",
                    "type": "fillet",
                    "operation": "modify",
                    "target": "base",
                    "parameters": {"radius": "fillet_radius"},
                },
            ],
            {"length": 20, "width": 10, "height": 5, "fillet_radius": 100},
        )
        self.assertIn("# feature:invalid_fillet", project.cad.source)

        with self.assertRaises(RunnerError) as raised:
            run_project(project, {}, fmt="stl")
        self.assertEqual(raised.exception.detail.get("operation_id"), "invalid_fillet")

    def test_unsupported_operation_is_not_replaced_by_existing_source(self):
        graph = FeatureGraph.model_validate(
            {
                "operations": [
                    {
                        "id": "base",
                        "type": "box",
                        "operation": "add",
                        "parameters": {"length": "length", "width": "width", "height": "height"},
                    },
                    {
                        "id": "freeform_feature",
                        "type": "freeform_sweep",
                        "operation": "add",
                        "target": "base",
                    },
                ]
            }
        )
        project = CADProject(
            title="Unsupported operation project",
            parameters={
                "length": {"label": "Length", "value": 20},
                "width": {"label": "Width", "value": 10},
                "height": {"label": "Height", "value": 5},
            },
            feature_graph=graph,
            cad=CADSource(
                source=(
                    'p = PARAMETERS\n'
                    'base = cq.Workplane("XY").box(p["length"], p["width"], p["height"])\n'
                    'result = base\n'
                ),
            ),
        )

        with self.assertRaisesRegex(CompilerError, "freeform_feature.*unsupported primitive type"):
            compile_project_feature_graph(project)

    def test_compiled_and_unresolved_capability_labels_are_independent_from_coverage(self):
        graph = FeatureGraph.model_validate(
            {
                "operations": [
                    {
                        "id": "base",
                        "type": "box",
                        "operation": "add",
                        "parameters": {"length": "length", "width": "width", "height": "height"},
                    }
                ]
            }
        )
        project = CADProject(
            parameters={
                "length": {"label": "Length", "value": 20},
                "width": {"label": "Width", "value": 10},
                "height": {"label": "Height", "value": 5},
            },
            feature_graph=graph,
            cad=CADSource(source='result = cq.Workplane("XY").box(1, 1, 1)\n'),
        )

        compiled = compile_project_feature_graph(project)
        operation = compiled.feature_graph.operations[0]
        self.assertEqual(operation.status, "implemented")
        self.assertEqual(operation.capability_status, "supported")

if __name__ == "__main__":
    unittest.main()
