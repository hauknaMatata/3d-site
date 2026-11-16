"""Project user-parameters (like Fusion 360 'User Parameters'). Any operation argument written as
"=expression" is evaluated against the project's parameters at replay time, so changing a parameter
re-generates every dependent feature."""
from __future__ import annotations

import ast
import operator
from typing import Any

from .engines.base import EngineError

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv}
_FUN = {"min": min, "max": max, "abs": abs, "round": round}


def _eval(node, params):
    if isinstance(node, ast.Expression):
        return _eval(node.body, params)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str)):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in params:
            raise EngineError(f"Unknown parameter '{node.id}'")
        return params[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        return _BIN[type(node.op)](_eval(node.left, params), _eval(node.right, params))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _eval(node.operand, params)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUN and not node.keywords:
        return _FUN[node.func.id](*[_eval(a, params) for a in node.args])
    raise EngineError("Unsupported expression (allowed: numbers, parameters, + - * / ** %, min, max, abs, round)")


def evaluate(expr: str, params: dict[str, Any]) -> Any:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise EngineError(f"Bad expression '{expr}': {e.msg}") from e
    return _eval(tree, params)


def resolve(value: Any, params: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("=") and len(value) > 1:
        return evaluate(value[1:], params)
    if isinstance(value, list):
        return [resolve(v, params) for v in value]
    if isinstance(value, dict):
        return {k: resolve(v, params) for k, v in value.items()}
    return value
