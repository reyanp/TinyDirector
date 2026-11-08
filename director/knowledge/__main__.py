"""Knowledge base CLI.

  python -m director.knowledge build              # (re)build the SQLite file from seed.json, verifying facts
  python -m director.knowledge stats              # counts, path, fact status
  python -m director.knowledge query "How does a voltage divider work?"   # what retrieval gives Nemotron
  python -m director.knowledge graph              # nodes + edges JSON (what the knowledge map draws)
"""

from __future__ import annotations

import argparse
import json
import sys

from . import KnowledgeBase, KnowledgeError, build, default_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m director.knowledge", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("build", "stats", "query", "graph"))
    ap.add_argument("text", nargs="?", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        if args.command == "build":
            counts = build()
            print(f"built {default_path()}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
            return 0
        kb = KnowledgeBase()
    except KnowledgeError as exc:
        print(f"KNOWLEDGE BASE INVALID: {exc}", file=sys.stderr)
        return 3
    if args.command == "stats":
        rows = kb.con.execute("SELECT status, COUNT(*) FROM facts GROUP BY status").fetchall()
        demos = kb.con.execute("SELECT demo, group_concat(name, ', ') FROM concepts GROUP BY demo").fetchall()
        print(f"{default_path()}")
        print("facts: " + ", ".join(f"{s} {n}" for s, n in rows))
        for d, names in demos:
            print(f"{d}: {names}")
        return 0
    if args.command == "graph":
        print(json.dumps(kb.graph(), indent=1, ensure_ascii=False))
        return 0
    if not args.text:
        ap.error("query needs the question text")
    k = kb.retrieve(args.text)
    if args.json:
        print(json.dumps(k.summary(), indent=1, ensure_ascii=False))
        return 0
    print("tool selector sees:\n" + k.tool_hint())
    print("\nexplainer / director see:\n" + (k.prompt_block() or "(nothing)"))
    print("\nfact status: " + ", ".join(f"{f['id']}={f['status']}" for f in k.facts))
    print("checks that apply: " + ", ".join(c["id"] for c in k.checks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
