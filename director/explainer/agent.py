"""Nemotron as the SCENE DIRECTOR for OVERRIDE lessons: question -> explainer spec (data) or a refusal.

One JSON object, validated by spec.py (schema + lint) plus the knowledge base's misconception check and the
conversation rules (no greeting mid-conversation, no repeated sentences). At most ONE corrective retry; then the
turn fails and nothing is built. The spec is only ever data for blender_engine/explainer.py, never code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..circuit.agent import CONVERSATION, continuity_check
from ..nemotron_client import ModelClient
from ..planner import extract_json
from ..trace import Trace
from . import spec as S

SHAPE_HINTS = {
    "cube": "box, package, block, container", "sphere": "ball, planet, cell, atom, generic thing",
    "cylinder": "can, tower, pillar, server", "cone": "funnel, cone, spike", "mug": "cup, drink",
    "tree": "tree, plant, nature", "rock": "rock, mineral, ground", "table": "table, desk",
    "chair": "chair, seat", "lamp": "lamp, light, idea", "tent": "tent, shelter, camp", "cabin": "house, home",
    "campfire": "fire, heat, energy", "torus": "ring, loop, orbit", "pyramid": "pyramid, hierarchy, mountain",
    "disc": "plate, coin-like platform, record", "cloud": "cloud, the internet, weather", "drop": "water, liquid",
    "person": "a person or role (you, a scammer, a customer, a doctor)", "building": "company, bank, carrier, store",
    "screen": "phone, computer, app, website", "coin": "money, payment, cost", "star": "goal, reward, highlight",
    "gear": "process, machine, mechanism", "battery": "energy store, power", "lock": "security, password, privacy",
    "shield": "protection, defense", "arrow": "direction, movement, result",
}

SCENE_PROMPT = """SCENE_DIRECTOR. You design small 3D explainer lessons for Poyo, a friendly tutor for beginners.
The learner said OVERRIDE: they want a lesson about something outside Poyo's prepared lessons. Turn their question
into ONE diagram-like 3D scene plus a short narration. A deterministic builder draws it in Blender from your JSON;
you never write code.

Output ONE JSON object and nothing else (no prose, no markdown):
{{"title": "<= 60 chars, the lesson's question or topic",
 "summary": "<= 200 chars, one plain sentence: the big idea",
 "layout": one of {layouts},
 "elements": [ 2 to 7 of {{"id": "snake_case", "label": "<= 28 chars, 1-3 words", "shape": one of the shapes,
                "color": "#RRGGBB", "size": "small"|"medium"|"large", "motion": "none"|"spin"|"bob"|"pulse",
                "glow": true|false, "note": "<= 110 chars: what this element is, for follow-up questions"}} ],
 "links": [ 0 to 8 of {{"from": "<element id>", "to": "<element id>", "label": "<= 20 chars, a verb"}} ],
 "narration": [ 2 to 6 of {{"focus": "<element id>" or "overview", "say": "1 or 2 short spoken sentences"}} ],
 "key_points": [ 2 to 5 short sentences: the takeaways ],
 "simplified": "<= 180 chars: what this diagram leaves out or simplifies"}}

Shapes (pick the closest picture; the label says what it is):
{shapes}

Layouts: flow = steps in order, left to right, linked by arrows (a process, an attack, a recipe); cycle = a loop
that repeats (3+ elements); hub = one central thing with the others around it; row = things side by side to
compare; stack = layers from top to bottom; grid = a collection of related things.

Rules:
- Teach the learner's question accurately at a beginner level: mainstream, well-established knowledge only. If
  you are not sure of something, leave it out. Avoid specific numbers, dates, prices or statistics unless they are
  basic, well-known facts.
- The narration is spoken while the camera visits each focus, in order: start with "overview" or the first
  element, then walk through the diagram, then end on "overview" with the takeaway. All steps together at most
  110 words. Talk to the learner ("you"), warm and plain. Plain text only: no markdown, lists or emojis.
- Do not greet the learner or introduce yourself; this is a continuing conversation. Do not reuse sentences
  from CONVERSATION.
- Colors: distinct, readable on a dark background. 1 to 3 elements may move or glow (the main actors).
- No web addresses, domains or phone numbers anywhere.
- Safety: for scams, fraud, hacking and other crimes, explain how they work at a high level and how to recognize
  and protect against them, never step-by-step how to commit them. If the question asks for real help with harm
  (weapons, hurting someone, self-harm, breaking into someone else's accounts, illegal drugs, sexual content), or
  cannot be shown as a diagram at all, output ONLY {{"refuse": "<one short, kind sentence the learner hears>"}}.
"""


@dataclass
class Design:
    spec: dict | None
    refusal: str | None = None
    live: bool = True
    model: str = ""
    attempts: int = 1
    rejected: list[str] = field(default_factory=list)


class DesignError(RuntimeError):
    def __init__(self, message: str, errors: list[str]):
        super().__init__(message)
        self.errors = errors


def system_prompt() -> str:
    return SCENE_PROMPT.format(layouts=" | ".join(S.layouts()),
                               shapes="\n".join(f"- {k}: {SHAPE_HINTS.get(k, k)}" for k in S.shapes()))


def _problems(obj, claim_check, convo, live: bool) -> list[str]:
    if not isinstance(obj, dict):
        return ["the answer must be one JSON object"]
    if "refuse" in obj:
        r = obj["refuse"]
        return [] if isinstance(r, str) and 3 <= len(r) <= 300 and len(obj) == 1 else \
            ["a refusal must be exactly {\"refuse\": \"<one sentence>\"}"]
    errs = S.validate(obj)
    if errs:
        return errs
    spoken = S.narration_text(obj) + " " + " ".join(obj["key_points"])
    if claim_check:
        errs += [f"states a known misconception: {w}" for w in claim_check(spoken)]
    if live:  # MOCK text is deterministic rules
        errs += [f"conversation rule: {c}" for c in continuity_check(S.narration_text(obj), convo)]
    return errs


def design(question: str, client: ModelClient, trace: Trace, knowledge: str = "", claim_check=None) -> Design:
    """`knowledge`: the knowledge base's prompt block (background, not verification). Raises DesignError when the
    spec is still invalid after one corrective retry (nothing is built)."""
    convo = list(CONVERSATION.get())[-4:]
    history = ("CONVERSATION (earlier turns, oldest first; context only):\n"
               + "\n".join(f"learner: {t.get('learner', '')}\nPoyo: {t.get('poyo', '')}" for t in convo) + "\n\n"
               if convo else "")
    system = system_prompt() + ("\nKNOWLEDGE (trusted background from the tutor's knowledge base; use it where it "
                                "fits, it is not the whole answer):\n" + knowledge if knowledge else "")
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": history + f"<question>{question}</question>\nReturn the JSON now."}]
    errors: list[str] = []
    rejected: list[str] = []
    for attempt in (1, 2):
        reply = client.complete(messages, max_tokens=2400)
        try:
            obj = extract_json(reply.content)
        except (ValueError, json.JSONDecodeError) as exc:
            obj, errors = None, [f"could not parse JSON: {exc}"]
        else:
            errors = _problems(obj, claim_check, convo, reply.live)
        trace.add("plan", f"{'LIVE' if reply.live else 'MOCK'} {reply.model} scene design, attempt {attempt} "
                          f"({reply.latency_s:.1f}s): " + ("accepted" if not errors else "rejected"),
                  errors=errors[:8])
        if not errors:
            if "refuse" in obj:
                return Design(None, obj["refuse"].strip(), reply.live, reply.model, attempt, rejected)
            return Design(obj, None, reply.live, reply.model, attempt, rejected)
        rejected.append(reply.content[:2000])
        if attempt == 1:
            messages = messages + [
                {"role": "assistant", "content": reply.content[:6000]},
                {"role": "user", "content": "That design was rejected:\n- " + "\n- ".join(errors[:12])
                 + "\nReturn ONE corrected JSON object only."}]
    raise DesignError("the scene design failed validation after 1 retry; nothing was built", errors)
