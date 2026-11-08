"""Real-Blender check of the What-If Lab (headless):

  blender -b --factory-startup --python blender_engine/compare_check.py -- [--render DIR] [--save FILE.blend]

1. Cinematic direction: focus + halo + big measurement callout per part, current-arrow emphasis, overview.
2. Split-reality comparison (330 ohm on stage vs a 1,000 ohm what-if copy): the copy's labels, arrow speed and
   LED glow match the calculation; the original lesson objects are unchanged (same uid, position, values);
   the camera frames both sides; both circuits are powered over the whole playback range.
3. Clear: the copy and everything it added are gone, the hidden HUD lines are back, the camera is home.
   A circuit update while comparing ends the comparison first.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bpy  # noqa: E402
from bpy_extras.object_utils import world_to_camera_view  # noqa: E402

from blender_engine import circuit, compare, direction  # noqa: E402
from director.circuit.display import circuit_request, compare_clear_request, compare_request, display_for  # noqa: E402
from director.circuit.model import LedCircuit, solve  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
render_dir = Path(args[args.index("--render") + 1]) if "--render" in args else None
save = Path(args[args.index("--save") + 1]) if "--save" in args else None
bpy.ops.wm.read_factory_settings(use_empty=True)
fails: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  ok   " if ok else "  FAIL ") + what)
    if not ok:
        fails.append(what)


def render(name: str, frame: int = 160) -> None:
    if render_dir:
        circuit.render_preview(render_dir / f"{name}.jpg", 1280, 720, frame=frame, samples=16, file_format="JPEG")


def in_frame(objs, margin=0.0) -> bool:
    sc, cam = bpy.context.scene, bpy.context.scene.camera
    bpy.context.view_layer.update()
    lo, hi = direction._bbox(objs)
    for p in (lo, hi, (lo + hi) / 2):
        v = world_to_camera_view(sc, cam, p)
        if not (-margin <= v.x <= 1 + margin and -margin <= v.y <= 1 + margin and v.z > 0):
            return False
    return True


base, alt = LedCircuit(), LedCircuit().with_resistance(1000)
n, errs = circuit.apply(circuit_request("w1", "build", base, caption="", title="Why does an LED need a resistor?"))
check(not errs, f"circuit build ({errs})")
before = circuit.read_back()
render("0_lesson")

print("== 1. cinematic direction ==")
tour = [("battery", {"value": "9 V", "note": "the battery pushes the current"}),
        ("resistor", {"value": "7.0 V", "note": "across the 330 Ω resistor"}),
        ("current", {"value": "21.2 mA", "note": "the same current all the way around"}),
        ("led", {"value": "≈ 2 V", "note": "LED forward voltage (simplified model)"}),
        ("overview", None)]
for i, (target, callout) in enumerate(tour, 1):
    req = {"kind": "direct", "request_id": f"t{i}", "focus": target, "caption": f"Stop {i}: {target}."}
    if callout:
        req["callout"] = callout
    e = direction.apply(req)
    rb = direction.read_back()
    check(not e and rb["focus"] == target, f"tour stop {i}: focus {target} ({e})")
    check(rb["callout"] == callout, f"   callout read back {rb['callout']}")
    check(rb["flow_emphasis"] == (target == "current"), f"   current arrows emphasised = {rb['flow_emphasis']}")
    render(f"1_tour_{i}_{target}")
check(circuit.read_back()["params"] == before["params"], "direction changed no lesson value")

print("== 2. split-reality comparison ==")
before = circuit.read_back()  # the tour's subtitles changed label_caption; compare against the stage as it is now
res = compare.make_result("c1", compare_request("c1", base, alt))
check(res["status"] == "ok", f"compare show ({res['errors']})")
cb = res["compare"]
want = display_for(solve(alt))
a = cb.get("alternative", {})
check(a.get("params") == {"source_v": 9.0, "resistance_ohm": 1000.0, "led_vf": 2.0}, f"what-if params {a.get('params')}")
check(a.get("labels", {}).get("label_current") == "I = 7.0 mA", f"what-if current label {a.get('labels', {}).get('label_current')!r}")
check(a.get("labels", {}).get("label_resistor", "").startswith("Resistor  R = 1,000 Ω"), "what-if resistor label")
check(abs(a["flow"]["lap_frames"] - want["flow_lap_frames"]) < 0.02 and a["flow"]["power"] == 1.0,
      f"what-if arrows: lap {a['flow']['lap_frames']} frames (calc {want['flow_lap_frames']:.2f}), powered")
check(abs(a["led"]["emission"] - want["led_emission"]) < 1e-3, f"what-if LED glow {a['led']['emission']} (calc {want['led_emission']:.3f})")
check(a["resistor_bands"] == [b.upper() for b in want["resistor_bands"]], f"what-if bands {a['resistor_bands']}")
check(cb["headlines"]["baseline"].endswith("= 21.2 mA") and cb["headlines"]["alternative"].endswith("= 7.0 mA"),
      f"headlines {cb['headlines']}")
after = res["circuit"]
check(after["params"] == before["params"] and after["labels"] == before["labels"], "original lesson values + labels unchanged")
check({o["id"]: (o["uid"], o["position"]) for o in after["objects"]} ==
      {o["id"]: (o["uid"], o["position"]) for o in before["objects"]}, "original objects: same uid and position")
check(after["flow"] == before["flow"] and after["led"] == before["led"], "original arrows + LED glow unchanged")
check(bpy.context.scene.frame_start >= compare.STEADY_FRAME, f"playback starts at {bpy.context.scene.frame_start} "
      f"(both circuits powered; original powers on at {circuit.POWER_ON[1]})")
check(res["direction"]["focus"] == "compare", "camera framed both sides")
check(in_frame(compare.side_objects("baseline")) and in_frame(compare.side_objects("alternative")),
      "both circuits are inside the camera view")
hud = circuit.objects()
check(hud["label_equation"].hide_render and hud["label_title"].hide_render, "the lesson's own title/equation hidden")
render("2_compare", frame=150)
render("2_compare_b", frame=170)
for side in ("baseline", "alternative"):
    e = direction.apply({"kind": "direct", "request_id": f"f{side}", "focus": side})
    check(not e and in_frame(compare.side_objects(side)), f"focus {side} ({e})")
    render(f"3_focus_{side}")
direction.apply({"kind": "direct", "request_id": "fo", "focus": "overview"})
check(direction.read_back()["focus"] == "overview" and in_frame(compare.side_objects("alternative")),
      "overview during a comparison frames both sides")
if save:
    bpy.ops.wm.save_as_mainfile(filepath=str(save), copy=True)
    print(f"  saved {save}")

print("== 3. clear ==")
res = compare.make_result("c2", compare_clear_request("c2"))
check(res["status"] == "ok" and not res["compare"]["active"], f"compare clear ({res['errors']})")
check(not any(o.get("td_compare") for o in bpy.data.objects), "no comparison object left")
check(not any(m.get("td_compare") for m in bpy.data.materials), "no comparison material left")
check(not hud["label_equation"].hide_render and not hud["label_title"].hide_render, "the lesson's HUD is back")
check(bpy.context.scene.frame_start == 1, "playback range restored")
check(circuit.read_back() == before, "the lesson read-back is exactly what it was before the comparison")
render("4_after_clear")
compare.make_result("c3", compare_request("c3", base, alt))
n, errs = circuit.apply(circuit_request("w2", "update", alt, caption="", title="t"))
compare.clear(move_camera=True)  # what the worker does before every circuit request
check(not compare.active() and circuit.read_back()["params"]["resistance_ohm"] == 1000, "update after a comparison")
bad = compare.make_result("c4", compare_request("c4", base, alt))  # baseline no longer matches the stage
check(bad["status"] == "error" and not compare.active(), f"stale baseline rejected: {bad['errors'][:1]}")
print(f"compare_check: {'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
sys.exit(1 if fails else 0)
