"""Render a small image of the TinyDirector objects from an auto-framed camera.

Used by the worker after every job so Nemotron (a vision-language model) can look at
what Blender actually produced. Does not touch the user's viewport or camera.
"""

from __future__ import annotations

import math
from pathlib import Path

import bpy
from mathutils import Vector

CAM_NAME = "TD_SnapCam"


def _bounds():
    pts = []
    for obj in bpy.data.objects:
        if "director_id" in obj and obj.get("director_asset") != "ground":
            pts += [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    if not pts:
        return Vector((0, 0, 0.5)), 2.0
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return (lo + hi) / 2, max((hi - lo).length / 2, 0.75)


def _camera():
    cam = bpy.data.objects.get(CAM_NAME)
    if cam is None:
        cam = bpy.data.objects.new(CAM_NAME, bpy.data.cameras.new(CAM_NAME))
        bpy.context.scene.collection.objects.link(cam)
        cam.hide_viewport = True
    cam.data.lens = 35
    return cam


def render(path: str | Path, width: int = 640, height: int = 427) -> str:
    scene = bpy.context.scene
    center, radius = _bounds()
    cam = _camera()
    direction = Vector((1.0, -1.25, 0.95)).normalized()
    fov = 2 * math.atan(cam.data.sensor_width / (2 * cam.data.lens))
    cam.location = center + direction * (radius / math.sin(fov / 2) * 1.05)
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()

    saved = (scene.camera, scene.render.engine, scene.render.filepath, scene.render.resolution_x,
             scene.render.resolution_y, scene.render.resolution_percentage, scene.render.image_settings.file_format, scene.render.image_settings.quality)
    scene.camera = cam
    scene.render.resolution_x, scene.render.resolution_y = width, height
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "JPEG"
    scene.render.image_settings.quality = 85
    scene.render.filepath = str(path)
    try:
        scene.render.engine = "BLENDER_WORKBENCH"
        scene.display.shading.light = "STUDIO"
        scene.display.shading.color_type = "MATERIAL"
        scene.display.shading.show_shadows = True
        bpy.ops.render.render(write_still=True)
    except Exception:
        # no GPU/OpenGL (headless servers): fall back to a quick CPU Cycles render
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = 8
        bpy.ops.render.render(write_still=True)
    finally:
        (scene.camera, scene.render.engine, scene.render.filepath, scene.render.resolution_x,
         scene.render.resolution_y, scene.render.resolution_percentage,
         scene.render.image_settings.file_format, scene.render.image_settings.quality) = saved
    return str(path)
