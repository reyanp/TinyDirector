"""Build and open the tutor's SQLite knowledge base.

seed.json (reviewed in git) -> build() -> <shared>/state/knowledge.sqlite (runtime, gitignored; override with
TD_KNOWLEDGE_DB). ensure() rebuilds automatically when seed.json changes. A build refuses inconsistent data:
unknown references, bad regexes, a prerequisite cycle, a template whose lesson spec does not validate, or a
"verified" fact whose calculator does not reproduce its numbers.

Fact status (set by the build, never by hand):
  verified    the fact names a calculator check; the calculator was re-run and produced the expected outputs,
              and every number in the fact's text is one of the calculator's inputs or outputs
  sourced     cited to a source, not machine-checked
  assumption  a simplification this tutor's models make (cited to the model code)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

from .. import paths
from . import registry

SEED = Path(__file__).with_name("seed.json")
SCHEMA_VERSION = 1

DDL = """
PRAGMA foreign_keys = ON;
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE sources (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL, license TEXT NOT NULL);
CREATE TABLE concepts (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, short TEXT NOT NULL, summary TEXT NOT NULL,
  demo TEXT NOT NULL CHECK (demo IN ('interactive', 'lesson', 'explanation_only')),
  depth INTEGER NOT NULL);
CREATE TABLE aliases (
  alias TEXT NOT NULL, concept_id TEXT NOT NULL REFERENCES concepts(id), PRIMARY KEY (alias, concept_id));
CREATE TABLE relations (
  src TEXT NOT NULL REFERENCES concepts(id),
  rel TEXT NOT NULL CHECK (rel IN ('requires', 'uses', 'is_a', 'contrasts')),
  dst TEXT NOT NULL REFERENCES concepts(id), note TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (src, rel, dst), CHECK (src <> dst));
CREATE TABLE checks (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('calculator', 'detector', 'text_check', 'readback', 'spec_lint')),
  description TEXT NOT NULL);
CREATE TABLE concept_checks (
  concept_id TEXT NOT NULL REFERENCES concepts(id), check_id TEXT NOT NULL REFERENCES checks(id),
  PRIMARY KEY (concept_id, check_id));
CREATE TABLE facts (
  id TEXT PRIMARY KEY, concept_id TEXT NOT NULL REFERENCES concepts(id),
  kind TEXT NOT NULL CHECK (kind IN ('definition', 'law', 'worked_example', 'rule_of_thumb', 'typical_value',
                                     'model_assumption')),
  text TEXT NOT NULL, source_id TEXT NOT NULL REFERENCES sources(id),
  status TEXT NOT NULL CHECK (status IN ('verified', 'sourced', 'assumption')),
  check_id TEXT REFERENCES checks(id), check_args TEXT, check_expect TEXT);
CREATE TABLE misconceptions (
  id TEXT PRIMARY KEY, concept_id TEXT NOT NULL REFERENCES concepts(id), wrong TEXT NOT NULL,
  correction TEXT NOT NULL, trigger TEXT NOT NULL, claim TEXT);
CREATE TABLE templates (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('interactive', 'lesson')), tool TEXT NOT NULL,
  calculator TEXT NOT NULL REFERENCES checks(id), title TEXT NOT NULL, question TEXT NOT NULL,
  main_concept TEXT NOT NULL REFERENCES concepts(id), spec_json TEXT);
CREATE TABLE template_concepts (
  template_id TEXT NOT NULL REFERENCES templates(id), concept_id TEXT NOT NULL REFERENCES concepts(id),
  PRIMARY KEY (template_id, concept_id));
CREATE VIRTUAL TABLE search USING fts5(kind UNINDEXED, ref UNINDEXED, concept_id UNINDEXED, body,
                                       tokenize = 'porter unicode61');
"""

FACT_KIND_ORDER = ["definition", "law", "worked_example", "rule_of_thumb", "typical_value", "model_assumption"]
TEMPLATE_TOOLS = {"interactive": {"teach_led_resistor", "teach_scam_texts"}, "lesson": {"teach_topic"}}
# what a demo is driven by: a calculator (numbers) or, for the scam lesson, the heuristic detector
DEMO_CHECK_KINDS = ("calculator", "detector")


class KnowledgeError(ValueError):
    """The seed data is inconsistent; nothing was written."""


def default_path() -> Path:
    return Path(os.environ.get("TD_KNOWLEDGE_DB") or paths.STATE / "knowledge.sqlite")


def seed_hash(seed_path: Path = SEED) -> str:
    return hashlib.sha256(seed_path.read_bytes()).hexdigest()[:16]


def _depths(concepts: list[str], relations: list[dict]) -> dict[str, int]:
    """Longest prerequisite chain under each concept (requires / is_a edges point at what comes first)."""
    parents: dict[str, list[str]] = {c: [] for c in concepts}
    for r in relations:
        if r["rel"] in ("requires", "is_a"):
            parents[r["src"]].append(r["dst"])
    depth: dict[str, int] = {}
    visiting: set[str] = set()

    def visit(c: str) -> int:
        if c in depth:
            return depth[c]
        if c in visiting:
            raise KnowledgeError(f"prerequisite cycle through {c!r}")
        visiting.add(c)
        depth[c] = 1 + max((visit(p) for p in parents[c]), default=-1)
        visiting.discard(c)
        return depth[c]

    for c in concepts:
        visit(c)
    return depth


def _verify_fact(f: dict) -> list[str]:
    """Re-run the fact's calculator; the expected outputs must match and the text may only use its numbers."""
    from ..circuit.agent import number_check
    chk = f["check"]
    try:
        out = registry.calculator(chk["id"])(chk["args"])
    except Exception as exc:  # noqa: BLE001 - reported as a build error
        return [f"fact {f['id']}: {chk['id']} failed: {exc}"]
    errs = [f"fact {f['id']}: {chk['id']} gives {k} = {out.get(k)!r}, the fact expects {v!r}"
            for k, v in chk["expect"].items() if out.get(k) != v]
    shown = {k: v for k, v in out.items() if isinstance(v, str)} | {"args": chk["args"]}
    bad = number_check(f["text"], shown)
    if bad:
        errs.append(f"fact {f['id']}: numbers {bad} in its text are not inputs or outputs of {chk['id']}")
    return errs


def _validate(seed: dict) -> list[str]:
    errs: list[str] = []
    concepts = {c["id"] for c in seed["concepts"]}
    sources = {s["id"] for s in seed["sources"]}
    checks = {c["id"]: c for c in seed["checks"]}
    if set(checks) != set(registry.CHECKS):
        errs.append(f"checks in seed.json and registry.CHECKS differ: {sorted(set(checks) ^ set(registry.CHECKS))}")
    for r in seed["relations"]:
        for end in ("src", "dst"):
            if r[end] not in concepts:
                errs.append(f"relation {r}: unknown concept {r[end]!r}")
    for f in seed["facts"]:
        if f["concept"] not in concepts:
            errs.append(f"fact {f['id']}: unknown concept {f['concept']!r}")
        if f.get("source") not in sources:
            errs.append(f"fact {f['id']}: every fact needs a known source, got {f.get('source')!r}")
        if f["kind"] not in FACT_KIND_ORDER:
            errs.append(f"fact {f['id']}: unknown kind {f['kind']!r}")
        if f.get("check"):
            if not f["check"]["id"].startswith("calc."):
                errs.append(f"fact {f['id']}: only calculator checks can verify a fact")
            else:
                errs += _verify_fact(f)
    for m in seed["misconceptions"]:
        if m["concept"] not in concepts:
            errs.append(f"misconception {m['id']}: unknown concept {m['concept']!r}")
        for key in ("trigger", "claim"):
            if m.get(key):
                try:
                    re.compile(m[key])
                except re.error as exc:
                    errs.append(f"misconception {m['id']}: bad {key} regex: {exc}")
    for cid, ids in seed["concept_checks"].items():
        if cid not in concepts:
            errs.append(f"concept_checks: unknown concept {cid!r}")
        errs += [f"concept_checks {cid}: unknown check {i!r}" for i in ids if i not in checks]
    for t in seed["templates"]:
        if t["tool"] not in TEMPLATE_TOOLS.get(t["kind"], ()):
            errs.append(f"template {t['id']}: tool {t['tool']!r} does not fit kind {t['kind']!r}")
        if checks.get(t["calculator"], {}).get("kind") not in DEMO_CHECK_KINDS:
            errs.append(f"template {t['id']}: a demo needs a working calculator or detector, got {t['calculator']!r}")
        errs += [f"template {t['id']}: unknown concept {c!r}" for c in t["concepts"] + [t["main_concept"]]
                 if c not in concepts]
        if t["kind"] == "lesson":
            errs += _validate_template_spec(t)
    for kind in ("concepts", "facts", "misconceptions", "templates", "sources"):
        ids = [item["id"] for item in seed[kind]]
        errs += [f"duplicate {kind} id {d!r}" for d in sorted({i for i in ids if ids.count(i) > 1})]
    return errs


def _validate_template_spec(t: dict) -> list[str]:
    from ..lesson.director_agent import check
    try:
        spec = registry.template_spec(t.get("spec", ""))
    except KeyError as exc:
        return [f"template {t['id']}: {exc}"]
    errs, _ = check(spec)
    tool = t["calculator"].removeprefix("calc.")
    if not any(c["tool"] == tool for c in spec["calculations"]):
        errs.append(f"its spec does not use {tool}")
    return [f"template {t['id']}: {e}" for e in errs]


def build(db_path: Path | None = None, seed_path: Path = SEED) -> dict:
    """Validate seed.json and write a fresh database atomically. Returns counts."""
    db_path = Path(db_path or default_path())
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    errs = _validate(seed)
    if errs:
        raise KnowledgeError("knowledge seed is inconsistent:\n- " + "\n- ".join(errs))
    concept_ids = [c["id"] for c in seed["concepts"]]
    depth = _depths(concept_ids, seed["relations"])
    demo = {c: "explanation_only" for c in concept_ids}
    for t in sorted(seed["templates"], key=lambda t: t["kind"] != "interactive"):  # interactive wins
        for c in t["concepts"]:
            if demo[c] == "explanation_only":
                demo[c] = t["kind"]
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_name(f".{db_path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        con.executescript(DDL)
        meta = {"schema_version": str(SCHEMA_VERSION), "seed_sha256": seed_hash(seed_path),
                "built_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        con.executemany("INSERT INTO meta VALUES (?, ?)", meta.items())
        con.executemany("INSERT INTO sources VALUES (?, ?, ?, ?)",
                        [(s["id"], s["title"], s["url"], s["license"]) for s in seed["sources"]])
        con.executemany("INSERT INTO concepts VALUES (?, ?, ?, ?, ?, ?)",
                        [(c["id"], c["name"], c["short"], c["summary"], demo[c["id"]], depth[c["id"]])
                         for c in seed["concepts"]])
        con.executemany("INSERT INTO aliases VALUES (?, ?)",
                        [(a.lower(), c["id"]) for c in seed["concepts"] for a in sorted(set(c["aliases"]))])
        con.executemany("INSERT INTO relations VALUES (?, ?, ?, ?)",
                        [(r["src"], r["rel"], r["dst"], r.get("note", "")) for r in seed["relations"]])
        con.executemany("INSERT INTO checks VALUES (?, ?, ?)",
                        [(c["id"], c["kind"], c["description"]) for c in seed["checks"]])
        con.executemany("INSERT INTO concept_checks VALUES (?, ?)",
                        [(c, i) for c, ids in seed["concept_checks"].items() for i in ids])
        for f in seed["facts"]:
            chk = f.get("check")
            status = "verified" if chk else "assumption" if f["kind"] == "model_assumption" else "sourced"
            con.execute("INSERT INTO facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (f["id"], f["concept"], f["kind"], f["text"], f["source"], status,
                         chk["id"] if chk else None, json.dumps(chk["args"]) if chk else None,
                         json.dumps(chk["expect"], ensure_ascii=False) if chk else None))
        con.executemany("INSERT INTO misconceptions VALUES (?, ?, ?, ?, ?, ?)",
                        [(m["id"], m["concept"], m["wrong"], m["correction"], m["trigger"], m.get("claim"))
                         for m in seed["misconceptions"]])
        for t in seed["templates"]:
            spec = json.dumps(registry.template_spec(t["spec"]), ensure_ascii=False) if t.get("spec") else None
            con.execute("INSERT INTO templates VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (t["id"], t["kind"], t["tool"], t["calculator"], t["title"], t["question"],
                         t["main_concept"], spec))
            con.executemany("INSERT INTO template_concepts VALUES (?, ?)", [(t["id"], c) for c in t["concepts"]])
        rows = [("concept", c["id"], c["id"], f"{c['name']}. {c['summary']}") for c in seed["concepts"]]
        rows += [("fact", f["id"], f["concept"], f["text"]) for f in seed["facts"]]
        rows += [("misconception", m["id"], m["concept"], f"{m['wrong']} {m['correction']}")
                 for m in seed["misconceptions"]]
        con.executemany("INSERT INTO search (kind, ref, concept_id, body) VALUES (?, ?, ?, ?)", rows)
        bad = con.execute("PRAGMA foreign_key_check").fetchall()
        if bad:
            raise KnowledgeError(f"foreign key violations: {bad[:5]}")
        con.commit()
        counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608 - fixed table names
                  for t in ("concepts", "relations", "facts", "misconceptions", "templates", "checks", "sources")}
        counts["verified_facts"] = con.execute("SELECT COUNT(*) FROM facts WHERE status = 'verified'").fetchone()[0]
    finally:
        con.close()
    os.replace(tmp, db_path)
    return counts


_LOCK = threading.Lock()


def ensure(db_path: Path | None = None) -> Path:
    """Path to an up-to-date database, (re)building it from seed.json when missing or stale."""
    db_path = Path(db_path or default_path())
    with _LOCK:
        if db_path.exists():
            try:
                con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                try:
                    row = con.execute("SELECT value FROM meta WHERE key = 'seed_sha256'").fetchone()
                finally:
                    con.close()
                if row and row[0] == seed_hash():
                    return db_path
            except sqlite3.DatabaseError:
                pass
        build(db_path)
    return db_path


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Read-only connection (the tutor never writes to the knowledge base at runtime)."""
    con = sqlite3.connect(f"file:{ensure(db_path)}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con
