"""Level 0 static code guard (SPEC12).

An AST allowlist run in the worker before every `exec`. It is defence-in-depth,
NOT the security boundary — the hardened container is. It stops casual and
accidental abuse cheaply (stray `import os`, obvious sandbox-escape idioms)
before any code runs.

`check(code) -> (ok, reason)`.
"""

import ast

# Only these top-level modules may be imported by generated CadQuery scripts.
ALLOWED_IMPORTS = {"cadquery", "math"}

# Builtins that enable arbitrary execution / IO / introspection escapes.
FORBIDDEN_NAMES = {
    "eval",
    "exec",
    "compile",
    "open",
    "input",
    "__import__",
    "globals",
    "locals",
    "vars",
    "getattr",
    "setattr",
    "delattr",
}


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def _top(module: str) -> str:
    return module.split(".", 1)[0]


def check(code: str) -> tuple[bool, str]:
    """Return (ok, reason). `ok=False` means the code must not be executed."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return False, f"syntax error: {exc.msg}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _top(alias.name) not in ALLOWED_IMPORTS:
                    return False, f"import of '{alias.name}' is not allowed"
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _top(module) not in ALLOWED_IMPORTS:
                return False, f"import from '{module or '.'}' is not allowed"
        elif isinstance(node, ast.Attribute):
            if _is_dunder(node.attr):
                return False, f"access to dunder attribute '{node.attr}' is not allowed"
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_NAMES:
                return False, f"use of '{node.id}' is not allowed"
            if _is_dunder(node.id):
                return False, f"use of '{node.id}' is not allowed"

    return True, ""
