"""Offline stand-ins for the lesson agents (always labeled MOCK, never presented as live calls)."""

from __future__ import annotations

import copy
import json

from ..nemotron_client import ModelReply
from . import fallback
from .examples import BJT_MOSFET_SPEC, LED_SPEC
from .spec import resolve


class MockDirector:
    """Picks a reference spec by keyword (the real director is Nemotron)."""
    live = False
    model = "MOCK-director"

    def complete(self, messages, *, max_tokens=None) -> ModelReply:
        q = messages[-1]["content"].lower() if messages[-1]["role"] == "user" else ""
        if "invalid" in q:  # corrective turn: answer with the last spec again
            q = next(m["content"].lower() for m in messages if m["role"] == "user")
        if any(w in q for w in ("bjt", "mosfet", "transistor")):
            spec = copy.deepcopy(BJT_MOSFET_SPEC)
        elif "led" in q or "resistor" in q:
            spec = copy.deepcopy(LED_SPEC)
        else:
            spec = {"unsupported": "MOCK director only knows the LED-resistor and BJT-vs-MOSFET lessons"}
        return ModelReply(content=json.dumps(spec, ensure_ascii=False), model=self.model, live=False)


class MockBuilder:
    """Returns the deterministic layout as if it were the builder agent's code (MOCK)."""
    live = False
    model = "MOCK-builder"

    def __init__(self, replies: list[str] | None = None):
        self.replies = list(replies or [])
        self.calls = 0

    def complete(self, messages, *, max_tokens=None) -> ModelReply:
        self.calls += 1
        if self.replies:
            return ModelReply(content=self.replies.pop(0), model=self.model, live=False)
        user = messages[1]["content"]
        draft = user[user.index("```python\n") + 10:user.rindex("```")]
        return ModelReply(content=f"```python\n{draft}```", model=self.model, live=False)


def resolved(spec: dict) -> tuple[dict, dict]:
    return resolve(spec)


def fallback_code(spec: dict) -> str:
    return fallback.code_for(resolve(spec)[0])
