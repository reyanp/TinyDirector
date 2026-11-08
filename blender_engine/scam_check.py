"""Real-Blender check of 'Spot the Scam' (headless):

  blender -b --factory-startup --python blender_engine/scam_check.py -- [--render DIR] [--save FILE.blend]

1. build: the phone shows the FICTIONAL message exactly (sender, 4 segments, banner), no tags yet.
2. mark: the detector's warning tags appear next to the right segments; the camera can focus each one
   (glowing outline, callout) and every target is inside the frame.
3. outcomes: both hypothetical outcomes appear beside the untouched message phone, the choice is marked,
   the camera frames all three; clear_outcomes removes every outcome object and restores the HUD.
4. a circuit build afterwards removes the whole scam scene (one lesson on stage).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import bpy  # noqa: E402
from bpy_extras.object_utils import world_to_camera_view  # noqa: E402

from blender_engine import circuit, direction, scam  # noqa: E402
from director.circuit.display import circuit_request  # noqa: E402
from director.circuit.model import LedCircuit  # noqa: E402
from director.scam import scenario  # noqa: E402
from director.scam.detector import detect_scenario  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
render_dir = Path(args[args.index("--render") + 1]) if "--render" in args else None
save = Path(args[args.index("--save") + 1]) if "--save" in args else None
bpy.ops.wm.read_factory_settings(use_empty=True)
fails: list[str] = []


def check(ok, what):
    print(("  ok   " if ok else "  FAIL ") + what)
    if not ok:
        fails.append(what)


def render(name):
    if render_dir:
        circuit.render_preview(render_dir / f"{name}.jpg", 1280, 720, frame=60, samples=16, file_format="JPEG")


def in_frame(objs) -> bool:
    sc, cam = bpy.context.scene, bpy.context.scene.camera
    bpy.context.view_layer.update()
    lo, hi = direction._bbox(objs)
    for p in (lo, hi):
        v = world_to_camera_view(sc, cam, p)
        if not (0 <= v.x <= 1 and 0 <= v.y <= 1 and v.z > 0):
            return False
    return True


print("== 1. build ==")
res = scam.make_result("s1", scenario.build_request("s1"))
check(res["status"] == "ok", f"build ({res['errors']})")
rb = res["scam"]
check(rb["sender"] == scenario.SENDER, f"sender {rb['sender']!r}")
check(rb["segments"] == {s["id"]: s["text"] for s in scenario.SEGMENTS}, f"message segments {rb['segments']}")
check(rb["title"] == "Spot the Scam" and "FICTIONAL" in rb["banner"], f"HUD title/banner {rb['title']!r} {rb['banner']!r}")
check(rb["tags"] == {}, "no warning tags before the learner looks")
check(in_frame(scam.objects_for("phone")), "the whole phone is in the camera view")
render("1_phone")

print("== 2. warning signs ==")
det = detect_scenario()
res = scam.make_result("s2", scenario.mark_request(det, "s2"))
want = scenario.indicator_tags(det)
check(res["status"] == "ok" and res["scam"]["tags"] == want, f"tags {res['scam']['tags']}")
for t in ("sender", "urgency", "link"):
    e = direction.apply({"kind": "direct", "focus": t, "callout": {"value": want[t][:24], "note": "warning sign"}})
    d = direction.read_back()
    check(not e and d["focus"] == t and scam.read_back()["highlight"] == t and in_frame(scam.objects_for(t)),
          f"focus {t}: outline glows, in frame ({e})")
    render(f"2_focus_{t}")
direction.apply({"kind": "direct", "focus": "overview"})
check(scam.read_back()["highlight"] is None, "overview: no outline")
direction.apply({"kind": "direct", "caption": "Caption test."})
check(direction.read_back()["caption"] == "Caption test.", "caption goes to the scam HUD")
render("2_marked")
before = scam.read_back()

print("== 3. outcomes ==")
res = scam.make_result("s3", scenario.outcomes_request("follow_link", "s3"))
o = res["scam"]["outcomes"]
check(res["status"] == "ok" and o["active"] and o["choice"] == "follow_link" and o["marked"] == ["follow_link"],
      f"outcomes shown, choice marked ({res['errors']})")
check(any("SIMULATED" in t for t in o["follow_link"]["texts"]) and
      any("hypothetical" in h for h in o["headlines"].values()), "both labeled hypothetical / SIMULATED")
check(any("7726" in t for t in o["verify_official"]["texts"]), "official-channel side shows how to report")
same = {k: v for k, v in res["scam"].items() if k not in ("outcomes", "objects", "title", "highlight")}
check(same == {k: v for k, v in before.items() if k not in ("outcomes", "objects", "title", "highlight")},
      "the message phone is unchanged")
check(res["direction"]["focus"] == "outcomes" and in_frame(scam.objects_for("outcome_a"))
      and in_frame(scam.objects_for("outcome_b")) and in_frame(scam.objects_for("phone")), "camera frames all three")
render("3_outcomes")
for side in ("outcome_a", "outcome_b"):
    e = direction.apply({"kind": "direct", "focus": side})
    check(not e and in_frame(scam.objects_for(side)), f"focus {side} ({e})")
    render(f"3_{side}")
if save:
    bpy.ops.wm.save_as_mainfile(filepath=str(save), copy=True)
res = scam.make_result("s4", scenario.outcomes_request("verify_official", "s4"))
check(res["scam"]["outcomes"]["marked"] == ["verify_official"], "a new choice re-marks (old outcomes replaced)")
res = scam.make_result("s5", scenario.clear_outcomes_request("s5"))
check(res["status"] == "ok" and not res["scam"]["outcomes"]["active"], "clear_outcomes")
check(not any(o.get("scam_group") in ("outcome_a", "outcome_b") or o.get("scam_id") in ("outcome_a", "outcome_b")
              for o in bpy.data.objects), "no outcome object left")
after = scam.read_back()
check(after["title"] == "Spot the Scam" and after["segments"] == before["segments"] and after["tags"] == before["tags"]
      and after["objects"] == before["objects"], "message phone + HUD exactly as before")

print("== 4. one lesson on stage ==")
n, errs = circuit.apply(circuit_request("c1", "build", LedCircuit(), caption="", title="t"))
check(not errs and not scam.present() and not any(o.get("scam_part") for o in bpy.data.objects),
      "a circuit build removes the scam scene")
check(circuit.present(), "the circuit is built")
print(f"scam_check: {'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}")
sys.exit(1 if fails else 0)
