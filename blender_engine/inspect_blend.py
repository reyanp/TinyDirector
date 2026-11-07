"""Open a saved .blend, play its timeline, and print what each director object actually does.

  blender -b docs/phase1_demo.blend --python blender_engine/inspect_blend.py

Prints one line `TD_INSPECT {json}` with the scene frame range, every director object
(asset, color, location, Z per frame) so a test can check the FILE, not just the live session.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bpy  # noqa: E402

from blender_engine import engine  # noqa: E402

scene = bpy.context.scene
out = {"file": bpy.data.filepath, "frame_start": scene.frame_start, "frame_end": scene.frame_end, "objects": {}}
objs = engine.director_objects()
z = {oid: [] for oid in objs}
for f in range(scene.frame_start, scene.frame_end + 1):
    scene.frame_set(f)
    for oid, obj in objs.items():
        z[oid].append(round(obj.matrix_world.translation.z, 4))
scene.frame_set(scene.frame_start)
for row in engine.read_scene():
    row["z_per_frame"] = z[row["id"]]
    out["objects"][row["id"]] = row
print("TD_INSPECT " + json.dumps(out))
