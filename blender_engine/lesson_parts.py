"""Part catalog shared by the director (spec validation, prompts) and the Blender toolkit. Stdlib only.

Pins are the ONLY way parts connect, so wiring can be checked geometrically: a wire must start and end
on the named pins (within a few millimetres of scene units).
"""

PARTS = {
    "battery": {"pins": ["+", "-"], "desc": "9 V battery, snap terminals on top"},
    "resistor": {"pins": ["a", "b"], "desc": "through-hole resistor with color bands (lies along X)"},
    "led": {"pins": ["anode", "cathode"], "desc": "5 mm LED, long leg = anode; color via param"},
    "npn_bjt": {"pins": ["E", "B", "C"], "desc": "NPN BJT in a TO-92 package (e.g. 2N3904), legs E-B-C"},
    "n_mosfet": {"pins": ["G", "D", "S"], "desc": "N-channel logic-level MOSFET in TO-220 (e.g. IRLZ44N), legs G-D-S"},
    "signal": {"pins": ["out", "gnd"], "desc": "logic signal source (a microcontroller pin), e.g. 5 V"},
    "ground": {"pins": ["gnd"], "desc": "ground / 0 V reference rail"},
    "switch": {"pins": ["a", "b"], "desc": "push-button switch"},
    "capacitor": {"pins": ["+", "-"], "desc": "electrolytic capacitor (can on two legs)"},
    "custom": {"pins": None, "desc": "anything else: the spec lists its pins and the builder models it from primitives"},
}

COLORS = {"red": "#D9342B", "black": "#202226", "orange": "#E58A1F", "yellow": "#E8C547", "blue": "#2F6FD6",
          "green": "#2E9B3C", "white": "#E8E8E8", "gray": "#8A8A8A", "purple": "#8B4FC7"}
LED_COLORS = {"red": "#FF2A1A", "green": "#2CFF5A", "blue": "#3A7BFF", "yellow": "#FFD21A", "white": "#F4F4FF"}


def pins_for(part: dict) -> list[str]:
    spec = PARTS.get(part.get("type"), {})
    return list(part.get("pins") or []) if spec.get("pins") is None else list(spec["pins"])
