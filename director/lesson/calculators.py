"""Calculators the director (Nemotron) may call when it writes a lesson spec. The ONLY source of numbers.

Each calculator takes plain-number args, returns a dict of formatted strings (for labels and explanations)
plus `*_ma` floats for current-flow animation. Models are deliberately simple and their assumptions are
returned with the result so the explanation can say so. Add a calculator = add a function + an entry in
CALCULATORS (doc, args with defaults/ranges, outputs).
"""

from __future__ import annotations

import math

from ..circuit.model import LedCircuit, format_ma, format_ohms, format_volts, solve


class CalcError(ValueError):
    pass


def _num(args: dict, key: str, default: float, lo: float, hi: float) -> float:
    v = args.get(key, default)
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or not lo <= v <= hi:
        raise CalcError(f"{key} must be a number in [{lo:g}, {hi:g}], got {v!r}")
    return float(v)


def led_resistor(args: dict) -> dict:
    c = LedCircuit(source_v=_num(args, "source_v", 9, 0.5, 48), resistance_ohm=_num(args, "resistance_ohm", 330, 1, 1e6),
                   led_vf=_num(args, "led_vf", 2.0, 0.5, 5))
    s = solve(c)
    return {"V_source": format_volts(c.source_v), "V_led": format_volts(c.led_vf, 1), "R": format_ohms(c.resistance_ohm),
            "V_R": format_volts(s.v_resistor, 1), "I": format_ma(s.current_ma), "I_ma": round(s.current_ma, 4),
            "assumptions": "ideal battery and wires; LED = fixed forward-voltage drop"}


E12 = (1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2)


def _e12_at_most(ohms: float) -> float:
    """Largest standard E12 resistor value <= ohms (so the design current is at least what was asked)."""
    decade = 10 ** math.floor(math.log10(ohms))
    best = max(m * decade for m in E12 if m * decade <= ohms * (1 + 1e-9))
    return round(best, 6)


def transistor_switch(args: dict) -> dict:
    """An LED load (supply -> R_load -> LED -> transistor -> ground) switched by a logic signal.

    BJT (NPN, saturated):  I_C = (V_supply - V_LED - V_CE(sat)) / R_load
                           minimum base current I_B = I_C / beta; design rule for hard saturation I_B ~ I_C / 10,
                           base resistor R_B = (V_drive - V_BE) / (I_C / 10), rounded DOWN to an E12 value,
                           then I_B = (V_drive - V_BE) / R_B with that real resistor.
    MOSFET (N-channel, logic level, fully on): I_D = (V_supply - V_LED) / (R_load + R_DS(on));
                           steady gate current ~ 0 (the gate is insulated; current only flows while it charges).
    """
    vs = _num(args, "supply_v", 9, 1, 48)
    vf = _num(args, "led_vf", 2.0, 0.5, 5)
    rl = _num(args, "load_ohm", 330, 1, 1e6)
    vdrive = _num(args, "drive_v", 5, 1, 24)
    beta = _num(args, "beta", 100, 10, 1000)
    vbe, vce = _num(args, "vbe", 0.7, 0.3, 1.2), _num(args, "vce_sat", 0.2, 0, 1)
    rds = _num(args, "rds_on_ohm", 0.05, 0, 100)
    vth = _num(args, "vgs_th", 2.0, 0.3, 10)
    if vs <= vf + vce:
        raise CalcError("supply voltage too low to light the LED in this model")
    ic = (vs - vf - vce) / rl
    ib_min = ic / beta
    rb = _e12_at_most((vdrive - vbe) / (ic / 10))  # forced beta 10, rounded DOWN to a real resistor value
    ib_design = (vdrive - vbe) / rb
    idn = (vs - vf) / (rl + rds)
    gate_ok = vdrive >= vth + 1.5
    return {
        "V_supply": format_volts(vs), "V_drive": format_volts(vdrive), "R_load": format_ohms(rl), "beta": f"{beta:g}",
        "I_C": format_ma(ic * 1000), "I_C_ma": round(ic * 1000, 4),
        "I_B_min": format_ma(ib_min * 1000), "I_B_min_ma": round(ib_min * 1000, 4),
        "I_B_design": format_ma(ib_design * 1000), "I_B_design_ma": round(ib_design * 1000, 4),
        "R_B": format_ohms(rb), "V_led": format_volts(vf, 1),
        "V_BE": format_volts(vbe, 1), "V_CE_sat": format_volts(vce, 1),
        "I_D": format_ma(idn * 1000), "I_D_ma": round(idn * 1000, 4),
        "I_G": "≈ 0 mA", "I_G_ma": 0.0, "V_GS_th": format_volts(vth, 1),
        "gate_drive_ok": "yes" if gate_ok else "marginal",
        "assumptions": "BJT saturated (V_CE(sat) fixed), forced beta 10 for the base-resistor rule; logic-level "
                       "MOSFET fully on with tiny R_DS(on); DC steady state only; LED = fixed drop",
    }


def _format_power(watts: float) -> str:
    return f"{watts:.2f} W" if watts >= 1 else f"{watts * 1000:.0f} mW" if watts >= 0.01 else f"{watts * 1000:.2f} mW"


def ohms_law(args: dict) -> dict:
    """One resistor across a source: I = V / R, P = V * I."""
    v = _num(args, "source_v", 9, 0.1, 400)
    r = _num(args, "resistance_ohm", 1000, 0.1, 1e7)
    i = v / r
    return {"V_source": format_volts(v), "R": format_ohms(r), "I": format_ma(i * 1000), "I_ma": round(i * 1000, 4),
            "P": _format_power(v * i), "assumptions": "ideal source and wires"}


def voltage_divider(args: dict) -> dict:
    """Two series resistors: V_out = V_source * R2 / (R1 + R2) across R2 (unloaded)."""
    v = _num(args, "source_v", 9, 0.1, 400)
    r1 = _num(args, "r1_ohm", 10_000, 1, 1e7)
    r2 = _num(args, "r2_ohm", 10_000, 1, 1e7)
    i = v / (r1 + r2)
    return {"V_source": format_volts(v), "R1": format_ohms(r1), "R2": format_ohms(r2),
            "V_out": format_volts(v * r2 / (r1 + r2), 2), "V_R1": format_volts(v * r1 / (r1 + r2), 2),
            "ratio": f"{r2 / (r1 + r2):.2f}", "I": format_ma(i * 1000), "I_ma": round(i * 1000, 4),
            "assumptions": "ideal source; nothing connected to the output (unloaded divider)"}


CALCULATORS = {
    "ohms_law": {
        "fn": ohms_law,
        "doc": "One resistor across a source. I = V / R, power P = V * I.",
        "args": {"source_v": 9, "resistance_ohm": 1000},
        "outputs": ["V_source", "R", "I", "I_ma", "P"],
        "shown_on": {"V_source": "battery", "R": "resistor"},
    },
    "voltage_divider": {
        "fn": voltage_divider,
        "doc": "Two resistors in series on a source; V_out across R2 = V * R2 / (R1 + R2) (unloaded).",
        "args": {"source_v": 9, "r1_ohm": 10000, "r2_ohm": 10000},
        "outputs": ["V_source", "R1", "R2", "V_out", "V_R1", "ratio", "I", "I_ma"],
        "shown_on": {"V_source": "battery", "R1": "resistor", "R2": "resistor"},
    },
    "led_resistor": {
        "fn": led_resistor,
        "doc": "LED + series resistor on a battery. I = (V_source - V_led) / R.",
        "args": {"source_v": 9, "resistance_ohm": 330, "led_vf": 2.0},
        "outputs": ["V_source", "V_led", "R", "V_R", "I", "I_ma"],
        "shown_on": {"V_source": "battery", "R": "resistor"},
        "requires_parts": ["led"],  # the LED's forward drop is part of the math
    },
    "transistor_switch": {
        "fn": transistor_switch,
        "doc": "Logic signal switching an LED load through an NPN BJT or an N-channel MOSFET (both computed).",
        "args": {"supply_v": 9, "led_vf": 2.0, "load_ohm": 330, "drive_v": 5, "beta": 100},
        "outputs": ["V_supply", "V_drive", "R_load", "beta", "I_C", "I_C_ma", "I_B_min", "I_B_min_ma", "I_B_design",
                    "I_B_design_ma", "R_B", "V_led", "V_BE", "V_CE_sat", "I_D", "I_D_ma", "I_G", "I_G_ma", "V_GS_th"],
        # R_B only when the circuit has a BJT (the MOSFET gate is driven directly)
        "shown_on": {"V_supply": "battery", "R_load": "resistor", "R_B": ("resistor", "npn_bjt")},
        "requires_parts": ["led", ("npn_bjt", "n_mosfet")],  # an LED load, and at least one of the transistors
    },
}


def run(tool: str, args: dict) -> dict:
    if tool not in CALCULATORS:
        raise CalcError(f"unknown calculator {tool!r}; available: {', '.join(CALCULATORS)}")
    if not isinstance(args, dict):
        raise CalcError("args must be an object")
    unknown = set(args) - set(CALCULATORS[tool]["args"]) - {"vbe", "vce_sat", "rds_on_ohm", "vgs_th"}
    if unknown:
        raise CalcError(f"{tool}: unknown args {sorted(unknown)}")
    return CALCULATORS[tool]["fn"](args)


def _shown_text(c: dict) -> str:
    rows = [f"{out} on a {t if isinstance(t, str) else t[0] + ' (when there is a ' + t[1] + ')'}"
            for out, t in c.get("shown_on", {}).items()]
    req = [p if isinstance(p, str) else " or ".join(p) for p in c.get("requires_parts", [])]
    return ((f" The circuit MUST contain, labeled with the placeholder: {'; '.join(rows)}." if rows else "")
            + (f" It also needs a part of type: {', '.join(req)}." if req else ""))


def catalog_text() -> str:
    return "\n".join(f"- {name}: {c['doc']} args (defaults): {c['args']}. outputs: {', '.join(c['outputs'])}."
                     + _shown_text(c) for name, c in CALCULATORS.items())
