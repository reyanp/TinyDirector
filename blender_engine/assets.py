"""Procedural asset meshes built with bmesh (no bpy.ops, so it is safe inside timers).

Every asset is ONE mesh object with its origin at the base (z=0). Material slot 0 is
the "main" color the plan controls; other slots are fixed accent materials
(e.g. tree trunk brown). Sizes are meters, roughly "toy diorama" scale so a mug is visible
next to a tree.
"""

from __future__ import annotations

import math

import bmesh
from mathutils import Matrix, Vector

# asset -> default main color when the plan gives none
DEFAULT_COLORS = {
    "mug": "#D95C52", "tree": "#457A5B", "rock": "#8A8580", "cube": "#5B8DEF", "sphere": "#EF8B5B",
    "cylinder": "#9B6BD6", "cone": "#E8C547", "table": "#A0703C", "chair": "#B5824A", "lamp": "#F2E3B3",
    "tent": "#E07A3F", "campfire": "#FF7A1A", "ground": "#6E8F5A",
}
# accent materials: name -> (hex, emission strength)
ACCENTS = {
    "trunk": ("#5A3B22", 0.0),
    "wood": ("#6B4426", 0.0),
    "metal": ("#3A3A3A", 0.0),
    "stone": ("#77736E", 0.0),
    "bulb": ("#FFF2C8", 4.0),
}


def _T(x=0.0, y=0.0, z=0.0):
    return Matrix.Translation(Vector((x, y, z)))


def _S(x, y, z):
    return Matrix.Diagonal(Vector((x, y, z, 1.0)))


def _faces_of(verts):
    return {f for v in verts for f in v.link_faces}


def _tag(bm, verts, slot):
    for f in _faces_of(verts):
        f.material_index = slot


def _box(bm, m, slot=0):
    r = bmesh.ops.create_cube(bm, size=1.0, matrix=m)
    _tag(bm, r["verts"], slot)


def _cyl(bm, m, r1=0.5, r2=0.5, segs=24, slot=0):
    # depth 1 centered at origin; caller's matrix positions/scales it
    r = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segs,
                              radius1=r1, radius2=r2, depth=1.0, matrix=m)
    _tag(bm, r["verts"], slot)


def _sphere(bm, m, slot=0, subdiv=3):
    r = bmesh.ops.create_icosphere(bm, subdivisions=subdiv, radius=0.5, matrix=m)
    _tag(bm, r["verts"], slot)


def _torus(bm, m, major=0.5, minor=0.1, seg_major=20, seg_minor=10, slot=0, arc=2 * math.pi):
    rings = []
    closed = abs(arc - 2 * math.pi) < 1e-6
    count = seg_major if closed else seg_major + 1
    for i in range(count):
        a = arc * i / seg_major
        center = Vector((major * math.cos(a), 0.0, major * math.sin(a)))
        out = Vector((math.cos(a), 0.0, math.sin(a)))
        ring = []
        for j in range(seg_minor):
            b = 2 * math.pi * j / seg_minor
            p = center + out * (minor * math.cos(b)) + Vector((0.0, minor * math.sin(b), 0.0))
            ring.append(bm.verts.new(m @ p))
        rings.append(ring)
    pairs = range(count if closed else count - 1)
    for i in pairs:
        a, b = rings[i], rings[(i + 1) % count]
        for j in range(seg_minor):
            k = (j + 1) % seg_minor
            f = bm.faces.new((a[j], b[j], b[k], a[k]))
            f.material_index = slot


def build(asset: str, bm, seed: int = 0) -> list[str]:
    """Fill bm with the asset's geometry. Returns accent material names for slots 1..n."""
    if asset == "cube":
        _box(bm, _T(0, 0, 0.5))
        return []
    if asset == "sphere":
        _sphere(bm, _T(0, 0, 0.5))
        return []
    if asset == "cylinder":
        _cyl(bm, _T(0, 0, 0.5))
        return []
    if asset == "cone":
        _cyl(bm, _T(0, 0, 0.5), r1=0.5, r2=0.0)
        return []
    if asset == "ground":
        r = bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=10.0)
        _tag(bm, r["verts"], 0)
        return []
    if asset == "mug":
        _cyl(bm, _T(0, 0, 0.3) @ _S(1, 1, 0.6), r1=0.28, r2=0.28, segs=32)
        # half-torus in XZ, rotated so the arc bulges outward (+X) from the body wall
        _torus(bm, _T(0.27, 0, 0.3) @ Matrix.Rotation(math.pi / 2, 4, "Y"),
               major=0.15, minor=0.04, arc=math.pi, seg_major=14)
        return []
    if asset == "tree":
        _cyl(bm, _T(0, 0, 0.45) @ _S(1, 1, 0.9), r1=0.14, r2=0.11, segs=12, slot=1)
        _cyl(bm, _T(0, 0, 1.2) @ _S(1, 1, 1.1), r1=0.75, r2=0.0, segs=16)
        _cyl(bm, _T(0, 0, 1.75) @ _S(1, 1, 0.9), r1=0.55, r2=0.0, segs=16)
        _cyl(bm, _T(0, 0, 2.2) @ _S(1, 1, 0.7), r1=0.35, r2=0.0, segs=16)
        return ["trunk"]
    if asset == "rock":
        r = bmesh.ops.create_icosphere(bm, subdivisions=2, radius=0.45, matrix=_T(0, 0, 0.25) @ _S(1.2, 1.0, 0.6))
        import random
        rnd = random.Random(seed)
        for v in r["verts"]:
            v.co *= 0.85 + 0.3 * rnd.random()
            v.co.z = max(v.co.z, 0.0)
        _tag(bm, r["verts"], 0)
        return []
    if asset == "table":
        _box(bm, _T(0, 0, 0.75) @ _S(1.6, 0.9, 0.07))
        for x in (-0.7, 0.7):
            for y in (-0.36, 0.36):
                _box(bm, _T(x, y, 0.36) @ _S(0.07, 0.07, 0.72))
        return []
    if asset == "chair":
        _box(bm, _T(0, 0, 0.45) @ _S(0.5, 0.5, 0.06))
        _box(bm, _T(0, 0.22, 0.75) @ _S(0.5, 0.06, 0.55))
        for x in (-0.21, 0.21):
            for y in (-0.21, 0.21):
                _box(bm, _T(x, y, 0.22) @ _S(0.05, 0.05, 0.44))
        return []
    if asset == "lamp":
        _cyl(bm, _T(0, 0, 0.03) @ _S(1, 1, 0.06), r1=0.2, r2=0.2, slot=1)
        _cyl(bm, _T(0, 0, 0.6) @ _S(1, 1, 1.15), r1=0.025, r2=0.025, segs=8, slot=1)
        _sphere(bm, _T(0, 0, 1.1) @ _S(0.18, 0.18, 0.18), slot=2, subdiv=2)
        _cyl(bm, _T(0, 0, 1.2) @ _S(1, 1, 0.35), r1=0.32, r2=0.16, segs=24)
        return ["metal", "bulb"]
    if asset == "tent":
        w, d, h = 1.0, 1.6, 1.1
        vs = [bm.verts.new(p) for p in ((-w, -d / 2, 0), (w, -d / 2, 0), (0, -d / 2, h),
                                        (-w, d / 2, 0), (w, d / 2, 0), (0, d / 2, h))]
        for face in ((0, 1, 2), (5, 4, 3), (0, 2, 5, 3), (1, 4, 5, 2), (0, 3, 4, 1)):
            bm.faces.new([vs[i] for i in face]).material_index = 0
        return []
    if asset == "campfire":
        for i in range(8):
            a = 2 * math.pi * i / 8
            _sphere(bm, _T(0.45 * math.cos(a), 0.45 * math.sin(a), 0.07) @ _S(0.2, 0.2, 0.15), slot=2, subdiv=1)
        for i in range(3):
            a = 2 * math.pi * i / 3
            _cyl(bm, _T(0.1 * math.cos(a), 0.1 * math.sin(a), 0.12) @ Matrix.Rotation(a, 4, "Z")
                 @ Matrix.Rotation(math.radians(70), 4, "Y") @ _S(1, 1, 0.7), r1=0.06, r2=0.06, segs=8, slot=1)
        _cyl(bm, _T(0, 0, 0.45) @ _S(1, 1, 0.7), r1=0.22, r2=0.0, segs=12)
        return ["wood", "stone"]
    raise ValueError(f"unknown asset {asset!r}")
