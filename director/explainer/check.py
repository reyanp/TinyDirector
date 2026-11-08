"""Check Blender's read-back of an OVERRIDE explainer scene against the request (never trust the request's word).

Hard checks (a failure fails the turn): applied, every element built with its shape and its EXACT label, the
title and the "not verified" labels on screen, every arrow drawn, motion keyed exactly where asked, the stored
spec equals the request. Layout checks (in frame, no overlaps) are measured from the camera's home framing and
reported as warnings: the content is still on screen and correct.
"""

from __future__ import annotations

from ..circuit.check import Check, summary  # noqa: F401 - same Check / summary as the circuit lesson

LAYOUT_CHECKS = ("every element is inside the camera frame", "no two elements overlap on screen")


def run(request: dict, result: dict) -> list[Check]:
    checks: list[Check] = []
    add = lambda ok, what, detail="": checks.append(Check(bool(ok), what, detail))  # noqa: E731
    ex = result.get("explainer") or {}
    add(result.get("status") == "ok" and result.get("applied") == 1, "Blender applied the request",
        f"status={result.get('status')} errors={result.get('errors')}")
    if request["action"] == "clear":
        add(not ex.get("present"), "the generated lesson is gone")
        return checks
    add(ex.get("present"), "the generated lesson is in the scene")
    if not ex.get("present"):
        return checks
    spec = request["spec"]
    els = ex.get("elements") or {}
    want = {e["id"]: e for e in spec["elements"]}
    add(set(els) == set(want), "every element was built (and nothing else)",
        f"missing {sorted(set(want) - set(els))} extra {sorted(set(els) - set(want))}")
    wrong = [k for k, e in want.items() if (els.get(k) or {}).get("label") != e["label"]]
    add(not wrong, "every label shows the exact text", ", ".join(f"{k}: {(els.get(k) or {}).get('label')!r}"
                                                               for k in wrong))
    wrong = [k for k, e in want.items() if (els.get(k) or {}).get("shape") != e["shape"]]
    add(not wrong, "every element has the requested shape", ", ".join(wrong))
    moving = {k for k, v in els.items() if v.get("animated")}
    asked = {k for k, e in want.items() if e.get("motion", "none") != "none"}
    add(moving == asked, "motion keyed exactly on the moving elements", f"keyed {sorted(moving)}, asked {sorted(asked)}")
    links = ex.get("links") or []
    add(len(links) == len(spec["links"]) and all(ln.get("drawn") for ln in links), "every arrow is drawn",
        f"{sum(1 for ln in links if ln.get('drawn'))}/{len(spec['links'])}")
    bad = [i for i, (a, b) in enumerate(zip(links, spec["links"]))
           if (a.get("from"), a.get("to")) != (b["from"], b["to"]) or (a.get("label") or "") != b.get("label", "")]
    add(not bad, "arrows connect the right elements with the right words", f"links {bad}")
    add(ex.get("title") == spec["title"], "title shown", repr(ex.get("title")))
    add("not verified" in (ex.get("banner") or "") and "not checked" in (ex.get("legend") or ""),
        "the lesson is labeled generated / not verified on screen", repr(ex.get("banner")))
    add(ex.get("spec") == spec, "Blender stored the spec it was sent (follow-ups read it back)")
    out = [k for k, v in els.items() if not v.get("in_frame")]
    add(not out, LAYOUT_CHECKS[0], ", ".join(out))
    add(not ex.get("overlaps"), LAYOUT_CHECKS[1], str(ex.get("overlaps"))[:200])
    return checks


def hard_failures(checks: list[Check]) -> list[Check]:
    return [c for c in checks if not c.ok and c.what not in LAYOUT_CHECKS]
