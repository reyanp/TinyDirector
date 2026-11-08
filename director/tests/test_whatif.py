"""What-If Lab (director side): tour + split-reality comparison tools, cues, read-back checks, server camera
events. Offline: MOCK model + SIMULATED engine (Blender itself: blender_engine/compare_check.py)."""

from __future__ import annotations

import copy
import json
import threading

import pytest
from jsonschema import Draft202012Validator

from director import paths, server, stage, voice
from director.circuit import check
from director.circuit.agent import validate_tool
from director.circuit.display import compare_clear_request, compare_request, display_for
from director.circuit.mock import TutorMockModel
from director.circuit.model import LedCircuit, solve
from director.circuit.sim import SimulatedCircuitExecutor
from director.tutor import run_turn, tour_callouts

from .test_stage_voice import _post, _wait

DEMO = ["Teach me why LEDs need resistors.", "Poyo, show me what's happening inside this circuit.",
        "Compare what happens if I increase the resistor to 1,000 ohms.", "Why is the right one dimmer?",
        "Go back to the original."]


def _turns(texts, tmp_path):
    sim, ctx, model, out = SimulatedCircuitExecutor(tmp_path / "s.json"), {}, TutorMockModel(), []
    for t in texts:
        ev = []
        before = sim.state["scene_version"]
        code = run_turn(t, model, sim, [], context=ctx, emit=lambda *a, **k: ev.append((a, k)))
        out.append({"code": code, "turn": [k for a, k in ev if a[0] == "turn"][-1], "events": ev,
                    "sent": sim.state["scene_version"] - before, "params": copy.deepcopy(
                        (sim.state["circuit"] or {}).get("params")), "compare": copy.deepcopy(sim.state.get("compare"))})
    return sim, ctx, out


# ---------------- tool schema ----------------
def test_new_tools_are_validated():
    assert validate_tool({"tool": "tour_circuit", "args": {"stops": ["battery", "resistor", "overview"]}}) == []
    assert validate_tool({"tool": "tour_circuit", "args": {"stops": ["battery"]}})          # at least 2 stops
    assert validate_tool({"tool": "tour_circuit", "args": {"stops": ["battery", "capacitor"]}})
    assert validate_tool({"tool": "tour_circuit", "args": {"stops": ["led", "led"]}})       # no repeats
    assert validate_tool({"tool": "compare_resistance", "args": {"resistance_ohm": 1000}}) == []
    assert validate_tool({"tool": "compare_resistance", "args": {"direction": "increase"}}) == []
    assert validate_tool({"tool": "compare_resistance", "args": {}})
    assert validate_tool({"tool": "compare_resistance", "args": {"resistance_ohm": 1000, "direction": "increase"}})
    assert validate_tool({"tool": "end_comparison", "args": {}}) == []
    assert validate_tool({"tool": "end_comparison", "args": {"x": 1}})


# ---------------- comparison numbers ----------------
def test_comparison_labels_are_the_calculation():
    req = compare_request("c1", LedCircuit(), LedCircuit().with_resistance(1000))
    schema = json.loads((paths.SCHEMA_DIR / "compare_request_v1.json").read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(req)) == []
    assert list(Draft202012Validator(schema).iter_errors(compare_clear_request("c2"))) == []
    # I = (9 V - 2 V) / R: 7 / 330 = 21.21 mA, 7 / 1000 = 7.00 mA
    assert req["headlines"]["baseline"] == "NOW  ·  R = 330 Ω\nI = (9 V − 2 V) ÷ 330 Ω = 21.2 mA"
    assert req["headlines"]["alternative"] == "WHAT IF  ·  R = 1,000 Ω\nI = (9 V − 2 V) ÷ 1,000 Ω = 7.0 mA"
    d = req["alternative"]["display"]
    assert d["labels"]["label_current"] == "I = 7.0 mA"
    assert abs(solve(LedCircuit().with_resistance(1000)).current_ma - 7.0) < 1e-9
    base = display_for(solve(LedCircuit()))
    assert d["flow_lap_frames"] / base["flow_lap_frames"] == pytest.approx(1000 / 330, rel=0.01)  # 3.03x slower
    assert d["led_emission"] < base["led_emission"]
    assert "illustrative" in req["legend"] and "not electron speed" in req["legend"]


def test_tour_callouts_are_calculated():
    c = tour_callouts(LedCircuit())
    assert [c[k]["value"] for k in ("battery", "resistor", "current", "led", "overview")] == \
           ["9 V", "7.0 V", "21.2 mA", "≈ 2 V", "I = 21.2 mA"]
    assert c["overview"]["note"] == "(9 V − 2 V) ÷ 330 Ω"
    for co in c.values():  # every callout fits direct_request_v1
        stage.direct_request("overview", callout=co)


# ---------------- cues ----------------
STOPS = ["battery", "resistor", "current", "led", "overview"]


def test_tour_cues_follow_the_stops():
    text = ("The 9 V battery pushes the current. The 330 Ω resistor takes 7.0 V. The same 21.2 mA flows everywhere. "
            "The LED keeps about 2 V. That's the whole loop.")
    cues = stage.tour_cues(text, STOPS, tour_callouts(LedCircuit()))
    assert [c["focus"] for c in cues] == STOPS
    assert cues[1]["callout"]["value"] == "7.0 V" and all(c["caption"] for c in cues)
    # fewer keywords than stops: shared out evenly, never backwards, ends on the whole circuit
    cues = stage.tour_cues("One. Two things. Three things here. Four of them. Five now.", STOPS, {})
    assert [c["focus"] for c in cues] == STOPS
    cues = stage.tour_cues("The resistor sets it. And then the battery. Finally the current loop. Done here.",
                           ["resistor", "current", "overview"], {})
    focus = [c["focus"] for c in cues]
    assert focus[0] == "resistor" and focus[-1] == "overview"
    assert all(STOPS.index(a) <= STOPS.index(b) or b == "overview" for a, b in zip(focus, focus[1:])) or \
        focus == sorted(focus, key=["resistor", "current", "overview"].index)


def test_compare_cues_look_at_the_side_named():
    text = ("On the left, the 330 Ω resistor lets 21.2 mA through. With 1,000 ohms on the right, only about 7 milliamps "
            "flows, so the LED is dimmer. More resistance means less current.")
    cues = stage.compare_cues(text, {"baseline": (330, 21.21), "alternative": (1000, 7.0)},
                              {"baseline": {"value": "21.2 mA"}, "alternative": {"value": "7.0 mA"}})
    focus = [c["focus"] for c in cues]
    assert focus[0] == "baseline" and "alternative" in focus and focus[-1] == "compare"
    assert cues[focus.index("alternative")]["callout"] == {"value": "7.0 mA"}


def test_direct_request_callout_validation():
    assert stage.direct_request(callout={"value": "21.2 mA", "note": "x"})["callout"]["value"] == "21.2 mA"
    with pytest.raises(ValueError):
        stage.direct_request("led", callout={"value": "x" * 25})
    with pytest.raises(ValueError):
        stage.direct_request("led", callout={"note": "no value"})


# ---------------- the demo flow ----------------
def test_tour_compare_and_return(tmp_path):
    sim, ctx, out = _turns(DEMO, tmp_path)
    assert [o["code"] for o in out] == [0] * 5
    build, tour, cmp, why, back = out
    # tour: Nemotron's (here MOCK) stops, calculated callouts, nothing sent to the lesson
    assert tour["sent"] == 0 and not tour["turn"]["scene_changed"]
    assert tour["turn"]["tour"] == STOPS and [c["focus"] for c in tour["turn"]["stage"]["cues"]] == STOPS
    assert tour["turn"]["stage"]["cues"][2]["callout"]["value"] == "21.2 mA"
    # comparison: the original keeps 330 ohm; the what-if copy has 1,000 ohm; every read-back check passed
    assert cmp["params"] == build["params"] == {"source_v": 9.0, "resistance_ohm": 330.0, "led_vf": 2.0}
    assert cmp["compare"]["alternative"]["circuit"]["resistance_ohm"] == 1000
    verify = [k for a, k in cmp["events"] if a[0] == "verify"][-1]
    assert verify["failed"] == 0 and verify["passed"] >= 12
    assert cmp["turn"]["lesson"]["chips"][:2] == ["Now: 330 Ω → 21.2 mA", "What if: 1,000 Ω → 7.0 mA"]
    assert "21.2 mA" in cmp["turn"]["reply"] and "7.0 mA" in cmp["turn"]["reply"]
    assert {c["focus"] for c in cmp["turn"]["stage"]["cues"]} >= {"alternative", "compare"}
    # a question about the two circuits: answered from both calculations, nothing sent
    assert why["sent"] == 0 and why["compare"] is not None and "7.0 mA" in why["turn"]["reply"]
    # back to the original: copy gone, same values, the lesson title/chips come back
    assert back["compare"] is None and back["params"] == build["params"]
    assert back["turn"]["lesson"]["title"] == "Why does an LED need a resistor?" and "compare" not in ctx
    assert "330 Ω" in back["turn"]["reply"] and "21.2 mA" in back["turn"]["reply"]


def test_a_value_change_while_comparing_ends_the_comparison(tmp_path):
    sim, ctx, out = _turns(DEMO[:1] + DEMO[2:3] + ["Change the resistor to 470 ohms."], tmp_path)
    assert out[1]["compare"] is not None
    assert out[2]["compare"] is None and out[2]["params"]["resistance_ohm"] == 470 and "compare" not in ctx


def test_compare_with_nothing_on_stage_builds_the_baseline_first(tmp_path):
    from director.tutor import compare_resistance
    from director.trace import Trace
    sim, ev = SimulatedCircuitExecutor(tmp_path / "s.json"), []
    code = compare_resistance({"resistance_ohm": 1000}, "compare 1k", TutorMockModel(), sim, None, sim.inspect(), None,
                              None, Trace("t", "x"), lambda *a, **k: ev.append((a, k)))
    assert code == 0 and sim.state["circuit"]["params"]["resistance_ohm"] == 330
    assert sim.state["compare"]["alternative"]["circuit"]["resistance_ohm"] == 1000


def test_same_value_and_end_without_comparison(tmp_path):
    sim, ctx, out = _turns([DEMO[0], "Compare it side by side with 330 ohms.", "Go back to the original."], tmp_path)
    assert out[1]["sent"] == 0 and "already has 330 Ω" in out[1]["turn"]["reply"]
    from director.tutor import end_comparison
    from director.trace import Trace
    ev = []
    assert end_comparison(sim, LedCircuit(), sim.inspect(), Trace("t", "x"), lambda *a, **k: ev.append(k)) == 0
    assert "already the original" in ev[-1]["reply"] and sim.state["scene_version"] == 1


def test_compare_check_catches_a_wrong_copy(tmp_path):
    sim = SimulatedCircuitExecutor(tmp_path / "s.json")
    run_turn(DEMO[0], TutorMockModel(), sim, [], context={})
    before = sim.inspect()
    req = compare_request("c1", LedCircuit(), LedCircuit().with_resistance(1000))
    good = sim.send(req)
    assert all(c.ok for c in check.run_compare(req, before, good))
    bad = copy.deepcopy(good)
    bad["compare"]["alternative"]["labels"]["label_current"] = "I = 21.2 mA"   # a stale label on the copy
    bad["circuit"]["params"]["resistance_ohm"] = 1000.0                          # the original was changed
    failed = {c.what for c in check.run_compare(req, before, bad) if not c.ok}
    assert "original circuit values unchanged" in failed
    assert any("current label" in f for f in failed)
    stale = SimulatedCircuitExecutor(tmp_path / "t.json")
    run_turn("Teach me with a 470 ohm resistor.", TutorMockModel(), stale, [], context={})
    assert stale.send(req)["status"] == "error"   # the baseline must be what is on stage


# ---------------- server: camera moves are visible tool actions ----------------
def test_direct_with_callout_is_an_activity_event(tmp_path):
    session = server.Session(TutorMockModel(), SimulatedCircuitExecutor(tmp_path / "sim.json"), model_label="MOCK",
                             executor_label="SIM", live=False, simulated=True, voice=voice.FakeVoice())
    httpd = server.serve(session, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        assert _post(f"{base}/api/turn", json.dumps({"text": DEMO[0]}).encode())[0] == 202
        _wait(base)
        body = {"focus": "current", "caption": "Same current.", "callout": {"value": "21.2 mA", "note": "all around"}}
        code, raw, _ = _post(f"{base}/api/direct", json.dumps(body).encode())
        d = json.loads(raw)["direction"]
        assert code == 200 and d["callout"] == body["callout"] and d["flow_emphasis"] is True
        ev = [e for e in _wait(base)["events"] if e["stage"] == "direct"][-1]
        assert ev["status"] == "ok" and ev["title"] == "Camera → current" and "callout 21.2 mA" in ev["detail"]
        assert _post(f"{base}/api/direct", json.dumps({"focus": "led", "callout": {"value": "x" * 30}}).encode())[0] == 400
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_tour_narration_must_have_one_sentence_per_stop(tmp_path):
    """A narration that skips stops is rejected once, then the deterministic tour text is used, so the camera
    always visits every stop Nemotron chose."""
    from director.nemotron_client import ModelReply

    class ShortNarrator(TutorMockModel):
        calls = 0

        def complete(self, messages, *, max_tokens=None):
            if messages[0]["content"].startswith("EXPLAINER"):
                ShortNarrator.calls += 1
                return ModelReply(content="The 9 V battery pushes. The 330 Ω resistor sets 21.2 mA.", model="m", live=False)
            return super().complete(messages, max_tokens=max_tokens)

    sim, ctx = SimulatedCircuitExecutor(tmp_path / "s.json"), {}
    run_turn(DEMO[0], TutorMockModel(), sim, [], context=ctx)
    ev = []
    assert run_turn(DEMO[1], ShortNarrator(), sim, [], context=ctx, emit=lambda *a, **k: ev.append((a, k))) == 0
    turn = [k for a, k in ev if a[0] == "turn"][-1]
    assert ShortNarrator.calls == 2 and turn["source"] == "template"
    assert [c["focus"] for c in turn["stage"]["cues"]] == STOPS
