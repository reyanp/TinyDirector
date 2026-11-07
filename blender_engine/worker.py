"""TinyDirector Blender worker. Polls shared/inbox, applies plans, writes shared/outbox.

Start it (Windows):   powershell -ExecutionPolicy Bypass -File blender_engine\\run_blender.ps1
Or by hand:           blender --python blender_engine/worker.py
Or inside an open Blender: Scripting tab -> Open this file -> Run Script.
Options after `--`:   --keep         keep TinyDirector objects from a previous session
                      --once         process the inbox once and quit (headless tests)
                      --loop         headless (-b) only: block and poll the inbox until killed
                      --save PATH    save a copy of the .blend after every job (e.g. a demo file)
Inbox files with {"kind": "inspect"} are read-only scene inspections: nothing is applied.
Env TINYDIRECTOR_ROOT overrides the repo root; TD_SHARED_DIR overrides the inbox/outbox/state folder.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path


def _repo_root() -> Path:
    if os.environ.get("TINYDIRECTOR_ROOT"):
        return Path(os.environ["TINYDIRECTOR_ROOT"]).resolve()
    candidates = []
    try:
        candidates.append(Path(__file__).resolve().parent.parent)
    except NameError:
        pass
    import bpy
    for text in bpy.data.texts:  # run from Blender's text editor
        if text.filepath and text.filepath.endswith("worker.py"):
            candidates.append(Path(bpy.path.abspath(text.filepath)).resolve().parent.parent)
    if bpy.data.filepath:
        candidates.append(Path(bpy.data.filepath).resolve().parent)
    for c in candidates:
        if (c / "shared" / "schema").is_dir():
            return c
    raise RuntimeError("Can't find the repo root (folder with shared/schema). Set TINYDIRECTOR_ROOT.")


ROOT = _repo_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bpy  # noqa: E402

from blender_engine import engine, snapshot  # noqa: E402

SNAPSHOTS = os.environ.get("TD_SNAPSHOT", "1") != "0"
SHARED = Path(os.environ["TD_SHARED_DIR"]).resolve() if os.environ.get("TD_SHARED_DIR") else ROOT / "shared"
INBOX = SHARED / "inbox"
OUTBOX = SHARED / "outbox"
STATE = SHARED / "state"
POLL_SECONDS = 0.25


def _args() -> list[str]:
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _redraw() -> None:
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    except Exception:
        pass


def _snapshot() -> dict:
    return engine.inspect("blender_startup")


def _save_path() -> str | None:
    args = _args()
    if "--save" in args and args.index("--save") + 1 < len(args):
        return str(Path(args[args.index("--save") + 1]).resolve())
    return None


def process_inbox() -> int:
    handled = 0
    for job in sorted(INBOX.glob("*.json")):
        if job.name.startswith("."):
            continue
        rid = job.stem
        try:
            plan = json.loads(job.read_text(encoding="utf-8"))
            if isinstance(plan, dict) and plan.get("kind") == "inspect":
                _write_json(OUTBOX / f"{rid}.json", engine.inspect(rid))
                job.unlink(missing_ok=True)
                handled += 1
                continue
            result = engine.make_result(rid, plan)
        except json.JSONDecodeError:
            continue  # partially written by a non-atomic writer; try next tick
        except Exception as exc:
            traceback.print_exc()
            result = {"schema_version": 1, "request_id": rid, "status": "error", "applied": 0,
                      "errors": [f"{type(exc).__name__}: {exc}"], "scene_version": engine.scene_version(),
                      "scene_objects": engine.read_scene()}
        if SNAPSHOTS and result.get("applied"):
            # render BEFORE writing the result so the image exists when the director sees the JSON
            try:
                t0 = time.perf_counter()
                snapshot.render(OUTBOX / f"{rid}.jpg")
                result["snapshot"] = f"{rid}.jpg"
                result["snapshot_ms"] = int((time.perf_counter() - t0) * 1000)
            except Exception as exc:
                print(f"[TinyDirector] snapshot failed: {exc}")
        _write_json(OUTBOX / f"{rid}.json", result)
        _write_json(STATE / "blender_manifest.json", result)
        save = _save_path()
        if save and result.get("applied"):
            try:
                bpy.ops.wm.save_as_mainfile(filepath=save, copy=True, compress=True)
            except Exception as exc:
                print(f"[TinyDirector] save failed: {exc}")
        try:
            job.unlink()
        except FileNotFoundError:
            pass
        print(f"[TinyDirector] {rid}: {result['status']} applied={result['applied']} "
              f"frames={result.get('scene', {}).get('frame_start')}-{result.get('scene', {}).get('frame_end')} "
              f"scene_version={result['scene_version']} objects={[o['id'] for o in result['scene_objects']]}"
              + (f" errors={result['errors']}" if result["errors"] else ""))
        handled += 1
    if handled:
        _redraw()
    return handled


def _tick():
    try:
        process_inbox()
    except Exception:
        traceback.print_exc()
    return POLL_SECONDS  # keep the timer alive


def setup_scene() -> None:
    scene = bpy.context.scene
    default_cube = bpy.data.objects.get("Cube")
    if default_cube is not None and "director_id" not in default_cube:
        bpy.data.objects.remove(default_cube, do_unlink=True)
    if not any(o.type == "LIGHT" and o.data.type == "SUN" for o in scene.objects):
        sun = bpy.data.objects.new("TD_Sun", bpy.data.lights.new("TD_Sun", "SUN"))
        sun.data.energy = 3.0
        sun.rotation_euler = (0.7, 0.2, 0.6)
        scene.collection.objects.link(sun)
    try:
        for area in bpy.context.screen.areas:
            if area.type == "VIEW_3D":
                area.spaces.active.shading.type = "MATERIAL"
    except Exception:
        pass


def start() -> None:
    args = _args()
    INBOX.mkdir(parents=True, exist_ok=True)
    OUTBOX.mkdir(parents=True, exist_ok=True)
    if "--keep" not in args:
        engine.reset_scene()
    setup_scene()
    _write_json(STATE / "blender_manifest.json", _snapshot())
    if "--once" in args:
        process_inbox()
        return
    if "--loop" in args:  # background mode has no event loop for timers, so poll in place
        print(f"[TinyDirector] Blender {bpy.app.version_string} headless worker looping. Watching {INBOX}", flush=True)
        while True:
            try:
                process_inbox()
            except Exception:
                traceback.print_exc()
            sys.stdout.flush()
            time.sleep(POLL_SECONDS)
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    bpy.app.timers.register(_tick, first_interval=0.5, persistent=True)
    print(f"[TinyDirector] Blender {bpy.app.version_string} worker running. Watching {INBOX}")


start()
