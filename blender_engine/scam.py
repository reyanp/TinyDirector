"""'Spot the Scam' in Blender: a large phone showing a FICTIONAL suspicious text, warning-sign tags, and two
hypothetical outcomes side by side (the What-If Lab pattern: copies beside the original, which stays untouched;
clearing removes every copy).

  {"kind": "scam", "action": "build" | "mark" | "outcomes" | "clear_outcomes"}   (shared/schema/scam_request_v1.json)

Display only. Every string comes from the director (director/scam/scenario.py, director/scam/detector.py); this
module draws it and reads it back. Nothing is opened, sent or collected: the "website" is 3D text.
Reuses the circuit lesson's look (materials, text, plates, world, lights, render settings) and the stage
direction module (camera framing, glide, callout, caption) through `targets()` / `objects_for()` /
`highlight()` / `caption_obj()`.
"""

from __future__ import annotations

import json
import math
import secrets
import textwrap

import bmesh
import bpy
from mathutils import Vector

from . import circuit, engine

STATE_KEY = "td_scam"
OUT_KEY = "td_scam_outcomes"
HL_KEY = "td_scam_highlight"
CAMERA = {"location": (1.15, -12.6, 3.35), "target": (1.15, 0.0, 2.45), "lens": 36}
PHONE = {"w": 2.4, "h": 4.7, "d": 0.26, "z": 2.5, "r": 0.32}
SCREEN = {"w": 2.14, "h": 4.3, "r": 0.2}
FRONT = -PHONE["d"] / 2
OFFSETS = {"follow_link": -5.6, "verify_official": 7.2}  # x of the outcome phones (the message phone stays at 0)
SIDE = {"follow_link": "outcome_a", "verify_official": "outcome_b"}
SEGMENT_IDS = ("brand", "body", "urgency", "link")
MARK_TARGETS = ("sender",) + SEGMENT_IDS
HUD = {  # id: (size, color, align, hud (x, y)) -- the circuit lesson's HUD slots
    "title": (0.088, "#FFFFFF", "LEFT", (-1.4, 0.78)),
    "banner": (0.04, "#FFB4A8", "LEFT", (-1.4, 0.66)),
    "caption": (0.042, "#E9EEF3", "LEFT", (-1.4, -0.56)),
    "legend": (0.028, "#AAB4BF", "RIGHT", (1.4, -0.74)),
}
AMBER = "#FFB020"
WRAP = 0.53  # average glyph width / size for the default font


# ---------------- small builders ----------------

def _link(obj, sid: str | None = None, group: str | None = None):
    circuit._link(obj)  # circuit_part: removed by circuit.clear() (a circuit or lesson build clears the scam)
    obj["scam_part"] = True
    if sid:
        obj["scam_id"] = sid
        obj["scam_uid"] = secrets.token_hex(4)
    if group:
        obj["scam_group"] = group
    return obj


def _mat(name: str, hex_color: str, emit: float = 0.0, rough: float = 0.5, alpha: float = 1.0):
    m = circuit._mat(f"td_scam_{name}", hex_color, rough=rough, emit=emit, alpha=alpha)
    m["scam_part"] = True
    return m


def _rrect_pts(w: float, h: float, r: float, n: int = 6):
    r = min(r, w / 2, h / 2)
    pts = []
    for cx, cz, a0 in ((w / 2 - r, h / 2 - r, 0), (-w / 2 + r, h / 2 - r, 90), (-w / 2 + r, -h / 2 + r, 180),
                       (w / 2 - r, -h / 2 + r, 270)):
        for i in range(n + 1):
            a = math.radians(a0 + 90 * i / n)
            pts.append((cx + r * math.cos(a), cz + r * math.sin(a)))
    return pts


def _panel(name: str, cx: float, cz: float, w: float, h: float, r: float, y: float, mat, group=None, sid=None):
    """A flat rounded rectangle facing the camera (-Y)."""
    me = bpy.data.meshes.new(name)
    pts = _rrect_pts(w, h, r)
    me.from_pydata([(cx + x, y, cz + z) for x, z in pts], [], [list(range(len(pts)))[::-1]])
    me.materials.append(mat)
    return _link(bpy.data.objects.new(name, me), sid, group)


def _body(name: str, x: float, mat):
    bm = bmesh.new()
    pts = _rrect_pts(PHONE["w"], PHONE["h"], PHONE["r"], 8)
    front = [bm.verts.new((x + px, FRONT, PHONE["z"] + pz)) for px, pz in pts]
    back = [bm.verts.new((x + px, -FRONT, PHONE["z"] + pz)) for px, pz in pts]
    bm.faces.new(front[::-1])
    bm.faces.new(back)
    n = len(pts)
    for i in range(n):
        f = bm.faces.new((front[i], front[(i + 1) % n], back[(i + 1) % n], back[i]))
        f.smooth = True
    obj = circuit._mesh_obj(name, bm, [mat])
    mod = obj.modifiers.new("Bevel", "BEVEL")
    mod.width, mod.segments, mod.limit_method = 0.05, 3, "ANGLE"
    return obj


def _wrap(text: str, size: float, width: float) -> str:
    """Word wrap; a link (no spaces) is cut into fixed chunks so read_back can join it back exactly."""
    n = max(8, int(width / (size * WRAP)))
    if " " not in text:  # a link: break after - / . where possible
        lines, rest = [], text
        while len(rest) > n:
            cut = max(rest.rfind(c, 0, n) for c in "-/.") + 1
            cut = cut if cut > n // 2 else n
            lines.append(rest[:cut])
            rest = rest[cut:]
        return "\n".join(lines + [rest])
    out = []
    for para in text.split("\n"):
        out += textwrap.wrap(para, n, break_on_hyphens=False) or [""]
    return "\n".join(out)


def _unwrap(body: str, original_has_space: bool) -> str:
    return body.replace("\n", " " if original_has_space else "")


def _text(name: str, body: str, size: float, color: str, x: float, z: float, align="LEFT", width: float | None = None,
          sid=None, group=None, emit=1.2, y=FRONT - 0.012):
    t = circuit._text_obj(name, _wrap(body, size, width) if width else body, size, color, align, emit=emit)
    t["scam_spaced"] = " " in body
    t.data.align_y = "TOP"
    t.data.materials[0]["scam_part"] = True
    _link(t, sid, group)
    t.location = (x, y, z)
    t.rotation_euler = (math.pi / 2, 0, 0)
    return t


def _world_box(obj):
    bpy.context.view_layer.update()
    pts = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    return (min(p.x for p in pts), min(p.z for p in pts), max(p.x for p in pts), max(p.z for p in pts))


def _frame(name: str, box, pad: float, sid: str, group=None):
    """A glowing outline around a message segment (hidden until the camera looks at it)."""
    x0, z0, x1, z1 = box
    w, h = x1 - x0 + 2 * pad, z1 - z0 + 2 * pad
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = 0.022
    sp = cu.splines.new("POLY")
    pts = _rrect_pts(w, h, 0.08)
    sp.points.add(len(pts) - 1)
    for p, (px, pz) in zip(sp.points, pts):
        p.co = ((x0 + x1) / 2 + px, FRONT - 0.03, (z0 + z1) / 2 + pz, 1.0)
    sp.use_cyclic_u = True
    cu.materials.append(_mat("frame", AMBER, emit=5.0))
    obj = _link(bpy.data.objects.new(name, cu), sid, group)
    obj["scam_frame"] = True
    obj.hide_render = obj.hide_viewport = True
    return obj


def _tag(name: str, sid: str, z: float, group=None):
    """Warning-sign tag to the right of the phone (filled in by 'mark')."""
    t = _text(name, "", 0.15, AMBER, PHONE["w"] / 2 + 0.38, z + 0.09, sid=sid, group=group, emit=2.4,
              y=FRONT - 0.05)
    circuit._plate(t, hud=False)
    t["scam_tag"] = True
    t.hide_render = t.hide_viewport = True  # shown by 'mark'
    return t


# ---------------- scene ----------------

def objects() -> dict:
    return {o["scam_id"]: o for o in bpy.data.objects if o.get("scam_part") and "scam_id" in o}


def present() -> bool:
    return "phone_1" in objects()


def _state() -> dict:
    return json.loads(bpy.context.scene.get(STATE_KEY, "{}"))


def outcomes_active() -> bool:
    return bool(bpy.context.scene.get(OUT_KEY))


def _camera_and_lights():
    cd = bpy.data.cameras.new("TD_ScamCam")
    cd.lens, cd.clip_start = CAMERA["lens"], 0.1
    cam = _link(bpy.data.objects.new("TD_ScamCam", cd))
    cam.location = CAMERA["location"]
    cam.rotation_euler = (Vector(CAMERA["target"]) - Vector(CAMERA["location"])).to_track_quat("-Z", "Y").to_euler()
    for name, loc, energy, color, size in (("TD_ScamKey", (-4.0, -6.0, 7.5), 900, (1.0, 0.96, 0.92), 6.0),
                                           ("TD_ScamRim", (4.5, 4.0, 6.0), 600, (0.55, 0.7, 1.0), 5.0),
                                           ("TD_ScamFill", (7.0, -5.0, 2.0), 200, (0.8, 0.85, 1.0), 4.0)):
        ld = bpy.data.lights.new(name, "AREA")
        ld.energy, ld.color, ld.size = energy, color, size
        lo = _link(bpy.data.objects.new(name, ld))
        lo.location = loc
        lo.rotation_euler = (Vector((0, 0, 2.0)) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    return cam


def _floor():
    me = bpy.data.meshes.new("TD_ScamFloor")
    n, r = 64, 16.0
    me.from_pydata([(0, 0, 0)] + [(r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n), 0)
                                  for i in range(n)], [], [(0, i + 1, (i + 1) % n + 1) for i in range(n)])
    me.materials.append(_mat("floor", "#0E1622", rough=0.35))
    return _link(bpy.data.objects.new("TD_ScamFloor", me))


def _phone_shell(prefix: str, x: float, sid: str | None, group: str | None):
    body = _link(_body(f"{prefix}_body", x, _mat("body", "#1B1F26", rough=0.3)), sid, group)
    screen = _panel(f"{prefix}_screen", x, PHONE["z"], SCREEN["w"], SCREEN["h"], SCREEN["r"], FRONT - 0.004,
                    _mat("screen", "#0A0F17", emit=0.25), group)
    _panel(f"{prefix}_notch", x, PHONE["z"] + SCREEN["h"] / 2 - 0.13, 0.55, 0.11, 0.055, FRONT - 0.008,
           _mat("notch", "#000000"), group)
    _text(f"{prefix}_clock", "9:41", 0.1, "#E9EEF3", x - SCREEN["w"] / 2 + 0.2, PHONE["z"] + SCREEN["h"] / 2 - 0.08,
          group=group)
    _text(f"{prefix}_signal", "5G  100%", 0.085, "#E9EEF3", x + SCREEN["w"] / 2 - 0.2,
          PHONE["z"] + SCREEN["h"] / 2 - 0.09, align="RIGHT", group=group)
    return body, screen


def _hud(cam, title: str, banner: str, legend: str):
    for hid, (size, color, align, (hx, hy)) in HUD.items():
        body = {"title": title, "banner": banner, "legend": legend, "caption": ""}[hid]
        t = circuit._text_obj(f"scam_{hid}", body, size, color, align)
        t.data.materials[0]["scam_part"] = True
        _link(t, f"hud_{hid}")
        t.parent = cam
        t.location = (hx, hy, -circuit.HUD_DIST)
        circuit._plate(t, hud=True)
    bpy.context.view_layer.update()
    for o in objects().values():
        if o.type == "FONT" and o.parent == cam:
            circuit._fit_plate(o)


def _build(req: dict) -> None:
    circuit.clear(keep_settings=True)  # one lesson on stage: the LED circuit (and any old scam scene) goes
    m = req["message"]
    cam = _camera_and_lights()
    _floor()
    _phone_shell("TD_ScamPhone", 0.0, "phone_1", None)
    top = PHONE["z"] + SCREEN["h"] / 2
    left = -SCREEN["w"] / 2 + 0.2
    sender = _text("TD_ScamSender", m["sender"], 0.16, "#FFFFFF", 0, top - 0.42, align="CENTER", sid="sender")
    _text("TD_ScamSenderNote", m["sender_note"], 0.08, "#9AA6B4", 0, top - 0.68, align="CENTER", group="sender")
    _text("TD_ScamTime", m["time"], 0.075, "#7F8B99", 0, top - 0.98, align="CENTER")
    z, segs = top - 1.32, {}
    for s in m["segments"]:
        color = "#6CB8FF" if s["id"] == "link" else "#F2F5F8"
        t = _text(f"TD_ScamSeg_{s['id']}", s["text"], 0.15, color, left + 0.12, z, width=1.62, sid=s["id"])
        segs[s["id"]] = t
        z = _world_box(t)[1] - 0.12
    x0 = min(_world_box(t)[0] for t in segs.values()) - 0.14
    z1 = max(_world_box(t)[3] for t in segs.values()) + 0.14
    x1 = max(_world_box(t)[2] for t in segs.values()) + 0.14
    z0 = min(_world_box(t)[1] for t in segs.values()) - 0.14
    _panel("TD_ScamBubble", (x0 + x1) / 2, (z0 + z1) / 2, x1 - x0, z1 - z0, 0.16, FRONT - 0.007,
           _mat("bubble", "#26303D", emit=0.15))
    _text("TD_ScamBanner", "FICTIONAL EXAMPLE\neducational simulation", 0.08, "#FF8A7A", 0,
          PHONE["z"] - SCREEN["h"] / 2 + 0.5, align="CENTER")
    for sid, obj in [("sender", sender)] + list(segs.items()):
        _frame(f"TD_ScamFrame_{sid}", _world_box(obj), 0.07, f"frame_{sid}", group=sid)
        _tag(f"TD_ScamTag_{sid}", f"tag_{sid}", (_world_box(obj)[1] + _world_box(obj)[3]) / 2, group=sid)
    _hud(cam, req["title"], req["banner"], req["legend"])
    circuit._world()
    circuit._scene_settings(cam)
    sc = bpy.context.scene
    sc.frame_start, sc.frame_end = 1, 120
    sc[STATE_KEY] = json.dumps({"scenario": req["scenario"], "title": req["title"], "legend": req["legend"]})
    sc[OUT_KEY] = ""
    sc[HL_KEY] = ""


def _mark(req: dict) -> None:
    objs = objects()
    for sid in MARK_TARGETS:
        tag = objs.get(f"tag_{sid}")
        if tag is None:
            continue
        tag.data.body = req["tags"].get(sid, "")
        bpy.context.view_layer.update()
        circuit._fit_plate(tag)
        tag.hide_render = tag.hide_viewport = not tag.data.body


# ---------------- outcomes (What-If pattern: copies beside the untouched original) ----------------

def _outcome(choice: str, o: dict, chosen: bool) -> None:
    """One hypothetical outcome phone, laid out top to bottom with measured text heights (no overlaps)."""
    x, g = OFFSETS[choice], SIDE[choice]
    _phone_shell(f"TD_Scam{g}", x, g, g)
    top, w = PHONE["z"] + SCREEN["h"] / 2, SCREEN["w"] - 0.3
    cur = [top - 0.32]
    n = [0]

    def put(body, size, color, gap=0.1, emit=1.2):
        n[0] += 1
        t = _text(f"TD_Scam{g}_t{n[0]}", body, size, color, x, cur[0], align="CENTER", width=w, group=g, emit=emit)
        cur[0] = _world_box(t)[1] - gap
        return t

    def panel(name, texts, hex_color, emit=0.3, pad=0.1, min_w=None):
        boxes = [_world_box(t) for t in texts]
        z0, z1 = min(b[1] for b in boxes) - pad, max(b[3] for b in boxes) + pad
        pw = min_w or (w + 0.1)
        _panel(f"TD_Scam{g}_{name}", x, (z0 + z1) / 2, pw, z1 - z0, 0.1, FRONT - 0.008,
               _mat(name, hex_color, emit=emit), g)

    if choice == "follow_link":
        a = put(o["address"], 0.1, "#E9EEF3")
        panel("bar", [a], "#3A4250", emit=0.15, pad=0.08)
        cur[0] -= 0.08
        put(o["address_note"], 0.1, "#FF7A6B", gap=0.18)
        put(o["page_title"], 0.18, "#FFFFFF", gap=0.16)
        for i, field in enumerate(o["fields"]):
            put(field, 0.115, "#B8C2CE", gap=0.05)
            _panel(f"TD_Scam{g}_field{i}", x, cur[0] - 0.13, w, 0.26, 0.07, FRONT - 0.008, _mat("field", "#EEF1F5"), g)
            cur[0] -= 0.36
        cur[0] -= 0.04
        b = put(o["button"], 0.13, "#FFFFFF", gap=0.26)
        panel("button", [b], "#2F7BE8", emit=0.4, pad=0.08)
        r1 = put(o["result"], 0.11, "#FFB4A8", gap=0.08)
        r2 = put(o["risk"], 0.1, "#FFD45C", gap=0.1)
        panel("danger", [r1, r2], "#4A1414", emit=0.3)
    else:
        a1 = put(o["app_title"], 0.16, "#FFFFFF", gap=0.06)
        a2 = put(o["app_note"], 0.1, "#BFEDE7", gap=0.3)
        panel("app", [a1, a2], "#0F6E66", emit=0.35)
        s1 = put(o["status"], 0.17, "#7BE08A", gap=0.06)
        s2 = put(o["detail"], 0.11, "#CDEBD6", gap=0.32)
        panel("okbg", [s1, s2], "#143A22")
        put(o["report"], 0.12, "#E9EEF3", gap=0.32)
        r = put(o["result"], 0.12, "#7BE08A")
        panel("okbg2", [r], "#143A22")
    head = _text(f"TD_Scam{g}_headline", o["headline"], 0.2, "#FF8A7A" if choice == "follow_link" else "#7BE08A",
                 x, PHONE["z"] + PHONE["h"] / 2 + 0.85, align="CENTER", group=g, emit=2.0, y=FRONT)
    circuit._plate(head, hud=False)
    bpy.context.view_layer.update()
    circuit._fit_plate(head)
    head["scam_headline"] = choice
    if chosen:
        mark = _text(f"TD_Scam{g}_choice", "YOUR CHOICE", 0.2, AMBER, x, PHONE["z"] + PHONE["h"] / 2 + 1.35,
                     align="CENTER", group=g, emit=2.6, y=FRONT)
        mark["scam_choice"] = choice
        outline = _frame(f"TD_Scam{g}_chosen", (x - PHONE["w"] / 2, PHONE["z"] - PHONE["h"] / 2,
                                                x + PHONE["w"] / 2, PHONE["z"] + PHONE["h"] / 2), 0.12, f"{g}_chosen", g)
        outline["scam_frame"] = False  # always on (not a focus outline)
        outline.hide_render = outline.hide_viewport = False


def _set_hud(hid: str, body: str) -> None:
    o = objects().get(f"hud_{hid}")
    if o is not None:
        o.data.body = body
        bpy.context.view_layer.update()  # the plate is fitted to the new text's bounds
        circuit._fit_plate(o)


def show_outcomes(req: dict) -> None:
    if outcomes_active():
        clear_outcomes()
    for choice in ("follow_link", "verify_official"):
        _outcome(choice, req["outcomes"][choice], choice == req["choice"])
    _set_hud("title", req["title"])
    _set_hud("legend", req["legend"])
    bpy.context.scene[OUT_KEY] = req["choice"]


def clear_outcomes() -> None:
    for obj in [o for o in bpy.data.objects if o.get("scam_group") in ("outcome_a", "outcome_b")
                or o.get("scam_id") in ("outcome_a", "outcome_b")]:
        for ch in list(obj.children):
            if ch.get("plate_hud") is not None:
                data = ch.data
                bpy.data.objects.remove(ch, do_unlink=True)
                if data is not None and data.users == 0:
                    bpy.data.meshes.remove(data)
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data, bpy.types.Curve):
                bpy.data.curves.remove(data)
    st = _state()
    if st:
        _set_hud("title", st["title"])
        _set_hud("legend", st["legend"])
    bpy.context.scene[OUT_KEY] = ""


# ---------------- stage direction hooks (blender_engine/direction.py) ----------------

def targets() -> list[str]:
    if not present():
        return []
    out = ["phone", "message"] + list(MARK_TARGETS)
    if outcomes_active():
        out += ["outcomes", "outcome_a", "outcome_b"]
    return out


WIDE = ("outcomes", "outcome_a", "outcome_b", "phone")  # whole phones: the comparison's wide framing


def objects_for(target: str) -> list:
    objs = [o for o in bpy.data.objects if o.get("scam_part")]
    if target == "phone":
        return [o for o in objs if o.get("scam_id") == "phone_1"] + [o for o in objs if o.get("scam_tag")
                                                                       and not o.hide_render]
    if target == "message":
        return [o for o in objs if o.get("scam_id") in SEGMENT_IDS]
    if target in MARK_TARGETS:
        return [o for o in objs if o.get("scam_id") == target or (o.get("scam_group") == target
                                                                  and not o.hide_render)]
    if target in ("outcome_a", "outcome_b"):
        return [o for o in objs if o.get("scam_id") == target or (o.get("scam_group") == target
                                                                  and (o.get("scam_headline") or o.get("scam_choice")))]
    if target == "outcomes":
        return objects_for("outcome_a") + objects_for("outcome_b") + objects_for("phone")
    return []


def highlight(target: str | None) -> None:
    """Glow the outline of the focused message part (the halo ring is for parts lying on a board)."""
    for o in bpy.data.objects:
        if o.get("scam_frame"):
            on = target is not None and o.get("scam_group") == target
            o.hide_render = o.hide_viewport = not on
    bpy.context.scene[HL_KEY] = target if target in MARK_TARGETS else ""


def caption_obj():
    return objects().get("hud_caption")


# ---------------- requests ----------------

def check_request(req) -> list[str]:
    if not isinstance(req, dict) or req.get("kind") != "scam" or req.get("schema_version") != 1:
        return ["not a scam_request_v1 document"]
    a = req.get("action")
    if a == "build":
        m = req.get("message") or {}
        segs = m.get("segments") or []
        errs = [] if isinstance(segs, list) and [s.get("id") for s in segs] == list(SEGMENT_IDS) else \
            [f"message.segments must be {list(SEGMENT_IDS)} in order"]
        for k in ("sender", "sender_note", "time"):
            if not isinstance(m.get(k), str) or len(m[k]) > 60:
                errs.append(f"message.{k} must be a short string")
        if any(not isinstance(s.get("text"), str) or not 0 < len(s["text"]) <= 120 for s in segs):
            errs.append("segment texts must be 1..120 characters")
        for k in ("title", "banner", "legend"):
            if not isinstance(req.get(k), str) or len(req[k]) > 240:
                errs.append(f"{k} must be a string (max 240)")
        return errs
    if not present():
        return ["no scam lesson on stage; build it first"]
    if a == "mark":
        tags = req.get("tags")
        if not isinstance(tags, dict) or set(tags) - set(MARK_TARGETS) or any(
                not isinstance(v, str) or len(v) > 40 for v in tags.values()):
            return [f"tags must map {list(MARK_TARGETS)} to short strings"]
        return []
    if a == "outcomes":
        if req.get("choice") not in OFFSETS:
            return [f"choice must be one of {list(OFFSETS)}"]
        if not isinstance(req.get("outcomes"), dict) or set(req["outcomes"]) != set(OFFSETS):
            return ["outcomes must describe follow_link and verify_official"]
        return []
    if a == "clear_outcomes":
        return []
    return ["action must be build, mark, outcomes or clear_outcomes"]


def read_back() -> dict:
    objs = objects()
    if "phone_1" not in objs:
        return {"present": False}
    st = _state()
    body = lambda sid: _unwrap(objs[sid].data.body, objs[sid].get("scam_spaced", True)) if sid in objs else None  # noqa: E731
    tags = {sid: objs[f"tag_{sid}"].data.body for sid in MARK_TARGETS
            if f"tag_{sid}" in objs and not objs[f"tag_{sid}"].hide_render}
    out = {"present": True, "scenario": st.get("scenario"),
           "title": objs["hud_title"].data.body if "hud_title" in objs else None,
           "banner": objs["hud_banner"].data.body if "hud_banner" in objs else None,
           "sender": body("sender"), "segments": {s: body(s) for s in SEGMENT_IDS},
           "tags": tags, "highlight": bpy.context.scene.get(HL_KEY) or None,
           "objects": sorted([{"id": k, "uid": str(v.get("scam_uid", ""))} for k, v in objs.items()],
                             key=lambda o: o["id"]),
           "outcomes": {"active": outcomes_active()}}
    if outcomes_active():
        side = {}
        for choice, g in SIDE.items():
            texts = [o.data.body.replace("\n", " ") for o in bpy.data.objects
                     if o.get("scam_group") == g and o.type == "FONT"]
            cx = None
            if g in objs:
                xs = [(objs[g].matrix_world @ Vector(c)).x for c in objs[g].bound_box]
                cx = round((min(xs) + max(xs)) / 2, 3)
            side[choice] = {"present": g in objs, "texts": sorted(texts), "center_x": cx}
        chosen = [o.get("scam_choice") for o in bpy.data.objects if o.get("scam_choice")]
        out["outcomes"] = {"active": True, "choice": bpy.context.scene.get(OUT_KEY), "marked": chosen,
                           "headlines": {o["scam_headline"]: o.data.body for o in bpy.data.objects
                                         if o.get("scam_headline")}, **side}
    return out


def make_result(request_id: str, req: dict) -> dict:
    from . import direction
    errs = check_request(req)
    applied = 0
    if not errs:
        try:
            a = req["action"]
            if a == "build":
                direction.clear()
                _build(req)
            elif a == "mark":
                _mark(req)
            elif a == "outcomes":
                show_outcomes(req)
                errs = direction.apply({"kind": "direct", "focus": "outcomes", "transition_s": 1.4})
            else:
                clear_outcomes()
                errs = direction.apply({"kind": "direct", "focus": "overview", "transition_s": 1.2})
            applied = 1
            sc = bpy.context.scene
            sc[engine.VERSION_KEY] = int(sc.get(engine.VERSION_KEY, 0)) + 1
        except Exception as exc:  # never leave half an outcome view on stage
            errs = [f"{type(exc).__name__}: {exc}"]
            try:
                if req.get("action") == "outcomes":
                    clear_outcomes()
                    errs.append("rolled back: outcomes removed")
            except Exception as exc2:  # noqa: BLE001
                errs.append(f"rollback failed: {exc2}")
            applied = 0
    return {"schema_version": 1, "request_id": request_id, "status": "error" if errs else "ok",
            "applied": 0 if errs else applied, "errors": errs, "scene_version": engine.scene_version(),
            "scene_objects": engine.read_scene(), "executor": f"blender {bpy.app.version_string}",
            "scam": read_back(), "direction": direction.read_back()}
