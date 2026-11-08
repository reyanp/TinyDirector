"""Verifier: does the scene Blender built match the spec, and is it readable? (code checks + Nemotron VL)

Structural (from Blender's read-back, never from the builder's word):
  every spec part exists with the right type; every connection is a wire whose END POINTS TOUCH the right pins;
  every flow follows the right wires with the calculated current and a speed proportional to it; labels,
  title, key points and captions show the resolved spec text; extra text carries no invented numbers.
Layout: parts don't overlap; wires don't pass through other parts; everything is inside the frame; labels
  don't cover each other, other parts, or the HUD.
Visual (optional): Nemotron VL looks at the render with the list of what must be visible.
"""

from __future__ import annotations

import base64
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from ..circuit.agent import number_check
from ..planner import extract_json

PIN_TOL = 0.02  # meters
FLOW_M_PER_FRAME_PER_MA = 0.0075  # must match blender_engine/toolkit.py
MAX_M_PER_FRAME = 0.4


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)  # block acceptance -> repair / fallback
    warnings: list[str] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)
    vision: dict | None = None

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        return (f"{len(self.passed)} checks passed, {len(self.errors)} errors, {len(self.warnings)} warnings"
                + (f"; vision: {'ok' if self.vision.get('matches') else 'issues'}" if self.vision else ""))


def _norm(t: str) -> str:
    return " ".join(str(t or "").replace("•", "").split())


def _dist(a, b) -> float:
    return math.dist(a, b)


def _area(r) -> float:
    return max(0.0, r[2] - r[0]) * max(0.0, r[3] - r[1])


def _inter(a, b) -> float:
    return _area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])


def structural(spec: dict, results: dict, rb: dict, rep: Report) -> None:
    if not rb.get("ok"):
        rep.errors.append(f"build failed: {rb.get('error')}")
        return
    E, P = rep.errors.append, rep.passed.append
    if _norm(rb.get("title")) != _norm(spec["title"]):
        E(f"title shows {rb.get('title')!r}, spec says {spec['title']!r}")
    else:
        P("title")
    parts, wires, flows, labels = rb["parts"], rb["wires"], rb["flows"], rb["labels"]
    for p in spec["parts"]:
        got = parts.get(p["id"])
        if got is None:
            E(f"part {p['id']} ({p['type']}) is missing")
        elif got["type"] != p["type"]:
            E(f"part {p['id']} is a {got['type']}, spec says {p['type']}")
        else:
            P(f"part {p['id']}")
            if p.get("label") and _norm(labels.get(f"{p['id']}_label", {}).get("text")) != _norm(p["label"]):
                E(f"part {p['id']} must show its label {p['label']!r} (pass label= to part())")
    for c in spec["connections"]:
        w = wires.get(c["id"])
        if w is None:
            E(f"connection {c['id']} ({c['from']} -> {c['to']}) has no wire with that id")
            continue
        ends = {w["from"], w["to"]}
        if ends != {c["from"], c["to"]}:
            E(f"wire {c['id']} joins {w['from']} and {w['to']}, spec says {c['from']} and {c['to']}")
            continue
        pa = parts.get(w["from"].split(".")[0], {}).get("pins", {}).get(w["from"].split(".")[1])
        pb = parts.get(w["to"].split(".")[0], {}).get("pins", {}).get(w["to"].split(".")[1])
        if pa is None or pb is None or _dist(w["start"], pa) > PIN_TOL or _dist(w["end"], pb) > PIN_TOL:
            E(f"wire {c['id']} does not touch its pins (start {w['start']} vs {pa}, end {w['end']} vs {pb})")
        else:
            P(f"wire {c['id']} touches {c['from']} and {c['to']}")
    extra = set(wires) - {c["id"] for c in spec["connections"]}
    if extra:
        E(f"wires not in the spec: {sorted(extra)} (every wire must be a spec connection)")
    for f in spec["flows"]:
        g = flows.get(f["id"])
        if g is None:
            E(f"flow {f['id']} is missing")
            continue
        if g["path"] != f["path"]:
            E(f"flow {f['id']} follows {g['path']}, spec says {f['path']}")
        if not math.isclose(g["current_ma"], f["current_ma"], rel_tol=1e-6, abs_tol=1e-9):
            E(f"flow {f['id']} uses {g['current_ma']} mA, the calculation says {f['current_ma']} mA")
        else:
            want = min(MAX_M_PER_FRAME, FLOW_M_PER_FRAME_PER_MA * f["current_ma"])
            got_speed = g["laps_per_frame"] * g["length_m"]
            if abs(got_speed - want) > 1e-3 + 0.01 * want:
                E(f"flow {f['id']} speed {got_speed:.4f} m/frame is not proportional to its current")
            else:
                P(f"flow {f['id']} {f['current_ma']:g} mA, speed proportional")
        if f.get("label") and _norm(labels.get(f"{f['id']}_label", {}).get("text")) != _norm(f["label"]):
            E(f"flow {f['id']} must show its label {f['label']!r} (pass label= to flow())")
    for lab in spec["labels"]:
        g = labels.get(lab["id"])
        if g is None:
            E(f"label {lab['id']} ({lab['text']!r}) is missing")
        elif _norm(g["text"]) != _norm(lab["text"]):
            E(f"label {lab['id']} shows {g['text']!r}, spec says {lab['text']!r}")
        else:
            P(f"label {lab['id']}")
    hud_text = _norm(" ".join(v for k, v in rb.get("hud", {}).items()))
    for k in spec["key_points"]:
        (P if _norm(k) in hud_text else E)(f"key point shown: {k!r}" if _norm(k) in hud_text else
                                           f"key point not shown: {k!r} (use key_points([...]))")
    for i, s in enumerate(spec["steps"]):
        cap = _norm(rb.get("hud", {}).get(f"hud_caption_{i + 1}"))
        if cap != _norm(s["caption"]):
            E(f"step {i + 1} caption {cap!r} != spec {s['caption']!r} (call step() once per spec step, in order)")
    if len([k for k in rb.get("hud", {}) if k.startswith("hud_caption_")]) != len(spec["steps"]):
        E(f"{len([k for k in rb.get('hud', {}) if k.startswith('hud_caption_')])} steps built, spec has "
          f"{len(spec['steps'])}")
    known = {lab["id"] for lab in spec["labels"]} | {f"{p['id']}_label" for p in spec["parts"]} | \
            {f"{f['id']}_label" for f in spec["flows"]}
    facts = {"calc": results, "spec": spec}
    for lid, g in labels.items():
        if lid not in known:
            bad = number_check(g["text"], facts)
            if bad:
                E(f"extra label {lid} has numbers not from the calculation: {bad}")


def layout(spec: dict, rb: dict, rep: Report) -> None:
    if not rb.get("ok"):
        return
    E, P = rep.errors.append, rep.passed.append
    parts = rb["parts"]
    ids = sorted(parts)
    overlaps = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            (alo, ahi), (blo, bhi) = parts[a]["bbox"], parts[b]["bbox"]
            if alo[0] < bhi[0] - 0.03 and blo[0] < ahi[0] - 0.03 and alo[1] < bhi[1] - 0.03 and blo[1] < ahi[1] - 0.03:
                overlaps.append(f"{a}/{b}")
    (E if overlaps else P)(f"parts overlap: {overlaps}" if overlaps else "no overlapping parts")
    through = []
    for wid, w in rb["wires"].items():
        own = {w["from"].split(".")[0], w["to"].split(".")[0]}
        for pid, p in parts.items():
            if pid in own or p["type"] == "ground":
                continue
            lo, hi = p["bbox"]
            for pt in w["points"][2:-2]:
                if lo[0] + 0.05 < pt[0] < hi[0] - 0.05 and lo[1] + 0.05 < pt[1] < hi[1] - 0.05 and pt[2] < hi[2]:
                    through.append(f"{wid} through {pid}")
                    break
    (E if through else P)(f"wires pass through parts: {through} (route with via=[...])" if through
                          else "no wire passes through a part")
    out = [k for k, v in list(parts.items()) + list(rb["labels"].items())
           if v["screen"][0] < 0.0 or v["screen"][1] < 0.0 or v["screen"][2] > 1.0 or v["screen"][3] > 1.0]
    (E if out else P)(f"outside the camera frame: {out}" if out else "everything inside the frame")
    labs = rb["labels"]
    lids = sorted(labs)
    clash = []
    for i, a in enumerate(lids):
        ra = labs[a]["screen"]
        for b in lids[i + 1:]:
            rb_ = labs[b]["screen"]
            if _inter(ra, rb_) > 0.15 * min(_area(ra), _area(rb_)):
                clash.append(f"{a}/{b}")
        for pid, p in parts.items():
            if pid == labs[a].get("near"):
                continue
            if _inter(ra, p["screen"]) > 0.35 * _area(ra):
                clash.append(f"{a} covers {pid}")
        for hid, hr in rb.get("hud_screen", {}).items():
            if rb.get("hud", {}).get(hid, "").strip() and _inter(ra, hr) > 0.1 * _area(ra):
                clash.append(f"{a} under HUD {hid}")
    (E if clash else P)(f"labels overlap: {clash} (move them with label_at or label(x=, y=, z=))" if clash
                        else "labels readable (no overlaps)")


VISION_PROMPT = """You check a 3D teaching scene rendered in Blender for the lesson "{title}".
It must clearly show: {must}.
Labels that must be readable: {labels}.
Look at the image. Judge readability and whether those things are visible and plausibly arranged (a wire from
one part's pin to another). Ignore the art style. Reply with ONE JSON object only:
{{"matches": true/false, "confidence": 0.0-1.0, "issues": ["short, concrete issue", ...]}}"""


def vision(spec: dict, image: Path, client, rep: Report) -> None:
    if client is None or not image.exists():
        return
    must = ", ".join(f"{p['id']} ({p['type'].replace('_', ' ')})" for p in spec["parts"])
    labels = "; ".join([lab["text"] for lab in spec["labels"]][:8])
    url = "data:image/png;base64," + base64.b64encode(image.read_bytes()).decode()
    msg = [{"role": "user", "content": [{"type": "text", "text": VISION_PROMPT.format(title=spec["title"], must=must,
                                                                                       labels=labels)},
                                        {"type": "image_url", "image_url": {"url": url}}]}]
    try:
        reply = client.complete(msg, max_tokens=600)
        data = extract_json(reply.content)
        rep.vision = {"matches": bool(data.get("matches")), "confidence": float(data.get("confidence", 0)),
                      "issues": [str(i)[:160] for i in (data.get("issues") or [])][:5],
                      "model": reply.model, "live": reply.live}
    except Exception as exc:  # noqa: BLE001 - vision is advisory
        rep.warnings.append(f"vision check skipped: {type(exc).__name__}: {exc}")
        return
    if not rep.vision["matches"] and rep.vision["confidence"] >= 0.6:
        rep.errors += [f"vision: {i}" for i in rep.vision["issues"]] or ["vision: scene does not match the spec"]
    else:
        rep.warnings += [f"vision: {i}" for i in rep.vision["issues"]]


def verify(spec: dict, results: dict, readback: dict, image: Path | None = None, vision_client=None,
           strict_layout: bool = True) -> Report:
    rep = Report()
    structural(spec, results, readback, rep)
    lay = Report()
    layout(spec, readback, lay)
    rep.passed += lay.passed
    (rep.errors if strict_layout else rep.warnings).extend(lay.errors)
    if rep.ok and image is not None:
        vision(spec, image, vision_client, rep)
    return rep


def load_readback(out_dir: Path) -> dict:
    p = Path(out_dir) / "readback.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"ok": False, "error": "no readback"}
