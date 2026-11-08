"""Check Blender's read-back of the Spot the Scam scene against the request (never trust the request's word).

The worker reads every value back from Blender's own data (text bodies, visibility, custom properties,
bounding boxes); these checks compare that with what the director sent, and confirm that the fictional
message on the phone never changes after it is built.
"""

from __future__ import annotations

from ..circuit.check import Check, summary  # noqa: F401 - same Check / summary as the circuit lesson
from . import scenario


def _message_ok(scam: dict) -> tuple[bool, str]:
    segs = scam.get("segments") or {}
    wrong = [s["id"] for s in scenario.SEGMENTS if segs.get(s["id"]) != s["text"]]
    if scam.get("sender") != scenario.SENDER:
        wrong.append("sender")
    return not wrong, ("differs: " + ", ".join(wrong)) if wrong else ""


def run(request: dict, before: dict | None, result: dict) -> list[Check]:
    """request = scam_request_v1 sent; before = inspect result (or None); result = worker's answer."""
    checks: list[Check] = []
    add = lambda ok, what, detail="": checks.append(Check(bool(ok), what, detail))  # noqa: E731
    scam = result.get("scam") or {}
    action = request["action"]
    add(result.get("status") == "ok" and result.get("applied") == 1, "Blender applied the request",
        f"status={result.get('status')} errors={result.get('errors')}")
    add(scam.get("present"), "the phone is in the scene")
    if not scam.get("present"):
        return checks
    ok, detail = _message_ok(scam)
    add(ok, "the phone shows the fictional message exactly (sender + 4 parts)", detail)
    add("FICTIONAL" in (scam.get("banner") or ""), "the FICTIONAL label is on screen", repr(scam.get("banner")))
    tags = scam.get("tags") or {}
    outcomes = scam.get("outcomes") or {}
    if action == "build":
        add(scam.get("scenario") == request["scenario"], "scenario stored", repr(scam.get("scenario")))
        add(scam.get("title") == request["title"], "title shown", repr(scam.get("title")))
        add(not tags, "no warning signs shown before the learner looks", ", ".join(tags))
        add(not outcomes.get("active"), "no outcomes yet")
    elif action == "mark":
        add(tags == request["tags"], "warning-sign tags = the detector's signs, at the right parts",
            f"got {sorted(tags)} want {sorted(request['tags'])}")
    elif action == "outcomes":
        choice = request["choice"]
        add(outcomes.get("active") and outcomes.get("choice") == choice, "both outcomes on screen, choice stored",
            f"choice={outcomes.get('choice')!r}")
        add(outcomes.get("marked") == [choice], "only the learner's choice is marked YOUR CHOICE",
            repr(outcomes.get("marked")))
        heads = outcomes.get("headlines") or {}
        add(len(heads) == 2 and all("hypothetical simulation" in h for h in heads.values()),
            "both outcomes are labeled hypothetical simulations", repr(sorted(heads.values())))
        a = " ".join((outcomes.get("follow_link") or {}).get("texts", []))
        b = " ".join((outcomes.get("verify_official") or {}).get("texts", []))
        add("SIMULATED" in a and ".example" in a, "outcome A: the look-alike page is labeled SIMULATED (.example address)")
        add("7726" in b and "official app" in b, "outcome B: official app + report to 7726 shown")
        xa = (outcomes.get("follow_link") or {}).get("center_x")
        xb = (outcomes.get("verify_official") or {}).get("center_x")
        add(xa is not None and xb is not None and xa < 0 < xb, "A left of the message phone, B right of it",
            f"A x={xa} B x={xb}")
        add((result.get("direction") or {}).get("focus") == "outcomes", "camera framed on both outcomes",
            repr((result.get("direction") or {}).get("focus")))
    elif action == "clear_outcomes":
        add(not outcomes.get("active"), "outcomes removed; the message phone stays")
    if before and (before.get("scam") or {}).get("present") and action != "build":
        keep = ("phone_1", "sender") + tuple(s["id"] for s in scenario.SEGMENTS)
        uids = lambda blk: {o["id"]: o["uid"] for o in blk.get("objects", []) if o["id"] in keep}  # noqa: E731
        add(len(uids(scam)) == len(keep) and uids(before["scam"]) == uids(scam),
            "same phone and message objects as before (not rebuilt)")
    return checks
