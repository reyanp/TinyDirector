"""Multi-agent lesson pipeline: question -> director spec -> builder code -> sandboxed Blender -> verifier.

    director (Nemotron)   writes lesson_spec_v1          validated + linted, 1 retry
    calculators           fill every number              the only source of numbers
    builder (OpenAI)      writes toolkit code            sandbox allowlist checked director-side
    lesson_runner         builds it in a SEPARATE headless Blender (timeout) -> readback.json, preview, .blend
    verifier              structural + layout from Blender's read-back, + Nemotron vision on the render
    repair                ONE builder repair turn with the verifier's errors
    fallback              deterministic builder (labeled), structural checks must still pass

Results are cached by question in shared/state/lesson_cache/<key>/ (spec, code, readback, report, png, blend).
Only a verified lesson.blend is ever loaded into the user's Blender (as data, by the worker).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from blender_engine import sandbox

from .. import paths
from . import builder as builder_mod
from . import director_agent, fallback, verify as verify_mod
from .spec import resolve

RUNNER = paths.REPO_ROOT / "blender_engine" / "lesson_runner.py"
CACHE = paths.STATE / "lesson_cache"


class LessonError(RuntimeError):
    pass


@dataclass
class Attempt:
    builder: str  # "openai:<model>" / "deterministic" / "MOCK builder"
    code_path: str
    ok: bool
    summary: str
    errors: list[str] = field(default_factory=list)
    seconds: float = 0.0


@dataclass
class Lesson:
    question: str
    ok: bool
    key: str
    out_dir: Path
    spec: dict | None = None
    resolved: dict | None = None
    results: dict | None = None
    report: verify_mod.Report | None = None
    builder: str = ""
    attempts: list[Attempt] = field(default_factory=list)
    unsupported: str = ""
    cached: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def blend(self) -> Path:
        return self.out_dir / "lesson.blend"

    @property
    def preview(self) -> Path:
        return self.out_dir / "preview.png"


def cache_key(question: str) -> str:
    norm = re.sub(r"[^a-z0-9 ]+", "", " ".join(question.lower().split()))
    slug = "-".join(norm.split()[:6])[:48] or "lesson"
    return f"{slug}-{hashlib.sha1(norm.encode()).hexdigest()[:8]}"


def blender_binary() -> str:
    exe = os.environ.get("BLENDER") or shutil.which("blender")
    if not exe:
        raise LessonError("Blender not found: put it on PATH or set BLENDER=/path/to/blender")
    return exe


def run_blender(code_path: Path, out_dir: Path, timeout: float = 240, render: bool = True) -> dict:
    """Build in a separate headless Blender. Returns the read-back (ok False on any failure)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in ("readback.json", "preview.png", "lesson.blend"):
        (out_dir / stale).unlink(missing_ok=True)
    cmd = [blender_binary(), "-b", "--factory-startup", "--python", str(RUNNER), "--",
           "--code", str(code_path), "--out", str(out_dir)] + ([] if render else ["--no-render"])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=paths.REPO_ROOT)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"build timed out after {timeout:.0f} s"}
    rb = verify_mod.load_readback(out_dir)
    if rb.get("ok") and proc.returncode != 0:
        rb = {"ok": False, "error": f"runner exit {proc.returncode}: {proc.stderr[-400:]}"}
    if not rb.get("ok") and rb.get("error") == "no readback":
        rb["error"] = f"runner produced no read-back (exit {proc.returncode}): {(proc.stderr or proc.stdout)[-400:]}"
    return rb


def _save(path: Path, data) -> None:
    path.write_text(data if isinstance(data, str) else json.dumps(data, indent=1, ensure_ascii=False),
                    encoding="utf-8")


def _report_dict(rep: verify_mod.Report) -> dict:
    return {"ok": rep.ok, "summary": rep.summary(), "errors": rep.errors, "warnings": rep.warnings,
            "passed": rep.passed, "vision": rep.vision}


def load_cached(question: str, cache_dir: Path = CACHE) -> Lesson | None:
    key = cache_key(question)
    d = cache_dir / key
    meta = d / "lesson.json"
    if not meta.exists() or not (d / "lesson.blend").exists():
        return None
    m = json.loads(meta.read_text(encoding="utf-8"))
    if not m.get("ok"):
        return None
    rep = verify_mod.Report(errors=[], warnings=m["report"]["warnings"], passed=m["report"]["passed"],
                            vision=m["report"].get("vision"))
    return Lesson(question, True, key, d, spec=m["spec"], resolved=m["resolved"], results=m["results"], report=rep,
                  builder=m["builder"], cached=True, notes=m.get("notes", []))


def build_lesson(question: str, director_client, builder_client=None, vision_client=None, *,
                 use_cache: bool = True, cache_dir: Path = CACHE, runner=run_blender, log=print) -> Lesson:
    question = " ".join(question.split())
    if use_cache:
        hit = load_cached(question, cache_dir)
        if hit is not None:
            log(f"[lesson] cache hit {hit.out_dir}")
            return hit
    key = cache_key(question)
    out = cache_dir / key
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    lesson = Lesson(question, False, key, out)

    # 1. director: what to teach and show
    t0 = time.time()
    dlabel = f"{getattr(director_client, 'model', '?')}{'' if getattr(director_client, 'live', True) else ' (MOCK)'}"
    log(f"[lesson] director {dlabel}: writing the lesson spec ...")
    try:
        sr = director_agent.write_spec(question, director_client)
    except director_agent.SpecError as exc:
        _save(out / "spec_errors.json", {"errors": exc.errors, "raw": [r.content for r in exc.replies]})
        raise LessonError(f"director spec invalid after one retry: {exc.errors[:3]}") from None
    if sr.spec is None:
        lesson.unsupported = sr.unsupported
        log(f"[lesson] director: out of scope - {sr.unsupported}")
        return lesson
    lesson.spec = sr.spec
    lesson.resolved, lesson.results = resolve(sr.spec)
    _save(out / "spec.json", sr.spec)
    _save(out / "resolved_spec.json", {"spec": lesson.resolved, "results": lesson.results})
    log(f"[lesson] spec ok in {time.time() - t0:.1f}s: {lesson.resolved['title']!r}, "
        f"{len(sr.spec['parts'])} parts, {len(sr.spec['connections'])} wires, {len(sr.spec['flows'])} flows")
    for w in sr.warnings:
        log(f"           spec note: {w}")
    lesson.notes += [w for w in sr.warnings if w.startswith("typed value")]
    draft = fallback.code_for(lesson.resolved)
    _save(out / "code_fallback.py", draft)

    def attempt(name: str, code: str, tag: str, strict: bool) -> tuple[verify_mod.Report, dict]:
        code_path = out / f"code_{tag}.py"
        _save(code_path, code)
        bdir = out / f"build_{tag}"
        t = time.time()
        errs = sandbox.check(code, set(builder_mod.TOOLKIT_NAMES))
        if errs:
            rep, rb = verify_mod.Report(errors=[f"sandbox: {e}" for e in errs[:12]]), {"ok": False}
        else:
            rb = runner(code_path, bdir)
            img = bdir / "preview.png"
            rep = verify_mod.verify(lesson.resolved, lesson.results, rb, image=img if img.exists() else None,
                                    vision_client=vision_client, strict_layout=strict)
        _save(bdir.with_suffix(".report.json"), _report_dict(rep))
        lesson.attempts.append(Attempt(name, str(code_path), rep.ok, rep.summary(), rep.errors, time.time() - t))
        log(f"[lesson] {tag} ({name}): {rep.summary()} in {time.time() - t:.1f}s")
        for e in rep.errors[:6]:
            log(f"           - {e}")
        return rep, rb

    def accept(tag: str, name: str, rep: verify_mod.Report) -> Lesson:
        bdir = out / f"build_{tag}"
        for f in ("lesson.blend", "preview.png", "readback.json"):
            if (bdir / f).exists():
                shutil.copy2(bdir / f, out / f)
        lesson.ok, lesson.report, lesson.builder = True, rep, name
        _save(out / "lesson.json", {"ok": True, "question": question, "builder": name, "director": dlabel,
                                    "spec": lesson.spec, "resolved": lesson.resolved, "results": lesson.results,
                                    "report": _report_dict(rep), "attempts": [a.__dict__ for a in lesson.attempts],
                                    "notes": lesson.notes, "created": time.strftime("%Y-%m-%d %H:%M:%S")})
        log(f"[lesson] ACCEPTED ({name}) -> {out / 'lesson.blend'}")
        return lesson

    # 2. builder agent: write -> run -> verify -> one repair
    candidate = None  # builder scene that is structurally verified but still has layout issues
    if builder_client is not None:
        bname = f"openai:{builder_client.model}" if getattr(builder_client, "live", True) else "MOCK builder"
        log(f"[lesson] builder {bname}: writing scene code ...")
        try:
            msgs = builder_mod.first_messages(lesson.resolved, draft)
            code, errs, _ = builder_mod.ask(builder_client, msgs)
            rep, rb = attempt(bname, code, "agent1", True) if code else (verify_mod.Report(errors=errs), {"ok": False})
            if rep.ok:
                return accept("agent1", bname, rep)
            log("[lesson] builder repair (1 of 1) ...")
            code2, errs2, _ = builder_mod.ask(builder_client, builder_mod.repair_messages(msgs, code, rep.errors, rb))
            rep2, rb2 = attempt(bname, code2, "agent2", True) if code2 else (verify_mod.Report(errors=errs2), {})
            if rep2.ok:
                return accept("agent2", bname, rep2)
            if rb2.get("ok"):  # structurally right, only layout left? it may still beat the plain fallback
                img = out / "build_agent2" / "preview.png"
                lenient = verify_mod.verify(lesson.resolved, lesson.results, rb2, image=img if img.exists() else None,
                                            vision_client=vision_client, strict_layout=False)
                if lenient.ok:
                    candidate = lenient
            lesson.notes.append("builder agent did not pass the strict layout checks twice")
        except Exception as exc:  # noqa: BLE001 - any builder failure falls back, reported
            lesson.notes.append(f"builder agent unavailable: {type(exc).__name__}: {exc}"[:300])
            log(f"[lesson] builder failed: {type(exc).__name__}: {exc}"[:300])
    else:
        lesson.notes.append("no builder LLM configured (OPENAI_API_KEY empty): deterministic builder used")

    # 3. deterministic fallback: same sandbox + structural checks; layout issues are warnings
    rep, _ = attempt("deterministic", draft, "fallback", False)
    if candidate is not None and (not rep.ok or len(candidate.warnings) <= len(rep.warnings)):
        lesson.notes.append(f"kept the builder's repaired scene: structurally verified, {len(candidate.warnings)} "
                            f"layout warning(s) vs {len(rep.warnings)} for the deterministic layout")
        return accept("agent2", f"{bname} (with layout warnings)", candidate)
    if rep.ok:
        lesson.notes.append("deterministic fallback used")
        return accept("fallback", "deterministic", rep)
    lesson.report = rep
    _save(out / "lesson.json", {"ok": False, "question": question, "spec": lesson.spec, "report": _report_dict(rep),
                                "attempts": [a.__dict__ for a in lesson.attempts], "notes": lesson.notes})
    raise LessonError(f"no build passed verification: {rep.errors[:3]}")
