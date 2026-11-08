"""Scene direction for the lesson on stage: camera focus, a highlight halo, and the on-screen caption.

Display only. Nothing here changes a lesson value (resistance, current, labels with numbers): the
director computes those and sends circuit / lesson requests. A {"kind": "direct"} request
(shared/schema/direct_request_v1.json, additive) only moves the camera, rings a part, or replaces the
subtitle caption. In the GUI the camera glides there (bpy.app.timers, no keyframes, so playback of the
lesson animation is not disturbed); in background mode the final pose is set at once.

Targets: the LED circuit's "battery" / "resistor" / "led" / "current" (the whole loop) / "overview"
(the lesson camera's original framing), or any part id of a generated lesson (TD_Lesson collection).
While a split-reality comparison is shown (compare.py): "compare" (both sides; also what "overview" means
then), "baseline", "alternative".

A request may also carry a callout: a large measurement in the top-right corner of the camera view
({"value": "7.0 V", "note": "across the resistor"}); the director formats it from the calculation, the engine
only displays it. Focusing "current" makes the current arrows glow brighter (emphasis only, same speed).
"""

from __future__ import annotations

import math
import re

import bpy
from mathutils import Matrix, Vector

COLLECTION = "TD_Direction"
HOME_KEY = "td_home_matrix"
FOCUS_KEY = "td_focus"
CIRCUIT_TARGETS = {  # the part + its measurement label (the numbers are part of what we look at)
    "battery": ["battery_1", "label_battery"],
    "resistor": ["resistor_1", "label_resistor"],
    "led": ["led_1", "label_led"],
    "current": ["wire_1", "wire_2", "wire_3", "flow_1", "label_current"],
}
HALO_PART = {"battery": "battery_1", "resistor": "resistor_1", "led": "led_1"}
FRAME_X = (0.2, 0.8)  # where the target may sit in the image (the HUD title / caption use the edges)
FRAME_Y = (0.22, 0.74)
WIDE_X, WIDE_Y = (0.03, 0.97), (0.17, 0.84)  # a side-by-side comparison uses (almost) the whole width
MIN_SPAN = 3.4  # a close-up shows at least this much of the board (m), so the part keeps its context
HALO = {"battery", "resistor", "led"}  # a ring around one part; "current" frames the loop instead
TARGET_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
HALO_HEX = "#35D0C0"
COMPARE_TARGETS = ("compare", "baseline", "alternative")
CALLOUT = {"value": ("TD_CalloutValue", 0.13, "#FFD45C", (1.42, 0.70)),
           "note": ("TD_CalloutNote", 0.044, "#E9EEF3", (1.42, 0.535))}
FLOW_EMPHASIS = 3.0  # x emission of the current arrows while the camera is on "current"
_TWEEN: dict = {}


class DirectionError(ValueError):
    pass


def _lin(hex_color: str):
    from blender_engine.circuit import _lin as lin
    return lin(hex_color)


def _camera():
    return bpy.context.scene.camera


def _circuit_objects(ids: list[str]) -> list:
    return [o for o in bpy.data.objects if o.get("circuit_id") in ids]


def _lesson_part(pid: str) -> list:
    return [o for o in bpy.data.objects if o.get("lesson_id") == pid and o.get("lesson_kind") == "part"]


def _with_children(objs: list) -> list:
    out, todo = [], list(objs)
    while todo:
        o = todo.pop()
        if o not in out:
            out.append(o)
            todo.extend(o.children)
    return out


def _rest_world(o) -> Matrix:
    """World matrix with every pop-in (scale) animation at its final value: the framing must not depend on
    the frame the timeline happens to be on (at frame 1 the lesson's parts are still scaled to 0)."""
    from blender_engine import engine
    sc = Vector(o.scale)
    if o.animation_data is not None:
        for fc in engine.fcurves(o):
            if fc.data_path == "scale" and len(fc.keyframe_points):
                sc[fc.array_index] = fc.keyframe_points[-1].co[1]
    rot = o.rotation_quaternion if o.rotation_mode == "QUATERNION" else o.rotation_euler.to_quaternion()
    basis = Matrix.LocRotScale(o.location, rot, sc)
    if o.parent is not None:
        return _rest_world(o.parent) @ o.matrix_parent_inverse @ basis
    return basis


def _points(o) -> list[Vector]:
    """World-space points that bound what the viewer sees (in the rest pose): spline points for curves (their
    bound_box includes handles), the evaluated mesh for text, the bound box otherwise."""
    mw = _rest_world(o)
    if o.type == "CURVE" and o.data.splines:
        pts = []
        for sp in o.data.splines:
            pts += [mw @ Vector(p.co[:3]) for p in sp.points] + [mw @ p.co for p in sp.bezier_points]
        if pts:
            return pts
    if o.type == "FONT":
        ev = o.evaluated_get(bpy.context.evaluated_depsgraph_get())
        me = ev.to_mesh()
        try:
            if me is not None and len(me.vertices):
                return [mw @ v.co for v in me.vertices]
        finally:
            ev.to_mesh_clear()
    return [mw @ Vector(c) for c in o.bound_box]


def _bbox(objs: list) -> tuple[Vector, Vector] | None:
    pts = []
    for o in _with_children(objs):
        if o.type not in {"MESH", "CURVE", "SURFACE", "META", "FONT"} or o.get("plate_hud") or o.hide_render:
            continue
        if o.type == "MESH" and o.name.endswith("_plate"):
            continue
        pts += _points(o)
    if not pts:
        return None
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


def _compare_active() -> bool:
    from blender_engine import compare
    return compare.active()


def targets() -> list[str]:
    """What can be focused right now."""
    out = ["overview"]
    if _circuit_objects(["resistor_1"]):
        out += list(CIRCUIT_TARGETS)
    if _compare_active():
        out += list(COMPARE_TARGETS)
    out += sorted({o["lesson_id"] for o in bpy.data.objects if o.get("lesson_kind") == "part" and o.get("lesson_id")})
    return out + _scam().targets()


def _scam():
    from blender_engine import scam
    return scam


def _objects_for(target: str) -> list:
    if target in _scam().targets():  # the Spot the Scam phone (and its hypothetical outcomes)
        return _scam().objects_for(target)
    if target in COMPARE_TARGETS:
        if not _compare_active():
            return []
        from blender_engine import compare
        sides = ("baseline", "alternative") if target == "compare" else (target,)
        return [o for side in sides for o in compare.side_objects(side)]
    if target in CIRCUIT_TARGETS:
        objs = _circuit_objects(CIRCUIT_TARGETS[target])
        if objs:
            return objs
    return _lesson_part(target)


def _home(cam) -> Matrix:
    if HOME_KEY not in cam:
        cam[HOME_KEY] = [v for row in cam.matrix_world for v in row]
    m = list(cam[HOME_KEY])
    return Matrix([m[0:4], m[4:8], m[8:12], m[12:16]])


def _framing(cam, lo: Vector, hi: Vector, fx=FRAME_X, fy=FRAME_Y) -> Matrix:
    """Same viewing direction as the lesson camera, as close as possible with the whole box in frame."""
    from bpy_extras.object_utils import world_to_camera_view
    home = _home(cam)
    _, rot, scale = home.decompose()
    forward = (rot @ Vector((0, 0, -1))).normalized()
    center = (lo + hi) / 2
    corners = [Vector((x, y, z)) for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]
    sc = bpy.context.scene
    saved = cam.matrix_world.copy()

    def fits(d: float) -> bool:
        cam.matrix_world = Matrix.LocRotScale(center - forward * d, rot, scale)
        bpy.context.view_layer.update()
        for c in corners:
            p = world_to_camera_view(sc, cam, c)
            if p.z <= 0 or not (fx[0] <= p.x <= fx[1] and fy[0] <= p.y <= fy[1]):
                return False
        return True

    near, far = 1.0, 60.0
    for _ in range(24):
        mid = (near + far) / 2
        near, far = (near, mid) if fits(mid) else (mid, far)
    cam.matrix_world = saved
    bpy.context.view_layer.update()
    return Matrix.LocRotScale(center - forward * far, rot, scale)


# ---------------- halo ----------------

def _collection():
    col = bpy.data.collections.get(COLLECTION)
    if col is None:
        col = bpy.data.collections.new(COLLECTION)
        bpy.context.scene.collection.children.link(col)
    return col


def _halo_material():
    mat = bpy.data.materials.get("TD_Halo")
    if mat is None:
        mat = bpy.data.materials.new("TD_Halo")
        mat.use_nodes = True
        b = mat.node_tree.nodes.get("Principled BSDF")
        b.inputs["Base Color"].default_value = _lin(HALO_HEX)
        b.inputs["Emission Color"].default_value = _lin(HALO_HEX)
        b.inputs["Emission Strength"].default_value = 6.0
        mat.diffuse_color = _lin(HALO_HEX)
    return mat


def _remove_halo() -> None:
    for o in [o for o in bpy.data.objects if "td_halo_target" in o]:
        data = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        if data is not None and data.users == 0:
            bpy.data.curves.remove(data)


def _add_halo(target: str, lo: Vector, hi: Vector):
    _remove_halo()
    cu = bpy.data.curves.new("TD_Halo", "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = 0.035
    cu.bevel_resolution = 3
    a = max((hi.x - lo.x) / 2 * 1.2 + 0.18, 0.45)
    b = max((hi.y - lo.y) / 2 * 1.2 + 0.18, 0.45)
    sp = cu.splines.new("POLY")
    n = 72
    sp.points.add(n - 1)
    for i, p in enumerate(sp.points):
        t = 2 * math.pi * i / n
        p.co = (a * math.cos(t), b * math.sin(t), 0.0, 1.0)
    sp.use_cyclic_u = True
    cu.materials.append(_halo_material())
    obj = bpy.data.objects.new("TD_Halo", cu)
    obj.location = ((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, 0.04)  # lies on the board, around the part
    obj["td_halo_target"] = target
    _collection().objects.link(obj)
    return obj


# ---------------- caption ----------------

def _caption_obj():
    return next(iter(_circuit_objects(["label_caption"])), None) or _scam().caption_obj()


def _set_caption(text: str) -> bool:
    lab = _caption_obj()
    if lab is None or lab.type != "FONT":
        return False
    from blender_engine.circuit import _fit_plate
    lab.data.body = text
    bpy.context.view_layer.update()  # the text's bound box is stale until the depsgraph re-evaluates it
    _fit_plate(lab)
    return True


# ---------------- callout (big measurement) + flow emphasis ----------------

def _remove_callout() -> None:
    for name, *_ in CALLOUT.values():
        o = bpy.data.objects.get(name)
        if o is None:
            continue
        for ch in list(o.children):
            data = ch.data
            bpy.data.objects.remove(ch, do_unlink=True)
            if data is not None and data.users == 0:
                bpy.data.meshes.remove(data)
        data = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        if data is not None and data.users == 0:
            bpy.data.curves.remove(data)


def _set_callout(callout: dict, cam) -> list:
    """Two HUD text lines in the top-right corner of the camera view (children of the camera, like the
    lesson's title). Returns the new objects (for the pop-in)."""
    from blender_engine import circuit
    _remove_callout()
    made = []
    for key, (name, size, color, (x, y)) in CALLOUT.items():
        body = callout.get(key) or ""
        if not body:
            continue
        t = circuit._text_obj(name, body, size, color, "RIGHT", emit=2.2 if key == "value" else 1.4)
        _collection().objects.link(t)
        t.parent = cam
        t.location = (x, y, -circuit.HUD_DIST)
        me = bpy.data.meshes.new(f"{name}_plate")
        me.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
        me.materials.append(bpy.data.materials.get("td_circ_plate")
                            or circuit._mat("td_circ_plate", "#0B1016", rough=0.9, alpha=0.82))
        plate = bpy.data.objects.new(f"{name}_plate", me)
        _collection().objects.link(plate)
        plate.parent = t
        plate["plate_hud"] = True
        made.append(t)
    bpy.context.view_layer.update()
    for t in made:
        circuit._fit_plate(t)
    return made


def _callout_read() -> dict | None:
    out = {k: bpy.data.objects[name].data.body for k, (name, *_) in CALLOUT.items() if name in bpy.data.objects}
    return out or None


def _flow_emphasis(on: bool) -> None:
    mat = bpy.data.materials.get("td_circ_flow")
    if mat is None or not mat.use_nodes:
        return
    sock = mat.node_tree.nodes.get("Principled BSDF").inputs["Emission Strength"]
    if "td_base_emit" not in mat:
        mat["td_base_emit"] = float(sock.default_value)
    sock.default_value = mat["td_base_emit"] * (FLOW_EMPHASIS if on else 1.0)
    mat["td_emphasis"] = on


# ---------------- camera motion ----------------

def _ease(t: float) -> float:
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def _tween_step():
    tw = _TWEEN
    cam = bpy.data.objects.get(tw.get("camera", ""))
    if cam is None:
        _TWEEN.clear()
        return None
    tw["t"] = min(1.0, tw["t"] + tw["dt"])
    k = _ease(tw["t"])
    (l0, r0, s0), (l1, r1, s1) = tw["from"], tw["to"]
    cam.matrix_world = Matrix.LocRotScale(l0.lerp(l1, k), r0.slerp(r1, k), s1)
    halo = bpy.data.objects.get(tw.get("halo") or "")
    if halo is not None:
        s = 0.55 + 0.45 * k
        halo.scale = (s, s, 1.0)
    for name in tw.get("pop", []):  # the callout grows in with the move
        o = bpy.data.objects.get(name)
        if o is not None:
            s = 0.4 + 0.6 * k
            o.scale = (s, s, s)
    if tw["t"] >= 1.0:
        _TWEEN.clear()
        return None
    return 1 / 30


def _move(cam, target: Matrix, seconds: float, halo=None, pop: list | None = None) -> None:
    if bpy.app.background or seconds <= 0:
        cam.matrix_world = target
        return
    if bpy.app.timers.is_registered(_tween_step):
        bpy.app.timers.unregister(_tween_step)
    _TWEEN.clear()
    _TWEEN.update({"camera": cam.name, "t": 0.0, "dt": 1 / (30 * seconds), "from": cam.matrix_world.decompose(),
                   "to": target.decompose(), "halo": halo.name if halo is not None else None,
                   "pop": [o.name for o in pop or []]})
    if halo is not None:
        halo.scale = (0.55, 0.55, 1.0)
    for o in pop or []:
        o.scale = (0.4, 0.4, 0.4)
    bpy.app.timers.register(_tween_step, first_interval=0.0)


# ---------------- requests ----------------

def check_request(req) -> list[str]:
    errs = []
    if not isinstance(req, dict) or req.get("kind") != "direct":
        return ["kind must be 'direct'"]
    focus = req.get("focus")
    if focus is not None and (not isinstance(focus, str) or not TARGET_RE.match(focus)):
        errs.append("focus must be a target id")
    cap = req.get("caption")
    if cap is not None and (not isinstance(cap, str) or len(cap) > 400):
        errs.append("caption must be a string of at most 400 characters")
    co = req.get("callout")
    if co is not None and (not isinstance(co, dict) or not isinstance(co.get("value", ""), str)
                           or not isinstance(co.get("note", ""), str) or len(co.get("value", "")) > 24
                           or len(co.get("note", "")) > 80 or set(co) - {"value", "note"}):
        errs.append("callout must be {value: string <= 24, note: string <= 80}")
    secs = req.get("transition_s", 0.9)
    if not isinstance(secs, (int, float)) or isinstance(secs, bool) or not 0 <= secs <= 3:
        errs.append("transition_s must be between 0 and 3")
    return errs


def apply(req: dict) -> list[str]:
    """Returns errors (empty = applied). A missing target is an error and changes nothing."""
    errs = check_request(req)
    if errs:
        return errs
    cam = _camera()
    focus = req.get("focus")
    pop = []
    if (focus is not None or "callout" in req) and cam is not None:
        co = req.get("callout")
        if co and co.get("value"):
            pop = _set_callout(co, cam)
        else:  # a new focus without a callout clears the old one
            _remove_callout()
    if focus is not None:
        if cam is None:
            return ["no lesson camera in the scene"]
        _flow_emphasis(focus == "current")
        scam_t = _scam().targets()
        if scam_t:  # glowing outline on the message part being discussed (none for overview / outcomes)
            _scam().highlight(focus)
        if focus == "overview" and (_compare_active() or "outcomes" in scam_t):
            focus_box = _bbox(_objects_for("compare" if _compare_active() else "outcomes"))
            _remove_halo()
            _move(cam, _framing(cam, *focus_box, WIDE_X, WIDE_Y), float(req.get("transition_s", 0.9)), None, pop)
        elif focus == "overview":
            _remove_halo()
            _move(cam, _home(cam), float(req.get("transition_s", 0.9)), None, pop)
        else:
            objs = _objects_for(focus)
            box = _bbox(objs) if objs else None
            if box is None:
                return [f"nothing to focus on: {focus!r} is not in the scene (targets: {', '.join(targets())})"]
            halo = None
            if req.get("highlight", True) and focus not in scam_t and (
                    focus in HALO or (focus not in CIRCUIT_TARGETS and focus not in COMPARE_TARGETS)):
                part = _circuit_objects([HALO_PART[focus]]) if focus in HALO_PART else objs
                halo = _add_halo(focus, *(_bbox(part) or box))
            else:
                _remove_halo()
            if focus in COMPARE_TARGETS or focus in _scam().WIDE:
                frame = _framing(cam, *box, WIDE_X, WIDE_Y)
            else:
                lo, hi = box
                grow = max(0.0, MIN_SPAN - (hi.x - lo.x)) / 2
                frame = _framing(cam, lo - Vector((grow, 0, 0)), hi + Vector((grow, 0, 0)))
            _move(cam, frame, float(req.get("transition_s", 0.9)), halo, pop)
        bpy.context.scene[FOCUS_KEY] = focus
    if req.get("caption") is not None and not _set_caption(req["caption"]):
        if focus is None:
            return ["this lesson has no caption to set"]
    return []


def read_back() -> dict:
    sc = bpy.context.scene
    cam = sc.camera
    halo = bpy.data.objects.get("TD_Halo")
    target = _TWEEN.get("to")
    loc = target[0] if target else (cam.matrix_world.translation if cam else None)
    cap = _caption_obj()
    return {"focus": sc.get(FOCUS_KEY), "camera": [round(v, 3) for v in loc] if loc is not None else None,
            "moving": bool(_TWEEN), "halo": halo.get("td_halo_target") if halo else None,
            "caption": cap.data.body if cap is not None and cap.type == "FONT" else None, "targets": targets(),
            "callout": _callout_read(),
            "flow_emphasis": bool((bpy.data.materials.get("td_circ_flow") or {}).get("td_emphasis", False))}


def clear() -> None:
    """A new lesson is going on stage: drop the halo and the remembered focus."""
    if bpy.app.timers.is_registered(_tween_step):
        bpy.app.timers.unregister(_tween_step)
    _TWEEN.clear()
    _remove_halo()
    _remove_callout()
    _flow_emphasis(False)
    if FOCUS_KEY in bpy.context.scene:
        del bpy.context.scene[FOCUS_KEY]


def make_result(request_id: str, req: dict) -> dict:
    from blender_engine import engine
    errs = apply(req)
    return {"schema_version": 1, "request_id": request_id, "status": "error" if errs else "ok", "applied": 0,
            "errors": errs, "scene_version": engine.scene_version(), "scene_objects": engine.read_scene(),
            "direction": read_back()}
