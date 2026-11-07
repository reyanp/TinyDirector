"""What each supported asset looks like, for the planner prompt and the offline mock.

Sizes are the measured bounding boxes of blender_engine/assets.py (width x depth x height, meters,
origin at the base center). Keep in sync if an asset's mesh changes:
  blender -b --python blender_engine/phase1_check.py   prints cabin dims; others rarely change.
"""

from __future__ import annotations

ASSET_INFO = {
    "mug": ((0.74, 0.56, 0.60), "coffee mug with handle"),
    "tree": ((1.5, 1.5, 2.55), "pine tree (cone layers on a trunk)"),
    "rock": ((1.07, 0.96, 0.56), "boulder"),
    "cube": ((1.0, 1.0, 1.0), "1 m box"),
    "sphere": ((1.0, 1.0, 1.0), "1 m ball"),
    "cylinder": ((1.0, 1.0, 1.0), "1 m cylinder"),
    "cone": ((1.0, 1.0, 1.0), "1 m cone"),
    "table": ((1.6, 0.9, 0.79), "table, top at z=0.79"),
    "chair": ((0.5, 0.5, 1.02), "chair, seat at z=0.45"),
    "lamp": ((0.64, 0.64, 1.38), "floor lamp or lantern with a glowing light; color = the light color"),
    "tent": ((2.0, 1.6, 1.1), "triangular camping tent (NOT a building)"),
    "cabin": ((3.0, 2.72, 2.93), "small log cabin with pitched roof, door, windows, chimney; use for cabin/hut/log house"),
    "campfire": ((1.08, 1.07, 0.86), "campfire: logs, stones, flame; color = the FLAME color (default orange)"),
    "ground": ((20.0, 20.0, 0.0), "flat 20 x 20 m ground plane"),
}


def footprint_radius(asset: str) -> float:
    w, d, _ = ASSET_INFO.get(asset, ((1.0, 1.0, 1.0), ""))[0]
    return max(w, d) / 2


def prompt_table() -> str:
    return "\n".join(f"- {a}: {w:g} x {d:g} x {h:g} m, {desc}" for a, ((w, d, h), desc) in ASSET_INFO.items())
