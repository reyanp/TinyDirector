"""Allowlisted mapping from knowledge-base ids to real code. The database only ever stores these ids; nothing
read from the database is imported, evaluated or executed. Adding a check = add an entry here + in seed.json
(a test keeps the two equal)."""

from __future__ import annotations

import importlib
from typing import Callable

# check id -> (module, function). "calc.*" ids are calculators: f(args: dict) -> dict of formatted outputs, and
# are the only checks a fact may be re-computed with.
CHECKS: dict[str, tuple[str, str]] = {
    "calc.ohms_law": ("director.lesson.calculators", "ohms_law"),
    "calc.led_resistor": ("director.lesson.calculators", "led_resistor"),
    "calc.voltage_divider": ("director.lesson.calculators", "voltage_divider"),
    "calc.transistor_switch": ("director.lesson.calculators", "transistor_switch"),
    "circuit.solve": ("director.circuit.model", "solve"),
    "verify.explanation_numbers": ("director.circuit.agent", "number_check"),
    "verify.misconceptions": ("director.knowledge.claims", "find_wrong_claims"),
    "verify.circuit_readback": ("director.circuit.check", "run"),
    "verify.lesson_spec": ("director.lesson.spec", "validate"),
    "verify.lesson_build": ("director.lesson.verify", "structural"),
}


def resolve_check(check_id: str) -> Callable:
    if check_id not in CHECKS:
        raise KeyError(f"unknown check {check_id!r}")
    module, name = CHECKS[check_id]
    return getattr(importlib.import_module(module), name)


def calculator(check_id: str) -> Callable[[dict], dict]:
    if not check_id.startswith("calc."):
        raise KeyError(f"{check_id!r} is not a calculator check")
    return resolve_check(check_id)


def template_spec(name: str) -> dict:
    """Lesson templates are the reviewed reference specs in director/lesson/examples.py, by constant name."""
    from ..lesson import examples
    specs = {"LED_SPEC": examples.LED_SPEC, "BJT_MOSFET_SPEC": examples.BJT_MOSFET_SPEC,
             "OHMS_LAW_SPEC": examples.OHMS_LAW_SPEC, "VOLTAGE_DIVIDER_SPEC": examples.VOLTAGE_DIVIDER_SPEC}
    if name not in specs:
        raise KeyError(f"unknown template spec {name!r}")
    return specs[name]
