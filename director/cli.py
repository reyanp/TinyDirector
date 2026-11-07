"""TinyDirector CLI.

  python -m director.cli --text "Create a red mug next to a tree" --dry-run
  python -m director.cli --mode edit --text "Make only the mug blue" \
      --manifest director/tests/fixtures/scene_manifest.json --dry-run
  python -m director.cli --text "..." --simulate      # execute in the pure-Python simulator
  python -m director.cli --text "..." --send          # execute in Blender via shared/inbox -> outbox
  python -m director.cli --send                       # no --text: interactive multi-turn session
  add --mock to use the offline rule-based model instead of Nemotron (clearly labeled MOCK)

Exit codes: 0 ok/clarify, 1 model call failed, 2 config error, 3 invalid plan,
            4 bridge timeout, 5 execution error.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from . import bridge, paths
from .engine_sim import make_result
from .nemotron_client import ConfigError, ModelCallError, NemotronClient
from .planner import PlanError, plan as make_plan
from .schema import pretty

SIM_MANIFEST = paths.STATE / "simulated_manifest.json"


def _objs(manifest):
    return {o["id"]: o for o in (manifest or {}).get("scene_objects", [])}


def observe(before: dict | None, after: dict) -> str:
    b, a = _objs(before), _objs(after)
    added = [i for i in a if i not in b]
    removed = [i for i in b if i not in a]
    changed = [i for i in a if i in b and a[i] != b[i]]
    unchanged = [i for i in a if i in b and a[i] == b[i]]
    parts = [f"scene_version={after.get('scene_version')}"]
    for label, ids in (("added", added), ("changed", changed), ("removed", removed), ("unchanged", unchanged)):
        if ids:
            parts.append(f"{label}: {', '.join(ids)}")
    return "; ".join(parts)


def execute(plan: dict, args, manifest: dict | None, trace) -> tuple[int, dict | None]:
    """Run a validated plan in Blender (--send) or the simulator (--simulate). Returns (code, result)."""
    code, out = 0, None
    if args.send:
        try:
            out = bridge.send(plan, timeout=args.timeout)
        except bridge.BridgeTimeout as exc:
            trace.add("execute", "timed out waiting for Blender")
            print(f"BRIDGE TIMEOUT: {exc}", file=sys.stderr)
            return 4, None
        except (bridge.BridgeResultError, FileExistsError) as exc:
            trace.add("execute", f"bridge error: {exc}")
            print(f"BRIDGE ERROR: {exc}", file=sys.stderr)
            return 5, None
        executor = "SIMULATED worker" if out.get("executor") == "simulated" else out.get("executor", "Blender")
    else:
        prev = manifest or {"scene_version": 0, "scene_objects": []}
        out = make_result(plan["request_id"], prev.get("scene_version", 0), prev.get("scene_objects", []), plan)
        bridge.atomic_write_json(SIM_MANIFEST, out)
        executor = "SIMULATED engine (not Blender)"
    trace.add("execute", f"{executor} applied {out.get('applied', '?')} operation(s), status={out['status']}",
              errors=out.get("errors", []))
    trace.add("observe", observe(manifest, out) + (f"; snapshot {out['snapshot']}" if out.get("snapshot") else ""),
              scene_objects=[o["id"] for o in out["scene_objects"]])
    print(f"\n{executor}: status={out['status']} applied={out.get('applied')}  " + observe(manifest, out))
    for e in out.get("errors", []):
        print(f"  ! {e}", file=sys.stderr)
    if out["status"] != "ok":
        code = 5
    return code, out


_VERIFIER = None


def run_verify(text: str, plan: dict, out: dict, trace):
    """Ask Nemotron VL whether Blender's render matches the request. Never raises."""
    from .verifier import verify
    image = paths.OUTBOX / out["snapshot"]
    if not image.exists():
        trace.add("verify", f"skipped: snapshot {image.name} not found")
        return None
    try:
        verdict = verify(text, plan, out, image, _VERIFIER)
    except ModelCallError as exc:
        trace.add("verify", f"skipped: vision call failed ({exc})")
        print(f"(visual check skipped: {exc})", file=sys.stderr)
        return None
    tag = "LIVE" if verdict.live else "MOCK"
    status = "MATCHES" if verdict.matches else "MISMATCH"
    trace.add("verify", f"{tag} {verdict.model} looked at the render ({verdict.latency_s:.1f}s): {status} "
                        f"conf={verdict.confidence:.2f} - {verdict.observations}",
              issues=verdict.issues, fix_request=verdict.fix_request)
    print(f"Visual check: {status} (confidence {verdict.confidence:.2f}) - {verdict.observations}")
    for issue in verdict.issues:
        print(f"  ! {issue}")
    return verdict


def run_one(text: str, args, client, manifest: dict | None) -> tuple[int, dict | None]:
    """Plan + optionally execute one request. Returns (exit_code, new_manifest)."""
    try:
        result = make_plan(text, client, manifest=manifest, mode=args.mode)
    except PlanError as exc:
        print("PLAN REJECTED - nothing was executed.", file=sys.stderr)
        for e in exc.errors:
            print(f"  - {e}", file=sys.stderr)
        if args.trace:
            print(exc.trace.render(), file=sys.stderr)
        exc.trace.save()
        return 3, manifest
    except ModelCallError as exc:
        print(f"MODEL CALL FAILED: {exc}", file=sys.stderr)
        return 1, manifest

    plan, trace = result.plan, result.trace
    if not result.live:
        print(f"[MOCK MODEL: {result.model} - this is NOT a live Nemotron call]", file=sys.stderr)
    if args.json:
        print(json.dumps(plan, indent=2))
    else:
        print(pretty(plan))

    code, new_manifest = 0, manifest
    if plan["mode"] == "clarify":
        trace.add("execute", "skipped: model asked for clarification")
    elif args.send or args.simulate:
        code, out = execute(plan, args, manifest, trace)
        if out:
            new_manifest = out
            corrections = 0
            while code == 0 and args.send and args.verify and out.get("snapshot"):
                verdict = run_verify(text, plan, out, trace)
                if verdict is None or verdict.matches or not verdict.fix_request or corrections >= args.max_corrections:
                    break
                corrections += 1
                print(f"\nSelf-correction {corrections}: {verdict.fix_request}")
                try:
                    fix = make_plan(verdict.fix_request, client, manifest=out, mode="edit", trace=trace,
                                    request_id=None)
                except (PlanError, ModelCallError) as exc:
                    trace.add("correct", f"correction plan failed: {exc}")
                    break
                plan = fix.plan
                trace.add("correct", f"executing correction {plan['request_id']}: {plan['summary']}")
                print(pretty(plan))
                if plan["mode"] == "clarify":
                    break
                code, fixed = execute(plan, args, out, trace)
                if not fixed:
                    break
                out = new_manifest = fixed
    else:
        trace.add("execute", "dry run: not sent to Blender")

    trace.save()
    if args.trace:
        print("\n" + trace.render())
    return code, new_manifest


def main(argv: list[str] | None = None) -> int:
    paths.load_env()
    ap = argparse.ArgumentParser(prog="python -m director.cli", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--text", help="request text; omit for an interactive session")
    ap.add_argument("--mode", choices=["auto", "create", "edit"], default="auto")
    ap.add_argument("--manifest", type=Path, help="scene manifest/result JSON (default: latest real scene)")
    run = ap.add_mutually_exclusive_group()
    run.add_argument("--dry-run", action="store_true", help="plan + validate only (default)")
    run.add_argument("--send", action="store_true", help="execute in Blender via the shared inbox/outbox")
    run.add_argument("--simulate", action="store_true", help="execute in the pure-Python simulator")
    ap.add_argument("--mock", action="store_true", help="offline rule-based model (NOT Nemotron)")
    ap.add_argument("--trace", action="store_true", help="print the agent trace")
    ap.add_argument("--json", action="store_true", help="print the raw plan JSON")
    ap.add_argument("--timeout", type=float, default=15.0, help="seconds to wait for Blender (default 15)")
    ap.add_argument("--reset", action="store_true", help="forget the saved scene manifest first")
    ap.add_argument("--no-verify", dest="verify", action="store_false",
                    help="skip the Nemotron visual self-check after Blender executes (on by default with --send)")
    ap.add_argument("--max-corrections", type=int, default=1, help="visual self-corrections per request (default 1)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    global _VERIFIER
    if args.mock:
        from .mock_model import RuleMockModel
        from .verifier import MockVerifierModel
        client = RuleMockModel()
        _VERIFIER = MockVerifierModel()
    else:
        try:
            client = NemotronClient()
            # same Nemotron VL model by default; NEMOTRON_VL_MODEL can point vision at a different one
            _VERIFIER = NemotronClient(model=os.environ.get("NEMOTRON_VL_MODEL") or None, temperature=0.1)
        except ConfigError as exc:
            print(f"CONFIG ERROR: {exc}", file=sys.stderr)
            return 2

    state_file = SIM_MANIFEST if args.simulate else paths.LATEST_MANIFEST
    if args.reset:
        state_file.unlink(missing_ok=True)
    try:
        if args.manifest:
            manifest = bridge.load_manifest(args.manifest)
            if manifest is None:
                print(f"CONFIG ERROR: manifest not found: {args.manifest}", file=sys.stderr)
                return 2
        elif args.send:
            # Prefer whichever is newer: our last saved result, or the snapshot Blender writes at
            # startup and after every job (so a freshly restarted Blender is never out of sync).
            candidates = [p for p in (paths.LATEST_MANIFEST, paths.STATE / "blender_manifest.json") if p.exists()]
            newest = max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None
            manifest = bridge.load_manifest(newest) if newest else None
        else:
            manifest = bridge.load_manifest(state_file)
    except json.JSONDecodeError as exc:
        print(f"CONFIG ERROR: manifest is not valid JSON: {exc}", file=sys.stderr)
        return 2
    if manifest and not args.json:
        ids = ", ".join(o["id"] for o in manifest.get("scene_objects", [])) or "(empty)"
        print(f"scene v{manifest.get('scene_version')}: {ids}", file=sys.stderr)

    if args.text:
        code, _ = run_one(args.text, args, client, manifest)
        return code

    # interactive multi-turn session: each turn's real result becomes the next turn's manifest
    print("TinyDirector interactive. Type a request, or 'quit'. "
          + ("Executing in Blender." if args.send else "Simulating." if args.simulate else "Dry run (no execution)."))
    code = 0
    while True:
        try:
            line = input("\ndirector> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if line.lower() in ("quit", "exit", "q"):
            break
        if not line:
            continue
        code, manifest = run_one(line, args, client, manifest)
    return code


if __name__ == "__main__":
    sys.exit(main())
