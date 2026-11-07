"""Apply TinyDirector plans to the current Blender scene and read back the REAL state.

Plans are data. Nothing from a plan is ever executed as code. Unknown ops are errors.
Each director object carries custom properties: director_id, director_asset.
"""

from __future__ import annotations

import math

import bmesh
import bpy

from . import assets

ASSETS = set(assets.DEFAULT_COLORS)
OPS = {"add", "modify", "delete", "animate"}
VERSION_KEY = "td_scene_version"


# ---------- color helpers (hex is sRGB; Blender material colors are linear) ----------

def _s2l(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _l2s(c: float) -> float:
    c = max(0.0, min(1.0, c))
    return c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def hex_to_linear(h: str) -> tuple[float, float, float, float]:
    h = h.lstrip("#")
    return tuple(_s2l(int(h[i:i + 2], 16) / 255) for i in (0, 2, 4)) + (1.0,)


def linear_to_hex(rgba) -> str:
    return "#" + "".join(f"{round(_l2s(c) * 255):02X}" for c in rgba[:3])


# ---------- materials ----------

def _material(name: str, hex_color: str, emission: float = 0.0):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    if not mat.use_nodes:
        mat.use_nodes = True
    _set_color(mat, hex_color)
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None and emission > 0:
        for key in ("Emission Color", "Emission"):
            if key in bsdf.inputs:
                bsdf.inputs[key].default_value = hex_to_linear(hex_color)
                break
        if "Emission Strength" in bsdf.inputs:
            bsdf.inputs["Emission Strength"].default_value = emission
    return mat


def _set_color(mat, hex_color: str) -> None:
    lin = hex_to_linear(hex_color)
    mat.diffuse_color = lin  # solid-mode viewport color
    bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.node_tree else None
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = lin


def _main_color(obj) -> str | None:
    if not obj.material_slots or obj.material_slots[0].material is None:
        return None
    mat = obj.material_slots[0].material
    bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.node_tree else None
    rgba = bsdf.inputs["Base Color"].default_value if bsdf is not None else mat.diffuse_color
    return linear_to_hex(rgba)


# ---------- scene access ----------

def director_objects() -> dict:
    return {o["director_id"]: o for o in bpy.data.objects if "director_id" in o}


def _collection():
    col = bpy.data.collections.get("TinyDirector")
    if col is None:
        col = bpy.data.collections.new("TinyDirector")
        bpy.context.scene.collection.children.link(col)
    return col


def _seed(oid: str) -> int:
    return sum(ord(c) * (i + 1) for i, c in enumerate(oid))


def _add(op: dict) -> None:
    oid, asset = op["id"], op["asset"]
    bm = bmesh.new()
    accents = assets.build(asset, bm, seed=_seed(oid))
    mesh = bpy.data.meshes.new(f"{oid}_mesh")
    bm.to_mesh(mesh)
    bm.free()
    for poly in mesh.polygons:
        poly.use_smooth = asset in ("sphere", "rock", "mug")
    obj = bpy.data.objects.new(oid, mesh)
    obj["director_id"] = oid
    obj["director_asset"] = asset
    mesh.materials.append(_material(f"td_{oid}", op.get("color") or assets.DEFAULT_COLORS[asset]))
    for name in accents:
        hx, em = assets.ACCENTS[name]
        mesh.materials.append(_material(f"td_accent_{name}", hx, em))
    _collection().objects.link(obj)
    _transform(obj, op)


def _transform(obj, values: dict) -> None:
    if "position" in values:
        obj.location = values["position"]
    if "rotation" in values:
        obj.rotation_euler = [math.radians(d) for d in values["rotation"]]
    if "scale" in values:
        s = float(values["scale"])
        obj.scale = (s, s, s)


def _modify(obj, changes: dict) -> None:
    _transform(obj, changes)
    if "color" in changes:
        mat = obj.material_slots[0].material if obj.material_slots else None
        if mat is None:
            obj.data.materials.append(_material(f"td_{obj['director_id']}", changes["color"]))
        else:
            _set_color(mat, changes["color"])


def _delete(obj) -> None:
    mesh, mats = obj.data, [s.material for s in obj.material_slots]
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    for m in mats:
        if m is not None and m.users == 0 and m.name.startswith("td_") and not m.name.startswith("td_accent_"):
            bpy.data.materials.remove(m)


def _animate(obj, op: dict) -> None:
    scene = bpy.context.scene
    prop = {"position": "location", "rotation": "rotation_euler", "scale": "scale"}[op["property"]]
    start, end, to = op["start_frame"], op["end_frame"], op["to"]
    obj.keyframe_insert(data_path=prop, frame=start)
    if prop == "rotation_euler":
        target = [math.radians(d) for d in to]  # absolute target, degrees -> radians
    else:
        target = list(to)
    original = list(getattr(obj, prop))
    setattr(obj, prop, target)
    obj.keyframe_insert(data_path=prop, frame=end)
    setattr(obj, prop, original)  # keep the frame-start pose in the live scene
    scene.frame_start = min(scene.frame_start, start)
    scene.frame_end = max(scene.frame_end, end)


def _check(op: dict, live: set, i: int) -> str | None:
    kind = op.get("op")
    if kind not in OPS:
        return f"op {i}: unsupported op {kind!r}"
    oid = op.get("id")
    if not isinstance(oid, str):
        return f"op {i}: missing id"
    if kind == "add":
        if op.get("asset") not in ASSETS:
            return f"op {i}: unsupported asset {op.get('asset')!r}"
        if oid in live:
            return f"op {i}: {oid} already exists"
        if not isinstance(op.get("position"), list) or len(op["position"]) != 3:
            return f"op {i}: add needs position [x,y,z]"
    elif oid not in live:
        return f"op {i}: unknown object {oid}"
    if kind == "modify" and (not isinstance(op.get("set"), dict) or not op["set"]):
        return f"op {i}: modify needs a non-empty 'set'"
    if kind == "animate":
        if op.get("property") not in ("position", "rotation", "scale"):
            return f"op {i}: bad animate property"
        if not (isinstance(op.get("start_frame"), int) and isinstance(op.get("end_frame"), int)
                and 1 <= op["start_frame"] < op["end_frame"]):
            return f"op {i}: bad frame range"
    return None


def apply_plan(plan: dict) -> tuple[int, list[str]]:
    """Apply in order. Each op is validated against the live scene first; bad ops are skipped."""
    applied, errors = 0, []
    if plan.get("schema_version") != 1 or not isinstance(plan.get("operations"), list):
        return 0, ["not a scene_plan_v1 document"]
    for i, op in enumerate(plan["operations"]):
        objs = director_objects()
        err = _check(op, set(objs), i) if isinstance(op, dict) else f"op {i}: not an object"
        if err:
            errors.append(err)
            continue
        try:
            kind = op["op"]
            if kind == "add":
                _add(op)
            elif kind == "modify":
                _modify(objs[op["id"]], op["set"])
            elif kind == "delete":
                _delete(objs[op["id"]])
            elif kind == "animate":
                _animate(objs[op["id"]], op)
            applied += 1
        except Exception as exc:  # report, never crash the worker
            errors.append(f"op {i}: {type(exc).__name__}: {exc}")
    if applied:
        scene = bpy.context.scene
        scene[VERSION_KEY] = int(scene.get(VERSION_KEY, 0)) + 1
    return applied, errors


def read_scene() -> list[dict]:
    """Read state back from Blender itself (not echoed from the plan)."""
    out = []
    for oid, obj in sorted(director_objects().items(), key=lambda kv: kv[1].name):
        out.append({
            "id": oid,
            "asset": obj.get("director_asset", oid.rsplit("_", 1)[0]),
            "color": _main_color(obj) or "#BBBBBB",
            "position": [round(v, 4) for v in obj.location],
            "rotation": [round(math.degrees(v), 3) for v in obj.rotation_euler],
            "scale": round(obj.scale.x, 4),
            **({"animated": True} if obj.animation_data and obj.animation_data.action else {}),
        })
    return out


def scene_version() -> int:
    return int(bpy.context.scene.get(VERSION_KEY, 0))


def make_result(request_id: str, plan: dict) -> dict:
    applied, errors = apply_plan(plan)
    status = "ok" if not errors else ("partial" if applied else "error")
    return {"schema_version": 1, "request_id": request_id, "status": status, "applied": applied,
            "errors": errors, "scene_version": scene_version(), "scene_objects": read_scene(),
            "executor": f"blender {bpy.app.version_string}"}


def reset_scene() -> None:
    for obj in list(director_objects().values()):
        _delete(obj)
    bpy.context.scene[VERSION_KEY] = 0
