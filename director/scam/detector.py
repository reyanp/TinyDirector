"""HEURISTIC PROTOTYPE scam-text detector (keyword rules). Not a trained classifier.

T-Mobile's synthetic dataset, taxonomy and baseline classifier are NOT available in this repository, so this
small rule set stands in for them behind one function, `detect(text, sender_known=None) -> Detection`. A real
baseline could replace it behind the same function (adapter), without touching the lesson.

What it gives the lesson:
- a coarse risk category ("likely scam" / "suspicious" / "no warning signs found"), never a probability;
- the warning signs it matched, with the exact words as evidence and the message segment they sit in;
- a fixed statement of its uncertainty;
- protective actions (before, and after disclosing something).

Limits (stated to the learner): keyword rules miss new wording, can flag real alerts, ignore context, and were
written by the same people who wrote its tiny test set (director/scam/eval_set.py), so its measured numbers are
optimistic and say nothing about real-world accuracy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

NAME = "HEURISTIC PROTOTYPE (keyword rules, not a trained model)"
UNCERTAINTY = ("Heuristic prototype: keyword rules written for this lesson, not a trained model, so there is no "
               "calibrated probability. It can miss new scam wording and can flag real alerts. Treat it as a "
               "checklist, not a verdict.")

URL_RE = re.compile(r"(?i)\b(?:https?://[^\s]+|www\.[^\s]+|(?:[a-z0-9-]+\.)+(?:com|net|org|info|co|io|xyz|top|"
                    r"example|ly|me|us|biz|online|site|link|app)(?:/[^\s]*)?)")
SHORTENERS = ("bit.ly", "tinyurl.com", "t.co", "goo.gl", "rb.gy", "is.gd", "cutt.ly")
LOOKALIKE = re.compile(r"(?i)(verify|secure|login|log-in|signin|acct|account|update|unlock|support|billing|"
                       r"confirm|alert|refund|claim)")


@dataclass
class Indicator:
    id: str
    label: str          # one line for the learner
    tag: str            # a few words, drawn next to the segment in Blender
    evidence: str       # the words that matched
    target: str | None  # the message segment it belongs to (lesson scenario), or None


@dataclass
class Detection:
    category: str                      # "likely scam" | "suspicious" | "no warning signs found"
    indicators: list[Indicator] = field(default_factory=list)
    uncertainty: str = UNCERTAINTY
    actions: list[str] = field(default_factory=list)
    detector: str = NAME

    @property
    def ids(self) -> list[str]:
        return [i.id for i in self.indicators]

    def as_dict(self) -> dict:
        return {"detector": self.detector, "category": self.category, "uncertainty": self.uncertainty,
                "indicators": [vars(i) for i in self.indicators], "actions": self.actions}


RULES = [  # (id, label, tag, regex)
    ("impersonation", "Claims to be a company you trust (your carrier)", "Pretends to be your carrier",
     r"(?i)\b(carrier\w*|wireless|mobile account|bank|irs|usps|fedex|ups|amazon|apple|paypal|netflix|"
     r"support team|security team|account team)\b"),
    ("alarm", "A scary account alert to make you react", "Scary alert",
     r"(?i)\b(unusual|suspicious|unauthori[sz]ed|fraudulent) (sign-?in|activity|login|log-in|charge|access)\b|"
     r"\b(compromised|hacked|breach)\b"),
    ("urgency", "Pressure to act fast (a short deadline)", "Rush: 24-hour deadline",
     r"(?i)\bwithin \d+ ?(hrs?|hours?|minutes?|mins?|days?)\b|\b(immediately|urgent(ly)?|right away|asap|act now|"
     r"final (notice|warning)|today only|expires? (today|soon|tonight))\b"),
    ("threat", "A threat if you don't act (suspension, lockout)", "Threat: service suspended",
     r"(?i)\b(suspend\w*|locked|lock(ed)? out|deactivat\w*|terminat\w*|disconnect\w*|legal action|arrest\w*|"
     r"penalt\w*|blocked)\b"),
    ("verify_request", "Asks you to verify or sign in through the text", "Asks you to verify",
     r"(?i)\b(verify|confirm|validate|re-?activate|update) (your |the )?(account|identity|details|info\w*|payment)\b|"
     r"\bverify (within|now|here|today|immediately)\b|\b(log ?in|sign ?in) (here|now|at|to)\b"),
    ("credential_request", "Asks for a password, PIN or ID number", "Wants your password / PIN",
     r"(?i)\b(password|passcode|pin|ssn|social security|card number|cvv|bank details|security code)\b"),
    ("lure", "A prize, refund or payment problem as bait", "Bait: prize / payment",
     r"(?i)\b(you('ve| have)? won|winner|prize|gift card|reward|refund|overdue|unpaid|payment (failed|declined)|"
     r"redelivery fee|toll (balance|due)|package (is )?(held|on hold))\b"),
]
ACTIONS_BEFORE = [
    "Don't tap the link or reply, not even STOP.",
    "Check the claim yourself: open your carrier's official app, or type its website yourself, or call the number "
    "on your bill.",
    "Report it: forward the text to 7726 (SPAM), then delete it.",
]
ACTION_NO_PASSWORD = "Your carrier won't ask for your password or PIN through a text link."
ACTIONS_AFTER = [  # if something was already typed into the fake page
    "Change that password and your account PIN right away in the official app or website, and anywhere you reused it.",
    "Call your carrier on the number from its official website or your bill: report it and ask it to lock number "
    "transfers (SIM swap protection).",
    "Turn on 2-step verification, sign out other devices, and check for new devices or call forwarding.",
    "Watch your bank and card statements; report the scam at ReportFraud.ftc.gov, and use IdentityTheft.gov if ID "
    "numbers were taken.",
]


def _segment_of(start: int, segments: list[dict] | None) -> str | None:
    if not segments:
        return None
    pos = 0
    for s in segments:
        end = pos + len(s["text"])
        if pos <= start < end:
            return s["id"]
        pos = end + 1  # segments are joined with one space
    return None


def _links(text: str) -> list[tuple[int, str, list[str]]]:
    out = []
    for m in URL_RE.finditer(text):
        url = m.group(0).rstrip(".,)")
        host = re.sub(r"(?i)^https?://", "", url).split("/")[0].lower()
        why = []
        if url.lower().startswith("http://"):
            why.append("not https")
        if any(host == s or host.endswith("." + s) for s in SHORTENERS):
            why.append("shortened link hides where it goes")
        if re.fullmatch(r"[\d.]+", host):
            why.append("a bare IP address")
        if LOOKALIKE.search(host):
            why.append(f"look-alike name ({LOOKALIKE.search(host).group(0)} in {host})")
        if host.count("-") >= 2:
            why.append("many hyphens in the name")
        out.append((m.start(), url, why))
    return out


def detect(text: str, sender_known: bool | None = None, segments: list[dict] | None = None) -> Detection:
    """`sender_known`: is the sender in the learner's contacts (None = unknown to the detector).
    `segments`: the lesson's message segments (to say where each warning sign is)."""
    found: list[Indicator] = []
    if sender_known is False:
        found.append(Indicator("unknown_sender", "From a number you don't know", "Unknown sender",
                               "not in your contacts", "sender"))
    links = _links(text)
    for start, url, why in links:
        if why:
            found.append(Indicator("suspicious_link", "A link whose address isn't your carrier's real site",
                                   "Look-alike link", f"{url}: {', '.join(why)}", _segment_of(start, segments)))
        else:
            found.append(Indicator("link", "Wants you to tap a link", "Tap-a-link request", url,
                                   _segment_of(start, segments)))
        break  # one link indicator per message
    for rid, label, tag, rx in RULES:
        m = re.search(rx, text)
        if m:
            found.append(Indicator(rid, label, tag, m.group(0), _segment_of(m.start(), segments)))
    ids = {i.id for i in found} - {"unknown_sender"}
    strong = ids & {"suspicious_link", "credential_request", "verify_request", "threat", "lure"}
    has_link = bool(ids & {"suspicious_link", "link"})
    if (has_link and len(ids) >= 3) or (has_link and strong - {"suspicious_link"}) or len(strong) >= 3:
        category = "likely scam"
    elif ids - {"impersonation"} or (ids and sender_known is False):
        category = "suspicious"
    else:
        category = "no warning signs found"
    actions = list(ACTIONS_BEFORE) if category != "no warning signs found" else [
        "No rule matched, but that doesn't prove it's safe: if a text asks you to act, check it in the official app."]
    if ids & {"verify_request", "credential_request", "suspicious_link"}:
        actions.append(ACTION_NO_PASSWORD)
    return Detection(category, found, UNCERTAINTY, actions)


def detect_scenario():
    """The lesson's fictional message (the sender is not in the learner's contacts)."""
    from . import scenario
    return detect(scenario.MESSAGE, sender_known=False, segments=scenario.SEGMENTS)
