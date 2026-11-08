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

    def _direction(self) -> dict:
        d = self.state.setdefault("direction", {"focus": None, "caption": None})
        return {"focus": d["focus"], "camera": None, "moving": False,
                "halo": d["focus"] if d["focus"] in ("battery", "resistor", "led") else None,
                "caption": d["caption"], "targets": list(DIRECT_TARGETS), "executor": "simulated"}

    def inspect(self) -> dict:
        out = {**self._result("inspect_sim"), "kind": "inspect"}
        if (self.state["circuit"] or {}).get("present"):
            out["direction"] = self._direction()
        return out

    def _direct(self, req: dict) -> dict:
        """SIMULATED stage direction (blender_engine/direction.py): records focus + caption, moves no camera."""
        rid = req["request_id"]
        if not (self.state["circuit"] or {}).get("present"):
            return self._result(rid, "error", 0, ["nothing on stage to direct"])
        focus = req.get("focus")
        if focus is not None and focus not in DIRECT_TARGETS:
            return self._result(rid, "error", 0, [f"unknown focus target {focus!r}"])
        d = self.state.setdefault("direction", {"focus": None, "caption": None})
        if focus is not None:
            d["focus"] = focus  # like Blender: "overview" is remembered as the focus
        if req.get("caption") is not None:
            d["caption"] = req["caption"]
        self._save()
        return {**self._result(rid, "ok", 0), "direction": self._direction()}

    def send(self, req: dict, timeout: float | None = None) -> dict:  # timeout: bridge parity, unused
        if req.get("kind") == "direct":
            return self._direct(req)
        rid = req["request_id"]
        circ = self.state["circuit"]
        if req["action"] == "update" and not (circ and circ.get("present")):
            return self._result(rid, "error", 0, ["no lesson circuit in the scene to update; build it first"])
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
