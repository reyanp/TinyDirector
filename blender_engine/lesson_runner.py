"""Sandboxed lesson build: runs builder-agent code in THIS (separate, headless) Blender process only.

  blender -b --factory-startup --python blender_engine/lesson_runner.py -- --code CODE.py --out DIR [--no-render]

Never run inside the user's Blender. The director starts this as a subprocess with a timeout. Steps:
allowlist check (sandbox.py) -> execute with only the toolkit API in scope -> toolkit.finish() ->
read back from Blender data -> DIR/readback.json, DIR/preview.png (last frame), DIR/lesson.blend.
On any failure DIR/readback.json is {"ok": false, "error": ...} and the exit code is 1.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def _arg(name):
    return argv[argv.index(name) + 1] if name in argv else None


def main() -> int:
    import bpy
    out = Path(_arg("--out"))
    out.mkdir(parents=True, exist_ok=True)
    code = Path(_arg("--code")).read_text(encoding="utf-8")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    from blender_engine import sandbox, toolkit
    t0 = time.time()
    try:
        sandbox.run(code, toolkit.API)
        toolkit.finish()
        rb = {"ok": True, **toolkit.readback(), "build_s": round(time.time() - t0, 2)}
    except Exception as exc:  # report to the director; it decides repair / fallback
        tb = traceback.format_exc(limit=3)
        line = ""
        for frame in traceback.extract_tb(exc.__traceback__):
            if frame.filename == "<builder>":
                line = f" (builder code line {frame.lineno})"
        rb = {"ok": False, "error": f"{type(exc).__name__}: {exc}{line}", "trace": tb[-1500:]}
        (out / "readback.json").write_text(json.dumps(rb, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"[lesson_runner] FAILED {rb['error']}")
        return 1
    (out / "readback.json").write_text(json.dumps(rb, indent=1, ensure_ascii=False), encoding="utf-8")
    sc = bpy.context.scene
    if "--no-render" not in argv:
        sc.frame_set(sc.frame_end)
        sc.render.filepath = str(out / "preview.png")
        sc.render.image_settings.file_format = "PNG"
        try:
            sc.eevee.taa_render_samples = 32
        except AttributeError:
            pass
        bpy.ops.render.render(write_still=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(out / "lesson.blend"), compress=True)
    print(f"[lesson_runner] ok in {time.time() - t0:.1f}s -> {out}")
    return 0


sys.exit(main())
