"""Offline stand-ins for Nemotron. Output is always labeled MOCK, never presented as a live call.

- ScriptedModel: returns pre-written responses in order (for pytest).
- RuleMockModel: a small keyword parser that produces schema-valid plans, so the
  CLI/bridge can be demoed with no network. It reads the same prompt the real
  model gets (user request + manifest) from the messages.
"""

from __future__ import annotations

import json
import re

from .nemotron_client import ModelReply

MOCK_MODEL_NAME = "mock/rule-based (NOT Nemotron)"


class ScriptedModel:
    live = False

    def __init__(self, responses: list[str], model: str = "mock/scripted"):
        self.responses = list(responses)
        self.model = model
        self.calls: list[list[dict]] = []

    def complete(self, messages, *, max_tokens=None) -> ModelReply:
        self.calls.append(messages)
        if not self.responses:
            raise AssertionError("ScriptedModel ran out of responses")
        return ModelReply(content=self.responses.pop(0), model=self.model, live=False)


COLORS = {
    "red": "#D95C52", "blue": "#3B6FD9", "green": "#4CAF50", "yellow": "#F2C94C", "orange": "#F2994A",
    "purple": "#9B51E0", "pink": "#EB6FA3", "white": "#F5F5F5", "black": "#222222", "gray": "#888888",
    "grey": "#888888", "brown": "#8B5A2B", "teal": "#2BB3A3", "gold": "#D4AF37",
}
ASSETS = ["mug", "tree", "rock", "cube", "sphere", "cylinder", "cone", "table", "chair", "lamp", "tent",
          "campfire", "ground"]
ALIASES = {"cup": "mug", "box": "cube", "ball": "sphere", "stone": "rock", "fire": "campfire", "floor": "ground",
           "trees": "tree", "rocks": "rock", "mugs": "mug", "chairs": "chair", "cubes": "cube"}
NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
UNKNOWN_THINGS = ["dragon", "car", "dog", "cat", "person", "house", "castle", "robot", "unicorn", "spaceship"]


def _extract(messages, tag):
    text = "\n".join(m["content"] for m in messages if m["role"] == "user")
    m = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", text, re.S)
    return m.group(1) if m else ""


class RuleMockModel:
    live = False
    model = MOCK_MODEL_NAME

    def complete(self, messages, *, max_tokens=None) -> ModelReply:
        request = _extract(messages, "user_request").lower()
        try:
            objects = json.loads(_extract(messages, "scene_manifest") or "[]")
        except json.JSONDecodeError:
            objects = []
        mode_hint = _extract(messages, "requested_mode").strip()
        plan = self._plan(request, objects, mode_hint)
        return ModelReply(content=json.dumps(plan), model=self.model, live=False)

    def _plan(self, text: str, objects: list[dict], mode_hint: str) -> dict:
        words = re.findall(r"[a-z]+", text)
        words = [ALIASES.get(w, w) for w in words]
        color = next((COLORS[w] for w in words if w in COLORS), None)
        mentioned = [w for w in words if w in ASSETS]
        unknown = [w for w in words if w in UNKNOWN_THINGS]
        base = {"schema_version": 1, "request_id": "pending"}

        if unknown and not mentioned:
            return {**base, "mode": "clarify", "summary": f"Unsupported object: {unknown[0]}", "operations": [],
                    "message": f"I can't build a {unknown[0]}. Supported assets: {', '.join(ASSETS)}."}

        by_asset: dict[str, list[dict]] = {}
        for o in objects:
            by_asset.setdefault(o["asset"], []).append(o)
        edit_verbs = {"change", "make", "turn", "paint", "move", "delete", "remove", "bigger", "smaller",
                      "spin", "rotate", "only", "just", "recolor", "color", "shift"}
        is_edit = mode_hint == "edit" or (mode_hint != "create" and objects and edit_verbs & set(words)
                                          and any(a in by_asset for a in mentioned)
                                          and not {"add", "create"} & set(words))

        if is_edit:
            ops = []
            targets = [o for a in mentioned for o in by_asset.get(a, [])]
            if not targets:
                return {**base, "mode": "clarify", "summary": "No matching object", "operations": [],
                        "message": "I couldn't find that object in the scene: " + ", ".join(mentioned or ["?"])}
            for o in targets:
                if {"delete", "remove"} & set(words):
                    ops.append({"op": "delete", "id": o["id"]})
                    continue
                setv = {}
                if color:
                    setv["color"] = color
                if "bigger" in words or "larger" in words:
                    setv["scale"] = round(o.get("scale", 1) * 1.5, 3)
                if "smaller" in words:
                    setv["scale"] = round(o.get("scale", 1) * 0.6, 3)
                pos = list(o.get("position", [0, 0, 0]))
                for w, axis, d in (("left", 0, -1), ("right", 0, 1), ("up", 2, 1), ("down", 2, -1),
                                   ("forward", 1, 1), ("back", 1, -1)):
                    if w in words:
                        pos[axis] += d
                        setv["position"] = pos
                if setv:
                    ops.append({"op": "modify", "id": o["id"], "set": setv})
                if {"spin", "rotate"} & set(words):
                    ops.append({"op": "animate", "id": o["id"], "property": "rotation",
                                "start_frame": 1, "end_frame": 48, "to": [0, 0, 360]})
            if not ops:
                return {**base, "mode": "clarify", "summary": "Unclear edit", "operations": [],
                        "message": "What should change? Try a color, size, position, spin, or delete."}
            return {**base, "mode": "edit", "summary": text[:120], "operations": ops}

        # create
        counts = {a: len(v) for a, v in by_asset.items()}
        used = {o["id"] for o in objects}
        ops, x = [], -3.0
        for i, w in enumerate(words):
            if w not in ASSETS:
                continue
            prev = words[i - 1] if i else ""
            n = NUMBERS.get(prev, int(prev) if prev.isdigit() else 1)
            scale = 0.6 if "small" in words[max(0, i - 3):i] else (1.8 if {"big", "large", "huge"} & set(words[max(0, i - 3):i]) else 1)
            local_color = next((COLORS[c] for c in words[max(0, i - 3):i] if c in COLORS), None)
            for _ in range(min(n, 5)):
                counts[w] = counts.get(w, 0) + 1
                oid = f"{w}_{counts[w]}"
                while oid in used:
                    counts[w] += 1
                    oid = f"{w}_{counts[w]}"
                used.add(oid)
                op = {"op": "add", "id": oid, "asset": w, "position": [x, 0, 0], "scale": scale}
                if local_color:
                    op["color"] = local_color
                ops.append(op)
                x += 2.0
        if not ops:
            return {**base, "mode": "clarify", "summary": "Nothing recognized", "operations": [],
                    "message": f"I didn't recognize any objects. Supported assets: {', '.join(ASSETS)}."}
        return {**base, "mode": "create", "summary": text[:120], "operations": ops[:24]}
