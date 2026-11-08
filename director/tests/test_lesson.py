"""Offline tests for the multi-agent lesson pipeline (director spec, builder code, sandbox, verifier).

No network and no Blender: the Blender runner is replaced by a fake that writes a synthetic read-back.
"""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

from blender_engine import sandbox
from director.circuit.agent import TOOLS, validate_tool
from director.circuit.display import TITLE, title_for
from director.circuit.mock import TutorMockModel
from director.circuit.model import LedCircuit
from director.lesson import builder, calculators, director_agent, fallback, pipeline, verify
from director.lesson.examples import BJT_MOSFET_SPEC, LED_SPEC
from director.lesson.mock import MockBuilder, MockDirector
from director.lesson.spec import resolve, validate
from director.mock_model import ScriptedModel

ROOT = Path(__file__).resolve().parents[2]


# ---------------- calculators ----------------

def test_led_resistor_numbers():
    r = calculators.run("led_resistor", {"source_v": 9, "resistance_ohm": 330, "led_vf": 2.0})
    assert r["I"] == "21.2 mA" and r["V_R"] == "7.0 V" and abs(r["I_ma"] - 21.2121) < 1e-3
    assert calculators.run("led_resistor", {"resistance_ohm": 1000})["I"] == "7.0 mA"


def test_transistor_switch_defaults():
    r = calculators.run("transistor_switch", {})
    assert r["I_C"] == "20.6 mA" and r["R_B"] == "1,800 Ω" and r["I_B_design"] == "2.4 mA"
    assert r["I_G_ma"] == 0.0 and r["I_D"] == "21.2 mA"


def test_ohms_law_and_divider():
    assert calculators.run("ohms_law", {"source_v": 9, "resistance_ohm": 1000})["I"] == "9.0 mA"
    d = calculators.run("voltage_divider", {"source_v": 9, "r1_ohm": 10000, "r2_ohm": 10000})
    assert d["V_out"] == "4.50 V" and d["ratio"] == "0.50"


@pytest.mark.parametrize("tool,args", [("led_resistor", {"resistance_ohm": -5}), ("led_resistor", {"bogus": 1}),
                                       ("nope", {}), ("ohms_law", {"source_v": True}),
                                       ("transistor_switch", {"supply_v": 2})])
def test_calculator_rejects(tool, args):
    with pytest.raises(calculators.CalcError):
        calculators.run(tool, args)


def test_e12_rounding():
    assert calculators._e12_at_most(2090) == 1800 and calculators._e12_at_most(330) == 330


# ---------------- spec validation ----------------

@pytest.mark.parametrize("spec", [LED_SPEC, BJT_MOSFET_SPEC])
def test_examples_validate(spec):
    errs, _ = validate(spec)
    assert errs == []


def _bad(mutate) -> list[str]:
    s = copy.deepcopy(LED_SPEC)
    mutate(s)
    return validate(s)[0]


def test_spec_rejects_bare_numbers():
    errs = _bad(lambda s: s["labels"][0].update(text="drops 7 V"))
    assert any("numbers must come from calculator" in e for e in errs)


def test_spec_allows_part_numbers():
    assert _bad(lambda s: s["parts"][1].update(label="2N3904 / TO-92 {c.R}")) == []


def test_spec_rejects_wrong_pin_and_unknown_output():
    assert any("has no pin" in e for e in _bad(lambda s: s["connections"][0].update(to="r1.c")))
    assert any("not an output" in e for e in _bad(lambda s: s["labels"][0].update(text="{c.nope}")))


def test_spec_lint_shorted_battery():
    errs = _bad(lambda s: s["connections"].append({"id": "w9", "from": "bat.+", "to": "bat.-"}))
    assert any("short" in e for e in errs)


def test_spec_flow_must_be_continuous_and_ma():
    def m(s):
        s["connections"].append({"id": "w4", "from": "bat.+", "to": "led1.cathode"})
        s["flows"][0]["path"] = ["w2", "w4"]
    assert any("not continuous" in e or "short" in e for e in _bad(m))
    assert any("_ma output" in e for e in _bad(lambda s: s["flows"][0].update(current_ma="{c.I}")))


def test_resolve_fills_numbers():
    res, results = resolve(LED_SPEC)
    assert res["flows"][0]["current_ma"] == pytest.approx(21.2121, abs=1e-3)
    assert res["labels"][0]["text"] == "drops 7.0 V" and "{" not in json.dumps(res["key_points"])
    assert results["c"]["I"] == "21.2 mA"


# ---------------- sandbox ----------------

@pytest.mark.parametrize("code", [
    "import os", "x = (1).__class__", "open('f')", "while True:\n    pass", "eval('1')", "f = lambda: 1",
    "'{}'.format(1)", "getattr(scene, 'x')", "_x = 1", "[scene][0]('t')", "try:\n    pass\nexcept Exception:\n    pass",
    "class A:\n    pass", "bpy = 1", "with x:\n    pass"])
def test_sandbox_rejects(code):
    assert sandbox.check(code, set(builder.TOOLKIT_NAMES))


def test_sandbox_allows_toolkit_code():
    code = ("scene('t')\ndef row(n):\n    return [i * 1.5 for i in range(n)]\nfor i, x in enumerate(row(3)):\n"
            "    part(f'r{i}', 'resistor', x=x, y=round(sin(0.5), 2))\n")
    assert sandbox.check(code, set(builder.TOOLKIT_NAMES)) == []


def test_sandbox_runs_with_limited_scope():
    calls = []
    sandbox.run("for i in range(3):\n    rec(i * 2)\n", {"rec": calls.append})
    assert calls == [0, 2, 4]
    with pytest.raises(sandbox.SandboxError):
        sandbox.run("for i in range(10 ** 6):\n    rec(i)\n", {"rec": calls.append})


@pytest.mark.parametrize("spec", [LED_SPEC, BJT_MOSFET_SPEC])
def test_fallback_code_passes_sandbox(spec):
    code = fallback.code_for(resolve(spec)[0])
    assert sandbox.check(code, set(builder.TOOLKIT_NAMES)) == []
    assert code.startswith("scene(") and code.count("wire(") == len(spec["connections"])


# ---------------- builder prompt / parsing ----------------

def test_builder_names_match_toolkit_api():
    tree = ast.parse((ROOT / "blender_engine" / "toolkit.py").read_text(encoding="utf-8"))
    api = next(n for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "API")
    assert {k.value for k in api.value.keys} == set(builder.TOOLKIT_NAMES)
    prompt = builder.system_prompt()
    for name in builder.TOOLKIT_NAMES:
        assert f"{name}(" in prompt
    assert "{colors}" not in prompt and "{catalog}" not in prompt


def test_extract_code():
    assert builder.extract_code("hi\n```python\nscene('a')\n```\nbye") == "scene('a')\n"
    with pytest.raises(ValueError):
        builder.extract_code("no code here")


def test_builder_requires_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from director.nemotron_client import ConfigError
    with pytest.raises(ConfigError):
        builder.OpenAIBuilderClient()


# ---------------- director agent ----------------

def test_director_mock_and_unsupported():
    assert director_agent.write_spec("Why would someone use a BJT over a MOSFET?", MockDirector()).spec["title"] \
        == BJT_MOSFET_SPEC["title"]
    r = director_agent.write_spec("How do volcanoes work?", MockDirector())
    assert r.spec is None and r.unsupported


def test_director_one_retry_then_ok():
    bad = copy.deepcopy(LED_SPEC)
    bad["labels"][0]["text"] = "drops 7 V"
    model = ScriptedModel([json.dumps(bad), json.dumps(LED_SPEC)])
    r = director_agent.write_spec("Why does an LED need a resistor?", model)
    assert r.spec is not None and len(r.replies) == 2


def test_director_gives_up_after_one_retry():
    model = ScriptedModel(["not json", json.dumps({"title": "x"})])
    with pytest.raises(director_agent.SpecError):
        director_agent.write_spec("q", model)


# ---------------- verifier on synthetic read-backs ----------------

def fake_readback(resolved: dict) -> dict:
    """What a correct build would read back: parts on a wide grid, wires on their pins, honest flows."""
    rb = {"ok": True, "title": resolved["title"], "parts": {}, "wires": {}, "flows": {}, "labels": {}, "hud": {},
          "hud_screen": {}, "frames": [1, 400]}
    from blender_engine.lesson_parts import pins_for
    n = len(resolved["parts"])
    for i, p in enumerate(resolved["parts"]):
        x = i * 3.0
        pins = {pin: [x + 0.1 * k, 0.0, 0.03] for k, pin in enumerate(pins_for(p))}
        sx = 0.9 / n
        rb["parts"][p["id"]] = {"type": p["type"], "pins": pins, "bbox": [[x - 0.4, -0.3, 0], [x + 0.4, 0.3, 1]],
                                "screen": [0.05 + i * sx, 0.3, 0.05 + i * sx + sx * 0.8, 0.4]}
        rb["labels"][f"{p['id']}_label"] = {"text": p["label"], "near": p["id"],
                                            "screen": [0.05 + i * sx, 0.45, 0.05 + i * sx + sx * 0.8, 0.48]}
    for c in resolved["connections"]:
        a = rb["parts"][c["from"].split(".")[0]]["pins"][c["from"].split(".")[1]]
        b = rb["parts"][c["to"].split(".")[0]]["pins"][c["to"].split(".")[1]]
        rb["wires"][c["id"]] = {"from": c["from"], "to": c["to"], "start": a, "end": b,
                                "points": [a, [a[0], -2, 0.14], [b[0], -2, 0.14], b]}
    for i, f in enumerate(resolved["flows"]):
        speed = min(verify.MAX_M_PER_FRAME, verify.FLOW_M_PER_FRAME_PER_MA * f["current_ma"])
        rb["flows"][f["id"]] = {"path": f["path"], "current_ma": f["current_ma"], "laps_per_frame": speed / 10.0,
                                "markers": 20, "length_m": 10.0}
        rb["labels"][f"{f['id']}_label"] = {"text": f["label"], "near": None,
                                            "screen": [0.05 + 0.2 * i, 0.55, 0.2 + 0.2 * i, 0.58]}
    for i, lab in enumerate(resolved["labels"]):
        rb["labels"][lab["id"]] = {"text": lab["text"], "near": lab["near"],
                                   "screen": [0.05 + 0.3 * i, 0.62, 0.3 + 0.3 * i, 0.65]}
    rb["hud"]["hud_title"] = resolved["title"]
    rb["hud"]["hud_key_points"] = "\n".join(f"•  {k}" for k in resolved["key_points"])
    for i, s in enumerate(resolved["steps"]):
        rb["hud"][f"hud_caption_{i + 1}"] = s["caption"]
    rb["hud_screen"] = {k: [0.0, 0.9, 0.3, 1.0] for k in rb["hud"]}
    return rb


@pytest.mark.parametrize("spec", [LED_SPEC, BJT_MOSFET_SPEC])
def test_verify_accepts_correct_build(spec):
    res, results = resolve(spec)
    rep = verify.verify(res, results, fake_readback(res))
    assert rep.ok, rep.errors


def _verify_with(mutate):
    res, results = resolve(LED_SPEC)
    rb = fake_readback(res)
    mutate(rb)
    return verify.verify(res, results, rb).errors


def test_verify_catches_wire_off_pin():
    assert any("does not touch" in e for e in _verify_with(lambda rb: rb["wires"]["w1"].update(end=[9, 9, 9])))


def test_verify_catches_wrong_current_and_speed():
    assert any("calculation says" in e for e in _verify_with(lambda rb: rb["flows"]["f1"].update(current_ma=50.0)))
    assert any("not proportional" in e for e in
               _verify_with(lambda rb: rb["flows"]["f1"].update(laps_per_frame=0.5)))


def test_verify_catches_missing_part_wrong_label_and_invented_number():
    assert any("missing" in e for e in _verify_with(lambda rb: rb["parts"].pop("r1")))
    assert any("must show its label" in e for e in
               _verify_with(lambda rb: rb["labels"]["r1_label"].update(text="470 Ω")))
    errs = _verify_with(lambda rb: rb["labels"].update(extra={"text": "about 99 mA", "near": None,
                                                              "screen": [0.8, 0.1, 0.9, 0.12]}))
    assert any("not from the calculation" in e for e in errs)


def test_verify_layout_overlap_and_through():
    def m(rb):
        rb["parts"]["led1"]["bbox"] = rb["parts"]["bat"]["bbox"]  # overlap; w3 still runs through r1
        rb["wires"]["w3"]["points"] = [[0, 0, 0], [0, 0, 0.1], [3.0, 0.0, 0.1], [3.0, 0.0, 0.1], [6, 0, 0.1],
                                       [6, 0, 0]]
    errs = _verify_with(m)
    assert any("parts overlap" in e for e in errs) and any("through" in e for e in errs)
    res, results = resolve(LED_SPEC)
    rb = fake_readback(res)
    m(rb)
    assert verify.verify(res, results, rb, strict_layout=False).ok  # fallback: layout -> warnings


# ---------------- pipeline (fake Blender runner) ----------------

class FakeRunner:
    """Stands in for the headless Blender build: honest read-back unless the code lacks a via=."""

    def __init__(self, resolved_layout_bad=False):
        self.calls = []
        self.layout_bad = resolved_layout_bad

    def __call__(self, code_path: Path, out_dir: Path) -> dict:
        self.calls.append(code_path.name)
        out_dir.mkdir(parents=True, exist_ok=True)
        spec = json.loads((code_path.parent / "resolved_spec.json").read_text(encoding="utf-8"))["spec"]
        rb = fake_readback(spec)
        if self.layout_bad and "GOOD" not in code_path.read_text(encoding="utf-8"):
            first = next(iter(rb["parts"].values()))
            for p in rb["parts"].values():
                p["bbox"] = first["bbox"]
        (out_dir / "readback.json").write_text(json.dumps(rb), encoding="utf-8")
        (out_dir / "lesson.blend").write_bytes(b"BLENDER-fake")
        return rb


def test_pipeline_builder_accepted(tmp_path):
    runner = FakeRunner()
    lesson = pipeline.build_lesson("Why does an LED need a resistor?", MockDirector(), MockBuilder(), None,
                                   cache_dir=tmp_path, runner=runner, log=lambda *a: None)
    assert lesson.ok and lesson.builder == "MOCK builder" and runner.calls == ["code_agent1.py"]
    assert (lesson.out_dir / "lesson.blend").exists() and (lesson.out_dir / "lesson.json").exists()
    again = pipeline.build_lesson("why does an LED need a resistor", MockDirector(), MockBuilder(), None,
                                  cache_dir=tmp_path, runner=runner, log=lambda *a: None)
    assert again.cached and runner.calls == ["code_agent1.py"]


def test_pipeline_repair_then_ok(tmp_path):
    draft = fallback.code_for(resolve(LED_SPEC)[0])
    mb = MockBuilder(replies=[f"```python\n{draft}```", f"```python\n# GOOD\n{draft}```"])
    runner = FakeRunner(resolved_layout_bad=True)
    lesson = pipeline.build_lesson("Why does an LED need a resistor?", MockDirector(), mb, None,
                                   cache_dir=tmp_path, runner=runner, log=lambda *a: None)
    assert lesson.ok and lesson.builder == "MOCK builder" and runner.calls == ["code_agent1.py", "code_agent2.py"]


def test_pipeline_bad_builder_falls_back(tmp_path):
    mb = MockBuilder(replies=["```python\nimport os\n```", "sorry, no code"])
    runner = FakeRunner()
    lesson = pipeline.build_lesson("Why does an LED need a resistor?", MockDirector(), mb, None,
                                   cache_dir=tmp_path, runner=runner, log=lambda *a: None)
    assert lesson.ok and lesson.builder == "deterministic" and runner.calls == ["code_fallback.py"]
    assert any("fallback" in n for n in lesson.notes) and mb.calls == 2


def test_pipeline_no_builder_and_unsupported(tmp_path):
    lesson = pipeline.build_lesson("Why would someone use a BJT over a MOSFET?", MockDirector(), None, None,
                                   cache_dir=tmp_path, runner=FakeRunner(), log=lambda *a: None)
    assert lesson.ok and lesson.builder == "deterministic" and "OPENAI_API_KEY" in lesson.notes[0]
    out = pipeline.build_lesson("Tell me about volcanoes", MockDirector(), None, None, cache_dir=tmp_path,
                                runner=FakeRunner(), log=lambda *a: None)
    assert not out.ok and out.unsupported


def test_pipeline_build_failure_raises(tmp_path):
    def broken(code_path, out_dir):
        return {"ok": False, "error": "boom"}
    with pytest.raises(pipeline.LessonError):
        pipeline.build_lesson("Why does an LED need a resistor?", MockDirector(), None, None, cache_dir=tmp_path,
                              runner=broken, log=lambda *a: None)


def test_cache_key_normalizes():
    assert pipeline.cache_key("Why does an LED need a resistor?") == pipeline.cache_key("why does an  LED need a resistor")


# ---------------- tutor integration ----------------

def test_teach_topic_tool_and_mock_routing():
    assert "teach_topic" in TOOLS
    assert validate_tool({"tool": "teach_topic", "args": {"question": "BJT vs MOSFET?"}}) == []
    assert validate_tool({"tool": "teach_topic", "args": {}})
    reply = TutorMockModel()._tool("Why would someone use a BJT over a MOSFET?", "circuit present")
    assert reply["tool"] == "teach_topic"


def test_title_follows_the_turn():
    assert title_for("teach_led_resistor", "teach me") == TITLE
    assert title_for("explain", "what if I remove the resistor?") == "What if I remove the resistor?"
    assert title_for("set_resistance", "", LedCircuit(resistance_ohm=1000)) == "What if the resistor is 1,000 Ω?"
    assert len(title_for("explain", "x" * 200)) <= 56


def test_spec_rejects_doubled_units():
    errs = _bad(lambda s: s["parts"][0].update(label="{c.V_source} V battery"))
    assert any("already include their unit" in e for e in errs)
    assert _bad(lambda s: s["parts"][0].update(label="{c.V_source} battery")) == []


def test_lint_calculation_must_match_drawn_circuit():
    errs = _bad(lambda s: s["parts"][1].update(label="resistor"))  # R no longer shown on the resistor
    assert any("assumes R" in e for e in errs)
    s = copy.deepcopy(BJT_MOSFET_SPEC)
    s["parts"] = [p for p in s["parts"] if p["id"] != "rb"]
    s["connections"] = [c for c in s["connections"] if c["id"] not in ("w5", "w6")] + [
        {"id": "w5", "from": "mcu.out", "to": "q1.B"}]
    s["flows"] = [f for f in s["flows"] if f["id"] != "f_b"]
    s["steps"][1]["show"] = [i for i in s["steps"][1]["show"] if i not in ("rb", "w6", "f_b")]
    s["key_points"] = s["key_points"][1:]
    assert any("assumes R_B" in e for e in validate(s)[0])


def test_lint_led_needs_series_resistor():
    def m(s):  # bypass the resistor: battery straight to the LED
        s["connections"][0]["to"] = "led1.anode"
        s["connections"][1] = {"id": "w2", "from": "r1.a", "to": "r1.b"}
        s["flows"][0]["path"] = ["w1", "w3"]
    errs = _bad(m)
    assert any("no series resistor" in e or "shorted" in e for e in errs)


def test_lint_no_steady_flow_through_capacitor():
    s = copy.deepcopy(LED_SPEC)
    s["calculations"] = [{"id": "c", "tool": "ohms_law", "args": {"source_v": 9, "resistance_ohm": 1000}}]
    s["parts"] = [{"id": "bat", "type": "battery", "label": "{c.V_source}"},
                  {"id": "r1", "type": "resistor", "label": "{c.R}"}, {"id": "c1", "type": "capacitor", "label": "cap"}]
    s["connections"] = [{"id": "w1", "from": "bat.+", "to": "r1.a"}, {"id": "w2", "from": "r1.b", "to": "c1.+"},
                        {"id": "w3", "from": "c1.-", "to": "bat.-"}]
    s["labels"] = []
    s["key_points"] = ["A capacitor charges."]
    s["steps"] = [{"show": ["bat", "r1", "c1"], "caption": "Charge it."}]
    assert any("capacitor" in e for e in validate(s)[0])


def test_spec_allows_zero():
    assert _bad(lambda s: s["labels"][0].update(text="the - side is 0 V")) == []


def test_lint_calculation_requires_its_parts():
    s = copy.deepcopy(LED_SPEC)
    s["parts"][2] = {"id": "led1", "type": "switch", "label": "switch"}
    s["connections"][1]["to"], s["connections"][2]["from"] = "led1.a", "led1.b"
    s["labels"][0]["text"] = "drops {c.V_R}"
    s["key_points"] = ["{c.I}"]
    assert any("models a circuit with a led" in e for e in validate(s)[0])


def test_lint_question_components_must_be_shown():
    s = copy.deepcopy(BJT_MOSFET_SPEC)
    assert validate(s)[0] == []
    s["question"] = "Why use a capacitor here?"
    assert any("no capacitor part" in e for e in validate(s)[0])
