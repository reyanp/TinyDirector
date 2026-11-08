"""Part catalog shared by the director (spec validation, prompts) and the Blender toolkit. Stdlib only.

Pins are the ONLY way parts connect, so wiring can be checked geometrically: a wire must start and end
on the named pins (within a few millimetres of scene units).
"""

# size = footprint W x D x H in meters at rotate=0 (X, Y, Z), for layout planning
PARTS = {
    "battery": {"pins": ["+", "-"], "size": (0.6, 0.9, 1.8), "desc": "battery block (prints the voltage from its label), snap terminals on top (+ at back, - at front)"},
    "resistor": {"pins": ["a", "b"], "size": (2.0, 0.6, 0.7), "desc": "through-hole resistor lying along X; pin a = left lead end, b = right; color bands drawn from an 'NNN Ω' value in its label"},
    "led": {"pins": ["anode", "cathode"], "size": (0.7, 0.7, 1.9), "desc": "5 mm LED standing up; legs along Y (anode at +Y/back, cathode at -Y/front); color param"},
    "npn_bjt": {"pins": ["E", "B", "C"], "size": (0.6, 0.5, 1.2), "desc": "NPN BJT, TO-92 package (e.g. 2N3904), legs E-B-C left to right along X, flat face to -Y"},
    "n_mosfet": {"pins": ["G", "D", "S"], "size": (1.0, 0.5, 2.0), "desc": "N-channel logic-level MOSFET, TO-220 (e.g. IRLZ44N), legs G-D-S left to right along X"},
    "signal": {"pins": ["out", "gnd"], "size": (1.4, 0.9, 0.4), "desc": "logic signal source (microcontroller board); header pins at the front edge"},
    "ground": {"pins": ["gnd"], "size": (1.4, 0.2, 0.1), "desc": "ground / 0 V rail (a metal bar along X)"},
    "switch": {"pins": ["a", "b"], "size": (0.7, 0.6, 0.5), "desc": "push-button switch, pins left/right along X"},
    "capacitor": {"pins": ["+", "-"], "size": (0.5, 0.5, 1.2), "desc": "electrolytic capacitor (can on two legs, stripe marks -)"},
    "custom": {"pins": None, "size": None, "desc": "anything else: the spec lists its pins and the builder models it from primitives"},
}

COLORS = {"red": "#D9342B", "black": "#202226", "orange": "#E58A1F", "yellow": "#E8C547", "blue": "#2F6FD6",
          "green": "#2E9B3C", "white": "#E8E8E8", "gray": "#8A8A8A", "purple": "#8B4FC7"}
LED_COLORS = {"red": "#FF2A1A", "green": "#2CFF5A", "blue": "#3A7BFF", "yellow": "#FFD21A", "white": "#F4F4FF"}


def pins_for(part: dict) -> list[str]:
    spec = PARTS.get(part.get("type"), {})
    return list(part.get("pins") or []) if spec.get("pins") is None else list(spec["pins"])
