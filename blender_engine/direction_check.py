"""Real-Blender check of stage direction (headless):

  blender -b --factory-startup --python blender_engine/direction_check.py -- [--render DIR]

Builds the LED circuit, then focuses battery / resistor / led / current / overview and checks the camera moved
to frame each target (target centre near the image centre, nothing else changed), the halo, the caption, and
that a lesson value change (update) keeps the focus. --render DIR writes one camera image per focus.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bpy  # noqa: E402
from bpy_extras.object_utils import world_to_camera_view  # noqa: E402

from blender_engine import circuit, direction  # noqa: E402
from director.circuit.display import circuit_request  # noqa: E402
from director.circuit.model import LedCircuit  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
render_dir = Path(args[args.index("--render") + 1]) if "--render" in args else None
bpy.ops.wm.read_factory_settings(use_empty=True)
fails: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  ok   " if ok else "  FAIL ") + what)
    if not ok:
        fails.append(what)


req = circuit_request("dc1", "build", LedCircuit(), caption="Direction check.", title="Why does an LED need a resistor?")
n, errs = circuit.apply(req)
check(not errs, f"circuit build ({errs})")
sc = bpy.context.scene
cam = sc.camera
home = cam.matrix_world.translation.copy()
values_before = circuit.read_back()["params"]

for target in ["battery", "resistor", "led", "current", "overview"]:
    errs = direction.apply({"kind": "direct", "request_id": f"f_{target}", "focus": target,
                            "caption": f"Looking at the {target}."})
    check(not errs, f"focus {target} applied ({errs})")
    rb = direction.read_back()
    check(rb["focus"] == target, f"read-back focus = {rb['focus']}")
    bpy.context.view_layer.update()
    if target == "overview":
        check((cam.matrix_world.translation - home).length < 1e-4, "overview returns to the lesson camera's framing")
        check(rb["halo"] is None, "no halo on overview")
    else:
        objs = direction._objects_for(target)
        lo, hi = direction._bbox(objs)
        p = world_to_camera_view(sc, cam, (lo + hi) / 2)
        check(abs(p.x - 0.5) < 0.12 and abs(p.y - 0.5) < 0.12, f"{target} centred in frame ({p.x:.2f}, {p.y:.2f})")
        check((cam.matrix_world.translation - home).length > (0.1 if target == "current" else 0.5),
              f"camera moved for {target}")
        check(rb["halo"] == (target if target in direction.HALO else None), f"halo = {rb['halo']}")
    check(rb["caption"] == f"Looking at the {target}.", "caption replaced")
    if render_dir:
        circuit.render_preview(render_dir / f"focus_{target}.jpg", 960, 540, samples=8, file_format="JPEG")

direction.apply({"kind": "direct", "request_id": "f2", "focus": "resistor"})
n, errs = circuit.apply(circuit_request("dc2", "update", LedCircuit().with_resistance(1000), caption="1 k", title="t"))
check(not errs and direction.read_back()["focus"] == "resistor", "a value update keeps the focus")
check(circuit.read_back()["params"]["resistance_ohm"] == 1000, "update applied")
check(direction.apply({"kind": "direct", "request_id": "x", "focus": "capacitor_9"}) != [], "unknown target rejected")
check(direction.apply({"kind": "direct", "request_id": "x", "focus": "a b"}) != [], "bad target id rejected")
check(values_before["resistance_ohm"] == 330, "direction never changed lesson values")
direction.clear()
check(direction.read_back()["halo"] is None and direction.read_back()["focus"] is None, "clear")
print(f"direction_check: {'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
sys.exit(1 if fails else 0)
