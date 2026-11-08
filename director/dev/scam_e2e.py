"""Spot the Scam, end to end, through the same HTTP API the Poyo window uses.

  python -m director.dev.scam_e2e           # live Nemotron + real Blender (headless worker, private bridge)
  python -m director.dev.scam_e2e --mock    # MOCK model + real Blender

Nemotron chooses every tool from the learner's words and writes every explanation (checked: numbers only from
FACTS, known misconceptions and lesson safety rules rejected, 1 retry, then a labeled template). The script plays
the browser's part: it sends each reply cue's camera focus + caption to /api/direct and checks Blender's
read-back after every step.

  1 "Poyo, teach me how to recognize scam texts."   -> phone with the FICTIONAL text; Poyo asks the question
  2 "What makes that message suspicious?"           -> heuristic warning-sign tags; camera walks the message
  3 "What if I follow the link?"                    -> both hypothetical outcomes, A (follow the link) chosen
  4 "What should I do instead?"                     -> B (verify independently) chosen; report to 7726
  5 "What if I already typed my password?"          -> recovery steps, no scene change
  6 "Teach me why LEDs need resistors."             -> the electronics lesson replaces the phone

Private bridge folder (shared/state/scam_run), never an open Blender. Outputs in experiments/out/scam_e2e/:
a JSON record, Blender's renders of every camera shot (TD_DIRECT_SNAPSHOT=1), the .blend with both outcomes.
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

RUN_DIR = Path(__file__).resolve().parents[2] / "shared" / "state" / "scam_run"
os.environ["TD_SHARED_DIR"] = str(RUN_DIR)

from .. import bridge, paths  # noqa: E402
from ..nemotron_client import ConfigError  # noqa: E402
from ..scam import scenario  # noqa: E402
from ..scam.lesson import SIDE  # noqa: E402
from ..server import make_session, serve  # noqa: E402
from .whatif_e2e import _http  # noqa: E402

TURNS = ["Poyo, teach me how to recognize scam texts.",
         "What makes that message suspicious?",
         "What if I follow the link?",
         "What should I do instead?",
         "What if I already typed my password?",
         "Teach me why LEDs need resistors."]
WANT = [("teach_scam_texts", {}), ("scam_inspect", {}), ("scam_choose", {"choice": "follow_link"}),
        ("scam_choose", {"choice": "verify_official"}), ("scam_recover", {}), ("teach_led_resistor", None)]
OUT = paths.REPO_ROOT / "experiments" / "out" / "scam_e2e"


def _message(sc: dict) -> tuple:
    return sc.get("sender"), sc.get("segments"), {o["id"]: o["uid"] for o in sc.get("objects", [])
                                                  if o["id"] in ("phone_1", "sender", *(x["id"] for x in scenario.SEGMENTS))}


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
    print(f"== Spot the Scam end-to-end: {label} ==")
    blend = OUT / "scam_final.blend"
    log = open(paths.STATE / "blender.log", "w")
    worker = subprocess.Popen([args.blender, "-b", "--factory-startup", "--python",
                               str(paths.REPO_ROOT / "blender_engine" / "worker.py"), "--", "--loop", "--save",
                               str(blend)], stdout=log, stderr=subprocess.STDOUT,
                              env={**os.environ, "TD_SHARED_DIR": str(RUN_DIR), "TD_DIRECT_SNAPSHOT": "1"})
    httpd = serve(session, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    fails, record, shots = [], [], []

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
        message = None
        for i, text in enumerate(TURNS, 1):
            print(f"\n=== Step {i}: {text!r} ===", flush=True)
            before = bridge.inspect(timeout=60)
            t0 = time.time()
            code, res = _http(base, "/api/turn", {"text": text})
            check(code == 202, f"/api/turn accepted ({code})")
            while _http(base, "/api/state")[1]["busy"]:
                time.sleep(0.2)
            st = _http(base, "/api/state")[1]
            evs = [e for e in st["events"] if e["turn"] == res.get("turn")]
            turn_ev = [e for e in evs if e["stage"] == "turn"][-1]
            d = turn_ev["data"]
            plan = [e for e in evs if e["stage"] == "plan" and e["status"] == "ok"]
            pd = plan[-1]["data"] if plan else {}
            tool, targs = pd.get("tool"), pd.get("args")
            print(f"  {pd.get('model', '?')} chose {tool} {json.dumps(targs)} ({time.time() - t0:.1f}s)"
                  + (f" - {plan[-1]['detail']}" if plan and plan[-1]["detail"] else ""))
            for e in evs:
                if e["stage"] in ("knowledge", "scene", "calculate", "apply", "verify", "explain") and e["status"] != "start":
                    print(f"    [{e['stage']}] {e['title']}" + (f" - {e['detail'][:120]}" if e["detail"] else ""))
            print(f"  Poyo ({d.get('source')}): {d.get('reply')}")
            check(turn_ev["status"] == "ok" and d.get("code", 0) == 0, f"turn ok ({turn_ev['title']})")
            want_tool, want_args = WANT[i - 1]
            check(tool == want_tool and (want_args is None or targs == want_args),
                  f"{'Nemotron' if live else 'MOCK model'} picked {tool} {json.dumps(targs)} (expected {want_tool}"
                  + (f" {json.dumps(want_args)})" if want_args is not None else ")"))
            if live:
                check(pd.get("live") is True and d.get("source") in ("nemotron", "template"),
                      f"real Nemotron calls (plan live={pd.get('live')}, explanation source={d.get('source')})")
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
            sc = after.get("scam") or {}
            g = d.get("grounding") or {}
            record.append({"step": i, "text": text, "tool": tool, "args": targs, "reply": d.get("reply"),
                           "source": d.get("source"), "cues": cues, "camera_moves": moves,
                           "events": [{k: e[k] for k in ("stage", "status", "title", "detail")} for e in evs],
                           "grounding": g, "lesson": d.get("lesson"), "seconds": round(time.time() - t0, 1),
                           "scene_version": [before["scene_version"], after["scene_version"]], "scam": sc,
                           "direction": after.get("direction")})
            reply = d.get("reply", "")
            verify = [e for e in evs if e["stage"] == "verify"]
            if i != 5:
                check(verify and verify[-1]["status"] == "ok", f"director read-back: {verify[-1]['title'] if verify else '-'}")
            if 2 <= i <= 5:
                check(g.get("facts"), f"knowledge base facts given to Nemotron: {[f['id'] for f in g.get('facts', [])]}")
            if i == 1:
                message = _message(sc)
                check(sc.get("present") and sc.get("segments", {}).get("link") == scenario.LINK and not sc.get("tags"),
                      "the phone shows the fictional text, no tags yet")
                check("FICTIONAL" in (sc.get("banner") or ""), "FICTIONAL label on screen")
                check(reply.endswith(scenario.QUESTION), "Poyo asks: follow the link or verify through the official app?")
            elif i == 2:
                check(set(sc.get("tags") or {}) == {"sender", "brand", "body", "urgency", "link"},
                      f"warning-sign tags on {sorted(sc.get('tags') or {})}")
                foci = [m["got"] for m in moves]
                check(len(set(foci) & {"sender", "brand", "body", "urgency", "link"}) >= 4,
                      f"the camera walked the message parts: {foci}")
                check(_message(sc) == message, "the message is unchanged")
                check(any(w in reply.lower() for w in ("heuristic", "checklist", "rules", "not certain")),
                      "Poyo states the heuristic's limits")
            elif i in (3, 4):
                choice = "follow_link" if i == 3 else "verify_official"
                o = sc.get("outcomes") or {}
                check(o.get("active") and o.get("choice") == choice and o.get("marked") == [choice],
                      f"both outcomes on stage, {choice} marked YOUR CHOICE")
                check(moves and moves[0]["got"] == SIDE[choice], "camera opens on the chosen outcome")
                check(_message(sc) == message, "the message phone is unchanged")
                if i == 3:
                    check(any(w in reply.lower() for w in ("scammer", "fake", "look-alike", "lookalike")),
                          "Poyo explains the simulated credential risk")
                    if blend.exists():
                        shutil.copy(blend, OUT / "scam_outcomes.blend")
                else:
                    check("official" in reply.lower() and "7726" in reply, "actionable: official app + report to 7726")
            elif i == 5:
                check(after["scene_version"] == before["scene_version"], "explanation only: nothing sent to Blender")
                check("password" in reply.lower() and any(w in reply.lower() for w in ("change", "reset")),
                      "recovery steps: change the password ...")
            elif i == 6:
                check((after.get("circuit") or {}).get("present") and not sc.get("present"),
                      "the LED circuit replaced the phone")
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
    (OUT / "scam_e2e.json").write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nrecord: {OUT / 'scam_e2e.json'}\nshots:  {len(shots)} Blender renders in {OUT}\n"
          f"scene:  {OUT / 'scam_outcomes.blend'} (both outcomes on stage)")
    print(f"\n{label}: {'ALL PASSED' if not fails else f'{len(fails)} FAILED: {fails}'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
