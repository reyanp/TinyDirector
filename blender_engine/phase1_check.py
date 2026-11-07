"""Phase 1 acceptance check, run in REAL Blender (no model, no network).

Runs the three sequential test steps as the plans the director should produce, in ONE scene,
then plays the timeline frame by frame and measures the cube's actual height. Optionally
saves the resulting .blend.

  blender -b --python blender_engine/phase1_check.py -- [--save docs/phase1_demo.blend]

The full pipeline (Nemotron -> validator -> bridge -> Blender) is exercised by
`python -m director.dev.phase1_e2e`; this file checks the engine semantics in isolation.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bpy  # noqa: E402

from blender_engine import engine  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
engine.reset_scene()
for name in ("Cube",):
    if bpy.data.objects.get(name) is not None:
        bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
fails: list[str] = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


def run(rid, ops):
    r = engine.make_result(rid, {"schema_version": 1, "request_id": rid, "mode": "edit", "summary": rid,
                                 "operations": ops})
    print(f"  {rid}: status={r['status']} applied={r['applied']} errors={r['errors']}")
    return r, {o["id"]: o for o in r["scene_objects"]}


def count(objs, asset):
    return sum(1 for o in objs.values() if o["asset"] == asset)


def sample_z(obj, lo, hi):
    """Play the timeline and read the evaluated world-space Z on every frame."""
    scene, out = bpy.context.scene, []
    for f in range(lo, hi + 1):
        scene.frame_set(f)
        out.append(obj.matrix_world.translation.z)
    scene.frame_set(lo)
    return out


def bounce_profile(z, base, height):
    peaks = [i for i in range(1, len(z) - 1) if z[i] >= z[i - 1] and z[i] > z[i + 1] and z[i] > base + height / 2]
    airborne = sum(1 for v in z if v > base + 0.05)
    step = max(abs(z[i + 1] - z[i]) for i in range(len(z) - 1))
    return peaks, airborne, step


# ---- Test 1: "Create a red cube beside a cabin." ----
print("\nTest 1: Create a red cube beside a cabin.")
r1, s1 = run("p1_test1", [
    {"op": "add", "id": "cube_1", "asset": "cube", "color": "#D95C52", "position": [-2.6, 0, 0]},
    {"op": "add", "id": "cabin_1", "asset": "cabin", "position": [0.5, 0, 0]},
])
check(r1["status"] == "ok", "test1 applied")
check(count(s1, "cube") == 1 and s1["cube_1"]["color"] == "#D95C52", "exactly one red cube (cube_1)")
check(count(s1, "cabin") == 1 and bpy.data.objects.get("cabin_1") is not None, "a cabin exists (cabin_1)")
cabin = bpy.data.objects["cabin_1"]
dims = cabin.dimensions
check(dims.z > 2.4 and dims.x > 2.4 and len(cabin.material_slots) >= 4,
      f"cabin is a house shape, not a tent ({dims.x:.1f} x {dims.y:.1f} x {dims.z:.1f} m, {len(cabin.material_slots)} materials)")
check(all(s1[i]["name"] == i for i in s1), "objects carry identifiable names (Blender name == director id)")

# ---- Test 2: "Change the red cube to blue." ----
print("\nTest 2: Change the red cube to blue.")
r2, s2 = run("p1_test2", [{"op": "modify", "id": "cube_1", "set": {"color": "#3B6FD9"}}])
check(s2["cube_1"]["color"] == "#3B6FD9", "the existing cube is now blue")
check(count(s2, "cube") == 1 and len(s2) == 2, "no duplicate cube, nothing else added")
check(s2["cabin_1"] == s1["cabin_1"], "cabin unchanged")
check(s2["cube_1"]["position"] == s1["cube_1"]["position"], "cube did not move")

# ---- Test 3: "Add a tree and make the cube bounce three times." ----
print("\nTest 3: Add a tree and make the cube bounce three times.")
r3, s3 = run("p1_test3", [
    {"op": "add", "id": "tree_1", "asset": "tree", "position": [-2.6, 2.2, 0]},
    {"op": "bounce", "id": "cube_1", "cycles": 3, "height": 1.5, "frames_per_cycle": 24},
])
check(r3["status"] == "ok" and all(r["ok"] for r in r3["op_results"]), "test3 applied, every op ok")
check(count(s3, "tree") == 1 and count(s3, "cube") == 1 and count(s3, "cabin") == 1 and len(s3) == 3,
      "one new tree; cube + cabin still there, nothing duplicated")
check(s3["cabin_1"] == s1["cabin_1"] and s3["cube_1"]["color"] == "#3B6FD9", "cabin unchanged, cube still blue")
scene = bpy.context.scene
check((scene.frame_start, scene.frame_end) == (1, 73), f"timeline fits the bounce: {scene.frame_start}-{scene.frame_end}")
zkeys = [a for a in s3["cube_1"].get("animation", []) if a["property"] == "position" and a["axis"] == "z"]
check(len(zkeys) == 1 and len(zkeys[0]["keys"]) == 7, f"7 Z keyframes read back: {zkeys[0]['keys'] if zkeys else None}")
cube = bpy.data.objects["cube_1"]
z = sample_z(cube, scene.frame_start, scene.frame_end)
peaks, airborne, step = bounce_profile(z, 0.0, 1.5)
print(f"  sampled z (every 4th frame): {[round(v, 2) for v in z[::4]]}")
check(abs(z[0]) < 1e-4, f"starts at ground level (z={z[0]:.4f})")
check(len(peaks) == 3, f"three bounces during playback (peaks at frames {[p + 1 for p in peaks]})")
check(all(abs(z[p] - 1.5) < 1e-3 for p in peaks), "each bounce reaches the requested height 1.5 m")
check(abs(z[-1]) < 1e-4, f"ends at ground level (z={z[-1]:.4f} at frame {scene.frame_end})")
check(airborne >= 60, f"visible motion: airborne on {airborne}/73 frames")
check(step < 0.3, f"smooth, no abrupt jump: max per-frame change {step:.3f} m")
check(min(z) > -1e-4, "never sinks below the ground")
ground_hits = [i + 1 for i in range(len(z)) if abs(z[i]) < 1e-4]
check(ground_hits == [1, 25, 49, 73], f"touches ground exactly at frames {ground_hits}")

# ---- persistence: later edits must not reset the animation ----
print("\nPersistence: recolor + move the bouncing cube")
keys_before = zkeys[0]["keys"]
r4, s4 = run("p1_persist", [{"op": "modify", "id": "cube_1", "set": {"color": "#D95C52", "position": [-3.0, 0, 0]}}])
zk4 = [a for a in s4["cube_1"].get("animation", []) if a["axis"] == "z"]
check(zk4 and zk4[0]["keys"] == keys_before, "bounce keys survive a recolor + XY move")
check(s4["cube_1"]["position"][:2] == [-3.0, 0], "cube moved in XY")
peaks4, _, _ = bounce_profile(sample_z(cube, 1, 73), 0.0, 1.5)
check(len(peaks4) == 3 and s4["tree_1"] == s3["tree_1"] and s4["cabin_1"] == s1["cabin_1"],
      "still three bounces; tree + cabin untouched")
run("p1_restore", [{"op": "modify", "id": "cube_1", "set": {"color": "#3B6FD9", "position": [-2.6, 0, 0]}}])

# ---- regression: chained animate segments used to overwrite each other ----
print("\nRegression: chained animate (up, then back down) on a scratch sphere")
run("p1_chain", [
    {"op": "add", "id": "sphere_9", "asset": "sphere", "position": [6, 6, 0]},
    {"op": "animate", "id": "sphere_9", "property": "position", "start_frame": 1, "end_frame": 12, "to": [6, 6, 2]},
    {"op": "animate", "id": "sphere_9", "property": "position", "start_frame": 12, "end_frame": 24, "to": [6, 6, 0]},
])
sz = sample_z(bpy.data.objects["sphere_9"], 1, 24)
check(abs(sz[11] - 2) < 1e-3 and abs(sz[0]) < 1e-4 and abs(sz[23]) < 1e-4,
      f"up-then-down keeps its peak (z@1={sz[0]:.2f}, z@12={sz[11]:.2f}, z@24={sz[23]:.2f})")
run("p1_chain_cleanup", [{"op": "delete", "id": "sphere_9"}])
check((scene.frame_start, scene.frame_end) == (1, 73), "timeline back to the bounce after cleanup")

# ---- atomicity: a plan with one bad op changes nothing ----
r6, s6 = run("p1_atomic", [{"op": "modify", "id": "cube_1", "set": {"color": "#000000"}},
                           {"op": "bounce", "id": "ghost_1", "cycles": 2}])
check(r6["status"] == "error" and r6["applied"] == 0 and s6["cube_1"]["color"] == "#3B6FD9",
      "plan with an unknown id is rejected whole; cube stays blue")

if "--save" in args:
    out = Path(args[args.index("--save") + 1]).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if not any(o.type == "LIGHT" for o in scene.objects):
        sun = bpy.data.objects.new("TD_Sun", bpy.data.lights.new("TD_Sun", "SUN"))
        sun.data.energy = 3.0
        sun.rotation_euler = (0.7, 0.2, 0.6)
        scene.collection.objects.link(sun)
    cam = bpy.data.objects.get("Camera") or bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    if cam.name not in scene.collection.objects:
        scene.collection.objects.link(cam)
    cam.location = (7.5, -9.5, 5.5)
    cam.rotation_euler = (math.radians(66), 0, math.radians(38))
    scene.camera = cam
    scene.frame_set(1)
    bpy.ops.wm.save_as_mainfile(filepath=str(out), compress=True)
    print(f"saved {out}")

print(f"\n{'ALL PASSED' if not fails else f'{len(fails)} FAILED'}")
sys.exit(1 if fails else 0)
