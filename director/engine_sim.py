"""Pure-Python reference executor for scene plans.

This is NOT Blender. It is used for (a) offline tests, (b) the --simulate CLI
mode, and (c) dev/fake_worker.py, which stands in for the Blender worker on
the inbox/outbox bridge. It also documents the exact semantics the real
Blender engine should implement. Output is always labeled SIMULATED.
"""

from __future__ import annotations

import copy

DEFAULT_COLOR = "#BBBBBB"


def apply_plan(scene_objects: list[dict], plan: dict) -> tuple[list[dict], int, list[str]]:
    """Return (new_scene_objects, applied_count, errors). Input is not mutated."""
    objs = {o["id"]: copy.deepcopy(o) for o in scene_objects}
    order = [o["id"] for o in scene_objects]
    applied, errors = 0, []
    for i, op in enumerate(plan.get("operations", [])):
        kind, oid = op.get("op"), op.get("id")
        if kind == "add":
            if oid in objs:
                errors.append(f"op {i}: {oid} already exists")
                continue
            objs[oid] = {
                "id": oid,
                "asset": op["asset"],
                "color": op.get("color", DEFAULT_COLOR),
                "position": list(op["position"]),
                "rotation": list(op.get("rotation", [0, 0, 0])),
                "scale": op.get("scale", 1),
            }
            order.append(oid)
        elif oid not in objs:
            errors.append(f"op {i}: unknown object {oid}")
            continue
        elif kind == "modify":
            for key, val in op["set"].items():
                objs[oid][key] = list(val) if isinstance(val, list) else val
        elif kind == "delete":
            del objs[oid]
            order.remove(oid)
        elif kind == "animate":
            anim = objs[oid].setdefault("animations", [])
            anim.append({k: op[k] for k in ("property", "start_frame", "end_frame", "to")})
        else:
            errors.append(f"op {i}: unsupported op {kind}")
            continue
        applied += 1
    return [objs[i] for i in order], applied, errors


def make_result(request_id: str, prev_version: int, scene_objects: list[dict], plan: dict) -> dict:
    new_objs, applied, errors = apply_plan(scene_objects, plan)
    status = "ok" if not errors else ("partial" if applied else "error")
    return {
        "schema_version": 1,
        "request_id": request_id,
        "status": status,
        "applied": applied,
        "errors": errors,
        "scene_version": prev_version + (1 if applied else 0),
        "scene_objects": new_objs,
        "executor": "simulated",
    }
