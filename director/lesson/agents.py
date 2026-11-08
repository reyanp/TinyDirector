"""Agent wiring shared by the lesson CLI and the tutor: which clients play director / builder / vision, and
loading a verified lesson into Blender (then re-verifying what Blender actually shows)."""

from __future__ import annotations

import os
import time

from .. import bridge, paths
from ..nemotron_client import ConfigError, NemotronClient
from ..events import noop
from . import pipeline, verify as verify_mod


def make_clients(mock: bool, builder: str, vision: bool, log=print):
    """(director, builder or None, vision or None). Raises ConfigError when a required key is missing."""
    if mock:
        from .mock import MockDirector
        log("[MOCK director - NOT a live Nemotron call]")
        director = MockDirector()
        vis = None
    else:
        director = NemotronClient(temperature=0.2)
        vis = NemotronClient(model=os.environ.get("NEMOTRON_VL_MODEL") or None, temperature=0.1) if vision else None
    if builder == "deterministic":
        return director, None, vis
    from .builder import OpenAIBuilderClient
    try:
        return director, OpenAIBuilderClient(), vis
    except ConfigError:
        if builder == "openai":
            raise
        if mock:
            from .mock import MockBuilder
            log("[MOCK builder - NOT a live OpenAI call]")
            return director, MockBuilder(), vis
        log("[builder] OPENAI_API_KEY is empty -> deterministic builder (no builder LLM)")
        return director, None, vis


def send_to_blender(lesson: pipeline.Lesson, timeout: float = 60.0, log=print, send=None, emit=noop) -> dict:
    """Load the verified build into the running Blender worker, then re-verify what Blender now shows."""
    rid = f"lesson-{time.strftime('%Y%m%d-%H%M%S')}-{lesson.key[-8:]}"
    rb = verify_mod.load_readback(lesson.out_dir)
    req = {"kind": "lesson", "request_id": rid, "blend": str(lesson.blend.resolve()),
           "title": lesson.resolved["title"], "frames": rb.get("frames") or [1, 600]}
    emit("load", "start", "Loading the verified lesson into Blender", "appending data only; no code runs there")
    result = (send or (lambda r: bridge.send(r, timeout=timeout)))(req)
    if result.get("status") != "ok":
        raise pipeline.LessonError(f"Blender could not load the lesson: {result.get('errors')}")
    live = result.get("lesson") or {"ok": False, "error": "no lesson read-back"}
    rep = verify_mod.Report()
    verify_mod.structural(lesson.resolved, lesson.results, live, rep)
    log(f"[blender] loaded {result.get('applied')} objects ({result.get('executor')}); re-check of the live scene: "
        f"{len(rep.passed)} passed, {len(rep.errors)} errors")
    for e in rep.errors[:5]:
        log(f"           - {e}")
    emit("load", "ok" if result.get("status") == "ok" else "error", f"Loaded {result.get('applied')} objects",
         result.get("executor", ""))
    emit("verify", "ok" if not rep.errors else "error",
         f"Live scene re-check: {len(rep.passed)} passed, {len(rep.errors)} errors", "; ".join(rep.errors[:2]))
    if rep.errors:
        raise pipeline.LessonError(f"live scene does not match the verified lesson: {rep.errors[:3]}")
    if result.get("snapshot"):
        log(f"[blender] snapshot {paths.OUTBOX / result['snapshot']}")
    return result
