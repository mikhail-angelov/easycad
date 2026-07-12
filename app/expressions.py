from __future__ import annotations

import ast
import math
from typing import Dict


ALLOWED_MATH = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "pi": math.pi,
}


class ExpressionError(ValueError):
    pass


def evaluate_expression(source: str, values: Dict[str, float]) -> float:
    tree = ast.parse(source, mode="eval")
    return float(_eval_node(tree.body, values))


def _eval_node(node: ast.AST, values: Dict[str, float]) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id not in values:
            raise ExpressionError(f"Unknown parameter '{node.id}'")
        return float(values[node.id])
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, values)
        right = _eval_node(node.right, values)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        raise ExpressionError("Operator is not allowed")
    if isinstance(node, ast.UnaryOp):
        value = _eval_node(node.operand, values)
        if isinstance(node.op, ast.UAdd):
            return value
        if isinstance(node.op, ast.USub):
            return -value
        raise ExpressionError("Unary operator is not allowed")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        fn = ALLOWED_MATH.get(node.func.id)
        if not callable(fn):
            raise ExpressionError(f"Function '{node.func.id}' is not allowed")
        args = [_eval_node(arg, values) for arg in node.args]
        return float(fn(*args))
    raise ExpressionError(f"Expression node {type(node).__name__} is not allowed")

