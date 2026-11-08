"""SIMULATED stand-in for blender_engine/circuit.py (offline tests and `--simulate`). NOT Blender.

It mirrors the engine's read-back shape from the request so the tutor loop, checks and CLI can be
tested without Blender. Results carry "executor": "simulated" and are labeled SIMULATED in output.
"""

from __future__ import annotations

import copy
import json
import secrets
from pathlib import Path

from .display import COMPONENTS, LABELS

POSITIONS = {"board_1": [0.1, 0, 0], "battery_1": [-2.75, 0, 0], "resistor_1": [0, 1.3, 0.42],
             "led_1": [2.7, 0, 0], "wire_1": [0, 0, 0], "wire_2": [0, 0, 0], "wire_3": [0, 0, 0],
             "flow_1": [0, 0, 0]}
DIRECT_TARGETS = ("battery", "resistor", "led", "current", "overview")
COMPARE_TARGETS = ("compare", "baseline", "alternative")
WORLD_LABELS = ("label_battery", "label_resistor", "label_led", "label_current")
SCAM_TARGETS = ("phone", "message", "sender", "brand", "body", "urgency", "link")
SCAM_OUTCOME_TARGETS = ("outcomes", "outcome_a", "outcome_b")
SCAM_SIDE_X = {"follow_link": -5.6, "verify_official": 7.2}


class SimulatedCircuitExecutor:
    """inspect()/send() like the bridge, applied to an in-memory (optionally file-backed) scene."""

    label = "SIMULATED circuit engine (not Blender)"

    def __init__(self, state_path: Path | None = None, v1_objects: list | None = None):
        self.state_path = state_path
        self.state = {"scene_version": 0, "circuit": None, "scene_objects": v1_objects or []}
        if state_path and Path(state_path).exists():
            self.state = json.loads(Path(state_path).read_text(encoding="utf-8"))

    def _save(self) -> None:
        if self.state_path:
            Path(self.state_path).parent.mkdir(parents=True, exist_ok=True)
            Path(self.state_path).write_text(json.dumps(self.state, indent=1), encoding="utf-8")

    def _result(self, rid: str, status="ok", applied=0, errors=None) -> dict:
        return {"schema_version": 1, "request_id": rid, "status": status, "applied": applied,
                "errors": errors or [], "scene_version": self.state["scene_version"],
                "scene_objects": copy.deepcopy(self.state["scene_objects"]), "executor": "simulated",
                "circuit": copy.deepcopy(self.state["circuit"])}

    def _targets(self) -> list[str]:
        ex = self.state.get("explainer")
        if ex:
            return ["overview", "scene"] + [e["id"] for e in ex["spec"]["elements"]]
        sc = self.state.get("scam")
        if sc:
            return ["overview"] + list(SCAM_TARGETS) + (list(SCAM_OUTCOME_TARGETS) if sc["outcomes"]["active"] else [])
        return list(DIRECT_TARGETS) + (list(COMPARE_TARGETS) if self.state.get("compare") else [])

    def _direction(self) -> dict:
        d = self.state.setdefault("direction", {"focus": None, "caption": None})
        return {"focus": d["focus"], "camera": None, "moving": False,
                "halo": d["focus"] if d["focus"] in ("battery", "resistor", "led") else None,
                "caption": d["caption"], "callout": d.get("callout"), "flow_emphasis": d["focus"] == "current",
                "targets": self._targets(), "executor": "simulated"}

    def _compare(self) -> dict:
        c = self.state.get("compare")
        if not c:
            return {"active": False}
        d = c["alternative"]["display"]
        return {"active": True, "executor": "simulated", "objects": 22,
                "alternative": {"params": dict(c["alternative"]["circuit"]),
                                "labels": {k: d["labels"][k] for k in WORLD_LABELS},
                                "flow": {"lap_frames": d["flow_lap_frames"], "markers": d["flow_markers"],
                                         "power": 1.0 if d["flow_lap_frames"] else 0.0},
                                "led": {"emission": d["led_emission"], "light_w": d["led_light_w"],
                                        "status": d["led_status"]},
                                "resistor_bands": [b.upper() for b in d["resistor_bands"]]},
                "headlines": dict(c["headlines"]), "title": c["title"], "legend": c["legend"]}

    def inspect(self) -> dict:
        out = {**self._result("inspect_sim"), "kind": "inspect"}
        if (self.state["circuit"] or {}).get("present"):
            out["direction"] = self._direction()
        if self.state.get("compare"):
            out["compare"] = self._compare()
        if self.state.get("scam"):
            out["scam"] = copy.deepcopy(self.state["scam"])
            out["direction"] = self._direction()
        if self.state.get("explainer"):
            out["explainer"] = copy.deepcopy(self.state["explainer"])
            out["direction"] = self._direction()
        return out

    def _explainer_request(self, req: dict) -> dict:
        """SIMULATED OVERRIDE explainer scene (blender_engine/explainer.py): records what Blender would draw (labels,
        arrows, keyed motion) from the request; draws nothing and measures no layout."""
        rid = req["request_id"]
        if req["action"] == "clear":
            self.state.pop("explainer", None)
        else:
            sp = req["spec"]
            self.state.update(circuit=None)
            self.state.pop("compare", None)
            self.state.pop("scam", None)
            self.state["explainer"] = {
                "present": True, "title": sp["title"], "banner": req["banner"], "legend": req["legend"],
                "layout": sp["layout"], "spec": copy.deepcopy(sp), "overlaps": [], "highlight": None,
                "elements": {e["id"]: {"shape": e["shape"], "label": e["label"], "center": [0, 0, 0],
                                       "animated": e.get("motion", "none") != "none", "box": [0, 0, 1, 1],
                                       "in_frame": True} for e in sp["elements"]},
                "links": [{"from": ln["from"], "to": ln["to"], "label": ln.get("label", ""), "drawn": True}
                          for ln in sp["links"]]}
            self.state["direction"] = {"focus": None, "caption": None}
        self.state["scene_version"] += 1
        self._save()
        return {**self._result(rid, "ok", 1), "explainer": copy.deepcopy(self.state.get("explainer")
                                                                         or {"present": False}),
                "direction": self._direction()}

    def _scam_request(self, req: dict) -> dict:
        """SIMULATED Spot the Scam scene (blender_engine/scam.py): records what Blender would draw; draws nothing."""
        rid = req["request_id"]
        sc = self.state.get("scam")
        d = self.state.setdefault("direction", {"focus": None, "caption": None})
        a = req["action"]
        if a != "build" and not sc:
            return {**self._result(rid, "error", 0, ["no Spot the Scam phone on stage; build it first"]),
                    "scam": {"present": False}}
        if a == "build":
            m = req["message"]
            self.state.update(circuit=None, compare=None)
            self.state.pop("compare", None)
            sc = {"present": True, "scenario": req["scenario"], "title": req["title"], "banner": req["banner"],
                  "sender": m["sender"], "segments": {s["id"]: s["text"] for s in m["segments"]}, "tags": {},
                  "highlight": None, "objects": [{"id": i, "uid": secrets.token_hex(4)} for i in
                                                 ("phone_1", "sender", *[s["id"] for s in m["segments"]],
                                                  "hud_title", "hud_banner", "hud_caption", "hud_legend")],
                  "outcomes": {"active": False}}
            self.state["direction"] = {"focus": None, "caption": None}
        elif a == "mark":
            sc["tags"] = dict(req["tags"])
        elif a == "outcomes":
            o = req["outcomes"]
            side = {}
            for choice, x in SCAM_SIDE_X.items():
                texts = [v if isinstance(v, str) else " ".join(v) for v in o[choice].values()]
                side[choice] = {"present": True, "texts": sorted(t.replace("\n", " ") for t in texts), "center_x": x}
            sc["outcomes"] = {"active": True, "choice": req["choice"], "marked": [req["choice"]],
                              "headlines": {k: o[k]["headline"] for k in o}, **side}
            d.update(focus="outcomes", callout=None)
        else:
            sc["outcomes"] = {"active": False}
            d.update(focus="overview", callout=None)
        self.state["scam"] = sc
        self.state["scene_version"] += 1
        self._save()
        return {**self._result(rid, "ok", 1), "scam": copy.deepcopy(sc), "direction": self._direction()}

    def _compare_request(self, req: dict) -> dict:
        """SIMULATED split-reality comparison (blender_engine/compare.py): records the what-if copy; draws nothing."""
        rid = req["request_id"]
        circ = self.state["circuit"] or {}
        d = self.state.setdefault("direction", {"focus": None, "caption": None})
        if req["action"] == "clear":
            if self.state.pop("compare", None) is not None:
                d.update(focus="overview", callout=None)
        else:
            if not circ.get("present"):
                return {**self._result(rid, "error", 0, ["no LED circuit on stage to compare; build the lesson first"]),
                        "compare": self._compare()}
            if req["baseline"]["circuit"] != circ["params"]:
                return {**self._result(rid, "error", 0, ["baseline does not match the circuit on stage"]),
                        "compare": self._compare()}
            self.state["compare"] = copy.deepcopy(req)
            d.update(focus="compare", callout=None)
        self.state["scene_version"] += 1
        self._save()
        return {**self._result(rid, "ok", 1), "compare": self._compare(), "direction": self._direction()}

    def _direct(self, req: dict) -> dict:
        """SIMULATED stage direction (blender_engine/direction.py): records focus + caption, moves no camera."""
        rid = req["request_id"]
        if not (self.state["circuit"] or {}).get("present") and not self.state.get("scam") \
                and not self.state.get("explainer"):
            return self._result(rid, "error", 0, ["nothing on stage to direct"])
        focus = req.get("focus")
        if focus is not None and focus not in self._targets():
            return self._result(rid, "error", 0, [f"unknown focus target {focus!r}"])
        d = self.state.setdefault("direction", {"focus": None, "caption": None})
        if focus is not None:
            d["focus"] = focus  # like Blender: "overview" is remembered as the focus
        if req.get("caption") is not None:
            d["caption"] = req["caption"]
        if focus is not None or "callout" in req:  # like Blender: a new focus without a callout removes it
            d["callout"] = req.get("callout")
        self._save()
        return {**self._result(rid, "ok", 0), "direction": self._direction()}

    def send(self, req: dict, timeout: float | None = None) -> dict:  # timeout: bridge parity, unused
        if req.get("kind") == "direct":
            return self._direct(req)
        if req.get("kind") == "compare":
            return self._compare_request(req)
        if req.get("kind") == "scam":
            if req.get("action") == "build":
                self.state.pop("explainer", None)  # like the worker: one lesson on stage at a time
            return self._scam_request(req)
        if req.get("kind") == "explainer":
            return self._explainer_request(req)
        rid = req["request_id"]
        circ = self.state["circuit"]
        if req["action"] == "update" and not (circ and circ.get("present")):
            return self._result(rid, "error", 0, ["no lesson circuit in the scene to update; build it first"])
        self.state.pop("compare", None)  # like the worker: a circuit request ends a comparison first
        if req["action"] == "build":
            self.state.pop("scam", None)  # like Blender: building the circuit clears the Spot the Scam phone
            self.state.pop("explainer", None)  # ... and a generated OVERRIDE lesson
        if req["action"] == "build":
            self.state["direction"] = {"focus": None, "caption": None}
        if req["action"] == "build" or circ is None:
            circ = {"present": True, "objects": [
                {"id": i, "uid": secrets.token_hex(4), "type": "FONT" if i.startswith("label_") else "MESH",
                 "position": POSITIONS.get(i, [0, 0, 0]), "parent": None} for i in COMPONENTS + LABELS]}
        d = req["display"]
        lap = d["flow_lap_frames"]
        circ.update({
            "params": dict(req["circuit"]),
            "labels": dict(d["labels"]),
            "flow": {"lap_frames": lap, "markers": d["flow_markers"], "power_at_show": 1.0 if lap else 0.0,
                     "power_at_start": 0.0},
            "led": {"emission_at_show": d["led_emission"], "light_w_at_show": d["led_light_w"],
                    "status": d["led_status"]},
            "resistor_bands": [b.upper() for b in d["resistor_bands"]],
        })
        self.state["circuit"] = circ
        self.state["scene_version"] += 1
        self._save()
        return self._result(rid, "ok", 1)
