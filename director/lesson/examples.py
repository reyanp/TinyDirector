"""Reference lesson specs: the format example in the director prompt, and offline fixtures (MOCK director)."""

LED_SPEC = {
    "schema_version": 1,
    "title": "Why does an LED need a resistor?",
    "question": "Why does an LED need a resistor?",
    "calculations": [{"id": "c", "tool": "led_resistor", "args": {"source_v": 9, "resistance_ohm": 330, "led_vf": 2.0}}],
    "parts": [
        {"id": "bat", "type": "battery", "label": "{c.V_source} battery"},
        {"id": "r1", "type": "resistor", "label": "{c.R}"},
        {"id": "led1", "type": "led", "label": "LED, about {c.V_led}", "color": "red"},
    ],
    "connections": [
        {"id": "w1", "from": "bat.+", "to": "r1.a", "color": "red"},
        {"id": "w2", "from": "r1.b", "to": "led1.anode", "color": "orange"},
        {"id": "w3", "from": "led1.cathode", "to": "bat.-", "color": "black"},
    ],
    "flows": [{"id": "f1", "path": ["w1", "w2", "w3"], "current_ma": "{c.I_ma}", "label": "I = {c.I}"}],
    "labels": [{"id": "l_r", "text": "drops {c.V_R}", "near": "r1"}],
    "key_points": ["The LED keeps about {c.V_led} across itself.",
                   "The resistor takes the rest ({c.V_R}) and sets the current: {c.I}.",
                   "Without it, nothing limits the current and the LED burns out."],
    "steps": [
        {"show": ["bat", "r1", "led1"], "caption": "A battery, a resistor and an LED."},
        {"show": ["w1", "w2", "w3", "f1", "l_r"], "caption": "Close the loop: the resistor limits the current to {c.I}."},
    ],
}

BJT_MOSFET_SPEC = {
    "schema_version": 1,
    "title": "BJT vs MOSFET as an LED switch",
    "question": "Why would someone use a BJT over a MOSFET?",
    "calculations": [{"id": "sw", "tool": "transistor_switch",
                      "args": {"supply_v": 9, "led_vf": 2.0, "load_ohm": 330, "drive_v": 5, "beta": 100}}],
    "parts": [
        {"id": "bat", "type": "battery", "label": "{sw.V_supply} supply"},
        {"id": "mcu", "type": "signal", "label": "{sw.V_drive} logic pin"},
        {"id": "gnd", "type": "ground", "label": "ground"},
        {"id": "rb", "type": "resistor", "label": "R_B {sw.R_B}", "group": "bjt"},
        {"id": "q1", "type": "npn_bjt", "label": "NPN BJT", "group": "bjt"},
        {"id": "rl1", "type": "resistor", "label": "{sw.R_load}", "group": "bjt"},
        {"id": "led1", "type": "led", "label": "LED", "color": "red", "group": "bjt"},
        {"id": "q2", "type": "n_mosfet", "label": "N-MOSFET", "group": "mosfet"},
        {"id": "rl2", "type": "resistor", "label": "{sw.R_load}", "group": "mosfet"},
        {"id": "led2", "type": "led", "label": "LED", "color": "red", "group": "mosfet"},
    ],
    "connections": [
        {"id": "w1", "from": "bat.+", "to": "rl1.a", "color": "red"},
        {"id": "w2", "from": "rl1.b", "to": "led1.anode", "color": "orange"},
        {"id": "w3", "from": "led1.cathode", "to": "q1.C", "color": "orange"},
        {"id": "w4", "from": "q1.E", "to": "gnd.gnd", "color": "black"},
        {"id": "w5", "from": "mcu.out", "to": "rb.a", "color": "blue"},
        {"id": "w6", "from": "rb.b", "to": "q1.B", "color": "blue"},
        {"id": "w7", "from": "bat.+", "to": "rl2.a", "color": "red"},
        {"id": "w8", "from": "rl2.b", "to": "led2.anode", "color": "orange"},
        {"id": "w9", "from": "led2.cathode", "to": "q2.D", "color": "orange"},
        {"id": "w10", "from": "q2.S", "to": "gnd.gnd", "color": "black"},
        {"id": "w11", "from": "mcu.out", "to": "q2.G", "color": "blue"},
        {"id": "w12", "from": "bat.-", "to": "gnd.gnd", "color": "black"},
        {"id": "w13", "from": "mcu.gnd", "to": "gnd.gnd", "color": "black"},
    ],
    "flows": [
        {"id": "f_c", "path": ["w1", "w2", "w3", "w4"], "current_ma": "{sw.I_C_ma}", "label": "I_C = {sw.I_C}"},
        {"id": "f_b", "path": ["w5", "w6"], "current_ma": "{sw.I_B_design_ma}", "label": "I_B = {sw.I_B_design}"},
        {"id": "f_d", "path": ["w7", "w8", "w9", "w10"], "current_ma": "{sw.I_D_ma}", "label": "I_D = {sw.I_D}"},
        {"id": "f_g", "path": ["w11"], "current_ma": "{sw.I_G_ma}", "label": "I_G {sw.I_G}"},
    ],
    "labels": [
        {"id": "l_bjt", "text": "current-driven: needs base current", "near": "q1"},
        {"id": "l_mos", "text": "voltage-driven: gate draws ~no current", "near": "q2"},
    ],
    "key_points": [
        "A BJT needs a steady base current ({sw.I_B_design} here) to stay on.",
        "A MOSFET's insulated gate needs voltage, not steady current ({sw.I_G}).",
        "BJTs are cheap and robust at low voltage; MOSFETs waste less power as switches.",
    ],
    "steps": [
        {"show": ["bat", "mcu", "gnd", "w12", "w13"], "caption": "One supply and one logic pin drive two switches."},
        {"show": ["rb", "q1", "rl1", "led1", "w1", "w2", "w3", "w4", "w5", "w6", "f_c", "f_b", "l_bjt"],
         "caption": "The BJT turns on only while base current flows."},
        {"show": ["q2", "rl2", "led2", "w7", "w8", "w9", "w10", "w11", "f_d", "f_g", "l_mos"],
         "caption": "The MOSFET turns on from gate voltage alone."},
    ],
}
