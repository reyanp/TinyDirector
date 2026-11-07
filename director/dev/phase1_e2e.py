"""Phase 1 end-to-end: the three acceptance prompts, sequentially, in ONE real Blender scene.

Starts a headless Blender worker, sends each prompt through the normal director pipeline
(inspect live scene -> plan -> validate -> bridge -> Blender -> read back -> post-check),
then checks the final scene and replays the saved .blend frame by frame.

  python -m director.dev.phase1_e2e            # live Nemotron (needs NVIDIA_API_KEY)
  python -m director.dev.phase1_e2e --mock     # offline rule-based model, labeled MOCK
  options: --blender PATH  --save docs/phase1_demo.blend  --no-snapshots

Exit code 0 only if every check passes.
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

# Private bridge folder, set BEFORE director.paths is imported, so this run can never send jobs
# to (or receive answers from) an interactive Blender that is watching shared/inbox.
RUN_DIR = Path(__file__).resolve().parents[2] / "shared" / "state" / "phase1_run"
os.environ["TD_SHARED_DIR"] = str(RUN_DIR)

from .. import bridge, paths  # noqa: E402
from ..cli import main as cli_main  # noqa: E402

PROMPTS = [
    "Create a red cube beside a cabin.",
    "Change the red cube to blue.",
    "Add a tree and make the cube bounce three times.",
]


def _is(hex_color: str, family: str) -> bool:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return {"red": r > 140 and r > g + 50 and r > b + 50, "blue": b > 140 and b > r + 50 and b > g + 30}[family]


def _count(objs, asset):
    return [o for o in objs.values() if o["asset"] == asset]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mock", action="store_true", help="offline rule-based model (NOT Nemotron)")
    ap.add_argument("--blender", default=shutil.which("blender") or "blender")
    ap.add_argument("--save", default=str(paths.REPO_ROOT / "docs" / "phase1_demo.blend"))
    ap.add_argument("--no-snapshots", action="store_true", help="skip the per-job render (and the visual check)")
    ap.add_argument("--timeout", type=float, default=60.0, help="seconds to wait for Blender per request")
    args = ap.parse_args()

    label = "MOCK model (NOT Nemotron)" if args.mock else "LIVE Nemotron"
    print(f"== Phase 1 end-to-end: {label} + real Blender ({args.blender}) ==")
    shutil.rmtree(RUN_DIR, ignore_errors=True)  # fresh private inbox/outbox/state every run
    for d in (paths.INBOX, paths.OUTBOX, paths.STATE):
        d.mkdir(parents=True, exist_ok=True)
    paths.LATEST_MANIFEST.unlink(missing_ok=True)
    print(f"private bridge folder: {RUN_DIR}")
    env = {**os.environ, "TINYDIRECTOR_ROOT": str(paths.REPO_ROOT), "TD_SHARED_DIR": str(RUN_DIR)}
    if args.no_snapshots:
        env["TD_SNAPSHOT"] = "0"
    save = os.path.abspath(args.save)
    os.makedirs(os.path.dirname(save), exist_ok=True)
    if os.path.exists(save):
        os.remove(save)
    log = open(paths.STATE / "phase1_blender.log", "w")
    worker = subprocess.Popen([args.blender, "-b", "--factory-startup", "--python",
                               str(paths.REPO_ROOT / "blender_engine" / "worker.py"), "--", "--loop", "--save", save],
                              stdout=log, stderr=subprocess.STDOUT, env=env)
    results, fails = [], []

    def check(cond, msg):
        print(("  PASS " if cond else "  FAIL ") + msg)
        if not cond:
            fails.append(msg)

    try:
        live = bridge.inspect(timeout=60)  # also waits for Blender to finish starting
        check(live["scene_objects"] == [], f"fresh Blender scene is empty ({live['executor']})")
        snapshots = []
        for i, text in enumerate(PROMPTS, 1):
            print(f"\n--- Test {i}: {text!r} ---", flush=True)
            argv = ["--send", "--text", text, "--timeout", str(args.timeout)] + (["--mock"] if args.mock else [])
            if args.no_snapshots:
                argv.append("--no-verify")
            code = cli_main(argv)
            state = bridge.inspect(timeout=30)
            results.append((code, state))
            snapshots += sorted(p.name for p in paths.OUTBOX.glob("*.jpg") if p.name not in snapshots)
            check(code == 0, f"director exit code {code} (0 = executed and post-check passed)")

        print("\n--- Acceptance checks on Blender's read-back ---")
        s1, s2, s3 = ({o["id"]: o for o in st["scene_objects"]} for _, st in results)
        cubes1, cabins1 = _count(s1, "cube"), _count(s1, "cabin")
        check(len(cubes1) == 1 and _is(cubes1[0]["color"], "red"), f"T1 one red cube: {[(c['id'], c['color']) for c in cubes1]}")
        check(len(cabins1) == 1, f"T1 a cabin exists: {[c['id'] for c in cabins1]}")
        check(bool(s1) and all(o.get("name") == oid for oid, o in s1.items()), f"T1 identifiable names: {sorted(s1)}")
        check(all(abs(o["position"][2]) < 1e-3 for o in s1.values()), "T1 objects stand on the ground (z=0): "
              + ", ".join(f"{i} z={o['position'][2]}" for i, o in s1.items()))
        cube_id, cabin_id = (cubes1[0]["id"] if cubes1 else None), (cabins1[0]["id"] if cabins1 else None)
        cubes2 = _count(s2, "cube")
        check(len(cubes2) == 1 and cubes2[0]["id"] == cube_id and _is(cubes2[0]["color"], "blue"),
              f"T2 the same cube is now blue, no duplicate: {[(c['id'], c['color']) for c in cubes2]}")
        check(cabin_id in s2 and s2[cabin_id] == s1[cabin_id], "T2 cabin unchanged")
        trees3 = _count(s3, "tree")
        check(len(trees3) == 1 and len(s3) == len(s2) + 1, f"T3 one new tree, nothing else added: {sorted(s3)}")
        check(cabin_id in s3 and s3[cabin_id] == s1[cabin_id] and cube_id in s3
              and _is(s3[cube_id]["color"], "blue"), "T3 cube + cabin still there, cabin unchanged, cube still blue")
        zk = [a for a in (s3.get(cube_id, {}).get("animation") or []) if a["property"] == "position" and a["axis"] == "z"]
        check(bool(zk), f"T3 cube has Z keyframes: {zk[0]['keys'] if zk else None}")
        print(f"  timeline: {results[2][1].get('scene')}")
    finally:
        worker.terminate()
        try:
            worker.wait(10)
        except subprocess.TimeoutExpired:
            worker.kill()
        log.close()

    print(f"\n--- Replaying the saved file {save} ---")
    if not os.path.exists(save):
        check(False, "demo .blend was saved")
    else:
        out = subprocess.run([args.blender, "-b", "--factory-startup", save, "--python",
                              str(paths.REPO_ROOT / "blender_engine" / "inspect_blend.py")],
                             capture_output=True, text=True, timeout=120, env=env)
        line = next((ln for ln in out.stdout.splitlines() if ln.startswith("TD_INSPECT ")), None)
        if line is None:
            check(False, "inspect_blend.py ran: " + out.stdout[-500:] + out.stderr[-500:])
        else:
            data = json.loads(line[len("TD_INSPECT "):])
            cube = data["objects"].get(cube_id or "", {})
            z = cube.get("z_per_frame", [])
            base = z[0] if z else 0.0
            peak = max(z) if z else 0.0
            peaks = [i + data["frame_start"] for i in range(1, len(z) - 1)
                     if z[i] >= z[i - 1] and z[i] > z[i + 1] and z[i] > base + (peak - base) / 2]
            print(f"  frames {data['frame_start']}-{data['frame_end']}, cube z every 6th frame: {z[::6]}")
            check(len(peaks) == 3, f"file: three bounces during playback (peaks at frames {peaks})")
            check(bool(z) and abs(base) < 1e-3, f"file: bounce starts at ground level (z={base})")
            check(bool(z) and abs(z[-1] - base) < 1e-3 and peak - base > 0.2,
                  f"file: starts and ends at its starting height ({base} -> {z[-1] if z else None}), peak {peak}")
            check(bool(z) and max(abs(z[i + 1] - z[i]) for i in range(len(z) - 1)) < (peak - base) / 3,
                  "file: smooth motion, no single-frame jumps")
            check(sorted(data["objects"]) == sorted(s3), f"file holds the same objects: {sorted(data['objects'])}")
    if snapshots:
        print(f"\nSnapshots rendered by Blender: {', '.join(str(paths.OUTBOX / s) for s in snapshots)}")
    print(f"Blender log: {paths.STATE / 'phase1_blender.log'}")
    print(f"\n{label}: {'ALL PASSED' if not fails else f'{len(fails)} FAILED'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
