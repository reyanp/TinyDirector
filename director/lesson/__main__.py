"""Multi-agent lesson builder CLI.

  python -m director.lesson --text "Why would someone use a BJT over a MOSFET?"            # build + verify only
  python -m director.lesson --text "..." --send          # then load the verified lesson into the open Blender
  python -m director.lesson --text "..." --mock --builder deterministic   # offline (MOCK director)

Agents: Nemotron = director (lesson spec) + visual verifier; OpenAI (OPENAI_API_KEY) = builder (scene code).
Without OPENAI_API_KEY the deterministic builder is used and the output says so.
Exit codes: 0 ok / out of scope, 1 model call failed, 2 config, 3 spec invalid, 4 bridge timeout, 5 build/verify failed.
"""

from __future__ import annotations

import argparse
import json
import sys

from .. import bridge, paths
from ..nemotron_client import ConfigError, ModelCallError
from . import pipeline
from .agents import make_clients, send_to_blender


def print_lesson(lesson: pipeline.Lesson) -> None:
    r = lesson.resolved
    print(f"\n{r['title']}")
    for k in r["key_points"]:
        print(f"  • {k}")
    for i, s in enumerate(r["steps"], 1):
        print(f"  step {i}: {s['caption']}")
    for cid, res in (lesson.results or {}).items():
        if res.get("assumptions"):
            print(f"  (model assumptions, {cid}: {res['assumptions']})")
    print(f"\nbuilder: {lesson.builder}{' (cached)' if lesson.cached else ''}; verifier: {lesson.report.summary()}")
    for w in lesson.report.warnings[:6]:
        print(f"  warning: {w}")
    for n in lesson.notes:
        print(f"  note: {n}")
    print(f"files: {lesson.out_dir}  (preview.png, lesson.blend, spec.json, code_*.py)")


def main(argv: list[str] | None = None) -> int:
    paths.load_env()
    ap = argparse.ArgumentParser(prog="python -m director.lesson", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--text", required=True, help="the learner's question")
    ap.add_argument("--send", action="store_true", help="load the verified lesson into the running Blender worker")
    ap.add_argument("--builder", choices=("auto", "openai", "deterministic"), default="auto")
    ap.add_argument("--no-cache", action="store_true", help="rebuild even if this question is cached")
    ap.add_argument("--no-vision", action="store_true", help="skip the Nemotron vision check")
    ap.add_argument("--mock", action="store_true", help="offline MOCK director (and MOCK builder without a key)")
    ap.add_argument("--timeout", type=float, default=60.0, help="seconds to wait for Blender when --send")
    ap.add_argument("--json", action="store_true", help="print the lesson record as JSON")
    ap.add_argument("--no-knowledge", action="store_true", help="do not give the director knowledge-base context")
    args = ap.parse_args(argv)
    try:
        director, builder, vision = make_clients(args.mock, args.builder, not args.no_vision)
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2
    knowledge = None
    if not args.no_knowledge:
        from ..knowledge import get_kb
        knowledge = get_kb().retrieve(args.text)
        if knowledge.concepts:
            print(f"[knowledge] {', '.join(c['name'] for c in knowledge.concepts)}; {len(knowledge.facts)} facts"
                  + (f"; reference spec: {knowledge.template['title']}" if knowledge.template else ""))
    try:
        lesson = pipeline.build_lesson(args.text, director, builder, vision, use_cache=not args.no_cache,
                                       knowledge=knowledge)
    except ModelCallError as exc:
        print(f"MODEL CALL FAILED: {exc}", file=sys.stderr)
        return 1
    except pipeline.LessonError as exc:
        print(f"LESSON FAILED: {exc}", file=sys.stderr)
        return 3 if "spec invalid" in str(exc) else 5
    if lesson.unsupported:
        print(f"Out of scope for a circuit lesson: {lesson.unsupported}")
        return 0
    if args.json:
        print(json.dumps(json.loads((lesson.out_dir / "lesson.json").read_text(encoding="utf-8")), indent=1,
                         ensure_ascii=False))
    else:
        print_lesson(lesson)
    if args.send:
        try:
            send_to_blender(lesson, args.timeout)
        except bridge.BridgeTimeout as exc:
            print(f"BRIDGE TIMEOUT: {exc}", file=sys.stderr)
            return 4
        except pipeline.LessonError as exc:
            print(f"LOAD FAILED: {exc}", file=sys.stderr)
            return 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
