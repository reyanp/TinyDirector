"""Local file bridge to the Blender worker (same machine, same checkout).

Protocol:
  director writes  shared/inbox/<request_id>.json   (via .tmp + atomic os.replace)
  Blender writes   shared/outbox/<request_id>.json  (should also use .tmp + os.replace)
  Blender must ignore *.tmp files in the inbox.
The result's scene_objects is the REAL scene, saved to shared/state/latest_manifest.json
and used for the next edit. Nothing here talks to GitHub or the network.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import paths
from .schema import validate_result


class BridgeTimeout(RuntimeError):
    pass


class BridgeResultError(RuntimeError):
    pass


def atomic_write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def submit(plan: dict, inbox: Path | None = None, outbox: Path | None = None) -> Path:
    inbox = inbox or paths.INBOX
    outbox = outbox or paths.OUTBOX
    rid = plan["request_id"]
    target = inbox / f"{rid}.json"
    if target.exists() or (outbox / f"{rid}.json").exists():
        raise FileExistsError(f"request_id {rid} already used; refusing to overwrite")
    atomic_write_json(target, plan)
    return target


def wait_for_result(request_id: str, timeout: float = 15.0, poll: float = 0.2,
                    outbox: Path | None = None, inbox: Path | None = None) -> dict:
    outbox = outbox or paths.OUTBOX
    inbox = inbox or paths.INBOX
    result_path = outbox / f"{request_id}.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result_path.exists():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                time.sleep(poll)  # writer may not be atomic; give it a moment
                continue
            errs = validate_result(result)
            if errs:
                raise BridgeResultError("Blender result does not match scene_result_v1: " + "; ".join(errs))
            if result.get("request_id") != request_id:
                raise BridgeResultError(f"result request_id {result.get('request_id')!r} != {request_id!r}")
            return result
        time.sleep(poll)
    still_waiting = (inbox / f"{request_id}.json").exists()
    raise BridgeTimeout(
        f"No result at {result_path} after {timeout:.0f}s.\n"
        + ("  The plan is still sitting in the inbox, so the worker never picked it up.\n" if still_waiting
           else "  The plan was picked up from the inbox but no result was written (check Blender's console).\n")
        + "  Check: (1) Blender is open with the TinyDirector worker/timer registered,\n"
          "         (2) scene.blend is saved at the repo root and the worker uses the SAME shared/ folder:\n"
          f"             {inbox.resolve()}\n"
          "         (3) director and Blender run on the same computer (GitHub does not sync runtime files),\n"
          "         (4) for a no-Blender test run: python -m director.dev.fake_worker")


def save_manifest(result: dict, path: Path | None = None) -> Path:
    path = path or paths.LATEST_MANIFEST
    atomic_write_json(path, result)
    return path


def load_manifest(path: Path | None = None) -> dict | None:
    path = Path(path) if path else paths.LATEST_MANIFEST
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def send(plan: dict, timeout: float = 15.0) -> dict:
    submit(plan)
    result = wait_for_result(plan["request_id"], timeout=timeout)
    save_manifest(result)
    return result
