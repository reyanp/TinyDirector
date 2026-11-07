"""Pure-Python reference executor for scene plans.

This is NOT Blender. It is used for (a) offline tests, (b) the --simulate CLI
mode, and (c) dev/fake_worker.py, which stands in for the Blender worker on
the inbox/outbox bridge. It also documents the exact semantics the real
Blender engine should implement. Output is always labeled SIMULATED.

Semantics (mirrored by blender_engine/engine.py):
- All-or-nothing: every op is checked first; if any is invalid nothing is applied.
- animate: keys start_frame (the value the channel ACTUALLY has there) and end_frame ("to"),
  replacing keys strictly between them, so segments chain.
- bounce: Z keys ground/peak/ground... for `cycles` bounces, ends at the start height.
- modify on an animated channel shifts the whole animation instead of wiping it.
Approximation: between keys the simulator interpolates linearly (Blender uses eased curves);
key values themselves are exact.
"""

from __future__ import annotations

import copy

DEFAULT_COLOR = "#BBBBBB"
DEFAULT_SCENE = {"frame_start": 1, "frame_end": 250, "frame_current": 1, "fps": 24}
BOUNCE_DEFAULTS = {"height": 1.0, "start_frame": 1, "frames_per_cycle": 24}
AXES = "xyz"


def _check(op, live: set, i: int) -> str | None:
    kind, oid = op.get("op"), op.get("id")
    if kind not in ("add", "modify", "delete", "animate", "bounce"):
        return f"op {i}: unsupported op {kind}"
    if kind == "add":
        return f"op {i}: {oid} already exists" if oid in live else None
    return None if oid in live else f"op {i}: unknown object {oid}"


def _channel(obj: dict, prop: str, axis: int, create: bool = False) -> list | None:
    for ch in obj.get("animation", []):
        if ch["property"] == prop and ch["axis"] == AXES[axis]:
            return ch["keys"]
    if not create:
        return None
    ch = {"property": prop, "axis": AXES[axis], "keys": []}
    obj.setdefault("animation", []).append(ch)
    return ch["keys"]


def _static(obj: dict, prop: str, axis: int) -> float:
    return float(obj["scale"]) if prop == "scale" else float(obj[prop][axis])


def value_at(obj: dict, prop: str, axis: int, frame: float) -> float:
    keys = _channel(obj, prop, axis)
    if not keys:
        return _static(obj, prop, axis)
    if frame <= keys[0][0]:
        return keys[0][1]
    for (f0, v0), (f1, v1) in zip(keys, keys[1:]):
        if f0 <= frame <= f1:
            return v0 + (v1 - v0) * (frame - f0) / (f1 - f0)
    return keys[-1][1]


def _put(keys: list, frame: float, value: float) -> None:
    keys[:] = [k for k in keys if k[0] != frame] + [[frame, round(value, 4)]]
    keys.sort(key=lambda k: k[0])


def _animate(obj: dict, op: dict) -> None:
    start, end = op["start_frame"], op["end_frame"]
    begin = [value_at(obj, op["property"], a, start) for a in range(3)]
    for a in range(3):
        keys = _channel(obj, op["property"], a, create=True)
        keys[:] = [k for k in keys if not start < k[0] < end]
        _put(keys, start, begin[a])
        _put(keys, end, float(op["to"][a]))


def _bounce(obj: dict, op: dict) -> None:
    b = {**BOUNCE_DEFAULTS, **op}
    start, period, height = b["start_frame"], b["frames_per_cycle"], float(b["height"])
    end = start + b["cycles"] * period
    base = value_at(obj, "position", 2, start)
    keys = _channel(obj, "position", 2, create=True)
    keys[:] = [k for k in keys if not start <= k[0] <= end]
    for c in range(b["cycles"]):
        f0 = start + c * period
        _put(keys, f0, base)
        _put(keys, f0 + period / 2, base + height)
    _put(keys, end, base)


def _modify(obj: dict, changes: dict, frame: int) -> None:
    for key, val in changes.items():
        if key == "color":
            obj["color"] = val
            continue
        target = [float(val)] * 3 if key == "scale" else [float(v) for v in val]
        for a in range(3):
            keys = _channel(obj, key, a)
            if keys:
                current = value_at(obj, key, a, frame)
                for k in keys:
                    if key == "scale":
                        k[1] = round(k[1] * target[a] / current if current else target[a], 4)
                    else:
                        k[1] = round(k[1] + target[a] - current, 4)
        obj[key] = float(val) if key == "scale" else list(val)


def _fit_timeline(objs: list[dict], scene: dict) -> None:
    frames = [k[0] for o in objs for ch in o.get("animation", []) for k in ch["keys"]]
    if frames:
        scene["frame_start"] = int(min(frames))
        scene["frame_end"] = max(scene["frame_start"] + 1, int(max(frames) + 0.999))


def apply_plan(scene_objects: list[dict], plan: dict, scene: dict | None = None
               ) -> tuple[list[dict], int, list[str]]:
    """Return (new_scene_objects, applied_count, errors). Input is not mutated."""
    new, applied, errors, _rows, _scene = apply_plan_full(scene_objects, plan, scene)
    return new, applied, errors


def apply_plan_full(scene_objects: list[dict], plan: dict, scene: dict | None = None):
    """(new_objects, applied, errors, op_results, scene_info); all-or-nothing."""
    scene = dict(scene or DEFAULT_SCENE)
    ops = plan.get("operations", [])
    live, errors = {o["id"] for o in scene_objects}, []
    for i, op in enumerate(ops):
        err = _check(op, live, i)
        if err:
            errors.append(err)
        elif op["op"] == "add":
            live.add(op["id"])
        elif op["op"] == "delete":
            live.discard(op["id"])
    if errors:
        bad = {int(e.split()[1].rstrip(":")): e for e in errors}
        rows = [{"index": i, "op": str(op.get("op")), "id": str(op.get("id")), "ok": False,
                 "error": bad.get(i, "not applied: plan rejected")} for i, op in enumerate(ops)]
        return copy.deepcopy(scene_objects), 0, errors + ["plan rejected before execution; scene unchanged"], rows, scene

    objs = {o["id"]: copy.deepcopy(o) for o in scene_objects}
    order = [o["id"] for o in scene_objects]
    rows = []
    for i, op in enumerate(ops):
        kind, oid = op["op"], op["id"]
        if kind == "add":
            objs[oid] = {
                "id": oid, "asset": op["asset"], "color": op.get("color", DEFAULT_COLOR),
                "position": list(op["position"]), "rotation": list(op.get("rotation", [0, 0, 0])),
                "scale": op.get("scale", 1), "name": oid, "type": "MESH", "parent": None, "material": f"td_{oid}",
            }
            order.append(oid)
        elif kind == "modify":
            _modify(objs[oid], op["set"], scene["frame_current"])
        elif kind == "delete":
            del objs[oid]
            order.remove(oid)
        elif kind == "animate":
            _animate(objs[oid], op)
        elif kind == "bounce":
            _bounce(objs[oid], op)
        if oid in objs and objs[oid].get("animation"):
            objs[oid]["animated"] = True
        rows.append({"index": i, "op": kind, "id": oid, "ok": True})
    out = [objs[i] for i in order]
    if any(op["op"] in ("animate", "bounce", "delete") for op in ops):
        _fit_timeline(out, scene)
    return out, len(ops), [], rows, scene


def make_result(request_id: str, prev_version: int, scene_objects: list[dict], plan: dict,
                scene: dict | None = None) -> dict:
    new_objs, applied, errors, rows, scene_info = apply_plan_full(scene_objects, plan, scene)
    return {
        "schema_version": 1,
        "request_id": request_id,
        "kind": "plan",
        "status": "error" if errors else "ok",
        "applied": applied,
        "errors": errors,
        "op_results": rows,
        "scene_version": prev_version + (1 if applied else 0),
        "scene_objects": new_objs,
        "other_objects": [],
        "scene": scene_info,
        "executor": "simulated",
    }


def inspect(request_id: str, state: dict) -> dict:
    return {"schema_version": 1, "request_id": request_id, "kind": "inspect", "status": "ok", "applied": 0,
            "errors": [], "scene_version": state.get("scene_version", 0),
            "scene_objects": state.get("scene_objects", []), "other_objects": [],
            "scene": state.get("scene", dict(DEFAULT_SCENE)), "executor": "simulated"}
