"""Voice-driven circuit lesson, end to end, through the same HTTP API the Poyo window uses.

  python -m director.dev.voice_e2e           # live: OpenAI speech-to-text/text-to-speech + Nemotron + real Blender
  python -m director.dev.voice_e2e --mock    # offline: MOCK voice + MOCK model + real Blender (headless)

A microphone can't be scripted, so each spoken request is SYNTHESIZED SPEECH: OpenAI text-to-speech in a
different voice (live) or UTF-8 text through the MOCK voice. The audio is POSTed to /api/voice like the browser
does, then the script plays the browser's part: it fetches Poyo's spoken reply from /api/tts and sends each
cue's stage direction to /api/direct, checking Blender's read-back after each step.

  1 "Poyo, teach me why LEDs need resistors."   -> builds the circuit, camera cues
  2 "What if I make the resistor 1,000 ohms?"   -> updates the SAME objects, values + animation change
  3 "Why did it get dimmer?"                     -> explanation only, the scene is not touched

Private bridge folder (shared/state/voice_run), never an open Blender. Outputs in experiments/out/voice_e2e/:
the request/reply audio, a JSON record of every step, the saved .blend and a camera render of the final view.
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

RUN_DIR = Path(__file__).resolve().parents[2] / "shared" / "state" / "voice_run"
os.environ["TD_SHARED_DIR"] = str(RUN_DIR)

from .. import bridge, paths  # noqa: E402
from ..nemotron_client import ConfigError  # noqa: E402
from ..server import make_session, serve  # noqa: E402

UTTERANCES = [("Poyo, teach me why LEDs need resistors.", "teach me why leds need resistors"),
              ("What if I make the resistor 1,000 ohms?", "resistor 1,000 ohms"),
              ("Why did it get dimmer?", "why did it get dimmer")]
OUT = paths.REPO_ROOT / "experiments" / "out" / "voice_e2e"


def _http(base: str, path: str, data: bytes | None = None, ctype: str = "application/json", timeout: float = 60):
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": ctype},
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9, ]", "", s.lower().replace("1000", "1,000").replace("one thousand", "1,000"))


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
        session = make_session(args.mock, False, 120.0, voice=True)
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}")
        return 2
    if session.voice is None:
        print(f"CONFIG ERROR: voice unavailable: {session.mode['voice']['error']}")
        return 2
    v = session.mode["voice"]
    label = f"{session.mode['model']} + {v['provider']} ({v['stt']} / {v['tts']}) + real Blender (headless)"
    print(f"== voice end-to-end: {label} ==")
    print("   spoken requests are SYNTHESIZED speech (" + ("MOCK voice: UTF-8 text" if args.mock else
          "OpenAI text-to-speech, voice 'ash'") + "), standing in for a microphone")

    blend = OUT / "voice_lesson.blend"
    log = open(paths.STATE / "blender.log", "w")
    worker = subprocess.Popen([args.blender, "-b", "--factory-startup", "--python",
                               str(paths.REPO_ROOT / "blender_engine" / "worker.py"), "--", "--loop", "--save",
                               str(blend)], stdout=log, stderr=subprocess.STDOUT,
                              env={**os.environ, "TD_SHARED_DIR": str(RUN_DIR)})
    httpd = serve(session, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    fails, record = [], []

    def check(cond, msg):
        print(("  PASS " if cond else "  FAIL ") + msg, flush=True)
        if not cond:
            fails.append(msg)

    def speech_for(text: str) -> tuple[bytes, str]:
        if args.mock:
            return text.encode(), "audio/webm"
        out = session.voice._client.audio.speech.create(  # a different voice than Poyo's: the "student"
            model=session.voice.tts_model, voice="ash", input=text, response_format="mp3",
            instructions="A curious student asking a tutor robot a question, natural pace.")
        return out.read() if hasattr(out, "read") else out.content, "audio/mpeg"

    try:
        bridge.inspect(timeout=90)
        circuits = []
        for i, (spoken, expect) in enumerate(UTTERANCES, 1):
            print(f"\n=== Step {i}: (spoken) {spoken!r} ===", flush=True)
            audio, mime = speech_for(spoken)
            (OUT / f"step{i}_request.{'mp3' if mime == 'audio/mpeg' else 'txt'}").write_bytes(audio)
            before = bridge.inspect(timeout=60)
            t0 = time.time()
            code, body = _http(base, "/api/voice", audio, mime, timeout=120)
            t_stt = time.time() - t0
            res = json.loads(body or b"{}")
            check(code == 202, f"/api/voice accepted ({code}) in {t_stt:.1f}s; heard {res.get('heard')!r}")
            check(expect in _norm(res.get("text", "")), f"transcript (wake word removed) = {res.get('text')!r}")
            while json.loads(_http(base, "/api/state")[1])["busy"]:
                time.sleep(0.2)
            t_turn = time.time() - t0
            st = json.loads(_http(base, "/api/state")[1])
            turn_ev = [e for e in st["events"] if e["stage"] == "turn" and e["turn"] == res.get("turn")][-1]
            d = turn_ev["data"]
            tools = [e["title"] for e in st["events"] if e["turn"] == res.get("turn") and e["stage"] == "plan"]
            print(f"  turn {res.get('turn')}: {turn_ev['status']} in {t_turn:.1f}s after the audio arrived; "
                  f"plan: {tools[-1] if tools else '-'}")
            print(f"  Poyo: {d.get('reply')}")
            check(turn_ev["status"] == "ok" and d.get("code", 0) == 0, f"turn ok (source: {d.get('source')})")
            # the browser's part: Poyo's voice + cue-by-cue stage direction
            t1 = time.time()
            code, mp3 = _http(base, "/api/tts", json.dumps({"text": d.get("reply", "")}).encode(), timeout=120)
            check(code == 200 and len(mp3) > 1000 or args.mock and code == 200,
                  f"/api/tts spoke the reply ({len(mp3)} bytes, {time.time() - t1:.1f}s)")
            (OUT / f"step{i}_poyo.mp3").write_bytes(mp3)
            cues = (d.get("stage") or {}).get("cues") or []
            print("  cues: " + " | ".join(f"{c['focus'] or '·'}: {c['text'][:38]}" for c in cues))
            focuses, last = [], None
            for c in cues:
                body = {"caption": c["caption"]} if c.get("caption") else {}
                if c.get("focus") and c["focus"] != last:
                    body["focus"] = last = c["focus"]
                if body:
                    code, out = _http(base, "/api/direct", json.dumps(body).encode())
                    rb = json.loads(out).get("direction") or {}
                    if "focus" in body:
                        focuses.append((body["focus"], rb.get("focus"), code))
            check(all(code == 200 and want == got for want, got, code in focuses),
                  f"camera followed the cues: {[f[1] for f in focuses]}")
            after = bridge.inspect(timeout=60)
            circ = after.get("circuit") or {}
            circuits.append(circ)
            record.append({"step": i, "spoken": spoken, "heard": res.get("heard"), "text": res.get("text"),
                           "turn": turn_ev, "seconds_to_turn_done": round(t_turn, 1), "stt_seconds": round(t_stt, 1),
                           "focus_sequence": focuses, "direction": after.get("direction"),
                           "scene_version_before": before["scene_version"], "scene_version_after": after["scene_version"],
                           "circuit": circ})
            if i == 1:
                check(d.get("scene_changed") and circ.get("present"), "the circuit was built in Blender")
                check((circ.get("params") or {}).get("resistance_ohm") == 330 and
                      (circ.get("labels") or {}).get("label_current") == "I = 21.2 mA",
                      f"Blender shows R = 330 Ω and {(circ.get('labels') or {}).get('label_current')!r}")
            elif i == 2:
                check(d.get("scene_changed"), "the scene was updated")
                check((circ.get("params") or {}).get("resistance_ohm") == 1000 and
                      (circ.get("labels") or {}).get("label_current") == "I = 7.0 mA",
                      f"Blender shows R = 1,000 Ω and {(circ.get('labels') or {}).get('label_current')!r}")
                laps = [(c.get("flow") or {}).get("lap_frames") for c in circuits]
                glow = [(c.get("led") or {}).get("emission_at_show") for c in circuits]
                check(all(laps) and abs(laps[1] / laps[0] - 21.2121 / 7.0) < 0.01,
                      f"current markers {laps[1] / laps[0]:.2f}x slower (current ratio 3.03)" if all(laps) else "laps")
                check(glow[1] < glow[0], f"LED glow {glow[0]} -> {glow[1]}")
                uids = [{o["id"]: o["uid"] for o in c.get("objects", [])} for c in circuits]
                check(uids[0] == uids[1] and len(uids[0]) >= 17,
                      f"the same {len(uids[0])} Blender objects were edited, not rebuilt")
                check(d.get("lesson", {}).get("chips", [])[:2] == ["R = 1,000 Ω", "I = 7.0 mA"],
                      f"window chips {d.get('lesson', {}).get('chips', [])[:2]}")
            else:
                check(not d.get("scene_changed"), "explanation only: no circuit request was sent")
                vals = lambda c: (c.get("params"), {k: v for k, v in (c.get("labels") or {}).items()  # noqa: E731
                                                    if k != "label_caption"}, c.get("flow"), c.get("led"))
                check(vals(circ) == vals(circuits[1]),
                      "Blender's lesson values, labels and animation are unchanged (only the subtitle follows Poyo)")
                uids = [{o["id"]: o["uid"] for o in c.get("objects", [])} for c in circuits]
                check(uids[2] == uids[1], "no object was rebuilt")
                check(re.search(r"\b(dimm|current|resist)", d.get("reply", ""), re.I) is not None,
                      "the answer talks about why it got dimmer")
        ack = session.mode["voice"]["phrases"]["ack"][0]
        code, _ = _http(base, "/api/tts", json.dumps({"text": ack}).encode(), timeout=60)
        check(code == 200, f"acknowledgment phrase spoken ({ack!r})")
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.terminate()
        try:
            worker.wait(15)
        except subprocess.TimeoutExpired:
            worker.kill()
        log.close()
    (OUT / "voice_e2e.json").write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
    if blend.exists():
        expr = ("import sys; sys.path.insert(0, %r); from blender_engine import circuit; "
                "circuit.render_preview(%r, 1280, 720, samples=24, file_format='JPEG')") % (
            str(paths.REPO_ROOT), str(OUT / "final_view.jpg"))
        subprocess.run([args.blender, "-b", str(blend), "--python-expr", expr], capture_output=True, timeout=600)
    print(f"\nrecord: {OUT / 'voice_e2e.json'}\naudio:  {OUT}/step*_request.*, step*_poyo.mp3\n"
          f"scene:  {blend}\nrender: {OUT / 'final_view.jpg'} (camera where the last cue left it)")
    print(f"\n{label}: {'ALL PASSED' if not fails else f'{len(fails)} FAILED: {fails}'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
