"""Headless engine check (no GUI): builds every asset, edits, animates, deletes, rejects bad ops,
and optionally renders a preview PNG.

  blender -b --python blender_engine/headless_check.py -- [--render out.png]
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bpy  # noqa: E402

from blender_engine import assets, engine  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
engine.reset_scene()
if bpy.data.objects.get("Cube") is not None:  # Blender's default startup cube (the worker removes it too)
    bpy.data.objects.remove(bpy.data.objects["Cube"], do_unlink=True)
fails = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


names = [a for a in assets.DEFAULT_COLORS if a != "ground"]
create = {"schema_version": 1, "request_id": "hc_create", "mode": "create", "summary": "all", "operations":
          [{"op": "add", "id": "ground_1", "asset": "ground", "position": [0, 0, 0]}] +
          [{"op": "add", "id": f"{a}_1", "asset": a, "position": [(i % 4) * 2.2 - 3.3, (i // 4) * 2.2 - 2.2, 0]}
           for i, a in enumerate(names)]}
r1 = engine.make_result("hc_create", create)
check(r1["status"] == "ok" and r1["applied"] == len(create["operations"]), f"create all assets: {r1['errors']}")
check(r1["scene_version"] == 1, "scene_version 1")
before = {o["id"]: o for o in r1["scene_objects"]}

edit = {"schema_version": 1, "request_id": "hc_edit", "mode": "edit", "summary": "edit", "operations": [
    {"op": "modify", "id": "mug_1", "set": {"color": "#3B6FD9", "scale": 1.5}},
    {"op": "animate", "id": "cube_1", "property": "rotation", "start_frame": 1, "end_frame": 48, "to": [0, 0, 360]},
    {"op": "delete", "id": "rock_1"},
    {"op": "modify", "id": "mug_7", "set": {"scale": 2}},
    {"op": "explode", "id": "tree_1"},
    {"op": "add", "id": "dragon_1", "asset": "dragon", "position": [0, 0, 0]},
]}
r2 = engine.make_result("hc_edit", edit)
after = {o["id"]: o for o in r2["scene_objects"]}
check(r2["status"] == "partial" and r2["applied"] == 3 and len(r2["errors"]) == 3, f"partial apply: {r2['errors']}")
check(after["mug_1"]["color"] == "#3B6FD9" and after["mug_1"]["scale"] == 1.5, "mug recolored + scaled (read back)")
check(after["tree_1"] == before["tree_1"], "tree untouched")
check("rock_1" not in after and bpy.data.objects.get("rock_1") is None, "rock deleted")
check(after["cube_1"].get("animated") is True and bpy.context.scene.frame_end >= 48, "cube animated")
bpy.context.scene.frame_set(48)
check(abs(math.degrees(bpy.data.objects["cube_1"].rotation_euler.z) - 360) < 1e-3, "keyframe reaches 360 deg at f48")
bpy.context.scene.frame_set(1)
check(r2["scene_version"] == 2, "scene_version 2")
check(engine.make_result("hc_noop", {"schema_version": 1, "operations": [{"op": "delete", "id": "nope_1"}]})
      ["scene_version"] == 2, "failed plan does not bump version")
check(engine.make_result("hc_code", {"schema_version": 1, "operations": [{"op": "add", "id": "cube_9", "asset": "cube",
      "position": [0, 0, 0], "python": "import os"}]})["applied"] == 1, "extra keys ignored, never executed")
check(json.loads(json.dumps(r2)) == r2, "result is JSON-serializable")

if "--render" in args:
    out = args[args.index("--render") + 1]
    scene = bpy.context.scene
    cam = bpy.data.objects.new("TD_Cam", bpy.data.cameras.new("TD_Cam"))
    scene.collection.objects.link(cam)
    cam.location = (9, -11, 8)
    cam.rotation_euler = (math.radians(58), 0, math.radians(39))
    scene.camera = cam
    sun = bpy.data.objects.new("TD_Sun", bpy.data.lights.new("TD_Sun", "SUN"))
    sun.data.energy = 3
    sun.rotation_euler = (0.7, 0.2, 0.6)
    scene.collection.objects.link(sun)
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 16
    scene.render.resolution_x, scene.render.resolution_y = 960, 600
    scene.render.filepath = out
    bpy.ops.render.render(write_still=True)
    print("rendered", out)

print(f"\n{'ALL PASSED' if not fails else f'{len(fails)} FAILED'}")
sys.exit(1 if fails else 0)
