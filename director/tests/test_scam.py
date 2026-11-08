"""Spot the Scam (director/scam/): heuristic detector, requests + schema, read-back checks, tool routing, the
5-turn lesson with the MOCK model + SIMULATED engine, knowledge base, and that the electronics tutor still works."""

from __future__ import annotations

import copy
import json

import pytest

from director import tutor
from director.circuit.agent import validate_tool
from director.circuit.mock import TutorMockModel
from director.circuit.sim import SimulatedCircuitExecutor
from director.knowledge import get_kb
from director.scam import check, scenario
from director.scam.detector import NAME, detect, detect_scenario
from director.scam.evaluate import evaluate
from director.scam.lesson import lesson_rules, validate_request

TURNS = ["Poyo, teach me how to recognize scam texts.", "What makes that message suspicious?",
         "What if I follow the link?", "What should I do instead?", "What if I already typed my password?"]


# ---------------- detector (HEURISTIC PROTOTYPE) ----------------

def test_detector_on_the_lesson_message():
    d = detect_scenario()
    assert d.category == "likely scam" and d.detector == NAME and "not a trained model" in d.detector
    assert {i.id: i.target for i in d.indicators} == {
        "unknown_sender": "sender", "suspicious_link": "link", "impersonation": "brand", "alarm": "body",
        "urgency": "urgency", "threat": "urgency", "verify_request": "urgency"}
    assert "no calibrated probability" in d.uncertainty
    assert "%" not in json.dumps(d.as_dict())  # never a made-up confidence
    assert any("7726" in a for a in d.actions)


def test_detector_categories_and_honest_eval():
    assert detect("Hey, are we still on for lunch at noon?").category == "no warning signs found"
    assert detect("Your verification code is 482913. Don't share this code with anyone.").category != "likely scam"
    r = evaluate()
    assert r["n"] == 30 and r["confusion"] == {"tp": 12, "fp": 0, "fn": 3, "tn": 15}
    assert "NOT T-Mobile data" in r["dataset"] and "not a real-world accuracy" in r["caveat"]


# ---------------- requests, schema, read-back checks ----------------

def test_requests_validate_and_are_fictional():
    reqs = [scenario.build_request("b1"), scenario.mark_request(detect_scenario(), "m1"),
            scenario.outcomes_request("follow_link", "o1"), scenario.clear_outcomes_request("c1")]
    for r in reqs:
        assert validate_request(r) == [], r["action"]
    assert scenario.SENDER.split("-")[0].endswith("555") and ".example/" in scenario.LINK
    assert "FICTIONAL" in scenario.BANNER and all("hypothetical simulation" in o["headline"]
                                                  for o in scenario.OUTCOMES.values())
    assert validate_request({**reqs[2], "choice": "pay_the_fee"})  # only the two choices
    with pytest.raises(ValueError):
        scenario.outcomes_request("pay_the_fee")


def test_readback_check_catches_a_changed_message():
    ex = SimulatedCircuitExecutor()
    req = scenario.build_request("b1")
    out = ex.send(req)
    assert all(c.ok for c in check.run(req, None, out))
    bad = copy.deepcopy(out)
    bad["scam"]["segments"]["link"] = "https://real-carrier.example"
    assert not all(c.ok for c in check.run(req, None, bad))
    mark = scenario.mark_request(detect_scenario(), "m1")
    before = ex.inspect()
    out = ex.send(mark)
    assert all(c.ok for c in check.run(mark, before, out))
    out["scam"]["tags"].pop("link")
    assert any(not c.ok and "tags" in c.what for c in check.run(mark, before, out))


# ---------------- tool calls ----------------

def test_scam_tool_schema():
    assert validate_tool({"tool": "scam_choose", "args": {"choice": "follow_link"}}) == []
    assert validate_tool({"tool": "scam_choose", "args": {"choice": "reply_stop"}})
    assert validate_tool({"tool": "teach_scam_texts", "args": {}}) == []
    assert validate_tool({"tool": "scam_inspect", "args": {"indicators": ["link"]}})  # the detector decides


def test_lesson_rules_reject_unsafe_or_overconfident_text():
    assert lesson_rules("Tap the link to check your account.")
    assert lesson_rules("This is definitely a scam.")
    assert lesson_rules("Our AI detected 7 warning signs.")
    assert lesson_rules("Don't tap the link; open the official app instead.") == []
    assert lesson_rules("If you tap the link, a look-alike page asks for your PIN.") == []
    assert lesson_rules("Scam texts copy real alerts.Scam texts copy real alerts.")  # repeats itself


# ---------------- the lesson, MOCK model + SIMULATED engine ----------------

def _run(texts, ex=None):
    ex = ex or SimulatedCircuitExecutor()
    client, history, context, turns = TutorMockModel(), [], {}, []
    for t in texts:
        events = []
        code = tutor.run_turn(t, client, ex, history, emit=lambda *a, **k: events.append((a, k)), context=context)
        plan = next(k for a, k in events if a[0] == "plan" and a[1] == "ok")
        turn = next(k for a, k in events if a[0] == "turn")
        turns.append({"code": code, "tool": plan["tool"], "args": plan["args"], "turn": turn, "events": events})
    return ex, context, turns


def test_five_turn_lesson_mock_simulated():
    ex, context, turns = _run(TURNS)
    assert [t["code"] for t in turns] == [0] * 5
    assert [t["tool"] for t in turns] == ["teach_scam_texts", "scam_inspect", "scam_choose", "scam_choose",
                                          "scam_recover"]
    assert turns[2]["args"] == {"choice": "follow_link"} and turns[3]["args"] == {"choice": "verify_official"}
    first = turns[0]["turn"]
    assert first["reply"].endswith(scenario.QUESTION) and first["title"] == scenario.TITLE
    assert first["lesson"]["chips"][:2] == ["FICTIONAL message", "Heuristic: likely scam · 7 signs"]
    # every scene change was checked against the read-back
    for t in turns[:4]:
        v = [k for a, k in t["events"] if a[0] == "verify"]
        assert v and v[-1]["failed"] == 0
    # the warning signs walk the camera over the message parts, in order
    foci = [c["focus"] for c in turns[1]["turn"]["stage"]["cues"] if c["focus"]]
    assert [f for i, f in enumerate(foci) if i == 0 or foci[i - 1] != f] == ["sender", "brand", "body", "urgency",
                                                                            "link"]
    assert turns[2]["turn"]["stage"]["cues"][0]["focus"] == "outcome_a"
    assert turns[3]["turn"]["stage"]["cues"][0]["focus"] == "outcome_b"
    assert "7726" in turns[3]["turn"]["reply"] and "ReportFraud" not in turns[3]["turn"]["reply"]
    assert "password" in turns[4]["turn"]["reply"].lower() and turns[4]["turn"]["scene_changed"] is False
    sc = ex.inspect()["scam"]
    assert sc["outcomes"]["choice"] == "verify_official" and set(sc["tags"]) == {"sender", "brand", "body",
                                                                                   "urgency", "link"}
    assert all(t["turn"]["source"] == "mock" for t in turns)  # labeled MOCK, never presented as Nemotron
    assert all(k.get("model", "").startswith("MOCK") for t in turns for a, k in t["events"] if a[0] == "plan"
               and a[1] == "ok")


def test_back_to_message_and_electronics_still_work():
    ex, context, turns = _run(TURNS[:3] + ["Go back to the message.", "Teach me why LEDs need resistors."])
    assert [t["tool"] for t in turns[3:]] == ["end_comparison", "teach_led_resistor"]
    assert [t["code"] for t in turns] == [0] * 5
    state = ex.inspect()
    assert "scam" not in state and state["circuit"]["present"]  # building the circuit cleared the phone
    assert turns[4]["turn"]["lesson"]["kind"] == "circuit"


def test_scam_tools_build_the_phone_when_missing():
    from director.circuit.agent import ToolCall
    from director.scam import lesson
    from director.trace import Trace
    ex = SimulatedCircuitExecutor()
    code = lesson.run(ToolCall("scam_inspect", {}, live=False), "What are the red flags?", TutorMockModel(), ex,
                      ex.inspect(), None, None, Trace("t", "x"), context={})
    assert code == 0 and set(ex.inspect()["scam"]["tags"]) == {"sender", "brand", "body", "urgency", "link"}


# ---------------- knowledge base ----------------

@pytest.mark.parametrize("question, concept", [
    ("Teach me how to recognize scam texts.", "smishing"), ("What if I follow the link?", "suspicious_links"),
    ("What should I do instead?", "protective_actions"), ("What if I already typed my password?", "credential_theft"),
])
def test_knowledge_retrieval_for_the_lesson(question, concept):
    k = get_kb().retrieve(question)
    assert k.ids[0] == concept and k.template["id"] == "spot_the_scam" and k.suggested["tool"] == "teach_scam_texts"
    assert k.facts and all(f["status"] == "sourced" or f["status"] == "assumption" for f in k.facts)
    assert all(f["source"]["title"] for f in k.facts)


def test_knowledge_misconceptions_for_scams():
    kb = get_kb()
    k = kb.retrieve("Does https mean the site is safe?")
    assert k.misconceptions[0]["id"] == "https_means_safe" and k.misconceptions[0]["triggered"]
    assert [w["id"] for w in kb.wrong_claims("The padlock means the site is safe.")] == ["https_means_safe"]
    assert [w["id"] for w in kb.wrong_claims("Replying STOP is safe and stops the texts.")] == ["reply_stop_safe"]
    assert kb.wrong_claims("Your carrier won't ask for your password or PIN through a text link.") == []
    # the electronics lessons keep their concepts
    assert get_kb().retrieve("Teach me why LEDs need resistors.").suggested["tool"] == "teach_led_resistor"


class _Capture(TutorMockModel):
    def __init__(self):
        self.seen = []

    def complete(self, messages, *, max_tokens=None):
        self.seen.append(messages)
        return super().complete(messages, max_tokens=max_tokens)


def test_model_writes_its_own_words_and_bad_text_falls_back_to_template():
    from director.nemotron_client import ModelReply
    ex, client = SimulatedCircuitExecutor(), _Capture()
    tutor.run_turn(TURNS[0], client, ex, [], context={})
    explainer = [m for m in client.seen if m[0]["content"].startswith("EXPLAINER")]
    assert explainer and "key_points" not in explainer[0][1]["content"]  # the fallback is never shown to it
    assert "heuristic prototype" in explainer[0][0]["content"]

    class Unsafe(TutorMockModel):
        def complete(self, messages, *, max_tokens=None):
            if messages[0]["content"].startswith("EXPLAINER"):
                return ModelReply(content="Just tap the link to verify. It is definitely a scam.", model="x", live=False)
            return super().complete(messages, max_tokens=max_tokens)
    events = []
    tutor.run_turn(TURNS[1], Unsafe(), ex, [], emit=lambda *a, **k: events.append((a, k)), context={})
    turn = next(k for a, k in events if a[0] == "turn")
    assert turn["source"] == "template" and "tap the link" not in turn["reply"].lower()
