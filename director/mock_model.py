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
          "cabin", "campfire", "ground"]
ALIASES = {"cup": "mug", "box": "cube", "ball": "sphere", "stone": "rock", "fire": "campfire", "floor": "ground",
           "hut": "cabin", "trees": "tree", "rocks": "rock", "mugs": "mug", "chairs": "chair", "cubes": "cube",
           "cabins": "cabin"}
NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "once": 1, "twice": 2}
UNKNOWN_THINGS = ["dragon", "car", "dog", "cat", "person", "house", "castle", "robot", "unicorn", "spaceship"]
SIZE_WORDS = {"small": 0.6, "tiny": 0.4, "big": 1.8, "large": 1.8, "huge": 2.5}
NEW_DETS = {"a", "an", "another", "new"} | {k for k in NUMBERS if k not in ("once", "twice")}
REF_DETS = {"the", "that", "this", "existing", "my"}
ADD_VERBS = {"add", "create", "put", "place", "build", "spawn"}
GAP = 0.8


def _extract(messages, tag):
    text = "\n".join(m["content"] for m in messages if m["role"] == "user")
    m = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", text, re.S)
    return m.group(1) if m else ""


def _words(text: str) -> list[str]:
    return [ALIASES.get(w, w) for w in re.findall(r"[a-z0-9]+", text.lower())]


class RuleMockModel:
    """Keyword planner for offline demos. It handles multi-part requests clause by clause
    ("add a tree and make the cube bounce three times"). It is NOT Nemotron and says so."""
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

    # ---- placement: never overlap, "next to X" = a gap beside X ----
    @staticmethod
    def _free_spot(scene: dict, asset: str, anchor: dict | None) -> list[float]:
        from .assets_info import footprint_radius as r
        solid = [o for o in scene.values() if o["asset"] != "ground"]

        def clear(x, y):
            return all(((x - o["position"][0]) ** 2 + (y - o["position"][1]) ** 2) ** 0.5
                       >= r(o["asset"]) * o.get("scale", 1) + r(asset) + GAP * 0.9 for o in solid)
        if anchor is not None:
            ax, ay = anchor["position"][0], anchor["position"][1]
            d = r(anchor["asset"]) * anchor.get("scale", 1) + GAP + r(asset)
            for dx, dy in ((d, 0), (-d, 0), (0, d), (0, -d)):
                if clear(round(ax + dx, 2), round(ay + dy, 2)):
                    return [round(ax + dx, 2), round(ay + dy, 2), 0]
        if not solid:
            return [-2.0, 0, 0]
        x = max(o["position"][0] + r(o["asset"]) * o.get("scale", 1) for o in solid) + GAP + r(asset)
        return [round(x, 2), 0, 0]

    def _plan(self, text: str, objects: list[dict], mode_hint: str) -> dict:
        base = {"schema_version": 1, "request_id": "pending"}
        all_words = _words(text)
        unknown = [w for w in all_words if w in UNKNOWN_THINGS]
        if unknown and not any(w in ASSETS for w in all_words):
            return {**base, "mode": "clarify", "summary": f"Unsupported object: {unknown[0]}", "operations": [],
                    "message": f"I can't build a {unknown[0]}. Supported assets: {', '.join(ASSETS)}."}

        scene = {o["id"]: dict(o) for o in objects}
        existing = set(scene)
        ops: list[dict] = []
        last = None  # most recently mentioned/created object, for "it" and "next to it"
        for clause in re.split(r"\s*(?:,|;|\bthen\b|\band\b)\s*", text):
            w = _words(clause)
            if not w:
                continue
            refs, adj_spans = [], set()
            anchor_ref = None
            for i, word in enumerate(w):
                if word not in ASSETS:
                    continue
                j, adj_colors, scale = i - 1, [], 1
                while j >= 0 and (w[j] in COLORS or w[j] in SIZE_WORDS or w[j] in ("only", "just")):
                    adj_spans.add(j)
                    if w[j] in COLORS:
                        adj_colors.append(COLORS[w[j]])
                    scale = SIZE_WORDS.get(w[j], scale)
                    j -= 1
                det = w[j] if j >= 0 else ""
                is_new = det in NEW_DETS or det.isdigit() or (
                    det not in REF_DETS and bool(ADD_VERBS & set(w)) and mode_hint != "edit")
                if is_new:
                    n = NUMBERS.get(det, int(det) if det.isdigit() else 1)
                    near = anchor_ref or (last if {"next", "beside", "near", "by"} & set(w) else None)
                    for _ in range(min(n, 5)):
                        k = 1
                        while f"{word}_{k}" in scene:
                            k += 1
                        oid = f"{word}_{k}"
                        op = {"op": "add", "id": oid, "asset": word,
                              "position": self._free_spot(scene, word, scene.get(near) if near else None),
                              "scale": scale}
                        if adj_colors:
                            op["color"] = adj_colors[0]
                        ops.append(op)
                        scene[oid] = {"id": oid, "asset": word, "position": op["position"], "scale": scale,
                                      "color": op.get("color")}
                        anchor_ref = anchor_ref or oid  # "a cube beside a cabin": cabin goes next to the cube
                        last = oid
                else:
                    cands = [o for o in scene.values() if o["asset"] == word]
                    if adj_colors:
                        cands = [o for o in cands if (o.get("color") or "").upper() == adj_colors[0].upper()] or cands
                    if not cands:
                        return {**base, "mode": "clarify", "summary": "No matching object", "operations": [],
                                "message": f"I couldn't find a {word} in the scene."}
                    refs.append(cands[-1]["id"])
                    last = anchor_ref = cands[-1]["id"]
            if not refs and "it" in w and last and not (ADD_VERBS & set(w)):
                refs = [last]
            colors = [COLORS[x] for i, x in enumerate(w) if x in COLORS and i not in adj_spans]
            for oid in refs:
                o = scene[oid]
                if {"delete", "remove"} & set(w):
                    ops.append({"op": "delete", "id": oid})
                    continue
                setv = {}
                if colors:
                    setv["color"] = colors[-1]
                if {"bigger", "larger"} & set(w):
                    setv["scale"] = round(o.get("scale", 1) * 1.5, 3)
                if "smaller" in w:
                    setv["scale"] = round(o.get("scale", 1) * 0.6, 3)
                pos = list(o.get("position", [0, 0, 0]))
                for word, axis, d in (("left", 0, -1), ("right", 0, 1), ("up", 2, 1), ("down", 2, -1),
                                      ("forward", 1, 1), ("back", 1, -1)):
                    if word in w:
                        pos[axis] += d
                        setv["position"] = pos
                if setv:
                    ops.append({"op": "modify", "id": oid, "set": setv})
                if {"spin", "rotate"} & set(w):
                    ops.append({"op": "animate", "id": oid, "property": "rotation",
                                "start_frame": 1, "end_frame": 48, "to": [0, 0, 360]})
                if {"bounce", "bounces", "bouncing", "hop", "jump"} & set(w):
                    n = 3
                    for i, x in enumerate(w):
                        if x in ("times", "bounces") and i and (w[i - 1] in NUMBERS or w[i - 1].isdigit()):
                            n = NUMBERS.get(w[i - 1]) or int(w[i - 1])
                        if x in ("once", "twice"):
                            n = NUMBERS[x]
                    ops.append({"op": "bounce", "id": oid, "cycles": max(1, min(n, 20)), "height": 1.0})
        if not ops:
            return {**base, "mode": "clarify", "summary": "Nothing recognized", "operations": [],
                    "message": "I didn't recognize what to do. Try adding a supported asset or changing color, "
                               f"size, position, spin, bounce, or delete. Assets: {', '.join(ASSETS)}."}
        touches_existing = any(op["id"] in existing for op in ops)
        mode = mode_hint if mode_hint in ("create", "edit") else ("edit" if touches_existing else "create")
        return {**base, "mode": mode, "summary": text[:120], "operations": ops[:24]}
