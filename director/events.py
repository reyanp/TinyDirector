"""Structured execution events for the companion UI (and anything else that wants to watch a turn).

An emitter is any callable  emit(stage, status, title, detail="", **data).  Events describe REAL execution:
"start" is sent right before a step actually runs, "ok"/"error" right after it finishes. Nothing here invents
progress, and no model reasoning text or secrets are put into events.

stages: understand, scene, plan, calculate, explain, apply, verify, director, builder, blender, load, turn
status: start | ok | error | info
"""

from __future__ import annotations

from typing import Protocol


class Emitter(Protocol):
    def __call__(self, stage: str, status: str, title: str, detail: str = "", /, **data) -> None: ...


def noop(stage: str, status: str, title: str, detail: str = "", /, **data) -> None:
    return None
