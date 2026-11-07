"""Apply TinyDirector plans to the current Blender scene and read back the REAL state.

Plans are data. Nothing from a plan is ever executed as code. Unknown ops are errors.
Each director object carries custom properties: director_id, director_asset.

Plans are applied all-or-nothing: every op is pre-checked against the live scene first, and if
any op is invalid NOTHING is applied. If Blender raises mid-plan, the touched objects are rolled
back to their previous state.
"""

from __future__ import annotations

import math

import bmesh
import bpy

from . import assets, library

ASSETS = set(assets.DEFAULT_COLORS)
OPS = {"add", "modify", "delete", "animate", "bounce"}
VERSION_KEY = "td_scene_version"
PROPS = {"position": "location", "rotation": "rotation_euler", "scale": "scale"}
PROP_NAMES = {v: k for k, v in PROPS.items()}
BOUNCE_DEFAULTS = {"height": 1.0, "start_frame": 1, "frames_per_cycle": 24}
MAX_FRAME = 1000


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
    if library.set_tint(mat, lin):  # textured library asset: tint, don't flatten the texture
        return
    mat.diffuse_color = lin  # solid-mode viewport color
    bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.node_tree else None
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = lin


def _main_color(obj) -> str | None:
    if not obj.material_slots or obj.material_slots[0].material is None:
        return None
    mat = obj.material_slots[0].material
    tint = library.get_tint(mat)
    if tint == "untinted":
        return obj.get("director_color")  # library asset never recolored: report the color it was added with
    if tint is not None:
        return linear_to_hex(tint)
    bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.node_tree else None
    rgba = bsdf.inputs["Base Color"].default_value if bsdf is not None else mat.diffuse_color
    return linear_to_hex(rgba)


# ---------- animation helpers (Blender 4.x legacy actions and 5.x layered/slotted actions) ----------

def fcurves(obj) -> list:
    ad = obj.animation_data
    if ad is None or ad.action is None:
        return []
    act = ad.action
    try:
        from bpy_extras.anim_utils import action_get_channelbag_for_slot  # Blender 4.4+
        bag = action_get_channelbag_for_slot(act, ad.action_slot) if ad.action_slot else None
        if bag is not None:
            return list(bag.fcurves)
    except ImportError:
        pass
    return list(getattr(act, "fcurves", []))  # Blender < 4.4


def _fcurve(obj, data_path: str, index: int, create: bool = False):
    for fc in fcurves(obj):
        if fc.data_path == data_path and fc.array_index == index:
            return fc
    if not create:
        return None
    obj.keyframe_insert(data_path=data_path, index=index, frame=bpy.context.scene.frame_current)
    return _fcurve(obj, data_path, index)


def _value_at(obj, data_path: str, index: int, frame: float) -> float:
    """The channel's value at `frame`: from its F-curve if animated, else the static value."""
    fc = _fcurve(obj, data_path, index)
    return fc.evaluate(frame) if fc is not None else getattr(obj, data_path)[index]


def _clear_keys(fc, lo: float, hi: float, inclusive: bool) -> None:
    for kp in reversed(list(fc.keyframe_points)):
        f = kp.co[0]
        if (lo <= f <= hi) if inclusive else (lo < f < hi):
            fc.keyframe_points.remove(kp)


def _key(fc, frame: float, value: float, interpolation: str = "BEZIER", easing: str = "AUTO"):
    kp = fc.keyframe_points.insert(frame, value, options={"FAST"})  # replaces a key on the same frame
    kp.interpolation = interpolation
    if interpolation not in ("BEZIER", "CONSTANT", "LINEAR"):
        kp.easing = easing
    return kp


def _remove_seed_key(fc, frame: float, keep: set) -> None:
    """_fcurve(create=True) seeds one key at frame_current; drop it unless the op set a key there."""
    if frame in keep:
        return
    for kp in list(fc.keyframe_points):
        if kp.co[0] == frame:
            fc.keyframe_points.remove(kp)
            return


def _update_frame_range() -> None:
    """Fit the timeline to the director's animations so playback loops over the real motion."""
    lo, hi = None, None
    for obj in director_objects().values():
        for fc in fcurves(obj):
            if len(fc.keyframe_points):
                a, b = fc.range()
                lo = a if lo is None else min(lo, a)
                hi = b if hi is None else max(hi, b)
    if lo is not None:
        scene = bpy.context.scene
        scene.frame_start = max(0, int(math.floor(lo)))
        scene.frame_end = max(scene.frame_start + 1, int(math.ceil(hi)))


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


# ---------- operations ----------

def _add(op: dict) -> None:
    oid, asset = op["id"], op["asset"]
    obj = library.instantiate(asset, oid)
    if obj is not None:  # prebuilt library mesh (TD_ASSET_LIBRARY=1); same ids/props/ops as procedural
        obj["director_id"] = oid
        obj["director_asset"] = asset
        if op.get("color"):
            _set_color(obj.material_slots[0].material, op["color"])
        else:
            obj["director_color"] = "#FFFFFF"  # untinted = the asset's own look
        _collection().objects.link(obj)
        _transform(obj, op)
        return
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
    """Change only the given keys. On an animated channel the whole animation is shifted
    (position/rotation: offset; scale: ratio) so edits never wipe existing keyframes."""
    targets = {}
    if "position" in changes:
        targets["location"] = list(changes["position"])
    if "rotation" in changes:
        targets["rotation_euler"] = [math.radians(d) for d in changes["rotation"]]
    if "scale" in changes:
        targets["scale"] = [float(changes["scale"])] * 3
    frame = bpy.context.scene.frame_current
    for path, target in targets.items():
        live = list(getattr(obj, path))
        for i in range(3):
            fc = _fcurve(obj, path, i)
            if fc is None:
                live[i] = target[i]
                continue
            current = fc.evaluate(frame)
            for kp in fc.keyframe_points:
                for attr in ("co", "handle_left", "handle_right"):
                    pt = getattr(kp, attr)
                    if path == "scale":
                        pt[1] = pt[1] * (target[i] / current) if abs(current) > 1e-9 else target[i]
                    else:
                        pt[1] = pt[1] + (target[i] - current)
            fc.update()
            live[i] = target[i]
        setattr(obj, path, live)
    if "color" in changes:
        mat = obj.material_slots[0].material if obj.material_slots else None
        if mat is None:
            obj.data.materials.append(_material(f"td_{obj['director_id']}", changes["color"]))
        else:
            _set_color(mat, changes["color"])


def _delete(obj) -> None:
    mesh, mats = obj.data, [s.material for s in obj.material_slots]
    action = obj.animation_data.action if obj.animation_data else None
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    if action is not None and action.users == 0:
        bpy.data.actions.remove(action)
    for m in mats:
        if m is not None and m.users == 0 and m.name.startswith("td_") and not m.name.startswith("td_accent_"):
            bpy.data.materials.remove(m)


def _animate(obj, op: dict) -> None:
    """Keyframe start_frame -> end_frame on all 3 axes. The start key is the value the object
    ACTUALLY has at start_frame (from its existing animation, if any), so segments chain:
    1-24 up, then 24-48 down works. Keys strictly inside the range are replaced."""
    path = PROPS[op["property"]]
    start, end = op["start_frame"], op["end_frame"]
    target = [math.radians(d) for d in op["to"]] if path == "rotation_euler" else [float(v) for v in op["to"]]
    begin = [_value_at(obj, path, i, start) for i in range(3)]  # read BEFORE creating any curves
    seed_frame = bpy.context.scene.frame_current
    for i in range(3):
        existed = _fcurve(obj, path, i) is not None
        fc = _fcurve(obj, path, i, create=True)
        if not existed:
            _remove_seed_key(fc, seed_frame, keep=set())
        _clear_keys(fc, start, end, inclusive=False)
        _key(fc, start, begin[i])
        _key(fc, end, target[i])
        fc.update()


def _bounce(obj, op: dict) -> None:
    """`cycles` hops on Z. Ground keys ease OUT (fast take-off, slowing to the top) and peak
    keys ease IN (speeding up on the way down), i.e. a ballistic arc on every bounce."""
    o = {**BOUNCE_DEFAULTS, **op}
    start, cycles, height, period = o["start_frame"], o["cycles"], float(o["height"]), o["frames_per_cycle"]
    end = start + cycles * period
    base = _value_at(obj, "location", 2, start)
    seed_frame = bpy.context.scene.frame_current
    existed = _fcurve(obj, "location", 2) is not None
    fc = _fcurve(obj, "location", 2, create=True)
    _clear_keys(fc, start, end, inclusive=True)
    frames = set()
    for c in range(cycles):
        f0 = start + c * period
        _key(fc, f0, base, "QUAD", "EASE_OUT")
        _key(fc, f0 + period / 2, base + height, "QUAD", "EASE_IN")
        frames |= {f0, f0 + period / 2}
    _key(fc, end, base, "QUAD", "EASE_OUT")
    frames.add(end)
    if not existed:
        _remove_seed_key(fc, seed_frame, keep=frames)
    fc.update()


# ---------- validation against the live scene ----------

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
        if op.get("property") not in PROPS:
            return f"op {i}: bad animate property"
        if not (isinstance(op.get("start_frame"), int) and isinstance(op.get("end_frame"), int)
                and 1 <= op["start_frame"] < op["end_frame"] <= MAX_FRAME):
            return f"op {i}: bad frame range"
        if not (isinstance(op.get("to"), list) and len(op["to"]) == 3):
            return f"op {i}: animate needs 'to' [x,y,z]"
    if kind == "bounce":
        o = {**BOUNCE_DEFAULTS, **op}
        ints = all(isinstance(o[k], int) and not isinstance(o[k], bool)
                   for k in ("cycles", "start_frame", "frames_per_cycle"))
        if not ints or o["cycles"] < 1 or o["frames_per_cycle"] < 2 or o["start_frame"] < 1:
            return f"op {i}: bounce needs integer cycles>=1, start_frame>=1, frames_per_cycle>=2"
        if not isinstance(o["height"], (int, float)) or o["height"] <= 0:
            return f"op {i}: bounce height must be > 0"
        if o["start_frame"] + o["cycles"] * o["frames_per_cycle"] > MAX_FRAME:
            return f"op {i}: bounce ends after frame {MAX_FRAME}"
    return None


def preflight(plan: dict) -> list[str]:
    """Check every op against the live scene, tracking ids added/deleted earlier in the plan."""
    if plan.get("schema_version") != 1 or not isinstance(plan.get("operations"), list):
        return ["not a scene_plan_v1 document"]
    live, errors = set(director_objects()), []
    for i, op in enumerate(plan["operations"]):
        err = _check(op, live, i) if isinstance(op, dict) else f"op {i}: not an object"
        if err:
            errors.append(err)
            continue
        if op["op"] == "add":
            live.add(op["id"])
        elif op["op"] == "delete":
            live.discard(op["id"])
    return errors


# ---------- rollback (only used if Blender raises mid-plan) ----------

def _capture() -> dict:
    scene = bpy.context.scene
    snap = {"frame": (scene.frame_start, scene.frame_end), "objects": {}}
    for oid, obj in director_objects().items():
        action = obj.animation_data.action if obj.animation_data else None
        snap["objects"][oid] = {
            "loc": tuple(obj.location), "rot": tuple(obj.rotation_euler), "scale": tuple(obj.scale),
            "color": _main_color(obj), "action": action.copy() if action is not None else None,
        }
    return snap


def _restore(snap: dict) -> None:
    for oid, obj in list(director_objects().items()):
        if oid not in snap["objects"]:
            _delete(obj)  # added by the failed plan
    for oid, obj in director_objects().items():
        s = snap["objects"][oid]
        current = obj.animation_data.action if obj.animation_data else None
        if s["action"] is None:
            if obj.animation_data:
                obj.animation_data_clear()
        else:
            obj.animation_data_create()
            obj.animation_data.action = s["action"]
            if hasattr(obj.animation_data, "action_slot") and obj.animation_data.action_slot is None:
                slots = list(obj.animation_data.action_suitable_slots)
                if slots:
                    obj.animation_data.action_slot = slots[0]
            s["action"] = None  # now owned by the object, don't discard it below
        if current is not None and current.users == 0:
            bpy.data.actions.remove(current)
        obj.location, obj.rotation_euler, obj.scale = s["loc"], s["rot"], s["scale"]
        if s["color"] and obj.material_slots and obj.material_slots[0].material:
            _set_color(obj.material_slots[0].material, s["color"])
    bpy.context.scene.frame_start, bpy.context.scene.frame_end = snap["frame"]


def _discard(snap: dict) -> None:
    for s in snap["objects"].values():
        if s["action"] is not None and s["action"].users == 0:
            bpy.data.actions.remove(s["action"])


# ---------- apply ----------

def apply_plan(plan: dict) -> tuple[int, list[str], list[dict]]:
    """All-or-nothing. Returns (applied_count, errors, op_results)."""
    ops = plan.get("operations") if isinstance(plan.get("operations"), list) else []

    def _row(i, op, ok, error=None):
        row = {"index": i, "op": str(op.get("op")) if isinstance(op, dict) else "?", "ok": ok}
        if isinstance(op, dict) and isinstance(op.get("id"), str):
            row["id"] = op["id"]
        if error:
            row["error"] = error
        return row

    errors = preflight(plan)
    if errors:
        bad = {int(e.split()[1].rstrip(":")): e for e in errors if e.startswith("op ")}
        rows = [_row(i, op, False, bad.get(i, "not applied: plan rejected")) for i, op in enumerate(ops)]
        return 0, errors + ["plan rejected before execution; scene unchanged"], rows

    snap = _capture()
    rows, i = [], 0
    try:
        for i, op in enumerate(ops):
            obj = None if op["op"] == "add" else director_objects()[op["id"]]
            {"add": lambda: _add(op), "modify": lambda: _modify(obj, op["set"]), "delete": lambda: _delete(obj),
             "animate": lambda: _animate(obj, op), "bounce": lambda: _bounce(obj, op)}[op["op"]]()
            rows.append(_row(i, op, True))
    except Exception as exc:  # report, roll back, never crash the worker
        msg = f"op {i}: {type(exc).__name__}: {exc}"
        _restore(snap)
        rows = [_row(j, op, False, msg if j == i else "rolled back") for j, op in enumerate(ops)]
        return 0, [msg, "rolled back; scene unchanged"], rows
    _discard(snap)
    if any(op["op"] in ("animate", "bounce", "delete") for op in ops):
        _update_frame_range()
    scene = bpy.context.scene
    scene.frame_set(scene.frame_current)  # re-evaluate animation so live values match the keys
    scene[VERSION_KEY] = int(scene.get(VERSION_KEY, 0)) + 1
    return len(ops), [], rows


# ---------- read back ----------

def _num(v: float, nd: int = 4):
    r = round(v, nd)
    return int(r) if r == int(r) else r


def read_animation(obj) -> list[dict]:
    out = []
    for fc in sorted(fcurves(obj), key=lambda f: (f.data_path, f.array_index)):
        prop = PROP_NAMES.get(fc.data_path)
        if prop is None or fc.array_index > 2 or not len(fc.keyframe_points):
            continue
        conv = math.degrees if fc.data_path == "rotation_euler" else float
        out.append({"property": prop, "axis": "xyz"[fc.array_index],
                    "keys": [[_num(kp.co[0], 2), _num(conv(kp.co[1]), 4)] for kp in fc.keyframe_points][:80]})
    return out


def _parent_ref(obj):
    p = obj.parent
    return None if p is None else str(p.get("director_id", p.name))


def read_scene() -> list[dict]:
    """Read state back from Blender itself (not echoed from the plan)."""
    out = []
    for oid, obj in sorted(director_objects().items(), key=lambda kv: kv[1].name):
        anim = read_animation(obj)
        mat = obj.material_slots[0].material if obj.material_slots else None
        row = {
            "id": oid,
            "asset": obj.get("director_asset", oid.rsplit("_", 1)[0]),
            "color": _main_color(obj) or "#BBBBBB",
            "position": [round(v, 4) for v in obj.location],
            "rotation": [round(math.degrees(v), 3) for v in obj.rotation_euler],
            "scale": round(obj.scale.x, 4),
            "name": obj.name,
            "type": obj.type,
            "parent": _parent_ref(obj),
        }
        if mat is not None:
            row["material"] = mat.name
        if obj.get("director_library"):
            row["library"] = str(obj["director_library"])
        if anim:
            row["animated"] = True
            row["animation"] = anim
        out.append(row)
    return out


def read_other_objects() -> list[dict]:
    """Non-director objects (user-made, cameras, lights) as read-only context. TD_ helpers are skipped."""
    return [{"name": o.name, "type": o.type, "position": [round(v, 3) for v in o.location],
             "parent": _parent_ref(o)}
            for o in sorted(bpy.data.objects, key=lambda o: o.name)
            if "director_id" not in o and not o.name.startswith("TD_") and o.users_scene]


def read_scene_info() -> dict:
    s = bpy.context.scene
    return {"frame_start": s.frame_start, "frame_end": s.frame_end, "frame_current": s.frame_current,
            "fps": round(s.render.fps / s.render.fps_base, 3)}


def scene_version() -> int:
    return int(bpy.context.scene.get(VERSION_KEY, 0))


def inspect(request_id: str) -> dict:
    """Read-only snapshot of the live scene in scene_result_v1 form. Applies nothing."""
    return {"schema_version": 1, "request_id": request_id, "kind": "inspect", "status": "ok", "applied": 0,
            "errors": [], "scene_version": scene_version(), "scene_objects": read_scene(),
            "other_objects": read_other_objects(), "scene": read_scene_info(),
            "executor": f"blender {bpy.app.version_string}"}


def make_result(request_id: str, plan: dict) -> dict:
    applied, errors, rows = apply_plan(plan)
    return {"schema_version": 1, "request_id": request_id, "kind": "plan", "status": "error" if errors else "ok",
            "applied": applied, "errors": errors, "op_results": rows, "scene_version": scene_version(),
            "scene_objects": read_scene(), "other_objects": read_other_objects(), "scene": read_scene_info(),
            "executor": f"blender {bpy.app.version_string}"}


def reset_scene() -> None:
    for obj in list(director_objects().values()):
        _delete(obj)
    bpy.context.scene[VERSION_KEY] = 0
