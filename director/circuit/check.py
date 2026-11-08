"""Check Blender's read-back of the lesson against the calculation (never trust the request's word).

Every check compares the circuit block Blender wrote (read from its own data: text bodies, modifier
inputs, F-curves, material colors, custom properties) with the request, AND re-derives the current
from the parameters Blender stored, so a stale label would be caught.
"""

from __future__ import annotations

from dataclasses import dataclass

from .display import COMPONENTS, LABELS
from .model import LedCircuit, format_ma, solve


@dataclass
class Check:
    ok: bool
    what: str
    detail: str = ""

    def line(self) -> str:
        return f"  {'PASS' if self.ok else 'FAIL'} {self.what}" + (f" ({self.detail})" if self.detail else "")


def _close(a, b, tol) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= tol


def _hex_close(a: str, b: str) -> bool:
    try:
        return all(abs(int(a[i:i + 2], 16) - int(b[i:i + 2], 16)) <= 2 for i in (1, 3, 5))
    except (TypeError, ValueError, IndexError):
        return False


def run(request: dict, before: dict | None, result: dict) -> list[Check]:
    """request = circuit_request_v1 sent; before = inspect result (or None); result = worker's answer."""
    checks: list[Check] = []
    add = lambda ok, what, detail="": checks.append(Check(bool(ok), what, detail))  # noqa: E731
    circ = result.get("circuit") or {}
    add(result.get("status") == "ok" and result.get("applied") == 1, "Blender applied the request",
        f"status={result.get('status')} errors={result.get('errors')}")
    add(circ.get("present"), "every lesson object exists",
        ", ".join(sorted(set(COMPONENTS + LABELS) - {o["id"] for o in circ.get("objects", [])})) or "")
    if not circ.get("present"):
        return checks
    want_c, want_d = request["circuit"], request["display"]
    got_p = circ.get("params", {})
    add(all(_close(got_p.get(k), want_c[k], 1e-9) for k in want_c), "circuit values stored on the objects",
        f"R={got_p.get('resistance_ohm')} V={got_p.get('source_v')} Vf={got_p.get('led_vf')}")
    # independent recomputation from what Blender stored
    stored = solve(LedCircuit.from_dict(got_p))
    add(circ["labels"].get("label_current") == f"I = {format_ma(stored.current_ma)}",
        "current label matches I recomputed from Blender's stored values",
        f"{circ['labels'].get('label_current')!r} vs {format_ma(stored.current_ma)}")
    wrong = [k for k in LABELS if circ["labels"].get(k) != want_d["labels"][k]]
    add(not wrong, "all labels show the calculated text", "differs: " + ", ".join(wrong) if wrong else "")
    flow = circ.get("flow", {})
    add(_close(flow.get("lap_frames"), want_d["flow_lap_frames"], 0.02) and flow.get("markers") == want_d["flow_markers"],
        "current-flow speed matches the calculated current",
        f"lap {flow.get('lap_frames')} frames, want {want_d['flow_lap_frames']}")
    add(_close(flow.get("power_at_show"), 1.0 if want_d["flow_lap_frames"] else 0.0, 1e-3)
        and _close(flow.get("power_at_start"), 0.0, 1e-3), "markers power on after the circuit closes")
    led = circ.get("led", {})
    add(_close(led.get("emission_at_show"), want_d["led_emission"], 0.01)
        and _close(led.get("light_w_at_show"), want_d["led_light_w"], 0.05) and led.get("status") == want_d["led_status"],
        "LED glow matches the calculated current",
        f"emission {led.get('emission_at_show')} want {want_d['led_emission']}, light {led.get('light_w_at_show')} W")
    bands = circ.get("resistor_bands", [])
    add(len(bands) == 4 and all(_hex_close(a, b) for a, b in zip(bands, want_d["resistor_bands"])),
        "resistor color bands match the value", " ".join(want_d.get("resistor_band_names", [])))

    if before is not None:
        b_objs = {o["id"]: o for o in (before.get("circuit") or {}).get("objects", [])}
        a_objs = {o["id"]: o for o in circ.get("objects", [])}
        if request["action"] == "update" and b_objs:
            recreated = [i for i in b_objs if i in a_objs and a_objs[i]["uid"] != b_objs[i]["uid"]]
            moved = [i for i in b_objs if i in a_objs and a_objs[i]["position"] != b_objs[i]["position"]]
            add(set(a_objs) == set(b_objs) and not recreated, "same objects as before (none recreated or removed)",
                f"recreated {recreated}" if recreated else f"{len(a_objs)} objects, ids and uids unchanged")
            add(not moved, "no object moved", f"moved {moved}" if moved else "")
        b_v1 = {o["id"]: o for o in before.get("scene_objects", [])}
        a_v1 = {o["id"]: o for o in result.get("scene_objects", [])}
        add(b_v1 == a_v1, "other TinyDirector objects untouched",
            f"{len(a_v1)} scene_plan objects" if b_v1 == a_v1 else "scene_plan objects changed")
    return checks


def _same_original(before: dict | None, circ: dict, add) -> None:
    """The lesson on stage before the comparison is still exactly there (same objects, values, animation)."""
    b = (before or {}).get("circuit") or {}
    if not b.get("present"):
        return
    key = lambda c: {o["id"]: (o["uid"], o["position"]) for o in c.get("objects", [])}  # noqa: E731
    add(key(circ) == key(b), "original circuit: same objects, same uid, same position",
        f"{len(circ.get('objects', []))} objects")
    add(circ.get("params") == b.get("params"), "original circuit values unchanged",
        f"R={circ.get('params', {}).get('resistance_ohm')}")
    labels = lambda c: {k: v for k, v in (c.get("labels") or {}).items() if k != "label_caption"}  # noqa: E731
    add(labels(circ) == labels(b) and circ.get("flow") == b.get("flow") and circ.get("led") == b.get("led"),
        "original labels, current arrows and LED glow unchanged")


def run_compare(request: dict, before: dict | None, result: dict) -> list[Check]:
    """request = compare_request_v1 sent; before = inspect result; result = worker's answer (circuit, compare,
    direction read-backs). Show: the what-if copy matches the calculation and the original is untouched.
    Clear: the copy is gone and the original is untouched."""
    checks: list[Check] = []
    add = lambda ok, what, detail="": checks.append(Check(bool(ok), what, detail))  # noqa: E731
    cmp, circ = result.get("compare") or {}, result.get("circuit") or {}
    add(result.get("status") == "ok" and result.get("applied") == 1, "Blender applied the comparison request",
        f"status={result.get('status')} errors={result.get('errors')}")
    _same_original(before, circ, add)
    if request["action"] == "clear":
        add(not cmp.get("active"), "the what-if copy is removed", "")
        return checks
    alt, want_c = cmp.get("alternative") or {}, request["alternative"]["circuit"]
    want_d = request["alternative"]["display"]
    add(cmp.get("active") and not cmp.get("error"), "the what-if copy exists", cmp.get("error", ""))
    got_p = alt.get("params") or {}
    add(all(_close(got_p.get(k), want_c[k], 1e-9) for k in want_c), "what-if values stored on the copy",
        f"R={got_p.get('resistance_ohm')}")
    if got_p:
        stored = solve(LedCircuit.from_dict(got_p))
        add((alt.get("labels") or {}).get("label_current") == f"I = {format_ma(stored.current_ma)}",
            "what-if current label matches I recomputed from the copy's stored values",
            f"{(alt.get('labels') or {}).get('label_current')!r} vs {format_ma(stored.current_ma)}")
    wrong = [k for k, v in (alt.get("labels") or {}).items() if want_d["labels"].get(k) != v]
    add(alt.get("labels") and not wrong, "what-if labels show the calculated text", ", ".join(wrong))
    flow = alt.get("flow") or {}
    add(_close(flow.get("lap_frames"), want_d["flow_lap_frames"], 0.02) and
        _close(flow.get("power"), 1.0 if want_d["flow_lap_frames"] else 0.0, 1e-3),
        "what-if arrow speed matches its calculated current",
        f"lap {flow.get('lap_frames')} frames, want {want_d['flow_lap_frames']}")
    led = alt.get("led") or {}
    add(_close(led.get("emission"), want_d["led_emission"], 0.01) and led.get("status") == want_d["led_status"],
        "what-if LED glow matches its calculated current (illustrative)",
        f"emission {led.get('emission')} want {want_d['led_emission']}")
    bands = alt.get("resistor_bands") or []
    add(len(bands) == 4 and all(_hex_close(a, b) for a, b in zip(bands, want_d["resistor_bands"])),
        "what-if resistor color bands match its value", " ".join(want_d.get("resistor_band_names", [])))
    add(cmp.get("headlines") == request["headlines"], "headlines show both calculations",
        " | ".join((cmp.get("headlines") or {}).values()).replace("\n", " "))
    add((result.get("direction") or {}).get("focus") == "compare", "camera frames both circuits",
        f"focus = {(result.get('direction') or {}).get('focus')}")
    return checks


def summary(checks: list[Check]) -> str:
    bad = sum(not c.ok for c in checks)
    return f"{len(checks) - bad}/{len(checks)} checks passed" + (f", {bad} FAILED" if bad else "")
