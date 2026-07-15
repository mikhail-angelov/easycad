from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, Iterable, Set

from .models import CADProject, FeatureGraph, FeatureOperation, FeatureValue
from .validator import ValidationError, validate_source


# Single source of truth for the types accepted by the trusted compiler.
# Keys are the only names proposed to an LLM; values retain compatibility with
# previously recorded plans without widening the planner contract.
COMPILER_OPERATION_TYPES = {
    "box": ("box", "rectangular_body"),
    "cylinder": ("cylinder",),
    "extrude": ("extrude", "extrusion"),
    "revolve": ("revolve", "revolution"),
    "hole": ("hole",),
    "through_hole": ("through_hole",),
    "counterbore": ("counterbore", "counterbore_hole"),
    "countersink": ("countersink", "countersink_hole"),
    "slot": ("slot", "slot2d"),
    "pocket": ("pocket", "rectangular_pocket"),
    "rib": ("rib",),
    "gusset": ("gusset",),
    "text": ("text", "engraving", "embossing"),
    "fillet": ("fillet",),
    "chamfer": ("chamfer",),
    "shell": ("shell",),
    "mirror": ("mirror",),
    "hole_pattern": ("hole_pattern",),
    "perforation_pattern": ("perforation_pattern",),
}

# The compiler accepts aliases for replay compatibility, but semantic evidence
# and planner contracts always refer to these canonical kinds.
COMPILER_OPERATION_KINDS = tuple(COMPILER_OPERATION_TYPES)


@dataclass(frozen=True)
class OperationContract:
    """The feature shape accepted by both the draft planner and trusted compiler."""

    allowed_operations: tuple[str, ...]
    required_parameters: tuple[str, ...] = ()
    optional_parameters: tuple[str, ...] = ()
    profile_types: tuple[str, ...] = ()
    pattern_types: tuple[str, ...] = ()

    @property
    def requires_profile(self) -> bool:
        return bool(self.profile_types)

    @property
    def requires_pattern(self) -> bool:
        return bool(self.pattern_types)

    @property
    def parameter_names(self) -> tuple[str, ...]:
        return self.required_parameters + self.optional_parameters


_SOLID_BOOLEAN_OPERATIONS = ("add", "cut", "intersect")

# This is deliberately the only catalogue proposed to an LLM.  The compiler
# keeps aliases above solely to replay historical trusted projects.
OPERATION_CONTRACTS: Dict[str, OperationContract] = {
    "box": OperationContract(_SOLID_BOOLEAN_OPERATIONS, ("length", "width", "height")),
    "cylinder": OperationContract(_SOLID_BOOLEAN_OPERATIONS, ("radius", "height")),
    "extrude": OperationContract(_SOLID_BOOLEAN_OPERATIONS, ("distance",), profile_types=("rectangle", "circle", "polyline")),
    "revolve": OperationContract(_SOLID_BOOLEAN_OPERATIONS, optional_parameters=("angle_deg",), profile_types=("rectangle", "circle", "polyline")),
    "hole": OperationContract(("cut",), ("diameter", "depth")),
    "through_hole": OperationContract(("cut",), ("diameter", "depth")),
    "counterbore": OperationContract(("cut",), ("diameter", "depth", "bore_diameter", "bore_depth")),
    "countersink": OperationContract(("cut",), ("diameter", "depth", "sink_diameter", "sink_depth")),
    "slot": OperationContract(("cut",), ("length", "width", "depth")),
    "pocket": OperationContract(("cut",), ("length", "width", "depth")),
    "rib": OperationContract(_SOLID_BOOLEAN_OPERATIONS, ("length", "thickness", "height")),
    "gusset": OperationContract(_SOLID_BOOLEAN_OPERATIONS, ("width",), profile_types=("rectangle", "circle", "polyline")),
    "text": OperationContract(_SOLID_BOOLEAN_OPERATIONS, ("content", "size", "distance")),
    "fillet": OperationContract(("modify",), ("radius",)),
    "chamfer": OperationContract(("modify",), ("distance",)),
    "shell": OperationContract(("modify",), ("thickness",)),
    "mirror": OperationContract(("modify",)),
    "hole_pattern": OperationContract(("pattern",), ("depth",), ("radius", "start_angle_deg"), ("circle", "rectangle", "slot"), ("linear", "polar")),
    "perforation_pattern": OperationContract(("pattern",), ("depth",), ("radius", "start_angle_deg"), ("circle", "rectangle", "slot"), ("linear", "polar")),
}

DRAFT_SPECIFICATION_OPERATION_KINDS = tuple(OPERATION_CONTRACTS)


def planner_operation_types() -> str:
    return ", ".join(COMPILER_OPERATION_KINDS)


def draft_specification_operation_types() -> str:
    return ", ".join(DRAFT_SPECIFICATION_OPERATION_KINDS)


def draft_operation_contract_descriptions() -> str:
    """Compact planner instructions derived from the same registry as validation."""
    lines = []
    for feature_type, contract in OPERATION_CONTRACTS.items():
        parameters = ", ".join(contract.required_parameters) or "none"
        optional = f"; optional {', '.join(contract.optional_parameters)}" if contract.optional_parameters else ""
        profile = f"; profile {'/'.join(contract.profile_types)}" if contract.profile_types else ""
        pattern = f"; pattern {'/'.join(contract.pattern_types)}" if contract.pattern_types else ""
        lines.append(
            f"{feature_type}: operation {'/'.join(contract.allowed_operations)}; parameters {parameters}{optional}{profile}{pattern}"
        )
    return "\n".join(lines)


def operation_contract(feature_type: str) -> OperationContract | None:
    """Return a draft contract for a canonical type, never for a compatibility alias."""
    return OPERATION_CONTRACTS.get(feature_type)


def feature_contract_issues(feature: FeatureOperation) -> list[str]:
    """Return deterministic draft/graph errors before CadQuery is invoked."""
    contract = operation_contract(feature.type)
    if contract is None:
        if canonical_operation_type(feature.type) in OPERATION_CONTRACTS:
            return [f"uses compatibility alias '{feature.type}'; use the canonical operation type"]
        return [f"uses unsupported operation type {feature.type}"]
    issues: list[str] = []
    if feature.operation not in contract.allowed_operations:
        issues.append(f"operation '{feature.operation}' is not allowed for {feature.type}")
    missing = [key for key in contract.required_parameters if key not in feature.parameters]
    if missing:
        issues.append(f"is missing required parameter(s): {', '.join(missing)}")
    unexpected = sorted(set(feature.parameters) - set(contract.parameter_names))
    if unexpected:
        issues.append(f"has unsupported parameter(s): {', '.join(unexpected)}")
    if contract.requires_profile:
        if feature.profile is None:
            issues.append("requires a profile")
        else:
            profile_type = feature.profile.type.lower()
            if profile_type not in contract.profile_types:
                issues.append(
                    f"profile type '{feature.profile.type}' is not supported; use one of {', '.join(contract.profile_types)}"
                )
            else:
                issues.extend(_profile_contract_issues(feature, profile_type))
    elif feature.profile is not None:
        issues.append("does not accept a profile")
    if contract.requires_pattern:
        if feature.pattern is None:
            issues.append("requires a pattern")
        else:
            if feature.pattern.type not in contract.pattern_types:
                issues.append(
                    f"pattern type '{feature.pattern.type}' is not supported; use one of {', '.join(contract.pattern_types)}"
                )
            elif feature.pattern.type == "polar" and "radius" not in feature.parameters:
                issues.append("polar pattern requires parameter 'radius'")
    elif feature.pattern is not None:
        issues.append("does not accept a pattern")
    return issues


def _profile_contract_issues(feature: FeatureOperation, profile_type: str) -> list[str]:
    profile = feature.profile
    assert profile is not None
    required_dimensions = {
        "rectangle": ("width", "height"),
        "circle": ("diameter",),
        "slot": ("length", "width"),
    }.get(profile_type, ())
    missing = [key for key in required_dimensions if key not in profile.dimensions]
    if missing:
        return [f"profile is missing required dimension(s): {', '.join(missing)}"]
    unexpected = sorted(set(profile.dimensions) - set(required_dimensions))
    if unexpected:
        return [f"profile has unsupported dimension(s): {', '.join(unexpected)}"]
    if profile_type == "polyline" and len(profile.points) < 3:
        return ["polyline profile requires at least three points"]
    return []


def compiler_operation_types() -> frozenset[str]:
    return frozenset(alias for aliases in COMPILER_OPERATION_TYPES.values() for alias in aliases)


def canonical_operation_type(value: str) -> str:
    normalized = value.strip().lower()
    for canonical, aliases in COMPILER_OPERATION_TYPES.items():
        if normalized in aliases:
            return canonical
    return normalized


class CompilerError(ValueError):
    def __init__(self, operation_id: str, message: str):
        super().__init__(f"Feature operation '{operation_id}': {message}")
        self.operation_id = operation_id


def compile_project_feature_graph(project: CADProject) -> CADProject:
    source = compile_feature_graph(project.feature_graph, project.parameters)
    compiled = project.model_copy(deep=True)
    compiled.cad.source = source
    compiled.cad.source_kind = "compiled"
    compiled.cad.implemented_feature_ids = [
        operation.id
        for operation in compiled.feature_graph.operations
        if operation.status not in {"unresolved", "unsupported"}
    ]
    for operation in compiled.feature_graph.operations:
        if operation.status in {"planned", "implemented"}:
            operation.status = "implemented"
            operation.implementation = operation.id
            operation.capability_status = "supported"
    return compiled


def pattern_instance_offsets(operation: FeatureOperation, values: Dict[str, object]) -> list[float]:
    pattern = operation.pattern
    if pattern is None or pattern.type != "linear":
        raise CompilerError(operation.id, "instance offsets currently require a linear pattern")
    count = int(_concrete_feature_value(pattern.count, values, operation.id))
    pitch = float(_concrete_feature_value(pattern.pitch, values, operation.id))
    margin = (
        float(_concrete_feature_value(pattern.start_margin, values, operation.id))
        if pattern.start_margin is not None
        else 0.0
    )
    if count < 1:
        raise CompilerError(operation.id, "pattern count must be positive")
    return [margin + index * pitch for index in range(count)]


def compile_feature_graph(graph: FeatureGraph, parameter_ids: Iterable[str]) -> str:
    parameters = set(parameter_ids)
    lines = ["p = PARAMETERS", ""]
    operation_roots: Dict[str, str] = {}
    current_shape: Dict[str, str] = {}
    predecessors: Dict[str, str] = {}
    last_shape = ""

    for operation in graph.operations:
        if operation.status in {"unresolved", "unsupported"}:
            continue
        feature_type = canonical_operation_type(operation.type)
        if operation.operation == "pattern":
            if not operation.target or operation.target not in operation_roots:
                raise CompilerError(operation.id, "pattern target has not been compiled")
            root = operation_roots[operation.target]
            target_shape = current_shape[root]
            predecessors[operation.id] = target_shape
            primitive_name = f"{operation.id}_shape"
            pattern_expression = _compile_pattern_primitive(operation, parameters)
            boolean_method = _pattern_boolean_method(operation)
            lines.extend(
                [
                    f"# feature:{operation.id}",
                    f"{primitive_name} = {pattern_expression}",
                    f"{operation.id} = {target_shape}.{boolean_method}({primitive_name})",
                    "",
                ]
            )
            operation_roots[operation.id] = root
            current_shape[root] = operation.id
            last_shape = operation.id
            continue
        if feature_type in {"fillet", "chamfer", "shell", "mirror"}:
            if not operation.target or operation.target not in operation_roots:
                raise CompilerError(operation.id, "modifier target has not been compiled")
            root = operation_roots[operation.target]
            target_shape = current_shape[root]
            predecessors[operation.id] = target_shape
            expression = _compile_modifier(operation, target_shape, parameters)
            lines.extend([f"# feature:{operation.id}", f"{operation.id} = {expression}", ""])
            operation_roots[operation.id] = root
            current_shape[root] = operation.id
            last_shape = operation.id
            continue
        shape_lines = _compile_primitive(operation, parameters)
        primitive_name = f"{operation.id}_shape"
        lines.extend([f"# feature:{operation.id}", f"{primitive_name} = ("])
        lines.extend(f"    {line}" for line in shape_lines)
        lines.append(")")

        if operation.target:
            if operation.target not in operation_roots:
                raise CompilerError(operation.id, f"target '{operation.target}' has not been compiled")
            root = operation_roots[operation.target]
            target_shape = current_shape[root]
            predecessors[operation.id] = target_shape
            method = {"add": "union", "cut": "cut", "intersect": "intersect"}.get(operation.operation)
            if method is None:
                raise CompilerError(operation.id, f"operation '{operation.operation}' is not a solid boolean")
            lines.append(f"{operation.id} = {target_shape}.{method}({primitive_name})")
        else:
            if operation.operation != "add":
                raise CompilerError(operation.id, f"root operation must be additive, got '{operation.operation}'")
            root = operation.id
            lines.append(f"{operation.id} = {primitive_name}")

        operation_roots[operation.id] = root
        current_shape[root] = operation.id
        last_shape = operation.id
        lines.append("")

    if not last_shape:
        raise CompilerError("feature_graph", "no compilable operations")
    lines.append(f"FEATURE_PREDECESSORS = {json.dumps(predecessors, sort_keys=True)}")
    lines.append(f"result = {last_shape}")
    source = "\n".join(lines) + "\n"
    try:
        validate_source(source)
    except ValidationError as exc:
        raise CompilerError("feature_graph", f"trusted compiler emitted invalid source: {exc}") from exc
    return source


def _compile_primitive(operation: FeatureOperation, parameter_ids: Set[str]) -> list[str]:
    feature_type = canonical_operation_type(operation.type)
    if feature_type == "box":
        length = _required_value(operation, "length", parameter_ids)
        width = _required_value(operation, "width", parameter_ids)
        height = _required_value(operation, "height", parameter_ids)
        expression = f'cq.Workplane("{_plane(operation)}").box({length}, {width}, {height}, centered=False)'
    elif feature_type == "cylinder":
        radius = _required_value(operation, "radius", parameter_ids)
        height = _required_value(operation, "height", parameter_ids)
        expression = f'cq.Workplane("{_plane(operation)}").circle({radius}).extrude({height})'
    elif feature_type == "extrude":
        profile = _compile_profile(operation, parameter_ids)
        distance = _required_value(operation, "distance", parameter_ids, aliases=("depth", "height", "length"))
        expression = f'cq.Workplane("{_plane(operation)}"){profile}.extrude({distance})'
    elif feature_type == "revolve":
        profile = _compile_profile(operation, parameter_ids)
        angle = _optional_value(operation, "angle_deg", 360, parameter_ids)
        expression = (
            f'cq.Workplane("{_plane(operation, default="XZ")}"){profile}'
            f".revolve({angle}, (0, 0), (0, 1))"
        )
    elif feature_type in {"hole", "through_hole"}:
        diameter = _required_value(operation, "diameter", parameter_ids)
        depth = _required_value(operation, "depth", parameter_ids)
        expression = f'cq.Workplane("{_plane(operation)}").circle({diameter} / 2).extrude({depth})'
    elif feature_type == "counterbore":
        diameter = _required_value(operation, "diameter", parameter_ids)
        depth = _required_value(operation, "depth", parameter_ids)
        bore_diameter = _required_value(operation, "bore_diameter", parameter_ids)
        bore_depth = _required_value(operation, "bore_depth", parameter_ids)
        plane = _plane(operation)
        expression = (
            f'cq.Workplane("{plane}").circle({diameter} / 2).extrude({depth})'
            f'.union(cq.Workplane("{plane}").circle({bore_diameter} / 2).extrude({bore_depth}))'
        )
    elif feature_type == "countersink":
        diameter = _required_value(operation, "diameter", parameter_ids)
        depth = _required_value(operation, "depth", parameter_ids)
        sink_diameter = _required_value(operation, "sink_diameter", parameter_ids)
        sink_depth = _required_value(operation, "sink_depth", parameter_ids)
        plane = _plane(operation)
        expression = (
            f'cq.Workplane("{plane}").circle({diameter} / 2).extrude({depth})'
            f'.union(cq.Workplane("{plane}").circle({sink_diameter} / 2)'
            f".workplane(offset={sink_depth}).circle({diameter} / 2).loft(combine=True))"
        )
    elif feature_type == "slot":
        length = _required_value(operation, "length", parameter_ids)
        width = _required_value(operation, "width", parameter_ids)
        depth = _required_value(operation, "depth", parameter_ids)
        expression = f'cq.Workplane("{_plane(operation)}").slot2D({length}, {width}).extrude({depth})'
    elif feature_type == "pocket":
        length = _required_value(operation, "length", parameter_ids)
        width = _required_value(operation, "width", parameter_ids)
        depth = _required_value(operation, "depth", parameter_ids)
        expression = f'cq.Workplane("{_plane(operation)}").rect({length}, {width}).extrude({depth})'
    elif feature_type == "rib":
        length = _required_value(operation, "length", parameter_ids)
        thickness = _required_value(operation, "thickness", parameter_ids)
        height = _required_value(operation, "height", parameter_ids)
        expression = f'cq.Workplane("{_plane(operation)}").box({length}, {thickness}, {height}, centered=False)'
    elif feature_type == "gusset":
        profile = _compile_profile(operation, parameter_ids)
        width = _required_value(operation, "width", parameter_ids)
        expression = f'cq.Workplane("{_plane(operation, default="XZ")}"){profile}.extrude({width})'
    elif feature_type == "text":
        content = _required_value(operation, "content", parameter_ids)
        size = _required_value(operation, "size", parameter_ids)
        distance = _required_value(operation, "distance", parameter_ids)
        expression = (
            f'cq.Workplane("{_plane(operation)}").text({content}, {size}, {distance}, combine=False)'
        )
    else:
        raise CompilerError(operation.id, f"unsupported primitive type '{operation.type}'")

    origin = operation.placement.origin if operation.placement else None
    if origin:
        coordinates = ", ".join(_value(item, parameter_ids, operation.id) for item in origin)
        expression += f".translate(({coordinates}))"
    return [expression]


def _compile_modifier(operation: FeatureOperation, target_shape: str, parameter_ids: Set[str]) -> str:
    feature_type = canonical_operation_type(operation.type)
    selector = operation.placement.reference if operation.placement else None
    if feature_type == "fillet":
        radius = _required_value(operation, "radius", parameter_ids)
        edges = f".edges({json.dumps(selector)})" if selector else ".edges()"
        return f"{target_shape}{edges}.fillet({radius})"
    if feature_type == "chamfer":
        distance = _required_value(operation, "distance", parameter_ids)
        edges = f".edges({json.dumps(selector)})" if selector else ".edges()"
        return f"{target_shape}{edges}.chamfer({distance})"
    if feature_type == "shell":
        thickness = _required_value(operation, "thickness", parameter_ids)
        faces = f".faces({json.dumps(selector)})" if selector else ".faces()"
        return f"{target_shape}{faces}.shell({thickness})"
    if feature_type == "mirror":
        plane = operation.placement.plane if operation.placement and operation.placement.plane else "YZ"
        return f"{target_shape}.mirror(mirrorPlane={json.dumps(plane)}, union=True)"
    raise CompilerError(operation.id, f"unsupported modifier type '{operation.type}'")


def _compile_pattern_primitive(operation: FeatureOperation, parameter_ids: Set[str]) -> str:
    pattern = operation.pattern
    if pattern is None:
        raise CompilerError(operation.id, "pattern definition is missing")
    plane = _plane(operation)
    count = f"int({_value(pattern.count, parameter_ids, operation.id)})"
    if pattern.type == "linear":
        pitch = _value(pattern.pitch, parameter_ids, operation.id)
        axis = (pattern.axis or "X").upper()
        if axis == "X":
            array = f".rarray({pitch}, 1, {count}, 1, center=False)"
        elif axis == "Y":
            array = f".rarray(1, {pitch}, 1, {count}, center=False)"
        else:
            raise CompilerError(operation.id, "linear pattern axis must be X or Y")
    elif pattern.type == "polar":
        radius = _required_value(operation, "radius", parameter_ids)
        start_angle = _optional_value(operation, "start_angle_deg", 0, parameter_ids)
        angle = _value(pattern.angle_deg, parameter_ids, operation.id)
        array = f".polarArray({radius}, {start_angle}, {angle}, {count}, fill=True)"
    else:
        raise CompilerError(operation.id, f"unsupported pattern type '{pattern.type}'")

    profile = operation.profile
    if profile is None:
        raise CompilerError(operation.id, "pattern requires profile")
    if profile.type.lower() == "circle":
        diameter = _profile_value(operation, "diameter", parameter_ids)
        profile_call = f".circle({diameter} / 2)"
    elif profile.type.lower() in {"rectangle", "rect"}:
        width = _profile_value(operation, "width", parameter_ids)
        height = _profile_value(operation, "height", parameter_ids)
        profile_call = f".rect({width}, {height})"
    elif profile.type.lower() == "slot":
        length = _profile_value(operation, "length", parameter_ids)
        width = _profile_value(operation, "width", parameter_ids)
        profile_call = f".slot2D({length}, {width})"
    else:
        raise CompilerError(operation.id, f"unsupported pattern profile '{profile.type}'")
    depth = _required_value(operation, "depth", parameter_ids)
    expression = f'cq.Workplane("{plane}"){array}{profile_call}.extrude({depth})'
    if pattern.type == "linear" and pattern.start_margin is not None:
        margin = _value(pattern.start_margin, parameter_ids, operation.id)
        translation = _local_axis_translation(plane, (pattern.axis or "X").upper(), margin)
        expression += f".translate({translation})"
    origin = operation.placement.origin if operation.placement else None
    if origin:
        coordinates = ", ".join(_value(item, parameter_ids, operation.id) for item in origin)
        expression += f".translate(({coordinates}))"
    return expression


def _local_axis_translation(plane: str, axis: str, distance: str) -> str:
    translations = {
        ("XY", "X"): f"({distance}, 0, 0)",
        ("XY", "Y"): f"(0, {distance}, 0)",
        ("XZ", "X"): f"({distance}, 0, 0)",
        ("XZ", "Y"): f"(0, 0, {distance})",
        ("YZ", "X"): f"(0, {distance}, 0)",
        ("YZ", "Y"): f"(0, 0, {distance})",
    }
    try:
        return translations[(plane, axis)]
    except KeyError as exc:
        raise CompilerError("pattern", f"unsupported plane/axis combination {plane}/{axis}") from exc


def _pattern_boolean_method(operation: FeatureOperation) -> str:
    feature_type = canonical_operation_type(operation.type)
    if feature_type in {"hole_pattern", "perforation_pattern"}:
        return "cut"
    raise CompilerError(operation.id, "pattern type must declare additive or subtractive feature semantics")


def _compile_profile(operation: FeatureOperation, parameter_ids: Set[str]) -> str:
    profile = operation.profile
    if profile is None:
        raise CompilerError(operation.id, "extrude/revolve requires profile")
    profile_type = profile.type.strip().lower()
    if profile_type in {"rectangle", "rect"}:
        width = _profile_value(operation, "width", parameter_ids)
        height = _profile_value(operation, "height", parameter_ids)
        return f".rect({width}, {height})"
    if profile_type == "circle":
        diameter = _profile_value(operation, "diameter", parameter_ids)
        return f".circle({diameter} / 2)"
    if profile_type == "polyline":
        if len(profile.points) < 3:
            raise CompilerError(operation.id, "polyline profile requires at least three points")
        first = profile.points[0]
        calls = f".moveTo({_value(first[0], parameter_ids, operation.id)}, {_value(first[1], parameter_ids, operation.id)})"
        for point in profile.points[1:]:
            calls += f".lineTo({_value(point[0], parameter_ids, operation.id)}, {_value(point[1], parameter_ids, operation.id)})"
        return calls + ".close()"
    raise CompilerError(operation.id, f"unsupported profile type '{profile.type}'")


def _required_value(
    operation: FeatureOperation,
    key: str,
    parameter_ids: Set[str],
    aliases: tuple[str, ...] = (),
) -> str:
    for candidate in (key, *aliases):
        if candidate in operation.parameters:
            return _value(operation.parameters[candidate], parameter_ids, operation.id)
        if operation.profile and candidate in operation.profile.dimensions:
            return _value(operation.profile.dimensions[candidate], parameter_ids, operation.id)
    raise CompilerError(operation.id, f"missing '{key}' parameter")


def _profile_value(operation: FeatureOperation, key: str, parameter_ids: Set[str]) -> str:
    if operation.profile and key in operation.profile.dimensions:
        return _value(operation.profile.dimensions[key], parameter_ids, operation.id)
    raise CompilerError(operation.id, f"profile is missing '{key}'")


def _optional_value(
    operation: FeatureOperation, key: str, default: FeatureValue, parameter_ids: Set[str]
) -> str:
    if key in operation.parameters:
        return _value(operation.parameters[key], parameter_ids, operation.id)
    return _value(default, parameter_ids, operation.id)


def _value(value: FeatureValue, parameter_ids: Set[str], operation_id: str) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return repr(value)
    if value not in parameter_ids:
        raise CompilerError(operation_id, f"references unknown parameter '{value}'")
    return f"p[{json.dumps(value)}]"


def _concrete_feature_value(value: FeatureValue | None, values: Dict[str, object], operation_id: str) -> object:
    if value is None:
        raise CompilerError(operation_id, "pattern value is missing")
    if isinstance(value, str):
        if value not in values:
            raise CompilerError(operation_id, f"references unknown parameter '{value}'")
        return values[value]
    return value


def _plane(operation: FeatureOperation, default: str = "XY") -> str:
    return operation.placement.plane if operation.placement and operation.placement.plane else default
