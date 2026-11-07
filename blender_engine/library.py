"""Optional asset library for the engine: swap procedural meshes for prebuilt library meshes.

Enabled when the worker runs with TD_ASSET_LIBRARY=1 (or `-- --library`) AND assets/library/index.json
exists (build it with assets/fetch.py + assets/build_library.py). Plans do NOT change: a plan still says
{"op": "add", "asset": "cabin"}; the catalog's "replaces" field maps that to e.g. td_cabin_winter.
If the library or a file is missing, the engine silently falls back to the procedural mesh.

Library meshes are appended (not linked), so every object is independent and editable. Slot 0 is the
tintable material: the plan's `color` sets its TD_Tint node (and enables TD_TintMix) instead of Base Color.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = ROOT / "assets" / "library"
_INDEX: dict | None = None


def enabled() -> bool:
    return os.environ.get("TD_ASSET_LIBRARY", "0") == "1" and (LIB_DIR / "index.json").exists()


def _index() -> dict:
    global _INDEX
    if _INDEX is None:
        idx = json.loads((LIB_DIR / "index.json").read_text())
        _INDEX = {info["replaces"]: {"id": lib_id, **info} for lib_id, info in idx.items()}
    return _INDEX


def entry_for(asset: str) -> dict | None:
    if not enabled():
        return None
    e = _index().get(asset)
    return e if e and (LIB_DIR / e["file"]).exists() else None


def instantiate(asset: str, oid: str):
    """Append the library mesh for `asset` as a new object named `oid`. Returns the object or None."""
    e = entry_for(asset)
    if e is None:
        return None
    with bpy.data.libraries.load(str(LIB_DIR / e["file"]), link=False) as (src, dst):
        dst.objects = [e["object"]]
    obj = dst.objects[0]
    if obj is None:
        return None
    obj.name = oid
    obj.data.name = f"{oid}_mesh"
    # per-instance copy of the tintable material so recoloring one cabin never recolors another
    if obj.material_slots and obj.material_slots[0].material is not None:
        mat = obj.material_slots[0].material.copy()
        mat.name = f"td_{oid}"
        obj.data.materials[0] = mat
    obj["director_library"] = e["id"]
    return obj


# ---------- tint (used by engine._set_color / _main_color) ----------

def tint_nodes(mat):
    nt = mat.node_tree if mat is not None else None
    if nt is None or "TD_Tint" not in nt.nodes:
        return None, None
    return nt.nodes["TD_Tint"], nt.nodes.get("TD_TintMix")


def set_tint(mat, linear_rgba) -> bool:
    rgb, mix = tint_nodes(mat)
    if rgb is None:
        return False
    rgb.outputs[0].default_value = linear_rgba
    if mix is not None:
        mix.inputs["Factor"].default_value = 0.85
    mat.diffuse_color = linear_rgba
    return True


def get_tint(mat):
    rgb, mix = tint_nodes(mat)
    if rgb is None:
        return None
    if mix is not None and mix.inputs["Factor"].default_value == 0.0:
        return "untinted"
    return tuple(rgb.outputs[0].default_value)
