"""Offline tests for the circuit tutor: model math, display mapping, tool validation, number guard,
and the full tutor turn against the SIMULATED executor with the MOCK model."""

import json

import pytest

from director.circuit import check
from director.circuit.agent import (ToolError, choose_tool, explain, facts_for, number_check, template_explanation,
                                    validate_tool, ToolCall)
from director.circuit.display import (BASE_LAP_FRAMES, COMPONENTS, LABELS, REF_MA, circuit_request, display_for,
                                      lap_frames, led_emission)
from director.circuit.mock import TutorMockModel, parse_ohms
from director.circuit.model import (CircuitModelError, LedCircuit, check_resistance, color_bands, format_ma,
                                    format_ohms, solve)
from director.circuit.sim import SimulatedCircuitExecutor
from director.mock_model import ScriptedModel
from director.schema import validate_result
from director.trace import Trace
from director.tutor import run_turn, validate_request


# ---------------- model ----------------

def test_330_ohm_gives_about_21_2_ma():
    sol = solve(LedCircuit(source_v=9, resistance_ohm=330, led_vf=2))
    assert sol.conducting
    assert sol.current_ma == pytest.approx(21.2121, abs=1e-3)
    assert format_ma(sol.current_ma) == "21.2 mA"
    assert sol.v_resistor == pytest.approx(7.0) and sol.v_led == 2.0


def test_1000_ohm_gives_about_7_0_ma():
    sol = solve(LedCircuit().with_resistance(1000))
    assert sol.current_ma == pytest.approx(7.0, abs=1e-9)
    assert format_ma(sol.current_ma) == "7.0 mA"
    assert sol.rating == "ok"


def test_default_circuit_is_the_lesson_circuit():
    c = LedCircuit()
    assert (c.source_v, c.resistance_ohm, c.led_vf) == (9.0, 330.0, 2.0)


def test_power_and_rating():
    sol = solve(LedCircuit())
    assert sol.p_resistor_w == pytest.approx(7.0 * 7.0 / 330)
    assert sol.rating == "high"  # 21.2 mA is just above the typical 20 mA rating
    assert solve(LedCircuit().with_resistance(10)).rating == "over_max"


def test_no_resistor_is_outside_the_model():
    with pytest.raises(CircuitModelError):
        solve(LedCircuit(resistance_ohm=0))


def test_led_off_below_forward_voltage():
    sol = solve(LedCircuit(source_v=1.5))
    assert not sol.conducting and sol.current_a == 0 and sol.rating == "off"


@pytest.mark.parametrize("ohms", [10, 220, 330, 470, 1000, 2200, 4700, 10000, 1_000_000])
def test_standard_values_accepted(ohms):
    assert check_resistance(ohms) is None


@pytest.mark.parametrize("ohms", [0, -5, 5, 1234, 2_000_000, float("inf"), True, "330"])
def test_bad_values_rejected(ohms):
    assert check_resistance(ohms)


def test_color_bands():
    assert color_bands(330) == ["orange", "orange", "brown", "gold"]
    assert color_bands(1000) == ["brown", "black", "red", "gold"]
    assert color_bands(10) == ["brown", "black", "black", "gold"]
    assert color_bands(4700) == ["yellow", "violet", "red", "gold"]
    with pytest.raises(CircuitModelError):
        color_bands(1234)


def test_formatting():
    assert format_ohms(330) == "330 Ω" and format_ohms(1000) == "1,000 Ω" and format_ohms(22000) == "22 kΩ"
    assert format_ma(700) == "700 mA"


# ---------------- display mapping ----------------

def test_flow_speed_and_glow_are_proportional_to_current():
    a, b = solve(LedCircuit()), solve(LedCircuit().with_resistance(1000))
    assert lap_frames(REF_MA) == BASE_LAP_FRAMES
    assert lap_frames(a.current_ma) / lap_frames(b.current_ma) == pytest.approx(b.current_ma / a.current_ma, rel=1e-3)
    assert led_emission(a.current_ma) / led_emission(b.current_ma) == pytest.approx(a.current_ma / b.current_ma,
                                                                                    rel=1e-3)
    assert lap_frames(0) is None and led_emission(0) == 0


def test_labels_come_from_the_model():
    d = display_for(solve(LedCircuit().with_resistance(1000)), caption="hello")
    assert d["labels"]["label_current"] == "I = 7.0 mA"
    assert d["labels"]["label_equation"] == "I = (9 V − 2 V) ÷ 1,000 Ω = 7.0 mA"
    assert "1,000 Ω" in d["labels"]["label_resistor"]
    assert d["resistor_band_names"] == ["brown", "black", "red", "gold"]
    assert set(d["labels"]) == set(LABELS)


def test_requests_match_the_shared_schema():
    for action, ohms in (("build", 330), ("update", 1000)):
        assert validate_request(circuit_request("r1", action, LedCircuit().with_resistance(ohms), "x")) == []
    bad = circuit_request("r1", "build", LedCircuit())
    bad["display"]["labels"]["label_current"] = 5
    assert validate_request(bad)


# ---------------- tool calls ----------------

def test_tool_validation():
    assert validate_tool({"tool": "set_resistance", "args": {"resistance_ohm": 1000}}) == []
    assert validate_tool({"tool": "teach_led_resistor", "args": {}}) == []
    assert validate_tool({"tool": "set_resistance", "args": {}})
    assert validate_tool({"tool": "set_resistance", "args": {"resistance_ohm": "1k"}})
    assert validate_tool({"tool": "run_python", "args": {}})
    assert validate_tool({"tool": "explain", "args": {"question": "why?", "extra": 1}})


def test_choose_tool_retries_once_then_succeeds():
    model = ScriptedModel(['{"tool": "set_resistance", "args": {"ohms": 1000}}',
                           '{"tool": "set_resistance", "args": {"resistance_ohm": 1000}}'])
    call = choose_tool("Change the resistor to 1,000 ohms.", model, LedCircuit(), [], Trace("t", "x"))
    assert call.tool == "set_resistance" and call.args == {"resistance_ohm": 1000} and call.attempts == 2
    assert "rejected" in model.calls[1][-1]["content"]


def test_choose_tool_fails_after_one_retry():
    model = ScriptedModel(["not json", '{"tool": "make_pcb", "args": {}}'])
    with pytest.raises(ToolError):
        choose_tool("design a PCB", model, None, [], Trace("t", "x"))
    assert len(model.calls) == 2


@pytest.mark.parametrize("text,ohms", [("Change the resistor to 1,000 ohms.", 1000), ("Put it back to 330 ohms.", 330),
                                       ("use a 2.2k resistor", 2200), ("Teach me why LEDs need resistors.", None)])
def test_mock_parses_values(text, ohms):
    assert parse_ohms(text) == ohms


# ---------------- explanation number guard ----------------

def _facts(before_r=330, after_r=1000):
    call = ToolCall("set_resistance", {"resistance_ohm": after_r})
    return facts_for(call, "Change the resistor to 1,000 ohms.", solve(LedCircuit().with_resistance(before_r)),
                     solve(LedCircuit().with_resistance(after_r)), built=False)


def test_number_check_allows_rounding_but_not_invention():
    f = _facts()
    ok = ("Increasing the resistance limits the current. With our simplified model, it drops from about 21 "
          "milliamps to 7 milliamps. That's why the LED becomes dimmer.")
    assert number_check(ok, f) == []
    assert number_check("Using a 1 kilohm resistor, about 3 times less current flows.", f) == []
    assert number_check("The current drops to 25 mA and the LED lasts 50,000 hours.", f) == ["25", "50,000"]


def test_template_explanation_passes_its_own_check():
    for f in (_facts(), facts_for(ToolCall("teach_led_resistor", {}), "teach me", None, solve(LedCircuit()), True)):
        assert number_check(template_explanation(f), f) == []


def test_explain_falls_back_to_template_after_two_bad_answers():
    model = ScriptedModel(["It drops to 3 mA.", "It drops to 3.5 mA."])
    expl = explain(_facts(), model, Trace("t", "x"))
    assert expl.source == "template" and len(model.calls) == 2
    assert "7.0 mA" in expl.text and "21.2 mA" in expl.text


def test_explain_accepts_good_text_first_try():
    model = ScriptedModel(["The bigger resistor limits current: about 21 mA becomes 7 mA, so the LED dims."])
    expl = explain(_facts(), model, Trace("t", "x"))
    assert expl.source == "mock" and expl.attempts == 1


# ---------------- full tutor turns (MOCK model + SIMULATED executor) ----------------

def test_three_prompts_update_the_same_scene(capsys):
    sim, model, history = SimulatedCircuitExecutor(), TutorMockModel(), []
    assert run_turn("Teach me why LEDs need resistors.", model, sim, history) == 0
    s1 = sim.inspect()["circuit"]
    assert run_turn("Change the resistor to 1,000 ohms.", model, sim, history) == 0
    s2 = sim.inspect()["circuit"]
    assert run_turn("Put it back to 330 ohms.", model, sim, history) == 0
    s3 = sim.inspect()["circuit"]
    assert s1["labels"]["label_current"] == "I = 21.2 mA"
    assert s2["labels"]["label_current"] == "I = 7.0 mA" and s2["params"]["resistance_ohm"] == 1000
    assert s3["labels"]["label_current"] == "I = 21.2 mA" and s3["params"]["resistance_ohm"] == 330
    uids = [{o["id"]: o["uid"] for o in s["objects"]} for s in (s1, s2, s3)]
    assert uids[0] == uids[1] == uids[2] and set(uids[0]) == set(COMPONENTS + LABELS)
    assert s2["flow"]["lap_frames"] > s1["flow"]["lap_frames"]  # slower markers at lower current
    assert s2["led"]["emission_at_show"] < s1["led"]["emission_at_show"]
    out = capsys.readouterr().out
    assert out.count("12/12 checks passed") == 2 and "MOCK" in out and "SIMULATED" in out


def test_unsupported_value_changes_nothing(capsys):
    sim, model = SimulatedCircuitExecutor(), TutorMockModel()
    run_turn("Teach me why LEDs need resistors.", model, sim, [])
    v = sim.inspect()["scene_version"]
    assert run_turn("Change the resistor to 1234 ohms.", model, sim, []) == 0
    assert sim.inspect()["scene_version"] == v
    assert "can't show that one" in capsys.readouterr().out


def test_clarify_changes_nothing(capsys):
    sim = SimulatedCircuitExecutor()
    model = ScriptedModel(['{"tool": "clarify", "args": {"message": "I can only change the resistor."}}'])
    assert run_turn("Design me a PCB", model, sim, []) == 0
    assert sim.inspect()["circuit"] is None


def test_postcheck_catches_a_stale_label():
    sim = SimulatedCircuitExecutor()
    req = circuit_request("r1", "build", LedCircuit())
    out = sim.send(req)
    out["circuit"]["labels"]["label_current"] = "I = 7.0 mA"  # Blender showing the wrong thing
    failed = [c.what for c in check.run(req, None, out) if not c.ok]
    assert any("recomputed" in w for w in failed) and any("labels" in w for w in failed)


def test_simulated_results_match_scene_result_v1():
    sim = SimulatedCircuitExecutor()
    out = sim.send(circuit_request("r1", "build", LedCircuit()))
    assert validate_result(out) == [] and out["executor"] == "simulated"
    assert json.dumps(out)  # serializable for the bridge
