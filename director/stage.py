"""Stage direction, director side: what each spoken sentence is about (cues), and direct_request_v1
requests that point Blender's camera at it, ring it, and show the sentence as the scene's caption.

Display only: a cue never changes a lesson value. Cues are deterministic keyword rules over the verified
explanation text (no model call), so "the resistor ..." looks at the resistor and "the current ..." at the
glowing current arrows, in the order Poyo says them.
"""

from __future__ import annotations

import json
import re
import time
from functools import lru_cache

from jsonschema import Draft202012Validator

from . import paths
from .circuit.display import wrap_caption

CIRCUIT_TARGETS = ("battery", "resistor", "led", "current", "overview")
# earliest mention in a sentence wins; "forward voltage" belongs to the LED, plain "voltage" to the battery
# (case-insensitive except "LED", so the verb "led" does not count)
CIRCUIT_WORDS = [
    ("led", r"(?i:\bforward voltage\b|\bdimmer\b|\bbrighter\b|\bglow\w*|\blight\b|\bburns? out\b)|\bLEDs?\b"),
    ("resistor", r"(?i:\bresist(?:or|ors|ance)\b|\bohms?\b|Ω|\bkilohms?\b)"),
    ("current", r"(?i:\bcurrent\b|\bmilli-?amps?\b|\bamps?\b|\barrows?\b|\bflow\w*\b|\belectrons?\b)|\bmA\b"),
    ("battery", r"(?i:\bbatter(?:y|ies)\b|\bvolts?\b|\bvoltage\b|\bsupply\b)"),
]
PART_WORDS = {  # generated lessons: part type -> words that name it
    "resistor": r"(?i:\bresist(?:or|ors|ance)\b|\bohms?\b|Ω)",
    "led": r"\bLEDs?\b|(?i:\bdimmer\b|\bbrighter\b|\bglow\w*)",
    "battery": r"(?i:\bbatter(?:y|ies)\b|\bsupply\b)",
    "npn_bjt": r"\bBJTs?\b|(?i:\bbase\b|\bbipolar\b|\bcollector\b)",
    "n_mosfet": r"\bMOSFETs?\b|\bFETs?\b|(?i:\bgate\b|\bdrain\b)",
    "signal": r"(?i:\blogic\b|\bsignal\b|\bpin\b|\bmicrocontroller\b)",
    "capacitor": r"(?i:\bcapacitors?\b)",
    "switch": r"(?i:\bswitch(?:es)?\b|\bbutton\b)",
}


def sentences(text: str) -> list[str]:
    """Split after . ! ? followed by a space, so "21.2 mA" and "7.0 V" stay whole."""
    return [s for s in re.split(r"(?<=[.!?])\s+", " ".join((text or "").split())) if s]


SUBJECTS = r"(?:current|LEDs?|resistors?|resistance|battery|arrows|markers)"
CLAUSE_BREAK = re.compile(
    r"(?<=[,;])\s+(?=(?:so|which|that's why|because|and so)\b)"   # ", so the LED ..."
    r"|(?<=;)\s+"                                                    # "...; the ..."
    r"|\s+(?=because\b)"                                             # "... dimmer because the resistance ..."
    rf"|(?<=,)\s+(?=(?:and |but )?the {SUBJECTS}\b)")                 # "..., the current falls ..."
MIN_SEGMENT = 16


def segments(text: str) -> list[str]:
    """Sentences, further split where a clause starts a new subject, so the camera can follow a sentence like
    "... the resistor went up, so the LED gets dimmer." Very short pieces join the next one."""
    out = []
    for s in sentences(text):
        parts = [p for p in CLAUSE_BREAK.split(s) if p]
        merged: list[str] = []
        for p in parts:
            if merged and len(merged[-1]) < MIN_SEGMENT:
                merged[-1] = f"{merged[-1]} {p}"
            else:
                merged.append(p)
        if len(merged) > 1 and len(merged[-1]) < MIN_SEGMENT:
            merged[-2:] = [f"{merged[-2]} {merged[-1]}"]
        out += merged
    return out


def _first(sentence: str, rules: list[tuple[str, str]]) -> str | None:
    best = None
    for target, rx in rules:
        m = re.search(rx, sentence)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), target)
    return best[1] if best else None


def cues(text: str, kind: str | None, parts: dict | None = None, opening: str | None = None) -> list[dict]:
    """[{"text": sentence, "focus": target or None (keep the current view), "caption": str or None}].
    kind "circuit" = the LED tutor (caption = the scene's subtitle), "topic" = a generated lesson (focus on
    part ids from `parts` {id: {"type", "label"}}), None = nothing on stage (no direction)."""
    if kind is None:
        return []
    out = []
    for i, s in enumerate(segments(text)):
        focus = None
        if kind == "circuit":
            focus = _first(s, CIRCUIT_WORDS)
        elif kind == "topic" and parts:
            focus = _part_for(s, parts)
        if i == 0 and opening:
            focus = opening
        out.append({"text": s, "focus": focus, "caption": wrap_caption(s) if kind == "circuit" else None})
    return out


def _part_for(sentence: str, parts: dict) -> str | None:
    for pid, p in parts.items():  # a part named by its label ("R1", "R2") beats a part type
        tag = (p.get("label") or "").split(" ")[0]
        if re.fullmatch(r"[A-Za-z]{1,3}\d{1,2}", tag) and re.search(rf"\b{re.escape(tag)}\b", sentence):
            return pid
    rules = [(ptype, rx) for ptype, rx in PART_WORDS.items()]
    ptype = _first(sentence, rules)
    return next((pid for pid, p in parts.items() if p.get("type") == ptype), None) if ptype else None


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads((paths.SCHEMA_DIR / "direct_request_v1.json").read_text(encoding="utf-8")))


def tour_cues(text: str, stops: list[str], callouts: dict) -> list[dict]:
    """A guided tour: the camera visits `stops` in order while Poyo narrates. Each spoken piece goes to the stop
    it names (keywords, never going backwards); if that misses a stop, the pieces are shared out evenly.
    `callouts` {stop: {"value", "note"} | None} come from the calculation module."""
    pieces = sentences(text)
    if len(pieces) == len(stops):  # the narration was written (and checked) as one sentence per stop
        return [{"text": s, "focus": t, "caption": wrap_caption(s), "callout": callouts.get(t)}
                for s, t in zip(pieces, stops)]
    if len(pieces) < len(stops):
        pieces = segments(text)
    idx, at = [], 0
    for i, s in enumerate(pieces):
        named = _first(s, [(t, rx) for t, rx in CIRCUIT_WORDS if t in stops[at:]])
        if i == 0:
            at = 0
        elif named is not None:
            at = max(at, stops.index(named))
        idx.append(at)
    if set(idx) != set(range(len(stops))) and len(pieces) >= len(stops):
        idx = [i * len(stops) // len(pieces) for i in range(len(pieces))]
    if pieces and stops[-1] == "overview" and len(pieces) >= 2:
        idx[-1] = len(stops) - 1  # the tour always ends on the whole circuit
    return [{"text": s, "focus": stops[k], "caption": wrap_caption(s), "callout": callouts.get(stops[k])}
            for s, k in zip(pieces, idx)]


SIDE_WORDS = {"alternative": r"(?i:\bwhat-?if\b|\bright\b|\bthe copy\b|\bnew one\b)",
              "baseline": r"(?i:\boriginal\b|\bleft\b|\bcurrent one\b)"}


def compare_cues(text: str, sides: dict, callouts: dict) -> list[dict]:
    """Split-reality comparison: a piece that names the what-if values (its resistance or current) looks at the
    right-hand copy, one naming the circuit's own values at the original, otherwise both. `sides`
    {"baseline"|"alternative": (resistance_ohm, current_ma)} are calculated values."""
    from .circuit.agent import _tokens

    def side_of(piece: str) -> str | None:
        for _, v, cls in _tokens(piece):  # the earliest calculated value in the piece decides
            for side, (ohms, ma) in sides.items():
                if (cls == "resistance" and abs(v - ohms) <= max(0.5, ohms * 0.01)) or \
                        (cls == "current" and abs(v - ma) <= 0.55):
                    return side
        return _first(piece, list(SIDE_WORDS.items()))

    pieces = segments(text)
    out, focus = [], "compare"
    for i, s in enumerate(pieces):  # an unnamed piece ("so its LED is dimmer") stays on the side just named
        focus = "compare" if i == len(pieces) - 1 and len(pieces) > 2 else side_of(s) or focus
        out.append({"text": s, "focus": focus, "caption": wrap_caption(s), "callout": callouts.get(focus)})
    return out


def direct_request(focus: str | None = None, caption: str | None = None, transition_s: float = 0.9,
                   highlight: bool = True, callout: dict | None = None) -> dict:
    req = {"kind": "direct", "schema_version": 1, "request_id": f"direct-{time.strftime('%H%M%S')}-{time.time_ns() % 10**6}",
           "transition_s": transition_s, "highlight": highlight}
    if focus is not None:
        req["focus"] = focus
    if caption is not None:
        req["caption"] = caption[:400]
    if callout is not None:
        req["callout"] = callout
    errs = [e.message for e in _validator().iter_errors(req)]
    if errs:
        raise ValueError("invalid direct request: " + "; ".join(errs))
    return req
