"""Simplified DC model of ONE circuit: 9 V battery -> resistor -> LED -> back to the battery.

This is the only place numbers come from. Nemotron never computes a current; it picks a tool, this
module computes, and the numbers are passed to the explanation and to Blender as data.

Model (deliberately simple, for teaching):
  - Ideal battery: constant source_v, no internal resistance.
  - Ideal resistor: exactly resistance_ohm, no tolerance, no heating.
  - LED as a fixed voltage drop: below led_vf it conducts nothing; at or above it, it holds exactly led_vf.
  - Series loop, so one current everywhere:  I = (V_source - V_LED) / R   when V_source > V_LED.
  - Wires: zero resistance.
Limitations (by design, see docs/CIRCUIT_TUTOR.md):
  - Not a circuit solver: one topology only, DC only, no transients.
  - A real LED's voltage rises slightly with current (an exponential I-V curve); ignored here.
  - With no resistor (R = 0) this model predicts unlimited current. That is the point of the lesson,
    but the model cannot say what really happens (battery internal resistance, the LED failing).
  - LED ratings are typical values for a 5 mm indicator LED, not a specific datasheet.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

R_MIN_OHM = 10.0
R_MAX_OHM = 1_000_000.0
TYPICAL_LED_MA = 20.0  # typical continuous rating of a 5 mm indicator LED (illustrative)
MAX_LED_MA = 30.0  # typical absolute maximum (illustrative)

BAND_COLORS = ["black", "brown", "red", "orange", "yellow", "green", "blue", "violet", "gray", "white"]


class CircuitModelError(ValueError):
    """The request is outside what the simplified model can answer."""


@dataclass(frozen=True)
class LedCircuit:
    source_v: float = 9.0
    resistance_ohm: float = 330.0
    led_vf: float = 2.0
    led_color: str = "red"

    def with_resistance(self, ohms: float) -> "LedCircuit":
        return replace(self, resistance_ohm=float(ohms))

    def as_dict(self) -> dict:
        return {"source_v": self.source_v, "resistance_ohm": self.resistance_ohm, "led_vf": self.led_vf}

    @classmethod
    def from_dict(cls, d: dict) -> "LedCircuit":
        return cls(source_v=float(d["source_v"]), resistance_ohm=float(d["resistance_ohm"]),
                   led_vf=float(d["led_vf"]))


@dataclass(frozen=True)
class Solution:
    circuit: LedCircuit
    conducting: bool
    current_a: float
    v_resistor: float
    v_led: float
    p_resistor_w: float
    p_led_w: float
    rating: str  # "off" | "ok" | "high" | "over_max"

    @property
    def current_ma(self) -> float:
        return self.current_a * 1000.0


def _finite_positive(name: str, v: float) -> None:
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v <= 0:
        raise CircuitModelError(f"{name} must be a positive number, got {v!r}")


def rating_for(current_ma: float) -> str:
    if current_ma <= 0:
        return "off"
    if current_ma <= TYPICAL_LED_MA:
        return "ok"
    if current_ma <= MAX_LED_MA:
        return "high"
    return "over_max"


def solve(c: LedCircuit) -> Solution:
    """Series current from the simplified model. Raises CircuitModelError outside its scope."""
    _finite_positive("source voltage", c.source_v)
    _finite_positive("LED forward voltage", c.led_vf)
    if not isinstance(c.resistance_ohm, (int, float)) or not math.isfinite(c.resistance_ohm) or c.resistance_ohm <= 0:
        raise CircuitModelError(
            "with no resistor (R = 0) the simplified model predicts unlimited current; it cannot give a number. "
            "A real LED would be destroyed.")
    if c.source_v <= c.led_vf:  # LED not forward-biased enough to conduct (simplified threshold)
        return Solution(c, False, 0.0, 0.0, c.source_v, 0.0, 0.0, "off")
    i = (c.source_v - c.led_vf) / c.resistance_ohm
    v_r = i * c.resistance_ohm
    return Solution(c, True, i, v_r, c.led_vf, i * v_r, i * c.led_vf, rating_for(i * 1000.0))


# ---------- standard resistor values and the 4-band color code ----------

def _two_digits(ohms: float) -> tuple[int, int] | None:
    """(two significant digits, power of ten) if ohms is exactly d1d2 x 10^k with k >= 0, else None."""
    if ohms < 10:
        return None
    exp = int(math.floor(math.log10(ohms))) - 1
    digits = round(ohms / 10 ** exp)
    if digits == 100:  # rounding pushed to three digits (e.g. 99.99...)
        digits, exp = 10, exp + 1
    return (digits, exp) if abs(digits * 10 ** exp - ohms) <= 1e-9 * ohms else None


def check_resistance(ohms) -> str | None:
    """None if this is a value the lesson can show (a real 4-band resistor), else a short reason."""
    if not isinstance(ohms, (int, float)) or isinstance(ohms, bool) or not math.isfinite(ohms):
        return f"resistance must be a number, got {ohms!r}"
    if ohms <= 0:
        return "R must be greater than 0 (with no resistor the simplified model has no current limit)"
    if not R_MIN_OHM <= ohms <= R_MAX_OHM:
        return f"this lesson supports {format_ohms(R_MIN_OHM)} to {format_ohms(R_MAX_OHM)}, got {format_ohms(ohms)}"
    if _two_digits(ohms) is None:
        return (f"{format_ohms(ohms)} is not a 2-significant-digit resistor value; "
                "use a standard value such as 220, 330, 470, 1000, 2200 or 4700 ohms")
    return None


def color_bands(ohms: float) -> list[str]:
    """4-band code: digit, digit, multiplier, tolerance (gold = 5%). E.g. 330 -> orange orange brown gold."""
    td = _two_digits(ohms)
    if td is None or not R_MIN_OHM <= ohms <= R_MAX_OHM:
        raise CircuitModelError(check_resistance(ohms) or f"no color code for {ohms}")
    digits, exp = td
    return [BAND_COLORS[digits // 10], BAND_COLORS[digits % 10], BAND_COLORS[exp], "gold"]


# ---------- formatting (the single source of every number shown to the user) ----------

def format_ohms(ohms: float) -> str:
    if ohms >= 1_000_000:
        return f"{ohms / 1_000_000:g} MΩ"
    if ohms >= 10_000:
        return f"{ohms / 1000:g} kΩ"
    return f"{ohms:,.0f} Ω" if ohms == int(ohms) else f"{ohms:g} Ω"


def format_ma(current_ma: float) -> str:
    return f"{current_ma:.1f} mA" if current_ma < 100 else f"{current_ma:.0f} mA"


def format_volts(v: float, places: int | None = None) -> str:
    """9 -> "9 V"; with places=1, 7 -> "7.0 V" (used for computed drops, so precision is explicit)."""
    if places is not None:
        return f"{v:.{places}f} V"
    return f"{v:g} V" if v == int(v) else f"{v:.1f} V"


E6 = (1.0, 1.5, 2.2, 3.3, 4.7, 6.8)


def step_resistance(ohms: float, direction: str) -> float:
    """'Make it bigger / smaller' without a value: the standard E6 value nearest to 3x (or 1/3) the present one,
    so the change is clearly visible (330 -> 1,000 Ω, 1,000 -> 330 Ω). Raises CircuitModelError at the ends."""
    if direction not in ("increase", "decrease"):
        raise CircuitModelError(f"direction must be increase or decrease, got {direction!r}")
    goal = ohms * 3 if direction == "increase" else ohms / 3
    values = [round(m * 10 ** e, 6) for e in range(1, 7) for m in E6]
    values = [v for v in values if R_MIN_OHM <= v <= R_MAX_OHM
              and (v > ohms if direction == "increase" else v < ohms)]
    if not values:
        raise CircuitModelError(f"the resistor is already at the {'largest' if direction == 'increase' else 'smallest'} "
                                f"value this lesson shows ({format_ohms(ohms)})")
    return min(values, key=lambda v: abs(math.log(v / goal)))
