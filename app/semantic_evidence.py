from __future__ import annotations

from typing import Mapping

from .feature_compiler import COMPILER_OPERATION_KINDS


SEMANTIC_EVIDENCE: dict[str, dict[str, str]] = {
    "box": {"positive": "test_feature_compiler:additive_and_subtractive_extrusions", "negative": "test_compiler_negative:noop_add_and_cut", "invariant": "positive material delta"},
    "cylinder": {"positive": "test_feature_compiler:cylinder_from_planner_catalog", "negative": "test_compiler_negative:noop_add_and_cut", "invariant": "diameter and height bounds"},
    "extrude": {"positive": "test_feature_compiler:additive_and_subtractive_extrusions", "negative": "test_compiler_negative:noop_add_and_cut", "invariant": "material delta"},
    "revolve": {"positive": "test_feature_compiler:revolved_polyline_profile", "negative": "test_compiler_negative:noop_add_and_cut", "invariant": "rotational bounds"},
    "hole": {"positive": "test_feature_compiler:holes_counterbores_countersinks_slots_and_pockets", "negative": "test_auto_repair:wrong_hole_count", "invariant": "cylindrical diameter and removal"},
    "through_hole": {"positive": "test_bracket_fixture_regression:complete_bracket", "negative": "test_bracket_fixture_regression:protected_feature_mutations", "invariant": "cylindrical diameter and removal"},
    "counterbore": {"positive": "test_feature_compiler:holes_counterbores_countersinks_slots_and_pockets", "negative": "test_compiler_negative:noop_add_and_cut", "invariant": "stepped cylindrical removal"},
    "countersink": {"positive": "test_feature_compiler:holes_counterbores_countersinks_slots_and_pockets", "negative": "test_compiler_negative:noop_add_and_cut", "invariant": "conical removal"},
    "slot": {"positive": "test_feature_compiler:holes_counterbores_countersinks_slots_and_pockets", "negative": "test_auto_repair:noop_pocket", "invariant": "subtractive volume"},
    "pocket": {"positive": "test_feature_compiler:holes_counterbores_countersinks_slots_and_pockets", "negative": "test_auto_repair:noop_pocket", "invariant": "subtractive volume"},
    "rib": {"positive": "test_feature_compiler:ribs_and_gussets", "negative": "test_auto_repair:thin_rib", "invariant": "additive volume and connectivity"},
    "gusset": {"positive": "test_feature_compiler:ribs_and_gussets", "negative": "test_auto_repair:thin_rib", "invariant": "additive volume and connectivity"},
    "text": {"positive": "test_feature_compiler:planar_text", "negative": "test_compiler_negative:empty_text", "invariant": "engraved volume reduction"},
    "fillet": {"positive": "test_feature_compiler:fillets_chamfers_shells_mirrors", "negative": "test_compiler_negative:oversized_modifier", "invariant": "bounded material removal"},
    "chamfer": {"positive": "test_feature_compiler:fillets_chamfers_shells_mirrors", "negative": "test_compiler_negative:oversized_modifier", "invariant": "bounded material removal"},
    "shell": {"positive": "test_feature_compiler:fillets_chamfers_shells_mirrors", "negative": "test_compiler_negative:oversized_modifier", "invariant": "volume reduction and one solid"},
    "mirror": {"positive": "test_feature_compiler:fillets_chamfers_shells_mirrors", "negative": "test_compiler_negative:oversized_modifier", "invariant": "symmetric bounds"},
    "hole_pattern": {"positive": "test_feature_compiler:linear_and_polar_patterns", "negative": "test_compiler_negative:zero_count_pattern", "invariant": "count and pitch"},
    "perforation_pattern": {"positive": "test_feature_compiler:perforation_patterns", "negative": "test_semantic_fixtures:missing_perforation", "invariant": "count and removal"},
}


def lint_semantic_evidence(manifest: Mapping[str, Mapping[str, str]] = SEMANTIC_EVIDENCE) -> None:
    missing_kinds = sorted(set(COMPILER_OPERATION_KINDS) - set(manifest))
    extra_kinds = sorted(set(manifest) - set(COMPILER_OPERATION_KINDS))
    incomplete = sorted(kind for kind, entry in manifest.items() if any(not entry.get(field) for field in ("positive", "negative", "invariant")))
    if missing_kinds or extra_kinds or incomplete:
        raise ValueError(f"semantic evidence mismatch: missing={missing_kinds}, extra={extra_kinds}, incomplete={incomplete}")
