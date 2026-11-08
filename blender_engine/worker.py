"""TinyDirector Blender worker. Polls shared/inbox, applies plans, writes shared/outbox.

Start it (Windows):   powershell -ExecutionPolicy Bypass -File blender_engine\\run_blender.ps1
Or by hand:           blender --python blender_engine/worker.py
Or inside an open Blender: Scripting tab -> Open this file -> Run Script.
Request kinds: plan (v1 ops), inspect, circuit, lesson, direct (camera / caption / callout), compare
(split-reality comparison beside the circuit: blender_engine/compare.py).
Options after `--`:   --keep         keep TinyDirector objects from a previous session
                      --once         process the inbox once and quit (headless tests)
                      --loop         headless (-b) only: block and poll the inbox until killed
                      --save PATH    save a copy of the .blend after every job (e.g. a demo file)
                      --library      use the asset library (same as TD_ASSET_LIBRARY=1; see assets/catalog.json)
                      --stage        GUI: make the 3D viewport the whole window (no panels, no overlays) and
                                     look through the lesson camera: Blender is the stage, Poyo sits beside it
                      --rendered     with --stage: EEVEE rendered viewport (glow) instead of Material Preview
Inbox files with {"kind": "inspect"} are read-only scene inspections: nothing is applied.
Inbox files with {"kind": "circuit"} drive the LED + resistor lesson (blender_engine/circuit.py,
shared/schema/circuit_request_v1.json); inspections then also carry a "circuit" read-back block.
Inbox files with {"kind": "lesson"} load a VERIFIED multi-agent lesson build (lesson.blend from the lesson
cache, appended as data by lesson_loader.py; no generated code runs in this Blender).
Inbox files with {"kind": "direct"} only direct the stage: camera focus, highlight halo, caption
(blender_engine/direction.py, shared/schema/direct_request_v1.json); lesson values never change.
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

from blender_engine import circuit, compare, direction, engine, explainer, lesson_loader, scam, snapshot  # noqa: E402

SNAPSHOTS = os.environ.get("TD_SNAPSHOT", "1") != "0"
DIRECT_SNAPSHOTS = os.environ.get("TD_DIRECT_SNAPSHOT", "0") == "1"  # e2e evidence: render camera moves too
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
    return _inspect("blender_startup")


def _inspect(rid: str) -> dict:
    result = engine.inspect(rid)
    lesson = circuit.read_back()
    if lesson is not None:
        result["circuit"] = lesson
        result["other_objects"] = circuit.other_objects()
    col = bpy.data.collections.get(lesson_loader.toolkit.COLLECTION)
    if col is not None and col.objects:
        result["lesson"] = {"ok": True, **lesson_loader.toolkit.readback()}
        result["other_objects"] = [o for o in result.get("other_objects", [])
                                   if not (bpy.data.objects.get(o["name"]) or {}).get("lesson_part")]
    if lesson is not None or "lesson" in result:
        result["direction"] = direction.read_back()
    if compare.active():
        result["compare"] = compare.read_back()
    if scam.present():
        result["scam"] = scam.read_back()
        result["direction"] = direction.read_back()
    if explainer.present():
        result["explainer"] = explainer.read_back()
        result["direction"] = direction.read_back()
    return result


def _stage(view) -> None:
    """--stage: the 3D viewport fills the window, clean (no panels, gizmos, grid), through the camera."""
    window, area = view
    space = area.spaces.active
    space.shading.type = "RENDERED" if "--rendered" in _args() else "MATERIAL"
    space.overlay.show_overlays = False
    space.show_gizmo = False
    for attr in ("show_region_header", "show_region_toolbar", "show_region_ui", "show_region_tool_header"):
        if hasattr(space, attr):
            setattr(space, attr, False)
    if not window.screen.show_fullscreen:
        with bpy.context.temp_override(window=window, area=area):
            bpy.ops.screen.screen_full_area(use_hide_panels=True)


def _fit_camera_frame():
    """--stage: the lesson camera's frame fills the viewport ("Frame Camera Bounds") and anything outside it is
    hidden (opaque passepartout), so the stage shows exactly the camera's composition with its HUD labels.
    Run from a timer: the full-window switch resizes the area only after a redraw."""
    try:
        cam = bpy.context.scene.camera
        if cam is not None:
            cam.data.show_passepartout = True
            cam.data.passepartout_alpha = 1.0
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type != "VIEW_3D":
                    continue
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                r3d = area.spaces.active.region_3d
                if region is None or r3d.view_perspective != "CAMERA":
                    continue
                with bpy.context.temp_override(window=window, area=area, region=region):
                    bpy.ops.view3d.view_center_camera()
    except Exception as exc:
        print(f"[TinyDirector] could not fit the camera frame: {exc}")
    return None


def _present_circuit(action: str) -> None:
    """GUI only: look through the lesson camera with materials on, and play the animation."""
    if bpy.app.background:
        return
    try:
        windows = list(bpy.context.window_manager.windows)
        view = None
        for window in windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.spaces.active.shading.type = "MATERIAL"
                    area.spaces.active.region_3d.view_perspective = "CAMERA"
                    view = view or (window, area)
        if view and "--stage" in _args():
            _stage(view)
            view = next(((w, a) for w in bpy.context.window_manager.windows for a in w.screen.areas
                         if a.type == "VIEW_3D"), view)
            view[1].spaces.active.region_3d.view_perspective = "CAMERA"
            for delay in (0.15, 0.6):  # once after the redraw, once more after the full-window resize settles
                bpy.app.timers.register(_fit_camera_frame, first_interval=delay)
        if action == "build":
            bpy.context.scene.frame_set(1)
        if view and not any(w.screen.is_animation_playing for w in windows):
            with bpy.context.temp_override(window=view[0], area=view[1]):
                bpy.ops.screen.animation_play()
    except Exception as exc:
        print(f"[TinyDirector] could not set up the lesson view: {exc}")


def _stage_startup():
    try:
        view = next(((w, a) for w in bpy.context.window_manager.windows for a in w.screen.areas
                     if a.type == "VIEW_3D"), None)
        if view is not None:
            _stage(view)
    except Exception as exc:
        print(f"[TinyDirector] could not set up the stage: {exc}")
    return None


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
                _write_json(OUTBOX / f"{rid}.json", _inspect(rid))
                job.unlink(missing_ok=True)
                handled += 1
                continue
            if isinstance(plan, dict) and plan.get("kind") == "direct":
                result = direction.make_result(rid, plan)  # display only: applied = 0, no snapshot
            elif isinstance(plan, dict) and plan.get("kind") == "scam":
                if plan.get("action") == "build":  # one lesson on screen at a time
                    compare.clear(move_camera=False)
                    lesson_loader.clear()
                result = scam.make_result(rid, plan)  # Spot the Scam phone, warning tags, hypothetical outcomes
            elif isinstance(plan, dict) and plan.get("kind") == "explainer":
                if plan.get("action") == "build":  # one lesson on screen at a time
                    compare.clear(move_camera=False)
                    lesson_loader.clear()
                result = explainer.make_result(rid, plan)  # OVERRIDE: a generated explainer scene (spec = data)
            elif isinstance(plan, dict) and plan.get("kind") == "compare":
                result = compare.make_result(rid, plan)  # split-reality comparison (show / clear)
            elif isinstance(plan, dict) and plan.get("kind") == "circuit":
                compare.clear(move_camera=plan.get("action") == "update")  # a lesson change ends a comparison
                if plan.get("action") == "build":
                    lesson_loader.clear()  # one lesson on screen at a time
                    direction.clear()
                result = circuit.make_result(rid, plan)
            elif isinstance(plan, dict) and plan.get("kind") == "lesson":
                compare.clear(move_camera=False)
                direction.clear()
                result = lesson_loader.make_result(rid, plan, [SHARED, ROOT / "shared"])
            else:
                result = engine.make_result(rid, plan)
        except json.JSONDecodeError:
            continue  # partially written by a non-atomic writer; try next tick
        except Exception as exc:
            traceback.print_exc()
            result = {"schema_version": 1, "request_id": rid, "status": "error", "applied": 0,
                      "errors": [f"{type(exc).__name__}: {exc}"], "scene_version": engine.scene_version(),
                      "scene_objects": engine.read_scene()}
        is_direct = isinstance(plan, dict) and plan.get("kind") == "direct"
        if SNAPSHOTS and (result.get("applied") or (DIRECT_SNAPSHOTS and is_direct and result.get("status") == "ok")):
            # render BEFORE writing the result so the image exists when the director sees the JSON
            try:
                t0 = time.perf_counter()
                if result.get("circuit_action") or "compare" in result or "scam" in result or "explainer" in result \
                        or is_direct:  # camera + EEVEE, powered on
                    circuit.render_preview(OUTBOX / f"{rid}.jpg", 960, 540, samples=16, file_format="JPEG")
                elif result.get("lesson_action"):
                    circuit.render_preview(OUTBOX / f"{rid}.jpg", 960, 540, frame=bpy.context.scene.frame_end,
                                           samples=16, file_format="JPEG")
                else:
                    snapshot.render(OUTBOX / f"{rid}.jpg")
                result["snapshot"] = f"{rid}.jpg"
                result["snapshot_ms"] = int((time.perf_counter() - t0) * 1000)
            except Exception as exc:
                print(f"[TinyDirector] snapshot failed: {exc}")
        _write_json(OUTBOX / f"{rid}.json", result)
        _write_json(STATE / "blender_manifest.json", result)
        if (result.get("circuit_action") or result.get("lesson_action")) and result.get("applied"):
            _present_circuit(result.get("circuit_action") or "build")
        elif isinstance(plan, dict) and plan.get("kind") in ("scam", "explainer") and plan.get("action") == "build" \
                and result.get("applied"):
            _present_circuit("build")
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
    if "--library" in args:
        os.environ["TD_ASSET_LIBRARY"] = "1"
    from blender_engine import library
    print(f"[TinyDirector] asset library: {'ON' if library.enabled() else 'off (procedural assets)'}")
    INBOX.mkdir(parents=True, exist_ok=True)
    OUTBOX.mkdir(parents=True, exist_ok=True)
    if "--keep" not in args:
        engine.reset_scene()
        circuit.clear()
        lesson_loader.clear()
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
    if "--stage" in args:  # clean, full-window viewport right away (the lesson camera arrives with a lesson)
        bpy.app.timers.register(_stage_startup, first_interval=0.3)
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    bpy.app.timers.register(_tick, first_interval=0.5, persistent=True)
    print(f"[TinyDirector] Blender {bpy.app.version_string} worker running. Watching {INBOX}")


start()
