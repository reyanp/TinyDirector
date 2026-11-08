"""Known-wrong statements in model text. A guard, not a proof: it can only reject explanations that state one
of the misconceptions in the knowledge base. Passing it does NOT make an explanation verified."""

from __future__ import annotations

import re
import sqlite3

# a sentence that negates or labels the idea ("current is NOT used up", "a common myth is ...") is a correction
NEGATION = re.compile(r"\b(not|never|no|cannot|myth|misconception|instead|rather than|wrong|mistake)\b|n't\b", re.I)
_SENTENCE = re.compile(r"[^.!?]+[.!?]?")


def find_wrong_claims(text: str, con: sqlite3.Connection) -> list[dict]:
    """Misconceptions (id, wrong, correction, sentence) that `text` states as true."""
    rows = con.execute("SELECT id, wrong, correction, claim FROM misconceptions WHERE claim IS NOT NULL").fetchall()
    found = []
    for sentence in (s.strip() for s in _SENTENCE.findall(text or "")):
        if not sentence or NEGATION.search(sentence):
            continue
        for r in rows:
            if re.search(r["claim"], sentence.lower()) and all(f["id"] != r["id"] for f in found):
                found.append({"id": r["id"], "wrong": r["wrong"], "correction": r["correction"], "sentence": sentence})
    return found
