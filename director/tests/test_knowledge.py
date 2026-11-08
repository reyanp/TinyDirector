"""Knowledge base (P1): SQLite build + validation, graph relationships, retrieval, lesson selection, the
misconception guard, and integration with the Nemotron prompts (MOCK model, SIMULATED engine, offline)."""

from __future__ import annotations

import copy
import json
import sqlite3

import pytest

from blender_engine import sandbox
from director.circuit.agent import ToolError, choose_tool, explain
from director.circuit.mock import TutorMockModel
from director.circuit.sim import SimulatedCircuitExecutor
from director.knowledge import KnowledgeBase, KnowledgeError, build, db, get_kb, registry
from director.lesson import builder as builder_mod, fallback, pipeline
from director.lesson.director_agent import messages_for, write_spec
from director.lesson.mock import MockDirector
from director.lesson.spec import resolve
from director.nemotron_client import ModelReply
from director.tests.test_lesson import FakeRunner
from director.trace import Trace
from director.tutor import run_turn

APPROVED = ["voltage", "current", "resistance", "ohms_law", "power", "series_circuits", "parallel_circuits", "led",
            "forward_voltage", "current_limiting", "transistor", "voltage_divider"]


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return get_kb()


def _seed() -> dict:
    return json.loads(db.SEED.read_text(encoding="utf-8"))


def _build_variant(tmp_path, mutate) -> None:
    seed = _seed()
    mutate(seed)
    path = tmp_path / "seed.json"
    path.write_text(json.dumps(seed), encoding="utf-8")
    build(tmp_path / "kb.sqlite", seed_path=path)


# ---------------- database build ----------------

def test_build_counts_and_fact_status(tmp_path):
    counts = build(tmp_path / "kb.sqlite")
    seed = _seed()
    assert counts["concepts"] == len(seed["concepts"]) >= 12
    assert counts["verified_facts"] == sum(1 for f in seed["facts"] if f.get("check")) >= 5
    con = sqlite3.connect(tmp_path / "kb.sqlite")
    ids = {r[0] for r in con.execute("SELECT id FROM concepts")}
    assert set(APPROVED) <= ids
    for status, check_id, source in con.execute("SELECT status, check_id, source_id FROM facts"):
        assert source  # every fact is cited
        assert (status == "verified") == (check_id is not None)  # verified ONLY when a calculator re-ran it
    demo = dict(con.execute("SELECT id, demo FROM concepts"))
    assert demo["parallel_circuits"] == "explanation_only"  # no calculator / 3D demo for it yet
    assert demo["current_limiting"] == "interactive" and demo["voltage_divider"] == "lesson"
    for (calc,) in con.execute("SELECT calculator FROM templates"):  # every demo has a working calculator
        assert con.execute("SELECT kind FROM checks WHERE id = ?", (calc,)).fetchone()[0] == "calculator"


@pytest.mark.parametrize("mutate, message", [
    (lambda s: s["facts"][next(i for i, f in enumerate(s["facts"]) if f.get("check"))]["check"]["expect"].update(
        I="99.9 mA"), "the fact expects"),
    (lambda s: s["facts"].append({"id": "x", "concept": "led", "kind": "law", "text": "t"}), "needs a known source"),
    (lambda s: s["relations"].append({"src": "voltage", "rel": "requires", "dst": "ohms_law"}), "cycle"),
    (lambda s: s["relations"].append({"src": "voltage", "rel": "requires", "dst": "magic"}), "unknown concept"),
    (lambda s: s["misconceptions"][0].update(trigger="(unclosed"), "bad trigger regex"),
    (lambda s: s["templates"][0].update(calculator="verify.lesson_spec"), "needs a working calculator"),
    (lambda s: s["checks"].append({"id": "calc.magic", "kind": "calculator", "description": "x"}), "registry.CHECKS"),
    (lambda s: s["facts"].append({"id": "y", "concept": "led", "kind": "worked_example", "source": "td_model",
                                  "text": "A 9 V battery and 330 Ω give 50 mA.",
                                  "check": {"id": "calc.led_resistor", "args": {"source_v": 9, "resistance_ohm": 330},
                                            "expect": {"I": "21.2 mA"}}}), "not inputs or outputs"),
])
def test_build_rejects_inconsistent_seed(tmp_path, mutate, message):
    with pytest.raises(KnowledgeError, match=message):
        _build_variant(tmp_path, mutate)
    assert not (tmp_path / "kb.sqlite").exists()  # nothing half-written


def test_ensure_rebuilds_when_seed_changes(tmp_path, monkeypatch):
    path = tmp_path / "kb.sqlite"
    db.ensure(path)
    con = sqlite3.connect(path)
    con.execute("UPDATE meta SET value = 'stale' WHERE key = 'seed_sha256'")
    con.commit()
    con.close()
    db.ensure(path)
    con = sqlite3.connect(path)
    assert con.execute("SELECT value FROM meta WHERE key = 'seed_sha256'").fetchone()[0] == db.seed_hash()


def test_registry_is_the_allowlist():
    assert {c["id"] for c in _seed()["checks"]} == set(registry.CHECKS)
    for cid in registry.CHECKS:
        assert callable(registry.resolve_check(cid))
    with pytest.raises(KeyError):
        registry.resolve_check("os.system")


def test_lesson_templates_build_offline():
    for t in _seed()["templates"]:
        if t["kind"] != "lesson":
            continue
        resolved, _ = resolve(registry.template_spec(t["spec"]))
        assert not sandbox.check(fallback.code_for(resolved), set(builder_mod.TOOLKIT_NAMES)), t["id"]


# ---------------- graph relationships ----------------

def test_graph_prerequisites_and_layout(kb):
    g = kb.graph()
    nodes = {n["id"]: n for n in g["nodes"]}
    requires = {(e["src"], e["dst"]) for e in g["edges"] if e["rel"] == "requires"}
    assert {("ohms_law", "voltage"), ("ohms_law", "current"), ("ohms_law", "resistance"),
            ("current_limiting", "led"), ("voltage_divider", "series_circuits")} <= requires
    assert ("parallel_circuits", "contrasts", "series_circuits") in {(e["src"], e["rel"], e["dst"]) for e in g["edges"]}
    for e in g["edges"]:
        assert e["src"] in nodes and e["dst"] in nodes
        if e["rel"] in ("requires", "is_a"):  # a prerequisite always sits in an earlier column
            assert nodes[e["dst"]]["depth"] < nodes[e["src"]]["depth"]
    assert all(0 <= n["x"] <= 1 and 0 < n["y"] < 1 for n in g["nodes"])
    assert nodes["voltage"]["depth"] == 0 and nodes["current_limiting"]["depth"] == 3


def test_background_is_prerequisites(kb):
    k = kb.retrieve("What is Ohm's law?")
    assert k.ids[0] == "ohms_law"
    assert {c["id"] for c in k.background} == {"voltage", "current", "resistance"}


# ---------------- retrieval + lesson selection ----------------

@pytest.mark.parametrize("question, first, tool, template", [
    ("Teach me why LEDs need resistors.", "current_limiting", "teach_led_resistor", "led_resistor_tutor"),
    ("How does a voltage divider work?", "voltage_divider", "teach_topic", "voltage_divider_lesson"),
    ("How do parallel circuits work?", "parallel_circuits", "explain_concept", None),
    ("What is Ohm's law?", "ohms_law", "teach_topic", "ohms_law_lesson"),
    ("Why would someone use a BJT over a MOSFET?", None, "teach_topic", "bjt_mosfet_lesson"),
    ("Why is it dimmer?", "led", "teach_led_resistor", "led_resistor_tutor"),
])
def test_retrieval_and_demo_selection(kb, question, first, tool, template):
    k = kb.retrieve(question)
    if first:
        assert k.ids[0] == first
    assert k.suggested["tool"] == tool
    assert (k.template or {}).get("id") == template
    assert k.facts and all(f["source"]["title"] and f["status"] in ("verified", "sourced", "assumption")
                           for f in k.facts)
    if template and k.template["kind"] == "lesson":
        assert k.template["spec"]["calculations"]  # the reviewed spec travels with the template


def test_retrieval_phrases_beat_words_and_nothing_for_off_topic(kb):
    k = kb.retrieve("How does a voltage divider work?")
    assert "voltage" not in k.ids  # "voltage divider" hides the plain word
    assert set(kb.retrieve("Why would someone use a BJT over a MOSFET?").ids) >= {"bjt", "mosfet"}
    off = kb.retrieve("How do I bake sourdough bread?")
    assert off.concepts == [] and off.suggested is None and off.prompt_block() == ""


def test_misconception_triggers_and_follow_up_context(kb):
    k = kb.retrieve("Does the LED use up the current?")
    assert k.misconceptions[0]["id"] == "current_used_up" and k.misconceptions[0]["triggered"]
    assert "WRONG:" in k.prompt_block() and "RIGHT:" in k.prompt_block()
    f = kb.retrieve("Explain that in simpler terms.", prior=["parallel_circuits"], follow_up=True)
    assert f.ids == ["parallel_circuits"] and f.from_context
    assert kb.retrieve("Explain that in simpler terms.").concepts == []


def test_director_prompt_uses_template_and_knowledge(kb):
    k = kb.retrieve("How does a voltage divider work?")
    system = messages_for("How does a voltage divider work?", k)[0]["content"]
    assert '"tool": "voltage_divider"' in system and "KNOWLEDGE (" in system
    assert "WRONG: A voltage divider is a good way to power a device" in system
    plain = messages_for("How does a voltage divider work?")[0]["content"]
    assert '"tool": "led_resistor"' in plain and "KNOWLEDGE (" not in plain


def test_pipeline_selects_lesson_from_knowledge(kb, tmp_path):
    q = "How does a voltage divider work?"
    without = pipeline.build_lesson(q, MockDirector(), None, None, cache_dir=tmp_path / "a", runner=FakeRunner(),
                                    log=lambda *a: None)
    assert without.unsupported  # the MOCK director alone has no divider lesson
    events = []
    lesson = pipeline.build_lesson(q, MockDirector(), None, None, cache_dir=tmp_path / "b", runner=FakeRunner(),
                                   log=lambda *a: None, knowledge=kb.retrieve(q),
                                   emit=lambda *a, **k: events.append(a))
    assert lesson.ok and [c["tool"] for c in lesson.spec["calculations"]] == ["voltage_divider"]
    assert lesson.results[lesson.spec["calculations"][0]["id"]]["V_out"] == "4.50 V"  # numbers from the calculator
    assert any(a[0] == "director" and "reference: How a voltage divider" in a[3] for a in events)


# ---------------- claims: retrieval is not verification ----------------

def test_wrong_claims_guard(kb):
    assert [w["id"] for w in kb.wrong_claims("The LED uses up the current. That is why it glows.")] == ["current_used_up"]
    assert kb.wrong_claims("Current is not used up by the LED.") == []  # a correction, not a claim
    assert [w["id"] for w in kb.wrong_claims("A bigger resistance means more current flows.")] == \
        ["more_resistance_more_current"]
    seed = _seed()
    texts = [f["text"] for f in seed["facts"]] + [m["correction"] for m in seed["misconceptions"]]
    texts += [k for t in seed["templates"] if t.get("spec") for k in resolve(registry.template_spec(t["spec"]))[0]["key_points"]]
    for t in texts:  # the knowledge base's own true statements never trip the guard
        assert kb.wrong_claims(t) == [], t


class _Scripted:
    live = False
    model = "scripted (MOCK)"

    def __init__(self, replies):
        self.replies, self.messages = list(replies), []

    def complete(self, messages, *, max_tokens=None):
        self.messages.append(messages)
        return ModelReply(content=self.replies.pop(0), model=self.model, live=False)


def test_explain_rejects_misconception_and_knowledge_numbers(kb):
    facts = {"key_points": ["About 21.2 mA flows around the loop."], "teaching_goal": "x"}
    check = lambda t: [w["id"] for w in kb.wrong_claims(t)]  # noqa: E731
    client = _Scripted(["The LED uses up the current, about 21.2 mA.", "About 21.2 mA flows all the way around."])
    expl = explain(facts, client, Trace("t", "x"), knowledge="Facts:\n- LEDs are rated about 20 mA.", claim_check=check)
    assert expl.attempts == 2 and expl.text.startswith("About 21.2 mA") and "misconception" in client.messages[1][-1]["content"]
    assert "KNOWLEDGE:" in client.messages[0][0]["content"]
    # a number that is only in KNOWLEDGE (not calculated) is rejected: knowledge is not a number source
    client = _Scripted(["LEDs are rated about 20 mA.", "LEDs are rated about 20 mA."])
    assert explain(facts, client, Trace("t", "x"), knowledge="- LEDs are rated about 20 mA.").source == "template"


def test_tool_selector_sees_knowledge_and_checks_concept_ids(kb):
    k = kb.retrieve("How do parallel circuits work?")
    client = _Scripted([json.dumps({"tool": "explain_concept", "args": {"concept": "quantum_tunneling"}})] * 2)
    with pytest.raises(ToolError) as err:
        choose_tool("How do parallel circuits work?", client, None, [], Trace("t", "x"), knowledge=k)
    assert "unknown concept 'quantum_tunneling'" in " ".join(err.value.errors)
    assert "<knowledge>" in client.messages[0][1]["content"] and "explain_concept" in client.messages[0][1]["content"]
    call = choose_tool("How do parallel circuits work?", TutorMockModel(), None, [], Trace("t", "x"), knowledge=k)
    assert call.tool == "explain_concept" and call.args == {"concept": "parallel_circuits"}


# ---------------- tutor turns (MOCK + SIMULATED) ----------------

def _turn(text, sim, ctx, model=None):
    events = []
    code = run_turn(text, model or TutorMockModel(), sim, [], context=ctx,
                    emit=lambda *a, **k: events.append((a, k)))
    return code, events, next(k for a, k in events if a[0] == "turn")


def test_explain_concept_turn_is_grounded_and_honest(tmp_path):
    sim, ctx = SimulatedCircuitExecutor(tmp_path / "s.json"), {}
    code, events, done = _turn("How do parallel circuits work?", sim, ctx)
    assert code == 0 and not done["scene_changed"]
    stages = [(a[0], a[1]) for a, _ in events]
    assert stages.index(("knowledge", "ok")) < stages.index(("plan", "ok")) < stages.index(("explain", "ok"))
    kev = next(k for a, k in events if a[0] == "knowledge" and a[1] == "ok")
    assert kev["concepts"][0]["id"] == "parallel_circuits" and kev["suggested"]["tool"] == "explain_concept"
    plan = next(k for a, k in events if a[0] == "plan" and a[1] == "ok")
    assert plan["tool"] == "explain_concept"
    g = done["grounding"]
    assert g["concepts"][0]["id"] == "parallel_circuits" and g["given_as_facts"]
    assert g["numbers"] in ("none", "cited")  # never "calculated": nothing was calculated
    assert all(f["status"] != "verified" for f in g["facts"])
    assert "parallel" in done["reply"].lower()
    assert any(a[0] == "apply" and "no 3D demo" in a[3] for a, _ in events)
    # a follow-up stays on the same concept (the knowledge base, not the LED circuit)
    code, events, done = _turn("Explain that in simpler terms.", sim, ctx)
    assert code == 0 and done["grounding"]["concepts"][0]["id"] == "parallel_circuits"
    assert ctx["last_kind"] == "concept"


def test_circuit_turn_carries_calculated_grounding(tmp_path):
    sim, ctx = SimulatedCircuitExecutor(tmp_path / "s.json"), {}
    recorder = _Recorder(TutorMockModel())
    code, events, done = _turn("Teach me why LEDs need resistors.", sim, ctx, recorder)
    assert code == 0 and done["scene_changed"]
    g = done["grounding"]
    assert g["numbers"] == "calculated" and g["misconception_check"]
    assert {c["id"] for c in g["concepts"]} >= {"current_limiting"}
    selector = next(m for m in recorder.seen if m[0]["content"].startswith("TOOL_SELECTOR"))
    explainer = next(m for m in recorder.seen if m[0]["content"].startswith("EXPLAINER"))
    assert "<knowledge>" in selector[1]["content"] and "teach_led_resistor" in selector[1]["content"]
    assert "KNOWLEDGE:" in explainer[0]["content"] and "KNOWLEDGE" not in explainer[1]["content"]
    assert ctx["concepts"][0] == "current_limiting"


class _Recorder:
    live = False

    def __init__(self, inner):
        self.inner, self.seen, self.model = inner, [], inner.model

    def complete(self, messages, *, max_tokens=None):
        self.seen.append(messages)
        return self.inner.complete(messages, max_tokens=max_tokens)


def test_topic_follow_up_uses_the_lessons_calculations(tmp_path):
    sim = SimulatedCircuitExecutor(tmp_path / "s.json")
    ctx = {"topic": {"title": "BJT vs MOSFET as an LED switch", "concepts": ["bjt", "mosfet"],
                     "key_points": ["A BJT needs a steady base current (2.4 mA here) to stay on.",
                                    "A MOSFET's insulated gate needs voltage, not steady current (≈ 0 mA)."],
                     "calculations": {"sw": {"I_B_design": "2.4 mA", "I_G": "≈ 0 mA"}}},
           "last_kind": "topic", "last_explanation": "BJTs need base current; MOSFETs need gate voltage.",
           "lesson": {"kind": "topic", "title": "BJT vs MOSFET as an LED switch"}}
    code, events, done = _turn("Why does one of them need current to stay on?", sim, ctx)
    assert code == 0 and not done["scene_changed"] and "2.4 mA" in done["reply"]
    assert done["grounding"]["numbers"] == "calculated"
    assert {c["id"] for c in done["grounding"]["concepts"]} & {"bjt", "mosfet"}
    assert any(a[0] == "apply" and "lesson on screen" in a[3] for a, _ in events)


def test_server_tracks_discussed_concepts_and_serves_the_map(tmp_path):
    from director import server
    from director.tests.test_server import _req, _turn as server_turn
    import threading
    session = server.Session(TutorMockModel(), SimulatedCircuitExecutor(tmp_path / "sim.json"), model_label="MOCK",
                             executor_label="SIMULATED", live=False, simulated=True)
    httpd = server.serve(session, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        s1 = server_turn(base, "Teach me why LEDs need resistors.")
        s2 = server_turn(base, "How do parallel circuits work?")
        assert s2["knowledge"]["active"] == ["parallel_circuits"]
        assert "current_limiting" in s2["knowledge"]["discussed"] and "parallel_circuits" in s2["knowledge"]["discussed"]
        assert s1["messages"][-1]["grounding"]["numbers"] == "calculated"
        assert s2["lesson"]["title"] == "Why does an LED need a resistor?"  # the concept answer left the scene alone
        code, body = _req(f"{base}/api/knowledge")
        g = json.loads(body)
        assert code == 200 and len(g["nodes"]) >= 12 and g["edges"]
        html = _req(f"{base}/")[1].decode()
        assert '"graph": {"nodes"' in html and 'id="kmap"' in html
        assert _req(f"{base}/api/reset", {})[0] == 200
        assert json.loads(_req(f"{base}/api/state")[1])["knowledge"] == {"active": [], "discussed": []}
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_mock_director_without_knowledge_is_unchanged():
    spec = write_spec("Why does an LED need a resistor?", MockDirector()).spec
    assert spec["calculations"][0]["tool"] == "led_resistor"
    k = copy.deepcopy(get_kb().retrieve("What is Ohm's law?"))
    assert write_spec("What is Ohm's law?", MockDirector(), knowledge=k).spec["calculations"][0]["tool"] == "ohms_law"


def test_follow_up_after_topic_stays_on_the_topic_in_simulation(tmp_path):
    """Regression: the SIMULATED engine still holds the earlier LED circuit (it cannot display lessons), so a
    follow-up after a topic lesson must follow the conversation, not that stale circuit."""
    sim, ctx = SimulatedCircuitExecutor(tmp_path / "s.json"), {}
    _turn("Teach me why LEDs need resistors.", sim, ctx)
    ctx.update(topic={"title": "How a voltage divider splits a voltage", "concepts": ["voltage_divider"],
                      "key_points": ["R2 gets the output: 4.50 V."], "calculations": {"vd": {"V_out": "4.50 V"}}},
               last_kind="topic", last_explanation="R2 gets the output: 4.50 V.")
    code, events, done = _turn("Explain that in simpler terms.", sim, ctx)
    assert code == 0 and "4.50 V" in done["reply"] and "LED" not in done["reply"]
    assert done["grounding"]["concepts"][0]["id"] == "voltage_divider"


def test_retrieval_keeps_a_verified_example(kb):
    k = kb.retrieve("How does a voltage divider work?")
    assert any(f["id"] == "divider_example" and f["status"] == "verified" for f in k.facts)
