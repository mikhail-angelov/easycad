from __future__ import annotations

import ast
from typing import List



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
