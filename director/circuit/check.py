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


def summary(checks: list[Check]) -> str:
    bad = sum(not c.ok for c in checks)
    return f"{len(checks) - bad}/{len(checks)} checks passed" + (f", {bad} FAILED" if bad else "")
