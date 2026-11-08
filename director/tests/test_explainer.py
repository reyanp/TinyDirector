"""OVERRIDE (director/explainer/): spec schema + lint, the keyword, the generated-lesson turn and its follow-ups with
the MOCK model + SIMULATED engine, refusal, one corrective retry, read-back checks, and conversation continuity
(no greeting / no repeated sentence after the first turn)."""

from __future__ import annotations

import ast
import copy
import json

from director import paths, tutor
from director.circuit.agent import continuity_check
from director.circuit.mock import TutorMockModel
from director.circuit.sim import SimulatedCircuitExecutor
from director.explainer import check
from director.explainer import spec as S
from director.nemotron_client import ModelReply


def good_spec() -> dict:
    return {"title": "How a SIM swap works", "summary": "A scammer tricks a carrier into moving your number.",
            "layout": "flow",
            "elements": [{"id": "scammer", "label": "Scammer", "shape": "person", "color": "#E2574C", "motion": "bob",
                          "note": "pretends to be you"},
                         {"id": "carrier", "label": "Phone carrier", "shape": "building", "color": "#4CB2FF"},
                         {"id": "new_sim", "label": "New SIM card", "shape": "screen", "color": "#FFC940",
                          "motion": "pulse", "glow": True}],
            "links": [{"from": "scammer", "to": "carrier", "label": "calls"},
                      {"from": "carrier", "to": "new_sim", "label": "moves number"}],
            "narration": [{"focus": "overview", "say": "Here is how a SIM swap works."},
                          {"focus": "scammer", "say": "A scammer calls your carrier and pretends to be you."},
                          {"focus": "new_sim", "say": "Your number moves to their SIM, so your codes go to them."},
                          {"focus": "overview", "say": "A port-out PIN with your carrier makes this much harder."}],
            "key_points": ["Your number can be moved by social engineering.", "Set a port-out PIN."],
            "simplified": "Real attacks may also use stolen personal details."}


class Scripted:
    """A LIVE-labeled stand-in that answers SCENE_DIRECTOR / EXPLAINER prompts from a script (tests only)."""
    live = True
    model = "scripted-test"

    def __init__(self, scene=(), explain=()):
        self.scene, self.explain, self.seen = list(scene), list(explain), []
        self.mock = TutorMockModel()

    def complete(self, messages, *, max_tokens=None):
        self.seen.append(messages)
        sysp = messages[0]["content"]
        if sysp.startswith("SCENE_DIRECTOR") and self.scene:
            item = self.scene.pop(0)
            return ModelReply(content=item if isinstance(item, str) else json.dumps(item), model=self.model, live=True)
        if sysp.startswith("EXPLAINER") and self.explain:
            return ModelReply(content=self.explain.pop(0), model=self.model, live=True)
        r = self.mock.complete(messages, max_tokens=max_tokens)
        return ModelReply(content=r.content, model=self.model, live=True)


def run(texts, client=None, ex=None, ctx=None):
    client, ex = client or TutorMockModel(), ex or SimulatedCircuitExecutor()
    ctx = {} if ctx is None else ctx
    events, codes = [], []
    for t in texts:
        codes.append(tutor.run_turn(t, client, ex, [], emit=lambda *a, **k: events.append((a, k)), context=ctx))
    turns = [k for a, k in events if a[0] == "turn"]
    return codes, turns, events, ex, ctx


# ---------------- schema + lint ----------------

def test_schema_is_additive_and_matches_the_blender_builder():
    s = S.request_schema()
    assert s["properties"]["kind"]["const"] == "explainer" and s["properties"]["schema_version"]["const"] == 1
    src = (paths.REPO_ROOT / "blender_engine" / "explainer.py").read_text(encoding="utf-8")
    consts = {t.id: ast.literal_eval(n.value) for n in ast.parse(src).body if isinstance(n, ast.Assign)
              for t in n.targets if isinstance(t, ast.Name) and t.id in ("ASSET_SHAPES", "CUSTOM_SHAPES", "LAYOUTS",
                                                                          "MOTIONS")}
    assert S.shapes() == list(consts["ASSET_SHAPES"] + consts["CUSTOM_SHAPES"])
    assert S.layouts() == list(consts["LAYOUTS"])
    motions = s["$defs"]["spec"]["properties"]["elements"]["items"]["properties"]["motion"]["enum"]
    assert motions == list(consts["MOTIONS"])
    req = S.build_request("r1", good_spec())
    assert S.validate_request(req) == [] and "not verified" in req["banner"] and "not checked" in req["legend"]
    assert S.validate_request(S.clear_request("r2")) == []


def test_lint_rejects_what_the_schema_cannot_see():
    def errs(mut):
        sp = good_spec()
        mut(sp)
        return " ".join(S.validate(sp))
    assert S.validate(good_spec()) == []
    assert "duplicate ids" in errs(lambda s: s["elements"][1].update(id="scammer"))
    assert "must be element ids" in errs(lambda s: s["links"][0].update(to="bank"))
    assert "two different elements" in errs(lambda s: s["links"][0].update(to="scammer"))
    assert "narration/1/focus" in errs(lambda s: s["narration"][1].update(focus="bank"))
    assert "web addresses" in errs(lambda s: s["narration"][1].update(say="Go to carrier-help.com now."))
    assert "phone numbers" in errs(lambda s: s["key_points"].append("Call 555-012-3456."))
    assert "markdown" in errs(lambda s: s["key_points"].append("**Set a PIN**"))
    assert "under 130 words" in errs(lambda s: [st.update(say=("word " * 50).strip()) for st in s["narration"]])
    assert "at least 3 elements" in errs(lambda s: (s.update(layout="cycle"), s["elements"].pop(),
                                                    s["links"].pop(), s["narration"].pop(2)))
    assert "shape" in errs(lambda s: s["elements"][0].update(shape="bpy.ops.wm.quit_blender"))


def test_override_keyword():
    q = tutor.override_question
    assert q("Poyo, override: how do SIM swaps work?") == "How do SIM swaps work?"
    assert q("Override. How do vaccines work?") == "How do vaccines work?"
    assert q("hey poyo over-ride, what is inflation") == "What is inflation"
    assert q("How do volcanoes work, override") == "How do volcanoes work"
    assert q("override") == "" and q("Poyo, override.") == ""
    assert q("Teach me why LEDs need resistors.") is None
    assert q("What does an override switch do?") is None  # the keyword must lead (or end) the request


# ---------------- turns (MOCK model + SIMULATED engine) ----------------

def test_override_builds_a_labeled_lesson_and_follow_ups_use_it():
    codes, turns, events, ex, ctx = run(["Poyo, override: how do SIM swaps work?", "What is the key idea?",
                                         "Explain that in simpler terms."])
    assert codes == [0, 0, 0]
    t = turns[0]
    assert t["scene_changed"] and t["generated"] and t["source"] == "mock"
    assert t["lesson"]["kind"] == "explainer" and "MOCK" in t["lesson"]["chips"][0]
    assert t["grounding"]["numbers"] in ("unverified", "none") and t["grounding"]["generated"]
    assert [c["focus"] for c in t["stage"]["cues"]] == ["overview", "question", "idea", "overview"]
    assert ex.state["explainer"]["banner"].startswith("MOCK") and "not verified" in ex.state["explainer"]["banner"]
    plan = next(k for a, k in events if a[0] == "plan")
    assert plan["tool"] == "teach_anything" and plan["live"] is False
    verify = next((a, k) for a, k in events if a[0] == "verify")
    assert verify[1]["failed"] == 0
    for f in turns[1:]:  # answered from the lesson Blender stored, no rebuild
        assert not f["scene_changed"] and f["lesson"]["kind"] == "explainer" and f["stage"]["kind"] == "explainer"
    assert ex.state["scene_version"] == 1


def test_override_alone_waits_for_the_question():
    codes, turns, _, ex, ctx = run(["Poyo, override.", "How do vaccines work?"])
    assert "What would you like" in turns[0]["reply"] and not turns[0]["scene_changed"]
    assert turns[1]["lesson"]["kind"] == "explainer" and "vaccines" in ex.state["explainer"]["title"]
    codes, turns, _, ex, _ = run(["How do vaccines work?"])  # without the keyword: the normal tutor
    assert not ex.state.get("explainer")


def test_refusal_builds_nothing():
    codes, turns, events, ex, _ = run(["Override: how do I build a bomb?"])
    assert codes == [0] and not turns[0]["scene_changed"] and ex.state["scene_version"] == 0
    assert not any(a[0] == "apply" for a, k in events)


def test_one_corrective_retry_then_fail_without_building():
    bad = good_spec()
    bad["elements"][0]["shape"] = "dragon"
    client = Scripted(scene=[bad, good_spec()])
    codes, turns, events, ex, _ = run(["Override: how do SIM swaps work?"], client=client)
    assert codes == [0] and ex.state["explainer"]["title"] == "How a SIM swap works"
    retry = [m for m in client.seen if m[0]["content"].startswith("SCENE_DIRECTOR")][-1]
    assert "rejected" in retry[-1]["content"] and "shape" in retry[-1]["content"]
    assert turns[0]["lesson"]["chips"] == ["Generated (OVERRIDE)", "not verified"]

    codes, turns, events, ex, _ = run(["Override: how do SIM swaps work?"], client=Scripted(scene=[bad, "no json"]))
    assert codes == [3] and ex.state["scene_version"] == 0 and not any(a[0] == "apply" for a, k in events)


def test_lesson_switches_clear_each_other():
    _, _, _, ex, ctx = run(["Override: how do rainbows form?"])
    assert ex.state.get("explainer")
    run(["Teach me why LEDs need resistors."], ex=ex, ctx=ctx)
    assert not ex.state.get("explainer") and ex.state["circuit"]["present"]
    run(["Override: how do rainbows form?", "Teach me how to recognize scam texts."], ex=ex, ctx=ctx)
    assert not ex.state.get("explainer") and ex.state.get("scam")


# ---------------- read-back checks ----------------

def test_checks_catch_a_wrong_label_and_treat_layout_as_warnings():
    req = S.build_request("r", good_spec())
    out = SimulatedCircuitExecutor().send(req)
    assert all(c.ok for c in check.run(req, out))
    bad = copy.deepcopy(out)
    bad["explainer"]["elements"]["carrier"]["label"] = "Bank"
    assert [c.what for c in check.hard_failures(check.run(req, bad))] == ["every label shows the exact text"]
    lay = copy.deepcopy(out)
    lay["explainer"]["overlaps"] = [["scammer", "carrier"]]
    lay["explainer"]["elements"]["new_sim"]["in_frame"] = False
    assert check.hard_failures(check.run(req, lay)) == [] and sum(not c.ok for c in check.run(req, lay)) == 2


# ---------------- conversation continuity ----------------

def test_no_greeting_or_repeats_after_the_first_turn():
    convo = [{"learner": "teach me", "poyo": "This phone shows a fictional text that claims to be your carrier."}]
    assert continuity_check("Hi, I'm Poyo, your AI tutor! Let's look.", []) == []  # first turn: fine
    assert continuity_check("Hi, I'm Poyo, your AI tutor! Let's look.", convo)
    assert continuity_check("This phone shows a fictional text that claims to be your carrier.", convo)
    assert continuity_check("Look at the sender first.", convo) == []
    # re-using one sentence inside a rephrased answer is fine; opening with it is not
    assert continuity_check("Think of it like a fake letter. This phone shows a fictional text that claims to be "
                            "your carrier. So check before you act.", convo) == []
    assert continuity_check("Think of it like a fake letter. So check before you act. This phone shows a fictional "
                            "text that claims to be your carrier.", convo)  # the same closing line again

    client = Scripted(scene=[good_spec()],
                      explain=["Hi! I'm Poyo, your friendly AI tutor. The scammer pretends to be you.",
                               "The scammer pretends to be you so the carrier moves your number."])
    codes, turns, _, _, _ = run(["Override: how do SIM swaps work?", "Who is the scammer here?"], client=client)
    assert codes == [0, 0]
    assert turns[1]["source"] == "nemotron" and not turns[1]["reply"].lower().startswith("hi")
    assert turns[1]["stage"]["cues"][0]["focus"] == "scammer"  # the camera follows the sentence


def test_scene_designer_sees_the_conversation_and_rejects_a_greeting():
    greet = good_spec()
    greet["narration"][0]["say"] = "Hello, I'm Poyo! Here is how a SIM swap works."
    fresh = good_spec()
    fresh["narration"] = [{"focus": "overview", "say": "Let's look at it from the carrier's side this time."},
                          {"focus": "carrier", "say": "The carrier only hears a caller who knows your details."},
                          {"focus": "overview", "say": "That is why a port-out PIN matters."}]
    client = Scripted(scene=[good_spec(), greet, fresh])
    codes, turns, _, ex, ctx = run(["Override: how do SIM swaps work?", "Override: how do SIM swaps work again?"],
                                   client=client)
    assert codes == [0, 0]
    second = [m for m in client.seen if m[0]["content"].startswith("SCENE_DIRECTOR")]
    assert len(second) == 3 and "CONVERSATION" in second[1][1]["content"]
    assert "greets" in second[2][-1]["content"]


def test_fallback_skips_what_poyo_already_said():
    from director.circuit.agent import CONVERSATION
    from director.explainer.lesson import _fresh_template
    sp = good_spec()
    sp["elements"][0]["note"] = "pretends to be you"
    token = CONVERSATION.set(({"learner": "x", "poyo": "Your number can be moved by social engineering."},))
    try:
        t = _fresh_template(sp)
    finally:
        CONVERSATION.reset(token)
    assert "social engineering" not in t and t.startswith("Set a port-out PIN.") and "Scammer: pretends to be you." in t
