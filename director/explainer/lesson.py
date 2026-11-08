"""OVERRIDE turns (director/tutor.py routes them here): generate a lesson for any question, answer follow-ups.

generate():  question -> Nemotron scene design (agent.py, validated, 1 retry) -> explainer_request_v1 build ->
             Blender read-back checked (check.py) -> Poyo narrates the design while the camera visits each element.
explain_question(): a question while the generated lesson is on stage -> answered from the spec Blender stored
             (read back, never remembered from the request) through the normal explanation checks.

Everything here is model-written content: it is labeled "Generated (OVERRIDE) · not verified" on screen, in the
chips, and in the grounding ("unverified"); only the scene's structure is machine-checked.
"""

from __future__ import annotations

import re
import sys

from .. import stage
from ..circuit.agent import Explanation
from ..circuit.display import wrap_caption
from ..events import noop
from ..planner import new_request_id
from ..trace import Trace
from . import check
from . import spec as S
from .agent import DesignError, design

CHIPS = ["Generated (OVERRIDE)", "not verified"]
EXPLAIN_PROMPT = """EXPLAINER. You are Poyo, a friendly tutor for beginners. The 3D scene shows a lesson that was
GENERATED for the learner's own question (FACTS.lesson_on_screen: its elements, arrows and key points). Follow
FACTS.teaching_goal in 2 or 3 short, warm, plain sentences (at most 70 words).

Hard rules:
- Stay consistent with the lesson on screen and with KNOWLEDGE; mainstream, well-established knowledge only. If
  you are not sure, say so briefly instead of guessing.
- Use a number only if it appears in FACTS. Never invent statistics, prices, dates or percentages.
- For scams, fraud and hacking: how to recognize and protect, never how to commit them.
- Plain text only: no markdown, no lists, no emojis, no web addresses or phone numbers.
"""


def on_stage(before: dict) -> dict | None:
    ex = before.get("explainer") or {}
    return ex if ex.get("present") and ex.get("spec") else None


def describe(ex: dict) -> str:
    """<circuit_state> for the tool selector (from Blender's read-back)."""
    s = ex["spec"]
    return ("No LED circuit on screen. A generated OVERRIDE lesson is on screen (model-written, not verified): "
            f"{s['title']!r}, showing " + ", ".join(e["label"] for e in s["elements"]) + ".")


def _lesson(context: dict, spec: dict, live: bool) -> dict:
    context["lesson"] = {"kind": "explainer", "title": spec["title"],
                         "chips": CHIPS if live else ["MOCK lesson (OVERRIDE)", "not verified"],
                         "generated": True}
    return context["lesson"]


def _label_words(label: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9']+", label.lower()) if len(w) > 2 and w not in
            ("the", "and", "for", "you", "your", "with", "from", "that", "this")]


def _element_for(sentence: str, spec: dict) -> str | None:
    """The element a sentence is about: its full label, else the most label words (earliest mention wins ties)."""
    low = sentence.lower()
    best, score = None, (0, 0)
    for e in spec["elements"]:
        if e["label"].lower() in low:
            s = (100, -low.index(e["label"].lower()))
        else:
            hits = [low.find(w) for w in _label_words(e["label"]) if re.search(rf"\b{re.escape(w)}", low)]
            s = (len(hits), -min(hits)) if hits else (0, 0)
        if s > score:
            best, score = e["id"], s
    return best


def narration_cues(spec: dict) -> list[dict]:
    return [{"text": st["say"].strip(), "focus": st["focus"], "caption": wrap_caption(st["say"].strip()),
             "callout": None} for st in spec["narration"]]


def answer_cues(text: str, spec: dict) -> list[dict]:
    return [{"text": s, "focus": _element_for(s, spec), "caption": wrap_caption(s), "callout": None}
            for s in stage.segments(text)]


def _grounding(kn, text: str, kb) -> dict:
    from .. import tutor as T
    return {**T._grounding(kn, Explanation(text, "nemotron"), "unverified", kb), "number_check": False,
            "generated": True}


def _send_build(req: dict, executor, trace: Trace, emit, show_trace: bool):
    from .. import tutor as T
    errs = S.validate_request(req)
    if errs:  # our own bug (the spec was validated already): never send it
        print("INTERNAL: explainer request failed explainer_request_v1:", *errs, sep="\n  ", file=sys.stderr)
        return None, [], 3
    trace.add("validate", f"explainer_request_v1 {req['action']} accepted")
    emit("apply", "start", "Building the generated lesson in Blender", f"explainer_request_v1 {req['action']}")
    out, code = T._send(executor, req, emit)
    if out is None:
        trace.save()
        return None, [], code
    name = T._executor_name(out)
    checks = check.run(req, out)
    hard = check.hard_failures(checks)
    warn = [c for c in checks if not c.ok and c not in hard]
    trace.add("execute", f"{name} explainer {req['action']}: status={out['status']} "
                         f"scene_version={out.get('scene_version')}", errors=out.get("errors", []))
    trace.add("check", f"{check.summary(checks)} against {name}'s read-back",
              failed=[c.line() for c in hard], warnings=[c.line() for c in warn])
    print(f"[{name}] explainer {req['action']}: status={out['status']}; read-back {check.summary(checks)}")
    for c in checks:
        if not c.ok or show_trace:
            print(c.line() + (" [layout warning]" if c in warn else ""))
    emit("apply", "ok" if out["status"] == "ok" else "error", f"{name}: explainer {req['action']} {out['status']}",
         f"scene v{out.get('scene_version')}", executor=name, simulated=out.get("executor") == "simulated")
    emit("verify", "ok" if not hard else "error", f"Read-back: {check.summary(checks)}",
         "; ".join(c.line() for c in (hard or warn)[:3]) + (" (layout warnings only)" if warn and not hard else ""),
         passed=len(checks) - len(hard) - len(warn), failed=len(hard), warnings=len(warn))
    return out, hard, 0 if out["status"] == "ok" and not hard else 5


def generate(question: str, text: str, client, executor, before: dict, kb, kn, trace: Trace, emit=noop,
             context: dict | None = None, show_trace: bool = False) -> int:
    from .. import tutor as T
    context = context if context is not None else {}
    emit("director", "start", "Designing a new lesson",
         f"OVERRIDE: {'Nemotron' if client.live else 'MOCK model'} designs a 3D explainer for: {question}")
    try:
        d = design(question, client, trace, knowledge=kn.prompt_block() if kn else "",
                   claim_check=T._claim_check(kb))
    except DesignError as exc:
        print("SCENE DESIGN REJECTED - nothing was built.", *exc.errors[:8], sep="\n  - ", file=sys.stderr)
        trace.save()
        return T._fail(emit, "director", "Lesson design rejected after one retry: " + "; ".join(exc.errors[:2]), 3)
    except T.ModelCallError as exc:
        print(f"MODEL CALL FAILED: {exc}", file=sys.stderr)
        trace.save()
        return T._fail(emit, "director", f"Model call failed: {exc}", 1)
    tag = "Nemotron" if d.live else "MOCK model"
    if d.refusal:
        emit("director", "ok", f"{tag} declined to build this one", d.refusal)
        reply = d.refusal
        print(f"\nTutor: {reply}")
        trace.add("execute", "skipped: the scene director declined the topic")
        trace.save()
        context["last_explanation"] = reply
        emit("turn", "ok", "Answered", "", reply=reply, source="nemotron" if d.live else "mock",
             scene_changed=False)
        return 0
    spec = d.spec
    emit("director", "ok", f"{tag} designed “{spec['title']}”",
         f"{len(spec['elements'])} elements, {spec['layout']} layout, {len(spec['narration'])} narration steps"
         + (" (after 1 repair)" if d.attempts == 2 else ""), elements=[e["label"] for e in spec["elements"]],
         layout=spec["layout"], attempts=d.attempts, live=d.live)
    rid = new_request_id()
    out, hard, code = _send_build(S.build_request(rid, spec, mock=not d.live), executor, trace, emit, show_trace)
    if out is None:
        return code
    reply = S.narration_text(spec)
    T._caption(executor, spec["summary"], trace)
    label = "" if d.live else " [MOCK lesson]"
    print(f"\nTutor{label}: {reply}")
    trace.save()
    if show_trace:
        print("\n" + trace.render())
    context.pop("topic", None)
    context.pop("compare", None)
    context["last_explanation"] = reply
    context["last_kind"] = "explainer"
    T._remember_concepts(context, kn)
    lesson = _lesson(context, spec, d.live)
    emit("turn", "ok" if code == 0 else "error", "Done" if code == 0 else "Scene does not match the design", "",
         reply=reply, source="nemotron" if d.live else "mock", title=spec["title"], snapshot=out.get("snapshot"),
         scene_changed=True, lesson=lesson, code=code, generated=True, grounding=_grounding(kn, reply, kb),
         stage={"kind": "explainer", "cues": narration_cues(spec)})
    return code


def _lesson_facts(spec: dict) -> dict:
    names = {e["id"]: e["label"] for e in spec["elements"]}
    return {"title": spec["title"], "big_idea": spec["summary"],
            "elements": [{"label": e["label"], **({"what_it_is": e["note"]} if e.get("note") else {})}
                         for e in spec["elements"]],
            "arrows": [f"{names[ln['from']]} -> {names[ln['to']]}" + (f" ({ln['label']})" if ln.get("label") else "")
                       for ln in spec["links"]],
            "key_points": spec["key_points"],
            **({"simplified": spec["simplified"]} if spec.get("simplified") else {}),
            "status": "generated by Nemotron for the learner's question; not checked by a calculator or source"}


def _fresh_template(spec: dict) -> str:
    """Fallback text from the lesson's own key points / element notes, skipping what Poyo already said."""
    from ..circuit.agent import CONVERSATION, _norm_sentences
    said = {s for t in CONVERSATION.get() for s in _norm_sentences(t.get("poyo", "")) if s}
    pool = list(spec["key_points"]) + [f"{e['label']}: {e['note'].rstrip('.')}." for e in spec["elements"]
                                       if e.get("note")]
    fresh = [p for p in pool if not set(_norm_sentences(p)) & said]
    return " ".join((fresh or [spec["summary"]])[:3])


def explain_question(text: str, client, executor, before: dict, kb, kn, trace: Trace, emit=noop,
                     context: dict | None = None, follow_up: bool = False, show_trace: bool = False) -> int:
    from .. import tutor as T
    context = context if context is not None else {}
    ex = on_stage(before)
    spec = ex["spec"]  # read back from Blender
    emit("apply", "info", "No scene change", "answering about the generated lesson on stage")
    facts = {"question": text, "lesson_on_screen": _lesson_facts(spec),
             "teaching_goal": "answer the question directly, connecting it to the lesson on screen"}
    if context.get("last_explanation"):
        facts["previous_explanation"] = context["last_explanation"]
        if follow_up:
            facts["teaching_goal"] = ("the learner is reacting to previous_explanation: re-explain THAT idea the way "
                                      "they ask (e.g. simpler words, an everyday analogy)")
    template = _fresh_template(spec)
    expl = T._explained(facts, client, kb, kn, trace, emit, "Nemotron answers about the generated lesson",
                        prompt=EXPLAIN_PROMPT, template=template,
                        ready="Explanation ready (consistent with the generated lesson; not verified)")
    trace.save()
    if show_trace:
        print("\n" + trace.render())
    context["last_explanation"] = expl.text
    context["last_kind"] = "explainer"
    T._remember_concepts(context, kn)
    lesson = _lesson(context, spec, client.live)
    emit("turn", "ok", "Done", "", reply=expl.text, source=expl.source, title=spec["title"], snapshot=None,
         scene_changed=False, lesson=lesson, code=0, generated=True,
         grounding=_grounding(kn, expl.text, kb), stage={"kind": "explainer", "cues": answer_cues(expl.text, spec)})
    return 0


def run(call, text: str, client, executor, before: dict, kb, kn, trace: Trace, emit=noop,
        context: dict | None = None, follow_up: bool = False, show_trace: bool = False) -> int | None:
    """A turn while a generated lesson is on stage. None = not for this lesson (the tutor handles it)."""
    if on_stage(before) is None:
        return None
    if call.tool in ("explain", "focus_camera") or (follow_up and call.tool in ("explain_concept", "clarify")):
        return explain_question(text, client, executor, before, kb, kn, trace, emit=emit, context=context,
                                follow_up=follow_up, show_trace=show_trace)
    return None
