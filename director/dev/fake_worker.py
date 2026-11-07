"""Stand-in for the Blender worker: watches shared/inbox, applies plans with the
pure-Python simulator, writes shared/outbox/<id>.json. Results are marked
"executor": "simulated" so they are never mistaken for real Blender output.

  python -m director.dev.fake_worker            # run until Ctrl+C
  python -m director.dev.fake_worker --once     # process what's waiting, then exit
  python -m director.dev.fake_worker --reset    # clear the simulated scene first
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .. import paths
from ..bridge import atomic_write_json
from ..engine_sim import inspect, make_result
from ..schema import validate_plan

SIM_STATE = paths.STATE / "fake_worker_scene.json"



def process_once(inbox: Path = paths.INBOX, outbox: Path = paths.OUTBOX, state_path: Path = SIM_STATE) -> int:
    handled = 0
    for job in sorted(inbox.glob("*.json")):
        state = json.loads(state_path.read_text()) if state_path.exists() else {"scene_version": 0, "scene_objects": []}
        rid = job.stem
        try:
            plan = json.loads(job.read_text(encoding="utf-8"))
            if isinstance(plan, dict) and plan.get("kind") == "inspect":
                atomic_write_json(outbox / f"{rid}.json", inspect(rid, state))
                job.unlink()
                handled += 1
                continue
        except json.JSONDecodeError as exc:
            result = {"schema_version": 1, "request_id": rid, "status": "error", "applied": 0,
                      "errors": [f"bad JSON: {exc}"], "scene_version": state["scene_version"],
                      "scene_objects": state["scene_objects"], "executor": "simulated"}
        else:
            # Defense in depth: the executor re-validates against its own current scene.
            errs = validate_plan(plan, {"scene_objects": state["scene_objects"]})
            if errs:
                result = {"schema_version": 1, "request_id": rid, "status": "error", "applied": 0, "errors": errs,
                          "scene_version": state["scene_version"], "scene_objects": state["scene_objects"],
                          "executor": "simulated"}
            else:
                result = make_result(rid, state["scene_version"], state["scene_objects"], plan, state.get("scene"))
        atomic_write_json(state_path, {"scene_version": result["scene_version"], "scene_objects": result["scene_objects"],
                                       **({"scene": result["scene"]} if "scene" in result else {})})
        atomic_write_json(outbox / f"{rid}.json", result)
        job.unlink()
        print(f"[fake_worker] {rid}: {result['status']} applied={result['applied']} "
              f"scene_version={result['scene_version']} objects={[o['id'] for o in result['scene_objects']]}")
        handled += 1
    return handled


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    paths.INBOX.mkdir(parents=True, exist_ok=True)
    paths.OUTBOX.mkdir(parents=True, exist_ok=True)
    if args.reset:
        atomic_write_json(SIM_STATE, {"scene_version": 0, "scene_objects": []})
        paths.LATEST_MANIFEST.unlink(missing_ok=True)
        print("[fake_worker] simulated scene reset")
    if args.once:
        process_once()
        return
    print(f"[fake_worker] SIMULATED Blender worker watching {paths.INBOX} (Ctrl+C to stop)")
    try:
        while True:
            process_once()
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
