"""Split-reality comparison: the lesson circuit on stage next to a "what if" copy with other values.

A {"kind": "compare"} request (shared/schema/compare_request_v1.json, additive):
  show  = duplicate the LED circuit that is on stage into the TD_Compare collection, beside it, and write the
          alternative's values (from director/circuit/model.py, sent in the request) onto the copy only:
          resistor bands, measurement labels, current-arrow speed, LED glow. Big headlines name each side.
          The camera frames both. The original lesson objects are never moved or changed (same objects, same
          values); only its HUD title / equation / status / legend are hidden while the comparison is shown.
  clear = delete the copy and everything the comparison added, unhide what it hid, restore the timeline range
          and the camera.
The engine computes nothing electrical. Glow and arrow speed are illustrative and proportional to the current
(director/circuit/display.py), not a photometric model or a drift velocity; the legend says so.

Copies share mesh / curve / action data with the original wherever the two sides look identical; whatever
differs gets its own data (label text, resistor band materials, LED material and light, plates). The copy's
values are static (no keyframes); while the comparison is shown, playback starts after the original's power-on,
so both circuits run side by side.
"""

from __future__ import annotations

import json

import bpy
from mathutils import Vector

from . import circuit, engine

COLLECTION = "TD_Compare"
STATE_KEY = "td_compare"
OFFSET = Vector((8.6, 0.0, 0.0))
ROOT_IDS = ["board_1", "battery_1", "resistor_1", "led_1", "wire_1", "wire_2", "wire_3", "flow_1",
            "label_battery", "label_resistor", "label_led", "label_current"]
WORLD_LABELS = ["label_battery", "label_resistor", "label_led", "label_current"]
LIGHTS = ["TD_CircuitKey", "TD_CircuitRim", "TD_CircuitFill"]
HIDE_HUD = ["label_title", "label_equation", "label_status", "label_legend"]
SIDE_COLORS = {"baseline": "#7FE3D6", "alternative": "#FFD45C"}
STEADY_FRAME = circuit.POWER_ON[1] + 8  # both circuits powered from here on


class CompareError(ValueError):
    pass


def _collection():
    col = bpy.data.collections.get(COLLECTION)
    if col is None:
        col = bpy.data.collections.new(COLLECTION)
    if col.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(col)
    return col


def active() -> bool:
    return STATE_KEY in bpy.context.scene and bpy.data.collections.get(COLLECTION) is not None


def side_objects(side: str) -> list:
    """Objects that make up one side (for camera framing): the original circuit or the copy, + its headline."""
    if side == "alternative":
        return [o for o in bpy.data.objects if o.get("td_compare") == "alternative" and o.parent is None
                and o.type != "LIGHT"]
    objs = [o for o in bpy.data.objects if o.get("circuit_id") in ROOT_IDS]
    return objs + [o for o in bpy.data.objects if o.get("td_compare") == "baseline" and o.parent is None]


# ---------------- request check ----------------

def check_request(req) -> list[str]:
    if not isinstance(req, dict) or req.get("kind") != "compare" or req.get("schema_version") != 1:
        return ["not a compare_request_v1 document"]
    if req.get("action") == "clear":
        return []
    if req.get("action") != "show":
        return ["action must be show or clear"]
    errs = []
    if not circuit.present():
        return ["no LED circuit on stage to compare; build the lesson first"]
    base, alt = req.get("baseline"), req.get("alternative")
    if not isinstance(base, dict) or not isinstance(alt, dict):
        return ["baseline and alternative must be objects"]
    rb = circuit.read_back() or {}
    for k in ("source_v", "resistance_ohm", "led_vf"):
        want = (base.get("circuit") or {}).get(k)
        if not isinstance(want, (int, float)) or abs(float(want) - rb["params"][k]) > 1e-9:
            errs.append(f"baseline.circuit.{k} = {want!r} but the circuit on stage has {rb['params'][k]!r}")
    fake = {"kind": "circuit", "schema_version": 1, "action": "build", "circuit": alt.get("circuit"),
            "display": alt.get("display")}
    errs += [f"alternative: {e}" for e in circuit.check_request(fake)]
    heads = req.get("headlines")
    if not isinstance(heads, dict) or any(not isinstance(heads.get(s), str) or len(heads[s]) > 160
                                          for s in ("baseline", "alternative")):
        errs.append("headlines.baseline / headlines.alternative must be strings (max 160)")
    for k, n in (("title", 160), ("legend", 240)):
        if not isinstance(req.get(k), str) or len(req[k]) > n:
            errs.append(f"{k} must be a string (max {n})")
    return errs


# ---------------- build the copy ----------------

def _hierarchy(roots: list) -> list:
    out, todo = [], list(roots)
    while todo:
        o = todo.pop(0)
        if o not in out:
            out.append(o)
            todo.extend(o.children)
    return out


def _mark_mat(mat):
    mat["td_compare"] = True
    mat["circuit_part"] = True
    return mat


def _copy_objects() -> dict:
    """{original circuit id or name: copy}. Originals untouched; the copy lives in TD_Compare."""
    objs = circuit.objects()
    roots = [objs[cid] for cid in ROOT_IDS if cid in objs] + [bpy.data.objects[n] for n in LIGHTS
                                                               if n in bpy.data.objects]
    col = _collection()
    mapping = {}
    for o in _hierarchy(roots):
        c = o.copy()
        if o.type == "FONT" or (o.type == "MESH" and o.name.endswith("_plate")):
            c.data = o.data.copy()  # own text / own plate size
        col.objects.link(c)
        if "circuit_id" in c:  # invisible to circuit.objects(): the lesson's ids stay unique
            c["compare_id"] = c["circuit_id"]
            del c["circuit_id"]
        if "circuit_uid" in c:
            del c["circuit_uid"]
        c["td_compare"] = "alternative"
        c["circuit_part"] = True
        mapping[o] = c
    for o, c in mapping.items():
        if o.parent in mapping:
            c.parent = mapping[o.parent]
            c.matrix_parent_inverse = o.matrix_parent_inverse.copy()
        elif o.parent is None:
            c.location = o.location + OFFSET
    return {(o.get("circuit_id") or o.name): c for o, c in mapping.items()}


def _static(id_data) -> None:
    if id_data is not None and id_data.animation_data is not None:
        id_data.animation_data_clear()


def _apply_alternative(b: dict, req: dict) -> None:
    c, d = req["alternative"]["circuit"], req["alternative"]["display"]
    b["battery_1"]["source_v"] = float(c["source_v"])
    b["resistor_1"]["resistance_ohm"] = float(c["resistance_ohm"])
    b["led_1"]["led_vf"] = float(c["led_vf"])
    b["led_1"]["led_status"] = d["led_status"]
    # resistor: its own mesh data + band materials
    res = b["resistor_1"]
    res.data = res.data.copy()
    for i, hex_color in enumerate(d["resistor_bands"]):
        mat = _mark_mat(res.data.materials[2 + i].copy())
        circuit._set_mat_color(mat, hex_color)
        res.data.materials[2 + i] = mat
    # LED: own material (static glow) and own light
    led = b["led_1"]
    led.data = led.data.copy()
    mat = _mark_mat(led.data.materials[0].copy())
    led.data.materials[0] = mat
    _static(mat.node_tree)
    circuit._bsdf(mat).inputs["Emission Strength"].default_value = float(d["led_emission"])
    light_obj = next(ch for ch in led.children if ch.name.startswith("TD_LedLight"))
    light_obj.data = light_obj.data.copy()
    _static(light_obj.data)
    light_obj.data.energy = float(d["led_light_w"])
    # current arrows: own speed, steady power (no power-on ramp on the copy)
    flow = b["flow_1"]
    _static(flow)
    ins = circuit._flow_inputs(flow)
    lap = d["flow_lap_frames"]
    ins["Laps per frame"][0].value = 0.0 if lap is None else 1.0 / float(lap)
    ins["Count"][0].value = int(d["flow_markers"])
    ins["Power"][0].value = 1.0 if lap is not None else 0.0
    for lid in WORLD_LABELS:
        b[lid].data.body = d["labels"][lid]
    for cid in ("battery_1", "resistor_1", "led_1") + tuple(WORLD_LABELS):  # no pop-in: fully there, always
        o = b[cid]
        if o.animation_data is not None:
            fcs = [fc for fc in engine.fcurves(o) if fc.data_path == "scale"]
            final = [fcs[i].evaluate(circuit.FRAME_END) for i in range(len(fcs))] if len(fcs) == 3 else None
            _static(o)
            if final:
                o.scale = final


def _text(name: str, body: str, size: float, hex_color: str, location, align="CENTER", parent=None, emit=1.6):
    t = circuit._text_obj(name, body, size, hex_color, align, emit=emit)
    _collection().objects.link(t)
    t["circuit_part"] = True
    t["td_compare"] = "hud" if parent is not None else "headline"
    if parent is not None:
        t.parent = parent
    t.location = location
    circuit._plate(t, parent is not None)
    plate = next(ch for ch in t.children if ch.name.endswith("_plate"))
    bpy.data.collections[circuit.COLLECTION].objects.unlink(plate)
    _collection().objects.link(plate)
    plate["td_compare"] = "plate"
    return t


def _face_camera(obj, cam_location: Vector) -> None:
    obj.rotation_euler = (cam_location - obj.location).to_track_quat("Z", "Y").to_euler()


def _decorate(req: dict, b: dict) -> list:
    """Headlines over each side, a divider, the comparison's HUD title + legend (originals hidden)."""
    cam = bpy.context.scene.camera
    made = []
    cam_loc = Vector(circuit.CAMERA["location"])
    for side, x in (("baseline", 0.1), ("alternative", 0.1 + OFFSET.x)):
        h = _text(f"TD_Compare_{side}", req["headlines"][side], 0.4, SIDE_COLORS[side], (x, 1.9, 3.45))
        h["td_compare"] = side
        _face_camera(h, cam_loc + Vector((x - 0.1, 0, 0)))
        made.append(h)
    me = bpy.data.meshes.new("TD_CompareDivider")
    gap_x = 0.1 + OFFSET.x / 2
    me.from_pydata([(gap_x - 0.03, -2.3, 0.01), (gap_x + 0.03, -2.3, 0.01), (gap_x + 0.03, 2.3, 0.01),
                    (gap_x - 0.03, 2.3, 0.01)], [], [(0, 1, 2, 3)])
    me.materials.append(_mark_mat(circuit._mat("td_compare_divider", "#35D0C0", rough=0.5, emit=3.0)))
    div = bpy.data.objects.new("TD_CompareDivider", me)
    _collection().objects.link(div)
    div["td_compare"] = "divider"
    div["circuit_part"] = True
    made.append(div)
    for lid, body in (("label_title", req["title"]), ("label_legend", req["legend"])):
        size, color, align, _, hud = circuit.LABEL_STYLE[lid]
        t = _text(f"TD_Compare_{lid}", body, size, color, (hud[0], hud[1], -circuit.HUD_DIST), align, parent=cam)
        made.append(t)
    bpy.context.view_layer.update()
    for t in made:
        if t.type == "FONT":
            circuit._fit_plate(t)
    for lid in WORLD_LABELS:
        circuit._fit_plate(b[lid])
    return made


def show(req: dict) -> None:
    if active():
        clear(move_camera=False)
    sc = bpy.context.scene
    objs = circuit.objects()
    hidden = [lid for lid in HIDE_HUD if lid in objs and not objs[lid].hide_viewport]
    state = {"hidden": hidden, "frame_start": sc.frame_start, "frame": sc.frame_current}
    sc[STATE_KEY] = json.dumps(state)
    b = _copy_objects()
    _apply_alternative(b, req)
    _decorate(req, b)
    for lid in hidden:
        for o in [objs[lid]] + list(objs[lid].children):
            o.hide_viewport = o.hide_render = True
    sc.frame_start = max(sc.frame_start, STEADY_FRAME)
    if sc.frame_current < STEADY_FRAME:
        sc.frame_set(STEADY_FRAME)
    sc[STATE_KEY] = json.dumps({**state, "request": {k: req[k] for k in ("alternative", "headlines", "title",
                                                                            "legend")}})


def clear(move_camera: bool = True) -> bool:
    """Remove the comparison; the original lesson is exactly as before. Returns whether one was shown."""
    sc = bpy.context.scene
    was = STATE_KEY in sc
    state = json.loads(sc[STATE_KEY]) if was else {}
    col = bpy.data.collections.get(COLLECTION)
    if col is not None:
        removers = ((bpy.types.Mesh, bpy.data.meshes), (bpy.types.Curve, bpy.data.curves),
                    (bpy.types.Light, bpy.data.lights))
        for o in list(col.objects):
            data = o.data
            bpy.data.objects.remove(o, do_unlink=True)
            if data is not None and data.users == 0:
                for kind, coll in removers:
                    if isinstance(data, kind):
                        coll.remove(data)
                        break
        bpy.data.collections.remove(col)
    for mat in [m for m in bpy.data.materials if m.get("td_compare") and m.users == 0]:
        bpy.data.materials.remove(mat)
    objs = circuit.objects()
    for lid in state.get("hidden", []):
        if lid in objs:
            for o in [objs[lid]] + list(objs[lid].children):
                o.hide_viewport = o.hide_render = False
            circuit._fit_plate(objs[lid])
    if "frame_start" in state:
        sc.frame_start = state["frame_start"]
    if was:
        del sc[STATE_KEY]
        from . import direction
        if move_camera and sc.camera is not None:
            direction.apply({"kind": "direct", "focus": "overview", "transition_s": 0.9})
    return was


# ---------------- read back (from Blender data) ----------------

def _r(v, nd=4):
    return round(float(v), nd)


def read_back() -> dict:
    if not active():
        return {"active": False}
    b = {o["compare_id"]: o for o in bpy.data.objects if o.get("td_compare") == "alternative" and "compare_id" in o}
    out = {"active": True, "objects": len([o for o in bpy.data.objects if o.get("td_compare")]),
           "frame_start": bpy.context.scene.frame_start}
    try:
        led_mat = b["led_1"].data.materials[0]
        ins = circuit._flow_inputs(b["flow_1"])
        laps = float(ins["Laps per frame"][0].value)
        light = next(ch for ch in b["led_1"].children if ch.name.startswith("TD_LedLight")).data
        out["alternative"] = {
            "params": {"source_v": float(b["battery_1"]["source_v"]),
                       "resistance_ohm": float(b["resistor_1"]["resistance_ohm"]),
                       "led_vf": float(b["led_1"]["led_vf"])},
            "labels": {lid: b[lid].data.body for lid in WORLD_LABELS},
            "flow": {"lap_frames": _r(1.0 / laps, 2) if laps > 0 else None, "markers": int(ins["Count"][0].value),
                     "power": _r(ins["Power"][0].value, 3)},
            "led": {"emission": _r(circuit._bsdf(led_mat).inputs["Emission Strength"].default_value, 3),
                    "light_w": _r(light.energy, 2), "status": str(b["led_1"].get("led_status", ""))},
            "resistor_bands": [circuit._mat_hex(b["resistor_1"].data.materials[2 + i]) for i in range(4)],
            "position_offset": [_r(v, 3) for v in (b["battery_1"].location - circuit.objects()["battery_1"].location)],
        }
    except (KeyError, StopIteration, IndexError) as exc:
        out["error"] = f"comparison copy incomplete: {exc}"
    out["headlines"] = {o["td_compare"]: o.data.body for o in bpy.data.objects
                        if o.get("td_compare") in ("baseline", "alternative") and o.type == "FONT"}
    hud = {o.name: o.data.body for o in bpy.data.objects if o.get("td_compare") == "hud"}
    out["title"] = hud.get("TD_Compare_label_title")
    out["legend"] = hud.get("TD_Compare_label_legend")
    return out


def make_result(request_id: str, req: dict) -> dict:
    from . import direction
    errs = check_request(req)
    if not errs:
        try:
            sc = bpy.context.scene
            if req["action"] == "show":
                show(req)
                errs = direction.apply({"kind": "direct", "focus": "compare", "highlight": False,
                                        "transition_s": 1.4})
            else:
                clear()
            sc[engine.VERSION_KEY] = int(sc.get(engine.VERSION_KEY, 0)) + 1
        except Exception as exc:  # never leave half a comparison on stage
            errs = [f"{type(exc).__name__}: {exc}"]
            try:
                clear(move_camera=False)
                errs.append("rolled back: comparison removed, original lesson unchanged")
            except Exception as exc2:  # noqa: BLE001
                errs.append(f"rollback failed: {exc2}")
    else:
        errs = errs + ["compare request rejected before execution; scene unchanged"]
    return {"schema_version": 1, "request_id": request_id, "status": "error" if errs else "ok",
            "applied": 0 if errs else 1, "errors": errs, "scene_version": engine.scene_version(),
            "scene_objects": engine.read_scene(), "executor": f"blender {bpy.app.version_string}",
            "circuit": circuit.read_back(), "compare": read_back(), "direction": direction.read_back()}
