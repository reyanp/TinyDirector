"""Load a VERIFIED lesson build into the running Blender scene (worker side, {"kind": "lesson"} requests).

No code from the builder agent runs here: the lesson was built and verified in a separate headless Blender
(lesson_runner.py); this only APPENDS its data (the TD_Lesson collection: meshes, curves, text, camera, lights,
animation) from lesson.blend, then sets the camera, timeline and render look. The .blend must live inside a
lesson cache folder of this repo / shared dir and be named lesson.blend.
The result carries a "lesson" read-back (toolkit.readback) so the director can re-verify what is now on screen.
"""

from __future__ import annotations

from pathlib import Path

import bpy

from . import circuit, engine, toolkit


def _allowed(path: Path, roots: list[Path]) -> bool:
    if path.name != "lesson.blend" or not path.is_file():
        return False
    for root in roots:
        cache = (root / "state" / "lesson_cache").resolve()
        if cache in path.parents:
            return True
    return False


def clear() -> None:
    toolkit.clear()
    col = bpy.data.collections.get(toolkit.COLLECTION)
    if col is not None and not col.objects and not col.children:
        bpy.data.collections.remove(col)
    sc = bpy.context.scene
    if "td_lesson" in sc:
        del sc["td_lesson"]


def load(req: dict, shared_roots: list[Path]) -> tuple[int, list[str]]:
    path = Path(str(req.get("blend", ""))).resolve()
    if not _allowed(path, shared_roots):
        return 0, [f"refusing to load {path}: only <shared>/state/lesson_cache/*/lesson.blend is allowed"]
    circuit.clear()
    clear()
    with bpy.data.libraries.load(str(path), link=False) as (src, dst):
        if toolkit.COLLECTION not in src.collections:
            return 0, [f"{path} has no {toolkit.COLLECTION} collection"]
        dst.collections = [toolkit.COLLECTION]
    col = dst.collections[0]
    col.name = toolkit.COLLECTION
    sc = bpy.context.scene
    if col.name not in sc.collection.children:
        sc.collection.children.link(col)
    cams = [o for o in col.objects if o.type == "CAMERA"]
    if not cams:
        return 0, ["the lesson has no camera"]
    sc.camera = cams[0]
    frames = req.get("frames") or [1, 600]
    sc.frame_start, sc.frame_end = int(frames[0]), int(frames[1])
    sc.render.fps, sc.render.resolution_x, sc.render.resolution_y = 24, 1280, 720
    try:
        sc.render.engine = "BLENDER_EEVEE"
    except TypeError:
        sc.render.engine = "BLENDER_EEVEE_NEXT"
    sc.view_settings.view_transform = "AgX"
    circuit._world()
    circuit._glare(sc)
    sun = bpy.data.objects.get("TD_Sun")  # same convention as the circuit lesson: studio lights only
    if sun is not None and not sun.hide_render:
        sun.hide_render = sun.hide_viewport = True
        sun["td_hidden_by_circuit"] = True
    sc["td_lesson"] = str(req.get("title", ""))
    sc.frame_set(sc.frame_end)
    return len(col.objects), []


def make_result(request_id: str, req: dict, shared_roots: list[Path]) -> dict:
    applied, errors = load(req, shared_roots)
    return {"schema_version": 1, "request_id": request_id, "status": "error" if errors else "ok",
            "applied": applied, "errors": errors, "scene_version": engine.scene_version(),
            "scene_objects": engine.read_scene(), "scene": engine.read_scene_info(),
            "executor": f"blender {bpy.app.version_string}",
            "lesson": {"ok": True, **toolkit.readback()} if not errors else None,
            "lesson_action": "load"}
