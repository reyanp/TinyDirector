"""LED + resistor lesson scene: draws a circuit_request_v1 and reads the REAL state back.

The engine computes nothing electrical. Every value (labels, marker speed, LED glow, band colors) comes
from director/circuit/model.py via the request; this module only draws it, stores the circuit
parameters on the objects, and reads everything back from Blender data so the director can verify it.

Objects (stable ids, Blender name == id, custom prop circuit_id == id, all in collection TD_Circuit):
  board_1 battery_1 resistor_1 led_1 wire_1 wire_2 wire_3 flow_1 + label_* text objects.
  Helpers (camera, lights, plates, band materials) are prefixed TD_ / named after their owner.
build  = clear any previous lesson, construct it with a build-up animation (parts pop in, wires grow,
         then the power comes on).
update = change values on the EXISTING objects only (their circuit_uid stays the same).
Both are all-or-nothing: the request is checked first, and if Blender raises, the previous lesson
is restored.

Current flow: one curve object (flow_1) whose Geometry Nodes modifier places arrow markers along the
closed loop at fract(index/N + frame * laps_per_frame). Speed is a single modifier input.
"""

from __future__ import annotations

import json
import math
import re
import secrets
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

from . import engine

COLLECTION = "TD_Circuit"
REQUEST_KEY = "td_circuit_request"
FPS = 24
FRAME_END = 288  # 12 s loop
POWER_ON = (80, 92)  # circuit closes at 80, fully on at 92
SHOW_FRAME = 200  # a frame where everything is built and powered (read-back + preview)
COMPONENTS = ["board_1", "battery_1", "resistor_1", "led_1", "wire_1", "wire_2", "wire_3", "flow_1"]
LABELS = ["label_title", "label_battery", "label_resistor", "label_led", "label_current", "label_equation",
          "label_status", "label_caption", "label_legend"]
STATUS_COLORS = {"off": "#9AA3AD", "ok": "#7BE08A", "high": "#FFC04D", "over_max": "#FF5A4E"}
CAMERA = {"location": (1.7, -10.2, 6.9), "target": (0.15, 0.35, 1.05), "lens": 36}
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_FLOW_INPUTS = ("Laps per frame", "Power", "Count")


# ---------------- small helpers ----------------

def _lin(hex_color: str):
    return engine.hex_to_linear(hex_color)


def _collection():
    col = bpy.data.collections.get(COLLECTION)
    if col is None:
        col = bpy.data.collections.new(COLLECTION)
    if col.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(col)
    return col


def _link(obj, cid: str | None = None, parent=None, uid: str | None = None):
    _collection().objects.link(obj)
    obj["circuit_part"] = True
    if cid:
        obj["circuit_id"] = cid
        obj["circuit_uid"] = uid or secrets.token_hex(4)
    if parent is not None:
        obj.parent = parent
    return obj


def _mat(name: str, hex_color: str, rough: float = 0.5, metal: float = 0.0, emit: float = 0.0,
         emit_hex: str | None = None, alpha: float = 1.0):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = _lin(hex_color)
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    bsdf.inputs["Emission Color"].default_value = _lin(emit_hex or hex_color)
    bsdf.inputs["Emission Strength"].default_value = emit
    if alpha < 1.0:
        bsdf.inputs["Alpha"].default_value = alpha
    mat.diffuse_color = _lin(hex_color)
    mat["circuit_part"] = True
    return mat


def _bsdf(mat):
    return mat.node_tree.nodes.get("Principled BSDF")


def _set_mat_color(mat, hex_color: str, emit_too: bool = False) -> None:
    b = _bsdf(mat)
    b.inputs["Base Color"].default_value = _lin(hex_color)
    if emit_too:
        b.inputs["Emission Color"].default_value = _lin(hex_color)
    mat.diffuse_color = _lin(hex_color)


def _mat_hex(mat) -> str:
    return engine.linear_to_hex(_bsdf(mat).inputs["Base Color"].default_value)


def _mesh_obj(name: str, bm, mats: list) -> bpy.types.Object:
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for m in mats:
        me.materials.append(m)
    return bpy.data.objects.new(name, me)


def _tag(bm, verts, mat_index: int, smooth: bool = True) -> None:
    for f in {f for v in verts for f in v.link_faces}:
        f.material_index = mat_index
        f.smooth = smooth


def _cyl(bm, r1, r2, depth, matrix, mat_index, segments=32, smooth=True):
    g = bmesh.ops.create_cone(bm, cap_ends=True, segments=segments, radius1=r1, radius2=r2, depth=depth)
    bmesh.ops.transform(bm, matrix=matrix, verts=g["verts"])
    _tag(bm, g["verts"], mat_index, smooth)
    return g["verts"]


def _sphere(bm, r, matrix, mat_index, seg=32, rings=16):
    g = bmesh.ops.create_uvsphere(bm, u_segments=seg, v_segments=rings, radius=r)
    bmesh.ops.transform(bm, matrix=matrix, verts=g["verts"])
    _tag(bm, g["verts"], mat_index, True)
    return g["verts"]


def _rounded_box(bm, size, center, bevel, mat_index):
    g = bmesh.ops.create_cube(bm, size=1.0)
    verts = g["verts"]
    bmesh.ops.transform(bm, matrix=Matrix.Diagonal((*size, 1.0)), verts=verts)
    edges = list({e for v in verts for e in v.link_edges})
    res = bmesh.ops.bevel(bm, geom=edges + verts, offset=bevel, segments=4, affect="EDGES", profile=0.5)
    allv = set(verts) | {v for f in res.get("faces", []) for v in f.verts}
    allv = [v for v in allv if v.is_valid]
    bmesh.ops.translate(bm, vec=Vector(center), verts=allv)
    _tag(bm, allv, mat_index, True)
    return allv


def _along_x(x0: float, x1: float, y: float, z: float) -> Matrix:
    return Matrix.Translation(((x0 + x1) / 2, y, z)) @ Matrix.Rotation(math.radians(90), 4, "Y")


def _catmull(points, samples=12):
    """Smooth poly through control points (centripetal-ish Catmull-Rom, endpoints repeated)."""
    pts = [Vector(p) for p in points]
    ext = [pts[0]] + pts + [pts[-1]]
    out = []
    for i in range(1, len(ext) - 2):
        p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
        for s in range(samples):
            t = s / samples
            t2, t3 = t * t, t * t * t
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
                              + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    out.append(pts[-1])
    return out


def _poly_curve(name: str, points, bevel: float = 0.0, cyclic: bool = False):
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    sp = cu.splines.new("POLY")
    sp.points.add(len(points) - 1)
    for p, c in zip(sp.points, points):
        p.co = (c[0], c[1], c[2], 1.0)
    sp.use_cyclic_u = cyclic
    if bevel:
        cu.bevel_depth = bevel
        cu.bevel_resolution = 4
        cu.use_fill_caps = True
        cu.bevel_factor_mapping_end = "SPLINE"
    return cu


# ---------------- layout (meters; z=0 is the board's top) ----------------

BAT = Vector((-2.75, 0.0, 0.0))
BAT_SIZE = (0.6, 0.9, 1.55)
PLUS = BAT + Vector((0, 0.22, BAT_SIZE[2] + 0.12))
MINUS = BAT + Vector((0, -0.22, BAT_SIZE[2] + 0.1))
RES = Vector((0.0, 1.3, 0.42))
RES_LEAD = 0.98  # lead ends at RES.x +- this
LED = Vector((2.7, 0.0, 0.0))
LED_SCALE = 1.45
LEG_Y = 0.1 * LED_SCALE

WIRE_PTS = {
    "wire_1": [PLUS, PLUS + Vector((0.05, 0.25, 0.32)), (-2.2, 1.3, 1.15), (-1.55, 1.3, 0.46),
               (RES.x - RES_LEAD, RES.y, RES.z)],
    "wire_2": [(RES.x + RES_LEAD, RES.y, RES.z), (1.65, 1.3, 0.36), (2.4, 0.85, 0.12),
               (LED.x, LEG_Y, 0.05)],
    "wire_3": [(LED.x, -LEG_Y, 0.05), (2.4, -0.9, 0.12), (1.2, -1.45, 0.26), (-1.45, -1.45, 0.26),
               (-1.95, -0.95, 0.45), (-2.12, -0.45, 1.35), (-2.5, -0.26, 1.95), MINUS],
}
WIRE_COLORS = {"wire_1": "#D9342B", "wire_2": "#E58A1F", "wire_3": "#202226"}
WIRE_TIMES = {"wire_1": (30, 50), "wire_2": (46, 64), "wire_3": (60, 80)}
POP_TIMES = {"battery_1": 2, "resistor_1": 12, "led_1": 22}


def flow_loop_points():
    """Closed conventional-current path: + terminal -> wire_1 -> resistor -> wire_2 -> LED -> wire_3 -> - terminal
    -> through the battery back to +. The battery segment is hidden inside its body."""
    pts = []
    pts += _catmull(WIRE_PTS["wire_1"])[:-1]
    pts += [Vector((RES.x - RES_LEAD + i * (2 * RES_LEAD) / 10, RES.y, RES.z)) for i in range(10)]
    pts += _catmull(WIRE_PTS["wire_2"])[:-1]
    leg_top = 0.62 * LED_SCALE
    dome = 0.95 * LED_SCALE
    pts += [Vector((LED.x, LEG_Y, z)) for z in (0.05, leg_top * 0.5, leg_top)]
    pts += [Vector((LED.x, 0.0, dome))]
    pts += [Vector((LED.x, -LEG_Y, z)) for z in (leg_top, leg_top * 0.5)]
    pts += _catmull(WIRE_PTS["wire_3"])[:-1]
    pts += [MINUS, BAT + Vector((0, -0.22, 0.6)), BAT + Vector((0, 0.22, 0.6))]
    return pts


# ---------------- builders ----------------

def _build_board(uid=None):
    bm = bmesh.new()
    _rounded_box(bm, (7.8, 4.7, 0.16), (0.1, 0.0, -0.08), 0.07, 0)
    mat = bpy.data.materials.new("td_circ_board")
    mat.use_nodes = True
    mat["circuit_part"] = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    bsdf.inputs["Roughness"].default_value = 0.75
    brick = nt.nodes.new("ShaderNodeTexBrick")
    brick.offset = 0.0
    brick.inputs["Color1"].default_value = _lin("#18212C")
    brick.inputs["Color2"].default_value = _lin("#1A2430")
    brick.inputs["Mortar"].default_value = _lin("#2A3A4C")
    brick.inputs["Scale"].default_value = 1.0
    brick.inputs["Mortar Size"].default_value = 0.012
    brick.inputs["Brick Width"].default_value = 0.25
    brick.inputs["Row Height"].default_value = 0.25
    tc = nt.nodes.new("ShaderNodeTexCoord")
    nt.links.new(tc.outputs["Object"], brick.inputs["Vector"])
    nt.links.new(brick.outputs["Color"], bsdf.inputs["Base Color"])
    mat.diffuse_color = _lin("#18212C")
    return _link(_mesh_obj("board_1", bm, [mat]), "board_1", uid=uid)


def _build_battery(uid=None):
    bm = bmesh.new()
    w, d, h = BAT_SIZE
    _rounded_box(bm, (w, d, h * 0.58), (0, 0, h * 0.29), 0.06, 0)  # lower body (dark)
    _rounded_box(bm, (w * 1.002, d * 1.002, h * 0.44), (0, 0, h * 0.78), 0.06, 1)  # upper label band
    _rounded_box(bm, (w * 0.86, d * 0.9, 0.05), (0, 0, h + 0.005), 0.02, 0)  # top plate
    _cyl(bm, 0.085, 0.085, 0.14, Matrix.Translation((0, 0.22, h + 0.07)), 2)  # + snap (round)
    _cyl(bm, 0.12, 0.12, 0.11, Matrix.Translation((0, -0.22, h + 0.055)), 2, segments=6, smooth=False)  # - snap (hex)
    mats = [_mat("td_circ_battery_body", "#1C1D22", rough=0.35),
            _mat("td_circ_battery_band", "#E2A325", rough=0.35, metal=0.2),
            _mat("td_circ_metal", "#C9CDD2", rough=0.22, metal=1.0)]
    obj = _link(_mesh_obj("battery_1", bm, mats), "battery_1", uid=uid)
    obj.location = BAT
    # printed markings on the front face and the terminal signs (children: move with the battery)
    for name, body, loc, size, rot in (
            ("battery_1_mark", "9V", (0.0, -d / 2 - 0.004, h * 0.79), 0.3, (math.radians(90), 0, 0)),
            ("battery_1_plus", "+", (0.2, 0.22, h + 0.02), 0.22, (0, 0, 0)),
            ("battery_1_minus", "−", (0.2, -0.22, h + 0.02), 0.22, (0, 0, 0))):
        t = _text_obj(name, body, size, "#151515" if name.endswith("mark") else "#F2F2F2", "CENTER", emit=0.0)
        t.location, t.rotation_euler = loc, rot
        _link(t, parent=obj)
    return obj


def _build_resistor(bands_hex, uid=None):
    bm = bmesh.new()
    _cyl(bm, 0.22, 0.22, 0.9, _along_x(-0.45, 0.45, 0, 0), 0, segments=48)  # body (enlarged so bands read)
    for sx in (-1, 1):  # bulged ends of a classic through-hole resistor
        _sphere(bm, 0.27, Matrix.Translation((sx * 0.52, 0, 0)) @ Matrix.Diagonal((0.9, 1, 1, 1)), 0)
    _cyl(bm, 0.03, 0.03, 2 * RES_LEAD, _along_x(-RES_LEAD, RES_LEAD, 0, 0), 1, segments=16)  # leads
    for i, x in enumerate((-0.52, -0.29, -0.09, 0.41)):
        r = 0.282 if i in (0, 3) else 0.228
        _cyl(bm, r, r, 0.095, _along_x(x - 0.0475, x + 0.0475, 0, 0), 2 + i, segments=48)
    mats = [_mat("td_circ_resistor_body", "#D8C39A", rough=0.45), bpy.data.materials.get("td_circ_metal")
            or _mat("td_circ_metal", "#C9CDD2", rough=0.22, metal=1.0)]
    mats += [_mat(f"td_circ_resistor_band_{i + 1}", bands_hex[i], rough=0.4) for i in range(4)]
    obj = _link(_mesh_obj("resistor_1", bm, mats), "resistor_1", uid=uid)
    obj.location = RES
    return obj


def _build_led(uid=None):
    s = LED_SCALE
    bm = bmesh.new()
    leg_top = 0.62
    _cyl(bm, 0.022, 0.022, leg_top + 0.02, Matrix.Translation((0, 0.1, leg_top / 2)), 1, segments=12)  # anode (long)
    _cyl(bm, 0.022, 0.022, leg_top - 0.04, Matrix.Translation((0, -0.1, leg_top / 2 + 0.02)), 1, segments=12)
    _cyl(bm, 0.235, 0.235, 0.05, Matrix.Translation((0, 0, leg_top + 0.025)), 0, segments=48)  # flange
    _cyl(bm, 0.2, 0.2, 0.34, Matrix.Translation((0, 0, leg_top + 0.05 + 0.17)), 0, segments=48)  # body
    dome = [v for v in _sphere(bm, 0.2, Matrix.Translation((0, 0, leg_top + 0.39)), 0, seg=48, rings=24)]
    bmesh.ops.delete(bm, geom=[v for v in dome if v.co.z < leg_top + 0.38], context="VERTS")
    led_mat = _mat("td_circ_led", "#E8261E", rough=0.12, emit=0.0, emit_hex="#FF2A1A")
    _bsdf(led_mat).inputs["Coat Weight"].default_value = 0.6
    mats = [led_mat, bpy.data.materials.get("td_circ_metal")]
    obj = _link(_mesh_obj("led_1", bm, mats), "led_1", uid=uid)
    obj.location = LED
    obj.scale = (s, s, s)
    light = bpy.data.lights.new("TD_LedLight", "POINT")
    light.color = (1.0, 0.18, 0.1)
    light.shadow_soft_size = 0.15
    light.energy = 0.0
    lo = _link(bpy.data.objects.new("TD_LedLight", light), parent=obj)
    lo.location = (0, 0, leg_top + 0.3)
    return obj


def _build_wire(wid: str, uid=None):
    cu = _poly_curve(wid, _catmull(WIRE_PTS[wid]), bevel=0.042)
    cu.materials.append(_mat(f"td_circ_{wid}", WIRE_COLORS[wid], rough=0.38))
    return _link(bpy.data.objects.new(wid, cu), wid, uid=uid)


def _flow_group():
    ng = bpy.data.node_groups.get("TD_CircuitFlow")
    if ng is not None:
        return ng
    ng = bpy.data.node_groups.new("TD_CircuitFlow", "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Laps per frame", in_out="INPUT", socket_type="NodeSocketFloat")
    ng.interface.new_socket("Power", in_out="INPUT", socket_type="NodeSocketFloat")
    ng.interface.new_socket("Count", in_out="INPUT", socket_type="NodeSocketInt")
    ng.interface.new_socket("Material", in_out="INPUT", socket_type="NodeSocketMaterial")
    N, L = ng.nodes, ng.links
    gin, gout = N.new("NodeGroupInput"), N.new("NodeGroupOutput")

    def m(op, a, b=None):
        node = N.new("ShaderNodeMath")
        node.operation = op
        L.new(a, node.inputs[0])
        if b is not None:
            L.new(b, node.inputs[1])
        return node.outputs[0]

    t = N.new("GeometryNodeInputSceneTime")
    idx = N.new("GeometryNodeInputIndex")
    phase = m("FRACT", m("ADD", m("DIVIDE", idx.outputs[0], gin.outputs["Count"]),
                         m("MULTIPLY", t.outputs["Frame"], gin.outputs["Laps per frame"])))
    samp = N.new("GeometryNodeSampleCurve")
    samp.mode = "FACTOR"
    L.new(gin.outputs["Geometry"], samp.inputs["Curves"])
    L.new(phase, samp.inputs["Factor"])
    pts = N.new("GeometryNodePoints")
    L.new(gin.outputs["Count"], pts.inputs["Count"])
    L.new(samp.outputs["Position"], pts.inputs["Position"])
    cone = N.new("GeometryNodeMeshCone")
    cone.inputs["Vertices"].default_value = 16
    cone.inputs["Radius Bottom"].default_value = 0.07
    cone.inputs["Radius Top"].default_value = 0.0
    cone.inputs["Depth"].default_value = 0.2
    setm = N.new("GeometryNodeSetMaterial")
    L.new(cone.outputs["Mesh"], setm.inputs["Geometry"])
    L.new(gin.outputs["Material"], setm.inputs["Material"])
    align = N.new("FunctionNodeAlignRotationToVector")
    align.axis = "Z"
    L.new(samp.outputs["Tangent"], align.inputs["Vector"])
    inst = N.new("GeometryNodeInstanceOnPoints")
    L.new(pts.outputs[0], inst.inputs["Points"])
    L.new(setm.outputs[0], inst.inputs["Instance"])
    L.new(align.outputs[0], inst.inputs["Rotation"])
    L.new(gin.outputs["Power"], inst.inputs["Scale"])
    L.new(inst.outputs[0], gout.inputs["Geometry"])
    return ng


def _flow_inputs(obj) -> dict:
    mod = obj.modifiers["Flow"]
    ids = {s.name: s.identifier for s in mod.node_group.interface.items_tree if getattr(s, "in_out", "") == "INPUT"}
    return {name: (getattr(mod.properties.inputs, ids[name]), ids[name]) for name in (*_FLOW_INPUTS, "Material")}


def _build_flow(uid=None):
    cu = _poly_curve("flow_1", flow_loop_points(), cyclic=True)
    obj = _link(bpy.data.objects.new("flow_1", cu), "flow_1", uid=uid)
    mod = obj.modifiers.new("Flow", "NODES")
    mod.node_group = _flow_group()
    ins = _flow_inputs(obj)
    ins["Material"][0].value = _mat("td_circ_flow", "#FFD45C", rough=0.3, emit=4.0, emit_hex="#FFC93D")
    return obj


def _text_obj(name: str, body: str, size: float, hex_color: str, align: str = "CENTER", emit: float = 1.4):
    cu = bpy.data.curves.new(name, "FONT")
    cu.body = body
    cu.size = size
    cu.align_x = align
    cu.align_y = "CENTER" if align == "CENTER" else "TOP"
    cu.extrude = 0.0
    cu.space_line = 1.08
    cu.materials.append(_mat(f"td_circ_{name}", hex_color, rough=0.6, emit=emit))
    return bpy.data.objects.new(name, cu)


LABEL_STYLE = {  # id: (size, color, align, world location or None for camera HUD, hud (x, y))
    "label_battery": (0.2, "#F4F6F8", "CENTER", (BAT.x, BAT.y - 0.1, 2.45), None),
    "label_resistor": (0.16, "#F4F6F8", "CENTER", (RES.x, RES.y, 1.42), None),
    "label_led": (0.16, "#FFB3AA", "CENTER", (LED.x + 0.05, LED.y, 2.25), None),
    "label_current": (0.27, "#FFD45C", "CENTER", (0.1, -1.55, 0.82), None),
    "label_title": (0.088, "#FFFFFF", "LEFT", None, (-1.4, 0.78)),
    "label_equation": (0.062, "#FFD45C", "LEFT", None, (-1.4, 0.645)),
    "label_status": (0.042, STATUS_COLORS["ok"], "LEFT", None, (-1.4, 0.55)),
    "label_caption": (0.042, "#E9EEF3", "LEFT", None, (-1.4, -0.56)),
    "label_legend": (0.03, "#AAB4BF", "RIGHT", None, (1.4, -0.76)),
}
HUD_DIST = 3.0


def _plate(label_obj, hud: bool):
    me = bpy.data.meshes.new(f"{label_obj.name}_plate")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    me.materials.append(bpy.data.materials.get("td_circ_plate")
                        or _mat("td_circ_plate", "#0B1016", rough=0.9, alpha=0.82))
    plate = _link(bpy.data.objects.new(f"{label_obj.name}_plate", me), parent=label_obj)
    plate["plate_hud"] = hud
    return plate


def _fit_plate(label_obj) -> None:
    plate = next((c for c in label_obj.children if c.name.endswith("_plate")), None)
    if plate is None:
        return
    bb = [Vector(c) for c in label_obj.bound_box]
    empty = not label_obj.data.body.strip()
    pad = label_obj.data.size * 0.45
    x0, x1 = min(v.x for v in bb) - pad, max(v.x for v in bb) + pad
    y0, y1 = min(v.y for v in bb) - pad, max(v.y for v in bb) + pad
    z = -0.004
    for v, co in zip(plate.data.vertices, ((x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z))):
        v.co = co
    plate.data.update()
    plate.hide_render = plate.hide_viewport = empty


def _build_labels(camera, labels: dict, uids=None):
    uids = uids or {}
    out = {}
    for lid in LABELS:
        size, color, align, loc, hud = LABEL_STYLE[lid]
        obj = _text_obj(lid, labels[lid], size, color, align)
        if hud is None:
            _link(obj, lid, uid=uids.get(lid))
            obj.location = loc
            obj.rotation_euler = (Vector(CAMERA["location"]) - Vector(loc)).to_track_quat("Z", "Y").to_euler()
        else:
            _link(obj, lid, parent=camera, uid=uids.get(lid))
            obj.location = (hud[0], hud[1], -HUD_DIST)
        _plate(obj, hud is not None)
        out[lid] = obj
    return out


def _build_camera_and_lights():
    cam_data = bpy.data.cameras.new("TD_CircuitCam")
    cam_data.lens = CAMERA["lens"]
    cam_data.clip_start = 0.1
    cam = _link(bpy.data.objects.new("TD_CircuitCam", cam_data))
    cam.location = CAMERA["location"]
    cam.rotation_euler = (Vector(CAMERA["target"]) - Vector(CAMERA["location"])).to_track_quat("-Z", "Y").to_euler()
    for name, kind, loc, energy, color, size in (
            ("TD_CircuitKey", "AREA", (-3.5, -4.5, 7.5), 900, (1.0, 0.96, 0.92), 5.0),
            ("TD_CircuitRim", "AREA", (3.5, 5.5, 4.5), 500, (0.55, 0.7, 1.0), 4.0),
            ("TD_CircuitFill", "AREA", (6.0, -3.0, 2.5), 160, (0.8, 0.85, 1.0), 3.0)):
        ld = bpy.data.lights.new(name, kind)
        ld.energy, ld.color = energy, color
        ld.size = size
        lo = _link(bpy.data.objects.new(name, ld))
        lo.location = loc
        lo.rotation_euler = (Vector((0, 0, 0.4)) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    return cam


def _world():
    world = bpy.data.worlds.get("TD_CircuitWorld") or bpy.data.worlds.new("TD_CircuitWorld")
    world.use_nodes = True
    nt = world.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg = nt.nodes.new("ShaderNodeBackground")
    grad = nt.nodes.new("ShaderNodeTexGradient")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(tc.outputs["Window"], sep.inputs[0])
    nt.links.new(sep.outputs["Y"], ramp.inputs["Fac"])
    ramp.color_ramp.elements[0].color = _lin("#070B12")
    ramp.color_ramp.elements[1].color = _lin("#1C2B40")
    nt.links.new(ramp.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = 1.0
    nt.links.new(bg.outputs[0], out.inputs["Surface"])
    nt.nodes.remove(grad)
    bpy.context.scene.world = world


def _scene_settings(cam):
    sc = bpy.context.scene
    sc.camera = cam
    sc.render.fps, sc.render.fps_base = FPS, 1.0
    sc.frame_start, sc.frame_end = 1, FRAME_END
    sc.render.resolution_x, sc.render.resolution_y = 1280, 720
    try:
        sc.render.engine = "BLENDER_EEVEE"
    except TypeError:
        sc.render.engine = "BLENDER_EEVEE_NEXT"
    try:
        sc.eevee.taa_render_samples = 48
    except AttributeError:
        pass
    sc.view_settings.view_transform = "AgX"
    for look in ("AgX - Medium High Contrast", "Medium High Contrast"):
        try:
            sc.view_settings.look = look
            break
        except TypeError:
            continue
    sun = bpy.data.objects.get("TD_Sun")  # the worker's generic sun washes out the lesson lighting
    if sun is not None and not sun.get("td_hidden_by_circuit"):
        sun["td_hidden_by_circuit"] = True
        sun.hide_render = sun.hide_viewport = True
    _glare(sc)


def _glare(sc) -> None:
    """Soft bloom around emissive parts (compositor Glare). Best effort: skipped if the API differs."""
    try:
        ng = bpy.data.node_groups.get("TD_CircuitComp")
        if ng is None:
            ng = bpy.data.node_groups.new("TD_CircuitComp", "CompositorNodeTree")
            ng.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
            rl = ng.nodes.new("CompositorNodeRLayers")
            gl = ng.nodes.new("CompositorNodeGlare")
            out = ng.nodes.new("NodeGroupOutput")
            for key, val in (("Type", "Bloom"), ("Quality", "High")):
                if key in gl.inputs:
                    gl.inputs[key].default_value = val
            for key, val in (("Threshold", 1.2), ("Strength", 0.55), ("Size", 0.6)):
                if key in gl.inputs:
                    gl.inputs[key].default_value = val
            ng.links.new(rl.outputs["Image"], gl.inputs["Image"])
            ng.links.new(gl.outputs["Image"], out.inputs[0])
        sc.compositing_node_group = ng
        sc.render.use_compositing = True
    except Exception as exc:  # noqa: BLE001 - cosmetic only
        print(f"[TinyDirector] circuit: bloom skipped ({type(exc).__name__}: {exc})")


# ---------------- animation ----------------

def _fc(id_data, data_path: str, index: int = 0):
    for fc in engine.fcurves(id_data):
        if fc.data_path == data_path and fc.array_index == index:
            return fc
    return None


def _two_keys(id_data, data_path: str, f0: float, v0: float, f1: float, v1: float, interp="BEZIER",
              easing="AUTO", index: int = -1):
    """Insert v0 at f0 and v1 at f1 on data_path (all indices if index=-1)."""
    owner, prop = _resolve(id_data, data_path)
    n = len(getattr(owner, prop)) if index == -1 and hasattr(getattr(owner, prop), "__len__") else 1
    for f, v in ((f0, v0), (f1, v1)):
        if n > 1:
            setattr(owner, prop, [v] * n)
        else:
            setattr(owner, prop, v)
        id_data.keyframe_insert(data_path, frame=f)
    for i in range(n):
        fc = _fc(id_data, data_path, i if n > 1 else 0)
        for kp in fc.keyframe_points:
            kp.interpolation = interp
            if interp not in ("BEZIER", "LINEAR", "CONSTANT"):
                kp.easing = easing
        fc.update()


def _resolve(id_data, data_path: str):
    owner_path, _, prop = data_path.rpartition(".")
    owner = id_data.path_resolve(owner_path) if owner_path else id_data
    return owner, prop


def _set_on_value(id_data, data_path: str, value: float) -> None:
    """Change the powered-on value (the last key) of a power-on ramp, and the static value."""
    fc = _fc(id_data, data_path)
    if fc is None or not len(fc.keyframe_points):
        raise RuntimeError(f"{id_data.name}: no power-on keys on {data_path}")
    kp = fc.keyframe_points[-1]
    kp.co[1] = value
    kp.handle_left[1] = kp.handle_right[1] = value
    fc.update()
    owner, prop = _resolve(id_data, data_path)
    setattr(owner, prop, value)


def _led_paths(led):
    mat = led.material_slots[0].material
    sock = _bsdf(mat).inputs["Emission Strength"]
    light = next(c for c in led.children if c.name.startswith("TD_LedLight")).data
    return mat.node_tree, sock.path_from_id("default_value"), light


def _animate_build(objs: dict, display: dict) -> None:
    for cid, f in POP_TIMES.items():  # components pop in with a little overshoot
        obj = objs[cid]
        s = tuple(obj.scale)
        obj.scale = (0, 0, 0)
        obj.keyframe_insert("scale", frame=f)
        obj.scale = s
        obj.keyframe_insert("scale", frame=f + 10)
        for i in range(3):
            fc = _fc(obj, "scale", i)
            fc.keyframe_points[0].interpolation = "BACK"
            fc.keyframe_points[0].easing = "EASE_OUT"
    for wid, (f0, f1) in WIRE_TIMES.items():  # wires grow from one end to the other
        _two_keys(objs[wid].data, "bevel_factor_end", f0, 0.0, f1, 1.0, interp="SINE", easing="EASE_IN_OUT")
    flow = objs["flow_1"]
    power = _flow_inputs(flow)["Power"][1]
    _two_keys(flow, f'modifiers["Flow"].properties.inputs.{power}.value', POWER_ON[0], 0.0, POWER_ON[1],
              1.0 if display["flow_lap_frames"] else 0.0, interp="SINE", easing="EASE_OUT")
    tree, path, light = _led_paths(objs["led_1"])
    _two_keys(tree, path, POWER_ON[0], 0.0, POWER_ON[1], display["led_emission"], interp="SINE", easing="EASE_OUT")
    _two_keys(light, "energy", POWER_ON[0], 0.0, POWER_ON[1], display["led_light_w"], interp="SINE",
              easing="EASE_OUT")


def _label_pop(objs: dict) -> None:
    times = {"label_battery": 6, "label_resistor": 16, "label_led": 26, "label_current": POWER_ON[1],
             "label_equation": POWER_ON[1] + 4, "label_status": POWER_ON[1] + 8}
    for lid, f in times.items():
        obj = objs[lid]
        obj.scale = (0, 0, 0)
        obj.keyframe_insert("scale", frame=f)
        obj.scale = (1, 1, 1)
        obj.keyframe_insert("scale", frame=f + 8)


# ---------------- apply ----------------

def objects() -> dict:
    return {o["circuit_id"]: o for o in bpy.data.objects if "circuit_id" in o}


def present() -> bool:
    objs = objects()
    return all(cid in objs for cid in COMPONENTS + LABELS)


def check_request(req) -> list[str]:
    """Minimal structural check (the director validates against circuit_request_v1.json first)."""
    errs = []
    if not isinstance(req, dict) or req.get("kind") != "circuit" or req.get("schema_version") != 1:
        return ["not a circuit_request_v1 document"]
    if req.get("action") not in ("build", "update"):
        errs.append("action must be build or update")
    c, d = req.get("circuit"), req.get("display")
    if not isinstance(c, dict) or not isinstance(d, dict):
        return errs + ["circuit and display must be objects"]
    for k in ("source_v", "resistance_ohm", "led_vf"):
        if not isinstance(c.get(k), (int, float)) or isinstance(c.get(k), bool) or not c[k] > 0:
            errs.append(f"circuit.{k} must be a positive number")
    labels = d.get("labels")
    if not isinstance(labels, dict) or any(not isinstance(labels.get(k), str) for k in LABELS):
        errs.append("display.labels must give a string for every label")
    lap = d.get("flow_lap_frames")
    if lap is not None and (not isinstance(lap, (int, float)) or lap <= 0):
        errs.append("display.flow_lap_frames must be a positive number or null")
    for k in ("led_emission", "led_light_w"):
        if not isinstance(d.get(k), (int, float)) or d[k] < 0:
            errs.append(f"display.{k} must be a number >= 0")
    if not isinstance(d.get("flow_markers"), int) or not 1 <= d["flow_markers"] <= 200:
        errs.append("display.flow_markers must be an integer 1-200")
    if d.get("led_status") not in STATUS_COLORS:
        errs.append("display.led_status is invalid")
    bands = d.get("resistor_bands")
    if not (isinstance(bands, list) and len(bands) == 4 and all(isinstance(b, str) and HEX_RE.match(b) for b in bands)):
        errs.append("display.resistor_bands must be 4 #RRGGBB colors")
    if req.get("action") == "update" and not present():
        errs.append("no lesson circuit in the scene to update; build it first")
    return errs


def _apply_values(objs: dict, req: dict) -> None:
    """Write the request's values onto existing objects (used by build and update)."""
    c, d = req["circuit"], req["display"]
    objs["battery_1"]["source_v"] = float(c["source_v"])
    objs["resistor_1"]["resistance_ohm"] = float(c["resistance_ohm"])
    objs["led_1"]["led_vf"] = float(c["led_vf"])
    objs["led_1"]["led_status"] = d["led_status"]
    for i, hex_color in enumerate(d["resistor_bands"]):
        _set_mat_color(objs["resistor_1"].material_slots[2 + i].material, hex_color)
    for lid in LABELS:
        objs[lid].data.body = d["labels"][lid]
    _set_mat_color(objs["label_status"].material_slots[0].material, STATUS_COLORS[d["led_status"]], emit_too=True)
    ins = _flow_inputs(objs["flow_1"])
    lap = d["flow_lap_frames"]
    ins["Laps per frame"][0].value = 0.0 if lap is None else 1.0 / float(lap)
    ins["Count"][0].value = int(d["flow_markers"])
    _set_on_value(objs["flow_1"], f'modifiers["Flow"].properties.inputs.{ins["Power"][1]}.value',
                  1.0 if lap is not None else 0.0)
    tree, path, light = _led_paths(objs["led_1"])
    _set_on_value(tree, path, float(d["led_emission"]))
    _set_on_value(light, "energy", float(d["led_light_w"]))
    bpy.context.view_layer.update()
    for lid in LABELS:
        _fit_plate(objs[lid])


def _build(req: dict) -> None:
    clear(keep_settings=True)
    d = req["display"]
    cam = _build_camera_and_lights()
    objs = {"board_1": _build_board(), "battery_1": _build_battery(),
            "resistor_1": _build_resistor(d["resistor_bands"]), "led_1": _build_led()}
    for wid in ("wire_1", "wire_2", "wire_3"):
        objs[wid] = _build_wire(wid)
    objs["flow_1"] = _build_flow()
    objs.update(_build_labels(cam, d["labels"]))
    _world()
    _scene_settings(cam)
    _animate_build(objs, d)
    _label_pop(objs)
    _apply_values(objs, req)


def apply(req: dict) -> tuple[int, list[str]]:
    errs = check_request(req)
    if errs:
        return 0, errs + ["circuit request rejected before execution; scene unchanged"]
    previous = bpy.context.scene.get(REQUEST_KEY)
    try:
        if req["action"] == "build":
            _build(req)
        else:
            _apply_values(objects(), req)
    except Exception as exc:  # restore the previous lesson, never crash the worker
        msg = f"{type(exc).__name__}: {exc}"
        try:
            if previous:
                prev = json.loads(previous)
                _build(prev) if req["action"] == "build" else _apply_values(objects(), prev)
            elif req["action"] == "build":
                clear()
        except Exception as exc2:  # noqa: BLE001
            return 0, [msg, f"restore failed: {exc2}"]
        return 0, [msg, "rolled back; previous lesson restored" if previous else "rolled back"]
    sc = bpy.context.scene
    sc[REQUEST_KEY] = json.dumps(req)  # only used to restore the previous lesson if a later request fails
    sc[engine.VERSION_KEY] = int(sc.get(engine.VERSION_KEY, 0)) + 1
    sc.frame_set(sc.frame_current)
    return 1, []


# ---------------- read back (from Blender data, never echoed from the request) ----------------

def _r(v, nd=4):
    return round(float(v), nd)


def read_back() -> dict | None:
    objs = objects()
    if not objs:
        return None
    out = {"present": present(), "frames": {"start": bpy.context.scene.frame_start,
                                            "end": bpy.context.scene.frame_end, "power_on": list(POWER_ON),
                                            "show": SHOW_FRAME},
           "objects": []}
    for cid in sorted(objs):
        o = objs[cid]
        out["objects"].append({"id": cid, "uid": str(o.get("circuit_uid", "")), "type": o.type,
                               "position": [_r(v) for v in o.location],
                               "parent": o.parent.name if o.parent else None})
    if not out["present"]:
        return out
    bat, res, led, flow = objs["battery_1"], objs["resistor_1"], objs["led_1"], objs["flow_1"]
    out["params"] = {"source_v": float(bat.get("source_v", 0)), "resistance_ohm": float(res.get("resistance_ohm", 0)),
                     "led_vf": float(led.get("led_vf", 0))}
    out["labels"] = {lid: objs[lid].data.body for lid in LABELS}
    ins = _flow_inputs(flow)
    laps = float(ins["Laps per frame"][0].value)
    power_fc = _fc(flow, f'modifiers["Flow"].properties.inputs.{ins["Power"][1]}.value')
    tree, path, light = _led_paths(led)
    em_fc, light_fc = _fc(tree, path), _fc(light, "energy")
    out["flow"] = {"lap_frames": _r(1.0 / laps, 2) if laps > 0 else None, "markers": int(ins["Count"][0].value),
                   "power_at_show": _r(power_fc.evaluate(SHOW_FRAME), 3) if power_fc else None,
                   "power_at_start": _r(power_fc.evaluate(1), 3) if power_fc else None}
    out["led"] = {"emission_at_show": _r(em_fc.evaluate(SHOW_FRAME), 3) if em_fc else None,
                  "light_w_at_show": _r(light_fc.evaluate(SHOW_FRAME), 2) if light_fc else None,
                  "status": str(led.get("led_status", "")),
                  "status_color": _mat_hex(objs["label_status"].material_slots[0].material)}
    out["resistor_bands"] = [_mat_hex(res.material_slots[2 + i].material) for i in range(4)]
    return out


def make_result(request_id: str, req: dict) -> dict:
    applied, errors = apply(req)
    return {"schema_version": 1, "request_id": request_id, "status": "error" if errors else "ok",
            "applied": applied, "errors": errors, "scene_version": engine.scene_version(),
            "scene_objects": engine.read_scene(), "other_objects": other_objects(),
            "scene": engine.read_scene_info(), "executor": f"blender {bpy.app.version_string}",
            "circuit": read_back(), "circuit_action": req.get("action") if isinstance(req, dict) else None}


def other_objects() -> list[dict]:
    return [o for o in engine.read_other_objects()
            if not (bpy.data.objects.get(o["name"]) and bpy.data.objects[o["name"]].get("circuit_part"))]


# ---------------- clear / preview ----------------

def clear(keep_settings: bool = False) -> None:
    """Remove every lesson object and its data. keep_settings=True (used by build) keeps the scene flags."""
    removers = ((bpy.types.Mesh, bpy.data.meshes), (bpy.types.Curve, bpy.data.curves),
                (bpy.types.Light, bpy.data.lights), (bpy.types.Camera, bpy.data.cameras))
    for obj in [o for o in bpy.data.objects if o.get("circuit_part")]:
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0:
            for kind, coll in removers:
                if isinstance(data, kind):
                    coll.remove(data)
                    break
    for mat in [m for m in bpy.data.materials if m.get("circuit_part") and m.users == 0]:
        bpy.data.materials.remove(mat)
    for act in [a for a in bpy.data.actions if a.users == 0]:
        bpy.data.actions.remove(act)
    if not keep_settings:
        sc = bpy.context.scene
        if REQUEST_KEY in sc:
            del sc[REQUEST_KEY]
        sun = bpy.data.objects.get("TD_Sun")
        if sun is not None and sun.get("td_hidden_by_circuit"):
            sun.hide_render = sun.hide_viewport = False
            del sun["td_hidden_by_circuit"]


def render_preview(path: str | Path, width: int = 1280, height: int = 720, frame: int = SHOW_FRAME,
                   samples: int = 32, file_format: str = "PNG") -> str:
    sc = bpy.context.scene
    saved = (sc.frame_current, sc.render.filepath, sc.render.resolution_x, sc.render.resolution_y,
             sc.render.resolution_percentage, sc.render.image_settings.file_format)
    sc.frame_set(frame)
    sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = width, height, 100
    sc.render.image_settings.file_format = file_format
    sc.render.filepath = str(path)
    try:
        sc.eevee.taa_render_samples = samples
    except AttributeError:
        pass
    try:
        bpy.ops.render.render(write_still=True)
    finally:
        sc.render.filepath = saved[1]
        sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = saved[2:5]
        sc.render.image_settings.file_format = saved[5]
        sc.frame_set(saved[0])
    return str(path)
