"""Circuit model -> what Blender should show (the `display` block of a circuit_request_v1).

Everything here is derived from model.solve(); Blender only draws it. The visual mappings are
ILLUSTRATIVE (documented in docs/CIRCUIT_TUTOR.md), not physical:
  - current-flow markers show CONVENTIONAL current (+ -> resistor -> LED -> -), not electron motion;
    their speed is proportional to the calculated current (real charge drift is ~mm/s).
  - LED glow strength is proportional to the calculated current (a fair first approximation for LEDs
    in their normal range), clamped so an over-driven LED does not blow out the image.
"""

from __future__ import annotations

import textwrap

from .model import (MAX_LED_MA, TYPICAL_LED_MA, LedCircuit, Solution, color_bands, format_ma, format_ohms,
                    format_volts, solve)

# stable ids of every lesson object (Blender object name == id, custom prop circuit_id == id)
COMPONENTS = ["board_1", "battery_1", "resistor_1", "led_1", "wire_1", "wire_2", "wire_3", "flow_1"]
LABELS = ["label_title", "label_battery", "label_resistor", "label_led", "label_current", "label_equation",
          "label_status", "label_caption", "label_legend"]

REF_MA = 20.0  # at 20 mA a marker takes BASE_LAP_FRAMES to go round the loop
BASE_LAP_FRAMES = 96.0  # 4 s at 24 fps
MIN_LAP_FRAMES = 12.0  # clamp: faster than this is unreadable
MAX_LAP_FRAMES = 2400.0
GLOW_PER_MA = 0.3  # LED emission strength per mA (330 ohm: ~6.4, 1 kohm: ~2.1)
GLOW_MAX = 40.0
LIGHT_W_PER_MA = 4.0  # the LED's point light (illuminates the board), watts per mA
LIGHT_MAX_W = 300.0
FLOW_MARKERS = 30

BAND_HEX = {"black": "#141414", "brown": "#6B3A1E", "red": "#C8231E", "orange": "#E8731A", "yellow": "#F2D21B",
            "green": "#2E9B3C", "blue": "#2457C5", "violet": "#7B3FB5", "gray": "#8A8A8A", "white": "#F2F2F2",
            "gold": "#C9A23A"}
STATUS_TEXT = {
    "off": "LED off: the battery voltage is below the LED's forward voltage",
    "ok": f"within a typical LED's {TYPICAL_LED_MA:g} mA rating",
    "high": f"just above a typical {TYPICAL_LED_MA:g} mA rating (limit ~{MAX_LED_MA:g} mA)",
    "over_max": f"TOO MUCH: above a typical LED's ~{MAX_LED_MA:g} mA maximum",
}
TITLE = "Why does an LED need a resistor?"
LEGEND = "Glowing arrows: conventional current (+ to −), speed proportional to current"


def lap_frames(current_ma: float) -> float | None:
    """Frames for one marker lap; None = no current, markers stop."""
    if current_ma <= 0:
        return None
    return round(min(MAX_LAP_FRAMES, max(MIN_LAP_FRAMES, BASE_LAP_FRAMES * REF_MA / current_ma)), 2)


def led_emission(current_ma: float) -> float:
    return round(min(GLOW_MAX, max(0.0, GLOW_PER_MA * current_ma)), 3)


def led_light_w(current_ma: float) -> float:
    return round(min(LIGHT_MAX_W, max(0.0, LIGHT_W_PER_MA * current_ma)), 2)


def equation_text(sol: Solution) -> str:
    c = sol.circuit
    if not sol.conducting:
        return f"{format_volts(c.source_v)} < {format_volts(c.led_vf)}: no current"
    return (f"I = ({format_volts(c.source_v)} − {format_volts(c.led_vf)}) ÷ {format_ohms(c.resistance_ohm)}"
            f" = {format_ma(sol.current_ma)}")


def wrap_caption(text: str, width: int = 70, max_lines: int = 5) -> str:
    lines = textwrap.wrap(" ".join(text.split()), width=width)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: width - 1].rstrip() + "…"
    return "\n".join(lines)


def title_for(tool: str, user_text: str = "", circuit: LedCircuit | None = None) -> str:
    """Scene title for this turn: the lesson question, the what-if being shown, or the learner's question."""
    if tool == "set_resistance" and circuit is not None:
        return f"What if the resistor is {format_ohms(circuit.resistance_ohm)}?"
    if tool == "explain" and user_text.strip():
        q = " ".join(user_text.split())
        q = q[0].upper() + q[1:]
        return q if len(q) <= 56 else q[:55].rstrip() + "…"  # one HUD line (the equation sits right below)
    return TITLE


def labels_for(sol: Solution, caption: str = "", title: str = TITLE) -> dict[str, str]:
    c = sol.circuit
    return {
        "label_title": title,
        "label_battery": f"{format_volts(c.source_v)} battery",
        "label_resistor": f"Resistor  R = {format_ohms(c.resistance_ohm)}\n"
                          f"voltage across it: {format_volts(sol.v_resistor, 1)}",
        "label_led": f"{c.led_color.capitalize()} LED\nforward voltage ≈ {format_volts(c.led_vf, 1)}",
        "label_current": f"I = {format_ma(sol.current_ma)}",
        "label_equation": equation_text(sol),
        "label_status": STATUS_TEXT[sol.rating],
        "label_caption": wrap_caption(caption) if caption else "",
        "label_legend": LEGEND,
    }


def display_for(sol: Solution, caption: str = "", title: str = TITLE) -> dict:
    ma = sol.current_ma
    return {
        "labels": labels_for(sol, caption, title),
        "flow_lap_frames": lap_frames(ma),
        "flow_markers": FLOW_MARKERS,
        "led_emission": led_emission(ma),
        "led_light_w": led_light_w(ma),
        "led_status": sol.rating,
        "resistor_bands": [BAND_HEX[b] for b in color_bands(sol.circuit.resistance_ohm)],
        "resistor_band_names": color_bands(sol.circuit.resistance_ohm),
    }


def circuit_request(request_id: str, action: str, circuit: LedCircuit, caption: str = "", title: str = TITLE) -> dict:
    """A circuit_request_v1 document (see shared/schema/circuit_request_v1.json)."""
    if action not in ("build", "update"):
        raise ValueError("action must be build or update")
    sol = solve(circuit)
    return {"kind": "circuit", "schema_version": 1, "request_id": request_id, "action": action,
            "circuit": circuit.as_dict(), "display": display_for(sol, caption, title)}
