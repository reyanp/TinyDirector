"""The tutor's educational knowledge base (SQLite): concepts, prerequisites, sourced and verified facts,
misconceptions, demo templates, and links to the calculators / verifiers. docs/KNOWLEDGE.md."""

from __future__ import annotations

import threading

from .db import KnowledgeError, build, default_path, ensure
from .retrieve import Knowledge, KnowledgeBase

_KB: KnowledgeBase | None = None
_LOCK = threading.Lock()


def get_kb() -> KnowledgeBase:
    """Process-wide read-only knowledge base (built from seed.json on first use)."""
    global _KB
    with _LOCK:
        if _KB is None:
            _KB = KnowledgeBase()
        return _KB


__all__ = ["Knowledge", "KnowledgeBase", "KnowledgeError", "build", "default_path", "ensure", "get_kb"]
