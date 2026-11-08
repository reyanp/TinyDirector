"""lesson_spec_v1: validation, circuit lint, and placeholder resolution.

The spec is what the scene MUST show. It is checked three ways before anything is built:
  1. JSON Schema (shared/schema/lesson_spec_v1.json)
  2. references: unique ids, real pins, connections between existing pins, flows along connected wires,
     labels/steps pointing at existing things, placeholders that name a real calculator output
  3. circuit lint: no shorted battery, no part with every pin on one net, LED not shorted
and numbers: any digit in display text must come from a {calc.output} placeholder (part numbers like
2N3904 are fine). Then resolve() runs the calculators and substitutes the formatted results.
"""

from __future__ import annotations

import copy
import json
import re
from functools import lru_cache

from jsonschema import Draft202012Validator

from blender_engine.lesson_parts import PARTS, pins_for

from .. import paths
from .calculators import CALCULATORS, CalcError, run as run_calc

PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\.([A-Za-z0-9_]+)\}")
# a standalone number: not part of a model number like 2N3904 / TO-92 / IRLZ44N
BARE_NUMBER = re.compile(r"(?<![A-Za-z0-9.\-])\d+(?:[.,]\d+)*(?![.,]?\d)(?![A-Za-z]+\d)")


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads((paths.SCHEMA_DIR / "lesson_spec_v1.json").read_text(encoding="utf-8")))


def _texts(spec: dict):
    """(where, text) for every string that ends up on screen."""
    yield "title", spec.get("title", "")
    for p in spec.get("parts", []):
        yield f"part {p.get('id')} label", p.get("label", "")
    for f in spec.get("flows", []):
        yield f"flow {f.get('id')} label", f.get("label", "")
    for lab in spec.get("labels", []):
        yield f"label {lab.get('id')}", lab.get("text", "")
    for i, k in enumerate(spec.get("key_points", [])):
        yield f"key point {i + 1}", k
    for i, s in enumerate(spec.get("steps", [])):
        yield f"step {i + 1} caption", s.get("caption", "")


def _nets(spec: dict) -> dict[str, int]:
    """Union-find over pins joined by connections: pin -> net number."""
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for p in spec["parts"]:
        for pin in pins_for(p):
            find(f"{p['id']}.{pin}")
    for c in spec["connections"]:
        parent[find(c["from"])] = find(c["to"])
    roots = {}
    return {pin: roots.setdefault(find(pin), len(roots)) for pin in list(parent)}


def _placeholder_hints(spec: dict, numbers: list[str]) -> str:
    """Suggest the placeholder whose value matches a hand-typed number: '5' -> {sw.V_drive} (value '5 V')."""
    out = []
    for n in numbers:
        for c in spec.get("calculations", []):
            try:
                res = run_calc(c["tool"], c["args"])
            except Exception:  # noqa: BLE001 - hints are best effort
                continue
            match = next((k for k, v in res.items() if isinstance(v, str) and k != "assumptions"
                          and re.match(rf"^{re.escape(n)}(?![\d.,])", v)), None)
            if match:
                out.append(f"{{{c['id']}.{match}}} for {n!r} (value {res[match]!r}, unit included)")
                break
    return "; ".join(out)


def autofix_numbers(spec: dict) -> list[str]:
    """Replace hand-typed values that EXACTLY equal a calculator output ("5 V" when V_drive is "5 V") with that
    output's placeholder. Changes `spec` in place and returns what was replaced. Anything else stays an error."""
    values: dict[str, str] = {}
    for c in spec.get("calculations", []) if isinstance(spec, dict) else []:
        try:
            res = run_calc(c["tool"], c["args"])
        except Exception:  # noqa: BLE001 - validate() reports bad calculations
            continue
        for k, v in res.items():
            if isinstance(v, str) and k != "assumptions" and re.search(r"\d", v):
                values.setdefault(v, f"{{{c['id']}.{k}}}")
    fixes: list[str] = []

    def fix(text):
        if not isinstance(text, str):
            return text
        for v, tag in sorted(values.items(), key=lambda kv: -len(kv[0])):  # longest first: "4.50 V" before "5 V"
            pat = re.compile(rf"(?<![\w.,{{]){re.escape(v)}(?![\w}}])")
            if pat.search(PLACEHOLDER.sub("", text)):
                new = pat.sub(tag, text)
                if new != text:
                    fixes.append(f"{text!r} -> {new!r}")
                    text = new
        return text

    spec["title"] = fix(spec.get("title"))
    for kind, key in (("parts", "label"), ("flows", "label"), ("labels", "text"), ("steps", "caption")):
        for item in spec.get(kind, []) if isinstance(spec.get(kind), list) else []:
            if isinstance(item, dict) and key in item:
                item[key] = fix(item[key])
    if isinstance(spec.get("key_points"), list):
        spec["key_points"] = [fix(k) for k in spec["key_points"]]
    return fixes


def validate(spec) -> tuple[list[str], list[str]]:
    """(errors, warnings). Errors block the build; warnings are reported."""
    errs = [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
            for e in sorted(_validator().iter_errors(spec), key=lambda e: list(e.absolute_path))]
    if errs:
        return errs, []
    warns: list[str] = []
    ids: dict[str, str] = {}
    for kind in ("parts", "connections", "flows", "labels", "calculations"):
        for item in spec[kind]:
            if item["id"] in ids:
                errs.append(f"duplicate id {item['id']!r} ({ids[item['id']]} and {kind})")
            ids[item["id"]] = kind
    parts = {p["id"]: p for p in spec["parts"]}
    for p in spec["parts"]:
        if p["type"] == "custom" and not p.get("pins"):
            errs.append(f"custom part {p['id']} must list its pins")
        if p["type"] != "custom" and p.get("pins"):
            errs.append(f"part {p['id']}: only custom parts declare pins ({p['type']} pins are {PARTS[p['type']]['pins']})")
    conns = {c["id"]: c for c in spec["connections"]}
    for c in spec["connections"]:
        for end in ("from", "to"):
            pid, _, pin = c[end].partition(".")
            if pid not in parts:
                errs.append(f"connection {c['id']}: unknown part {pid!r}")
            elif pin not in pins_for(parts[pid]):
                errs.append(f"connection {c['id']}: {pid} ({parts[pid]['type']}) has no pin {pin!r}; "
                            f"pins are {pins_for(parts[pid])}")
        if c["from"] == c["to"]:
            errs.append(f"connection {c['id']} connects a pin to itself")
    calc_ids = {c["id"]: c for c in spec["calculations"]}
    for c in spec["calculations"]:
        if c["tool"] not in CALCULATORS:
            errs.append(f"calculation {c['id']}: unknown tool {c['tool']!r}; available: {', '.join(CALCULATORS)}")
    for f in spec["flows"]:
        missing = [w for w in f["path"] if w not in conns]
        if missing:
            errs.append(f"flow {f['id']}: unknown connections {missing}")
            continue
        for a, b in zip(f["path"], f["path"][1:]):
            pa = {conns[a]["from"].split(".")[0], conns[a]["to"].split(".")[0]}
            pb = {conns[b]["from"].split(".")[0], conns[b]["to"].split(".")[0]}
            if not pa & pb:
                errs.append(f"flow {f['id']}: {a} and {b} do not share a part, so the path is not continuous")
        cid, _, key = f["current_ma"][1:-1].partition(".")
        if not key.endswith("_ma"):
            errs.append(f"flow {f['id']}: current_ma must reference a *_ma output, got {f['current_ma']}")
    for lab in spec["labels"]:
        if lab["near"] not in parts and lab["near"] not in conns:
            errs.append(f"label {lab['id']}: near={lab['near']!r} is not a part or connection id")
    shown = {i for s in spec["steps"] for i in s["show"]}
    for s_i, s in enumerate(spec["steps"]):
        bad = [i for i in s["show"] if i not in ids or ids[i] == "calculations"]
        if bad:
            errs.append(f"step {s_i + 1}: unknown ids {bad}")
    not_shown = [i for i, k in ids.items() if k != "calculations" and i not in shown]
    if not_shown:
        warns.append(f"not revealed by any step (will appear in the last step): {not_shown}")
    for where, text in _texts(spec):
        for m in PLACEHOLDER.finditer(text):
            if m.group(1) not in calc_ids:
                errs.append(f"{where}: placeholder {m.group(0)} names no calculation")
            elif m.group(2) not in CALCULATORS.get(calc_ids[m.group(1)]["tool"], {}).get("outputs", []):
                errs.append(f"{where}: {m.group(0)} is not an output of {calc_ids[m.group(1)]['tool']}")
        doubled = re.findall(r"\}\s*(?:V|mA|A|Ω|kΩ|ohms?|volts?|W|mW)(?![A-Za-z])", text)
        if doubled:
            errs.append(f"{where}: placeholder values already include their unit; remove the unit after it in {text!r}")
        bare = [n for n in BARE_NUMBER.findall(PLACEHOLDER.sub("", text)) if n != "0"]  # "0 V rail", "~0 A" are fine
        if bare:
            hint = _placeholder_hints(spec, bare)
            errs.append(f"{where}: numbers must come from calculator placeholders, found {bare} in {text!r}"
                        + (f" (use {hint})" if hint else ""))
    if not errs:
        errs += lint(spec)
    return errs, warns


ASKED_PARTS = [(("mosfet", "mosfets", "fet", "fets"), "n_mosfet"), (("bjt", "bjts"), "npn_bjt"),
               (("capacitor", "capacitors"), "capacitor"), (("led", "leds"), "led")]


def lint(spec: dict) -> list[str]:
    """Physics sanity that does not need a solver, and calculation <-> drawn circuit consistency."""
    nets, errs = _nets(spec), []
    types = {p["type"] for p in spec["parts"]}
    for c in spec["calculations"]:  # every resistor / supply the calculation assumes must be in the scene
        for out, need in CALCULATORS[c["tool"]].get("shown_on", {}).items():
            ptype, only_if = (need, None) if isinstance(need, str) else need
            if only_if and only_if not in types:
                continue
            tag = "{" + c["id"] + "." + out + "}"
            if not any(p["type"] == ptype and tag in p["label"] for p in spec["parts"]):
                errs.append(f"calculation {c['id']} ({c['tool']}) assumes {out}: the circuit must contain a {ptype} "
                            f"part whose label shows {tag}, wired where the calculation assumes it")
        for need in CALCULATORS[c["tool"]].get("requires_parts", []):
            options = (need,) if isinstance(need, str) else need
            if not types & set(options):
                errs.append(f"calculation {c['id']} ({c['tool']}) models a circuit with a {' or '.join(options)}; "
                            f"the drawn circuit has none, so its numbers would not match the scene")
    q = " " + re.sub(r"[^a-z0-9]+", " ", spec.get("question", "").lower()) + " "
    for words, ptype in ASKED_PARTS:  # answer the question that was asked: named components must be shown
        if any(f" {w} " in q for w in words) and ptype not in types:
            errs.append(f"the question asks about {words[0].upper() if len(words[0]) <= 6 else words[0]}, but the "
                        f"circuit has no {ptype} part: show it (in its own group when comparing)")
    by_pin_net: dict[int, set[str]] = {}
    for pin, net in nets.items():
        by_pin_net.setdefault(net, set()).add(pin.split(".")[0])
    ptype_of = {p["id"]: p["type"] for p in spec["parts"]}
    conns = {c["id"]: c for c in spec["connections"]}
    for f in spec["flows"]:  # flow markers show STEADY (DC) current; none flows through a charged capacitor
        for a, b in zip(f["path"], f["path"][1:]):
            shared = {conns[a]["from"].split(".")[0], conns[a]["to"].split(".")[0]} & \
                     {conns[b]["from"].split(".")[0], conns[b]["to"].split(".")[0]}
            caps = [q for q in shared if ptype_of.get(q) == "capacitor"]
            if caps:
                errs.append(f"flow {f['id']} passes through capacitor {caps[0]}: flows animate steady DC current, "
                            f"and no steady current flows through a capacitor")
    for p in spec["parts"]:
        if p["type"] == "led":
            near = by_pin_net[nets[f"{p['id']}.anode"]] | by_pin_net[nets[f"{p['id']}.cathode"]]
            if not any(ptype_of[q] == "resistor" for q in near):
                errs.append(f"LED {p['id']} has no series resistor next to it: nothing would limit its current "
                            f"(put a resistor between the supply and its anode, or after its cathode)")
    for p in spec["parts"]:
        pins = [f"{p['id']}.{x}" for x in pins_for(p)]
        if len(pins) >= 2 and len({nets[x] for x in pins}) == 1 and any(
                c["from"] in pins or c["to"] in pins for c in spec["connections"]):
            errs.append(f"{p['id']} ({p['type']}) has all its pins on one net: it is shorted out")
        if p["type"] == "battery" and nets[f"{p['id']}.+"] == nets[f"{p['id']}.-"]:
            errs.append(f"battery {p['id']} is short-circuited (+ and - on the same net)")
    return errs


def resolve(spec: dict) -> tuple[dict, dict]:
    """Run the calculators and substitute placeholders. Returns (resolved spec, calculator results)."""
    results = {}
    for c in spec["calculations"]:
        try:
            results[c["id"]] = run_calc(c["tool"], c["args"])
        except CalcError as exc:
            raise CalcError(f"calculation {c['id']} ({c['tool']}): {exc}") from None
    out = copy.deepcopy(spec)

    def sub(text: str) -> str:
        return PLACEHOLDER.sub(lambda m: str(results[m.group(1)][m.group(2)]), text)

    out["title"] = sub(out["title"])
    for p in out["parts"]:
        p["label"] = sub(p["label"])
    for lab in out["labels"]:
        lab["text"] = sub(lab["text"])
    out["key_points"] = [sub(k) for k in out["key_points"]]
    for s in out["steps"]:
        s["caption"] = sub(s["caption"])
    for f in out["flows"]:
        f["label"] = sub(f["label"])
        cid, _, key = f["current_ma"][1:-1].partition(".")
        f["current_ma"] = float(results[cid][key])
    return out, results
