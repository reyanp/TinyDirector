"""Interactive circuit tutor: "Why does an LED need a resistor?"

  python -m director.tutor --text "Teach me why LEDs need resistors."     # one turn, real Blender
  python -m director.tutor                                                # interactive, real Blender
  python -m director.tutor --simulate --mock --text "..."                 # offline: SIMULATED + MOCK
Blender side: `blender --python blender_engine/worker.py` (same as the scene director).

Each turn:  inspect Blender (the circuit it actually shows)  ->  Nemotron picks ONE tool (validated,
max 1 retry)  ->  director/circuit/model.py calculates  ->  Nemotron explains the calculated facts
(numbers checked)  ->  circuit_request_v1 to Blender  ->  read-back checked against the calculation.

Exit codes: 0 ok / answered / politely declined, 1 model call failed, 2 config, 3 invalid tool call
after retry, 4 bridge timeout, 5 execution error or post-check failed.
"""

from __future__ import annotations

import argparse
import json
import sys
from functools import lru_cache

from jsonschema import Draft202012Validator

from . import bridge, paths
from .circuit import check
from .circuit.agent import ToolError, choose_tool, describe_state, explain, facts_for
from .circuit.display import circuit_request, equation_text
from .circuit.model import LedCircuit, check_resistance, format_ma, format_ohms, solve
from .nemotron_client import ConfigError, ModelCallError, NemotronClient
from .planner import new_request_id
from .trace import Trace

SIM_STATE = paths.STATE / "circuit_sim_scene.json"


@lru_cache(maxsize=1)
def _request_validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads((paths.SCHEMA_DIR / "circuit_request_v1.json").read_text(encoding="utf-8")))


def validate_request(req: dict) -> list[str]:
    return [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
            for e in _request_validator().iter_errors(req)]


class BridgeExecutor:
    """Real Blender through the shared inbox/outbox."""

    def __init__(self, timeout: float = 60.0):
        self.timeout = timeout

    def inspect(self) -> dict:
        return bridge.inspect(timeout=self.timeout)

    def send(self, req: dict) -> dict:
        return bridge.send(req, timeout=self.timeout)


def _executor_name(out: dict) -> str:
    return "SIMULATED engine (not Blender)" if out.get("executor") == "simulated" else out.get("executor", "Blender")


def run_turn(text: str, client, executor, history: list[str], show_trace: bool = False) -> int:
    rid = new_request_id()
    trace = Trace(rid, text)
    trace.add("intent", f"user said: {text!r}")
    try:
        before = executor.inspect()
    except bridge.BridgeTimeout as exc:
        print(f"BRIDGE TIMEOUT (scene inspection): {exc}", file=sys.stderr)
        return 4
    circ = before.get("circuit") or {}
    current = LedCircuit.from_dict(circ["params"]) if circ.get("present") and circ.get("params") else None
    trace.add("read_world", f"{_executor_name(before)}: {describe_state(current)}")

    try:
        call = choose_tool(text, client, current, history, trace)
    except ToolError as exc:
        print("TOOL CALL REJECTED - nothing was executed.", file=sys.stderr)
        for e in exc.errors:
            print(f"  - {e}", file=sys.stderr)
        trace.save()
        return 3
    except ModelCallError as exc:
        print(f"MODEL CALL FAILED: {exc}", file=sys.stderr)
        return 1
    tag = "Nemotron" if call.live else f"MOCK {call.model}"
    print(f"[{tag}] tool: {call.tool} {json.dumps(call.args)}" + (f"  - {call.reason}" if call.reason else ""))
    history.append(f"{text} -> {call.tool} {json.dumps(call.args)}")

    if call.tool == "clarify":
        print(f"\nTutor: {call.args['message']}")
        trace.add("execute", "skipped: request is outside the lesson")
        trace.save()
        return 0

    base = current or LedCircuit()
    target = base
    if call.tool in ("teach_led_resistor", "set_resistance"):
        ohms = call.args.get("resistance_ohm", base.resistance_ohm)
        problem = check_resistance(ohms)
        if problem:
            trace.add("validate", f"value rejected by the circuit model: {problem}")
            print(f"\nTutor: I can't show that one: {problem}. The scene was not changed.")
            trace.save()
            return 0
        target = base.with_resistance(ohms)
    action = "build" if current is None and call.tool != "explain" else "update" if current is not None else None

    before_sol = solve(current) if current is not None else None
    after_sol = solve(target)
    trace.add("calculate", equation_text(after_sol) + (f" (was {format_ma(before_sol.current_ma)} with "
                                                       f"{format_ohms(current.resistance_ohm)})" if before_sol else ""))
    print(f"[calc] {equation_text(after_sol)}"
          + (f"   (before: {format_ma(before_sol.current_ma)} with {format_ohms(current.resistance_ohm)})"
             if before_sol and current.resistance_ohm != target.resistance_ohm else ""))

    facts = facts_for(call, text, before_sol, after_sol, built=action == "build")
    try:
        expl = explain(facts, client, trace)
    except ModelCallError as exc:
        from .circuit.agent import Explanation, template_explanation
        trace.add("explain", f"model call failed ({exc}); using the deterministic template")
        expl = Explanation(template_explanation(facts), "template")

    code = 0
    if action is not None:
        req = circuit_request(rid, action, target, caption=expl.text)
        errs = validate_request(req)
        if errs:  # our own bug, never send it
            print("INTERNAL: circuit request failed circuit_request_v1:", *errs, sep="\n  ", file=sys.stderr)
            return 3
        trace.add("validate", f"circuit_request_v1 {action} accepted")
        try:
            out = executor.send(req)
        except bridge.BridgeTimeout as exc:
            print(f"BRIDGE TIMEOUT: {exc}", file=sys.stderr)
            trace.save()
            return 4
        except (bridge.BridgeResultError, FileExistsError) as exc:
            print(f"BRIDGE ERROR: {exc}", file=sys.stderr)
            trace.save()
            return 5
        name = _executor_name(out)
        checks = check.run(req, before, out)
        failed = [c for c in checks if not c.ok]
        trace.add("execute", f"{name} {action}: status={out['status']} scene_version={out.get('scene_version')}",
                  errors=out.get("errors", []))
        trace.add("check", f"{check.summary(checks)} against {name}'s read-back", failed=[c.line() for c in failed])
        print(f"[{name}] {action}: status={out['status']}, scene v{out.get('scene_version')}; "
              f"read-back {check.summary(checks)}" + (f"; snapshot {paths.OUTBOX / out['snapshot']}"
                                                       if out.get("snapshot") else ""))
        for c in checks:
            if not c.ok or show_trace:
                print(c.line())
        for e in out.get("errors", []):
            print(f"  ! {e}", file=sys.stderr)
        if out["status"] != "ok" or failed:
            print("The scene does not match the calculation (see above).", file=sys.stderr)
            code = 5
    else:
        trace.add("execute", "no scene change (no lesson circuit yet; answering from the default circuit)")

    label = {"nemotron": "", "mock": " [MOCK explanation]",
             "template": " [template explanation: model text failed the number check]"}[expl.source]
    print(f"\nTutor{label}: {expl.text}")
    trace.save()
    if show_trace:
        print("\n" + trace.render())
    return code


def main(argv: list[str] | None = None) -> int:
    paths.load_env()
    ap = argparse.ArgumentParser(prog="python -m director.tutor", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--text", help="one message; omit for an interactive session")
    ap.add_argument("--simulate", action="store_true", help="SIMULATED circuit engine instead of Blender")
    ap.add_argument("--reset-sim", action="store_true", help="forget the simulated scene first")
    ap.add_argument("--mock", action="store_true", help="offline rule-based model (NOT Nemotron)")
    ap.add_argument("--timeout", type=float, default=60.0, help="seconds to wait for Blender (default 60)")
    ap.add_argument("--trace", action="store_true", help="print every check and the agent trace")
    args = ap.parse_args(argv)

    if args.mock:
        from .circuit.mock import TutorMockModel
        client = TutorMockModel()
        print("[MOCK MODEL - this is NOT a live Nemotron call]", file=sys.stderr)
    else:
        try:
            client = NemotronClient(temperature=0.2)
        except ConfigError as exc:
            print(f"CONFIG ERROR: {exc}", file=sys.stderr)
            return 2
    if args.simulate:
        from .circuit.sim import SimulatedCircuitExecutor
        if args.reset_sim:
            SIM_STATE.unlink(missing_ok=True)
        executor = SimulatedCircuitExecutor(SIM_STATE)
        print("[SIMULATED circuit engine - NOT Blender]", file=sys.stderr)
    else:
        executor = BridgeExecutor(args.timeout)

    history: list[str] = []
    if args.text:
        return run_turn(args.text, client, executor, history, args.trace)
    print("Circuit tutor: 'Why does an LED need a resistor?'  Try: \"Teach me why LEDs need resistors.\", "
          "\"Change the resistor to 1,000 ohms.\", \"Put it back to 330 ohms.\"  Type 'quit' to exit.")
    code = 0
    while True:
        try:
            line = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if line.lower() in ("quit", "exit", "q"):
            break
        if line:
            code = run_turn(line, client, executor, history, args.trace)
    return code


if __name__ == "__main__":
    sys.exit(main())
