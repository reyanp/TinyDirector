"""Poyo's Cinematic What-If Lab, end to end, through the same HTTP API the Poyo window uses.

  python -m director.dev.whatif_e2e           # live Nemotron + real Blender (headless worker, private bridge)
  python -m director.dev.whatif_e2e --mock    # MOCK model + real Blender

Nemotron chooses every tool from the learner's words; nothing is scripted on the model side. The script plays the
browser's part: it sends each reply cue's camera focus + calculated callout to /api/direct and checks Blender's
read-back after every step.

  1 "Teach me why LEDs need resistors."                                -> build (330 Ω, 21.2 mA)
  2 "Poyo, show me what's happening inside this circuit."              -> tour: camera + halo + callout per stop
  3 "Compare what happens if I increase the resistor to 1,000 ohms."   -> split reality: what-if copy beside it
  4 "Why is the LED on the right dimmer?"                              -> explanation only, both stay on screen
  5 "Okay, go back to the original circuit."                           -> copy removed, original exactly as before

Private bridge folder (shared/state/whatif_run), never an open Blender. Outputs in experiments/out/whatif_e2e/:
a JSON record, Blender's renders of every camera shot (TD_DIRECT_SNAPSHOT=1), the .blend with the comparison on
stage and the final .blend.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

RUN_DIR = Path(__file__).resolve().parents[2] / "shared" / "state" / "whatif_run"
os.environ["TD_SHARED_DIR"] = str(RUN_DIR)

from .. import bridge, paths  # noqa: E402
from ..nemotron_client import ConfigError  # noqa: E402
from ..server import make_session, serve  # noqa: E402

TURNS = ["Teach me why LEDs need resistors.",
         "Poyo, show me what's happening inside this circuit.",
         "Compare what happens if I increase the resistor to 1,000 ohms.",
         "Why is the LED on the right dimmer?",
         "Okay, go back to the original circuit."]
WANT_TOOL = ["teach_led_resistor", "tour_circuit", "compare_resistance", "explain", "end_comparison"]
OUT = paths.REPO_ROOT / "experiments" / "out" / "whatif_e2e"


def _http(base: str, path: str, body: dict | None = None, timeout: float = 60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"},
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _values(c: dict) -> tuple:
    """A lesson's values, labels (not the subtitle) and animation, as Blender reads them back."""
    return (c.get("params"), {k: v for k, v in (c.get("labels") or {}).items() if k != "label_caption"},
            c.get("flow"), c.get("led"), {o["id"]: (o["uid"], o["position"]) for o in c.get("objects", [])})


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
    label = f"{session.mode['model']} + real Blender (headless)"
    print(f"== What-If Lab end-to-end: {label} ==")
    blend = OUT / "whatif_final.blend"
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
        lesson = None
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
            tool = (plan[-1]["data"].get("tool") if plan else None)
            print(f"  {plan[-1]['data'].get('model') if plan else '?'} chose {tool} "
                  f"{json.dumps(plan[-1]['data'].get('args')) if plan else ''} ({time.time() - t0:.1f}s)")
            for e in evs:
                if e["stage"] in ("scene", "calculate", "apply", "verify") and e["status"] != "start":
                    print(f"    [{e['stage']}] {e['title']}" + (f" - {e['detail'][:110]}" if e["detail"] else ""))
            print(f"  Poyo ({d.get('source')}): {d.get('reply')}")
            check(turn_ev["status"] == "ok" and d.get("code", 0) == 0, f"turn ok ({turn_ev['title']})")
            check(tool == WANT_TOOL[i - 1], f"{'Nemotron' if session.mode.get('live', not args.mock) else 'MOCK model'} picked {tool} (expected {WANT_TOOL[i - 1]})")
            keep(d.get("snapshot"), f"step{i}_scene")
            # the browser's part: each cue's camera move + callout, as Poyo speaks
            cues = (d.get("stage") or {}).get("cues") or []
            moves, last = [], None
            for k, c in enumerate(cues):
                body = {"caption": c["caption"]} if c.get("caption") else {}
                if c.get("focus") and c["focus"] != last:
                    body["focus"] = last = c["focus"]
                    if c.get("callout"):
                        body["callout"] = c["callout"]
                if not body:
                    continue
                code, out = _http(base, "/api/direct", body, timeout=60)
                rb = out.get("direction") or {}
                if "focus" in body:
                    moves.append({"want": body["focus"], "got": rb.get("focus"), "code": code,
                                  "callout_sent": body.get("callout"), "callout_read_back": rb.get("callout"),
                                  "halo": rb.get("halo"), "flow_emphasis": rb.get("flow_emphasis")})
                    keep(out.get("snapshot"), f"step{i}_cue{k + 1}_{body['focus']}")
            if moves:
                print("  camera: " + " → ".join(f"{m['got']}" + (f" [{m['callout_read_back']['value']}]"
                                                                   if m["callout_read_back"] else "") for m in moves))
            check(all(m["code"] == 200 and m["want"] == m["got"] and m["callout_sent"] == m["callout_read_back"]
                      for m in moves), f"Blender's camera + callout followed all {len(moves)} cue moves")
            after = bridge.inspect(timeout=60)
            circ, cmp = after.get("circuit") or {}, after.get("compare") or {}
            record.append({"step": i, "text": text, "tool": tool, "args": plan[-1]["data"].get("args") if plan else None,
                           "events": [{k: e[k] for k in ("stage", "status", "title", "detail")} for e in evs],
                           "reply": d.get("reply"), "source": d.get("source"), "cues": cues, "camera_moves": moves,
                           "seconds": round(time.time() - t0, 1), "compare": cmp, "direction": after.get("direction"),
                           "scene_version": [before["scene_version"], after["scene_version"]], "circuit": circ})
            reply = d.get("reply", "")
            if i == 1:
                lesson = _values(circ)
                check(circ.get("present") and (circ.get("params") or {}).get("resistance_ohm") == 330 and
                      (circ.get("labels") or {}).get("label_current") == "I = 21.2 mA", "built: R = 330 Ω, I = 21.2 mA")
            elif i == 2:
                stops = [m["got"] for m in moves]
                chosen = (plan[-1]["data"].get("args") or {}).get("stops", []) if plan else []
                chosen = [x for x in chosen if x != "overview"] + ["overview"]
                check(stops == chosen, f"the camera visited every stop Nemotron chose, in order: {stops}")
                values = {m["got"]: (m["callout_read_back"] or {}).get("value") for m in moves}
                want = {"battery": "9 V", "resistor": "7.0 V", "current": "21.2 mA", "led": "≈ 2 V",
                        "overview": "I = 21.2 mA"}
                check(all(values[s] == want[s] for s in values), f"callouts are the calculated values {values}")
                check(all(m["flow_emphasis"] == (m["got"] == "current") for m in moves),
                      "current arrows emphasised exactly while the camera is on the current")
                check(after["scene_version"] == before["scene_version"] and _values(circ) == lesson,
                      "the tour changed no lesson value, label, animation or object")
            elif i == 3:
                alt = cmp.get("alternative") or {}
                check(cmp.get("active") and (alt.get("params") or {}).get("resistance_ohm") == 1000,
                      "a what-if copy with 1,000 Ω is on stage")
                check((alt.get("labels") or {}).get("label_current") == "I = 7.0 mA",
                      f"what-if label {(alt.get('labels') or {}).get('label_current')!r}")
                check(cmp.get("headlines", {}).get("baseline", "").endswith("= 21.2 mA") and
                      cmp.get("headlines", {}).get("alternative", "").endswith("= 7.0 mA"),
                      "headlines: NOW … 21.2 mA | WHAT IF … 7.0 mA")
                laps = ((circ.get("flow") or {}).get("lap_frames"), (alt.get("flow") or {}).get("lap_frames"))
                check(all(laps) and abs(laps[1] / laps[0] - 21.2121 / 7.0) < 0.02,
                      f"what-if arrows {laps[1] / laps[0]:.2f}x slower (current ratio 3.03)" if all(laps) else "laps")
                check((alt.get("led") or {}).get("emission", 9) < (circ.get("led") or {}).get("emission_at_show", 0),
                      "what-if LED glow is dimmer (illustrative)")
                check(_values(circ) == lesson, "the original lesson objects and values are unchanged")
                verify = [e for e in evs if e["stage"] == "verify"]
                check(verify and verify[-1]["status"] == "ok", f"director read-back: {verify[-1]['title'] if verify else '-'}")
                check(re.search(r"\b21(\.2)?\b", reply) and re.search(r"\b7(\.0)?\b", reply),
                      "the explanation quotes both calculated currents")
                if blend.exists():
                    shutil.copy(blend, OUT / "whatif_compare.blend")
            elif i == 4:
                check(after["scene_version"] == before["scene_version"] and cmp.get("active"),
                      "explanation only: both circuits stay on screen, nothing sent")
            elif i == 5:
                check(not cmp.get("active"), "the what-if copy is gone")
                check(_values(circ) == lesson, "the original circuit is exactly as it was after step 1")
                check(lesson and "330 Ω" in reply and "21.2 mA" in reply, "reply quotes the original values")
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.terminate()
        try:
            worker.wait(15)
        except subprocess.TimeoutExpired:
            worker.kill()
        log.close()
    (OUT / "whatif_e2e.json").write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nrecord: {OUT / 'whatif_e2e.json'}\nshots:  {len(shots)} Blender renders in {OUT}\n"
          f"scenes: {OUT / 'whatif_compare.blend'} (comparison on stage), {blend} (after going back)")
    print(f"\n{label}: {'ALL PASSED' if not fails else f'{len(fails)} FAILED: {fails}'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
