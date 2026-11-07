"""Real-Blender check of the LED + resistor lesson (no model, no bridge).

  blender -b --factory-startup --python blender_engine/circuit_check.py [-- --out DIR]

Builds the lesson at 330 ohm, updates to 1000 ohm, then back to 330 ohm, using requests made by
director/circuit/display.py (pure Python, same code the tutor uses). After each step it reads the scene
back and checks values, labels, flow speed, LED glow, bands, and that objects were not recreated.
Renders one preview per step into DIR (default experiments/out/circuit_check/).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = Path(argv[argv.index("--out") + 1]) if "--out" in argv else ROOT / "experiments" / "out" / "circuit_check"

import bpy  # noqa: E402

bpy.ops.wm.read_factory_settings(use_empty=True)  # no default cube

from blender_engine import circuit, engine  # noqa: E402
from director.circuit import check  # noqa: E402  (pure Python: dataclasses + model)
from director.circuit.display import circuit_request  # noqa: E402
from director.circuit.model import LedCircuit  # noqa: E402

OUT.mkdir(parents=True, exist_ok=True)
fails: list[str] = []


def inspect() -> dict:
    res = engine.inspect("check")
    res["circuit"] = circuit.read_back()
    return res


def step(name: str, action: str, ohms: float, caption: str) -> dict:
    before = inspect()
    req = circuit_request(f"check_{name}", action, LedCircuit().with_resistance(ohms), caption)
    out = circuit.make_result(req["request_id"], req)
    checks = check.run(req, before, out)
    print(f"\n--- {name}: {action} R={ohms:g} -> {check.summary(checks)}")
    for c in checks:
        print(c.line())
        if not c.ok:
            fails.append(f"{name}: {c.what}")
    c = out["circuit"]
    print(f"  labels: current={c['labels']['label_current']!r} equation={c['labels']['label_equation']!r}")
    print(f"  flow lap={c['flow']['lap_frames']} frames, LED emission={c['led']['emission_at_show']} "
          f"light={c['led']['light_w_at_show']} W, bands={c['resistor_bands']}")
    png = circuit.render_preview(OUT / f"{name}.png", 1280, 720, samples=32)
    print(f"  preview: {png}")
    return out


s1 = step("1_build_330", "build", 330, "Teach: the LED keeps about 2 V; the resistor takes the other 7 V.")
s2 = step("2_update_1000", "update", 1000, "A bigger resistor lets less current through, so the LED is dimmer.")
s3 = step("3_back_to_330", "update", 330, "Back to 330 Ω: the current returns to 21.2 mA.")

uids = [{o["id"]: o["uid"] for o in s["circuit"]["objects"]} for s in (s1, s2, s3)]
ok = uids[0] == uids[1] == uids[2]
print(f"\n{'PASS' if ok else 'FAIL'} every object kept its uid across build -> 1000 -> 330 ({len(uids[0])} objects)")
if not ok:
    fails.append("uids changed")
same = {k: v for k, v in s3["circuit"].items() if k != "objects"} == {k: v for k, v in s1["circuit"].items()
                                                                       if k != "objects"} or None
labels_back = s3["circuit"]["labels"]["label_current"] == s1["circuit"]["labels"]["label_current"]
print(f"{'PASS' if labels_back else 'FAIL'} step 3 shows the same current as step 1")
if not labels_back:
    fails.append("step 3 current differs from step 1")

blend = OUT / "circuit_check.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(blend), compress=True)
print(f"saved {blend}")
print("\nCIRCUIT CHECK: " + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
