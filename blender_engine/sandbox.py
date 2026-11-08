"""Allowlist checker + restricted runner for builder-agent code. Stdlib only (used by director AND Blender).

Generated code may ONLY call the lesson toolkit (blender_engine/toolkit.py API) and basic math. Rules:
  - no imports, no attribute access at all (`x.y` is rejected), so no object-graph escapes
    (`().__class__...`), no string .format tricks, no bpy, no os
  - no while loops, with/try/raise, classes, lambdas, global/nonlocal, decorators, async, yield
  - every call must be a plain name: a toolkit function, an allowed builtin, or a function defined in the code
  - names starting with "_" are rejected; size and loop counts are bounded
The runner also executes in a SEPARATE headless Blender process (lesson_runner.py) with a timeout, never
in the user's open scene. This is defense in depth, not a perfect Python sandbox; the process boundary,
timeout, and the no-attribute rule are the main protections.
"""

from __future__ import annotations

import ast
import math

MAX_CODE_CHARS = 30_000
MAX_RANGE = 2_000

SAFE_BUILTINS = {
    "range": None, "len": len, "min": min, "max": max, "abs": abs, "round": round, "enumerate": enumerate,
    "zip": zip, "list": list, "dict": dict, "tuple": tuple, "float": float, "int": int, "str": str,
    "sorted": sorted, "sum": sum, "any": any, "all": all, "reversed": reversed, "print": print, "bool": bool,
}
MATH = {"pi": math.pi, "sin": math.sin, "cos": math.cos, "tan": math.tan, "atan2": math.atan2, "sqrt": math.sqrt,
        "radians": math.radians, "degrees": math.degrees, "hypot": math.hypot, "floor": math.floor,
        "ceil": math.ceil}

ALLOWED_NODES = (
    ast.Module, ast.Expr, ast.Assign, ast.AugAssign, ast.AnnAssign, ast.For, ast.If, ast.IfExp, ast.Compare,
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Call, ast.Name, ast.Load, ast.Store, ast.Constant, ast.List, ast.Tuple,
    ast.Dict, ast.Set, ast.Subscript, ast.Slice, ast.keyword, ast.ListComp, ast.DictComp, ast.GeneratorExp,
    ast.comprehension, ast.FunctionDef, ast.arguments, ast.arg, ast.Return, ast.Pass, ast.Break, ast.Continue,
    ast.JoinedStr, ast.FormattedValue, ast.Starred,
    ast.operator, ast.unaryop, ast.cmpop, ast.boolop, ast.expr_context,
)
BLOCKED_NAMES = {"eval", "exec", "open", "compile", "getattr", "setattr", "delattr", "globals", "locals", "vars",
                 "input", "breakpoint", "help", "dir", "type", "object", "super", "memoryview", "bytes", "bytearray",
                 "classmethod", "staticmethod", "property", "exit", "quit", "bpy", "os", "sys", "id", "hash",
                 "iter", "next", "callable", "isinstance", "issubclass", "format", "repr", "chr", "ord"}


class SandboxError(ValueError):
    pass


def check(code: str, api_names: set[str]) -> list[str]:
    """Problems with `code` (empty list = allowed). api_names = toolkit functions it may call."""
    if len(code) > MAX_CODE_CHARS:
        return [f"code is {len(code)} characters; limit {MAX_CODE_CHARS}"]
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        return [f"syntax error line {exc.lineno}: {exc.msg}"]
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    callable_names = set(api_names) | set(SAFE_BUILTINS) | set(MATH) | defined
    errs = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", "?")
        if not isinstance(node, ALLOWED_NODES):
            what = type(node).__name__
            hint = " (attribute access is not allowed; use toolkit functions such as pin('q1.B'))" \
                if isinstance(node, ast.Attribute) else ""
            errs.append(f"line {line}: {what} is not allowed{hint}")
            continue
        if isinstance(node, ast.FunctionDef) and (node.decorator_list or node.name.startswith("_")):
            errs.append(f"line {line}: decorators / private function names are not allowed")
        if isinstance(node, ast.Name):
            if node.id.startswith("_") or node.id in BLOCKED_NAMES:
                errs.append(f"line {line}: name {node.id!r} is not allowed")
        if isinstance(node, ast.arg) and node.arg.startswith("_"):
            errs.append(f"line {line}: argument name {node.arg!r} is not allowed")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                errs.append(f"line {line}: only direct calls of toolkit/builtin functions are allowed")
            elif node.func.id not in callable_names:
                errs.append(f"line {line}: {node.func.id}() is not a toolkit function")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) > 400:
            errs.append(f"line {line}: string constant too long")
    return errs


def _range(*args):
    r = range(*args)
    if len(r) > MAX_RANGE:
        raise SandboxError(f"range of {len(r)} items exceeds {MAX_RANGE}")
    return r


def run(code: str, api: dict) -> None:
    """Check, then execute `code` with ONLY the toolkit api + safe builtins + math in scope."""
    errs = check(code, set(api))
    if errs:
        raise SandboxError("; ".join(errs[:8]))
    builtins = {k: v for k, v in SAFE_BUILTINS.items() if v is not None}
    builtins["range"] = _range
    scope = {"__builtins__": builtins, **MATH, **api}
    exec(compile(code, "<builder>", "exec"), scope, scope)  # noqa: S102 - allowlisted code, isolated process
