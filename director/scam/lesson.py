"""Spot the Scam: the tutor turns for the one cybersecurity lesson (director/tutor.py routes them here).

Nemotron picks the tool (validated, 1 retry) and writes every explanation from FACTS built here: the
fictional scenario (scenario.py), the HEURISTIC PROTOTYPE detector's output (detector.py) and the knowledge
base's cited guidance. Its text goes through the same checks as the circuit tutor (numbers must come from
FACTS, known misconceptions rejected) plus lesson rules (no "tap the link" advice, no invented certainty);
1 retry, then a deterministic template. Blender draws the phone and the two hypothetical outcomes from
scam_request_v1 requests, and every change is checked against Blender's read-back (check.py).

Turns: teach_scam_texts (build + Poyo asks the question) -> scam_inspect (warning signs) -> scam_choose
(both hypothetical outcomes, the learner's choice marked) -> scam_recover (what to do if details were already
shared); explain / end_comparison / follow-ups while the phone is on stage.
"""

from __future__ import annotations

import json
import re
import sys
from functools import lru_cache

from jsonschema import Draft202012Validator

from .. import paths, stage
from ..circuit.display import wrap_caption
from ..events import noop
from ..trace import Trace
from . import check, scenario
from .detector import ACTION_NO_PASSWORD, ACTIONS_AFTER, ACTIONS_BEFORE, NAME, UNCERTAINTY, detect_scenario

CHOICE_TEXT = {"follow_link": "follow the link in the text",
               "verify_official": "verify it independently in the carrier's official app"}
CHOICE_CHIP = {"follow_link": "Choice: follow the link (A)", "verify_official": "Choice: verify independently (B)"}
SIDE = {"follow_link": "outcome_a", "verify_official": "outcome_b"}
SCAM_CONCEPTS = ("smishing", "social_engineering", "impersonation", "credential_theft", "suspicious_links",
                 "account_verification", "protective_actions")  # knowledge-base ids (seed.json)
WHERE = {"sender": "the sender line", "brand": "the company name", "body": "the alert",
         "urgency": "the deadline and threat", "link": "the link"}

EXPLAIN_PROMPT = """EXPLAINER. You are Poyo, a friendly tutor who teaches beginners to recognize scam text messages
(smishing). The 3D scene shows a phone with ONE FICTIONAL suspicious text written for this lesson (no real company,
number or website). Follow FACTS.teaching_goal in short, warm, plain sentences (at most 80 words).

Hard rules:
- Use only what FACTS and KNOWLEDGE say. Use a number only if it appears in FACTS. Never invent statistics,
  probabilities, percentages, confidence or accuracy.
- The warning signs come from a heuristic prototype (keyword rules), not a trained model or AI detector: never
  claim it is certain or call it AI.
- Never tell the learner to tap the link, reply to the text, or share a password, PIN or code.
- Plain text only: no markdown, no lists, no emojis.
"""

UNSAFE = [  # lesson rules for Poyo's text (sentence level; a negated sentence is a warning, not advice)
    (re.compile(r"(?i)^(so |just |go ahead and |you should |you can |it'?s (safe|fine|ok(ay)?) to )?"
                r"(tap|click|follow|open|use) (on )?(the|that|this) link\b"), "advises using the link"),
    (re.compile(r"(?i)\b(reply|text back|respond)\b[^.]*\b(safe|fine|okay|ok)\b"), "says replying is safe"),
    (re.compile(r"(?i)\b(definitely|certainly|100 ?%|guaranteed|for sure|proven to be)\b[^.]*\bscam\b|"
                r"\bscam\b[^.]*\b(definitely|certainly|for sure)\b"), "claims certainty the heuristic does not have"),
    (re.compile(r"(?i)\b(AI|machine learning|neural|trained model|classifier) (detected|found|flagged|says)\b"),
     "presents the heuristic as an AI / trained model"),
]
NEGATED = re.compile(r"(?i)\b(not|never|don'?t|do not|no|instead|without|avoid)\b|n't\b")


def lesson_rules(text: str) -> list[str]:
    """Problems with Poyo's text under the lesson's safety rules (empty = fine)."""
    seen, out = set(), []
    for piece in re.split(r"(?<=[.!?])\s*", text or ""):
        key = re.sub(r"\W+", " ", piece.lower()).strip()
        if len(key) > 12 and key in seen:
            out.append(f"{piece.strip()!r} (repeats itself)")
        seen.add(key)
    for s in stage.sentences(text):
        for rx, why in UNSAFE:
            if rx.search(s) and not (why in ("advises using the link", "says replying is safe") and NEGATED.search(s)):
                out.append(f"{s!r} ({why})")
    return out


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads((paths.SCHEMA_DIR / "scam_request_v1.json").read_text(encoding="utf-8")))


def validate_request(req: dict) -> list[str]:
    return [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}" for e in _validator().iter_errors(req)]


# ---------------- scene state ----------------

def on_stage(before: dict) -> dict | None:
    sc = before.get("scam") or {}
    return sc if sc.get("present") else None


def describe(sc: dict) -> str:
    """<circuit_state> for the tool selector (from Blender's read-back)."""
    out = sc.get("outcomes") or {}
    text = ("No LED circuit on screen. The Spot the Scam lesson is on screen: a phone shows one FICTIONAL "
            f"suspicious text from an unknown number: {scenario.MESSAGE!r}.")
    text += (f" Warning-sign tags are shown on {len(sc.get('tags') or {})} parts of it." if sc.get("tags") else
             " Its warning signs have not been shown yet.")
    if out.get("active"):
        text += (f" Both hypothetical outcomes are shown beside it (A: follow the link, B: verify independently); "
                 f"the learner chose {'A' if out.get('choice') == 'follow_link' else 'B'}.")
    return text


def _message_facts() -> dict:
    return {"sender": f"{scenario.SENDER} ({scenario.SENDER_NOTE})", "text": scenario.MESSAGE,
            "label": "FICTIONAL example written for this lesson: no real company, number or website"}


def _detection_facts(d) -> dict:
    return {"detector": NAME, "category": d.category, "uncertainty": UNCERTAINTY,
            "warning_sign_count": len(d.indicators),
            "warning_signs": [{"where": WHERE.get(i.target, "the message"), "sign": i.label, "evidence": i.evidence}
                              for i in sorted(d.indicators, key=lambda i: list(WHERE).index(i.target)
                                              if i.target in WHERE else 9)]}


def _lesson(context: dict, choice: str | None = None) -> dict:
    d = detect_scenario()
    chips = ["FICTIONAL message", f"Heuristic: {d.category} · {len(d.indicators)} signs"]
    if choice:
        chips.append(CHOICE_CHIP[choice])
    context["lesson"] = {"kind": "scam", "title": scenario.TITLE, "chips": chips, "choice": choice,
                         "detector": NAME}
    return context["lesson"]


# ---------------- Blender requests ----------------

def _apply(req: dict, executor, before: dict | None, trace: Trace, emit, title: str, show_trace: bool = False):
    """Send one scam request, check the read-back. (result, failed checks, exit code)."""
    from .. import tutor as T
    errs = validate_request(req)
    if errs:  # our own bug, never send it
        print("INTERNAL: scam request failed scam_request_v1:", *errs, sep="\n  ", file=sys.stderr)
        return None, [], 3
    trace.add("validate", f"scam_request_v1 {req['action']} accepted")
    emit("apply", "start", title, f"scam_request_v1 {req['action']}")
    out, code = T._send(executor, req, emit)
    if out is None:
        trace.save()
        return None, [], code
    name = T._executor_name(out)
    checks = check.run(req, before, out)
    failed = [c for c in checks if not c.ok]
    trace.add("execute", f"{name} scam {req['action']}: status={out['status']} scene_version={out.get('scene_version')}",
              errors=out.get("errors", []))
    trace.add("check", f"{check.summary(checks)} against {name}'s read-back", failed=[c.line() for c in failed])
    print(f"[{name}] scam {req['action']}: status={out['status']}; read-back {check.summary(checks)}")
    for c in checks:
        if not c.ok or show_trace:
            print(c.line())
    emit("apply", "ok" if out["status"] == "ok" else "error", f"{name}: scam {req['action']} {out['status']}",
         f"scene v{out.get('scene_version')}", executor=name, simulated=out.get("executor") == "simulated")
    emit("verify", "ok" if not failed else "error", f"Read-back: {check.summary(checks)}",
         "; ".join(c.line() for c in failed[:3]), passed=len(checks) - len(failed), failed=len(failed))
    return out, failed, 0 if out["status"] == "ok" and not failed else 5


def _detect(emit, trace: Trace):
    emit("calculate", "start", "Checking the message", "HEURISTIC PROTOTYPE keyword rules (director/scam/detector.py)")
    d = detect_scenario()
    trace.add("calculate", f"{NAME}: {d.category}; signs: {', '.join(d.ids)}")
    emit("calculate", "ok", f"Heuristic: {d.category} ({len(d.indicators)} warning signs)",
         "keyword rules, not a trained model: no calibrated probability", category=d.category,
         indicators=d.ids, detector=NAME)
    return d


# ---------------- explanation + turn end ----------------

SCAM_WORDS = [  # the message parts a sentence is about (earliest mention wins)
    ("link", r"(?i:\blinks?\b|\burl\b|\baddress\b|\bwebsite\b|\bsite\b|\bweb ?page\b|\bhttp\b|\.example\b|\bdomain\b)"),
    ("urgency", r"(?i:\bdeadline\b|\b24\b|\bhours?\b|\bhrs\b|\brush\w*\b|\bhurry\b|\burgen\w*\b|\bpressure\w*\b|"
                r"\bsuspend\w*\b|\bthreat\w*\b|\bclock\b)"),
    ("brand", r"(?i:carrieralert\b|\bcompany name\b|\bpretend\w*\b|\bimpersonat\w*\b|\bbrand\b|\bclaims to be\b)"),
    ("body", r"(?i:\bsign-?in\b|\balert\b|\bunusual\b|\bscar(y|e)\b|\balarm\w*\b)"),
    ("sender", r"(?i:\bsender\b|\bnumber\b|\bcontacts?\b|\bunknown\b|\b555\b|\bwho sent\b)"),
]
OUTCOME_WORDS = [
    ("outcome_b", r"(?i:\bofficial\b|\bapp\b|\bindependent\w*\b|\b7726\b|\breport\w*\b|\bdelete\b|\bsafe\w*\b|"
                  r"\bon your bill\b|\bno alerts?\b|\bscenario b\b|\bright\b)"),
    ("outcome_a", r"(?i:\blinks?\b|\bfake\b|\blook-?alike\b|\bscammer\w*\b|\bpassword\b|\bpin\b|\btyped?\b|"
                  r"\bsim swap\b|\btakeover\b|\bscenario a\b|\bleft\b|\bform\b)"),
]


def _cues(text: str, outcomes: bool, opening: str | None) -> list[dict]:
    rules = OUTCOME_WORDS if outcomes else SCAM_WORDS
    out = []
    for i, s in enumerate(stage.segments(text)):
        focus = stage._first(s, rules)
        if i == 0 and opening:
            focus = opening
        out.append({"text": s, "focus": focus, "caption": wrap_caption(s), "callout": None})
    return out


def _finish(text: str, expl, client, trace: Trace, emit, context: dict, kn, kb, *, title: str, scene_changed: bool,
            snapshot, code: int, outcomes: bool, opening: str | None, executor, show_trace: bool = False,
            on_stage: bool = True) -> int:
    from .. import tutor as T
    reply = expl.text if not text else f"{expl.text} {text}"
    if scene_changed and code == 0:
        T._caption(executor, reply, trace)
    trace.save()
    if show_trace:
        print("\n" + trace.render())
    context["last_explanation"] = reply
    context["last_kind"] = "scam"
    context.pop("topic", None)
    T._remember_concepts(context, kn)
    emit("turn", "ok" if code == 0 else "error", "Done" if code == 0 else "Scene does not match the request", "",
         reply=reply, source=expl.source, title=title, snapshot=snapshot, scene_changed=scene_changed,
         lesson=context.get("lesson"), code=code, grounding={**T._grounding(kn, expl, "scenario", kb),
                                                               "detector": NAME},
         stage={"kind": "scam", "cues": _cues(reply, outcomes, opening)} if on_stage else
         {"kind": None, "cues": []})
    return code


def _template(*sentences: str) -> list[str]:
    return [s for s in sentences if s]


def _say(facts: dict, client, kb, kn, trace: Trace, emit, detail: str, shape_check=None, prompt=None,
         claim_check=None):
    """Nemotron explains FACTS; the deterministic key points are only the fallback (never shown to the model,
    so its explanation is its own words)."""
    from .. import tutor as T
    template = " ".join(facts.pop("key_points"))
    return T._explained(facts, client, kb, kn, trace, emit, detail, shape_check=shape_check, prompt=prompt,
                        claim_check=claim_check, template=template)


def _ensure_phone(executor, before: dict, trace: Trace, emit, context: dict, show_trace: bool):
    """The phone must be on stage for inspect / choose / recover: build it first if it is not."""
    if on_stage(before):
        return before, 0, None
    out, failed, code = _apply(scenario.build_request(), executor, before, trace, emit,
                               "Putting the phone with the fictional text on stage", show_trace)
    if code:
        return before, code, out
    _lesson(context)
    return out, 0, out


# ---------------- turns ----------------

def teach(text: str, client, executor, before: dict, kb, kn, trace: Trace, emit=noop, context: dict | None = None,
          show_trace: bool = False) -> int:
    from .. import tutor as T
    context = context if context is not None else {}
    out, failed, code = _apply(scenario.build_request(), executor, before, trace, emit,
                               "Putting the phone with the fictional text on stage", show_trace)
    if out is None:
        return code
    _lesson(context)
    context["scam"] = {"choice": None, "marked": False}
    facts = {"user_request": text, "what_happened": "built the Spot the Scam lesson in 3D: a phone shows one text",
             "message_on_screen": _message_facts(),
             "lesson_plan": "first look for warning signs together, then the learner decides what they would do, "
                            "then both hypothetical outcomes are shown side by side",
             "teaching_goal": "reply to the learner's request in 2 short sentences, in your own words: the phone "
                              "now shows a FICTIONAL text that claims to be from their carrier, and real scam texts "
                              "copy real alerts, so it pays to look closely before acting. Do NOT list the warning "
                              "signs yet and do NOT ask a question (Poyo asks one next).",
             "key_points": _template("This phone shows a fictional text that claims to come from your carrier.",
                                     "Scam texts copy real alerts, so let's look closely before deciding anything.")}
    expl = _say(facts, client, kb, kn, trace, emit, "Nemotron introduces the fictional message",
                        shape_check=lambda t: "it asked a question; Poyo asks the lesson's question next"
                        if "?" in t else None, prompt=EXPLAIN_PROMPT, claim_check=lesson_rules)
    return _finish(scenario.QUESTION, expl, client, trace, emit, context, kn, kb, title=scenario.TITLE,
                   scene_changed=True, snapshot=out.get("snapshot"), code=code, outcomes=False, opening="phone",
                   executor=executor, show_trace=show_trace)


def inspect(text: str, client, executor, before: dict, kb, kn, trace: Trace, emit=noop, context: dict | None = None,
            show_trace: bool = False) -> int:
    from .. import tutor as T
    context = context if context is not None else {}
    before, code, built = _ensure_phone(executor, before, trace, emit, context, show_trace)
    if code:
        return code
    d = _detect(emit, trace)
    out, failed, code = _apply(scenario.mark_request(d), executor, before, trace, emit,
                               "Tagging the warning signs on the phone", show_trace)
    if out is None:
        return code
    context.setdefault("scam", {})["marked"] = True
    lesson = _lesson(context, (context.get("scam") or {}).get("choice"))
    signs = [f"{WHERE.get(i.target, 'the message').capitalize()}: {i.label.lower()} ({i.evidence})."
             for i in d.indicators]
    facts = {"user_request": text, "message_on_screen": _message_facts(), **_detection_facts(d),
             "teaching_goal": "explain what makes this message suspicious in this order, one short sentence per part "
                              "of the message: the unknown sender, the company name it claims, the scary alert, the "
                              "24-hour deadline with the threat, and the look-alike link. End with one sentence: these "
                              "signs come from a simple heuristic checklist, not a certainty, so check through the "
                              "official app.",
             "key_points": _template(
                 "It comes from an unknown number that isn't in your contacts.",
                 "It claims to be your carrier, but anyone can type a company name.",
                 "It opens with a scary sign-in alert to make you react.",
                 "It rushes you with a 24-hour deadline and threatens to suspend your service.",
                 "The link isn't your carrier's real site: it's a look-alike address without https.",
                 "These signs come from a simple heuristic checklist, not a certainty, so check through the "
                 "official app.")}

    def covers_parts(t: str) -> str | None:
        low = t.lower()
        missing = [w for w, rx in (("the link", r"\blinks?\b|\baddress\b|\bsite\b"),
                                   ("the deadline", r"deadline|24|hours?|hrs|rush|hurry|urgen"),
                                   ("the sender", r"sender|number|contacts?|unknown"),
                                   ("the heuristic's limits", r"heuristic|checklist|rules|not (a )?certain|"
                                                              r"no guarantee|can'?t be sure|not proof"))
                   if not re.search(rx, low)]
        return f"it must mention {', '.join(missing)}" if missing else None

    expl = _say(facts, client, kb, kn, trace, emit, "Nemotron explains the warning signs the heuristic found",
                        shape_check=covers_parts, prompt=EXPLAIN_PROMPT, claim_check=lesson_rules)
    emit("calculate", "info", "Warning signs", " · ".join(signs)[:600])
    return _finish("", expl, client, trace, emit, context, kn, kb, title=lesson["title"], scene_changed=True,
                   snapshot=out.get("snapshot"), code=code,
                   outcomes=bool(((out.get("scam") or {}).get("outcomes") or {}).get("active")), opening="sender",
                   executor=executor, show_trace=show_trace)


def _outcome_text(choice: str) -> dict:
    return {k: (" ".join(v) if isinstance(v, list) else v.replace("\n", " "))
            for k, v in scenario.OUTCOMES[choice].items()}


def choose(choice: str, text: str, client, executor, before: dict, kb, kn, trace: Trace, emit=noop,
           context: dict | None = None, show_trace: bool = False) -> int:
    from .. import tutor as T
    context = context if context is not None else {}
    before, code, built = _ensure_phone(executor, before, trace, emit, context, show_trace)
    if code:
        return code
    out, failed, code = _apply(scenario.outcomes_request(choice), executor, before, trace, emit, "Showing both hypothetical outcomes side by side", show_trace)
    if out is None:
        return code
    context.setdefault("scam", {})["choice"] = choice
    lesson = _lesson(context, choice)
    other = "verify_official" if choice == "follow_link" else "follow_link"
    facts = {"user_request": text, "learner_choice": CHOICE_TEXT[choice],
             "chosen_outcome": _outcome_text(choice), "other_outcome": _outcome_text(other),
             "simulation_note": "both outcomes are hypothetical educational simulations: no website was opened and "
                                "nothing was sent or collected",
             "safe_steps": ACTIONS_BEFORE + [ACTION_NO_PASSWORD]}
    if choice == "follow_link":
        facts["teaching_goal"] = ("in 3 or 4 short sentences, reacting to how the learner put it: this is a "
                                  "hypothetical simulation (left side); what the look-alike page would do with what "
                                  "they type and what the scammer could then do; then the safer choice on the right "
                                  "(open the carrier's official app yourself, report the text to 7726).")
        facts["key_points"] = _template(
            "This is a hypothetical simulation: the link opens a look-alike page, not your carrier's site.",
            "Anything you type there, like your PIN or password, goes straight to the scammer, who could take over "
            "your account or move your number with a SIM swap.",
            "The safer choice is on the right: open your carrier's official app yourself, and forward the text to "
            "7726 (SPAM).")
    else:
        facts["teaching_goal"] = ("in 3 or 4 short sentences, reacting to how the learner put it: why checking "
                                  "independently in the official app is safe (it never uses the message's link) and "
                                  "what it shows here (hypothetical simulation, right side); how to report the text to "
                                  "7726 and delete it; the left side shows what the link would have done.")
        facts["key_points"] = _template(
            "Good choice: in this hypothetical simulation you open your carrier's official app yourself, without "
            "touching the link.",
            "The app shows no alerts, so the warning was fake.",
            "Then report the text by forwarding it to 7726 (SPAM) and delete it; the left side shows what the link "
            "would have done.")
    expl = _say(facts, client, kb, kn, trace, emit, "Nemotron explains the outcome of the learner's choice",
                        prompt=EXPLAIN_PROMPT, claim_check=lesson_rules)
    return _finish("", expl, client, trace, emit, context, kn, kb, title=lesson["title"], scene_changed=True,
                   snapshot=out.get("snapshot"), code=code, outcomes=True, opening=SIDE[choice], executor=executor,
                   show_trace=show_trace)


def recover(text: str, client, executor, before: dict, kb, kn, trace: Trace, emit=noop, context: dict | None = None,
            show_trace: bool = False) -> int:
    from .. import tutor as T
    context = context if context is not None else {}
    sc = on_stage(before) or {}
    outcomes = bool((sc.get("outcomes") or {}).get("active"))
    emit("apply", "info", "No scene change", "recovery steps for the phone on stage")
    facts = {"user_request": text, "situation": "the learner asks what to do if they already typed details into the "
                                                "fake page (in real life)",
             "steps_now": ACTIONS_AFTER,
             "teaching_goal": "calmly give the recovery steps in order, in short sentences: change the password and "
                              "PIN in the official app; call the carrier from its official number and ask it to lock "
                              "number transfers (SIM swap protection); turn on 2-step verification and sign out other "
                              "devices; watch statements and report it at ReportFraud.ftc.gov. Say acting fast helps.",
             "key_points": ACTIONS_AFTER[:3] + ["Acting fast limits the damage."]}
    expl = _say(facts, client, kb, kn, trace, emit, "Nemotron explains the recovery steps",
                        prompt=EXPLAIN_PROMPT, claim_check=lesson_rules)
    if sc:
        _lesson(context, (context.get("scam") or {}).get("choice"))
    code = _finish("", expl, client, trace, emit, context, kn, kb, title=scenario.TITLE, scene_changed=False,
                   snapshot=None, code=0, outcomes=outcomes, opening="outcome_a" if outcomes else "link",
                   executor=executor, show_trace=show_trace, on_stage=bool(sc))
    return code


def explain_question(text: str, client, executor, before: dict, kb, kn, trace: Trace, emit=noop,
                     context: dict | None = None, follow_up: bool = False, show_trace: bool = False) -> int:
    from .. import tutor as T
    context = context if context is not None else {}
    sc = on_stage(before) or {}
    outcomes = sc.get("outcomes") or {}
    d = detect_scenario()
    emit("apply", "info", "No scene change", "answering about the phone on stage")
    facts = {"question": text, "message_on_screen": _message_facts(), **_detection_facts(d),
             "safe_steps": ACTIONS_BEFORE + [ACTION_NO_PASSWORD],
             "outcomes_on_screen": ({"choice": CHOICE_TEXT.get(outcomes.get("choice"), ""),
                                     "A": _outcome_text("follow_link"), "B": _outcome_text("verify_official")}
                                    if outcomes.get("active") else "not shown yet"),
             "teaching_goal": "answer the question directly about the message on screen, in 2 or 3 short sentences",
             "key_points": _template(f"This fictional text shows {len(d.indicators)} warning signs, so treat it as "
                                     "a likely scam.", ACTIONS_BEFORE[1])}
    if context.get("last_explanation"):
        facts["previous_explanation"] = context["last_explanation"]
        if follow_up:
            facts["teaching_goal"] = ("the learner is reacting to previous_explanation: re-explain THAT idea the way "
                                      "they ask (e.g. simpler words, an everyday analogy), using only these facts")
    expl = _say(facts, client, kb, kn, trace, emit, "Nemotron answers about the message on screen",
                        prompt=EXPLAIN_PROMPT, claim_check=lesson_rules)
    _lesson(context, (context.get("scam") or {}).get("choice"))
    return _finish("", expl, client, trace, emit, context, kn, kb, title=scenario.TITLE, scene_changed=False,
                   snapshot=None, code=0, outcomes=bool(outcomes.get("active")), opening=None, executor=executor,
                   show_trace=show_trace)


def back_to_message(executor, before: dict, trace: Trace, emit=noop, context: dict | None = None,
                    show_trace: bool = False) -> int:
    context = context if context is not None else {}
    out, failed, code = _apply(scenario.clear_outcomes_request(), executor, before, trace, emit,
                               "Removing the outcomes", show_trace)
    if out is None:
        return code
    reply = ("Back to the message. Remember: when a text pushes you to act fast, check it yourself in your "
             "carrier's official app.")
    context.update(last_explanation=reply, last_kind="scam")
    context.setdefault("scam", {})["choice"] = None
    _lesson(context)
    trace.save()
    emit("turn", "ok" if code == 0 else "error", "Done", "", reply=reply, source="template", title=scenario.TITLE,
         scene_changed=True, lesson=context.get("lesson"), code=code, snapshot=out.get("snapshot"),
         stage={"kind": "scam", "cues": [{"text": reply, "focus": None, "caption": wrap_caption(reply),
                                          "callout": None}]})
    return code


def run(call, text: str, client, executor, before: dict, kb, kn, trace: Trace, emit=noop,
        context: dict | None = None, follow_up: bool = False, show_trace: bool = False) -> int | None:
    """Route a tool call to the Spot the Scam lesson. None = not a scam turn (the circuit tutor handles it)."""
    sc = on_stage(before)
    kw = dict(emit=emit, context=context, show_trace=show_trace)
    if call.tool == "teach_scam_texts":
        return teach(text, client, executor, before, kb, kn, trace, **kw)
    if call.tool == "scam_inspect":
        return inspect(text, client, executor, before, kb, kn, trace, **kw)
    if call.tool == "scam_choose":
        return choose(call.args["choice"], text, client, executor, before, kb, kn, trace, **kw)
    if call.tool == "scam_recover":
        return recover(text, client, executor, before, kb, kn, trace, **kw)
    if sc is None:
        return None
    if call.tool == "end_comparison" and (sc.get("outcomes") or {}).get("active"):
        return back_to_message(executor, before, trace, **kw)
    if call.tool in ("explain", "end_comparison") or follow_up or (
            call.tool == "explain_concept" and call.args.get("concept") in SCAM_CONCEPTS):
        return explain_question(text, client, executor, before, kb, kn, trace, follow_up=follow_up, **kw)
    return None
