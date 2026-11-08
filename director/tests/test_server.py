"""Offline tests for the companion UI server and tutor events (MOCK model + SIMULATED engine)."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from director import server
from director.circuit.mock import TutorMockModel
from director.circuit.sim import SimulatedCircuitExecutor
from director.tutor import run_turn


@pytest.fixture()
def running(tmp_path):
    session = server.Session(TutorMockModel(), SimulatedCircuitExecutor(tmp_path / "sim.json"),
                             model_label="MOCK model (not Nemotron)", executor_label="SIMULATED engine (not Blender)",
                             live=False, simulated=True)
    httpd = server.serve(session, "127.0.0.1", 0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield session, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _req(url, data=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _turn(base, text):
    code, body = _req(f"{base}/api/turn", {"text": text})
    assert code == 202, body
    for _ in range(100):
        state = json.loads(_req(f"{base}/api/state")[1])
        if not state["busy"]:
            return state
        time.sleep(0.05)
    raise AssertionError("turn did not finish")


def test_turns_keep_context_and_emit_real_events(running):
    session, base = running
    s1 = _turn(base, "Teach me why LEDs need resistors.")
    assert s1["mode"]["simulated"] and not s1["mode"]["live"]
    assert s1["lesson"]["title"] == "Why does an LED need a resistor?"
    assert s1["lesson"]["info"]["chips"][:2] == ["R = 330 Ω", "I = 21.2 mA"]
    s2 = _turn(base, "What if the resistor is 1,000 ohms?")
    assert s2["lesson"]["title"] == "What if the resistor is 1,000 Ω?"
    assert s2["lesson"]["info"]["chips"][1] == "I = 7.0 mA"
    assert [m["role"] for m in s2["messages"]] == ["user", "tutor", "user", "tutor"]
    assert all(m.get("source") == "mock" for m in s2["messages"] if m["role"] == "tutor")
    stages = [(e["stage"], e["status"]) for e in s2["events"] if e["turn"] == 2]
    for want in [("plan", "ok"), ("calculate", "ok"), ("apply", "ok"), ("verify", "ok"), ("turn", "ok")]:
        assert want in stages
    assert stages.index(("calculate", "ok")) < stages.index(("apply", "ok")) < stages.index(("verify", "ok"))
    s3 = _turn(base, "Why did the current decrease?")  # explain: the scene is updated, the value is not
    assert s3["lesson"]["info"]["chips"][0] == "R = 1,000 Ω"
    assert session.context["last_explanation"]


def test_busy_and_bad_requests(running):
    session, base = running
    assert _req(f"{base}/api/turn", {"text": "  "})[0] == 400
    assert _req(f"{base}/api/turn", {"text": "x" * 600})[0] == 400
    session.busy = True
    assert _req(f"{base}/api/turn", {"text": "hello"})[0] == 409
    assert _req(f"{base}/api/reset", {})[0] == 409
    session.busy = False
    assert _req(f"{base}/api/snapshot/..%2F..%2Fsecret.jpg")[0] == 404
    assert _req(f"{base}/api/snapshot/nope.jpg")[0] == 404
    assert _req(f"{base}/nope")[0] == 404


def test_index_inlines_state_safely(running):
    session, base = running
    session.messages.append({"role": "user", "text": "</script><script>alert(1)</script>", "turn": 0})
    code, body = _req(f"{base}/")
    html = body.decode()
    assert code == 200 and "window.__POYO_STATE__" in html and 'id="poyo-body"' in html
    assert "</script><script>alert(1)" not in html


def test_events_stream_replays_since(running):
    session, base = running
    _turn(base, "Teach me why LEDs need resistors.")
    with urllib.request.urlopen(f"{base}/api/events?since=0", timeout=5) as r:
        first = b""
        while b"\n\n" not in first:
            first += r.read(1)
    ev = json.loads(first.split(b"data: ", 1)[1].split(b"\n", 1)[0])
    assert ev["id"] == 1 and ev["stage"] == "understand"


def test_run_turn_event_order_offline(tmp_path):
    events = []
    code = run_turn("Teach me why LEDs need resistors.", TutorMockModel(), SimulatedCircuitExecutor(tmp_path / "s.json"),
                    [], emit=lambda *a, **k: events.append((a[0], a[1])))
    assert code == 0
    assert events[0] == ("understand", "ok") and events[-1] == ("turn", "ok")
    starts = [s for s, st in events if st == "start"]
    for s in starts:  # every start is closed by a result for the same stage
        assert any(s2 == s and st in ("ok", "error", "info") for s2, st in events)


def test_follow_up_keeps_title_and_reexplains(tmp_path):
    from director.tutor import FOLLOW_UP_RE
    assert FOLLOW_UP_RE.search("Explain that in simpler terms.") and FOLLOW_UP_RE.search("I don't get it")
    assert not FOLLOW_UP_RE.search("What happens if I remove the resistor?")
    sim, ctx, events = SimulatedCircuitExecutor(tmp_path / "s.json"), {}, []
    model = TutorMockModel()
    run_turn("Teach me why LEDs need resistors.", model, sim, [], context=ctx)
    run_turn("Explain that in simpler terms.", model, sim, [], context=ctx, emit=lambda *a, **k: events.append((a, k)))
    done = [k for a, k in events if a[0] == "turn"][0]
    assert done["title"] == "Why does an LED need a resistor?"
