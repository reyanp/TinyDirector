"""The one 'Spot the Scam' scenario: a FICTIONAL smishing text, and the two hypothetical outcomes.

Nothing here is real: the sender uses the 555-01xx range reserved for fiction, the link uses the reserved
`.example` domain (RFC 2606, it can never resolve to a real site), and "CarrierAlert" is not a real company.
No T-Mobile dataset is available locally, so this example was written for the lesson and is labeled FICTIONAL
everywhere it appears. Blender only draws these strings; nothing is ever opened, sent or collected.
"""

from __future__ import annotations

import time

SCENARIO_ID = "carrier_signin_alert"
TITLE = "Spot the Scam"
QUESTION = "Would you follow this link, or verify the message through your carrier's official app?"
SENDER = "+1 (469) 555-0142"
SENDER_NOTE = "Unknown number · not in your contacts"
TIME = "Today 9:41 AM"
LINK = "http://carrier-verify-acct.example/login"
# The message, in the segments Blender draws (and the camera can point at).
SEGMENTS = [
    {"id": "brand", "text": "CarrierAlert:"},
    {"id": "body", "text": "Unusual sign-in detected on your account."},
    {"id": "urgency", "text": "Verify within 24 hrs or your service will be suspended."},
    {"id": "link", "text": LINK},
]
MESSAGE = " ".join(s["text"] for s in SEGMENTS)
BANNER = "FICTIONAL EXAMPLE · educational simulation · not a real message"
LEGEND = ("Fictional message written for this lesson (no real company, number or website). "
          "Warning signs come from a heuristic prototype, not a trained classifier.")

OUTCOME_TITLE = "Two choices, two outcomes (hypothetical simulations)"
OUTCOME_LEGEND = ("Both outcomes are hypothetical educational simulations: no website is opened and nothing is "
                  "sent or collected.")
OUTCOMES = {
    "follow_link": {
        "headline": "SCENARIO A · Follow the link\n(hypothetical simulation)",
        "address": "carrier-verify-acct.example/login",
        "address_note": "Not your carrier's site:\na look-alike page",
        "page_title": "Verify your account",
        "fields": ["Phone number", "Account PIN", "Password"],
        "button": "Verify now",
        "result": "SIMULATED: anything\ntyped here goes straight\nto the scammer",
        "risk": "Risk: account takeover,\nnumber transfer (SIM swap),\nfraud charges",
    },
    "verify_official": {
        "headline": "SCENARIO B · Verify independently\n(hypothetical simulation)",
        "app_title": "Your carrier's\nofficial app",
        "app_note": "Opened yourself, not from the text",
        "status": "Account status:\nno alerts",
        "detail": "No unusual sign-in on record",
        "report": "Report the text:\nforward it to 7726 (SPAM),\nthen delete it",
        "result": "Safe: the warning was\nchecked without\ntouching the link",
    },
}
CHOICES = tuple(OUTCOMES)


def _rid(prefix: str) -> str:
    return f"{prefix}-{time.strftime('%H%M%S')}-{time.time_ns() % 10**6}"


def build_request(request_id: str | None = None) -> dict:
    """scam_request_v1 'build': the phone with the fictional message (no warning signs shown yet)."""
    return {"kind": "scam", "schema_version": 1, "request_id": request_id or _rid("scam"), "action": "build",
            "scenario": SCENARIO_ID, "title": TITLE, "banner": BANNER, "legend": LEGEND,
            "message": {"sender": SENDER, "sender_note": SENDER_NOTE, "time": TIME, "segments": SEGMENTS}}


def indicator_tags(detection) -> dict:
    """{segment target: short warning tag} for the warning signs the detector found on drawn segments."""
    tags = {}
    for ind in detection.indicators:
        if ind.target and ind.target not in tags:
            tags[ind.target] = ind.tag
    return tags


def mark_request(detection, request_id: str | None = None) -> dict:
    """scam_request_v1 'mark': show the detector's warning tags next to the segments they belong to."""
    return {"kind": "scam", "schema_version": 1, "request_id": request_id or _rid("scam"), "action": "mark",
            "tags": indicator_tags(detection)}


def outcomes_request(choice: str, request_id: str | None = None) -> dict:
    """scam_request_v1 'outcomes': both hypothetical outcomes side by side, the learner's choice marked."""
    if choice not in OUTCOMES:
        raise ValueError(f"unknown choice {choice!r}")
    return {"kind": "scam", "schema_version": 1, "request_id": request_id or _rid("scam"), "action": "outcomes",
            "choice": choice, "title": OUTCOME_TITLE, "legend": OUTCOME_LEGEND, "outcomes": OUTCOMES}


def clear_outcomes_request(request_id: str | None = None) -> dict:
    return {"kind": "scam", "schema_version": 1, "request_id": request_id or _rid("scam"), "action": "clear_outcomes"}
