"""Director agent: Nemotron turns a learner's question into a lesson spec (lesson_spec_v1).

It decides WHAT to teach and show (parts, wiring, which calculations, labels, key points, step order).
It never writes numbers on screen itself: every value is a {calc.output} placeholder that the calculators
fill in. The spec is validated (schema + references + circuit lint + no bare numbers); on failure the model
gets ONE corrective retry with the exact errors, then we give up without building anything.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from blender_engine.lesson_parts import PARTS

from ..nemotron_client import ModelReply
from ..planner import extract_json
from . import spec as spec_mod
from .calculators import CalcError, catalog_text
from .examples import LED_SPEC


class SpecError(RuntimeError):
    def __init__(self, message: str, errors: list[str], replies: list[ModelReply]):
        super().__init__(message)
        self.errors, self.replies = errors, replies


@dataclass
class SpecResult:
    spec: dict | None  # None when the question is out of scope
    unsupported: str = ""
    warnings: list[str] = field(default_factory=list)
    replies: list[ModelReply] = field(default_factory=list)


def _parts() -> str:
    return "\n".join(f"- {n}: {p['desc']}; pins: {', '.join(p['pins']) if p['pins'] else 'you list them in pins'}"
                     for n, p in PARTS.items())


SYSTEM_PROMPT = """You are the DIRECTOR of a 3D electronics tutor. A learner asks a question; you design ONE short
visual lesson that answers it with a small real circuit. Another agent builds your design in Blender and a
verifier checks the build against your spec, so the spec must be exact.

Output ONE JSON object, lesson_spec_v1:
{{"schema_version": 1, "title": "<= 70 chars, the lesson's question or claim", "question": "<the learner's words>",
  "calculations": [{{"id": "c", "tool": "<calculator>", "args": {{...}}}}],
  "parts": [{{"id": "bat", "type": "<part type>", "label": "short on-screen name", "color": "red"(LED only),
              "group": "optional: sub-circuit name when comparing two circuits (parts only)"}}],
  "connections": [{{"id": "w1", "from": "part.pin", "to": "part.pin", "color": "red|black|orange|yellow|blue|green|white|gray|purple"}}],
  "flows": [{{"id": "f1", "path": ["w1", "w2"], "current_ma": "{{c.I_ma}}", "label": "I = {{c.I}}"}}],
  "labels": [{{"id": "l1", "text": "short note", "near": "<part or connection id>"}}],
  "key_points": ["1 to 4 short sentences that answer the question"],
  "steps": [{{"show": ["ids revealed together"], "caption": "one sentence"}}]}}

PART TYPES:
{parts}

CALCULATORS (the ONLY source of numbers; use their outputs through placeholders):
{calcs}

RULES:
- Every number shown on screen (labels, key points, captions, title) must be a placeholder {{calc_id.output}}.
  Do not type digits yourself (part numbers like 2N3904 are fine). Flow current_ma must be a *_ma output.
  Formatted outputs already carry their unit ("9 V", "21.2 mA", "10 kΩ"): write "{{c.V_source}} battery", never
  "{{c.V_source}} V battery".
- Real wiring: every connection joins two existing pins. Close every loop back to the source. LED anode toward +,
  cathode toward -. NPN BJT: C to the load, E to ground, B through a base resistor from the signal. N-MOSFET:
  D to the load, S to ground, G straight from the signal. A ground part ties negatives together; connect the
  battery - and the signal gnd to it.
- Transistor LED switch wiring (each transistor gets its OWN load resistor and LED; ids are examples):
  bat.+ -> rl1.a, rl1.b -> led1.anode, led1.cathode -> q1.C, q1.E -> gnd.gnd, sig.out -> rb.a, rb.b -> q1.B (BJT);
  bat.+ -> rl2.a, rl2.b -> led2.anode, led2.cathode -> q2.D, q2.S -> gnd.gnd, sig.out -> q2.G (MOSFET);
  bat.- -> gnd.gnd, sig.gnd -> gnd.gnd. Use only the pin names listed for each part type.
- Flows follow consecutive connections in conventional-current direction (+ to -); a flow's path must be
  continuous. Give each distinct current its own flow.
- 3 to 12 parts. 1 to 5 steps that build the idea up. Short labels (under about 40 characters).
- Answer the question that was asked. If the question compares two components (e.g. BJT vs MOSFET), BOTH must
  appear as parts, each in its own sub-circuit with "group", side by side.
- If no calculator and part type can show the idea honestly, reply {{"unsupported": "one sentence why"}}.

EXAMPLE (for "Why does an LED need a resistor?"):
{example}

Reply with the JSON object only."""


def messages_for(question: str) -> list[dict]:
    return [{"role": "system", "content": SYSTEM_PROMPT.format(parts=_parts(), calcs=catalog_text(),
                                                               example=json.dumps(LED_SPEC, ensure_ascii=False))},
            {"role": "user", "content": f"Learner's question: {question}"}]


def check(spec) -> tuple[list[str], list[str]]:
    """validate + make sure the calculators accept the args (so resolve() cannot fail later)."""
    if not isinstance(spec, dict):
        return ["the reply must be a JSON object"], []
    errs, warns = spec_mod.validate(spec)
    if not errs:
        try:
            spec_mod.resolve(spec)
        except CalcError as exc:
            errs.append(str(exc))
    return errs, warns


def write_spec(question: str, client, max_tokens: int = 6000) -> SpecResult:
    question = question.strip()
    if not question:
        raise ValueError("empty question")
    messages = messages_for(question)
    replies: list[ModelReply] = []
    errors: list[str] = []
    for attempt in (1, 2):
        reply = client.complete(messages, max_tokens=max_tokens)
        replies.append(reply)
        try:
            data = extract_json(reply.content)
        except ValueError as exc:
            errors = [f"reply is not JSON: {exc}"]
        else:
            if isinstance(data.get("unsupported"), str) and "parts" not in data:
                return SpecResult(None, unsupported=data["unsupported"][:300], replies=replies)
            data.setdefault("schema_version", 1)
            data["question"] = question
            errors, warns = check(data)
            if not errors:
                return SpecResult(data, warnings=warns, replies=replies)
        if attempt == 1:  # ONE corrective retry with the exact problems
            messages = messages + [
                {"role": "assistant", "content": reply.content},
                {"role": "user", "content": "That spec is invalid:\n- " + "\n- ".join(errors[:20]) +
                 "\nReply with the corrected complete JSON object only."}]
    raise SpecError("lesson spec still invalid after one retry", errors, replies)
