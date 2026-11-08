"""OVERRIDE lessons in Blender: a generated explainer scene built from a validated spec (data, never code).

  {"kind": "explainer", "action": "build" | "clear", "spec": {...}}   (shared/schema/explainer_request_v1.json)

Nemotron writes the spec (director/explainer/): 2-7 elements, each one shape from a fixed allowlist with a
label, a color, a size and an optional motion; a layout (row / flow / cycle / hub / stack / grid) that this
module turns into positions; links (curved arrows with labels) between elements; and the narration. Nothing in
the spec is executed: unknown shapes, layouts or motions are rejected by check_request.

Everything is placed in the XZ plane facing the camera (a diagram stage), every element has its label under it
on a dark plate, and the camera is fitted so that all of it is in frame. read_back() reports what Blender
actually built: label texts, element boxes in camera space, which elements are out of frame and which overlap,
so the director can check the build instead of trusting it. Reuses the circuit lesson's look (materials,
text, plates, world, render settings, HUD) and the stage direction module through `targets()` /
`objects_for()` / `highlight()` / `caption_obj()`.
"""

from __future__ import annotations

import json
import math
import re
import secrets
import textwrap

import bmesh
import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Matrix, Vector

from . import assets, circuit, engine
from .assets import _T, _box, _cyl, _S, _sphere, _torus

STATE_KEY = "td_explainer"
HL_KEY = "td_explainer_highlight"
ASSET_SHAPES = ("cube", "sphere", "cylinder", "cone", "mug", "tree", "rock", "table", "chair", "lamp", "tent",
                "cabin", "campfire")
CUSTOM_SHAPES = ("torus", "pyramid", "disc", "cloud", "drop", "person", "building", "screen", "coin", "star", "gear",
                 "battery", "lock", "shield", "arrow")
SHAPES = ASSET_SHAPES + CUSTOM_SHAPES
LAYOUTS = ("row", "flow", "cycle", "hub", "stack", "grid")
MOTIONS = ("none", "spin", "bob", "pulse")
SIZES = {"small": 0.75, "medium": 1.1, "large": 1.6}
ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
HUD = {  # the circuit lesson's HUD slots
    "title": (0.088, "#FFFFFF", "LEFT", (-1.4, 0.78)),
    "banner": (0.04, "#9FD3FF", "LEFT", (-1.4, 0.66)),
    "caption": (0.042, "#E9EEF3", "LEFT", (-1.4, -0.56)),
    "legend": (0.028, "#AAB4BF", "RIGHT", (1.4, -0.74)),
}
AMBER = "#FFB020"
LABEL_SIZE = 0.32
CELL = 3.0          # spacing between element centers (row / grid)
FRAME_MARGIN = (0.06, 0.94, 0.17, 0.80)  # x0, x1, y0, y1 of the camera view kept for the diagram (HUD outside)
LENS = 35.0  # the HUD slots (circuit lesson) need at least this field of view


# ---------------- small builders ----------------

def _link(obj, sid: str | None = None, group: str | None = None):
    circuit._link(obj)  # circuit_part: removed by circuit.clear() (any other lesson build clears this one)
    obj["explainer_part"] = True
    if sid:
        obj["explainer_id"] = sid
        obj["explainer_uid"] = secrets.token_hex(4)
    if group:
        obj["explainer_group"] = group
    return obj


def _mat(name: str, hex_color: str, emit: float = 0.0, rough: float = 0.45, alpha: float = 1.0):
    m = circuit._mat(f"td_expl_{name}", hex_color, rough=rough, emit=emit, alpha=alpha)
    m["explainer_part"] = True
    return m


def _text(name: str, body: str, size: float, color: str, loc, align="CENTER", sid=None, group=None, emit=1.3):
    t = circuit._text_obj(name, body, size, color, align, emit=emit)
    t.data.align_y = "CENTER"
    t.data.materials[0]["explainer_part"] = True
    _link(t, sid, group)
    t.location = loc
    t.rotation_euler = (math.pi / 2, 0, 0)  # face the camera (-Y)
    return t


def _wrap(text: str, width_chars: int) -> str:
    return "\n".join(textwrap.wrap(text, width_chars, break_on_hyphens=False)) or text


def _custom(shape: str, bm) -> None:
    """Allowlisted custom shapes, ~1 unit tall, base at z = 0 (like the asset library)."""
    if shape == "torus":
        _torus(bm, _T(0, 0, 0.5), major=0.38, minor=0.12, seg_major=32, seg_minor=12)  # faces the camera
    elif shape == "pyramid":
        _cyl(bm, _T(0, 0, 0.5), r1=0.62, r2=0.0, segs=4)
    elif shape == "disc":
        _cyl(bm, _T(0, 0, 0.5) @ Matrix.Rotation(math.pi / 2, 4, "X") @ _S(1, 1, 0.12), r1=0.5, r2=0.5, segs=40)
    elif shape == "coin":
        _cyl(bm, _T(0, 0, 0.5) @ Matrix.Rotation(math.pi / 2, 4, "X") @ _S(0.8, 0.8, 0.1), r1=0.5, r2=0.5, segs=40)
    elif shape == "cloud":
        for x, z, s in ((-0.32, 0.42, 0.55), (0.0, 0.55, 0.7), (0.32, 0.42, 0.55), (0.16, 0.3, 0.45),
                        (-0.16, 0.3, 0.45)):
            _sphere(bm, _T(x, 0, z) @ _S(s, s * 0.8, s))
    elif shape == "drop":
        _sphere(bm, _T(0, 0, 0.32) @ _S(0.62, 0.62, 0.62))
        _cyl(bm, _T(0, 0, 0.6) @ _S(1, 1, 0.52), r1=0.29, r2=0.0, segs=24)
    elif shape == "person":
        _cyl(bm, _T(0, 0, 0.3) @ _S(1, 1, 0.6), r1=0.24, r2=0.2, segs=20)
        _sphere(bm, _T(0, 0, 0.78) @ _S(0.36, 0.36, 0.36))
    elif shape == "building":
        _box(bm, _T(0, 0, 0.5) @ _S(0.6, 0.5, 1.0))
        _box(bm, _T(0.42, 0, 0.32) @ _S(0.3, 0.45, 0.64))
    elif shape == "screen":
        _box(bm, _T(0, 0, 0.6) @ _S(1.0, 0.08, 0.62))
        _box(bm, _T(0, 0, 0.14) @ _S(0.1, 0.1, 0.28))
        _box(bm, _T(0, 0, 0.02) @ _S(0.45, 0.3, 0.04))
    elif shape == "battery":
        _cyl(bm, _T(0, 0, 0.45) @ _S(1, 1, 0.9), r1=0.28, r2=0.28, segs=28)
        _cyl(bm, _T(0, 0, 0.95) @ _S(1, 1, 0.1), r1=0.1, r2=0.1, segs=16)
    elif shape == "lock":
        _box(bm, _T(0, 0, 0.3) @ _S(0.7, 0.3, 0.55))
        _torus(bm, _T(0, 0, 0.6), major=0.22, minor=0.06, seg_major=24, seg_minor=8, arc=math.pi)
    elif shape == "shield":
        _cyl(bm, _T(0, 0, 0.55) @ Matrix.Rotation(math.pi / 2, 4, "X") @ _S(0.85, 1.0, 0.15), r1=0.5, r2=0.5,
             segs=6)
    elif shape == "star":
        verts, n = [], 5
        for i in range(2 * n):
            r = 0.5 if i % 2 == 0 else 0.22
            a = math.pi / 2 + math.pi * i / n
            verts.append((r * math.cos(a), r * math.sin(a)))
        front = [bm.verts.new((x, -0.08, 0.5 + z)) for x, z in verts]
        back = [bm.verts.new((x, 0.08, 0.5 + z)) for x, z in verts]
        bm.faces.new(front[::-1])
        bm.faces.new(back)
        for i in range(len(verts)):
            j = (i + 1) % len(verts)
            bm.faces.new((front[i], front[j], back[j], back[i]))
    elif shape == "gear":
        _cyl(bm, _T(0, 0, 0.5) @ Matrix.Rotation(math.pi / 2, 4, "X") @ _S(1, 1, 0.18), r1=0.36, r2=0.36, segs=32)
        for i in range(8):
            a = 2 * math.pi * i / 8
            _box(bm, _T(0.42 * math.cos(a), 0, 0.5 + 0.42 * math.sin(a)) @ Matrix.Rotation(-a, 4, "Y")
                 @ _S(0.16, 0.18, 0.14))
    elif shape == "arrow":
        _cyl(bm, _T(-0.12, 0, 0.5) @ Matrix.Rotation(math.pi / 2, 4, "Y") @ _S(1, 1, 0.6), r1=0.1, r2=0.1, segs=16)
        _cyl(bm, _T(0.3, 0, 0.5) @ Matrix.Rotation(math.pi / 2, 4, "Y") @ _S(1, 1, 0.3), r1=0.24, r2=0.0, segs=16)


def _element_mesh(eid: str, el: dict):
    """One element: mesh at the origin, ~1 unit tall, base at z = 0, colored by the spec."""
    shape = el["shape"]
    bm = bmesh.new()
    accents = assets.build(shape, bm, seed=len(eid)) if shape in ASSET_SHAPES else (_custom(shape, bm) or [])
    me = bpy.data.meshes.new(f"TD_Expl_{eid}_mesh")
    bm.to_mesh(me)
    bm.free()
    for poly in me.polygons:
        poly.use_smooth = shape in ("sphere", "rock", "mug", "cloud", "drop", "person", "torus", "coin", "disc",
                                    "battery", "cylinder")
    me.materials.append(_mat(f"el_{eid}", el["color"], emit=1.6 if el.get("glow") else 0.0, rough=0.4))
    for name in accents:
        hx, em = assets.ACCENTS[name]
        me.materials.append(_mat(f"accent_{name}", hx, emit=em))
    obj = _link(bpy.data.objects.new(f"TD_Expl_{eid}", me), eid, eid)
    obj["explainer_shape"] = shape
    return obj


# ---------------- layout ----------------

def layout_positions(spec: dict) -> dict[str, tuple[float, float]]:
    """(x, z) center of each element in the diagram plane. Deterministic (the director's simulator mirrors it)."""
    ids = [e["id"] for e in spec["elements"]]
    n = len(ids)
    kind = spec.get("layout", "row")
    big = max(SIZES[e.get("size", "medium")] for e in spec["elements"])
    cell = max(CELL, big * 1.9 + 0.9)
    if kind in ("row", "flow"):
        return {i: ((k - (n - 1) / 2) * cell, 0.0) for k, i in enumerate(ids)}
    if kind == "stack":
        step = max(2.2, big * 1.2 + 1.3)  # room for an arrow between layers (labels sit beside, not below)
        return {i: (0.0, ((n - 1) / 2 - k) * step) for k, i in enumerate(ids)}
    if kind == "grid":
        cols = math.ceil(n / 2) if n > 3 else n
        rows = math.ceil(n / cols)
        step_z = max(2.6, big * 1.25 + 1.0)
        out = {}
        for k, i in enumerate(ids):  # snake order: the second row runs back, so k -> k+1 stays a short arrow
            row, col = divmod(k, cols)
            col = col if row % 2 == 0 else cols - 1 - col
            out[i] = ((col - (cols - 1) / 2) * cell, ((rows - 1) / 2 - row) * step_z)
        return out
    ring = ids[1:] if kind == "hub" else ids
    m = len(ring)
    radius = max(3.2, cell * m / (2 * math.pi) * 1.45, cell * 1.05 if m <= 3 else 0)  # room for arrows + labels
    out = {ids[0]: (0.0, 0.0)} if kind == "hub" else {}
    for k, i in enumerate(ring):
        a = math.pi / 2 - 2 * math.pi * k / m  # start at the top, clockwise (reads like a cycle diagram)
        out[i] = (radius * 1.25 * math.cos(a), radius * math.sin(a))
    return out


# ---------------- scene ----------------

def objects() -> dict:
    return {o["explainer_id"]: o for o in bpy.data.objects if o.get("explainer_part") and "explainer_id" in o}


def _spec() -> dict | None:
    raw = bpy.context.scene.get(STATE_KEY)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def present() -> bool:
    s = _spec()
    return bool(s) and all(e["id"] in objects() for e in s["elements"])


def _animate(obj, motion: str) -> None:
    if motion == "none":
        return
    sc = bpy.context.scene
    end = sc.frame_end
    if motion == "spin":
        obj.rotation_euler.z = 0.0
        obj.keyframe_insert("rotation_euler", index=2, frame=1)
        obj.rotation_euler.z = 2 * math.pi
        obj.keyframe_insert("rotation_euler", index=2, frame=end + 1)
        obj.rotation_euler.z = 0.0
    elif motion == "bob":
        z = obj.location.z
        for f, dz in ((1, 0.0), (end // 2 + 1, 0.22), (end + 1, 0.0)):
            obj.location.z = z + dz
            obj.keyframe_insert("location", index=2, frame=f)
        obj.location.z = z
    elif motion == "pulse":
        s = obj.scale.copy()
        for f, k in ((1, 1.0), (end // 4 + 1, 1.1), (end // 2 + 1, 1.0)):
            obj.scale = s * k
            obj.keyframe_insert("scale", frame=f)
        obj.scale = s
    for fc in engine.fcurves(obj):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR" if motion == "spin" else "BEZIER"
        if not any(m.type == "CYCLES" for m in fc.modifiers):
            fc.modifiers.new("CYCLES")


def _mix(hex_a: str, hex_b: str, t: float) -> str:
    """t of colour a over colour b (opaque: transparent bands dither in EEVEE)."""
    a = [int(hex_a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(hex_b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(t * x + (1 - t) * y):02X}" for x, y in zip(a, b))


def _panel_xz(name: str, cx: float, cz: float, w: float, h: float, mat, y: float):
    """A flat rectangle in the diagram plane, behind the elements (stack layout bands)."""
    me = bpy.data.meshes.new(name)
    me.from_pydata([(cx - w / 2, y, cz - h / 2), (cx + w / 2, y, cz - h / 2), (cx + w / 2, y, cz + h / 2),
                    (cx - w / 2, y, cz + h / 2)], [], [(0, 1, 2, 3)])
    me.materials.append(mat)
    return bpy.data.objects.new(name, me)


def _world_bounds(objs) -> tuple[Vector, Vector] | None:
    bpy.context.view_layer.update()
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    if not pts:
        return None
    return (Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts))),
            Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts))))


def _arrow(name: str, a: Vector, b: Vector, bend: Vector, color: str, group: str):
    """Curved arrow a -> b (quadratic bezier through `bend`), with a cone head."""
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = 0.045
    cu.bevel_resolution = 3
    sp = cu.splines.new("POLY")
    n = 24
    pts = [(1 - t) ** 2 * a + 2 * (1 - t) * t * bend + t ** 2 * b for t in (i / n for i in range(n + 1))]
    head_len = 0.32
    tip, prev = pts[-1], pts[-2]
    d = (tip - prev).normalized()
    shaft_end = tip - d * head_len * 0.9
    pts[-1] = shaft_end
    sp.points.add(len(pts) - 1)
    for p, v in zip(sp.points, pts):
        p.co = (v.x, v.y, v.z, 1.0)
    mat = _mat(f"arrow_{name}", color, emit=1.2)
    cu.materials.append(mat)
    shaft = _link(bpy.data.objects.new(name, cu), None, group)
    bm = bmesh.new()
    _cyl(bm, Matrix.Identity(4), r1=0.13, r2=0.0, segs=16)
    me = bpy.data.meshes.new(f"{name}_head")
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mat)
    head = _link(bpy.data.objects.new(f"{name}_head", me), None, group)
    head.scale = (1, 1, head_len)
    head.rotation_euler = d.to_track_quat("Z", "Y").to_euler()
    head.location = tip - d * head_len / 2
    return shaft, head


def _ring(eid: str, center: Vector, radius: float):
    """Amber highlight ring around an element, facing the camera (hidden until focused)."""
    bm = bmesh.new()
    _torus(bm, Matrix.Identity(4), major=radius, minor=0.035, seg_major=64, seg_minor=8)
    me = bpy.data.meshes.new(f"TD_Expl_{eid}_ring")
    bm.to_mesh(me)
    bm.free()
    me.materials.append(_mat("ring", AMBER, emit=6.0))
    ring = _link(bpy.data.objects.new(f"TD_Expl_{eid}_ring", me), None, eid)
    ring.location = center - Vector((0, 0.5, 0))
    ring["explainer_ring"] = True
    ring.hide_render = ring.hide_viewport = True
    return ring


def _camera_and_lights(lo: Vector, hi: Vector):
    cd = bpy.data.cameras.new("TD_ExplCam")
    cd.lens, cd.clip_start, cd.clip_end = LENS, 0.1, 400
    cam = _link(bpy.data.objects.new("TD_ExplCam", cd))
    center = (lo + hi) / 2
    tilt = math.radians(8)  # look slightly down: the diagram floats above a dark floor
    fwd = Vector((0, math.cos(tilt), -math.sin(tilt)))
    cam.rotation_euler = fwd.to_track_quat("-Z", "Y").to_euler()
    sc = bpy.context.scene
    sc.camera = cam
    sc.render.resolution_x, sc.render.resolution_y = 1280, 720
    corners = [Vector((x, y, z)) for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]
    x0, x1, y0, y1 = FRAME_MARGIN

    def fits(dist: float) -> bool:
        cam.location = center - fwd * dist
        bpy.context.view_layer.update()
        for c in corners:
            p = world_to_camera_view(sc, cam, c)
            if p.z <= 0 or not (x0 <= p.x <= x1 and y0 <= p.y <= y1):
                return False
        return True

    near, far = 2.0, 200.0
    for _ in range(30):
        mid = (near + far) / 2
        near, far = (near, mid) if fits(mid) else (mid, far)
    cam.location = center - fwd * far
    span = max(hi.x - lo.x, hi.z - lo.z, 4.0)
    for name, off, energy, color, size in (("TD_ExplKey", (-0.6, -0.9, 0.9), 1300, (1.0, 0.96, 0.92), 8.0),
                                           ("TD_ExplRim", (0.7, 0.8, 0.7), 900, (0.55, 0.7, 1.0), 7.0),
                                           ("TD_ExplFill", (0.9, -0.7, -0.1), 350, (0.8, 0.85, 1.0), 6.0)):
        ld = bpy.data.lights.new(name, "AREA")
        ld.energy, ld.color, ld.size = energy * (span / 8.0) ** 2, color, size * span / 8.0
        lo_ = _link(bpy.data.objects.new(name, ld))
        lo_.location = center + Vector(off) * span
        lo_.rotation_euler = (center - lo_.location).to_track_quat("-Z", "Y").to_euler()
    return cam


def _floor(lo: Vector, hi: Vector):
    me = bpy.data.meshes.new("TD_ExplFloor")
    n, r = 64, max(hi.x - lo.x, 8.0) * 1.6
    cz = lo.z - 0.45
    cx = (lo.x + hi.x) / 2
    me.from_pydata([(cx, 0, cz)] + [(cx + r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n), cz)
                                    for i in range(n)], [], [(0, i + 1, (i + 1) % n + 1) for i in range(n)])
    me.materials.append(_mat("floor", "#0E1622", rough=0.35))
    return _link(bpy.data.objects.new("TD_ExplFloor", me))


def _hud(cam, title: str, banner: str, legend: str):
    for hid, (size, color, align, (hx, hy)) in HUD.items():
        body = {"title": title, "banner": banner, "legend": legend, "caption": ""}[hid]
        t = circuit._text_obj(f"expl_{hid}", body, size, color, align)
        t.data.materials[0]["explainer_part"] = True
        _link(t, f"hud_{hid}")
        t.parent = cam
        t.location = (hx, hy, -circuit.HUD_DIST)
        circuit._plate(t, hud=True)
    bpy.context.view_layer.update()
    for o in objects().values():
        if o.type == "FONT" and o.parent == cam:
            circuit._fit_plate(o)


def _build(req: dict) -> None:
    circuit.clear(keep_settings=True)  # one lesson on stage
    spec = req["spec"]
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 1, 240
    pos = layout_positions(spec)
    stack = spec.get("layout") == "stack"
    centers, radius = {}, {}
    for el in spec["elements"]:
        eid = el["id"]
        k = SIZES[el.get("size", "medium")]
        obj = _element_mesh(eid, el)
        obj.scale = (k, k, k)
        x, z = pos[eid]
        lo, hi = _world_bounds([obj])
        h = hi.z - lo.z
        obj.location = (x, 0.0, z - h / 2)  # center the shape on its layout point
        centers[eid] = Vector((x, 0.0, z))
        radius[eid] = max(hi.x - lo.x, h) / 2
        _animate(obj, el.get("motion", "none"))
        if stack:  # layers: a wide band behind each element, the label beside it
            band = _panel_xz(f"TD_Expl_{eid}_band", x + 1.6, z, 9.0, min(2.0, radius[eid] * 2 + 0.8),
                             _mat(f"band_{eid}", _mix(el["color"], "#0E1622", 0.3), rough=0.85), 0.9)
            _link(band, None, eid)
            band["explainer_band"] = True
            label = _text(f"TD_Expl_{eid}_label", _wrap(el["label"], 22), LABEL_SIZE, "#F4F6F8",
                          (x + radius[eid] + 0.55, -0.75, z), align="LEFT", sid=f"label_{eid}", group=eid)
        else:
            label = _text(f"TD_Expl_{eid}_label", _wrap(el["label"], 16), LABEL_SIZE, "#F4F6F8",
                          (x, -0.75, z - radius[eid] - 0.5), sid=f"label_{eid}", group=eid)
        label["explainer_label"] = eid
        circuit._plate(label, hud=False)
        _ring(eid, centers[eid], radius[eid] * 1.15 + 0.15)
    hub = spec.get("layout") in ("cycle", "hub")
    mid = sum(centers.values(), Vector()) / len(centers)
    # 2D boxes (x, z, half width, half height) that a link label must not touch: shapes and their labels
    blocked = []
    for el in spec["elements"]:
        c, r, n = centers[el["id"]], radius[el["id"]], min(len(el["label"]), 22 if stack else 16)
        blocked.append((c.x, c.z, r, r))
        blocked.append((c.x + r + 0.55 + n * 0.1, c.z, n * 0.1 + 0.1, 0.3) if stack else
                       (c.x, c.z - r - 0.5, n * 0.1 + 0.1, 0.3))

    def clearance(x: float, z: float, hw: float, hh: float) -> float:
        return min(max(abs(x - bx) - (hw + bw), abs(z - bz) - (hh + bh)) for bx, bz, bw, bh in blocked)

    for i, ln in enumerate(spec.get("links", [])):
        a, b = centers[ln["from"]], centers[ln["to"]]
        d = (b - a).normalized()
        # an arrow leaving downward / arriving from below would cross the label under that element
        clear_from = 1.0 if d.z < -0.35 and not stack else 0.25
        clear_to = 1.0 if d.z > 0.35 and not stack else 0.25
        start, end = a + d * (radius[ln["from"]] + clear_from), b - d * (radius[ln["to"]] + clear_to)
        m = (start + end) / 2
        side = Vector((-d.z, 0, d.x))  # perpendicular in the diagram plane
        if hub and (m - mid).length > 1e-3:
            side = (m - mid).normalized()
        amount = min(0.9, (end - start).length * 0.18)
        away = 0.75 if abs(d.z) > 0.7 else 0.38  # beside a near-vertical arrow, above / below a flat one
        upright = abs(d.z) > 0.7  # a label beside a near-vertical arrow is aligned away from it

        def label_box(sd: Vector, hw: float) -> tuple:
            p = m + sd * (amount + away)
            shift = (hw if sd.x > 0 else -hw) if upright else 0.0
            return p.x + shift, p.z, hw, 0.2

        if ln.get("label"):  # bend the arrow (and put its label) on whichever side is clear of the elements
            hw = len(ln["label"]) * 0.075 + 0.15
            room = {sgn: clearance(*label_box(side * sgn, hw)) for sgn in (1, -1)}
            if room[1] < 0.2 and room[-1] > room[1]:
                side = -side
        bend = m + side * amount + Vector((0, -0.2, 0))
        _arrow(f"TD_Expl_link{i}", start, end, bend, ln.get("color") or "#7FD3FF", f"link_{i}")
        if ln.get("label"):
            spot = bend + side * away
            blocked.append(label_box(side, hw))
            t = _text(f"TD_Expl_link{i}_label", ln["label"], 0.25, "#BFE6FF", spot
                      + Vector((0, -0.6, 0)), sid=f"linklabel_{i}", group=f"link_{i}")
            t.data.align_x = "LEFT" if upright and side.x > 0 else "RIGHT" if upright else "CENTER"
            circuit._plate(t, hud=False)
    bpy.context.view_layer.update()
    for o in objects().values():
        if o.type == "FONT" and o.get("explainer_label") or str(o.get("explainer_id", "")).startswith("linklabel_"):
            circuit._fit_plate(o)
    content = [o for o in bpy.data.objects if o.get("explainer_part") and not o.get("explainer_ring")]
    lo, hi = _world_bounds(content)
    _floor(lo, hi)
    cam = _camera_and_lights(lo, hi)
    _hud(cam, spec["title"], req["banner"], req["legend"])
    circuit._world()
    circuit._scene_settings(cam)
    sc.frame_start, sc.frame_end = 1, 240
    sc[STATE_KEY] = json.dumps(spec, ensure_ascii=False)
    sc[HL_KEY] = ""


def clear() -> None:
    kinds = ((bpy.types.Mesh, bpy.data.meshes), (bpy.types.Curve, bpy.data.curves),
             (bpy.types.Light, bpy.data.lights), (bpy.types.Camera, bpy.data.cameras))
    for o in [o for o in bpy.data.objects if o.get("explainer_part")]:
        data = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        if data is not None and data.users == 0:
            for t, coll in kinds:
                if isinstance(data, t):
                    coll.remove(data)
                    break
    sc = bpy.context.scene
    for key in (STATE_KEY, HL_KEY):
        if key in sc:
            del sc[key]


# ---------------- stage direction hooks (blender_engine/direction.py) ----------------

WIDE = ("scene",)


def targets() -> list[str]:
    if not present():
        return []
    return ["scene"] + [e["id"] for e in _spec()["elements"]]


def objects_for(target: str) -> list:
    objs = [o for o in bpy.data.objects if o.get("explainer_part") and not o.get("explainer_ring")]
    if target == "scene":
        return [o for o in objs if o.parent is None or o.parent.type != "CAMERA"]
    return [o for o in objs if o.get("explainer_group") == target and not o.name.endswith("_plate")
            and not o.get("explainer_band")]


def highlight(target: str | None) -> None:
    for o in bpy.data.objects:
        if o.get("explainer_ring"):
            on = target is not None and o.get("explainer_group") == target
            o.hide_render = o.hide_viewport = not on
    bpy.context.scene[HL_KEY] = target if target in targets() and target != "scene" else ""


def caption_obj():
    return objects().get("hud_caption")


# ---------------- requests ----------------

def check_request(req) -> list[str]:
    """Structural validation inside Blender (no jsonschema here): an unknown shape / layout / motion or a bad
    reference is refused before anything is built."""
    if not isinstance(req, dict) or req.get("kind") != "explainer" or req.get("schema_version") != 1:
        return ["not an explainer_request_v1 document"]
    if req.get("action") not in ("build", "clear"):
        return ["action must be build or clear"]
    if req["action"] == "clear":
        return []
    errs = []
    spec = req.get("spec")
    if not isinstance(spec, dict):
        return ["spec missing"]
    for key in ("banner", "legend"):
        if not isinstance(req.get(key), str):
            errs.append(f"{key} must be a string")
    if not isinstance(spec.get("title"), str) or not 1 <= len(spec["title"]) <= 70:
        errs.append("title must be 1-70 characters")
    if spec.get("layout") not in LAYOUTS:
        errs.append(f"layout must be one of {LAYOUTS}")
    els = spec.get("elements")
    if not isinstance(els, list) or not 2 <= len(els) <= 7:
        return errs + ["2 to 7 elements"]
    ids = set()
    for e in els:
        if not isinstance(e, dict) or not ID_RE.match(str(e.get("id", ""))) or e["id"] in ids:
            errs.append(f"bad or duplicate element id {e.get('id') if isinstance(e, dict) else e!r}")
            continue
        ids.add(e["id"])
        if e.get("shape") not in SHAPES:
            errs.append(f"{e['id']}: unknown shape {e.get('shape')!r}")
        if not isinstance(e.get("label"), str) or not 1 <= len(e["label"]) <= 32:
            errs.append(f"{e['id']}: label must be 1-32 characters")
        if not HEX_RE.match(str(e.get("color", ""))):
            errs.append(f"{e['id']}: color must be #RRGGBB")
        if e.get("size", "medium") not in SIZES or e.get("motion", "none") not in MOTIONS:
            errs.append(f"{e['id']}: bad size or motion")
    for ln in spec.get("links", []) or []:
        if not isinstance(ln, dict) or ln.get("from") not in ids or ln.get("to") not in ids or ln["from"] == ln["to"]:
            errs.append(f"bad link {ln!r}")
        elif ln.get("color") is not None and not HEX_RE.match(str(ln["color"])):
            errs.append("link color must be #RRGGBB")
    return errs


def _in_frame_and_overlaps() -> tuple[dict, list]:
    """Each element (shape + label) as a box in the HOME camera view; out-of-frame elements and overlaps."""
    sc = bpy.context.scene
    cam = sc.camera
    bpy.context.view_layer.update()
    boxes, frame = {}, {}
    for e in _spec()["elements"]:
        parts = [o for o in bpy.data.objects if o.get("explainer_group") == e["id"] and not o.get("explainer_ring")
                 and not o.get("explainer_band") and not o.name.endswith("_plate")]
        pts = [world_to_camera_view(sc, cam, o.matrix_world @ Vector(c)) for o in parts for c in o.bound_box]
        if not pts:
            continue
        box = (min(p.x for p in pts), min(p.y for p in pts), max(p.x for p in pts), max(p.y for p in pts))
        boxes[e["id"]] = box
        frame[e["id"]] = all(p.z > 0 for p in pts) and box[0] >= 0.0 and box[2] <= 1.0 and box[1] >= 0.0 \
            and box[3] <= 1.0
    def hit(A, B) -> bool:
        return min(A[2], B[2]) - max(A[0], B[0]) > 0.004 and min(A[3], B[3]) - max(A[1], B[1]) > 0.004

    overlaps = []
    ids = list(boxes)
    for i, a in enumerate(ids):
        overlaps += [[a, b] for b in ids[i + 1:] if hit(boxes[a], boxes[b])]
    for o in bpy.data.objects:  # a link's label must not sit on an element or its label
        if str(o.get("explainer_id", "")).startswith("linklabel_"):
            pts = [world_to_camera_view(sc, cam, o.matrix_world @ Vector(c)) for c in o.bound_box]
            lb = (min(p.x for p in pts), min(p.y for p in pts), max(p.x for p in pts), max(p.y for p in pts))
            overlaps += [[o["explainer_id"], k] for k in ids if hit(lb, boxes[k])]
    return {k: {"box": [round(v, 3) for v in boxes[k]], "in_frame": frame[k]} for k in boxes}, overlaps


def read_back() -> dict:
    if not present():
        return {"present": False}
    spec = _spec()
    objs = objects()
    sc = bpy.context.scene
    home = sc.camera.matrix_world.copy() if sc.camera else None
    from . import direction
    cam = sc.camera
    try:  # measure from the lesson's home framing, not wherever the camera is gliding to
        if cam is not None and direction.HOME_KEY in cam:
            cam.matrix_world = direction._home(cam)
        view, overlaps = _in_frame_and_overlaps()
    finally:
        if cam is not None and home is not None:
            cam.matrix_world = home
            bpy.context.view_layer.update()
    elements = {}
    for e in spec["elements"]:
        o, lab = objs.get(e["id"]), objs.get(f"label_{e['id']}")
        elements[e["id"]] = {"shape": o.get("explainer_shape") if o else None,
                             "label": lab.data.body.replace("\n", " ") if lab else None,
                             "center": [round(v, 3) for v in o.matrix_world.translation] if o else None,
                             "animated": bool(o and engine.fcurves(o)), **view.get(e["id"], {"in_frame": False})}
    links = [{"from": ln["from"], "to": ln["to"],
              "label": objs[f"linklabel_{i}"].data.body if f"linklabel_{i}" in objs else None,
              "drawn": bpy.data.objects.get(f"TD_Expl_link{i}") is not None}
             for i, ln in enumerate(spec.get("links", []))]
    return {"present": True, "title": objs["hud_title"].data.body if "hud_title" in objs else None,
            "banner": objs["hud_banner"].data.body if "hud_banner" in objs else None,
            "legend": objs["hud_legend"].data.body if "hud_legend" in objs else None,
            "layout": spec.get("layout"), "elements": elements, "links": links, "overlaps": overlaps,
            "highlight": sc.get(HL_KEY) or None, "spec": spec,
            "objects": sorted([{"id": k, "uid": str(v.get("explainer_uid", ""))} for k, v in objs.items()],
                              key=lambda o: o["id"])}


def make_result(request_id: str, req: dict) -> dict:
    from . import direction
    errs = check_request(req)
    applied = 0
    if not errs:
        try:
            if req["action"] == "build":
                direction.clear()
                _build(req)
            else:
                clear()
            applied = 1
            sc = bpy.context.scene
            sc[engine.VERSION_KEY] = int(sc.get(engine.VERSION_KEY, 0)) + 1
        except Exception as exc:  # never leave half a lesson on stage
            errs = [f"{type(exc).__name__}: {exc}"]
            try:
                clear()
                errs.append("rolled back: the partial lesson was removed")
            except Exception as exc2:  # noqa: BLE001
                errs.append(f"rollback failed: {exc2}")
            applied = 0
    return {"schema_version": 1, "request_id": request_id, "status": "error" if errs else "ok",
            "applied": 0 if errs else applied, "errors": errs, "scene_version": engine.scene_version(),
            "scene_objects": engine.read_scene(), "executor": f"blender {bpy.app.version_string}",
            "explainer": read_back(), "direction": direction.read_back()}
