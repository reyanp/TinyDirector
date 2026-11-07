"""Load the shared schema and validate plans: JSON Schema first, then scene-aware rules.

The schema is always read from shared/schema/ (never copied), so the shared
contract stays the single source of truth.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

from .paths import PLAN_SCHEMA_PATH, RESULT_SCHEMA_PATH


@lru_cache(maxsize=None)
def load_schema(path: str | Path = PLAN_SCHEMA_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        schema = json.load(f)
    Draft202012Validator.check_schema(schema)
    return schema


def plan_schema() -> dict:
    return load_schema(str(PLAN_SCHEMA_PATH))


def result_schema() -> dict:
    return load_schema(str(RESULT_SCHEMA_PATH))


def allowed_assets() -> list[str]:
    return list(plan_schema()["$defs"]["asset"]["enum"])


def _fmt_path(path: Iterable[Any]) -> str:
    out = "plan"
    for p in path:
        out += f"[{p}]" if isinstance(p, int) else f".{p}"
    return out


def _schema_errors(obj: Any, schema: dict) -> list[str]:
    validator = Draft202012Validator(schema)
    errors = []
    for err in sorted(validator.iter_errors(obj), key=lambda e: list(e.absolute_path)):
        # oneOf failures are vague; surface the best sub-error for the op's declared type.
        if err.validator == "oneOf" and isinstance(err.instance, dict) and "op" in err.instance:
            op = err.instance.get("op")
            if op not in ("add", "modify", "delete", "animate"):
                errors.append(f"{_fmt_path(err.absolute_path)}: unknown op {op!r}; allowed: add, modify, delete, animate")
                continue
            idx = ["add", "modify", "delete", "animate"].index(op)
            sub = [c for c in (err.context or []) if list(c.schema_path)[:1] == [idx]]
            if sub:
                for c in sub:
                    errors.append(f"{_fmt_path(list(err.absolute_path) + list(c.absolute_path))}: {c.message}")
                continue
        errors.append(f"{_fmt_path(err.absolute_path)}: {err.message}")
    return errors


def manifest_objects(manifest: dict | None) -> list[dict]:
    if not manifest:
        return []
    objs = manifest.get("scene_objects", [])
    return objs if isinstance(objs, list) else []


def validate_plan(obj: Any, manifest: dict | None = None, expected_mode: str | None = None) -> list[str]:
    """Return a list of precise, human-readable errors. Empty list == valid."""
    errors = _schema_errors(obj, plan_schema())
    if errors:
        return errors  # semantic checks assume a structurally valid plan

    existing = {o["id"]: o for o in manifest_objects(manifest) if isinstance(o, dict) and "id" in o}
    mode = obj["mode"]
    if expected_mode in ("create", "edit") and mode not in (expected_mode, "clarify"):
        errors.append(f"plan.mode: expected {expected_mode!r} (or 'clarify') but got {mode!r}")

    live = set(existing)          # ids that exist at this point in the plan
    added: set[str] = set()
    deleted: dict[str, str] = {}  # id -> asset

    for i, op in enumerate(obj["operations"]):
        where = f"plan.operations[{i}]"
        kind, oid = op["op"], op["id"]
        if kind == "add":
            prefix = oid.rsplit("_", 1)[0]
            if prefix != op["asset"]:
                errors.append(f"{where}.id: {oid!r} must start with its asset name, e.g. '{op['asset']}_N'")
            if oid in added:
                errors.append(f"{where}.id: duplicate add id {oid!r}")
            elif oid in existing:
                errors.append(f"{where}.id: {oid!r} already exists in the scene; use 'modify' or pick a new id")
            if op["asset"] in deleted.values() and mode == "edit":
                errors.append(f"{where}: do not delete and re-create a {op['asset']}; use 'modify' on the existing object")
            added.add(oid)
            live.add(oid)
        else:
            if oid in deleted:
                errors.append(f"{where}.id: {oid!r} was deleted earlier in this plan")
            elif oid not in live:
                known = ", ".join(sorted(live)) or "(scene is empty)"
                errors.append(f"{where}.id: unknown object {oid!r}; known ids: {known}")
            if kind == "delete" and oid in live:
                deleted[oid] = (existing.get(oid) or {}).get("asset", oid.rsplit("_", 1)[0])
                live.discard(oid)
            if kind == "animate" and op["end_frame"] <= op["start_frame"]:
                errors.append(f"{where}: end_frame ({op['end_frame']}) must be greater than start_frame ({op['start_frame']})")
            if kind == "animate" and op["property"] == "scale" and min(op["to"]) <= 0:
                errors.append(f"{where}.to: scale values must be > 0")
    return errors


def validate_result(obj: Any) -> list[str]:
    return _schema_errors(obj, result_schema())


def pretty(plan: dict) -> str:
    """Human-readable one-line-per-operation rendering. Never evaluates anything."""
    lines = [f"[{plan.get('mode')}] {plan.get('summary', '')}  (request {plan.get('request_id')})"]
    if plan.get("mode") == "clarify":
        lines.append(f"  ? {plan.get('message', '')}")
    for op in plan.get("operations", []):
        k = op.get("op")
        if k == "add":
            extra = f" color={op.get('color', '-')} pos={op.get('position')} scale={op.get('scale', 1)}"
            lines.append(f"  + add    {op['id']:<12} {op['asset']}{extra}")
        elif k == "modify":
            changes = ", ".join(f"{key}={val}" for key, val in op["set"].items())
            lines.append(f"  ~ modify {op['id']:<12} {changes}")
        elif k == "delete":
            lines.append(f"  - delete {op['id']}")
        elif k == "animate":
            lines.append(f"  > animate {op['id']:<11} {op['property']} -> {op['to']} frames {op['start_frame']}-{op['end_frame']}")
    return "\n".join(lines)
