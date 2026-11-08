"""Interactive circuit tutor: "Why does an LED need a resistor?"

  python -m director.tutor --text "Teach me why LEDs need resistors."     # one turn, real Blender
  python -m director.tutor                                                # interactive, real Blender
  python -m director.tutor --simulate --mock --text "..."                 # offline: SIMULATED + MOCK
Blender side: `blender --python blender_engine/worker.py` (same as the scene director).

A question about a different electronics idea -> teach_topic -> multi-agent lesson (director/lesson/,
docs/MULTI_AGENT.md): Nemotron director spec -> builder code -> sandboxed Blender build -> verifier -> load.

Each turn:  inspect Blender (the circuit it actually shows)  ->  knowledge base lookup (concepts, trusted
facts, misconceptions, which demo exists; director/knowledge/)  ->  Nemotron picks ONE tool (validated,
max 1 retry)  ->  director/circuit/model.py calculates  ->  Nemotron explains the calculated facts
(numbers checked)  ->  circuit_request_v1 to Blender  ->  read-back checked against the calculation.

Exit codes: 0 ok / answered / politely declined, 1 model call failed, 2 config, 3 invalid tool call
after retry, 4 bridge timeout, 5 execution error or post-check failed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from functools import lru_cache

from jsonschema import Draft202012Validator

from . import bridge, paths
from .circuit import check
from .events import noop
from .circuit.agent import SCAM_TOOLS, ToolError, _tokens, choose_tool, describe_state, explain, facts_for
from .circuit.display import (circuit_request, compare_clear_request, compare_request, equation_text, title_for,
                              wrap_caption)
from .circuit.model import (CircuitModelError, LedCircuit, check_resistance, format_ma, format_ohms, format_volts,
                            solve, step_resistance)
from . import stage
from .scam import lesson as scam_lesson
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


@lru_cache(maxsize=1)
def _compare_validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads((paths.SCHEMA_DIR / "compare_request_v1.json").read_text(encoding="utf-8")))


class BridgeExecutor:
    """Real Blender through the shared inbox/outbox."""

    def __init__(self, timeout: float = 60.0):
        self.timeout = timeout

    def inspect(self) -> dict:
        return bridge.inspect(timeout=self.timeout)

    def send(self, req: dict, timeout: float | None = None) -> dict:
        return bridge.send(req, timeout=timeout or self.timeout)


def _executor_name(out: dict) -> str:
    return "SIMULATED engine (not Blender)" if out.get("executor") == "simulated" else out.get("executor", "Blender")


# "explain that in simpler terms", "again?", "I don't get it": a reaction to the last answer, not a new question
FOLLOW_UP_RE = re.compile(r"\b(simpler|simply|again|rephrase|repeat|eli5|another way|don'?t (get|understand)|"
                          r"didn'?t (get|understand)|what do you mean|explain (that|it|this)|in other words)\b", re.I)


def _retrieve(text: str, context: dict, follow_up: bool, emit, trace: Trace):
    """(knowledge base, Knowledge) or (None, None) when the knowledge base is unavailable (the turn goes on)."""
    from .knowledge import get_kb
    emit("knowledge", "start", "Looking up the knowledge base", "concepts, trusted facts, misconceptions (SQLite)")
    try:
        kb = get_kb()
        kn = kb.retrieve(text, prior=context.get("concepts", []), follow_up=follow_up)
    except Exception as exc:  # noqa: BLE001 - knowledge is an aid; the calculated lesson still works without it
        trace.add("knowledge", f"knowledge base unavailable: {type(exc).__name__}: {exc}")
        emit("knowledge", "error", "Knowledge base unavailable", f"{type(exc).__name__}: {exc}"[:300])
        return None, None
    names = ", ".join(c["name"] for c in kn.concepts)
    trace.add("knowledge", f"{names or 'no concept matched'}; {len(kn.facts)} facts, {len(kn.misconceptions)} "
                           f"misconceptions" + (" (from the previous turn)" if kn.from_context else ""))
    emit("knowledge", "ok" if kn.concepts else "info",
         f"{len(kn.concepts)} concept(s), {len(kn.facts)} fact(s)" if kn.concepts else "No matching concept",
         names + (" (carried over from the last turn)" if kn.from_context else ""), **kn.summary())
    return kb, kn


def _claim_check(kb):
    if kb is None:
        return None
    return lambda text: [f"{w['sentence']!r} (correct: {w['correction']})" for w in kb.wrong_claims(text)]


def _grounding(kn, expl, numbers_from: str, kb=None, given: list[str] | None = None) -> dict:
    """What backs a reply, stated honestly for the UI:
    numbers: "calculated" (every number matched a calculator result), "cited" (numbers come from cited
    knowledge-base facts, nothing was calculated) or "none"; facts: what was RETRIEVED and given to the model
    (retrieval is not verification: only facts with status "verified" were re-computed by a calculator)."""
    summary = kn.summary() if kn is not None else {}
    quantities = _tokens(re.sub(r"\b[23]-?D\b", "", expl.text))  # "3D scene" is not a numeric claim
    return {"numbers": numbers_from if quantities else "none",
            "number_check": True, "misconception_check": kb is not None and expl.source != "template",
            "concepts": summary.get("concepts", []), "facts": summary.get("facts", []),
            "given_as_facts": given or [], "misconceptions": [m["id"] for m in summary.get("misconceptions", [])]}


def _fail(emit, stage: str, message: str, code: int) -> int:
    emit(stage, "error", message.split(":")[0], message)
    emit("turn", "error", "Turn failed", message, code=code)
    return code


def run_turn(text: str, client, executor, history: list[str], show_trace: bool = False, emit=noop,
             context: dict | None = None) -> int:
    """One tutor turn. `emit` receives structured events (director/events.py); `context` (optional, kept by the
    caller across turns) carries the last explanation so follow-ups like "simpler, please" have something to
    refer to. CLI behavior is unchanged."""
    context = context if context is not None else {}
    rid = new_request_id()
    trace = Trace(rid, text)
    trace.add("intent", f"user said: {text!r}")
    emit("understand", "ok", "Got your question", text)
    emit("scene", "start", "Reading the scene", "asking Blender what it shows right now")
    try:
        before = executor.inspect()
    except bridge.BridgeTimeout as exc:
        print(f"BRIDGE TIMEOUT (scene inspection): {exc}", file=sys.stderr)
        return _fail(emit, "scene", f"Blender did not answer: {str(exc).splitlines()[0]}", 4)
    circ = before.get("circuit") or {}
    current = LedCircuit.from_dict(circ["params"]) if circ.get("present") and circ.get("params") else None
    focus = (before.get("direction") or {}).get("focus")
    lesson_on_stage = (before.get("lesson") or {}).get("title") or (
        (context.get("topic") or {}).get("title") if before.get("lesson") else None)
    comparing = before.get("compare") if (before.get("compare") or {}).get("active") and current is not None else None
    scam_sc = scam_lesson.on_stage(before)  # the Spot the Scam phone (director/scam/lesson.py)
    scene_text = scam_lesson.describe(scam_sc) if scam_sc else describe_state(current, lesson_on_stage, focus, comparing)
    trace.add("read_world", f"{_executor_name(before)}: {scene_text}")
    emit("scene", "ok", "Scene read", f"{_executor_name(before)}: {scene_text}",
         executor=_executor_name(before), simulated=before.get("executor") == "simulated")

    follow_up_text = bool(FOLLOW_UP_RE.search(text))
    kb, kn = _retrieve(text, context, follow_up_text, emit, trace)
    emit("plan", "start", "Choosing what to do", f"{'Nemotron' if client.live else 'MOCK model'} picks one tool")
    try:
        call = choose_tool(text, client, current, history, trace, knowledge=kn, scene=scene_text)
    except ToolError as exc:
        print("TOOL CALL REJECTED - nothing was executed.", file=sys.stderr)
        for e in exc.errors:
            print(f"  - {e}", file=sys.stderr)
        trace.save()
        return _fail(emit, "plan", "Tool call rejected after one retry: " + "; ".join(exc.errors[:2]), 3)
    except ModelCallError as exc:
        print(f"MODEL CALL FAILED: {exc}", file=sys.stderr)
        return _fail(emit, "plan", f"Model call failed: {exc}", 1)
    tag = "Nemotron" if call.live else f"MOCK {call.model}"
    print(f"[{tag}] tool: {call.tool} {json.dumps(call.args)}" + (f"  - {call.reason}" if call.reason else ""))
    emit("plan", "ok", f"Tool: {call.tool}", call.reason, tool=call.tool, args=call.args, model=tag,
         live=call.live, attempts=call.attempts)
    history.append(f"{text} -> {call.tool} {json.dumps(call.args)}")

    if call.tool == "clarify":
        print(f"\nTutor: {call.args['message']}")
        trace.add("execute", "skipped: request is outside the lesson")
        trace.save()
        context["last_explanation"] = call.args["message"]
        emit("turn", "ok", "Answered", "", reply=call.args["message"], source="nemotron" if call.live else "mock",
             scene_changed=False)
        return 0
    if call.tool in SCAM_TOOLS or scam_sc:
        code = scam_lesson.run(call, text, client, executor, before, kb, kn, trace, emit, context, follow_up_text,
                               show_trace)
        if code is not None:
            return code
    if call.tool == "teach_topic":
        return teach_topic(call.args["question"], client, executor, trace, show_trace, emit=emit, context=context,
                           kb=kb, kn=kn)
    if call.tool == "focus_camera":
        return focus_camera(call.args["target"], executor, current, before, trace, emit, context)
    if call.tool == "tour_circuit":
        return tour_circuit(call.args["stops"], text, client, executor, current, before, kb, kn, trace, emit, context)
    if call.tool == "compare_resistance":
        return compare_resistance(call.args, text, client, executor, current, before, kb, kn, trace, emit, context)
    if call.tool == "end_comparison":
        return end_comparison(executor, current, before, trace, emit, context)
    if call.tool == "explain_concept" or (call.tool == "explain" and follow_up_text
                                          and context.get("last_kind") == "concept" and context.get("concept")):
        concept = call.args.get("concept") or context["concept"]
        return explain_concept(concept, text, client, kb, kn, trace, emit, context, follow_up_text)
    # a generated lesson is on stage: Blender shows it (the lesson load cleared the circuit), or, with the
    # SIMULATED engine (which cannot display lessons), the conversation moved to it after the last circuit turn
    topic_on_stage = bool(context.get("topic")) and (
        (current is None and bool(before.get("lesson")))
        or (before.get("executor") == "simulated" and context.get("last_kind") != "circuit"))
    if call.tool == "explain" and topic_on_stage:
        return explain_topic(text, client, kb, kn, trace, emit, context, follow_up_text)

    base = current or LedCircuit()
    target = base
    if call.tool == "adjust_resistance":
        try:  # "make it bigger": the calculation module picks the value, never the model
            ohms = step_resistance(base.resistance_ohm, call.args["direction"])
        except CircuitModelError as exc:
            reply = f"I can't go further that way: {exc}. The scene was not changed."
            emit("calculate", "error", "No standard value that way", str(exc))
            emit("turn", "ok", "Answered", "", reply=reply, source="template", scene_changed=False)
            return 0
        trace.add("calculate", f"{call.args['direction']} from {format_ohms(base.resistance_ohm)} -> {format_ohms(ohms)}")
        call.args = {**call.args, "resistance_ohm": ohms}
    if call.tool in ("teach_led_resistor", "set_resistance", "adjust_resistance"):
        ohms = call.args.get("resistance_ohm", base.resistance_ohm)
        problem = check_resistance(ohms)
        if problem:
            trace.add("validate", f"value rejected by the circuit model: {problem}")
            reply = f"I can't show that one: {problem}. The scene was not changed."
            print(f"\nTutor: {reply}")
            trace.save()
            emit("calculate", "error", "Value rejected by the circuit model", problem)
            emit("turn", "ok", "Answered", "", reply=reply, source="template", scene_changed=False)
            return 0
        target = base.with_resistance(ohms)
    # a question never rebuilds or re-sends the scene: it is explained as it is (Blender keeps showing it)
    action = None if call.tool == "explain" else "build" if current is None else "update"

    emit("calculate", "start", "Calculating", "director/circuit/model.py: I = (V_source - V_LED) / R")
    before_sol = solve(current) if current is not None else None
    after_sol = solve(target)
    emit("calculate", "ok", equation_text(after_sol),
         (f"was {format_ma(before_sol.current_ma)} with {format_ohms(current.resistance_ohm)}"
          if before_sol and current.resistance_ohm != target.resistance_ohm else ""),
         current_ma=round(after_sol.current_ma, 3), resistance_ohm=target.resistance_ohm)
    trace.add("calculate", equation_text(after_sol) + (f" (was {format_ma(before_sol.current_ma)} with "
                                                       f"{format_ohms(current.resistance_ohm)})" if before_sol else ""))
    print(f"[calc] {equation_text(after_sol)}"
          + (f"   (before: {format_ma(before_sol.current_ma)} with {format_ohms(current.resistance_ohm)})"
             if before_sol and current.resistance_ohm != target.resistance_ohm else ""))

    facts = facts_for(call, text, before_sol, after_sol, built=action == "build")
    if comparing and action is None:  # a question while both circuits are on screen
        alt = LedCircuit.from_dict(comparing["alternative"]["params"])
        facts["comparison_on_screen"] = _compare_facts(solve(current), solve(alt))
    follow_up = call.tool == "explain" and follow_up_text
    if context.get("last_explanation") and call.tool == "explain":
        facts["previous_explanation"] = context["last_explanation"]  # "simpler, please" / "why did it ...?"
        if follow_up:
            facts["teaching_goal"] = ("the learner is reacting to previous_explanation: re-explain THAT idea the way "
                                      "they ask (e.g. simpler words, an everyday analogy), using only these facts; "
                                      "do not say that nothing changed")
    # the scene goes first (the learner sees the change while the explanation is written)
    title = (context.get("lesson") or {}).get("title") if follow_up else None
    title = title or title_for("set_resistance" if call.tool == "adjust_resistance" else call.tool, text, target)
    snapshot = None

    code = 0
    if action is not None:
        req = circuit_request(rid, action, target, title=title,
                              caption=_scene_caption(action, current, target, before_sol, after_sol))
        errs = validate_request(req)
        if errs:  # our own bug, never send it
            print("INTERNAL: circuit request failed circuit_request_v1:", *errs, sep="\n  ", file=sys.stderr)
            return 3
        trace.add("validate", f"circuit_request_v1 {action} accepted")
        emit("apply", "start", "Building the circuit in Blender" if action == "build" else "Updating the scene",
             f"circuit_request_v1 {action}")
        try:
            out = executor.send(req)
        except bridge.BridgeTimeout as exc:
            print(f"BRIDGE TIMEOUT: {exc}", file=sys.stderr)
            trace.save()
            return _fail(emit, "apply", f"Blender did not answer: {str(exc).splitlines()[0]}", 4)
        except (bridge.BridgeResultError, FileExistsError) as exc:
            print(f"BRIDGE ERROR: {exc}", file=sys.stderr)
            trace.save()
            return _fail(emit, "apply", f"Bridge error: {exc}", 5)
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
        snapshot = out.get("snapshot")
        emit("apply", "ok" if out["status"] == "ok" else "error", f"{name}: {action} {out['status']}",
             f"scene v{out.get('scene_version')}", executor=name, simulated=out.get("executor") == "simulated")
        emit("verify", "ok" if not failed else "error", f"Read-back: {check.summary(checks)}",
             "; ".join(c.line() for c in failed[:3]), passed=len(checks) - len(failed), failed=len(failed))
        if out["status"] != "ok" or failed:
            print("The scene does not match the calculation (see above).", file=sys.stderr)
            code = 5
    elif current is not None:
        trace.add("execute", "no scene change: the question is answered about the circuit on screen")
        emit("apply", "info", "No scene change", "explaining the circuit already on screen")
    else:
        trace.add("execute", "no scene change (no lesson circuit yet; answering from the default circuit)")
        emit("apply", "info", "No scene change", "answered from the default circuit")

    emit("explain", "start", "Writing the explanation", "numbers are checked against the calculation")
    try:
        expl = explain(facts, client, trace, knowledge=kn.prompt_block() if kn else "", claim_check=_claim_check(kb))
    except ModelCallError as exc:
        from .circuit.agent import Explanation, template_explanation
        trace.add("explain", f"model call failed ({exc}); using the deterministic template")
        expl = Explanation(template_explanation(facts), "template")
    emit("explain", "ok", {"nemotron": "Explanation ready (numbers verified)", "mock": "MOCK explanation ready",
                           "template": "Template explanation (model text failed the number check)"}[expl.source],
         "", source=expl.source, attempts=expl.attempts)
    if action is not None and code == 0:  # the scene changed first; now its subtitle gets the explanation
        try:
            executor.send(stage.direct_request(caption=wrap_caption(expl.text)), timeout=20)
        except Exception as exc:  # noqa: BLE001 - the lesson is already on screen and verified
            trace.add("execute", f"caption update failed: {type(exc).__name__}: {exc}")

    label = {"nemotron": "", "mock": " [MOCK explanation]",
             "template": " [template explanation: model text failed the number check]"}[expl.source]
    print(f"\nTutor{label}: {expl.text}")
    trace.save()
    if show_trace:
        print("\n" + trace.render())
    context["last_explanation"] = expl.text
    context["last_kind"] = "circuit"
    _remember_concepts(context, kn)
    if action is not None:
        context.pop("topic", None)
        context["lesson"] = {"kind": "circuit", "title": title, "resistance_ohm": target.resistance_ohm,
                             "current_ma": round(after_sol.current_ma, 3),
                             "chips": [f"R = {format_ohms(target.resistance_ohm)}", f"I = {format_ma(after_sol.current_ma)}",
                                       equation_text(after_sol)]}  # formatted by the calculation module
    emit("turn", "ok" if code == 0 else "error", "Done" if code == 0 else "Scene does not match the calculation",
         "", reply=expl.text, source=expl.source, title=title if action is not None else (context.get("lesson") or {}).get("title"),
         snapshot=snapshot,
         scene_changed=action is not None, lesson=context.get("lesson"), code=code,
         grounding=_grounding(kn, expl, "calculated", kb),
         stage=(_compare_stage(expl.text, solve(current), solve(LedCircuit.from_dict(comparing["alternative"]["params"])))
                if comparing and action is None else
                _stage_for(expl.text, "circuit" if (action is not None or current is not None) else None,
                           opening="overview" if action == "build" else None)))
    if action is not None:
        context.pop("compare", None)  # a circuit change ends a comparison (the worker removes the copy first)
    return code


def _scene_caption(action, current, target, before_sol, after_sol) -> str:
    """Subtitle shown while the explanation is being written: calculated values only."""
    if action == "update" and current is not None and current.resistance_ohm != target.resistance_ohm:
        return (f"Resistor {format_ohms(current.resistance_ohm)} → {format_ohms(target.resistance_ohm)}: current "
                f"{format_ma(before_sol.current_ma)} → {format_ma(after_sol.current_ma)}.")
    return f"With a {format_ohms(target.resistance_ohm)} resistor the current is {format_ma(after_sol.current_ma)}."


def _stage_for(text: str, kind: str | None, parts: dict | None = None, opening: str | None = None) -> dict:
    """Cues for the spoken explanation: which part each sentence is about (director/stage.py)."""
    return {"kind": kind, "cues": stage.cues(text, kind, parts, opening)}


FOCUS_NAMES = {"battery": "battery", "resistor": "resistor", "led": "LED", "current": "current", "overview": "circuit"}


def focus_camera(target: str, executor, current, before: dict, trace: Trace, emit=noop,
                 context: dict | None = None) -> int:
    """'Show me the resistor': move Blender's camera there (display only) and say what it is, with the
    calculated values. A generated lesson: focus the first part of that type."""
    context = context if context is not None else {}
    parts = (context.get("topic") or {}).get("parts") or {}
    if current is not None:
        focus, kind = target, "circuit"
    elif before.get("lesson") and target == "overview":
        focus, kind = "overview", "topic"
    elif before.get("lesson"):
        ptype = {"led": "led", "resistor": "resistor", "battery": "battery"}.get(target)
        focus, kind = next((pid for pid, p in parts.items() if p.get("type") == ptype), None), "topic"
    else:
        focus, kind = None, None
    if focus is None:
        reply = ("There's nothing on the stage yet. Ask me to teach you something first!" if kind is None else
                 f"This lesson doesn't have a {FOCUS_NAMES[target]} to show.")
        emit("turn", "ok", "Answered", "", reply=reply, source="direction", scene_changed=False)
        return 0
    emit("apply", "start", f"Pointing the camera at the {FOCUS_NAMES[target]}", "display only: no values change")
    try:
        out = executor.send(stage.direct_request(focus))
    except bridge.BridgeTimeout as exc:
        return _fail(emit, "apply", f"Blender did not answer: {str(exc).splitlines()[0]}", 4)
    except (bridge.BridgeResultError, FileExistsError) as exc:
        return _fail(emit, "apply", f"Bridge error: {exc}", 5)
    got = (out.get("direction") or {}).get("focus")
    ok = out.get("status") == "ok" and got == focus
    emit("apply", "ok" if ok else "error", f"Camera on the {FOCUS_NAMES[target]}" if ok else "Camera move failed",
         "; ".join(out.get("errors", [])))
    emit("verify", "ok" if ok else "error", f"Read-back: focus = {got}", "")
    if current is not None and ok:
        sol = solve(current)
        reply = {"resistor": f"Here's the resistor: {format_ohms(current.resistance_ohm)}, with "
                             f"{format_volts(sol.v_resistor, 1)} across it.",
                 "led": f"Here's the LED. It holds about {format_volts(current.led_vf, 1)} and glows with "
                        f"{format_ma(sol.current_ma)}.",
                 "battery": f"Here's the {format_volts(current.source_v)} battery that pushes the current around.",
                 "current": f"These glowing arrows are the current, {format_ma(sol.current_ma)}, flowing from + to −.",
                 "overview": "Here's the whole circuit again."}[target]
    else:
        reply = f"Here's the {FOCUS_NAMES[target]}." if ok else "I couldn't move the camera there."
    trace.add("execute", f"focus {focus}: {out.get('status')}")
    trace.save()
    context["last_kind"] = context.get("last_kind") or "circuit"
    emit("turn", "ok" if ok else "error", "Done", "", reply=reply, source="direction", scene_changed=False,
         lesson=context.get("lesson"), code=0 if ok else 5,
         stage={"kind": kind, "cues": [{"text": reply, "focus": None,
                                        "caption": stage.wrap_caption(reply) if kind == "circuit" else None}]})
    return 0 if ok else 5


def _send(executor, req: dict, emit, timeout: float | None = None) -> tuple[dict | None, int]:
    """(result, 0) or (None, exit code) with the failure already reported."""
    try:
        return executor.send(req, timeout=timeout), 0
    except bridge.BridgeTimeout as exc:
        return None, _fail(emit, "apply", f"Blender did not answer: {str(exc).splitlines()[0]}", 4)
    except (bridge.BridgeResultError, FileExistsError) as exc:
        return None, _fail(emit, "apply", f"Bridge error: {exc}", 5)


def _explained(facts: dict, client, kb, kn, trace: Trace, emit, detail: str, shape_check=None, prompt=None,
               claim_check=None, template: str | None = None):
    emit("explain", "start", "Writing the explanation", detail)
    kb_check = _claim_check(kb)
    checks = [c for c in (kb_check, claim_check) if c]
    try:
        expl = explain(facts, client, trace, knowledge=kn.prompt_block() if kn else "",
                       claim_check=(lambda t: [w for c in checks for w in c(t)]) if checks else None,
                       shape_check=shape_check, prompt=prompt, template=template)
    except ModelCallError as exc:
        from .circuit.agent import Explanation, template_explanation
        trace.add("explain", f"model call failed ({exc}); using the deterministic template")
        expl = Explanation(template or template_explanation(facts), "template")
    emit("explain", "ok", {"nemotron": "Explanation ready (numbers verified)", "mock": "MOCK explanation ready",
                           "template": "Template explanation (model text failed the checks)"}[expl.source],
         "", source=expl.source, attempts=expl.attempts)
    label = {"nemotron": "", "mock": " [MOCK explanation]",
             "template": " [template explanation: model text failed the checks]"}[expl.source]
    print(f"\nTutor{label}: {expl.text}")
    return expl


def _caption(executor, text: str, trace: Trace) -> None:
    """Subtitle on the stage (display only; a failure never breaks the turn)."""
    try:
        executor.send(stage.direct_request(caption=wrap_caption(text)), timeout=20)
    except Exception as exc:  # noqa: BLE001
        trace.add("execute", f"caption update failed: {type(exc).__name__}: {exc}")


ARROWS_NOTE = ("the glowing arrows show conventional current (from + to −); their speed is illustrative, scaled with "
               "the calculated current, not the speed of electrons")
GLOW_NOTE = "the LED glow is illustrative, scaled with the calculated current (not a measured brightness)"


def tour_callouts(c: LedCircuit) -> dict:
    """Each tour stop's big on-screen measurement, formatted by the calculation module."""
    sol = solve(c)
    return {"battery": {"value": format_volts(c.source_v), "note": "the battery pushes the current around"},
            "resistor": {"value": format_volts(sol.v_resistor, 1), "note": f"across the {format_ohms(c.resistance_ohm)} resistor"},
            "current": {"value": format_ma(sol.current_ma), "note": "the same current all the way around the loop"},
            "led": {"value": f"≈ {format_volts(c.led_vf)}", "note": "LED forward voltage (simplified model)"},
            "overview": {"value": f"I = {format_ma(sol.current_ma)}",
                         "note": f"({format_volts(c.source_v)} − {format_volts(c.led_vf)}) ÷ {format_ohms(c.resistance_ohm)}"}}


TOUR_MEANING = {"battery": "the battery: it supplies the voltage that pushes the current around the loop",
                "resistor": "the resistor: it takes the rest of the battery voltage and so sets the current",
                "current": "the current arrows: the same current flows through every part of the loop",
                "led": "the LED: it keeps about its forward voltage and glows with the current",
                "overview": "the whole circuit: the formula that ties it together"}


def tour_sentence(stop: str, c: LedCircuit) -> str:
    """The deterministic narration of one tour stop (MOCK text and the template fallback)."""
    sol = solve(c)
    return {"battery": f"The battery supplies {format_volts(c.source_v)} to push the current around the loop.",
            "resistor": f"The {format_ohms(c.resistance_ohm)} resistor takes {format_volts(sol.v_resistor, 1)} of it, "
                        "and that sets the current.",
            "current": f"The same {format_ma(sol.current_ma)} flows all the way around the loop.",
            "led": f"The LED keeps about {format_volts(c.led_vf)} and glows with that current.",
            "overview": f"All together: I = ({format_volts(c.source_v)} − {format_volts(c.led_vf)}) ÷ "
                        f"{format_ohms(c.resistance_ohm)} = {format_ma(sol.current_ma)}."}[stop]


def tour_circuit(stops: list[str], text: str, client, executor, current, before: dict, kb, kn, trace: Trace,
                 emit=noop, context: dict | None = None) -> int:
    """'Show me what's happening inside this circuit': Nemotron chose the stops; the calculation module gives each
    stop's measurement; Nemotron narrates one sentence per stop; the camera visits them in sync with the speech
    (cues -> direct_request_v1 focus + halo + callout, sent by the browser or the e2e runner)."""
    context = context if context is not None else {}
    if current is None:
        reply = "There's no circuit on the stage yet. Ask me why an LED needs a resistor and I'll build one first!"
        emit("turn", "ok", "Answered", "", reply=reply, source="template", scene_changed=False)
        return 0
    stops = list(dict.fromkeys(stops))
    if stops[-1] != "overview":
        stops = [s for s in stops if s != "overview"] + ["overview"]  # a tour always ends on the whole circuit
    on_stage = set((before.get("direction") or {}).get("targets") or [])
    missing = [s for s in stops if on_stage and s not in on_stage]
    emit("scene", "ok" if not missing else "error", "Tour: " + " → ".join(stops),
         "every stop is a part Blender reports on stage" if not missing else f"not on stage: {', '.join(missing)}",
         stops=stops)
    if missing:
        return _fail(emit, "scene", f"Blender does not report {', '.join(missing)} on stage", 5)
    sol, callouts = solve(current), tour_callouts(current)
    emit("calculate", "ok", equation_text(sol), " · ".join(f"{s}: {callouts[s]['value']}" for s in stops))
    trace.add("calculate", "tour measurements: " + ", ".join(f"{s} {callouts[s]['value']}" for s in stops))
    facts = {"user_request": text,
             "what_happened": "a camera tour of the circuit on screen; no value changed",
             "tour": [{"stop": s, "shows": TOUR_MEANING[s], "measurement": callouts[s]["value"],
                       "measurement_means": callouts[s]["note"]} for s in stops],
             "formula": f"I = ({format_volts(current.source_v)} − {format_volts(current.led_vf)}) ÷ R",
             "arrows": ARROWS_NOTE,
             "model_note": "simplified model: the LED is treated as a fixed 2 V drop; wires and battery are ideal",
             "key_points": [tour_sentence(s, current) for s in stops],
             "teaching_goal": (f"narrate this camera tour for a beginner: exactly {len(stops)} short sentences, one per "
                               f"stop, in this order: {', '.join(stops)}; each sentence names its part and uses its "
                               "measurement; at most 85 words in total; this overrides the usual 2-3 sentences")}
    def one_per_stop(t: str) -> str | None:  # the camera follows the narration sentence by sentence
        n = len(stage.sentences(t))
        return None if n == len(stops) else (f"it has {n} sentences but the tour has {len(stops)} stops "
                                             f"({', '.join(stops)}): write exactly one sentence per stop, in order")
    expl = _explained(facts, client, kb, kn, trace, emit, "one sentence per tour stop, numbers checked",
                      shape_check=one_per_stop)
    cues = stage.tour_cues(expl.text, stops, callouts)
    trace.add("execute", "tour cues: " + " → ".join(c["focus"] for c in cues))
    trace.save()
    context.update(last_explanation=expl.text, last_kind="circuit")
    _remember_concepts(context, kn)
    emit("turn", "ok", "Done", "", reply=expl.text, source=expl.source, title=(context.get("lesson") or {}).get("title"),
         scene_changed=False, lesson=context.get("lesson"), code=0, tour=stops,
         grounding=_grounding(kn, expl, "calculated", kb), stage={"kind": "circuit", "cues": cues})
    return 0


def _compare_facts(sa, sb) -> dict:
    ratio = max(sa.current_ma, sb.current_ma) / min(sa.current_ma, sb.current_ma) if min(sa.current_ma, sb.current_ma) > 0 else None
    out = {"left_circuit_now": {"resistance": format_ohms(sa.circuit.resistance_ohm), "current": format_ma(sa.current_ma),
                                "voltage_across_resistor": format_volts(sa.v_resistor, 1)},
           "right_what_if_copy": {"resistance": format_ohms(sb.circuit.resistance_ohm), "current": format_ma(sb.current_ma),
                                  "voltage_across_resistor": format_volts(sb.v_resistor, 1)},
           "what_if_current": "lower" if sb.current_ma < sa.current_ma else "higher",
           "visuals": f"{ARROWS_NOTE}; {GLOW_NOTE}"}
    if ratio:
        out["current_ratio"] = f"about {ratio:.1f} times"
    return out


def _compare_stage(text: str, sa, sb) -> dict:
    callouts = {"baseline": {"value": format_ma(sa.current_ma), "note": f"now · {format_ohms(sa.circuit.resistance_ohm)}"},
                "alternative": {"value": format_ma(sb.current_ma),
                                "note": f"what if · {format_ohms(sb.circuit.resistance_ohm)}"}}
    sides = {"baseline": (sa.circuit.resistance_ohm, sa.current_ma), "alternative": (sb.circuit.resistance_ohm, sb.current_ma)}
    return {"kind": "circuit", "cues": stage.compare_cues(text, sides, callouts)}


def compare_resistance(args: dict, text: str, client, executor, current, before: dict, kb, kn, trace: Trace,
                       emit=noop, context: dict | None = None) -> int:
    """Split reality: the circuit on stage stays as it is; Blender adds a what-if copy with another resistor beside
    it. Both scenarios are calculated by model.py; the read-back of both is checked before anything is said."""
    context = context if context is not None else {}
    base = current or LedCircuit()
    if "resistance_ohm" in args:
        ohms = args["resistance_ohm"]
    else:
        try:
            ohms = step_resistance(base.resistance_ohm, args["direction"])
        except CircuitModelError as exc:
            reply = f"I can't go further that way: {exc}. The scene was not changed."
            emit("calculate", "error", "No standard value that way", str(exc))
            emit("turn", "ok", "Answered", "", reply=reply, source="template", scene_changed=False)
            return 0
    problem = check_resistance(ohms)
    if problem or float(ohms) == base.resistance_ohm:
        reply = (f"I can't show that one: {problem}. The scene was not changed." if problem else
                 f"The circuit on screen already has {format_ohms(base.resistance_ohm)}. Pick another value to compare.")
        emit("calculate", "error" if problem else "info", "Nothing to compare", problem or "same value")
        emit("turn", "ok", "Answered", "", reply=reply, source="template", scene_changed=False)
        return 0
    alt = base.with_resistance(float(ohms))
    if current is None:  # nothing on stage yet: build the lesson circuit first, then compare against it
        rid = new_request_id()
        req = circuit_request(rid, "build", base, title=title_for("teach_led_resistor", text, base),
                              caption=_scene_caption("build", None, base, None, solve(base)))
        emit("apply", "start", "Building the circuit in Blender", "circuit_request_v1 build (the baseline)")
        out, code = _send(executor, req, emit)
        if out is None:
            return code
        checks = check.run(req, before, out)
        failed = [c for c in checks if not c.ok]
        emit("verify", "ok" if not failed else "error", f"Read-back: {check.summary(checks)}",
             "; ".join(c.line() for c in failed[:3]))
        if out["status"] != "ok" or failed:
            return _fail(emit, "verify", "The baseline circuit does not match the calculation", 5)
        before = executor.inspect()
    sa, sb = solve(base), solve(alt)
    emit("calculate", "start", "Calculating both", "director/circuit/model.py: I = (V_source - V_LED) / R, twice")
    emit("calculate", "ok", f"Now {format_ma(sa.current_ma)} · What if {format_ma(sb.current_ma)}",
         f"{equation_text(sa)}  |  {equation_text(sb)}", baseline_ma=round(sa.current_ma, 3),
         alternative_ma=round(sb.current_ma, 3))
    trace.add("calculate", f"baseline {equation_text(sa)}; what-if {equation_text(sb)}")
    print(f"[calc] now: {equation_text(sa)}   what-if: {equation_text(sb)}")
    rid = new_request_id()
    req = compare_request(rid, base, alt)
    errs = [e.message for e in _compare_validator().iter_errors(req)]
    if errs:  # our own bug, never send it
        print("INTERNAL: compare request failed compare_request_v1:", *errs, sep="\n  ", file=sys.stderr)
        return 3
    _caption(executor, f"What if the resistor were {format_ohms(alt.resistance_ohm)}? Now {format_ma(sa.current_ma)}, "
                       f"what if {format_ma(sb.current_ma)} (calculated).", trace)
    emit("apply", "start", "Building the what-if copy beside the circuit",
         f"compare_request_v1 show: {format_ohms(base.resistance_ohm)} vs {format_ohms(alt.resistance_ohm)}")
    out, code = _send(executor, req, emit)
    if out is None:
        return code
    name = _executor_name(out)
    checks = check.run_compare(req, before, out)
    failed = [c for c in checks if not c.ok]
    trace.add("check", f"{check.summary(checks)} against {name}'s read-back", failed=[c.line() for c in failed])
    print(f"[{name}] compare: status={out['status']}; read-back {check.summary(checks)}")
    for c in failed:
        print(c.line())
    emit("apply", "ok" if out["status"] == "ok" else "error", f"{name}: comparison {out['status']}",
         "; ".join(out.get("errors", []))[:300] or f"{(out.get('compare') or {}).get('objects', 0)} what-if objects",
         executor=name, simulated=out.get("executor") == "simulated")
    emit("verify", "ok" if not failed else "error", f"Read-back: {check.summary(checks)}",
         "; ".join(c.line() for c in failed[:3]), passed=len(checks) - len(failed), failed=len(failed),
         checks=[c.line() for c in checks])
    if out["status"] != "ok" or failed:
        print("The comparison does not match the calculation (see above).", file=sys.stderr)
        return _fail(emit, "verify", "The comparison does not match the calculation: "
                     + "; ".join([c.what for c in failed[:2]] + out.get("errors", [])[:1]), 5)
    cf = _compare_facts(sa, sb)
    facts = {"user_request": text,
             "what_happened": (f"a what-if copy with a {format_ohms(alt.resistance_ohm)} resistor appeared to the right "
                               "of the circuit on screen; the original circuit on the left is unchanged"),
             "battery": format_volts(base.source_v), "led_forward_voltage": format_volts(base.led_vf),
             "formula": f"I = ({format_volts(base.source_v)} − {format_volts(base.led_vf)}) ÷ R", **cf,
             "model_note": "simplified model: the LED is treated as a fixed 2 V drop; wires and battery are ideal",
             "key_points": [f"Left is the circuit now: {cf['left_circuit_now']['resistance']} gives "
                            f"{cf['left_circuit_now']['current']}.",
                            f"On the right, the what-if copy with {cf['right_what_if_copy']['resistance']} gives "
                            f"{cf['right_what_if_copy']['current']}, so its LED glows "
                            f"{'dimmer' if cf['what_if_current'] == 'lower' else 'brighter'} and its arrows move "
                            f"{'slower' if cf['what_if_current'] == 'lower' else 'faster'}.",
                            "More resistance means less current; glow and arrow speed are illustrative."],
             "teaching_goal": ("compare the two circuits on screen for a beginner in 3 short sentences: quote both "
                               "currents, say why they differ (the resistor limits the current), and what the learner "
                               "sees on the what-if side (LED glow, arrow speed); mention once that glow and arrow "
                               "speed are illustrative")}
    expl = _explained(facts, client, kb, kn, trace, emit, "both calculations, numbers checked")
    _caption(executor, expl.text, trace)
    trace.save()
    title = req["title"]
    original = context.get("lesson") if (context.get("lesson") or {}).get("kind") == "circuit" else None
    context["compare"] = {"baseline_ohm": base.resistance_ohm, "alternative_ohm": alt.resistance_ohm,
                          "lesson": (context.get("compare") or {}).get("lesson") or original}
    context["lesson"] = {"kind": "circuit", "title": title, "resistance_ohm": base.resistance_ohm,
                         "current_ma": round(sa.current_ma, 3), "compare": True,
                         "chips": [f"Now: {format_ohms(base.resistance_ohm)} → {format_ma(sa.current_ma)}",
                                   f"What if: {format_ohms(alt.resistance_ohm)} → {format_ma(sb.current_ma)}",
                                   "glow + arrow speed: illustrative"]}
    context.update(last_explanation=expl.text, last_kind="circuit")
    _remember_concepts(context, kn)
    emit("turn", "ok", "Done", "", reply=expl.text, source=expl.source, title=title, scene_changed=True,
         lesson=context["lesson"], code=0, grounding=_grounding(kn, expl, "calculated", kb), snapshot=out.get("snapshot"),
         compare=out.get("compare"),
         stage=_compare_stage(expl.text, sa, sb))
    return 0


def end_comparison(executor, current, before: dict, trace: Trace, emit=noop, context: dict | None = None) -> int:
    """'Go back to the original': remove the what-if copy (Blender restores everything it hid) and check that the
    original circuit is exactly as it was."""
    context = context if context is not None else {}
    if current is None:
        reply = "There's no circuit on the stage yet."
        emit("turn", "ok", "Answered", "", reply=reply, source="template", scene_changed=False)
        return 0
    sol = solve(current)
    if not (before.get("compare") or {}).get("active"):
        reply = (f"This is already the original circuit: {format_ohms(current.resistance_ohm)} and "
                 f"{format_ma(sol.current_ma)}.")
        emit("apply", "info", "No comparison on screen", "nothing to remove")
        emit("turn", "ok", "Answered", "", reply=reply, source="template", scene_changed=False, lesson=context.get("lesson"))
        return 0
    reply = (f"Back to the original circuit: {format_ohms(current.resistance_ohm)} and {format_ma(sol.current_ma)}. "
             "The what-if copy is gone and nothing in the original changed.")
    req = compare_clear_request(new_request_id())
    _caption(executor, f"Back to the original circuit: {format_ohms(current.resistance_ohm)}, {format_ma(sol.current_ma)}.",
             trace)
    emit("apply", "start", "Removing the what-if copy", "compare_request_v1 clear")
    out, code = _send(executor, req, emit)
    if out is None:
        return code
    name = _executor_name(out)
    checks = check.run_compare(req, before, out)
    failed = [c for c in checks if not c.ok]
    trace.add("check", f"{check.summary(checks)} against {name}'s read-back", failed=[c.line() for c in failed])
    emit("apply", "ok" if out["status"] == "ok" else "error", f"{name}: comparison removed" if out["status"] == "ok"
         else f"{name}: clear {out['status']}", "; ".join(out.get("errors", []))[:300],
         executor=name, simulated=out.get("executor") == "simulated")
    emit("verify", "ok" if not failed else "error", f"Read-back: {check.summary(checks)}",
         "; ".join(c.line() for c in failed[:3]), passed=len(checks) - len(failed), failed=len(failed),
         checks=[c.line() for c in checks])
    if out["status"] != "ok" or failed:
        return _fail(emit, "verify", "The original circuit was not restored exactly", 5)
    trace.save()
    print(f"\nTutor: {reply}")
    saved = (context.pop("compare", None) or {}).get("lesson")
    if saved:
        context["lesson"] = saved
    context.update(last_explanation=reply, last_kind="circuit")
    emit("turn", "ok", "Done", "", reply=reply, source="template", title=(context.get("lesson") or {}).get("title"),
         scene_changed=True, lesson=context.get("lesson"), code=0, snapshot=out.get("snapshot"),
         stage={"kind": "circuit", "cues": [{"text": reply, "focus": None, "caption": None, "callout": None}]})
    return 0


def _remember_concepts(context: dict, kn) -> None:
    if kn is not None and kn.ids:
        context["concepts"] = kn.ids


def _explain_only(facts: dict, client, kb, kn, trace: Trace, emit, stage_detail: str):
    emit("explain", "start", "Writing the explanation", stage_detail)
    try:
        expl = explain(facts, client, trace, knowledge=kn.prompt_block() if kn else "", claim_check=_claim_check(kb))
    except ModelCallError as exc:
        from .circuit.agent import Explanation, template_explanation
        trace.add("explain", f"model call failed ({exc}); using the facts as the answer")
        expl = Explanation(template_explanation(facts), "template")
    emit("explain", "ok", {"nemotron": "Explanation ready", "mock": "MOCK explanation ready",
                           "template": "Template answer (model text failed the checks)"}[expl.source],
         "", source=expl.source, attempts=expl.attempts)
    return expl


def explain_concept(concept: str, text: str, client, kb, kn, trace: Trace, emit=noop, context: dict | None = None,
                    follow_up: bool = False) -> int:
    """A concept answered from the knowledge base, with no scene change (e.g. parallel circuits, which has no
    calculator or 3D demo yet). Numbers in the answer can only come from cited facts, and are labeled so."""
    context = context if context is not None else {}
    if kb is None:
        reply = "My knowledge base is unavailable right now, so I can't answer that one reliably."
        emit("turn", "ok", "Answered", "", reply=reply, source="template", scene_changed=False)
        return 0
    k = kn if kn is not None and kn.ids[:1] == [concept] else kb.for_concepts([concept], text)
    c = k.concepts[0]
    given = [f for f in k.facts if f["concept"] == concept][:3]
    facts = {"question": text, "concept": c["name"], "key_points": [f["text"] for f in given],
             "demo": ("there is no 3D demo for this concept yet; the Blender scene does not change"
                      if c["demo"] == "explanation_only" else "the Blender scene does not change for this answer"),
             "teaching_goal": "explain the concept to a beginner in 2 or 3 short sentences using key_points; if "
                              "there is no 3D demo, say in a few words that this one is explained, not shown"}
    if follow_up and context.get("last_explanation"):
        facts["previous_explanation"] = context["last_explanation"]
        facts["teaching_goal"] = ("the learner is reacting to previous_explanation: re-explain THAT idea the way they "
                                  "ask (e.g. simpler words, an everyday analogy), using only these facts")
    trace.add("plan", f"explain_concept {concept}: {len(given)} knowledge-base facts")
    expl = _explain_only(facts, client, kb, k, trace, emit, f"from the knowledge base: {c['name']}")
    emit("apply", "info", "No scene change", facts["demo"])
    label = {"nemotron": "", "mock": " [MOCK explanation]", "template": " [knowledge-base facts]"}[expl.source]
    print(f"\nTutor{label}: {expl.text}")
    trace.save()
    context.update(last_explanation=expl.text, last_kind="concept", concept=concept, concepts=[concept])
    emit("turn", "ok", "Done", "", reply=expl.text, source=expl.source, title=None, scene_changed=False,
         lesson=context.get("lesson"), code=0, grounding=_grounding(k, expl, "cited", kb, [f["id"] for f in given]))
    return 0


def explain_topic(text: str, client, kb, kn, trace: Trace, emit=noop, context: dict | None = None,
                  follow_up: bool = False) -> int:
    """A question about the generated lesson on screen, answered from ITS calculated facts (no scene change)."""
    context = context if context is not None else {}
    t = context["topic"]
    facts = {"question": text, "lesson_title": t["title"], "key_points": t["key_points"],
             "calculations": t["calculations"],
             "teaching_goal": "answer the learner's question about the lesson on screen using only these facts; "
                              "the 3D scene does not change"}
    if follow_up and context.get("last_explanation"):
        facts["previous_explanation"] = context["last_explanation"]
        facts["teaching_goal"] = ("the learner is reacting to previous_explanation: re-explain THAT idea the way they "
                                  "ask (e.g. simpler words, an everyday analogy), using only these facts")
    if kb is not None and t.get("concepts"):  # the lesson on screen first, then what the question names
        asked = kn.ids if kn is not None else []
        ids = [c for c in asked if c in t["concepts"]] + t["concepts"] + asked
        kn = kb.for_concepts(list(dict.fromkeys(ids)), text)
    trace.add("plan", f"question about the lesson on screen: {t['title']!r}")
    expl = _explain_only(facts, client, kb, kn, trace, emit, f"from the lesson's calculations: {t['title']}")
    emit("apply", "info", "No scene change", f"the lesson on screen: {t['title']}")
    label = {"nemotron": "", "mock": " [MOCK explanation]", "template": " [lesson key points]"}[expl.source]
    print(f"\nTutor{label}: {expl.text}")
    trace.save()
    context.update(last_explanation=expl.text, last_kind="topic")
    _remember_concepts(context, kn)
    emit("turn", "ok", "Done", "", reply=expl.text, source=expl.source, title=None, scene_changed=False,
         lesson=context.get("lesson"), code=0, grounding=_grounding(kn, expl, "calculated", kb),
         stage=_stage_for(expl.text, "topic", t.get("parts")))
    return 0


def teach_topic(question: str, client, executor, trace: Trace, show_trace: bool = False, emit=noop,
                context: dict | None = None, kb=None, kn=None) -> int:
    """A new topic: director (Nemotron) spec -> builder agent code -> sandboxed Blender build -> verifier ->
    load the verified lesson into Blender -> explain from the calculated facts."""
    from .lesson import pipeline
    from .lesson.agents import make_clients, send_to_blender
    context = context if context is not None else {}
    try:
        director, builder, vision = make_clients(not client.live, "auto", True)
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return _fail(emit, "director", f"Config error: {exc}", 2)
    trace.add("plan", f"new topic -> lesson agents (director {director.model}, builder "
                      f"{getattr(builder, 'model', 'deterministic')}): {question!r}")
    try:
        lesson = pipeline.build_lesson(question, director, builder, vision, emit=emit, knowledge=kn)
    except ModelCallError as exc:
        print(f"MODEL CALL FAILED: {exc}", file=sys.stderr)
        trace.save()
        return _fail(emit, "director", f"Model call failed: {exc}", 1)
    except pipeline.LessonError as exc:
        print(f"LESSON FAILED (nothing was loaded): {exc}", file=sys.stderr)
        trace.save()
        return _fail(emit, "verify", f"Lesson failed, nothing was loaded: {exc}", 3 if "spec invalid" in str(exc) else 5)
    if lesson.unsupported:
        reply = f"I can't build an honest 3D lesson for that yet: {lesson.unsupported}"
        print(f"\nTutor: {reply}")
        trace.save()
        context["last_explanation"] = reply
        emit("turn", "ok", "Answered", "", reply=reply, source="nemotron" if client.live else "mock",
             scene_changed=False)
        return 0
    trace.add("verify", f"{lesson.builder}: {lesson.report.summary()}" + (" (cached)" if lesson.cached else ""))
    print(f"[lesson] {lesson.resolved['title']!r} built by {lesson.builder}"
          f"{' (cached)' if lesson.cached else ''}; verifier: {lesson.report.summary()}")
    for n in lesson.notes:
        print(f"  note: {n}")
    if isinstance(executor, BridgeExecutor):
        try:
            loaded = send_to_blender(lesson, executor.timeout, emit=emit)
            snapshot = loaded.get("snapshot")
        except bridge.BridgeTimeout as exc:
            print(f"BRIDGE TIMEOUT: {exc}", file=sys.stderr)
            trace.save()
            return _fail(emit, "load", f"Blender did not answer: {str(exc).splitlines()[0]}", 4)
        except pipeline.LessonError as exc:
            print(f"LOAD FAILED: {exc}", file=sys.stderr)
            trace.save()
            return _fail(emit, "load", f"Load failed: {exc}", 5)
    else:
        snapshot = None
        print(f"[SIMULATED engine - the verified lesson was built in headless Blender but is not displayed: "
              f"{lesson.preview}]")
        emit("load", "info", "SIMULATED engine: lesson not displayed", "built and verified in headless Blender")
    facts = {"question": question, "lesson_title": lesson.resolved["title"],
             "key_points": lesson.resolved["key_points"],
             "calculations": {cid: {k: v for k, v in r.items() if not k.endswith("_ma")}
                              for cid, r in (lesson.results or {}).items()},
             "teaching_goal": "answer the learner's question with this lesson's facts; mention that the 3D scene "
                              "shows the circuit and the current arrows; note the model's assumptions if relevant"}
    emit("explain", "start", "Writing the explanation", "numbers are checked against the calculation")
    try:
        expl = explain(facts, client, trace, knowledge=kn.prompt_block() if kn else "", claim_check=_claim_check(kb))
    except ModelCallError as exc:
        from .circuit.agent import Explanation, template_explanation
        trace.add("explain", f"model call failed ({exc}); using the lesson's key points")
        expl = Explanation(template_explanation(facts), "template")
    emit("explain", "ok", "Explanation ready", "", source=expl.source)
    label = {"nemotron": "", "mock": " [MOCK explanation]",
             "template": " [lesson key points: model text failed the number check]"}[expl.source]
    print(f"\nTutor{label}: {expl.text}")
    trace.save()
    if show_trace:
        print("\n" + trace.render())
    context["last_explanation"] = expl.text
    context["last_kind"] = "topic"
    _remember_concepts(context, kn)
    context["topic"] = {"title": lesson.resolved["title"], "key_points": lesson.resolved["key_points"],
                        "calculations": facts["calculations"], "concepts": kn.ids if kn is not None else [],
                        "parts": {p["id"]: {"type": p["type"], "label": p.get("label", "")}
                                  for p in lesson.resolved.get("parts", [])}}
    context["lesson"] = {"kind": "topic", "title": lesson.resolved["title"], "key_points": lesson.resolved["key_points"],
                         "builder": lesson.builder, "cached": lesson.cached}
    emit("turn", "ok", "Done", "", reply=expl.text, source=expl.source, title=lesson.resolved["title"],
         snapshot=snapshot, scene_changed=True, lesson=context["lesson"],
         preview=str(lesson.preview) if lesson.preview.exists() else None, code=0,
         grounding=_grounding(kn, expl, "calculated", kb),
         stage=_stage_for(expl.text, "topic" if isinstance(executor, BridgeExecutor) else None,
                          context["topic"]["parts"], opening="overview"))
    return 0


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
          "\"Change the resistor to 1,000 ohms.\", \"Put it back to 330 ohms.\", or a new topic: \"Why would someone use "
          "a BJT over a MOSFET?\"  Type 'quit' to exit.")
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
