"""Companion UI server for the tutor (Poyo). Standard library only; binds to localhost.

  python -m director.server                      # real Nemotron + the Blender worker on the shared bridge
  python -m director.server --mock --simulate    # offline: MOCK model + SIMULATED circuit engine (labeled)

Open http://127.0.0.1:8765 . Blender stays open as the 3D stage (blender --python blender_engine/worker.py).

API (JSON):
  GET  /api/state             mode, busy flag, conversation, current lesson, recent events
  POST /api/turn  {"text"}    run one tutor turn in the background (409 while another turn runs)
  POST /api/reset             new conversation (does not touch Blender)
  GET  /api/events            Server-Sent Events: structured execution events (director/events.py), replayable
                              with ?since=<id> or the Last-Event-ID header
  GET  /api/snapshot/<file>   a Blender snapshot from the outbox (name-checked)
  GET  /api/knowledge         the knowledge map: concepts + relations from the SQLite knowledge base
  POST /api/voice  <audio>    push-to-talk: raw recorded audio (audio/webm, audio/wav, ...) -> speech-to-text ->
                              the same tutor turn as /api/turn (409 busy, 422 nothing heard)
  POST /api/tts  {"text"}     Poyo's voice (audio/mpeg) for a tutor reply or a fixed phrase, nothing else
  POST /api/direct {"focus", "caption"}  stage direction for the lesson on screen (display only, direct_request_v1)
Voice is transport only (director/voice.py): Nemotron still decides every turn and Poyo speaks the verified reply.
Every event comes from real execution (director/tutor.py, director/lesson/*). Nothing is simulated for show;
MOCK / SIMULATED modes are labeled in /api/state and in the UI.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import paths
from .nemotron_client import ConfigError, NemotronClient

FRONTEND = paths.REPO_ROOT / "frontend"
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/styles.css": ("styles.css", "text/css; charset=utf-8"),
          "/poyo.svg": ("poyo.svg", "image/svg+xml")}
SNAPSHOT_RE = re.compile(r"^[A-Za-z0-9_.-]+\.(jpg|png)$")
MAX_TEXT = 500
MAX_EVENTS = 2000
# what Poyo says between hearing you and answering (spoken only; the answer itself is the verified reply)
PHRASES = {"ack": ["Okay, let me show you!", "Good question. Let me look.", "On it!", "Let me check the circuit."],
           "unheard": ["Sorry, I didn't catch that. Could you say it again?"],
           "busy": ["Hang on, I'm still working on the last one."]}


class Session:
    """One learner, one conversation, one Blender scene. Turns run one at a time in a background thread."""

    def __init__(self, client, executor, *, model_label: str, executor_label: str, live: bool, simulated: bool,
                 voice=None, voice_error: str | None = None):
        self.client, self.executor, self.voice = client, executor, voice
        self.mode = {"model": model_label, "executor": executor_label, "live": live, "simulated": simulated,
                     "voice": {**voice.info(), "phrases": PHRASES} if voice is not None else
                     {"error": voice_error or "voice is off", "phrases": PHRASES}}
        self.cond = threading.Condition()
        self.busy = False
        self.turn = 0
        self.seq = 0
        self.events: list[dict] = []
        self.messages: list[dict] = []
        self.history: list[str] = []
        self.context: dict = {}
        self.lesson: dict | None = None
        self.knowledge = {"active": [], "discussed": []}  # concept ids: this turn / the whole conversation

    # ---- events ----
    def emit(self, stage: str, status: str, title: str, detail: str = "", /, **data) -> None:
        with self.cond:
            self.seq += 1
            turn = self.turn + 1 if data.pop("_next_turn", False) else self.turn  # speech-to-text precedes its turn
            ev = {"id": self.seq, "turn": turn, "t": round(time.time(), 3), "stage": stage, "status": status,
                  "title": str(title)[:200], "detail": str(detail or "")[:500], "data": _jsonable(data)}
            self.events.append(ev)
            del self.events[:-MAX_EVENTS]
            if stage == "knowledge" and status in ("ok", "info"):
                self.knowledge["active"] = [c["id"] for c in ev["data"].get("concepts", [])]
            if stage == "turn":
                self._finish_turn(ev)
            self.cond.notify_all()

    def _finish_turn(self, ev: dict) -> None:
        d = ev["data"]
        grounding = d.get("grounding")
        if grounding and grounding.get("concepts"):  # the concepts the answer was actually grounded in
            self.knowledge["active"] = [c["id"] for c in grounding["concepts"]]
        for cid in self.knowledge["active"]:
            if cid not in self.knowledge["discussed"]:
                self.knowledge["discussed"].append(cid)
        if d.get("reply"):
            self.messages.append({"role": "tutor", "text": d["reply"], "source": d.get("source"), "turn": ev["turn"],
                                  "ok": ev["status"] == "ok", "grounding": grounding})
        elif ev["status"] == "error":
            self.messages.append({"role": "tutor", "text": f"Something went wrong: {ev['detail']}", "source": "error",
                                  "turn": ev["turn"], "ok": False})
        if d.get("scene_changed") and d.get("title"):
            self.lesson = {"title": d["title"], "explanation": d.get("reply", ""), "snapshot": d.get("snapshot"),
                           "info": d.get("lesson"), "turn": ev["turn"]}
        elif self.lesson is not None and d.get("reply"):
            self.lesson = {**self.lesson, "explanation": d["reply"], "turn": ev["turn"]}

    def events_since(self, since: int, timeout: float) -> list[dict]:
        with self.cond:
            if not any(e["id"] > since for e in self.events):
                self.cond.wait(timeout)
            return [e for e in self.events if e["id"] > since]

    # ---- turns ----
    def start_turn(self, text: str, via: str = "text") -> int | None:
        with self.cond:
            if self.busy:
                return None
            self.busy = True
            self.turn += 1
            turn = self.turn
            self.messages.append({"role": "user", "text": text, "turn": turn, "via": via})
        threading.Thread(target=self._run, args=(text,), daemon=True, name=f"turn-{turn}").start()
        return turn

    def _run(self, text: str) -> None:
        from .tutor import run_turn
        try:
            run_turn(text, self.client, self.executor, self.history, emit=self.emit, context=self.context)
        except Exception as exc:  # noqa: BLE001 - report, never crash the server
            traceback.print_exc()
            self.emit("turn", "error", "Turn crashed", f"{type(exc).__name__}: {exc}")
        finally:
            with self.cond:
                if not any(e["turn"] == self.turn and e["stage"] == "turn" for e in self.events):
                    self.seq += 1
                    self.events.append({"id": self.seq, "turn": self.turn, "t": time.time(), "stage": "turn",
                                        "status": "error", "title": "Turn ended without a result", "detail": "",
                                        "data": {}})
                self.busy = False
                self.cond.notify_all()

    def speakable(self, text: str) -> bool:
        """Poyo only speaks what the tutor actually said (or a fixed phrase): /api/tts is not a free TTS service."""
        text = " ".join(text.split())
        if not text:
            return False
        if any(text in group for group in PHRASES.values()):
            return True
        with self.cond:
            return any(m["role"] == "tutor" and text in " ".join(m["text"].split()) for m in self.messages[-20:])

    def direct(self, focus: str | None, caption: str | None) -> tuple[int, dict]:
        """Stage direction from the browser while Poyo speaks (cue by cue). Display only."""
        from . import stage
        if self.busy:
            return 409, {"error": "a turn is running"}
        try:
            req = stage.direct_request(focus=focus, caption=caption, transition_s=0.9)
        except ValueError as exc:
            return 400, {"error": str(exc)}
        try:
            out = self.executor.send(req, timeout=10)
        except Exception as exc:  # noqa: BLE001 - a missed camera move never breaks the lesson
            return 502, {"error": f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"}
        return (200 if out.get("status") == "ok" else 422), {"status": out.get("status"), "errors": out.get("errors", []),
                                                             "direction": out.get("direction")}

    def reset(self) -> bool:
        with self.cond:
            if self.busy:
                return False
            self.messages.clear()
            self.history.clear()
            self.context.clear()
            self.knowledge = {"active": [], "discussed": []}
            self.emit("understand", "info", "New conversation", "the Blender scene was not changed")
            return True

    def state(self) -> dict:
        with self.cond:
            return {"mode": self.mode, "busy": self.busy, "turn": self.turn, "messages": list(self.messages),
                    "lesson": self.lesson, "knowledge": {k: list(v) for k, v in self.knowledge.items()}, "events": [e for e in self.events if e["turn"] >= self.turn - 2][-200:],
                    "last_event_id": self.seq}


POYO_SLOT = '<div class="poyo idle" id="poyo" aria-hidden="true"></div>'
STATE_SLOT = '<script src="/app.js"></script>'


def knowledge_graph() -> dict | None:
    """The knowledge map (static per seed.json); None if the knowledge base cannot be opened."""
    try:
        from .knowledge import get_kb
        return get_kb().graph()
    except Exception as exc:  # noqa: BLE001 - the tutor still works without the map
        print(f"[server] knowledge base unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def render_index(session: Session) -> bytes:
    """index.html with Poyo's SVG, the current state and the knowledge map inlined (complete first paint)."""
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    svg = (FRONTEND / "poyo.svg").read_text(encoding="utf-8")
    state = json.dumps({**session.state(), "graph": knowledge_graph()}, ensure_ascii=False).replace("</", "<\\/")
    html = html.replace(POYO_SLOT, POYO_SLOT.replace("></div>", f">{svg}</div>"), 1)
    html = html.replace(STATE_SLOT, f"<script>window.__POYO_STATE__ = {state};</script>\n  {STATE_SLOT}", 1)
    return html.encode("utf-8")


def _jsonable(obj):
    try:
        json.dumps(obj)
        return obj
    except TypeError:
        return json.loads(json.dumps(obj, default=str))


def make_handler(session: Session):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PoyoTutor/1"

        def log_message(self, fmt, *args):  # keep the console for the tutor's own output
            if "/api/events" not in (args[0] if args else ""):
                sys.stderr.write(f"[server] {self.address_string()} {fmt % args}\n")

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_GET(self):
            url = urlparse(self.path)
            if url.path in STATIC:
                name, ctype = STATIC[url.path]
                f = FRONTEND / name
                if not f.exists():
                    return self._json(404, {"error": f"missing {name}"})
                if name == "index.html":
                    return self._send(200, render_index(session), ctype)
                return self._send(200, f.read_bytes(), ctype)
            if url.path == "/api/state":
                return self._json(200, session.state())
            if url.path == "/api/events":
                return self._stream(url)
            if url.path == "/api/knowledge":
                graph = knowledge_graph()
                return self._json(200, graph) if graph else self._json(503, {"error": "knowledge base unavailable"})
            if url.path.startswith("/api/snapshot/"):
                name = url.path.rsplit("/", 1)[1]
                f = paths.OUTBOX / name
                if not SNAPSHOT_RE.match(name) or not f.is_file():
                    return self._json(404, {"error": "no such snapshot"})
                return self._send(200, f.read_bytes(), "image/jpeg" if name.endswith(".jpg") else "image/png")
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            url = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            if url.path == "/api/voice":
                return self._voice(length)
            if length > 10_000:
                return self._json(413, {"error": "request too large"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._json(400, {"error": "invalid JSON"})
            if url.path == "/api/turn":
                text = " ".join(str(body.get("text", "")).split())
                if not text:
                    return self._json(400, {"error": "empty message"})
                if len(text) > MAX_TEXT:
                    return self._json(400, {"error": f"message longer than {MAX_TEXT} characters"})
                turn = session.start_turn(text)
                if turn is None:
                    return self._json(409, {"error": "Poyo is still working on the last question"})
                return self._json(202, {"turn": turn})
            if url.path == "/api/reset":
                return self._json(200, {"ok": True}) if session.reset() else self._json(409, {"error": "busy"})
            if url.path == "/api/tts":
                return self._tts(str(body.get("text", "")))
            if url.path == "/api/direct":
                focus, caption = body.get("focus"), body.get("caption")
                code, out = session.direct(focus if isinstance(focus, str) else None,
                                           caption if isinstance(caption, str) else None)
                return self._json(code, out)
            return self._json(404, {"error": "not found"})

        def _voice(self, length: int):
            from .voice import MAX_AUDIO_BYTES, strip_wake_word
            if session.voice is None:
                self.rfile.read(min(length, MAX_AUDIO_BYTES))
                return self._json(503, {"error": session.mode["voice"]["error"]})
            if length <= 0 or length > MAX_AUDIO_BYTES:
                return self._json(413 if length else 400, {"error": "audio missing or larger than 8 MB"})
            audio = self.rfile.read(length)
            if session.busy:
                return self._json(409, {"error": "Poyo is still working on the last question"})
            mime = self.headers.get("Content-Type") or "audio/webm"
            session.emit("listen", "start", "Transcribing what you said", session.mode["voice"].get("stt", ""), _next_turn=True)
            try:
                heard = session.voice.transcribe(audio, mime)
            except Exception as exc:  # noqa: BLE001
                session.emit("listen", "error", "Speech-to-text failed", str(exc)[:300], _next_turn=True)
                return self._json(502, {"error": str(exc)[:300]})
            text = " ".join(strip_wake_word(heard).split())[:MAX_TEXT]
            if not text:
                session.emit("listen", "info", "Didn't catch that", repr(heard)[:200], _next_turn=True)
                return self._json(422, {"error": "nothing heard", "heard": heard})
            session.emit("listen", "ok", f"Heard: “{text}”", "speech-to-text, then the normal tutor turn", _next_turn=True)
            turn = session.start_turn(text, via="voice")
            if turn is None:
                return self._json(409, {"error": "Poyo is still working on the last question", "text": text})
            return self._json(202, {"turn": turn, "text": text, "heard": heard})

        def _tts(self, text: str):
            from .voice import cached_speech
            if session.voice is None:
                return self._json(503, {"error": session.mode["voice"]["error"]})
            if not session.speakable(text):
                return self._json(403, {"error": "Poyo only speaks the tutor's own replies"})
            try:
                audio = cached_speech(session.voice, text)
            except Exception as exc:  # noqa: BLE001
                return self._json(502, {"error": str(exc)[:300]})
            return self._send(200, audio, "audio/mpeg")

        def _stream(self, url):
            q = parse_qs(url.query)
            since = int((q.get("since") or [self.headers.get("Last-Event-ID") or 0])[0] or 0)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                while True:
                    evs = session.events_since(since, timeout=15)
                    if not evs:
                        self.wfile.write(b": keep-alive\n\n")
                    for ev in evs:
                        since = ev["id"]
                        self.wfile.write(f"id: {ev['id']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

    return Handler


def make_voice(mock: bool, enabled: bool = True):
    """(provider, error). MOCK model -> MOCK voice (offline); otherwise OpenAI if OPENAI_API_KEY is set."""
    from .voice import FakeVoice, OpenAIVoice
    if not enabled:
        return None, "voice is off (--no-voice): type instead"
    if mock:
        return FakeVoice(), None
    try:
        return OpenAIVoice(), None
    except ConfigError as exc:
        return None, str(exc)


def make_session(mock: bool, simulate: bool, timeout: float, voice: bool = True) -> Session:
    if mock:
        from .circuit.mock import TutorMockModel
        client, model_label, live = TutorMockModel(), "MOCK model (not Nemotron)", False
    else:
        client = NemotronClient(temperature=0.2)
        model_label, live = f"Nemotron ({client.model.split('/')[-1]})", True
    if simulate:
        from .circuit.sim import SimulatedCircuitExecutor
        from .tutor import SIM_STATE
        SIM_STATE.unlink(missing_ok=True)
        executor, exec_label = SimulatedCircuitExecutor(SIM_STATE), "SIMULATED engine (not Blender)"
    else:
        from .tutor import BridgeExecutor
        executor, exec_label = BridgeExecutor(timeout), "Blender (live)"
    provider, voice_error = make_voice(mock, voice)
    return Session(client, executor, model_label=model_label, executor_label=exec_label, live=live,
                   simulated=simulate, voice=provider, voice_error=voice_error)


def serve(session: Session, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), make_handler(session))
    httpd.daemon_threads = True
    return httpd


def main(argv: list[str] | None = None) -> int:
    paths.load_env()
    ap = argparse.ArgumentParser(prog="python -m director.server", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--mock", action="store_true", help="offline MOCK model (NOT Nemotron)")
    ap.add_argument("--simulate", action="store_true", help="SIMULATED circuit engine instead of Blender")
    ap.add_argument("--timeout", type=float, default=90.0, help="seconds to wait for Blender per request")
    ap.add_argument("--no-voice", action="store_true", help="no speech-to-text / text-to-speech (type instead)")
    args = ap.parse_args(argv)
    try:
        session = make_session(args.mock, args.simulate, args.timeout, voice=not args.no_voice)
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2
    httpd = serve(session, args.host, args.port)
    print(f"Poyo tutor: http://{args.host}:{httpd.server_address[1]}   model: {session.mode['model']}   "
          f"engine: {session.mode['executor']}   voice: "
          f"{session.mode['voice'].get('provider') or session.mode['voice']['error']}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
