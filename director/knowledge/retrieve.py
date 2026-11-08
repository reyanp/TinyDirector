"""Retrieval from the knowledge base: which concepts a question is about, the trusted facts and misconceptions
for them, the demo that can show them, and the checks that apply. Deterministic (alias phrases, misconception
triggers, SQLite FTS5 over facts), so tests can pin it down. Nothing here verifies a claim; see claims.py and
the calculators for that."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import claims, db

STOPWORDS = set("""a an and are as at be because been but by can could did do does doing for from get gets got had
has have how i if in into is it its it's me my of on or so than that the their them then there these they this to
too us was we were what when where which while who why will with would you your about tell explain teach show
learn understand work works working happen happens happening mean means need needs use used using want make
makes just like really some something thing things other more less much very please again simpler simply terms
example lesson circuit circuits question""".split())
MAX_PRIMARY = 3
MAX_FACTS = 8
FACTS_PER_CONCEPT = 3
DEMO_TEXT = {"interactive": "interactive 3D demo", "lesson": "3D lesson template", "explanation_only":
             "no 3D demo yet: explanation only"}


def normalize(text: str) -> str:
    text = (text or "").lower().replace("’", "'").replace("‘", "'").replace("ω", "Ω")
    return " ".join(text.split())


@dataclass
class Knowledge:
    query: str
    concepts: list[dict] = field(default_factory=list)  # primary: what the question is about, best first
    background: list[dict] = field(default_factory=list)  # their direct prerequisites
    facts: list[dict] = field(default_factory=list)
    misconceptions: list[dict] = field(default_factory=list)
    template: dict | None = None
    checks: list[dict] = field(default_factory=list)
    from_context: bool = False  # concepts carried over from the previous turn (follow-up)
    all_ids: frozenset = frozenset()  # every concept id in the knowledge base (to validate tool args)

    @property
    def ids(self) -> list[str]:
        return [c["id"] for c in self.concepts]

    @property
    def suggested(self) -> dict | None:
        """The demo the knowledge base has for the main concept (Nemotron still decides)."""
        if not self.concepts:
            return None
        if self.template is None:
            return {"tool": "explain_concept", "args": {"concept": self.concepts[0]["id"]}}
        if self.template["tool"] == "teach_topic":
            return {"tool": "teach_topic", "args": {"question": self.query}, "template": self.template["id"]}
        return {"tool": self.template["tool"], "args": {}, "template": self.template["id"]}

    def tool_hint(self) -> str:
        """Short block for the tool selector."""
        if not self.concepts:
            return "No concept in the knowledge base matched this message."
        lines = ["Concepts the knowledge base matched (best first):"]
        lines += [f"- {c['id']} ({c['name']}): {DEMO_TEXT[c['demo']]}" for c in self.concepts]
        s = self.suggested
        if s and s["tool"] == "explain_concept":
            lines.append(f"no 3D demo for the main concept: answer with explain_concept "
                         f"{json.dumps(s['args'], ensure_ascii=False)}")
        elif s and s["tool"] == "teach_topic":
            lines.append(f"demo for a NEW topic: teach_topic (lesson template {self.template['title']!r})")
        elif s:
            lines.append(f"demo: {s['tool']} (interactive: {self.template['title']})")
        return "\n".join(lines)

    def prompt_block(self) -> str:
        """Trusted background for the director / explainer prompts."""
        if not self.concepts:
            return ""
        out = ["Concepts: " + "; ".join(f"{c['name']} ({DEMO_TEXT[c['demo']]})" for c in self.concepts)
               + (" | builds on: " + ", ".join(c["name"] for c in self.background) if self.background else "")]
        if self.facts:
            out.append("Facts:")
            out += [f"- {f['text']}" for f in self.facts]
        if self.misconceptions:
            out.append("Common misconceptions (never state them; correct them if the learner shows one):")
            out += [f"- WRONG: {m['wrong']} RIGHT: {m['correction']}" for m in self.misconceptions]
        return "\n".join(out)

    def summary(self) -> dict:
        """JSON for events and the UI (no lesson spec, no regexes)."""
        return {"concepts": [{k: c[k] for k in ("id", "name", "demo")} for c in self.concepts],
                "background": [c["id"] for c in self.background],
                "facts": [{k: f[k] for k in ("id", "concept", "kind", "text", "status", "source")} for f in self.facts],
                "misconceptions": [{k: m[k] for k in ("id", "wrong", "correction", "triggered")}
                                   for m in self.misconceptions],
                "template": {k: self.template[k] for k in ("id", "title", "kind", "tool")} if self.template else None,
                "checks": [c["id"] for c in self.checks], "suggested": self.suggested,
                "from_context": self.from_context}


class KnowledgeBase:
    """Read-only access to the SQLite knowledge base. Safe to share between threads (one lock)."""

    def __init__(self, path: Path | None = None):
        self.con = db.connect(path)
        self._lock = threading.Lock()
        with self._lock:
            self._aliases = [(r["alias"], r["concept_id"], re.compile(r"(?<![\w'])" + re.escape(r["alias"]) + r"(?!\w)"))
                             for r in self.con.execute("SELECT alias, concept_id FROM aliases")]
            self._aliases.sort(key=lambda a: -len(a[0]))
            self._triggers = [(r["id"], r["concept_id"], re.compile(r["trigger"]))
                              for r in self.con.execute("SELECT id, concept_id, trigger FROM misconceptions")]
            self.concept_ids = {r[0] for r in self.con.execute("SELECT id FROM concepts")}

    def close(self) -> None:
        self.con.close()

    def _q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self._lock:
            return self.con.execute(sql, args).fetchall()

    # ---------------- scoring ----------------
    def _alias_scores(self, text: str) -> dict[str, float]:
        """Phrase matches; a longer phrase hides the shorter ones inside it ("voltage divider" is not "voltage")."""
        taken: list[tuple[int, int]] = []
        scores: dict[str, float] = {}
        for alias, cid, rx in self._aliases:
            for m in rx.finditer(text):
                if any(m.start() < e and s < m.end() for s, e in taken):
                    continue
                taken.append((m.start(), m.end()))
                scores[cid] = scores.get(cid, 0.0) + 2.0 + len(alias.split())
        return scores

    def _fts(self, text: str) -> list[sqlite3.Row]:
        words = [w for w in re.findall(r"[a-z][a-z0-9]+", text) if w not in STOPWORDS and len(w) > 2]
        if not words:
            return []
        query = " OR ".join(f'"{w}"' for w in dict.fromkeys(words))
        try:
            return self._q("SELECT kind, ref, concept_id, bm25(search) AS rank FROM search WHERE search MATCH ? "
                           "ORDER BY rank LIMIT 6", (query,))
        except sqlite3.OperationalError:
            return []

    # ---------------- retrieval ----------------
    def retrieve(self, question: str, *, prior: list[str] | tuple = (), follow_up: bool = False) -> Knowledge:
        text = normalize(question)
        scores: dict[str, float] = {}
        triggered: list[str] = []
        fts_facts: list[str] = []
        if not follow_up:
            scores = self._alias_scores(text)
            for mid, cid, rx in self._triggers:
                if rx.search(text):
                    triggered.append(mid)
                    scores[cid] = scores.get(cid, 0.0) + 2.0
            boosted: set[str] = set()
            for i, row in enumerate(self._fts(text)):  # full-text hits nudge a concept once; phrases decide
                if row["concept_id"] not in boosted:
                    boosted.add(row["concept_id"])
                    scores[row["concept_id"]] = scores.get(row["concept_id"], 0.0) + (1.0 if i == 0 else 0.5)
                if row["kind"] == "fact":
                    fts_facts.append(row["ref"])
        from_context = False
        if not scores or max(scores.values()) < 1.0:
            prior = [c for c in prior if c in self.concept_ids]
            if prior:
                scores, from_context = {c: 1.0 - i * 0.01 for i, c in enumerate(prior)}, True
        if not scores:
            return Knowledge(question, all_ids=frozenset(self.concept_ids))
        top = max(scores.values())
        ranked = sorted((c for c, s in scores.items() if s >= 0.5 * top), key=lambda c: (-scores[c], c))[:MAX_PRIMARY]
        return self._assemble(question, ranked, triggered, fts_facts, from_context)

    def for_concepts(self, ids: list[str], question: str = "") -> Knowledge:
        ids = [c for c in ids if c in self.concept_ids][:MAX_PRIMARY]
        return self._assemble(question, ids, [], [], False)

    def _assemble(self, question: str, ranked: list[str], triggered: list[str], fts_facts: list[str],
                  from_context: bool) -> Knowledge:
        k = Knowledge(question, from_context=from_context, all_ids=frozenset(self.concept_ids))
        if not ranked:
            return k
        marks = ",".join("?" * len(ranked))
        rows = {r["id"]: dict(r) for r in self._q(f"SELECT * FROM concepts WHERE id IN ({marks})", ranked)}
        k.concepts = [rows[c] for c in ranked]
        k.background = [dict(r) for r in self._q(
            f"SELECT DISTINCT c.* FROM relations r JOIN concepts c ON c.id = r.dst WHERE r.src IN ({marks}) "
            f"AND r.rel IN ('requires', 'is_a') ORDER BY c.depth, c.id", ranked) if r["id"] not in ranked][:4]
        order = {kind: i for i, kind in enumerate(db.FACT_KIND_ORDER)}
        facts = [dict(r) for r in self._q(
            f"SELECT f.*, s.title AS source_title, s.url AS source_url FROM facts f JOIN sources s ON s.id = f.source_id "
            f"WHERE f.concept_id IN ({marks})", ranked)]
        rank_of = {c: i for i, c in enumerate(ranked)}
        facts.sort(key=lambda f: (rank_of[f["concept_id"]], f["id"] not in fts_facts, order[f["kind"]], f["id"]))
        picked: list[dict] = []
        for cid in ranked:
            mine = [f for f in facts if f["concept_id"] == cid]
            take = mine[:FACTS_PER_CONCEPT]
            verified = next((f for f in mine if f["status"] == "verified"), None)
            if verified and verified not in take and take:  # keep one calculator-checked example when there is one
                take[-1] = verified
            picked += take
        for f in picked[:MAX_FACTS]:
            k.facts.append({"id": f["id"], "concept": f["concept_id"], "kind": f["kind"], "text": f["text"],
                            "status": f["status"], "check": f["check_id"],
                            "source": {"id": f["source_id"], "title": f["source_title"], "url": f["source_url"]}})
        mis = [dict(r) for r in self._q(
            f"SELECT id, concept_id, wrong, correction FROM misconceptions WHERE id IN ({','.join('?' * len(triggered))}) "
            f"OR concept_id IN ({marks})", [*triggered, *ranked])]
        mis.sort(key=lambda m: (m["id"] not in triggered, rank_of.get(m["concept_id"], 9), m["id"]))
        k.misconceptions = [{"id": m["id"], "concept": m["concept_id"], "wrong": m["wrong"],
                             "correction": m["correction"], "triggered": m["id"] in triggered} for m in mis[:3]]
        k.template = self._template(ranked)
        k.checks = [dict(r) for r in self._q(
            f"SELECT DISTINCT ch.id, ch.kind, ch.description FROM concept_checks cc JOIN checks ch ON ch.id = cc.check_id "
            f"WHERE cc.concept_id IN ({marks}) ORDER BY ch.kind, ch.id", ranked)]
        return k

    def _template(self, ranked: list[str]) -> dict | None:
        """Best demo for the MAIN concept: it must cover ranked[0]; its main concept and coverage break ties."""
        rows = self._q("SELECT t.*, group_concat(tc.concept_id) AS covers FROM templates t "
                       "JOIN template_concepts tc ON tc.template_id = t.id GROUP BY t.id")
        best, best_score = None, 0.0
        for r in rows:
            covers = set(r["covers"].split(","))
            if ranked[0] not in covers:
                continue
            score = 1.0 + (3.0 if r["main_concept"] == ranked[0] else 0.0) + (0.5 if r["kind"] == "interactive" else 0.0) + sum(
                1.0 / (i + 1) for i, c in enumerate(ranked) if c in covers)
            if score > best_score:
                best, best_score = r, score
        if best is None:
            return None
        t = {k: best[k] for k in ("id", "kind", "tool", "calculator", "title", "question", "main_concept")}
        t["spec"] = json.loads(best["spec_json"]) if best["spec_json"] else None
        return t

    # ---------------- claims + graph ----------------
    def wrong_claims(self, text: str) -> list[dict]:
        with self._lock:
            return claims.find_wrong_claims(text, self.con)

    def graph(self) -> dict:
        """Nodes + edges for the knowledge map, laid out in columns by prerequisite depth."""
        nodes = [dict(r) for r in self._q(
            "SELECT c.id, c.name, c.short, c.summary, c.demo, c.depth, "
            "(SELECT COUNT(*) FROM facts f WHERE f.concept_id = c.id) AS facts, "
            "(SELECT COUNT(*) FROM facts f WHERE f.concept_id = c.id AND f.status = 'verified') AS verified "
            "FROM concepts c ORDER BY c.depth, c.id")]
        edges = [dict(r) for r in self._q("SELECT src, rel, dst, note FROM relations ORDER BY src, rel, dst")]
        cols: dict[int, list[dict]] = {}
        for n in nodes:
            cols.setdefault(n["depth"], []).append(n)
        y_of: dict[str, float] = {}
        ndepth = max(cols) if cols else 0
        for d in sorted(cols):
            col = cols[d]
            def bary(n):  # average position of the neighbours already placed (fewer crossings)
                ys = [y_of[e["dst"]] for e in edges if e["src"] == n["id"] and e["dst"] in y_of] + \
                     [y_of[e["src"]] for e in edges if e["dst"] == n["id"] and e["src"] in y_of]
                return sum(ys) / len(ys) if ys else 0.5
            col.sort(key=lambda n: (bary(n), n["id"]))
            for i, n in enumerate(col):
                n["x"] = round(d / ndepth, 4) if ndepth else 0.5
                n["y"] = round((i + 0.5) / len(col), 4)
                y_of[n["id"]] = n["y"]
        return {"nodes": nodes, "edges": edges}
