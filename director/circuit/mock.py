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
            content = json.dumps(self._tool(_tag(user, "user_message"), _tag(user, "circuit_state")))
        elif system.startswith("EXPLAINER"):
            content = self._explain(json.loads(user.split("\n", 1)[1].rsplit("\nWrite the explanation now.", 1)[0]))
        else:
            content = "{}"
        return ModelReply(content=content, model=self.model, live=False)

    @staticmethod
    def _tool(message: str, state: str) -> dict:
        low = message.lower()
        ohms = parse_ohms(message)
        no_circuit = state.startswith("No lesson")
        if ohms is not None and any(w in low for w in ("resistor", "ohm", "Ω".lower(), "back", "change", "make", "set")):
            return {"tool": "set_resistance", "args": {"resistance_ohm": ohms}, "reason": "user named a value"}
        if any(w in low for w in ("teach", "lesson", "show me", "need a resistor", "need resistors")) and (
                no_circuit or "teach" in low):
            return {"tool": "teach_led_resistor", "args": {}, "reason": "start the lesson"}
        if "?" in message or any(w in low for w in ("why", "what", "how", "explain")):
            return {"tool": "explain", "args": {"question": message}, "reason": "question"}
        return {"tool": "clarify", "args": {"message": "This lesson can change the resistor value or answer "
                                                       "questions about the circuit."}, "reason": "unsupported"}

    @staticmethod
    def _explain(f: dict) -> str:
        now = f["now"]
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
