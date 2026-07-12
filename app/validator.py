from __future__ import annotations

import ast
import re
from typing import List, Set

from .expressions import ExpressionError, evaluate_expression


FORBIDDEN_NODES = (
    ast.Import,
    ast.ImportFrom,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    ast.Raise,
    ast.Global,
    ast.Nonlocal,
    ast.Delete,
    ast.While,
    ast.For,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.Yield,
    ast.YieldFrom,
    ast.Await,
)

FORBIDDEN_NAMES = {
    "open",
    "exec",
    "eval",
    "compile",
    "__import__",
    "globals",
    "locals",
    "vars",
    "dir",
    "getattr",
    "setattr",
    "delattr",
    "input",
    "help",
    "breakpoint",
    "os",
    "sys",
    "subprocess",
    "socket",
    "pathlib",
    "requests",
    "httpx",
    "builtins",
}

ALLOWED_ROOT_NAMES = {"cq", "PARAMETERS", "p", "math", "result"}
ALLOWED_CADQUERY_METHODS = {
    "Workplane",
    "box",
    "circle",
    "ellipse",
    "polygon",
    "rect",
    "slot2D",
    "polyline",
    "moveTo",
    "lineTo",
    "threePointArc",
    "close",
    "extrude",
    "revolve",
    "sweep",
    "loft",
    "cut",
    "cutThruAll",
    "union",
    "intersect",
    "hole",
    "cboreHole",
    "cskHole",
    "fillet",
    "chamfer",
    "shell",
    "faces",
    "edges",
    "vertices",
    "workplane",
    "center",
    "pushPoints",
    "translate",
    "rotate",
    "mirror",
    "rarray",
    "polarArray",
    "text",
    "val",
}
DISCOURAGED_SOURCE_PATTERNS = {
    "helix": "Helical/thread geometry is not allowed in this prototype; use a plain cylinder",
}
ALLOWED_MATH_ATTRS = {"sqrt", "sin", "cos", "tan", "radians", "degrees", "pi"}
ALLOWED_DIRECT_CALLS = {"int"}


class ValidationError(ValueError):
    pass


def validate_source(source: str) -> None:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValidationError(f"Syntax error: {exc}") from exc

    errors: List[str] = []
    assigned_names = set()

    for node in ast.walk(tree):
        if isinstance(node, FORBIDDEN_NODES):
            errors.append(f"{type(node).__name__} is not allowed")
        if isinstance(node, ast.Name):
            if node.id.startswith("__") or node.id in FORBIDDEN_NAMES:
                errors.append(f"Name '{node.id}' is not allowed")
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                errors.append(f"Private attribute '{node.attr}' is not allowed")
            if isinstance(node.value, ast.Name) and node.value.id == "math":
                if node.attr not in ALLOWED_MATH_ATTRS:
                    errors.append(f"math.{node.attr} is not allowed")
            elif node.attr not in ALLOWED_CADQUERY_METHODS:
                errors.append(f"Method or attribute '{node.attr}' is not allowed")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id not in ALLOWED_DIRECT_CALLS:
                    errors.append(f"Direct function call '{node.func.id}' is not allowed")
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned_names.add(target.id)

    if "result" not in assigned_names:
        errors.append("Generated source must assign result")

    lowered_source = source.lower()
    for pattern, message in DISCOURAGED_SOURCE_PATTERNS.items():
        if pattern in lowered_source:
            errors.append(message)

    if errors:
        raise ValidationError("; ".join(sorted(set(errors))))


def validate_project(project) -> None:
    errors: List[str] = []
    parameter_ids = set(project.parameters)
    values = {}

    for key, param in project.parameters.items():
        if not re.match(r"^[a-z][a-z0-9_]*$", key):
            errors.append(f"Parameter '{key}' must be snake_case and start with a letter")
        if param.type == "number":
            if param.min is not None and param.max is not None and param.min > param.max:
                errors.append(f"Parameter '{key}' minimum is greater than maximum")
            if param.value is not None and param.min is not None and param.value < param.min:
                errors.append(f"Parameter '{key}' value is below minimum {param.min}")
            if param.value is not None and param.max is not None and param.value > param.max:
                errors.append(f"Parameter '{key}' value is above maximum {param.max}")
            if param.value is not None:
                values[key] = float(param.value)

    pending = {
        key: param.expression
        for key, param in project.parameters.items()
        if param.type == "expression" and param.expression
    }
    while pending:
        progressed = False
        for key, expression in list(pending.items()):
            try:
                values[key] = evaluate_expression(expression, values)
            except ExpressionError:
                continue
            except Exception as exc:
                errors.append(f"Parameter '{key}' expression is invalid: {exc}")
                del pending[key]
                progressed = True
                continue
            del pending[key]
            progressed = True
        if not progressed:
            unresolved = ", ".join(sorted(pending))
            errors.append(f"Could not resolve derived parameters: {unresolved}")
            break

    coverage_by_feature = {}
    for operation in project.feature_graph.operations:
        for feature_id in operation.source_feature_ids:
            coverage_by_feature.setdefault(feature_id, []).append(operation)
    for feature in project.analysis.features:
        try:
            confidence = float(feature.get("confidence"))
        except (TypeError, ValueError):
            continue
        if confidence < 0.8:
            continue
        feature_id = str(feature.get("id") or "")
        if not feature_id:
            continue
        coverage = coverage_by_feature.get(feature_id, [])
        if not coverage:
            errors.append(f"High-confidence feature '{feature_id}' has no Feature Graph coverage")
        elif all(operation.status == "planned" for operation in coverage):
            errors.append(f"High-confidence feature '{feature_id}' has no final coverage state")

    if errors:
        raise ValidationError("; ".join(errors))


def parameter_references(source: str) -> Set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    refs: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        target = node.value
        if isinstance(target, ast.Name) and target.id in {"PARAMETERS", "p"}:
            key = _literal_subscript_key(node.slice)
            if key:
                refs.add(key)
    return refs


def _literal_subscript_key(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""
