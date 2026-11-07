"""Circuit tutor end-to-end: three prompts in ONE Blender scene, live Nemotron, checked read-backs.

  python -m director.dev.circuit_e2e            # live Nemotron + real headless Blender
  python -m director.dev.circuit_e2e --mock     # offline rule-based model (MOCK) + real Blender

Uses a private bridge folder (shared/state/circuit_run), never an open Blender. Records the scene state
Blender reports before and after every prompt to experiments/out/circuit_lesson_states.json, saves
experiments/out/circuit_lesson.blend, copies the worker's per-step snapshots and a 1280x720 preview
render into docs/circuit/.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

RUN_DIR = Path(__file__).resolve().parents[2] / "shared" / "state" / "circuit_run"
os.environ["TD_SHARED_DIR"] = str(RUN_DIR)

from .. import bridge, paths  # noqa: E402
from ..nemotron_client import ConfigError, NemotronClient  # noqa: E402
from ..tutor import BridgeExecutor, run_turn  # noqa: E402

PROMPTS = ["Teach me why LEDs need resistors.", "Change the resistor to 1,000 ohms.", "Put it back to 330 ohms."]
OUT = paths.REPO_ROOT / "experiments" / "out"
DOCS = paths.REPO_ROOT / "docs" / "circuit"


def main() -> int:
    paths.load_env()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--blender", default=shutil.which("blender") or "blender")
    args = ap.parse_args()
    if args.mock:
        from ..circuit.mock import TutorMockModel
        client = TutorMockModel()
    else:
        try:
            client = NemotronClient(temperature=0.2)
        except ConfigError as exc:
            print(f"CONFIG ERROR: {exc}")
            return 2
    label = "MOCK model (NOT Nemotron)" if args.mock else f"LIVE Nemotron ({client.model})"
    print(f"== circuit tutor end-to-end: {label} + real Blender (headless) ==")
    shutil.rmtree(RUN_DIR, ignore_errors=True)
    for d in (paths.INBOX, paths.OUTBOX, paths.STATE):
        d.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
    blend = OUT / "circuit_lesson.blend"
    env = {**os.environ, "TD_SHARED_DIR": str(RUN_DIR)}
    log = open(paths.STATE / "blender.log", "w")
    worker = subprocess.Popen([args.blender, "-b", "--factory-startup", "--python",
                               str(paths.REPO_ROOT / "blender_engine" / "worker.py"), "--", "--loop", "--save",
                               str(blend)], stdout=log, stderr=subprocess.STDOUT, env=env)
    executor = BridgeExecutor(timeout=120)
    fails, record, history = [], [], []

    def check(cond, msg):
        print(("  PASS " if cond else "  FAIL ") + msg)
        if not cond:
            fails.append(msg)

    try:
        bridge.inspect(timeout=90)
        for i, text in enumerate(PROMPTS, 1):
            print(f"\n=== Step {i}: {text!r} ===", flush=True)
            before = executor.inspect()
            t0 = time.time()
            code = run_turn(text, client, executor, history)
            after = executor.inspect()
            snaps = sorted(paths.OUTBOX.glob("*.jpg"), key=lambda p: p.stat().st_mtime)
            if snaps:
                shutil.copy(snaps[-1], DOCS / f"step{i}.jpg")
            record.append({"step": i, "prompt": text, "exit_code": code, "seconds": round(time.time() - t0, 1),
                           "before": {"scene_version": before["scene_version"], "circuit": before.get("circuit")},
                           "after": {"scene_version": after["scene_version"], "circuit": after.get("circuit")}})
            check(code == 0, f"exit code {code} (0 = executed and every read-back check passed)")

        print("\n=== Checks across the three prompts (Blender read-back) ===")
        s = [r["after"]["circuit"] or {} for r in record]
        val = lambda c, *k: (lambda d: [d := (d or {}).get(x) for x in k][-1])(c)  # noqa: E731
        check(s[0].get("present") and val(s[0], "params", "resistance_ohm") == 330, "step 1 built the 330 Ω lesson")
        check(val(s[0], "labels", "label_current") == "I = 21.2 mA", f"step 1 shows {val(s[0], 'labels', 'label_current')!r}")
        check(val(s[1], "params", "resistance_ohm") == 1000 and val(s[1], "labels", "label_current") == "I = 7.0 mA",
              f"step 2 shows R=1000 and {val(s[1], 'labels', 'label_current')!r}")
        check(val(s[2], "params", "resistance_ohm") == 330 and val(s[2], "labels", "label_current") == "I = 21.2 mA",
              f"step 3 back to R=330 and {val(s[2], 'labels', 'label_current')!r}")
        lap = [val(c, "flow", "lap_frames") for c in s]
        glow = [val(c, "led", "emission_at_show") for c in s]
        check(lap[1] and lap[0] and abs(lap[1] / lap[0] - (21.2121 / 7.0)) < 0.01,
              f"markers {lap[1] / lap[0]:.3f}x slower at 1,000 Ω (current ratio {21.2121 / 7.0:.3f})" if all(lap) else "lap")
        check(glow[1] < glow[0] and glow[2] == glow[0], f"LED glow {glow[0]} -> {glow[1]} -> {glow[2]}")
        check(val(s[1], "resistor_bands") != val(s[0], "resistor_bands") == val(s[2], "resistor_bands"),
              "resistor bands changed to brown-black-red and back")
        uid = [{o["id"]: (o["uid"], o["position"]) for o in c.get("objects", [])} for c in s]
        check(len(uid[0]) == 17 and uid[0] == uid[1] == uid[2],
              f"all {len(uid[0])} objects persisted with the same uid and position through both edits")
    finally:
        worker.terminate()
        try:
            worker.wait(15)
        except subprocess.TimeoutExpired:
            worker.kill()
        log.close()
    (OUT / "circuit_lesson_states.json").write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
    if blend.exists():
        shutil.copy(blend, DOCS / "circuit_lesson.blend")
        expr = ("import sys; sys.path.insert(0, %r); from blender_engine import circuit; "
                "circuit.render_preview(%r, 1280, 720, samples=48)") % (str(paths.REPO_ROOT), str(DOCS / "preview.png"))
        subprocess.run([args.blender, "-b", str(blend), "--python-expr", expr], capture_output=True, timeout=600)
    print(f"\nstates: {OUT / 'circuit_lesson_states.json'}\nscene:  {DOCS / 'circuit_lesson.blend'}\n"
          f"render: {DOCS / 'preview.png'}  (+ step1-3.jpg worker snapshots)")
    print(f"\n{label}: {'ALL PASSED' if not fails else f'{len(fails)} FAILED: {fails}'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
