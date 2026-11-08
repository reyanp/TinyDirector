"""Nemotron's two jobs in the circuit tutor: pick a tool, then explain the CALCULATED result.

1. choose_tool(): the user's words + the circuit Blender reports -> ONE JSON tool call
   {"tool": ..., "args": {...}}. Validated against TOOL_SCHEMA, at most one corrective retry.
   The model never computes a current; it only picks the tool and its arguments.
2. explain(): facts from director/circuit/model.py (already formatted strings) -> 2-3 beginner-friendly
   sentences. Every number in the text must match a number in the facts (rounding allowed); one retry,
   then a deterministic template (labeled as such). Model text is only ever displayed, never executed.
"""

from __future__ import annotations

import contextvars
import json
import re
from dataclasses import dataclass, field

from jsonschema import Draft202012Validator

from ..nemotron_client import ModelClient, ModelReply
from ..planner import extract_json
from ..trace import Trace
from .model import R_MAX_OHM, R_MIN_OHM, LedCircuit, Solution, format_ma, format_ohms, format_volts, solve

TOUR_STOPS = ("battery", "resistor", "current", "led", "overview")
TOOLS = {
    "teach_led_resistor": "Start (or continue) the lesson 'Why does an LED need a resistor?'. Builds the 3D circuit "
                          "if it is not in the scene yet. args: {} or {\"resistance_ohm\": number}",
    "set_resistance": "Change the resistor in the EXISTING circuit to a new value. args: {\"resistance_ohm\": number}. "
                      "Convert words to a plain number of ohms: '1k' / '1,000 ohms' / 'one thousand ohms' -> 1000, "
                      "'2.2 kΩ' -> 2200. 'Put it back to 330' -> 330.",
    "adjust_resistance": "Make the resistor bigger or smaller when the learner gives NO value ('what happens if I "
                         "increase the resistance?', 'use a smaller resistor'). The calculation module picks the next "
                         "standard value (about 3 times larger or smaller). args: {\"direction\": \"increase\" or "
                         "\"decrease\"}",
    "focus_camera": "Only point the camera at something on screen, no explanation asked ('show me the resistor', "
                    "'zoom in on the LED', 'look at the current', 'zoom out'). args: {\"target\": \"battery\" | "
                    "\"resistor\" | \"led\" | \"current\" | \"overview\"}",
    "tour_circuit": "A guided camera tour of the circuit ON SCREEN ('show me what's happening inside this circuit', "
                    "'walk me through it'). Choose 2 to 5 stops in a good teaching order from battery, resistor, "
                    "current, led, and end with overview; the camera visits them while you narrate, and the "
                    "calculation module supplies each stop's measurement. args: {\"stops\": [\"battery\", "
                    "\"resistor\", \"current\", \"led\", \"overview\"]}",
    "compare_resistance": "Show the circuit on screen SIDE BY SIDE with a what-if copy that has another resistor, "
                          "WITHOUT changing the original ('compare what happens if I use 1,000 ohms', 'show both', "
                          "'side by side'). args: {\"resistance_ohm\": number} or, with no value given, "
                          "{\"direction\": \"increase\" or \"decrease\"}",
    "end_comparison": "Remove the side-by-side comparison and go back to the original circuit ('go back', 'back to "
                      "the original', 'stop comparing', 'remove the copy'). args: {}",
    "explain": "Answer a question about the circuit WITHOUT changing it (e.g. 'why is it dimmer?', 'what does the "
               "resistor do?', 'what if there were no resistor?'). args: {\"question\": string}",
    "teach_topic": "The user wants to learn a DIFFERENT electronics idea that needs a different circuit (e.g. BJT vs "
                   "MOSFET, transistor switches, voltage dividers, Ohm's law with one resistor). A director + builder "
                   "agent team designs, builds and verifies a new 3D lesson for it. args: {\"question\": the "
                   "learner's question, rephrased as a clear question}",
    "explain_concept": "Explain a general electronics concept WITHOUT changing the scene, from the knowledge base. "
                       "Use it for concepts with no 3D demo (the <knowledge> block says so, e.g. parallel circuits) "
                       "or a general 'what is X?' that is not about the circuit on screen. args: {\"concept\": "
                       "a concept id from <knowledge>}",
    "teach_scam_texts": "Start the cybersecurity lesson 'Spot the Scam': learning to recognize scam / phishing text "
                        "messages (smishing). Shows a phone with ONE fictional suspicious text and asks the learner "
                        "what they would do. args: {}",
    "scam_inspect": "Spot the Scam on screen: show and explain the warning signs in the message ('what makes that "
                    "message suspicious?', 'what are the red flags?', 'how can I tell?'). A heuristic checker finds "
                    "the signs; you only choose this tool. args: {}",
    "scam_choose": "Spot the Scam on screen: the learner picks (or asks about) what to do with the text. Shows BOTH "
                   "hypothetical outcomes side by side, their choice marked. 'I'd click it', 'what if I follow the "
                   "link?' -> {\"choice\": \"follow_link\"}; 'I'd check the official app', 'verify it', 'what "
                   "should I do instead?', 'what's the safe thing to do?' -> {\"choice\": \"verify_official\"}",
    "scam_recover": "Spot the Scam: the learner already tapped the link or gave details away ('what if I already "
                    "typed my password?', 'I entered my PIN, now what?'): the recovery steps. args: {}",
    "clarify": "The request is not about electronics or the Spot the Scam lesson, or is unsafe or impossible to show "
               "(mains wiring, PCB layout, ...). args: {\"message\": short reply saying what IS possible: this "
               "lesson's resistor, questions about the circuit, a new electronics topic, or spotting scam texts; for "
               "any other topic, tell the learner they can say \"override\" before their question and Poyo will "
               "generate a lesson about it}",
}
SCAM_TOOLS = ("teach_scam_texts", "scam_inspect", "scam_choose", "scam_recover")

TOOL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["tool", "args"],
    "properties": {
        "tool": {"enum": list(TOOLS)},
        "args": {"type": "object"},
        "reason": {"type": "string", "maxLength": 400},
    },
    "allOf": [
        {"if": {"properties": {"tool": {"const": "set_resistance"}}},
         "then": {"properties": {"args": {"type": "object", "additionalProperties": False,
                                          "required": ["resistance_ohm"],
                                          "properties": {"resistance_ohm": {"type": "number"}}}}}},
        {"if": {"properties": {"tool": {"const": "teach_led_resistor"}}},
         "then": {"properties": {"args": {"type": "object", "additionalProperties": False,
                                          "properties": {"resistance_ohm": {"type": "number"}}}}}},
        {"if": {"properties": {"tool": {"const": "adjust_resistance"}}},
         "then": {"properties": {"args": {"type": "object", "additionalProperties": False, "required": ["direction"],
                                          "properties": {"direction": {"enum": ["increase", "decrease"]}}}}}},
        {"if": {"properties": {"tool": {"const": "focus_camera"}}},
         "then": {"properties": {"args": {"type": "object", "additionalProperties": False, "required": ["target"],
                                          "properties": {"target": {"enum": ["battery", "resistor", "led", "current",
                                                                             "overview"]}}}}}},
        {"if": {"properties": {"tool": {"const": "tour_circuit"}}},
         "then": {"properties": {"args": {"type": "object", "additionalProperties": False, "required": ["stops"],
                                          "properties": {"stops": {"type": "array", "minItems": 2, "maxItems": 6,
                                                                   "uniqueItems": True,
                                                                   "items": {"enum": list(TOUR_STOPS)}}}}}}},
        {"if": {"properties": {"tool": {"const": "compare_resistance"}}},
         "then": {"properties": {"args": {"type": "object", "additionalProperties": False, "minProperties": 1,
                                          "maxProperties": 1,
                                          "properties": {"resistance_ohm": {"type": "number"},
                                                         "direction": {"enum": ["increase", "decrease"]}}}}}},
        {"if": {"properties": {"tool": {"enum": ["teach_scam_texts", "scam_inspect", "scam_recover"]}}},
         "then": {"properties": {"args": {"type": "object", "additionalProperties": False, "maxProperties": 0}}}},
        {"if": {"properties": {"tool": {"const": "scam_choose"}}},
         "then": {"properties": {"args": {"type": "object", "additionalProperties": False, "required": ["choice"],
                                          "properties": {"choice": {"enum": ["follow_link", "verify_official"]}}}}}},
        {"if": {"properties": {"tool": {"const": "end_comparison"}}},
         "then": {"properties": {"args": {"type": "object", "additionalProperties": False, "maxProperties": 0}}}},
        {"if": {"properties": {"tool": {"const": "explain"}}},
         "then": {"properties": {"args": {"type": "object", "additionalProperties": False, "required": ["question"],
                                          "properties": {"question": {"type": "string", "maxLength": 500}}}}}},
        {"if": {"properties": {"tool": {"const": "teach_topic"}}},
         "then": {"properties": {"args": {"type": "object", "additionalProperties": False, "required": ["question"],
                                          "properties": {"question": {"type": "string", "minLength": 3,
                                                                      "maxLength": 300}}}}}},
        {"if": {"properties": {"tool": {"const": "explain_concept"}}},
         "then": {"properties": {"args": {"type": "object", "additionalProperties": False, "required": ["concept"],
                                          "properties": {"concept": {"type": "string", "maxLength": 60}}}}}},
        {"if": {"properties": {"tool": {"const": "clarify"}}},
         "then": {"properties": {"args": {"type": "object", "additionalProperties": False, "required": ["message"],
                                          "properties": {"message": {"type": "string", "maxLength": 500}}}}}},
    ],
}
_TOOL_VALIDATOR = Draft202012Validator(TOOL_SCHEMA)

TOOL_PROMPT = """TOOL_SELECTOR. You are the director of interactive 3D lessons in Blender. The main lesson is
electronics: "Why does an LED need a resistor?" The circuit is fixed: a 9 V battery -> one resistor -> one red LED ->
back to the battery. You do NOT calculate anything; a calculation module does that after you choose a tool.
There is also ONE cybersecurity lesson, "Spot the Scam" (recognizing scam text messages), with its own tools.

Choose exactly ONE tool for the user's latest message and output ONE JSON object, nothing else (no prose, no
markdown): {{"tool": "<name>", "args": {{...}}, "reason": "<one short sentence>"}}

Tools:
{tools}

Rules:
- Only these tools. Resistor values must be plain numbers of ohms between {rmin:g} and {rmax:g}.
- If the user wants a different resistor value, use set_resistance even if they phrase it as a question
  ("what if it were 470 ohms?"). Bigger / smaller / more / less resistance with NO number -> adjust_resistance.
- "Why?", "why did it get dimmer?", "what does that mean?" -> explain: answer about what is already on screen;
  never rebuild or change it for a question.
- Only moving the view ("show me / zoom in on / look at the resistor", "zoom out") -> focus_camera.
- "Show me what's happening inside this circuit", "walk me through it", "give me a tour" (circuit on screen)
  -> tour_circuit; pick the stops that explain it best, e.g. battery -> resistor -> current -> led -> overview.
- "Compare ...", "side by side", "show both", "what would change if ..." with the word compare/both/side by side
  -> compare_resistance (the original stays; a what-if copy appears beside it). Without those words, a new value
  -> set_resistance.
- When <circuit_state> says a comparison is on screen: "go back", "back to the original", "stop comparing" ->
  end_comparison; questions about the two circuits -> explain.
- "Teach me ..." / "show me ..." / "why do LEDs need resistors?" with no specific value -> teach_led_resistor.
- Anything about removing the resistor, or why it is dimmer/brighter -> explain (the scene keeps its values).
- A question about other components or circuits (transistors, MOSFETs, voltage dividers, ...) -> teach_topic,
  even mid-lesson: the learner may switch topics at any time.
- <knowledge> lists the concepts the tutor's knowledge base matched and which demo exists for them. A concept
  with no 3D demo (explanation only) -> explain_concept with its id; never invent a demo for it. Questions about
  the circuit already on screen still use explain / set_resistance.
- Scam texts, phishing, smishing, suspicious messages or links -> the Spot the Scam tools: start it with
  teach_scam_texts. When <circuit_state> says the Spot the Scam phone is on screen: warning signs / red flags /
  "what makes it suspicious?" -> scam_inspect; the learner's decision or "what if I follow the link?" /
  "what should I do instead?" -> scam_choose; "I already typed / clicked / gave ..." -> scam_recover; "go back",
  "back to the message" while the outcomes are shown -> end_comparison; other questions about it -> explain.
  Never use teach_topic for scams (it builds electronics circuits only).
- When <circuit_state> says a generated OVERRIDE lesson is on screen: questions about it, "show me ..." or "explain
  that" -> explain (it is answered from that lesson). A new electronics or scam lesson uses its own tool as usual.
"""

EXPLAIN_PROMPT = """EXPLAINER. You are a friendly electronics tutor for complete beginners. The 3D scene has just
been updated. Explain what happened and why, in 2 or 3 short sentences (at most 70 words), like the example:
"Increasing the resistance limits the current. With our simplified model, it drops from about 21 milliamps to 7
milliamps. That's why the LED becomes dimmer."

Hard rules:
- Use ONLY numbers that appear in FACTS (you may round them, e.g. 21.2 mA -> about 21 milliamps). Never compute or
  invent any other number, ratio, or rating.
- Plain text only: no markdown, no lists, no equations in LaTeX.
- Follow FACTS.teaching_goal. When it is natural, say this is a simplified model (the LED is treated as a fixed
  2 V drop).
"""


@dataclass
class ToolCall:
    tool: str
    args: dict
    reason: str = ""
    live: bool = True
    model: str = ""
    attempts: int = 1


@dataclass
class Explanation:
    text: str
    source: str  # "nemotron" | "mock" | "template"
    attempts: int = 0
    rejected: list[str] = field(default_factory=list)


class ToolError(RuntimeError):
    def __init__(self, message: str, errors: list[str]):
        super().__init__(message)
        self.errors = errors


# ---------------- tool selection ----------------

def describe_state(current: LedCircuit | None, lesson_title: str | None = None, focus: str | None = None,
                   compare: dict | None = None) -> str:
    """What Blender reports is on stage (read back, never assumed)."""
    if current is not None and compare and compare.get("active"):
        alt = (compare.get("alternative") or {}).get("params") or {}
        return (describe_state(current) + " A side-by-side comparison is on screen: left = this circuit (unchanged), "
                f"right = a what-if copy with a {format_ohms(alt.get('resistance_ohm', 0))} resistor.")
    if current is None:
        if lesson_title:
            return (f"No LED circuit on screen; a generated lesson is: {lesson_title!r}."
                    + (f" The camera is on {focus}." if focus and focus != "overview" else ""))
        return "No lesson circuit in the scene yet."
    if focus and focus != "overview":
        return describe_state(current) + f" The camera is focused on the {focus}."
    return (f"Circuit in the scene (read back from Blender): battery {format_volts(current.source_v)}, "
            f"resistor {format_ohms(current.resistance_ohm)}, red LED (forward voltage {format_volts(current.led_vf)}).")


def validate_tool(obj) -> list[str]:
    errs = [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
            for e in sorted(_TOOL_VALIDATOR.iter_errors(obj), key=lambda e: list(e.absolute_path))]
    return errs


def choose_tool(text: str, client: ModelClient, current: LedCircuit | None, history: list[str],
                trace: Trace, knowledge=None, scene: str | None = None) -> ToolCall:
    """`knowledge` (director.knowledge.Knowledge, optional) adds the matched concepts and demos to the prompt and
    lets explain_concept be checked against the knowledge base's concept ids."""
    system = TOOL_PROMPT.format(tools="\n".join(f"- {k}: {v}" for k, v in TOOLS.items()), rmin=R_MIN_OHM,
                                rmax=R_MAX_OHM)
    user = (f"<circuit_state>{scene or describe_state(current)}</circuit_state>\n"
            + (f"<earlier_turns>{json.dumps(history[-6:])}</earlier_turns>\n" if history else "")
            + (f"<knowledge>\n{knowledge.tool_hint()}\n</knowledge>\n" if knowledge is not None else "")
            + f"<user_message>{text}</user_message>\nReturn the JSON tool call now.")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    errors: list[str] = []
    for attempt in (1, 2):
        reply: ModelReply = client.complete(messages, max_tokens=600)
        trace.add("plan", f"{'LIVE' if reply.live else 'MOCK'} {reply.model} tool choice, attempt {attempt} "
                          f"({reply.latency_s:.1f}s)")
        try:
            obj = extract_json(reply.content)
        except (ValueError, json.JSONDecodeError) as exc:
            errors = [f"could not parse JSON: {exc}"]
        else:
            errors = validate_tool(obj)
            if not errors and obj["tool"] == "explain_concept" and knowledge is not None \
                    and obj["args"]["concept"] not in knowledge.all_ids:
                errors = [f"args/concept: unknown concept {obj['args']['concept']!r}; known: "
                          f"{', '.join(sorted(knowledge.all_ids))}"]
            if not errors:
                call = ToolCall(obj["tool"], obj["args"], obj.get("reason", ""), reply.live, reply.model, attempt)
                trace.add("validate", f"tool {call.tool} {json.dumps(call.args)} accepted"
                          + (" (after 1 repair)" if attempt == 2 else ""))
                return call
        trace.add("validate", f"rejected tool call attempt {attempt}", errors=errors)
        if attempt == 1:
            messages = messages + [
                {"role": "assistant", "content": reply.content[:3000]},
                {"role": "user", "content": "That tool call was rejected:\n- " + "\n- ".join(errors)
                 + "\nReturn ONE corrected JSON object only."}]
    raise ToolError("tool call failed validation after 1 retry; nothing was executed", errors)


# ---------------- facts + explanation ----------------

def _glow_word(sol: Solution) -> str:
    return {"off": "off", "ok": "lit", "high": "bright", "over_max": "extremely bright (over its limit)"}[sol.rating]


def _snapshot(sol: Solution) -> dict:
    return {"resistance": format_ohms(sol.circuit.resistance_ohm), "current": format_ma(sol.current_ma),
            "voltage_across_resistor": format_volts(sol.v_resistor, 1), "led": _glow_word(sol)}


def facts_for(tool: ToolCall, user_text: str, before: Solution | None, after: Solution, built: bool) -> dict:
    c = after.circuit
    facts = {
        "user_request": user_text,
        "what_happened": ("built the lesson circuit in 3D" if built else
                          "changed the resistor" if before and before.circuit.resistance_ohm != c.resistance_ohm
                          else "no change to the circuit (answering a question)"),
        "battery": format_volts(c.source_v),
        "led_forward_voltage": format_volts(c.led_vf),
        "formula": f"I = ({format_volts(c.source_v)} − {format_volts(c.led_vf)}) ÷ R",
        "now": _snapshot(after),
        "typical_led_rating": "20 mA (absolute maximum about 30 mA)",
        "model_note": "simplified model: the LED is treated as a fixed 2 V drop; wires and battery are ideal",
        "without_a_resistor": "nothing limits the current; the simplified model predicts unlimited current, and a "
                              "real LED would quickly burn out",
    }
    small = solve(c.with_resistance(10.0))
    facts["example_tiny_resistor"] = {"resistance": format_ohms(10.0), "current": format_ma(small.current_ma)}
    if before is not None and before.circuit.resistance_ohm != c.resistance_ohm:
        facts["before"] = _snapshot(before)
        if before.current_ma > 0 and after.current_ma > 0:
            ratio = max(before.current_ma, after.current_ma) / min(before.current_ma, after.current_ma)
            facts["change"] = {"current": "decreased" if after.current_ma < before.current_ma else "increased",
                               "factor": f"about {ratio:.1f} times"}
    if tool.tool == "explain":
        facts["question"] = tool.args.get("question", user_text)
        facts["teaching_goal"] = "answer the question directly, using these facts"
    elif "before" in facts:
        facts["teaching_goal"] = ("say how the current changed and why (resistance limits current), and what the "
                                  "learner sees: the LED glow and the speed of the current arrows")
    else:
        facts["teaching_goal"] = ("answer WHY an LED needs a resistor: the LED only drops about its forward voltage, "
                                  "so without a resistor nothing limits the current and it burns out; the resistor "
                                  "takes the rest of the battery voltage and sets the current")
    return facts


_NUM = r"(?<![\w.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_UNITS = [  # (class, regex, multiplier to the class's base unit)
    ("current", r"mA\b|milli-?amps?\b|milli-?amperes?\b", 1.0),
    ("current", r"A\b|amps?\b|amperes?\b", 1000.0),
    ("voltage", r"V\b|volts?\b", 1.0),
    ("resistance", r"kΩ|k\s*ohms?\b|kilo-?ohms?\b|kilohms?\b|k\b", 1000.0),
    ("resistance", r"Ω|ohms?\b", 1.0),
    ("ratio", r"times\b|x\b|×", 1.0),
]
_TOKEN_RE = re.compile(_NUM + r"(?:\s*(" + "|".join(f"(?:{u})" for _, u, _ in _UNITS) + r"))?")


def _tokens(text: str) -> list[tuple[str, float, str | None]]:
    """(raw number, value in the class's base unit, unit class or None) for every number in text."""
    out = []
    for m in _TOKEN_RE.finditer(text):
        raw, unit = m.group(1), m.group(2)
        value, cls = float(raw.replace(",", "")), None
        if unit:
            for c, pattern, mult in _UNITS:
                if re.fullmatch(pattern, unit):
                    value, cls = value * mult, c
                    break
        out.append((raw, value, cls))
    return out


def number_check(text: str, facts: dict) -> list[str]:
    """Numbers in `text` that do not match a number in the facts, to the text's own precision. A number with a
    unit (mA, V, ohms, times) must match a fact number of the SAME kind, so "3 mA" can't borrow "3.0 times"."""
    fact_tokens = _tokens(json.dumps(facts, ensure_ascii=False))
    any_vals = {v for _, v, _ in fact_tokens} | {float(r.replace(",", "")) for r, _, _ in fact_tokens}
    by_cls: dict[str, set[float]] = {}
    for _, v, c in fact_tokens:
        if c:
            by_cls.setdefault(c, set()).add(v)
    bad = []
    for raw, v, cls in _tokens(text):
        places = len(raw.split(".")[1]) if "." in raw else 0
        tol = 0.5 * 10 ** (-places) + 1e-9
        pool = by_cls.get(cls, set()) if cls else any_vals | {a / 1000 for a in any_vals if a >= 1000}
        scale = 1000.0 if cls == "resistance" and v >= 1000 and v != float(raw.replace(",", "")) else 1.0
        if not any(abs(v - a) <= tol * scale for a in pool):
            bad.append(raw)
    return bad


def template_explanation(facts: dict) -> str:
    if "now" not in facts:  # a built topic lesson: its verified key points are the answer
        return " ".join(facts.get("key_points", [])) or "The lesson is on screen."
    now = facts["now"]
    if "before" in facts:
        b = facts["before"]
        direction = facts.get("change", {}).get("current", "changed")
        return (f"The resistor went from {b['resistance']} to {now['resistance']}, so the current {direction} from "
                f"{b['current']} to {now['current']}. In our simplified model the LED holds about "
                f"{facts['led_forward_voltage']}, and the resistor sets the current with {facts['formula']}.")
    return (f"The LED holds about {facts['led_forward_voltage']}, so the resistor takes the remaining "
            f"{now['voltage_across_resistor']} and limits the current to {now['current']} ({facts['formula']}). "
            f"Without a resistor nothing would limit the current and the LED would burn out.")


def _clean(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S | re.I)
    text = text.replace("**", "").replace("`", "").strip().strip('"').strip()
    return " ".join(text.split())


# The conversation so far ([{"learner": ..., "poyo": ...}], oldest first), set by tutor.run_turn for the turn being
# answered. Every explanation sees it as CONTEXT (never as a source of numbers) so Poyo stays a continuing
# conversation: no re-introductions, no copy-pasted sentences, answers that react to what the learner said.
CONVERSATION: contextvars.ContextVar[tuple] = contextvars.ContextVar("td_conversation", default=())
CONTINUITY_NOTE = """
CONVERSATION shows the earlier turns of this conversation (context only: never a source of numbers). It is a
continuing conversation: do NOT greet the learner or introduce yourself, do NOT repeat sentences you already said,
and respond to what the learner just said in their own words.
"""
_GREETING = re.compile(r"(?i)^\W*(hi|hello|hey|greetings|welcome)\b|\b(i am|i'm|my name is) poyo\b|"
                       r"\bpoyo here\b|\byour (friendly )?(ai )?tutor\b")


def _norm_sentences(text: str) -> list[str]:
    return [re.sub(r"\W+", " ", s.lower()).strip() for s in re.split(r"(?<=[.!?])\s*", text or "")]


def continuity_check(text: str, conversation=None) -> list[str]:
    """Problems with an explanation given the conversation so far: a greeting / self-introduction after the first
    turn, or Poyo's last two replies copied word for word (the opening or closing sentence, two or more sentences,
    or a short reply that is mostly copied). Re-using one sentence in the middle of a rephrased answer is fine."""
    conversation = list(CONVERSATION.get() if conversation is None else conversation)
    if not conversation:
        return []
    out = []
    if _GREETING.search(text or ""):
        out.append("it greets the learner or introduces Poyo again in the middle of the conversation")
    earlier = {s for turn in conversation[-2:] for s in _norm_sentences(turn.get("poyo", "")) if len(s) >= 30}
    mine = [s for s in _norm_sentences(text) if s]
    copied = [s for s in mine if len(s) >= 30 and s in earlier]
    if copied and (len(copied) >= 2 or mine[0] in copied or mine[-1] in copied or len(mine) <= 2):
        out.append(f"it repeats, word for word, what Poyo already said: {copied[0]!r}")
    return out


KNOWLEDGE_NOTE = """
KNOWLEDGE below is trusted background from the tutor's knowledge base. Use it to answer accurately and to gently
correct a misconception the learner shows. It is NOT a source of numbers: use a number only if it is in FACTS.
"""


def explain(facts: dict, client: ModelClient, trace: Trace, knowledge: str = "", claim_check=None,
            shape_check=None, prompt: str | None = None, template: str | None = None) -> Explanation:
    """`knowledge`: prompt block from the knowledge base (system prompt; never part of the number check).
    `claim_check(text) -> [problems]`: rejects text that states a known misconception (1 retry, then template).
    `shape_check(text) -> problem | None`: rejects text with the wrong structure (e.g. a tour needs one sentence per
    stop so the camera can follow it); same 1 retry, then template.
    `prompt`: a different EXPLAINER system prompt (the Spot the Scam lesson); the checks stay the same.
    `template`: the deterministic fallback text (default: template_explanation(facts))."""
    convo = list(CONVERSATION.get())[-4:]
    system = (prompt or EXPLAIN_PROMPT) + (CONTINUITY_NOTE if convo else "") + (
        KNOWLEDGE_NOTE + "KNOWLEDGE:\n" + knowledge if knowledge else "")
    history = ("CONVERSATION (earlier turns, oldest first; context only, not a source of numbers):\n"
               + "\n".join(f"learner: {t.get('learner', '')}\nPoyo: {t.get('poyo', '')}" for t in convo) + "\n\n"
               if convo else "")
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": history + ("FACTS (from the lesson's modules):\n" if prompt else
                                                       "FACTS (from the calculation module):\n")
                                            + json.dumps(facts, indent=1, ensure_ascii=False)
                                            + "\nWrite the explanation now."}]
    rejected: list[str] = []
    live = True
    for attempt in (1, 2):
        reply = client.complete(messages, max_tokens=400)
        live = reply.live
        text = _clean(reply.content)
        bad = number_check(text, facts)
        too_long = len(text.split()) > 90
        wrong = claim_check(text) if claim_check and text and not bad and not too_long else []
        if text and not bad and not too_long and not wrong and reply.live:  # MOCK text is deterministic rules
            wrong = [f"conversation rule: {c}" for c in continuity_check(text, convo)]
        shape = shape_check(text) if shape_check and text and not bad and not too_long and not wrong else None
        trace.add("explain", f"{'LIVE' if reply.live else 'MOCK'} {reply.model} explanation attempt {attempt} "
                             f"({reply.latency_s:.1f}s): "
                             + ("numbers verified against the calculation" if not bad and text and not too_long
                                and not wrong and not shape else
                                f"rejected ({'empty' if not text else 'too long' if too_long else 'states a known misconception or breaks a rule: ' + '; '.join(wrong) if wrong else shape if shape else 'numbers not in facts: ' + ', '.join(bad)})"))
        if text and not bad and not too_long and not wrong and not shape:
            return Explanation(text, "nemotron" if reply.live else "mock", attempt, rejected)
        rejected.append(text)
        if attempt == 1:
            problem = ("it used numbers that are not in FACTS: " + ", ".join(bad)) if bad else \
                      ("it states a known misconception or breaks a rule: " + "; ".join(wrong)) if wrong else \
                      shape if shape else \
                      ("it was empty" if not text else "it was too long")
            messages = messages + [{"role": "assistant", "content": reply.content[:2000]},
                                   {"role": "user", "content": f"Rejected: {problem}. Rewrite it "
                                                               + ("following FACTS.teaching_goal exactly (short "
                                                                  "sentences, at most 85 words)" if shape_check else
                                                                  "in at most 3 short sentences")
                                                               + " using only numbers from FACTS."}]
    trace.add("explain", "using the deterministic template (model text failed the checks twice)")
    return Explanation(template or template_explanation(facts), "template", 2, rejected)
