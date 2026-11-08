"""OVERRIDE, end to end, through the same HTTP API the Poyo window uses.

  python -m director.dev.explainer_e2e           # live Nemotron + real Blender (headless worker, private bridge)
  python -m director.dev.explainer_e2e --mock    # MOCK model + real Blender

  1 "Poyo, override: how do SIM swaps work?"       -> Nemotron designs a lesson (validated, 1 retry); Blender builds
                                                      it; read-back checked; the camera follows the narration
  2 "Why does moving the phone number matter?"     -> Nemotron picks explain: answered from the lesson Blender stored
  3 "Explain that in simpler terms."               -> a follow-up: no greeting, no repeated sentence
  4 "Override: how does the water cycle work?"     -> a different generated lesson replaces it
  5 "Teach me why LEDs need resistors."            -> the calculated electronics lesson replaces it

Private bridge folder (shared/state/explainer_run), never an open Blender. Outputs in experiments/out/explainer_e2e/:
a JSON record, Blender's renders of every camera shot (TD_DIRECT_SNAPSHOT=1), the .blend of the first lesson.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

RUN_DIR = Path(__file__).resolve().parents[2] / "shared" / "state" / "explainer_run"
os.environ["TD_SHARED_DIR"] = str(RUN_DIR)

from .. import bridge, paths  # noqa: E402
from ..circuit.agent import continuity_check  # noqa: E402
from ..nemotron_client import ConfigError  # noqa: E402
from ..server import make_session, serve  # noqa: E402
from .whatif_e2e import _http  # noqa: E402

TURNS = ["Poyo, override: how do SIM swaps work?",
         "Why does moving the phone number matter so much?",
         "Explain that in simpler terms.",
         "Override: how does the water cycle work?",
         "Teach me why LEDs need resistors."]
WANT = ["teach_anything", "explain", "explain", "teach_anything", "teach_led_resistor"]
OUT = paths.REPO_ROOT / "experiments" / "out" / "explainer_e2e"


def main() -> int:
    paths.load_env()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--blender", default=os.environ.get("BLENDER") or shutil.which("blender") or "blender")
    args = ap.parse_args()
    shutil.rmtree(RUN_DIR, ignore_errors=True)
    for d in (paths.INBOX, paths.OUTBOX, paths.STATE):
        d.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        session = make_session(args.mock, False, 120.0, voice=False)
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}")
        return 2
    live = not args.mock
    label = f"{session.mode['model']} + real Blender (headless)"
    print(f"== OVERRIDE end-to-end: {label} ==")
    blend = OUT / "explainer_final.blend"
    log = open(paths.STATE / "blender.log", "w")
    worker = subprocess.Popen([args.blender, "-b", "--factory-startup", "--python",
                               str(paths.REPO_ROOT / "blender_engine" / "worker.py"), "--", "--loop", "--save",
                               str(blend)], stdout=log, stderr=subprocess.STDOUT,
                              env={**os.environ, "TD_SHARED_DIR": str(RUN_DIR), "TD_DIRECT_SNAPSHOT": "1"})
    httpd = serve(session, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    fails, record, shots, dialogue = [], [], [], []
    first_title = None

    def check(cond, msg):
        print(("  PASS " if cond else "  FAIL ") + msg, flush=True)
        if not cond:
            fails.append(msg)

    def keep(snapshot: str | None, name: str):
        if snapshot and (paths.OUTBOX / snapshot).exists():
            shutil.copy(paths.OUTBOX / snapshot, OUT / f"{name}.jpg")
            shots.append(f"{name}.jpg")

    try:
        bridge.inspect(timeout=90)
        for i, text in enumerate(TURNS, 1):
            print(f"\n=== Step {i}: {text!r} ===", flush=True)
            before = bridge.inspect(timeout=60)
            t0 = time.time()
            code, res = _http(base, "/api/turn", {"text": text})
            check(code == 202, f"/api/turn accepted ({code})")
            while _http(base, "/api/state")[1]["busy"]:
                time.sleep(0.3)
            st = _http(base, "/api/state")[1]
            evs = [e for e in st["events"] if e["turn"] == res.get("turn")]
            turn_ev = [e for e in evs if e["stage"] == "turn"][-1]
            d = turn_ev["data"]
            plan = [e for e in evs if e["stage"] == "plan" and e["status"] == "ok"]
            pd = plan[-1]["data"] if plan else {}
            tool = pd.get("tool")
            print(f"  {pd.get('model', '?')} → {tool} {json.dumps(pd.get('args'))} ({time.time() - t0:.1f}s)")
            for e in evs:
                if e["stage"] in ("knowledge", "director", "apply", "verify", "explain") and e["status"] != "start":
                    print(f"    [{e['stage']}] {e['title']}" + (f" - {e['detail'][:140]}" if e["detail"] else ""))
            reply = d.get("reply") or ""
            print(f"  Poyo ({d.get('source')}): {reply}")
            check(turn_ev["status"] == "ok" and d.get("code", 0) == 0, f"turn ok ({turn_ev['title']}: {turn_ev['detail']})")
            check(tool == WANT[i - 1], f"tool {tool} (expected {WANT[i - 1]})")
            if live and i != 5:
                check(d.get("source") == "nemotron", f"Nemotron's own words (source={d.get('source')})")
            if dialogue and live:  # the rule applies to model text (MOCK text is fixed rules)
                problems = continuity_check(reply, dialogue)
                check(not problems, f"continuing conversation: no greeting, no repeated sentence {problems}")
            dialogue.append({"learner": text, "poyo": reply})
            keep(d.get("snapshot"), f"step{i}_scene")
            cues = (d.get("stage") or {}).get("cues") or []
            moves, last = [], None
            for k, c in enumerate(cues):
                body = {"caption": c["caption"]} if c.get("caption") else {}
                if c.get("focus") and c["focus"] != last:
                    body["focus"] = last = c["focus"]
                if not body:
                    continue
                code, out = _http(base, "/api/direct", body, timeout=60)
                rb = out.get("direction") or {}
                if "focus" in body:
                    moves.append({"want": body["focus"], "got": rb.get("focus"), "code": code})
                    keep(out.get("snapshot"), f"step{i}_cue{k + 1}_{body['focus']}")
            if moves:
                print("  camera: " + " → ".join(m["got"] or "?" for m in moves))
            check(all(m["code"] == 200 and m["want"] == m["got"] for m in moves),
                  f"Blender's camera followed all {len(moves)} cue moves")
            after = bridge.inspect(timeout=60)
            ex = after.get("explainer") or {}
            verify = [e for e in evs if e["stage"] == "verify"]
            record.append({"step": i, "text": text, "tool": tool, "reply": reply, "source": d.get("source"),
                           "cues": cues, "camera_moves": moves, "lesson": d.get("lesson"),
                           "grounding": d.get("grounding"), "seconds": round(time.time() - t0, 1),
                           "events": [{k: e[k] for k in ("stage", "status", "title", "detail")} for e in evs],
                           "scene_version": [before["scene_version"], after["scene_version"]],
                           "explainer": {k: ex.get(k) for k in ("title", "banner", "legend", "layout", "elements",
                                                                "links", "overlaps", "spec")} if ex else None})
            if i in (1, 4):
                spec = ex.get("spec") or {}
                check(ex.get("present") and verify and verify[-1]["status"] == "ok",
                      f"generated lesson on stage; director read-back: {verify[-1]['title'] if verify else '-'}")
                check({k: v["label"] for k, v in (ex.get("elements") or {}).items()}
                      == {e["id"]: e["label"] for e in spec.get("elements", [])}, "every label exact in Blender")
                check(not ex.get("overlaps") and all(v["in_frame"] for v in (ex.get("elements") or {}).values()),
                      f"layout: in frame, no overlaps {ex.get('overlaps')}")
                check("not verified" in (ex.get("banner") or ""), "labeled not verified on screen")
                check((d.get("lesson") or {}).get("chips") == ["Generated (OVERRIDE)", "not verified"] or not live,
                      f"chips {(d.get('lesson') or {}).get('chips')}")
                check(len(moves) >= 2, f"the camera visited {len(moves)} narration stops")
                print(f"  design: {spec.get('title')!r} · {spec.get('layout')} · "
                      + ", ".join(f"{e['label']} ({e['shape']})" for e in spec.get("elements", [])))
                if i == 1:
                    first_title = spec.get("title")
                    if blend.exists():
                        shutil.copy(blend, OUT / "sim_swap.blend")
                else:
                    check(spec.get("title") != first_title, "a different lesson replaced the first one")
            elif i in (2, 3):
                check(after["scene_version"] == before["scene_version"], "explanation only: the scene is unchanged")
                check((d.get("stage") or {}).get("kind") == "explainer", "camera cues for the generated lesson")
            elif i == 5:
                check((after.get("circuit") or {}).get("present") and not ex.get("present"),
                      "the LED circuit replaced the generated lesson")
                check((after.get("circuit") or {}).get("labels", {}).get("label_current") == "I = 21.2 mA",
                      "electronics lesson: I = 21.2 mA")
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.terminate()
        try:
            worker.wait(15)
        except subprocess.TimeoutExpired:
            worker.kill()
        log.close()
    (OUT / "explainer_e2e.json").write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nrecord: {OUT / 'explainer_e2e.json'}\nshots:  {len(shots)} Blender renders in {OUT}")
    print(f"\n{label}: {'ALL PASSED' if not fails else f'{len(fails)} FAILED: {fails}'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
