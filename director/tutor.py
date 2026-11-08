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
from .circuit.agent import ToolError, _tokens, choose_tool, describe_state, explain, facts_for
from .circuit.display import circuit_request, equation_text, title_for
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
    trace.add("read_world", f"{_executor_name(before)}: {describe_state(current)}")
    emit("scene", "ok", "Scene read", f"{_executor_name(before)}: {describe_state(current)}",
         executor=_executor_name(before), simulated=before.get("executor") == "simulated")

    follow_up_text = bool(FOLLOW_UP_RE.search(text))
    kb, kn = _retrieve(text, context, follow_up_text, emit, trace)
    emit("plan", "start", "Choosing what to do", f"{'Nemotron' if client.live else 'MOCK model'} picks one tool")
    try:
        call = choose_tool(text, client, current, history, trace, knowledge=kn)
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
    if call.tool == "teach_topic":
        return teach_topic(call.args["question"], client, executor, trace, show_trace, emit=emit, context=context,
                           kb=kb, kn=kn)
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
    if call.tool in ("teach_led_resistor", "set_resistance"):
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
    action = "build" if current is None and call.tool != "explain" else "update" if current is not None else None

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
    follow_up = call.tool == "explain" and follow_up_text
    if context.get("last_explanation") and call.tool == "explain":
        facts["previous_explanation"] = context["last_explanation"]  # "simpler, please" / "why did it ...?"
        if follow_up:
            facts["teaching_goal"] = ("the learner is reacting to previous_explanation: re-explain THAT idea the way "
                                      "they ask (e.g. simpler words, an everyday analogy), using only these facts; "
                                      "do not say that nothing changed")
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
    title = (context.get("lesson") or {}).get("title") if follow_up else None
    title = title or title_for(call.tool, text, target)
    snapshot = None

    code = 0
    if action is not None:
        req = circuit_request(rid, action, target, caption=expl.text, title=title)
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
    else:
        trace.add("execute", "no scene change (no lesson circuit yet; answering from the default circuit)")
        emit("apply", "info", "No scene change", "answered from the default circuit")

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
         "", reply=expl.text, source=expl.source, title=title if action is not None else None, snapshot=snapshot,
         scene_changed=action is not None, lesson=context.get("lesson"), code=code,
         grounding=_grounding(kn, expl, "calculated", kb))
    return code


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
         lesson=context.get("lesson"), code=0, grounding=_grounding(kn, expl, "calculated", kb))
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
                        "calculations": facts["calculations"], "concepts": kn.ids if kn is not None else []}
    context["lesson"] = {"kind": "topic", "title": lesson.resolved["title"], "key_points": lesson.resolved["key_points"],
                         "builder": lesson.builder, "cached": lesson.cached}
    emit("turn", "ok", "Done", "", reply=expl.text, source=expl.source, title=lesson.resolved["title"],
         snapshot=snapshot, scene_changed=True, lesson=context["lesson"],
         preview=str(lesson.preview) if lesson.preview.exists() else None, code=0,
         grounding=_grounding(kn, expl, "calculated", kb))
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
