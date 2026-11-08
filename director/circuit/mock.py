"""Offline stand-in for Nemotron in the circuit tutor. Always labeled MOCK, never presented as live.

It answers the same two prompts the real model gets (TOOL_SELECTOR, EXPLAINER) with simple rules, and
its explanations reuse the fact strings verbatim, so they pass the number check.
"""

from __future__ import annotations

import json
import re

from ..nemotron_client import ModelReply

MOCK_TUTOR_NAME = "mock/tutor-rules (NOT Nemotron)"
_OHMS_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(k(?:ilo)?\b)?\s*(ohms?|Ω|ω)?", re.I)


def _tag(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", text, re.S)
    return m.group(1) if m else ""


def parse_ohms(message: str) -> float | None:
    """First resistor value in the message: '1,000 ohms' -> 1000, '2.2k' -> 2200, '330' -> 330."""
    for m in _OHMS_RE.finditer(message):
        value = float(m.group(1).replace(",", ""))
        if m.group(2):
            return value * 1000
        if m.group(3) or value >= 10:
            return value
    return None


class TutorMockModel:
    live = False
    model = MOCK_TUTOR_NAME

    def complete(self, messages, *, max_tokens=None) -> ModelReply:
        system = messages[0]["content"]
        user = "\n".join(m["content"] for m in messages if m["role"] == "user")
        if system.startswith("TOOL_SELECTOR"):
            content = json.dumps(self._tool(_tag(user, "user_message"), _tag(user, "circuit_state"),
                                            _tag(user, "knowledge")))
        elif system.startswith("EXPLAINER"):
            content = self._explain(json.loads(user.split("\n", 1)[1].rsplit("\nWrite the explanation now.", 1)[0]))
        else:
            content = "{}"
        return ModelReply(content=content, model=self.model, live=False)

    @staticmethod
    def _tool(message: str, state: str, knowledge: str = "") -> dict:
        low = message.lower()
        ohms = parse_ohms(message)
        no_circuit = state.startswith("No lesson")
        if any(w in low for w in ("bjt", "mosfet", "transistor", "divider")):
            return {"tool": "teach_topic", "args": {"question": message}, "reason": "new topic"}
        comparing = "comparison is on screen" in state
        if comparing and re.search(r"\b(go back|back to the original|original|stop comparing|remove the copy)\b", low):
            return {"tool": "end_comparison", "args": {}, "reason": "back to the original"}
        if not no_circuit and re.search(r"\b(compare|side by side|both)\b", low):
            if ohms is not None:
                return {"tool": "compare_resistance", "args": {"resistance_ohm": ohms}, "reason": "what-if copy"}
            up = not re.search(r"\b(decrease|smaller|lower|reduce)\b", low)
            return {"tool": "compare_resistance", "args": {"direction": "increase" if up else "decrease"},
                    "reason": "what-if copy, no value"}
        if not no_circuit and re.search(r"\b(what'?s happening|walk me through|tour|inside (this|the) circuit)\b", low):
            return {"tool": "tour_circuit", "args": {"stops": ["battery", "resistor", "current", "led", "overview"]},
                    "reason": "guided tour"}
        if ohms is not None and any(w in low for w in ("resistor", "ohm", "Ω".lower(), "back", "change", "make", "set")):
            return {"tool": "set_resistance", "args": {"resistance_ohm": ohms}, "reason": "user named a value"}
        if not no_circuit and re.search(r"\b(increase|decrease|bigger|smaller|higher|lower|raise|reduce)\b", low) \
                and re.search(r"\b(resist\w*|ohms?)\b", low) and not low.startswith(("why", "what if", "how")):
            up = re.search(r"\b(increase|bigger|higher|raise)\b", low) is not None
            return {"tool": "adjust_resistance", "args": {"direction": "increase" if up else "decrease"},
                    "reason": "change without a number"}
        view = re.search(r"\b(show me|zoom|look at|focus on|point at|go back to)\b", low)
        if not no_circuit and view and "teach" not in low:
            target = next((t for t, rx in (("resistor", r"resistor"), ("led", r"\bled\b"), ("battery", r"batter"),
                                           ("current", r"current|flow|arrow")) if re.search(rx, low)), "overview")
            return {"tool": "focus_camera", "args": {"target": target}, "reason": "view only"}
        no_demo = re.search(r"explain_concept (\{.*\})", knowledge)  # the knowledge base has no 3D demo for it
        if no_demo:
            return {"tool": "explain_concept", "args": json.loads(no_demo.group(1)), "reason": "explanation only"}
        if any(w in low for w in ("teach", "lesson", "show me", "need a resistor", "need resistors")) and (
                no_circuit or "teach" in low):
            return {"tool": "teach_led_resistor", "args": {}, "reason": "start the lesson"}
        if "?" in message or any(w in low for w in ("why", "what", "how", "explain")):
            return {"tool": "explain", "args": {"question": message}, "reason": "question"}
        return {"tool": "clarify", "args": {"message": "This lesson can change the resistor value or answer "
                                                       "questions about the circuit."}, "reason": "unsupported"}

    @staticmethod
    def _explain(f: dict) -> str:
        if "now" not in f:
            return " ".join(f.get("key_points", []))
        now = f["now"]
        if "comparison_on_screen" in f:  # a question while both circuits are on screen
            c = f["comparison_on_screen"]
            left, right = c["left_circuit_now"], c["right_what_if_copy"]
            return (f"The left circuit has {left['resistance']} and {left['current']}; the what-if copy on the right "
                    f"has {right['resistance']} and only {right['current']}. More resistance limits the current, so "
                    f"its LED glows {'dimmer' if c['what_if_current'] == 'lower' else 'brighter'}.")
        if "before" in f:
            b = f["before"]
            verb = f.get("change", {}).get("current", "changed")
            dimmer = "dimmer" if verb == "decreased" else "brighter"
            return (f"Changing the resistor from {b['resistance']} to {now['resistance']} {verb} the current from "
                    f"{b['current']} to {now['current']}, so the LED gets {dimmer}. This is our simplified model: "
                    f"the LED holds about {f['led_forward_voltage']}.")
        return (f"The LED keeps about {f['led_forward_voltage']} across itself, so the resistor takes the other "
                f"{now['voltage_across_resistor']} and sets the current to {now['current']}. Without a resistor, "
                f"nothing would limit the current and the LED would burn out.")
