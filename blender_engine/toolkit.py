"""Lesson toolkit: the ONLY API the builder agent's code can call (see sandbox.py, lesson_runner.py).

Coordinates: meters, board top at z=0, X right, Y away from the camera. Call order in builder code:

    scene("Why use a MOSFET instead of a BJT?")
    part("bat", "battery", x=-4, y=0)                     # returns the id; pins: pin("bat.+")
    part("q1", "npn_bjt", x=0, y=0, label="NPN BJT")
    wire("w1", "bat.+", "r1.a", color="red")              # pin to pin; optional via=[(x, y), ...]
    flow("f1", ["w1", "w2"], current_ma=20.6, label="I_C = 20.6 mA")
    label("l1", "tiny base current", near="q1")
    key_points(["...", "..."])
    step(["bat", "q1", "w1"], "First, the power source.")  # reveal order; one call per step
    # finish() is called by the runner, not by builder code

Custom parts: part("cap", "custom", x, y, pins={"a": (-0.3, 0, 0.05), "b": (0.3, 0, 0.05)}) then add
box()/cylinder()/sphere()/text3d() with part="cap" (positions relative to the part).
Everything is recorded and read back from Blender data (readback()) for the verifier.
"""

from __future__ import annotations

import json
import math
import re
import textwrap

import bmesh
import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Matrix, Vector

from . import circuit as C
from .lesson_parts import COLORS, LED_COLORS, PARTS

COLLECTION = "TD_Lesson"
STEP_FRAMES = 60
HOLD_FRAMES = 150
FLOW_M_PER_FRAME_PER_MA = 0.0075  # marker speed proportional to current (20 mA -> 0.15 m/frame = 3.6 m/s)
MAX_M_PER_FRAME = 0.4
MARKER_SPACING = 0.45
CAM_DIR = Vector((0.18, -1.0, 0.8)).normalized()
LENS = 35

R: dict = {}  # registry of everything the builder declared


class ToolkitError(ValueError):
    pass


def _reset_registry(title: str) -> None:
    R.clear()
    R.update({"title": title, "parts": {}, "wires": {}, "flows": {}, "labels": {}, "key_points": [], "steps": [],
              "prims": {}})


def _id(i) -> str:
    if not isinstance(i, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,15}", i):
        raise ToolkitError(f"bad id {i!r}: use lowercase letters, digits, underscore (max 16)")
    for kind in ("parts", "wires", "flows", "labels", "prims"):
        if i in R.get(kind, {}):
            raise ToolkitError(f"id {i!r} already used")
    return i


def _col():
    col = bpy.data.collections.get(COLLECTION)
    if col is None:
        col = bpy.data.collections.new(COLLECTION)
    if col.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(col)
    return col


def _link(obj, lid=None, kind=None, parent=None):
    _col().objects.link(obj)
    obj["lesson_part"] = True
    if lid:
        obj["lesson_id"] = lid
        obj["lesson_kind"] = kind or ""
    if parent is not None:
        obj.parent = parent
    return obj


def _hex(color, default="#8A8A8A") -> str:
    if color is None:
        return default
    if isinstance(color, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        return color
    if color in COLORS:
        return COLORS[color]
    raise ToolkitError(f"unknown color {color!r}: use one of {sorted(COLORS)} or '#RRGGBB'")


def _num(v, name):
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
        raise ToolkitError(f"{name} must be a number, got {v!r}")
    return float(v)


# ---------------- part geometry (local: origin at base center) ----------------

def _metal():
    return bpy.data.materials.get("td_circ_metal") or C._mat("td_circ_metal", "#C9CDD2", rough=0.22, metal=1.0)


def _legs(bm, xs, top, mat_index=1, r=0.025):
    for x in xs:
        C._cyl(bm, r, r, top, Matrix.Translation((x, 0, top / 2)), mat_index, segments=12)


def _geo_battery(pid, color, label=None):
    bm = bmesh.new()
    w, d, h = C.BAT_SIZE
    C._rounded_box(bm, (w, d, h * 0.58), (0, 0, h * 0.29), 0.06, 0)
    C._rounded_box(bm, (w * 1.002, d * 1.002, h * 0.44), (0, 0, h * 0.78), 0.06, 1)
    C._rounded_box(bm, (w * 0.86, d * 0.9, 0.05), (0, 0, h + 0.005), 0.02, 0)
    C._cyl(bm, 0.085, 0.085, 0.14, Matrix.Translation((0, 0.22, h + 0.07)), 2)
    C._cyl(bm, 0.12, 0.12, 0.11, Matrix.Translation((0, -0.22, h + 0.055)), 2, segments=6, smooth=False)
    mats = [C._mat("td_les_bat_body", "#1C1D22", rough=0.35), C._mat("td_les_bat_band", "#E2A325", rough=0.35),
            _metal()]
    pins = {"+": (0, 0.22, h + 0.14), "-": (0, -0.22, h + 0.11)}
    volts = re.search(r"(\d+(?:\.\d+)?)\s*V\b", label or "")  # print the lesson's voltage, never a fixed one
    marks = [(f"{volts.group(1)}V" if volts else "DC", (0.0, -d / 2 - 0.004, h * 0.79), 0.3 if volts and
              len(volts.group(1)) <= 2 else 0.2, (math.radians(90), 0, 0), "#151515"),
             ("+", (0.2, 0.22, h + 0.02), 0.22, (0, 0, 0), "#F2F2F2"), ("−", (0.2, -0.22, h + 0.02), 0.22, (0, 0, 0), "#F2F2F2")]
    return bm, mats, pins, marks


def _bands_from_label(label: str):
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*([kM]?)\s*Ω", label or "")
    if not m:
        return None
    ohms = float(m.group(1).replace(",", "")) * {"": 1, "k": 1e3, "M": 1e6}[m.group(2)]
    try:
        from director.circuit.model import color_bands  # pure Python; present in the repo
        from director.circuit.display import BAND_HEX
        return [BAND_HEX[b] for b in color_bands(ohms)]
    except Exception:  # not a standard value: plain body, no misleading bands
        return None


def _geo_resistor(pid, color, label):
    bm = bmesh.new()
    z = 0.42
    C._cyl(bm, 0.22, 0.22, 0.9, C._along_x(-0.45, 0.45, 0, z), 0, segments=48)
    for sx in (-1, 1):
        C._sphere(bm, 0.27, Matrix.Translation((sx * 0.52, 0, z)) @ Matrix.Diagonal((0.9, 1, 1, 1)), 0)
    C._cyl(bm, 0.03, 0.03, 1.96, C._along_x(-0.98, 0.98, 0, z), 1, segments=16)
    bands = _bands_from_label(label)
    mats = [C._mat(f"td_les_{pid}_body", "#D8C39A", rough=0.45), _metal()]
    if bands:
        for i, x in enumerate((-0.52, -0.29, -0.09, 0.41)):
            r = 0.282 if i in (0, 3) else 0.228
            C._cyl(bm, r, r, 0.095, C._along_x(x - 0.0475, x + 0.0475, 0, z), 2 + i, segments=48)
            mats.append(C._mat(f"td_les_{pid}_band{i + 1}", bands[i], rough=0.4))
    return bm, mats, {"a": (-0.98, 0, z), "b": (0.98, 0, z)}, []


def _geo_led(pid, color):
    s = 1.45
    bm = bmesh.new()
    leg_top = 0.62 * s
    C._cyl(bm, 0.032, 0.032, leg_top + 0.03, Matrix.Translation((0, 0.145, (leg_top + 0.03) / 2)), 1, segments=12)
    C._cyl(bm, 0.032, 0.032, leg_top - 0.05, Matrix.Translation((0, -0.145, (leg_top - 0.05) / 2 + 0.05)), 1,
           segments=12)
    C._cyl(bm, 0.235 * s, 0.235 * s, 0.05 * s, Matrix.Translation((0, 0, leg_top + 0.025 * s)), 0, segments=48)
    C._cyl(bm, 0.2 * s, 0.2 * s, 0.34 * s, Matrix.Translation((0, 0, leg_top + 0.22 * s)), 0, segments=48)
    dome = C._sphere(bm, 0.2 * s, Matrix.Translation((0, 0, leg_top + 0.39 * s)), 0, seg=48, rings=24)
    bmesh.ops.delete(bm, geom=[v for v in dome if v.co.z < leg_top + 0.38 * s], context="VERTS")
    glow = LED_COLORS.get(color or "red", _hex(color, "#FF2A1A") if color else "#FF2A1A")
    led = C._mat(f"td_les_{pid}_led", glow, rough=0.12, emit=0.0, emit_hex=glow)
    return bm, [led, _metal()], {"anode": (0, 0.145, 0.03), "cathode": (0, -0.145, 0.03)}, []


def _geo_to92(pid, color):
    bm = bmesh.new()
    top = 0.6
    _legs(bm, (-0.2, 0, 0.2), top)
    g = bmesh.ops.create_cone(bm, cap_ends=True, segments=32, radius1=0.28, radius2=0.28, depth=0.55)
    bmesh.ops.transform(bm, matrix=Matrix.Translation((0, 0.08, top + 0.275)), verts=g["verts"])
    bmesh.ops.bisect_plane(bm, geom=list(bm.verts) + list(bm.edges) + list(bm.faces), plane_co=(0, -0.08, 0),
                           plane_no=(0, -1, 0), clear_outer=True)
    edges = [e for e in bm.edges if e.is_boundary]
    if edges:
        bmesh.ops.holes_fill(bm, edges=edges)
    for f in bm.faces:
        if f.calc_center_median().z > top - 0.01:
            f.material_index = 0
    pins = {"E": (-0.2, 0, 0.03), "B": (0, 0, 0.03), "C": (0.2, 0, 0.03)}
    marks = [(n, (x, -0.2, 0.18), 0.13, (math.radians(90), 0, 0), "#F2F2F2") for n, x in (("E", -0.2), ("B", 0), ("C", 0.2))]
    marks.append(("NPN", (0, -0.085, top + 0.3), 0.12, (math.radians(90), 0, 0), "#DDDDDD"))
    return bm, [C._mat("td_les_pkg", "#1A1A1C", rough=0.5), _metal()], pins, marks


def _geo_to220(pid, color):
    bm = bmesh.new()
    top = 0.6
    _legs(bm, (-0.3, 0, 0.3), top, r=0.035)
    C._rounded_box(bm, (0.95, 0.42, 0.85), (0, 0, top + 0.425), 0.03, 0)
    C._rounded_box(bm, (0.95, 0.08, 0.6), (0, 0.17, top + 0.85 + 0.3), 0.02, 1)
    C._cyl(bm, 0.11, 0.11, 0.1, Matrix.Translation((0, 0.17, top + 1.25)) @ Matrix.Rotation(math.radians(90), 4, "X"),
           0, segments=24)
    pins = {"G": (-0.3, 0, 0.03), "D": (0, 0, 0.03), "S": (0.3, 0, 0.03)}
    marks = [(n, (x, -0.2, 0.18), 0.13, (math.radians(90), 0, 0), "#F2F2F2") for n, x in (("G", -0.3), ("D", 0), ("S", 0.3))]
    marks.append(("MOSFET", (0, -0.215, top + 0.45), 0.12, (math.radians(90), 0, 0), "#DDDDDD"))
    return bm, [C._mat("td_les_pkg", "#1A1A1C", rough=0.5), _metal()], pins, marks


def _geo_signal(pid, color):
    bm = bmesh.new()
    C._rounded_box(bm, (1.4, 0.9, 0.1), (0, 0, 0.05), 0.03, 0)
    C._rounded_box(bm, (0.5, 0.5, 0.12), (0.1, 0.12, 0.16), 0.02, 1)
    for x in (-0.3, 0.3):
        C._cyl(bm, 0.035, 0.035, 0.25, Matrix.Translation((x, -0.3, 0.2)), 2, segments=12)
    mats = [C._mat("td_les_pcb", "#1F4FA0", rough=0.5), C._mat("td_les_pkg", "#1A1A1C", rough=0.5), _metal()]
    marks = [("MCU", (0.1, 0.12, 0.23), 0.14, (0, 0, 0), "#DDDDDD"), ("OUT", (0.3, -0.4, 0.11), 0.09, (0, 0, 0), "#F2F2F2"),
             ("GND", (-0.3, -0.4, 0.11), 0.09, (0, 0, 0), "#F2F2F2")]
    return bm, mats, {"out": (0.3, -0.3, 0.32), "gnd": (-0.3, -0.3, 0.32)}, marks


def _geo_ground(pid, color):
    bm = bmesh.new()
    C._rounded_box(bm, (1.4, 0.16, 0.08), (0, 0, 0.04), 0.02, 0)
    return bm, [_metal()], {"gnd": (0, 0, 0.08)}, [("GND  0 V", (0, -0.2, 0.09), 0.13, (0, 0, 0), "#DDDDDD")]


def _geo_switch(pid, color):
    bm = bmesh.new()
    C._rounded_box(bm, (0.6, 0.6, 0.28), (0, 0, 0.14), 0.04, 0)
    C._cyl(bm, 0.17, 0.17, 0.14, Matrix.Translation((0, 0, 0.35)), 1, segments=32)
    _legs(bm, (-0.3, 0.3), 0.06)
    return bm, [C._mat("td_les_pkg", "#1A1A1C", rough=0.5), C._mat("td_les_btn", _hex(color, "#D9342B"), rough=0.4),
                _metal()], {"a": (-0.3, 0, 0.03), "b": (0.3, 0, 0.03)}, []


def _geo_capacitor(pid, color):
    bm = bmesh.new()
    _legs(bm, (-0.1, 0.1), 0.35, mat_index=2)
    C._cyl(bm, 0.25, 0.25, 0.8, Matrix.Translation((0, 0, 0.75)), 0, segments=40)
    C._cyl(bm, 0.252, 0.252, 0.8, Matrix.Translation((0, -0.01, 0.75)) @ Matrix.Diagonal((0.35, 1, 1, 1)), 1, segments=40)
    return bm, [C._mat("td_les_cap", _hex(color, "#2D4E9E"), rough=0.35), C._mat("td_les_cap_stripe", "#D8D8D8"),
                _metal()], {"+": (0.1, 0, 0.03), "-": (-0.1, 0, 0.03)}, [("−", (-0.1, -0.27, 0.9), 0.2, (math.radians(90), 0, 0), "#202020")]


GEOMETRY = {"battery": _geo_battery, "resistor": _geo_resistor, "led": _geo_led, "npn_bjt": _geo_to92,
            "n_mosfet": _geo_to220, "signal": _geo_signal, "ground": _geo_ground, "switch": _geo_switch,
            "capacitor": _geo_capacitor}


# ---------------- builder API ----------------

def scene(title, subtitle=""):
    """Start a lesson scene (clears any previous lesson)."""
    if not isinstance(title, str) or not title.strip():
        raise ToolkitError("scene() needs a title")
    clear()
    _reset_registry(title.strip())
    R["subtitle"] = str(subtitle or "")
    return title


def part(id, type, x=0.0, y=0.0, rotate=0.0, label=None, color=None, pins=None):
    """Place a catalog part (blender_engine/lesson_parts.py) or a 'custom' part with pins={name: (dx, dy, dz)}."""
    if "title" not in R:
        raise ToolkitError("call scene(title) first")
    pid = _id(id)
    if type not in PARTS:
        raise ToolkitError(f"unknown part type {type!r}; types: {sorted(PARTS)}")
    x, y, rot = _num(x, "x"), _num(y, "y"), _num(rotate, "rotate")
    if type == "custom":
        if not isinstance(pins, dict) or not pins:
            raise ToolkitError("custom parts need pins={'name': (dx, dy, dz), ...}")
        obj = _link(bpy.data.objects.new(pid, None), pid, "part")
        obj.empty_display_size = 0.2
        local_pins = {str(k): tuple(_num(c, "pin coordinate") for c in v) for k, v in pins.items()}
    else:
        if pins:
            raise ToolkitError(f"{type} has fixed pins {PARTS[type]['pins']}; do not pass pins=")
        geo = GEOMETRY[type]
        bm, mats, local_pins, marks = geo(pid, color, label) if type in ("resistor", "battery") else geo(pid, color)
        obj = _link(C._mesh_obj(pid, bm, mats), pid, "part")
        for i, (body, loc, size, rotm, hexc) in enumerate(marks):
            t = C._text_obj(f"{pid}_mark{i}", body, size, hexc, "CENTER", emit=0.3 if hexc != "#151515" else 0.0)
            t.location, t.rotation_euler = loc, rotm
            _link(t, parent=obj)
    obj.location = (x, y, 0.0)
    obj.rotation_euler = (0, 0, math.radians(rot))
    obj["lesson_type"] = type
    obj["lesson_pins"] = json.dumps({k: list(v) for k, v in local_pins.items()})
    R["parts"][pid] = {"type": type, "label": label or "", "color": color}
    bpy.context.view_layer.update()
    if label:  # every part label is shown, just above the part (move it with label_at)
        lo, hi = _world_bbox(obj)
        lab = _label_obj(f"{pid}_label", label, 0.15, "#F4F6F8")
        lab.location = ((lo.x + hi.x) / 2, lo.y - 0.15, hi.z + 0.3)
        lab["lesson_near"] = pid
        R["labels"][f"{pid}_label"] = {"text": label, "near": pid, "auto": True}
    return pid


def label_at(id, x=None, y=None, z=None):
    """Move any label (including automatic ones: '<part>_label', '<flow>_label') to x, y, z."""
    obj = bpy.data.objects.get(id)
    if obj is None or obj.get("lesson_kind") != "label":
        raise ToolkitError(f"no label called {id!r}")
    cur = obj.location
    obj.location = (_num(x, "x") if x is not None else cur.x, _num(y, "y") if y is not None else cur.y,
                    _num(z, "z") if z is not None else cur.z)
    return id


def pin(ref):
    """World position (x, y, z) of 'part.pin'."""
    if not isinstance(ref, str) or "." not in ref:
        raise ToolkitError(f"pin reference must look like 'q1.B', got {ref!r}")
    pid, _, name = ref.partition(".")
    obj = bpy.data.objects.get(pid)
    if obj is None or obj.get("lesson_kind") != "part":
        raise ToolkitError(f"unknown part {pid!r} in {ref!r}")
    pins = json.loads(obj["lesson_pins"])
    if name not in pins:
        raise ToolkitError(f"{pid} has no pin {name!r}; pins: {list(pins)}")
    p = obj.matrix_world @ Vector(pins[name])
    return (round(p.x, 4), round(p.y, 4), round(p.z, 4))


def _chaikin(points, iterations=3):
    pts = [Vector(p) for p in points]
    for _ in range(iterations):
        if len(pts) < 3:
            break
        new = [pts[0]]
        for a, b in zip(pts, pts[1:]):
            new += [a.lerp(b, 0.25), a.lerp(b, 0.75)]
        new.append(pts[-1])
        pts = new
    return pts


def _densify(points, step=0.08):
    out = [Vector(points[0])]
    for a, b in zip(points, points[1:]):
        a, b = Vector(a), Vector(b)
        n = max(1, int((b - a).length / step))
        out += [a.lerp(b, (i + 1) / n) for i in range(n)]
    return out


def wire(id, frm, to, via=None, color=None, height=0.14):
    """Wire from pin to pin. via = [(x, y) or (x, y, z), ...] waypoints; default: a tidy right-angle route."""
    wid = _id(id)
    p0, p1 = Vector(pin(frm)), Vector(pin(to))
    h = _num(height, "height")
    if via:
        mids = []
        for v in via:
            v = [_num(c, "via coordinate") for c in v]
            mids.append(Vector((v[0], v[1], v[2] if len(v) > 2 else h)))
    else:
        mids = [Vector((p0.x, p0.y, max(h, min(p0.z, 0.6)))), Vector((p1.x, p0.y, h)),
                Vector((p1.x, p1.y, max(h, min(p1.z, 0.6))))]
        if abs(p1.y - p0.y) > abs(p1.x - p0.x):  # go along Y first when that is the long way
            mids[1] = Vector((p0.x, p1.y, h))
    rough = [p0, Vector((p0.x, p0.y, max(p0.z, h))) if p0.z < h else p0 + Vector((0, 0, 0.12))] + mids
    rough += [Vector((p1.x, p1.y, max(p1.z, h))) if p1.z < h else p1 + Vector((0, 0, 0.12)), p1]
    dedup = [rough[0]]
    for p in rough[1:]:
        if (p - dedup[-1]).length > 1e-3:
            dedup.append(p)
    pts = _densify(_chaikin(dedup), 0.06)
    pts[0], pts[-1] = p0, p1  # exact pin contact
    cu = C._poly_curve(wid, pts, bevel=0.04)
    cu.materials.append(C._mat(f"td_les_{wid}", _hex(color, "#E58A1F"), rough=0.38))
    obj = _link(bpy.data.objects.new(wid, cu), wid, "wire")
    obj["lesson_from"], obj["lesson_to"] = frm, to
    R["wires"][wid] = {"from": frm, "to": to, "color": color}
    return wid


def _wire_points(wid):
    obj = bpy.data.objects[wid]
    return [obj.matrix_world @ Vector(p.co[:3]) for p in obj.data.splines[0].points]


def flow(id, path, current_ma, label=None, color="yellow"):
    """Conventional-current markers along wires `path` (in current direction). Speed proportional to current_ma."""
    fid = _id(id)
    if not isinstance(path, (list, tuple)) or not path:
        raise ToolkitError("flow path must be a list of wire ids")
    for w in path:
        if w not in R["wires"]:
            raise ToolkitError(f"flow {fid}: {w!r} is not a wire")
    ma = _num(current_ma, "current_ma")
    pts: list[Vector] = []
    for i, w in enumerate(path):
        wp = _wire_points(w)
        ends = (R["wires"][w]["from"].split(".")[0], R["wires"][w]["to"].split(".")[0])
        if i + 1 < len(path):  # orient so this wire ends on the part shared with the next one
            nxt = {R["wires"][path[i + 1]]["from"].split(".")[0], R["wires"][path[i + 1]]["to"].split(".")[0]}
            if ends[1] not in nxt and ends[0] in nxt:
                wp.reverse()
        elif pts and (wp[0] - pts[-1]).length > (wp[-1] - pts[-1]).length:
            wp.reverse()
        pts += wp
    cu = C._poly_curve(fid, pts, cyclic=False)
    obj = _link(bpy.data.objects.new(fid, cu), fid, "flow")
    mod = obj.modifiers.new("Flow", "NODES")
    mod.node_group = C._flow_group()
    ins = C._flow_inputs(obj)
    length = sum((b - a).length for a, b in zip(pts, pts[1:]))
    speed = min(MAX_M_PER_FRAME, FLOW_M_PER_FRAME_PER_MA * max(ma, 0.0))
    ins["Laps per frame"][0].value = speed / length if length > 0 else 0.0
    ins["Count"][0].value = max(2, int(length / MARKER_SPACING))
    ins["Material"][0].value = C._mat(f"td_les_flow_{fid}", _hex(color, "#FFD45C"), rough=0.3, emit=4.0)
    ins["Power"][0].value = 1.0 if ma > 0 else 0.0
    obj["lesson_current_ma"] = ma
    obj["lesson_path"] = json.dumps(list(path))
    R["flows"][fid] = {"path": list(path), "current_ma": ma, "label": label or ""}
    if label:
        label_obj = _label_obj(f"{fid}_label", label, 0.15, "#FFD45C")
        mid = pts[len(pts) // 2]
        label_obj.location = (mid.x, mid.y, mid.z + 0.45)
        label_obj["lesson_near"] = fid
        R["labels"][f"{fid}_label"] = {"text": label, "near": fid, "auto": True}
    return fid


def _plate(text_obj):
    """Dark backing card behind a text object (child, resized by C._fit_plate)."""
    me = bpy.data.meshes.new(f"{text_obj.name}_plate")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    me.materials.append(bpy.data.materials.get("td_circ_plate") or C._mat("td_circ_plate", "#0B1016", rough=0.9,
                                                                           alpha=0.82))
    return _link(bpy.data.objects.new(f"{text_obj.name}_plate", me), parent=text_obj)


def _board_mat():
    mat = bpy.data.materials.get("td_les_board")
    if mat is not None:
        return mat
    mat = bpy.data.materials.new("td_les_board")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.75
    brick = nt.nodes.new("ShaderNodeTexBrick")
    brick.offset = 0.0
    for key, val in (("Color1", "#18212C"), ("Color2", "#1A2430"), ("Mortar", "#2A3A4C")):
        brick.inputs[key].default_value = C._lin(val)
    for key, val in (("Scale", 1.0), ("Mortar Size", 0.012), ("Brick Width", 0.25), ("Row Height", 0.25)):
        brick.inputs[key].default_value = val
    tc = nt.nodes.new("ShaderNodeTexCoord")
    nt.links.new(tc.outputs["Object"], brick.inputs["Vector"])
    nt.links.new(brick.outputs["Color"], bsdf.inputs["Base Color"])
    mat.diffuse_color = C._lin("#18212C")
    return mat


def _label_obj(lid, text, size, hexc):
    obj = C._text_obj(lid, str(text), size, hexc, "CENTER")
    _link(obj, lid, "label")
    _plate(obj)
    return obj


def label(id, text, near=None, x=None, y=None, z=None, size=0.16, color="white"):
    """3D label next to a part/wire (near=id) or at x, y, z. Turned to face the camera automatically."""
    lid = _id(id)
    if not isinstance(text, str) or not text.strip():
        raise ToolkitError("label text must be a non-empty string")
    obj = _label_obj(lid, text, _num(size, "size"), _hex(color, "#F4F6F8"))
    if near is not None:
        tgt = bpy.data.objects.get(near)
        if tgt is None:
            raise ToolkitError(f"label {lid}: nothing called {near!r}")
        if tgt.get("lesson_kind") == "wire":
            pts = _wire_points(near)
            m = pts[len(pts) // 2]
            obj.location = (m.x, m.y, m.z + 0.4)
        else:
            lo, hi = _world_bbox(tgt)
            obj.location = ((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, hi.z + 0.35)
        obj["lesson_near"] = near
    if x is not None or y is not None or z is not None:
        cur = obj.location
        obj.location = (_num(x, "x") if x is not None else cur.x, _num(y, "y") if y is not None else cur.y,
                        _num(z, "z") if z is not None else cur.z)
    R["labels"][lid] = {"text": text, "near": near}
    return lid


def key_points(points):
    if not isinstance(points, (list, tuple)) or not 1 <= len(points) <= 4:
        raise ToolkitError("key_points needs a list of 1-4 strings")
    R["key_points"] = [str(p) for p in points]
    return len(points)


def step(show, caption):
    """Reveal `show` ids (parts, wires, flows, labels) with this caption. Steps play in call order."""
    if not isinstance(show, (list, tuple)):
        raise ToolkitError("step(show=[ids], caption)")
    R["steps"].append({"show": [str(s) for s in show], "caption": str(caption)})
    return len(R["steps"])


def _prim(id, part, loc, color, emit=0.0):
    pid = _id(id)
    parent = None
    if part is not None:
        parent = bpy.data.objects.get(part)
        if parent is None or parent.get("lesson_kind") != "part":
            raise ToolkitError(f"{pid}: unknown part {part!r}")
    return pid, parent, Vector([_num(c, "position") for c in loc]), _hex(color, "#9AA3AD"), _num(emit, "emit")


def _prim_obj(pid, bm, hexc, emit, parent, loc):
    obj = _link(C._mesh_obj(pid, bm, [C._mat(f"td_les_{pid}", hexc, rough=0.45, emit=emit)]), pid, "prim", parent=parent)
    obj.location = loc
    R["prims"][pid] = {"part": parent.name if parent else None}
    return pid


def box(id, sx, sy, sz, x=0.0, y=0.0, z=0.0, part=None, color="gray", emit=0.0):
    """Rounded box of size (sx, sy, sz) with its BOTTOM at z (relative to `part` if given)."""
    pid, parent, loc, hexc, em = _prim(id, part, (x, y, z), color, emit)
    bm = bmesh.new()
    C._rounded_box(bm, (_num(sx, "sx"), _num(sy, "sy"), _num(sz, "sz")), (0, 0, sz / 2), min(sx, sy, sz) * 0.08, 0)
    return _prim_obj(pid, bm, hexc, em, parent, loc)


def cylinder(id, r, depth, x=0.0, y=0.0, z=0.0, axis="z", part=None, color="gray", emit=0.0):
    """Cylinder; bottom at z for axis='z', centered for axis 'x'/'y'."""
    pid, parent, loc, hexc, em = _prim(id, part, (x, y, z), color, emit)
    rot = {"z": Matrix.Translation((0, 0, depth / 2)), "x": Matrix.Rotation(math.radians(90), 4, "Y"),
           "y": Matrix.Rotation(math.radians(90), 4, "X")}.get(axis)
    if rot is None:
        raise ToolkitError("axis must be 'x', 'y' or 'z'")
    bm = bmesh.new()
    C._cyl(bm, _num(r, "r"), _num(r, "r"), _num(depth, "depth"), rot, 0, segments=32)
    return _prim_obj(pid, bm, hexc, em, parent, loc)


def sphere(id, r, x=0.0, y=0.0, z=0.0, part=None, color="gray", emit=0.0):
    pid, parent, loc, hexc, em = _prim(id, part, (x, y, z), color, emit)
    bm = bmesh.new()
    C._sphere(bm, _num(r, "r"), Matrix.Identity(4), 0)
    return _prim_obj(pid, bm, hexc, em, parent, loc)


def text3d(id, text, x=0.0, y=0.0, z=0.0, size=0.15, part=None, color="white", flat=True):
    """Small printed text (e.g. markings on a custom part). flat=True lies on top, else faces -Y."""
    pid, parent, loc, hexc, _ = _prim(id, part, (x, y, z), color)
    obj = C._text_obj(pid, str(text), _num(size, "size"), hexc, "CENTER", emit=0.3)
    _link(obj, pid, "prim", parent=parent)
    obj.location = loc
    if not flat:
        obj.rotation_euler = (math.radians(90), 0, 0)
    R["prims"][pid] = {"part": part}
    return pid


API = {"scene": scene, "part": part, "pin": pin, "wire": wire, "flow": flow, "label": label, "label_at": label_at,
       "key_points": key_points, "step": step, "box": box, "cylinder": cylinder, "sphere": sphere, "text3d": text3d}


# ---------------- finish: board, camera, lights, HUD, timeline ----------------

def _world_bbox(obj):
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    for ch in obj.children_recursive:
        if ch.type in ("MESH", "FONT", "CURVE") and not ch.name.endswith("_plate"):
            pts += [ch.matrix_world @ Vector(c) for c in ch.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


def _content_bbox():
    objs = [o for o in _col().objects if o.get("lesson_kind") in ("part", "wire", "label", "prim") and o.parent is None]
    lo, hi = Vector((1e9, 1e9, 1e9)), Vector((-1e9, -1e9, -1e9))
    for o in objs:
        a, b = _world_bbox(o)
        lo = Vector((min(lo.x, a.x), min(lo.y, a.y), min(lo.z, a.z)))
        hi = Vector((max(hi.x, b.x), max(hi.y, b.y), max(hi.z, b.z)))
    return lo, hi


def _hud(cam, name, body, size, hexc, x, y, align="LEFT"):
    obj = C._text_obj(name, body, size, hexc, align)
    _link(obj, name, "hud", parent=cam)
    obj.location = (x, y, -C.HUD_DIST)
    _plate(obj)
    return obj


def _pop(obj, frame):
    s = tuple(obj.scale)
    obj.scale = (0, 0, 0)
    obj.keyframe_insert("scale", frame=max(1, frame))
    obj.scale = s
    obj.keyframe_insert("scale", frame=max(2, frame + 10))


def _visible_between(obj, f0, f1):
    obj.scale = (0, 0, 0)
    obj.keyframe_insert("scale", frame=1)
    for f, v in ((f0, 0), (f0 + 1, 1)) + (((f1, 1), (f1 + 1, 0)) if f1 else ()):
        obj.scale = (v, v, v)
        obj.keyframe_insert("scale", frame=f)
    for fc in C.engine.fcurves(obj):
        for kp in fc.keyframe_points:
            kp.interpolation = "CONSTANT"


def finish():
    """Board, camera framing, lights, HUD, step timeline. Called by the runner after builder code."""
    if "title" not in R:
        raise ToolkitError("the code never called scene(title)")
    if not R["parts"]:
        raise ToolkitError("the code placed no parts")
    bpy.context.view_layer.update()
    lo, hi = _content_bbox()
    center = (lo + hi) / 2
    size = hi - lo
    board = bmesh.new()
    C._rounded_box(board, (size.x + 1.6, size.y + 1.6, 0.16), (center.x, center.y, -0.08), 0.07, 0)
    _link(C._mesh_obj("lesson_board", board, [_board_mat()]), None, None)

    # camera: fixed viewing direction, distance to fit the content, content kept in the middle band
    cam_data = bpy.data.cameras.new("TD_LessonCam")
    cam_data.lens = LENS
    cam = _link(bpy.data.objects.new("TD_LessonCam", cam_data))
    radius = max(size.x, size.y * 0.8, 2.0) / 2 + 0.6
    fov = 2 * math.atan(cam_data.sensor_width / (2 * LENS))
    dist = radius / math.tan(fov / 2) * 1.05
    target = Vector((center.x, center.y, max(0.4, size.z * 0.35))) + Vector((0, 0, radius * 0.12))
    cam.location = target + CAM_DIR * dist
    cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
    sc0 = bpy.context.scene
    sc0.camera = cam
    sc0.render.resolution_x, sc0.render.resolution_y = 1280, 720
    _fit_camera(cam)
    _declutter_labels(cam)
    _fit_camera(cam)
    for name, kind, off, energy, color, sz in (("TD_LessonKey", "AREA", (-0.4, -0.6, 0.9), 1100, (1, 0.96, 0.92), 6),
                                               ("TD_LessonRim", "AREA", (0.4, 0.8, 0.5), 600, (0.55, 0.7, 1), 5),
                                               ("TD_LessonFill", "AREA", (0.9, -0.3, 0.3), 200, (0.8, 0.85, 1), 4)):
        ld = bpy.data.lights.new(name, kind)
        ld.energy, ld.color, ld.size = energy * max(1.0, radius / 4) ** 2, color, sz
        lo_ = _link(bpy.data.objects.new(name, ld))
        lo_.location = center + Vector(off) * max(8.0, radius * 2)
        lo_.rotation_euler = (center - lo_.location).to_track_quat("-Z", "Y").to_euler()
    C._world()
    sc = bpy.context.scene
    sc.camera = cam
    sc.render.fps, sc.render.resolution_x, sc.render.resolution_y = 24, 1280, 720
    try:
        sc.render.engine = "BLENDER_EEVEE"
    except TypeError:
        sc.render.engine = "BLENDER_EEVEE_NEXT"
    sc.view_settings.view_transform = "AgX"
    C._glare(sc)

    # labels face the camera
    for lid in R["labels"]:
        o = bpy.data.objects[lid]
        o.rotation_euler = (cam.location - o.location).to_track_quat("Z", "Y").to_euler()

    # HUD: title, step captions, key points
    title = _hud(cam, "hud_title", _wrap(R["title"], 34), 0.072, "#FFFFFF", -1.45, 0.8)
    bpy.context.view_layer.update()
    cap_y = 0.8 - (max(c[1] for c in title.bound_box) - min(c[1] for c in title.bound_box)) - 0.07
    kp = "\n".join(f"•  {k}" for k in R["key_points"])
    if kp:
        _hud(cam, "hud_key_points", _wrap(kp, 52), 0.034, "#E9EEF3", 0.2, 0.8)
    _hud(cam, "hud_legend", "Glowing arrows: conventional current (+ to −), speed proportional to current",
         0.026, "#AAB4BF", 1.45, -0.78, align="RIGHT")

    # timeline: steps reveal things in order; anything never shown appears with the last step
    steps = R["steps"] or [{"show": [], "caption": ""}]
    declared = set(R["parts"]) | set(R["wires"]) | set(R["flows"]) | {k for k, v in R["labels"].items() if not v.get("auto")}
    shown = {i for s in steps for i in s["show"]}
    steps[-1]["show"] = steps[-1]["show"] + sorted(declared - shown)
    frame_of = {}
    for i, s in enumerate(steps):
        f0 = 1 + i * STEP_FRAMES
        for oid in s["show"]:
            frame_of.setdefault(oid, f0)
        cap = _hud(cam, f"hud_caption_{i + 1}", _wrap(s["caption"], 52), 0.042, "#FFD45C", -1.45, cap_y)
        _visible_between(cap, f0, 1 + (i + 1) * STEP_FRAMES - 1 if i + 1 < len(steps) else None)
    for oid, f in frame_of.items():
        obj = bpy.data.objects.get(oid)
        if obj is None:
            continue
        kind = obj.get("lesson_kind")
        if kind in ("part", "label"):
            _pop(obj, f)
            lab = bpy.data.objects.get(f"{oid}_label") if kind == "part" else None
            if lab is not None:
                _pop(lab, f + 6)
        elif kind == "wire":
            C._two_keys(obj.data, "bevel_factor_end", f, 0.0, f + 18, 1.0, interp="SINE", easing="EASE_IN_OUT")
        elif kind == "flow":
            power = C._flow_inputs(obj)["Power"][1]
            on = 1.0 if obj["lesson_current_ma"] > 0 else 0.0
            C._two_keys(obj, f'modifiers["Flow"].properties.inputs.{power}.value', f, 0.0, f + 12, on)
            lab = bpy.data.objects.get(f"{oid}_label")
            if lab is not None:
                _pop(lab, f + 6)
        if kind == "flow":
            for led in [o for o in _col().objects if o.get("lesson_type") == "led"]:
                _led_glow(led, f)
    sc.frame_start, sc.frame_end = 1, 1 + len(steps) * STEP_FRAMES + HOLD_FRAMES
    for lid in R["labels"]:
        bpy.context.view_layer.update()
        C._fit_plate(bpy.data.objects[lid])
    for h in [o for o in _col().objects if o.get("lesson_kind") == "hud"]:
        C._fit_plate(h)
    sc["td_lesson"] = json.dumps(R)
    sc.frame_set(sc.frame_end)


CONTENT_AREA = (0.03, 0.04, 0.97, 0.70)  # x0, y0, x1, y1 of the frame kept free of the HUD (top band)


def _content_points():
    pts = []
    for o in _col().objects:
        if o.get("lesson_kind") in ("part", "wire", "label") and o.type in ("MESH", "CURVE", "FONT"):
            pts += [o.matrix_world @ Vector(c) for c in o.bound_box]
    return pts


def _fit_camera(cam):
    """Move/zoom the camera (no rotation) until all content sits inside CONTENT_AREA."""
    sc = bpy.context.scene
    fov = 2 * math.atan(cam.data.sensor_width / (2 * cam.data.lens))
    fwd = cam.matrix_world.to_3x3() @ Vector((0, 0, -1))
    for _ in range(60):
        bpy.context.view_layer.update()
        pr = [world_to_camera_view(sc, cam, p) for p in _content_points()]
        x0, y0 = min(p.x for p in pr), min(p.y for p in pr)
        x1, y1 = max(p.x for p in pr), max(p.y for p in pr)
        ax0, ay0, ax1, ay1 = CONTENT_AREA
        if x0 >= ax0 and y0 >= ay0 and x1 <= ax1 and y1 <= ay1 and (x1 - x0 > 0.8 * (ax1 - ax0) or y1 - y0 > 0.8 * (ay1 - ay0)):
            break
        depth = sum(max(0.1, (p.z)) for p in pr) / len(pr)
        vis_w = 2 * depth * math.tan(fov / 2)
        vis_h = vis_w * sc.render.resolution_y / sc.render.resolution_x
        right = cam.matrix_world.to_3x3() @ Vector((1, 0, 0))
        up = cam.matrix_world.to_3x3() @ Vector((0, 1, 0))
        cam.location += right * ((x0 + x1) / 2 - (ax0 + ax1) / 2) * vis_w * 0.8
        cam.location += up * ((y0 + y1) / 2 - (ay0 + ay1) / 2) * vis_h * 0.8
        too_big = (x1 - x0) > (ax1 - ax0) or (y1 - y0) > (ay1 - ay0)
        too_small = (x1 - x0) < 0.8 * (ax1 - ax0) and (y1 - y0) < 0.8 * (ay1 - ay0)
        if too_big:
            cam.location -= fwd * depth * 0.07
        elif too_small:
            cam.location += fwd * depth * 0.05


def _rect(o, cam):
    sc = bpy.context.scene
    pr = [world_to_camera_view(sc, cam, o.matrix_world @ Vector(c)) for c in o.bound_box]
    return (min(p.x for p in pr), min(p.y for p in pr), max(p.x for p in pr), max(p.y for p in pr))


def _hit(a, b, pad=0.006):
    return a[0] < b[2] + pad and b[0] < a[2] + pad and a[1] < b[3] + pad and b[1] < a[3] + pad


def _declutter_labels(cam):
    """Lift labels (in world Z) until no label overlaps another label or a part it does not belong to."""
    labels = [o for o in _col().objects if o.get("lesson_kind") == "label"]
    parts = [o for o in _col().objects if o.get("lesson_kind") == "part" and o.type == "MESH"]
    for o in labels:
        o.rotation_euler = (cam.location - o.location).to_track_quat("Z", "Y").to_euler()
    bpy.context.view_layer.update()
    placed = []
    for o in sorted(labels, key=lambda o: o.location.z):
        for _ in range(14):
            bpy.context.view_layer.update()
            r = _rect(o, cam)
            blockers = [p for p in placed if _hit(r, p)] + [
                _rect(p, cam) for p in parts if p.name != o.get("lesson_near") and _hit(r, _rect(p, cam), 0.0)]
            if not blockers:
                break
            o.location.z += 0.22
        placed.append(_rect(o, cam))


def _led_glow(led, frame):
    """An LED lights when a flow through it starts; glow proportional to that flow's current."""
    flows = [bpy.data.objects[f] for f in R["flows"]]
    through = [f for f in flows if any(led.name in (R["wires"][w]["from"].split(".")[0], R["wires"][w]["to"].split(".")[0])
                                       for w in R["flows"][f.name]["path"])]
    if not through or led.get("glow_keyed"):
        return
    ma = max(f["lesson_current_ma"] for f in through)
    mat = led.material_slots[0].material
    sock = C._bsdf(mat).inputs["Emission Strength"]
    C._two_keys(mat.node_tree, sock.path_from_id("default_value"), frame, 0.0, frame + 12, min(40.0, 0.3 * ma))
    led["glow_keyed"] = True
    led["lesson_glow"] = min(40.0, 0.3 * ma)


def _wrap(text, width):
    out = []
    for line in str(text).split("\n"):
        out += textwrap.wrap(line, width=width, subsequent_indent="    " if line.startswith("•") else "") or [""]
    return "\n".join(out)


# ---------------- read back (from Blender data) ----------------

def _screen_rect(obj, cam):
    sc = bpy.context.scene
    pts = [world_to_camera_view(sc, cam, obj.matrix_world @ Vector(c)) for c in obj.bound_box]
    for ch in obj.children:
        if ch.name.endswith("_plate"):
            pts += [world_to_camera_view(sc, cam, ch.matrix_world @ Vector(c)) for c in ch.bound_box]
    return [round(min(p.x for p in pts), 4), round(min(p.y for p in pts), 4),
            round(max(p.x for p in pts), 4), round(max(p.y for p in pts), 4)]


def readback() -> dict:
    sc = bpy.context.scene
    cam = sc.camera
    bpy.context.view_layer.update()
    out = {"title": None, "parts": {}, "wires": {}, "flows": {}, "labels": {}, "hud": {}, "frames":
           [sc.frame_start, sc.frame_end]}
    for o in _col().objects:
        kind, lid = o.get("lesson_kind"), o.get("lesson_id")
        if not lid:
            continue
        if kind == "part":
            lo, hi = _world_bbox(o)
            pins = {k: [round(c, 4) for c in o.matrix_world @ Vector(v)] for k, v in json.loads(o["lesson_pins"]).items()}
            out["parts"][lid] = {"type": o["lesson_type"], "pins": pins, "bbox": [list(map(lambda v: round(v, 3), lo)),
                                                                                   list(map(lambda v: round(v, 3), hi))],
                                 "screen": _screen_rect(o, cam), "glow": o.get("lesson_glow")}
        elif kind == "wire":
            pts = _wire_points(lid)
            out["wires"][lid] = {"from": o["lesson_from"], "to": o["lesson_to"],
                                 "start": [round(c, 4) for c in pts[0]], "end": [round(c, 4) for c in pts[-1]],
                                 "points": [[round(c, 3) for c in p] for p in pts[:: max(1, len(pts) // 40)]]}
        elif kind == "flow":
            ins = C._flow_inputs(o)
            out["flows"][lid] = {"path": json.loads(o["lesson_path"]), "current_ma": o["lesson_current_ma"],
                                 "laps_per_frame": round(ins["Laps per frame"][0].value, 6),
                                 "markers": int(ins["Count"][0].value),
                                 "length_m": round(sum((b - a).length for a, b in zip(_pts(o), _pts(o)[1:])), 3)}
        elif kind == "label":
            out["labels"][lid] = {"text": o.data.body, "near": o.get("lesson_near"), "screen": _screen_rect(o, cam)}
        elif kind == "hud":
            out["hud"][lid] = o.data.body
            out.setdefault("hud_screen", {})[lid] = _screen_rect(o, cam)
    out["title"] = out["hud"].get("hud_title")
    return out


def _pts(o):
    return [o.matrix_world @ Vector(p.co[:3]) for p in o.data.splines[0].points]


def clear() -> None:
    removers = ((bpy.types.Mesh, bpy.data.meshes), (bpy.types.Curve, bpy.data.curves),
                (bpy.types.Light, bpy.data.lights), (bpy.types.Camera, bpy.data.cameras))
    for obj in [o for o in bpy.data.objects if o.get("lesson_part")]:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0:
            for kind, coll in removers:
                if isinstance(data, kind):
                    coll.remove(data)
                    break
    R.clear()
