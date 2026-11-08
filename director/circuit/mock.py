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
            body = user[user.index("FACTS ("):]  # after the optional CONVERSATION block
            content = self._explain(json.loads(body.split("\n", 1)[1].rsplit("\nWrite the explanation now.", 1)[0]))
        elif system.startswith("SCENE_DIRECTOR"):
            content = json.dumps(self._scene(_tag(user, "question")))
        else:
            content = "{}"
        return ModelReply(content=content, model=self.model, live=False)

    @staticmethod
    def _scene(question: str) -> dict:
        """OVERRIDE: a fixed 3-step MOCK design around the question (labeled MOCK; no knowledge in it)."""
        if re.search(r"(?i)\b(bomb|weapon|explosive|poison)\w*", question):
            return {"refuse": "That's not something I can make a lesson about, but I'm happy to teach you "
                              "something else."}
        topic = re.sub(r"[^A-Za-z0-9 '-]", "", question).strip()[:50] or "your question"
        return {"title": f"MOCK lesson: {topic}"[:60], "summary": "An offline MOCK design: three generic steps.",
                "layout": "flow",
                "elements": [{"id": "question", "label": "Your question", "shape": "person", "color": "#7FB8FF",
                              "size": "medium", "motion": "bob", "note": "what you asked"},
                             {"id": "idea", "label": "Key idea", "shape": "lamp", "color": "#FFC940",
                              "size": "large", "motion": "pulse", "glow": True, "note": "the main idea (MOCK)"},
                             {"id": "takeaway", "label": "Takeaway", "shape": "star", "color": "#6CCB5F",
                              "size": "medium", "motion": "none", "note": "what to remember (MOCK)"}],
                "links": [{"from": "question", "to": "idea", "label": "leads to"},
                          {"from": "idea", "to": "takeaway", "label": "gives"}],
                "narration": [{"focus": "overview", "say": "This is a MOCK lesson from the offline model, not "
                                                           "Nemotron."},
                              {"focus": "question", "say": "It starts with your question."},
                              {"focus": "idea", "say": "Nemotron would explain the key idea here."},
                              {"focus": "overview", "say": "Run it live to get a real generated lesson."}],
                "key_points": ["This is a MOCK lesson.", "A live run asks Nemotron to design the real one."],
                "simplified": "Everything: this is the offline MOCK design."}

    @staticmethod
    def _tool(message: str, state: str, knowledge: str = "") -> dict:
        low = message.lower()
        ohms = parse_ohms(message)
        no_circuit = state.startswith("No lesson")
        scam_on = "Spot the Scam lesson is on screen" in state
        if scam_on:
            outcomes_on = "hypothetical outcomes are shown" in state
            if re.search(r"\balready (typed|clicked|tapped|entered|gave|put)\b|\bi (typed|entered|gave)\b", low):
                return {"tool": "scam_recover", "args": {}, "reason": "details already shared"}
            if outcomes_on and re.search(r"\b(go back|back to the message|stop comparing)\b", low):
                return {"tool": "end_comparison", "args": {}, "reason": "back to the message"}
            if re.search(r"\b(instead|safe thing|official|verify|check (it|the app))\b", low):
                return {"tool": "scam_choose", "args": {"choice": "verify_official"}, "reason": "the safe choice"}
            if re.search(r"\b(follow|click|tap|open)\w*\b[^.?]*\blink\b|\bi'?d click\b", low):
                return {"tool": "scam_choose", "args": {"choice": "follow_link"}, "reason": "follow the link"}
            if re.search(r"\b(suspicious|red flags?|warning signs?|how (can|do) i tell|what makes)\b", low):
                return {"tool": "scam_inspect", "args": {}, "reason": "warning signs"}
        if "generated OVERRIDE lesson is on screen" in state and "teach" not in low and (
                "?" in message or re.search(r"\b(why|what|how|explain|show me|tell me|simpler)\b", low)):
            return {"tool": "explain", "args": {"question": message}, "reason": "question about the generated lesson"}
        if re.search(r"\b(scam|phishing|smishing|spam text|fraud)\w*", low):
            if scam_on and "?" in message and "teach" not in low:
                return {"tool": "explain", "args": {"question": message}, "reason": "question about the message"}
            return {"tool": "teach_scam_texts", "args": {}, "reason": "Spot the Scam lesson"}
        if scam_on and "teach" not in low and ("?" in message or re.search(r"\b(why|what|how|explain)\b", low)):
            return {"tool": "explain", "args": {"question": message}, "reason": "question about the message"}
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
    def _scam(f: dict) -> str:
        """Spot the Scam: rule-based text from the lesson's FACTS (MOCK)."""
        safe = f.get("safe_steps") or []
        if "learner_choice" in f:
            o = f["chosen_outcome"]
            return f"This is a hypothetical simulation. {o['result']}. " + " ".join(safe[1:3])
        if "steps_now" in f:
            return " ".join(f["steps_now"][:3])
        if "question" in f:
            return f"This fictional text shows {f['warning_sign_count']} warning signs. {safe[1]}"
        if "warning_signs" in f:
            return " ".join(f"{s['where'].capitalize()}: {s['sign'].lower()}." for s in f["warning_signs"]) + \
                " These signs come from a heuristic checklist, not a certainty."
        return "This phone shows a fictional text that claims to come from your carrier. Let's look closely first."

    @staticmethod
    def _explain(f: dict) -> str:
        if "lesson_on_screen" in f:  # a generated OVERRIDE lesson: its own key points (MOCK)
            return " ".join(f["lesson_on_screen"]["key_points"][:2])
        if "message_on_screen" in f or "learner_choice" in f or "steps_now" in f:
            return TutorMockModel._scam(f)
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
