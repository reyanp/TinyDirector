"""Offline tests for the scene-first redesign: stage cues, stage direction requests, the new tutor tools
(adjust_resistance / focus_camera / explain without re-sending the scene), and the voice + direction endpoints
(MOCK voice, MOCK model, SIMULATED engine)."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from director import server, stage, voice
from director.circuit.agent import TOOL_SCHEMA, describe_state
from director.circuit.mock import TutorMockModel
from director.circuit.model import CircuitModelError, LedCircuit, step_resistance
from director.circuit.sim import SimulatedCircuitExecutor
from director.tutor import run_turn


# ---------------- cues ----------------
def test_cues_follow_the_sentence_subject():
    text = ("Changing the resistor from 330 Ω to 1,000 Ω decreased the current from 21.2 mA to 7.0 mA, so the LED "
            "gets dimmer. The battery still gives 9 V.")
    cs = stage.cues(text, "circuit")
    assert [c["focus"] for c in cs] == ["resistor", "led", "battery"]
    assert "".join(c["text"] for c in cs).replace(" ", "") == text.replace(" ", "")   # nothing lost or reordered
    assert all(c["caption"] for c in cs)
    assert cs[0]["text"].endswith("7.0 mA,")   # decimals are not sentence breaks


def test_cues_current_and_led_case():
    cs = stage.cues("Current flows from + to −. It led to a brighter glow.", "circuit")
    assert cs[0]["focus"] == "current"
    assert cs[1]["focus"] == "led"   # "brighter", not the verb "led"
    assert stage.cues("Hello there.", "circuit")[0]["focus"] is None
    assert stage.cues("Any text.", None) == []
    assert stage.cues("The resistor sets it.", "circuit", opening="overview")[0]["focus"] == "overview"


def test_cues_for_generated_lessons_use_part_ids():
    parts = {"r1": {"type": "resistor", "label": "R1 10 kΩ"}, "r2": {"type": "resistor", "label": "R2 10 kΩ"},
             "bat": {"type": "battery", "label": "9 V battery"}}
    cs = stage.cues("R2 takes half of it. The battery gives 9 V. A resistor drops voltage.", "topic", parts)
    assert [c["focus"] for c in cs] == ["r2", "bat", "r1"]
    assert all(c["caption"] is None for c in cs)


def test_direct_request_is_validated():
    req = stage.direct_request(focus="resistor", caption="Look here.")
    assert req["kind"] == "direct" and req["focus"] == "resistor" and req["highlight"]
    with pytest.raises(ValueError):
        stage.direct_request()                      # needs focus or caption
    with pytest.raises(ValueError):
        stage.direct_request(focus="a b; rm -rf")   # ids only


# ---------------- calculation: "make it bigger" ----------------
def test_step_resistance_uses_standard_values():
    assert step_resistance(330, "increase") == 1000
    assert step_resistance(1000, "decrease") == 330
    assert step_resistance(1000, "increase") == 3300
    assert step_resistance(470, "increase") > 470
    with pytest.raises(CircuitModelError):
        step_resistance(1_000_000, "increase")


def test_new_tools_are_in_the_schema_and_state_mentions_focus():
    s = json.dumps(TOOL_SCHEMA)
    assert "adjust_resistance" in s and "focus_camera" in s
    text = describe_state(LedCircuit(), None, "resistor")
    assert "resistor" in text and "330" in text


# ---------------- tutor turns ----------------
def _turns(texts, tmp_path):
    sim, ctx, model, out = SimulatedCircuitExecutor(tmp_path / "s.json"), {}, TutorMockModel(), []
    for t in texts:
        ev = []
        code = run_turn(t, model, sim, [], context=ctx, emit=lambda *a, **k: ev.append((a, k)))
        out.append((code, [k for a, k in ev if a[0] == "turn"][-1], ev))
    return sim, out


def test_voice_demo_flow_modifies_then_explains_without_rebuilding(tmp_path):
    sim, out = _turns(["Teach me why LEDs need resistors.", "What if I make the resistor 1,000 ohms?",
                       "Why did it get dimmer?"], tmp_path)
    (c1, t1, _), (c2, t2, _), (c3, t3, ev3) = out
    assert (c1, c2, c3) == (0, 0, 0)
    assert t1["scene_changed"] and t1["stage"]["kind"] == "circuit" and t1["stage"]["cues"][0]["focus"] == "overview"
    assert t2["scene_changed"] and t2["lesson"]["chips"][:2] == ["R = 1,000 Ω", "I = 7.0 mA"]
    assert sim.state["scene_version"] == 2           # build + one update, nothing else was sent
    assert not t3["scene_changed"] and t3["stage"]["kind"] == "circuit"
    assert t3["title"] == "What if the resistor is 1,000 Ω?"   # what is on screen, unchanged
    assert any(a[0] == "apply" and a[1] == "info" for a, k in ev3)
    assert sim.state["circuit"]["params"]["resistance_ohm"] == 1000


def test_adjust_resistance_without_a_number(tmp_path):
    sim, out = _turns(["Teach me why LEDs need resistors.", "Increase the resistance.", "Decrease the resistance."],
                      tmp_path)
    assert out[1][1]["lesson"]["chips"][0] == "R = 1,000 Ω"
    assert out[2][1]["lesson"]["chips"][0] == "R = 330 Ω"
    assert sim.state["scene_version"] == 3


def test_focus_camera_changes_the_view_only(tmp_path):
    sim, out = _turns(["Teach me why LEDs need resistors.", "Show me the resistor."], tmp_path)
    code, turn, _ = out[1]
    assert code == 0 and not turn["scene_changed"] and turn["source"] == "direction"
    assert "330 Ω" in turn["reply"]
    assert sim.state["direction"]["focus"] == "resistor" and sim.state["scene_version"] == 1


def test_focus_camera_with_nothing_on_stage(tmp_path):
    sim = SimulatedCircuitExecutor(tmp_path / "s.json")
    from director.tutor import focus_camera
    from director.trace import Trace
    ev = []
    assert focus_camera("led", sim, None, sim.inspect(), Trace("t", "x"), lambda *a, **k: ev.append(k)) == 0
    assert "nothing on the stage" in ev[-1]["reply"].lower()


# ---------------- voice helpers ----------------
def test_wake_word_and_speakable_units():
    assert voice.strip_wake_word("Poyo, teach me why LEDs need resistors.") == "Teach me why LEDs need resistors."
    assert voice.strip_wake_word("Hey Poyo what if I make it 1,000 ohms?") == "What if I make it 1,000 ohms?"
    assert voice.strip_wake_word("Why did it get dimmer?") == "Why did it get dimmer?"
    assert voice.strip_wake_word("Poyo.") == ""
    assert voice.speakable("7.0 mA through 1,000 Ω at 9 V") == "7.0 milliamps through 1,000 ohms at 9 volts"


# ---------------- server: voice, tts, direct ----------------
@pytest.fixture()
def running(tmp_path):
    session = server.Session(TutorMockModel(), SimulatedCircuitExecutor(tmp_path / "sim.json"),
                             model_label="MOCK model (not Nemotron)", executor_label="SIMULATED engine (not Blender)",
                             live=False, simulated=True, voice=voice.FakeVoice())
    httpd = server.serve(session, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield session, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _post(url, data: bytes, ctype="application/json"):
    req = urllib.request.Request(url, data=data, headers={"Content-Type": ctype}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), r.headers.get("Content-Type")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Content-Type")


def _wait(base):
    for _ in range(100):
        s = json.loads(urllib.request.urlopen(f"{base}/api/state", timeout=5).read())
        if not s["busy"]:
            return s
        time.sleep(0.05)
    raise AssertionError("turn did not finish")


def test_voice_turn_end_to_end(running):
    session, base = running
    code, body, _ = _post(f"{base}/api/voice", b"Poyo, teach me why LEDs need resistors.", "audio/webm")
    assert code == 202
    assert json.loads(body)["text"] == "Teach me why LEDs need resistors."
    s = _wait(base)
    assert s["mode"]["voice"]["provider"].startswith("MOCK")
    assert s["messages"][0] == {"role": "user", "text": "Teach me why LEDs need resistors.", "turn": 1, "via": "voice"}
    listen = [e for e in s["events"] if e["stage"] == "listen"]
    assert listen and all(e["turn"] == 1 for e in listen)
    reply = s["messages"][-1]["text"]
    # Poyo speaks the tutor's reply and its fixed phrases, nothing else
    code, audio, ctype = _post(f"{base}/api/tts", json.dumps({"text": reply}).encode())
    assert code == 200 and ctype == "audio/mpeg" and audio.startswith(b"ID3MOCK")
    sentence = stage.sentences(reply)[0]
    assert _post(f"{base}/api/tts", json.dumps({"text": sentence}).encode())[0] == 200
    assert _post(f"{base}/api/tts", json.dumps({"text": server.PHRASES["ack"][0]}).encode())[0] == 200
    assert _post(f"{base}/api/tts", json.dumps({"text": "Say something else entirely."}).encode())[0] == 403
    # stage direction while speaking: display only
    code, body, _ = _post(f"{base}/api/direct", json.dumps({"focus": "resistor", "caption": "Here."}).encode())
    assert code == 200 and json.loads(body)["direction"]["focus"] == "resistor"
    assert session.executor.state["scene_version"] == 1
    assert _post(f"{base}/api/direct", json.dumps({"focus": "x y"}).encode())[0] == 400
    assert _post(f"{base}/api/direct", json.dumps({"focus": "capacitor_9"}).encode())[0] == 422


def test_voice_rejects_silence_and_busy(running):
    session, base = running
    assert _post(f"{base}/api/voice", b"Poyo.", "audio/webm")[0] == 422
    assert _post(f"{base}/api/voice", b"", "audio/webm")[0] == 400
    session.busy = True
    assert _post(f"{base}/api/voice", b"Why?", "audio/webm")[0] == 409
    assert _post(f"{base}/api/direct", json.dumps({"focus": "led"}).encode())[0] == 409
    session.busy = False


def test_voice_off_keeps_text_path(tmp_path):
    session = server.Session(TutorMockModel(), SimulatedCircuitExecutor(tmp_path / "sim.json"), model_label="MOCK",
                             executor_label="SIM", live=False, simulated=True, voice=None, voice_error="no key")
    httpd = server.serve(session, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        code, body, _ = _post(f"{base}/api/voice", b"hello", "audio/webm")
        assert code == 503 and "no key" in json.loads(body)["error"]
        assert _post(f"{base}/api/tts", json.dumps({"text": "On it!"}).encode())[0] == 503
        assert _post(f"{base}/api/turn", json.dumps({"text": "Teach me why LEDs need resistors."}).encode())[0] == 202
        assert _wait(base)["lesson"]["title"] == "Why does an LED need a resistor?"
    finally:
        httpd.shutdown()
        httpd.server_close()
