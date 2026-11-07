"""Asset-library end-to-end: Nemotron plans with the UNCHANGED contract; Blender renders library assets.

Starts a headless worker with TD_ASSET_LIBRARY=1 in a private bridge folder (never your open Blender),
then runs prompts through the normal pipeline (inspect -> plan -> validate -> Blender -> read back ->
post-check) and checks that library assets were imported, edited with the existing ops, and persisted.

  python -m director.dev.library_e2e            # live Nemotron
  python -m director.dev.library_e2e --mock     # offline rule-based model (MOCK)
Needs: python assets/fetch.py && blender -b --factory-startup --python assets/build_library.py
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

RUN_DIR = Path(__file__).resolve().parents[2] / "shared" / "state" / "library_run"
os.environ["TD_SHARED_DIR"] = str(RUN_DIR)

from .. import bridge, paths  # noqa: E402
from ..cli import main as cli_main  # noqa: E402

PROMPTS = [
    "Create a log cabin with a pine tree on each side and a campfire in front of it.",
    "Make the cabin red and move the campfire one meter to the right.",
    "Make the campfire bounce twice.",
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--blender", default=shutil.which("blender") or "blender")
    ap.add_argument("--save", default=str(paths.REPO_ROOT / "experiments" / "out" / "library_e2e.blend"))
    args = ap.parse_args()
    if not (paths.REPO_ROOT / "assets" / "library" / "index.json").exists():
        print("asset library not built: python assets/fetch.py && "
              "blender -b --factory-startup --python assets/build_library.py")
        return 2
    label = "MOCK model (NOT Nemotron)" if args.mock else "LIVE Nemotron"
    print(f"== asset library end-to-end: {label} + real Blender, TD_ASSET_LIBRARY=1 ==")
    shutil.rmtree(RUN_DIR, ignore_errors=True)
    for d in (paths.INBOX, paths.OUTBOX, paths.STATE):
        d.mkdir(parents=True, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(args.save)), exist_ok=True)
    env = {**os.environ, "TD_SHARED_DIR": str(RUN_DIR), "TD_ASSET_LIBRARY": "1"}
    log = open(paths.STATE / "blender.log", "w")
    worker = subprocess.Popen([args.blender, "-b", "--factory-startup", "--python",
                               str(paths.REPO_ROOT / "blender_engine" / "worker.py"), "--", "--loop",
                               "--save", os.path.abspath(args.save)], stdout=log, stderr=subprocess.STDOUT, env=env)
    fails, states = [], []

    def check(cond, msg):
        print(("  PASS " if cond else "  FAIL ") + msg)
        if not cond:
            fails.append(msg)

    try:
        bridge.inspect(timeout=60)
        for i, text in enumerate(PROMPTS, 1):
            print(f"\n--- Step {i}: {text!r} ---", flush=True)
            code = cli_main(["--send", "--text", text, "--timeout", "90"] + (["--mock"] if args.mock else []))
            states.append({o["id"]: o for o in bridge.inspect(timeout=30)["scene_objects"]})
            check(code == 0, f"exit code {code} (0 = executed and post-check passed)")

        print("\n--- Library checks on Blender's read-back ---")
        s1, s2, s3 = states
        lib = {i: o.get("library") for i, o in s1.items()}
        print(f"  objects -> library asset: {lib}")
        cabin = next((i for i, o in s1.items() if o["asset"] == "cabin"), None)
        fire = next((i for i, o in s1.items() if o["asset"] == "campfire"), None)
        trees = [i for i, o in s1.items() if o["asset"] == "tree"]
        check(cabin and s1[cabin].get("library") == "td_cabin_winter", "cabin is the library cabin")
        check(fire and s1[fire].get("library") == "ph_fire_pit_lit", "campfire is the Poly Haven fire pit")
        check(len(trees) == 2 and all(s1[t].get("library") == "td_pine_snowy" for t in trees), "two library pines")
        r, g, b = (int(s2[cabin]["color"][k:k + 2], 16) for k in (1, 3, 5)) if cabin in s2 else (0, 0, 0)
        check(r > 150 and r > g + 60 and r > b + 60, f"cabin tinted red via the existing color op: {s2.get(cabin, {}).get('color')}")
        check(fire in s2 and abs(s2[fire]["position"][0] - s1[fire]["position"][0] - 1.0) < 0.05,
              f"campfire moved +1 m in X: {s1.get(fire, {}).get('position')} -> {s2.get(fire, {}).get('position')}")
        check(all(s2[t] == s1[t] for t in trees), "trees untouched by step 2")
        zk = [a for a in (s3.get(fire, {}).get("animation") or []) if a["axis"] == "z"]
        check(bool(zk) and len([k for k in zk[0]["keys"] if k[1] > zk[0]["keys"][0][1] + 0.1]) == 2,
              f"campfire bounces twice: {zk[0]['keys'] if zk else None}")
        check(all(s3[i].get("library") == s1[i].get("library") for i in s1),
              "every object kept its library asset across all edits (persistence)")
        check(s3[cabin]["color"] == s2[cabin]["color"] and s3[fire]["position"][:2] == s2[fire]["position"][:2],
              "step 3 kept the red cabin and the moved campfire")
    finally:
        worker.terminate()
        try:
            worker.wait(10)
        except subprocess.TimeoutExpired:
            worker.kill()
        log.close()
    shots = sorted(paths.OUTBOX.glob("*.jpg"))
    if shots:
        print(f"\nlatest Blender snapshot: {shots[-1]}")
    print(f"saved scene: {args.save}\n\n{label}: {'ALL PASSED' if not fails else f'{len(fails)} FAILED'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
