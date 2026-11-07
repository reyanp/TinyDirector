"""Build the TinyDirector asset library: one .blend per catalog 'library' entry in assets/library/.

  python assets/fetch.py                                  # once: download CC0 sources into assets/cache/
  blender -b --factory-startup --python assets/build_library.py

Every library asset is ONE mesh object (named after its catalog id) with its origin at the base center,
material slot 0 = the plan-tintable material (it carries a "TD_Tint" RGB node + "TD_TintMix" node), all
images packed into the .blend, and a winter snow layer on upward-facing surfaces. Writes
assets/library/index.json with the measured size, triangle count and materials of each asset.
Nothing downloaded is executed: models are glTF, textures are images.
"""

from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
LIB = HERE / "library"
CATALOG = json.loads((HERE / "catalog.json").read_text())


# =============================== materials ===============================

def _tex_paths(source_id: str) -> dict:
    d = CACHE / source_id
    out = {}
    for p in d.glob("*.jpg"):
        for key, tag in (("diff", "_diff_"), ("nor", "_nor_gl_"), ("rough", "_rough_")):
            if tag in p.name:
                out[key] = str(p)
    return out


def _image(path: str, color: bool):
    img = bpy.data.images.load(path, check_existing=True)
    img.colorspace_settings.name = "sRGB" if color else "Non-Color"
    return img


def _mix_rgba(nt, blend="MIX"):
    n = nt.nodes.new("ShaderNodeMix")
    n.data_type, n.blend_type = "RGBA", blend
    a = next(s for s in n.inputs if s.name == "A" and s.type == "RGBA")
    b = next(s for s in n.inputs if s.name == "B" and s.type == "RGBA")
    out = next(s for s in n.outputs if s.type == "RGBA")
    return n, a, b, out


def add_tint(mat, color_socket_from):
    """Insert TD_Tint between a color source and Base Color. COLOR blend keeps the texture's light/dark
    detail and replaces hue/saturation; TD_TintMix factor 0 = untouched (engine sets it on recolor)."""
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    rgb = nt.nodes.new("ShaderNodeRGB")
    rgb.name = rgb.label = "TD_Tint"
    rgb.outputs[0].default_value = (1, 1, 1, 1)
    mix, a, b, out = _mix_rgba(nt, "COLOR")
    mix.name = mix.label = "TD_TintMix"
    mix.inputs["Factor"].default_value = 0.0
    if color_socket_from is not None:
        nt.links.new(color_socket_from, a)
    else:
        a.default_value = bsdf.inputs["Base Color"].default_value[:]
    nt.links.new(rgb.outputs[0], b)
    nt.links.new(out, bsdf.inputs["Base Color"])
    return mat


def snow_group():
    """Node group: Shader in -> Shader out with a snow layer where the surface faces up (world space)."""
    g = bpy.data.node_groups.get("TD_SnowCover")
    if g is not None:
        return g
    g = bpy.data.node_groups.new("TD_SnowCover", "ShaderNodeTree")
    g.interface.new_socket("Shader", in_out="INPUT", socket_type="NodeSocketShader")
    amt = g.interface.new_socket("Amount", in_out="INPUT", socket_type="NodeSocketFloat")
    amt.default_value, amt.min_value, amt.max_value = 1.0, 0.0, 1.0
    g.interface.new_socket("Shader", in_out="OUTPUT", socket_type="NodeSocketShader")
    n, L = g.nodes, g.links
    gi, go = n.new("NodeGroupInput"), n.new("NodeGroupOutput")
    geo = n.new("ShaderNodeNewGeometry")
    sep = n.new("ShaderNodeSeparateXYZ")
    L.new(geo.outputs["Normal"], sep.inputs[0])
    noise = n.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 3.5
    noise.inputs["Detail"].default_value = 4.0
    tc = n.new("ShaderNodeTexCoord")
    L.new(tc.outputs["Object"], noise.inputs["Vector"])
    add = n.new("ShaderNodeMath")
    add.operation = "ADD"
    L.new(sep.outputs["Z"], add.inputs[0])
    nm = n.new("ShaderNodeMath")
    nm.operation = "MULTIPLY_ADD"
    L.new(noise.outputs["Fac"], nm.inputs[0])
    nm.inputs[1].default_value, nm.inputs[2].default_value = 0.35, -0.175
    L.new(nm.outputs[0], add.inputs[1])
    ramp = n.new("ShaderNodeMapRange")
    ramp.inputs["From Min"].default_value, ramp.inputs["From Max"].default_value = 0.52, 0.68
    L.new(add.outputs[0], ramp.inputs["Value"])
    mul = n.new("ShaderNodeMath")
    mul.operation = "MULTIPLY"
    mul.use_clamp = True
    L.new(ramp.outputs["Result"], mul.inputs[0])
    L.new(gi.outputs["Amount"], mul.inputs[1])
    snow = n.new("ShaderNodeBsdfPrincipled")
    _snow_bsdf(snow)
    mix = n.new("ShaderNodeMixShader")
    L.new(mul.outputs[0], mix.inputs["Fac"])
    L.new(gi.outputs["Shader"], mix.inputs[1])
    L.new(snow.outputs[0], mix.inputs[2])
    L.new(mix.outputs[0], go.inputs["Shader"])
    return g


def _snow_bsdf(bsdf):
    bsdf.inputs["Base Color"].default_value = (0.86, 0.9, 0.96, 1)
    bsdf.inputs["Roughness"].default_value = 0.55
    if "Subsurface Weight" in bsdf.inputs:
        bsdf.inputs["Subsurface Weight"].default_value = 0.15
        bsdf.inputs["Subsurface Radius"].default_value = (0.9, 0.95, 1.0)
        bsdf.inputs["Subsurface Scale"].default_value = 0.05
    if "Coat Weight" in bsdf.inputs:
        bsdf.inputs["Specular IOR Level"].default_value = 0.4


def add_snow(mat, amount=1.0):
    """Wrap whatever feeds the Material Output in the TD_SnowCover group."""
    nt = mat.node_tree
    out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output)
    link = out.inputs["Surface"].links[0]
    src = link.from_socket
    nt.links.remove(link)
    grp = nt.nodes.new("ShaderNodeGroup")
    grp.node_tree = snow_group()
    grp.name = "TD_Snow"
    grp.inputs["Amount"].default_value = amount
    nt.links.new(src, grp.inputs[0])
    nt.links.new(grp.outputs[0], out.inputs["Surface"])
    return mat


def pbr(name, source_id, scale=1.0, rough_mul=1.0, tint=True, snow=1.0, hue=None):
    """Box-projected PBR material from a cached Poly Haven texture set (no UVs needed)."""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    paths = _tex_paths(source_id)
    tc, mp = nt.nodes.new("ShaderNodeTexCoord"), nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (scale, scale, scale)
    nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])

    def tex(path, color):
        t = nt.nodes.new("ShaderNodeTexImage")
        t.image = _image(path, color)
        t.projection, t.projection_blend = "BOX", 0.25
        nt.links.new(mp.outputs["Vector"], t.inputs["Vector"])
        return t
    color_out = tex(paths["diff"], True).outputs["Color"]
    if hue is not None:  # gentle stylization: push the photo texture toward the palette
        hsv = nt.nodes.new("ShaderNodeHueSaturation")
        hsv.inputs["Saturation"].default_value, hsv.inputs["Value"].default_value = hue
        nt.links.new(color_out, hsv.inputs["Color"])
        color_out = hsv.outputs["Color"]
    if "rough" in paths:
        r = tex(paths["rough"], False)
        m = nt.nodes.new("ShaderNodeMath")
        m.operation, m.use_clamp = "MULTIPLY", True
        m.inputs[1].default_value = rough_mul
        nt.links.new(r.outputs["Color"], m.inputs[0])
        nt.links.new(m.outputs[0], bsdf.inputs["Roughness"])
    # Bump from the texture itself: tangent-space normal maps need UVs, and procedural meshes have none.
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.35
    bump.inputs["Distance"].default_value = 0.02
    nt.links.new(color_out, bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    if tint:
        add_tint(mat, color_out)
    else:
        nt.links.new(color_out, bsdf.inputs["Base Color"])
    if snow:
        add_snow(mat, snow)
    return mat


def flat(name, rgb, rough=0.6, emission=0.0, tint=False, snow=0.0, noise=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    lin = tuple(c ** 2.2 for c in rgb) + (1,)
    bsdf.inputs["Base Color"].default_value = lin
    bsdf.inputs["Roughness"].default_value = rough
    mat.diffuse_color = lin
    src = None
    if noise:
        nz = nt.nodes.new("ShaderNodeTexNoise")
        nz.inputs["Scale"].default_value = 6.0
        mix, a, b, out = _mix_rgba(nt, "MULTIPLY")
        mix.inputs["Factor"].default_value = noise
        a.default_value = lin
        nt.links.new(nz.outputs["Color"], b)
        src = out
    if emission:
        bsdf.inputs["Emission Color"].default_value = lin
        bsdf.inputs["Emission Strength"].default_value = emission
    if tint:
        add_tint(mat, src)
        if emission:  # tint drives the glow color too
            nt.links.new(next(s for s in nt.nodes["TD_TintMix"].outputs if s.type == "RGBA"),
                         bsdf.inputs["Emission Color"])
    elif src is not None:
        nt.links.new(src, bsdf.inputs["Base Color"])
    if snow:
        add_snow(mat, snow)
    return mat


def flame_material():
    """Emissive flame: yellow core at the bottom fading to orange-red at the tip (object-space Z)."""
    mat = bpy.data.materials.new("td_lib_flame")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(tc.outputs["Generated"], sep.inputs[0])
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (1.0, 0.75, 0.25, 1)
    ramp.color_ramp.elements[1].color = (0.9, 0.18, 0.02, 1)
    nt.links.new(sep.outputs["Z"], ramp.inputs["Fac"])
    add_tint(mat, ramp.outputs["Color"])
    tint_out = next(s for s in nt.nodes["TD_TintMix"].outputs if s.type == "RGBA")
    nt.links.new(tint_out, bsdf.inputs["Emission Color"])
    bsdf.inputs["Emission Strength"].default_value = 3.5
    mat.diffuse_color = (1.0, 0.45, 0.1, 1)
    return mat


# =============================== geometry helpers ===============================

def _new_bm():
    return bmesh.new()


def _faces(verts):
    return {f for v in verts for f in v.link_faces}


def _tag(verts, slot):
    for f in _faces(verts):
        f.material_index = slot


def box(bm, center, size, slot, rot=None):
    m = Matrix.Translation(Vector(center)) @ (rot or Matrix()) @ Matrix.Diagonal(Vector((*size, 1)))
    r = bmesh.ops.create_cube(bm, size=1.0, matrix=m)
    _tag(r["verts"], slot)
    return r["verts"]


def log(bm, a, b, radius, slot, segs=12):
    """Cylinder from point a to point b."""
    a, b = Vector(a), Vector(b)
    d = b - a
    rot = d.to_track_quat("Z", "Y").to_matrix().to_4x4()
    m = Matrix.Translation((a + b) / 2) @ rot @ Matrix.Diagonal(Vector((1, 1, d.length, 1)))
    r = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segs, radius1=radius,
                              radius2=radius, depth=1.0, matrix=m)
    _tag(r["verts"], slot)
    return r["verts"]


def cone(bm, center, r1, r2, depth, slot, segs=16):
    m = Matrix.Translation(Vector(center))
    r = bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segs, radius1=r1, radius2=r2,
                              depth=depth, matrix=m)
    _tag(r["verts"], slot)
    return r["verts"]


def blob(bm, center, size, slot, subdiv=2, seed=0, jitter=0.12):
    rnd = random.Random(seed)
    m = Matrix.Translation(Vector(center)) @ Matrix.Diagonal(Vector((*size, 1)))
    r = bmesh.ops.create_icosphere(bm, subdivisions=subdiv, radius=0.5, matrix=m)
    for v in r["verts"]:
        v.co += (v.co - Vector(center)).normalized() * rnd.uniform(-jitter, jitter) * min(size)
    _tag(r["verts"], slot)
    return r["verts"]


def slab(bm, corners_top, thickness, slot):
    """Extrude a quad (4 top corners, CCW from above) downward by `thickness` along -Z."""
    top = [bm.verts.new(c) for c in corners_top]
    bot = [bm.verts.new(Vector(c) - Vector((0, 0, thickness))) for c in corners_top]
    faces = [top, list(reversed(bot))] + [[top[i], bot[i], bot[(i + 1) % 4], top[(i + 1) % 4]][::-1]
                                         for i in range(4)]
    for f in faces:
        bm.faces.new(f).material_index = slot
    return top + bot


def finish(bm, name, mats, smooth_slots=()):
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    mesh = bpy.data.meshes.new(name + "_mesh")
    bm.to_mesh(mesh)
    bm.free()
    for p in mesh.polygons:
        p.use_smooth = p.material_index in smooth_slots
    for m in mats:
        mesh.materials.append(m)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


# =============================== library assets ===============================

def build_cabin(spec):
    W, D, r = 3.0, 2.3, 0.11            # wall lengths (log centers), log radius
    mats = [pbr("td_lib_cabin_logs", "ph_tex_pine_bark", scale=1.6, hue=(0.85, 0.95), snow=0.9),   # 0 walls
            pbr("td_lib_cabin_planks", "ph_tex_wooden_planks", scale=1.2, tint=False, snow=0.0),  # 1 door/gable
            pbr("td_lib_cabin_roof", "ph_tex_roof_slates_02", scale=1.5, tint=False, snow=0.0),   # 2 roof
            flat("td_lib_snow", (0.93, 0.95, 0.99), rough=0.5, noise=0.08),                     # 3 snow
            flat("td_lib_window", (1.0, 0.72, 0.38), rough=0.3, emission=4.0),                  # 4 glow
            flat("td_lib_stone", (0.48, 0.47, 0.46), rough=0.85, noise=0.35, snow=1.0),         # 5 stone
            flat("td_lib_trim", (0.30, 0.20, 0.13), rough=0.7, snow=0.8)]                       # 6 dark wood
    _snow_bsdf(mats[3].node_tree.nodes["Principled BSDF"])
    bm = _new_bm()
    step = 2 * r * 0.9
    n_layers = 8
    ext = 0.22
    for i in range(n_layers):
        z = r + i * step
        zx = z + (step / 2)  # side walls offset half a log => interlocking corners
        for y in (-D / 2, D / 2):
            log(bm, (-W / 2 - ext, y, z), (W / 2 + ext, y, z), r, 0)
        for x in (-W / 2, W / 2):
            log(bm, (x, -D / 2 - ext, zx), (x, D / 2 + ext, zx), r, 0)
    H = r + (n_layers - 1) * step + step / 2 + r      # wall top
    pitch = math.radians(38)
    over_x, over_y = 0.35, 0.42
    half = D / 2 + over_y
    rise = math.tan(pitch) * half
    ridge = H + rise
    # gable triangles (planks), slightly inset
    for x in (-W / 2, W / 2):
        sgn = 1 if x > 0 else -1
        v = [bm.verts.new(p) for p in ((x, -D / 2 - r, H - 0.02), (x, D / 2 + r, H - 0.02),
                                        (x, 0, H + math.tan(pitch) * (D / 2 + r)))]
        f = bm.faces.new(v if sgn > 0 else list(reversed(v)))
        f.material_index = 1
        bmesh.ops.solidify(bm, geom=[f], thickness=0.12)
    # roof slabs: rafters along X (the ridge runs along X)
    t = 0.12
    for sgn in (-1, 1):
        y0, y1 = 0.0, sgn * half
        z0, z1 = ridge, ridge - rise
        c = [(-W / 2 - over_x, y0, z0), (W / 2 + over_x, y0, z0), (W / 2 + over_x, y1, z1), (-W / 2 - over_x, y1, z1)]
        if sgn < 0:
            c = [c[1], c[0], c[3], c[2]]
        slab(bm, c, t, 2)
        # thick rounded snow blanket on top of each roof plane
        lift = Vector((0, 0, 0.012))
        sc = [Vector(p) + lift for p in c]
        sv = slab(bm, sc, -0.16, 3)
        edges = list({e for v in sv for e in v.link_edges})
        bmesh.ops.bevel(bm, geom=edges, offset=0.07, segments=3, affect="EDGES", profile=0.7)
    # ridge snow roll
    log(bm, (-W / 2 - over_x - 0.02, 0, ridge + 0.17), (W / 2 + over_x + 0.02, 0, ridge + 0.17), 0.12, 3, segs=10)
    # door (front = -Y), frame, step
    fy = -D / 2 - r
    box(bm, (0.35, fy - 0.02, 0.62), (0.62, 0.08, 1.2), 1)
    for x in (0.35 - 0.36, 0.35 + 0.36):
        box(bm, (x, fy - 0.05, 0.65), (0.08, 0.1, 1.3), 6)
    box(bm, (0.35, fy - 0.05, 1.3), (0.8, 0.1, 0.09), 6)
    box(bm, (0.35, fy - 0.35, 0.06), (1.1, 0.55, 0.12), 1)
    box(bm, (0.35, fy - 0.35, 0.135), (1.12, 0.57, 0.03), 3)  # snow on the step
    # windows: glowing panes with cross frames and snowy sills (front + both sides)
    def window(center, normal_axis, sgn):
        cx, cy, cz = center
        if normal_axis == "y":
            box(bm, (cx, cy + sgn * 0.02, cz), (0.62, 0.06, 0.52), 4)
            box(bm, (cx, cy + sgn * 0.05, cz), (0.05, 0.06, 0.56), 6)
            box(bm, (cx, cy + sgn * 0.05, cz), (0.66, 0.06, 0.05), 6)
            box(bm, (cx, cy + sgn * 0.09, cz - 0.3), (0.78, 0.16, 0.06), 6)
            box(bm, (cx, cy + sgn * 0.09, cz - 0.25), (0.76, 0.15, 0.05), 3)
        else:
            box(bm, (cx + sgn * 0.02, cy, cz), (0.06, 0.62, 0.52), 4)
            box(bm, (cx + sgn * 0.05, cy, cz), (0.06, 0.05, 0.56), 6)
            box(bm, (cx + sgn * 0.05, cy, cz), (0.06, 0.66, 0.05), 6)
            box(bm, (cx + sgn * 0.09, cy, cz - 0.3), (0.16, 0.78, 0.06), 6)
            box(bm, (cx + sgn * 0.09, cy, cz - 0.25), (0.15, 0.76, 0.05), 3)
    window((-0.75, fy, 0.95), "y", -1)
    window((-W / 2 - r, 0.0, 0.95), "x", -1)
    window((W / 2 + r, 0.0, 0.95), "x", 1)
    # stone chimney with snow cap, poking through the back roof plane
    cx, cy = 0.9, 0.55
    ztop = ridge + 0.55
    box(bm, (cx, cy, (H + ztop) / 2 - 0.4), (0.42, 0.42, ztop - H + 0.8), 5)
    box(bm, (cx, cy, ztop + 0.04), (0.5, 0.5, 0.08), 5)
    blob(bm, (cx, cy, ztop + 0.1), (0.46, 0.46, 0.12), 3, seed=3, jitter=0.05)
    # woodpile beside the cabin (right side)
    rnd = random.Random(7)
    for row in range(3):
        for k in range(4 - row):
            z = 0.09 + row * 0.16
            y = -0.6 + (k + row * 0.5) * 0.19
            log(bm, (W / 2 + 0.45, y, z), (W / 2 + 0.95 + rnd.uniform(-0.04, 0.04), y, z), 0.085, 0, segs=8)
    blob(bm, (W / 2 + 0.7, -0.6 + 0.19 * 1.5, 0.52), (0.6, 0.5, 0.12), 3, seed=5, jitter=0.08)
    obj = finish(bm, spec["id"], mats, smooth_slots=(0, 3))
    return obj


def build_pine(spec):
    mats = [flat("td_lib_needles", (0.16, 0.36, 0.30), rough=0.75, tint=True, snow=0.0, noise=0.25),  # 0
            pbr("td_lib_pine_trunk", "ph_tex_pine_bark", scale=3.0, tint=False, snow=0.0),           # 1
            flat("td_lib_pine_snow", (0.93, 0.95, 0.99), rough=0.5, noise=0.06)]                    # 2
    _snow_bsdf(mats[2].node_tree.nodes["Principled BSDF"])
    bm = _new_bm()
    cone(bm, (0, 0, 0.35), 0.13, 0.09, 0.7, 1, segs=10)
    tiers = [(0.45, 0.78, 0.85), (0.95, 0.64, 0.75), (1.4, 0.5, 0.66), (1.8, 0.36, 0.56), (2.12, 0.22, 0.44)]
    rnd = random.Random(11)
    for ti, (z, rad, h) in enumerate(tiers):
        n = 14
        ring = []
        for i in range(2 * n):
            a = math.pi * i / n + ti * 0.3
            rr = rad * (1.0 if i % 2 == 0 else 0.72) * rnd.uniform(0.93, 1.05)
            droop = -0.09 if i % 2 == 0 else 0.0
            ring.append(bm.verts.new((rr * math.cos(a), rr * math.sin(a), z + droop)))
        apex = bm.verts.new((0, 0, z + h))
        under = bm.verts.new((0, 0, z + 0.08))
        for i in range(2 * n):
            a, b = ring[i], ring[(i + 1) % (2 * n)]
            bm.faces.new((a, b, apex)).material_index = 0
            bm.faces.new((b, a, under)).material_index = 0
        # snow cap: a thin shell over the top ~45% of the tier, with a scalloped lower edge
        cap_ring = []
        for i in range(2 * n):
            co = ring[i].co
            # upper part of each tier is hidden inside the tier above, so the cap reaches down the skirt
            f = rnd.uniform(0.72, 0.84) if i % 2 == 0 else rnd.uniform(0.55, 0.66)
            p = Vector((co.x * f * 1.04, co.y * f * 1.04, z + (h * (1 - f)) + 0.025))
            cap_ring.append(bm.verts.new(p))
        cap_apex = bm.verts.new((0, 0, z + h + 0.03))
        for i in range(2 * n):
            a, b = cap_ring[i], cap_ring[(i + 1) % (2 * n)]
            bm.faces.new((a, b, cap_apex)).material_index = 2
    obj = finish(bm, spec["id"], mats, smooth_slots=())
    return obj


def import_gltf(source_id):
    before = set(bpy.data.objects)
    gl = next((CACHE / source_id).glob("*.gltf"))
    bpy.ops.import_scene.gltf(filepath=str(gl))
    objs = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    for o in bpy.data.objects:
        if o not in before and o.type != "MESH":
            bpy.data.objects.remove(o, do_unlink=True)
    return objs


def join(objs, name):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    if len(objs) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    obj.name = name
    obj.data.name = name + "_mesh"
    return obj


def tint_imported(mat):
    """glTF materials: put TD_Tint between the base-color texture and the BSDF."""
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.name = "Principled BSDF"
    links = bsdf.inputs["Base Color"].links
    src = links[0].from_socket if links else None
    if links:
        nt.links.remove(links[0])
    add_tint(mat, src)


def slot_first(obj, mat_name):
    mats = [s.material for s in obj.material_slots]
    i = next(k for k, m in enumerate(mats) if m.name == mat_name)
    if i == 0:
        return
    order = [i] + [k for k in range(len(mats)) if k != i]
    remap = {old: new for new, old in enumerate(order)}
    new_idx = [remap[p.material_index] for p in obj.data.polygons]
    obj.data.materials.clear()          # NOTE: this resets every face to slot 0 ...
    for k in order:
        obj.data.materials.append(mats[k])
    for p, k in zip(obj.data.polygons, new_idx):  # ... so assign the remapped slots afterwards
        p.material_index = k


def base_to_origin(obj, sink=0.0):
    """Origin at the bottom-center of the bounding box (optionally sunk below z=0)."""
    pts = [Vector(v.co) for v in obj.data.vertices]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    off = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z + sink))
    obj.data.transform(Matrix.Translation(-off))


def build_fire_pit(spec):
    pit = import_gltf("ph_model_stone_fire_pit")
    pit = join(pit, "pit_tmp")
    base_to_origin(pit)
    for m in pit.data.materials:
        add_snow(m, 0.35)
    flame = flame_material()
    logs_mat = pbr("td_lib_fire_logs", "ph_tex_pine_bark", scale=4.0, tint=False, snow=0.0)
    ember = flat("td_lib_embers", (1.0, 0.35, 0.05), rough=0.5, emission=2.5)
    bm = _new_bm()
    for k in range(4):
        a = math.radians(45 + 90 * k)
        log(bm, (0.08 * math.cos(a), 0.08 * math.sin(a), 0.42), (0.45 * math.cos(a), 0.45 * math.sin(a), 0.14),
            0.05, 1, segs=8)
    for k, (dx, dy, s) in enumerate(((0, 0, 1.0), (0.09, 0.05, 0.7), (-0.08, -0.04, 0.65), (0.03, -0.09, 0.55))):
        verts = blob(bm, (dx, dy, 0.32 + 0.22 * s), (0.22 * s, 0.22 * s, 0.5 * s), 0, subdiv=3, seed=20 + k,
                     jitter=0.05)
        top = max(v.co.z for v in verts)
        bottom = min(v.co.z for v in verts)
        for v in verts:  # teardrop: pinch toward the top
            f = (v.co.z - bottom) / (top - bottom)
            c = Vector((dx, dy, v.co.z))
            v.co = c + (v.co - c) * (1.0 - 0.85 * f ** 1.4)
    for k in range(9):
        a = k * 2.399
        blob(bm, (0.2 * math.cos(a) * (k / 9), 0.2 * math.sin(a) * (k / 9), 0.18), (0.06, 0.06, 0.04), 2,
             subdiv=1, seed=40 + k)
    extra = finish(bm, "fire_tmp", [flame, logs_mat, ember], smooth_slots=(0,))
    obj = join([pit, extra], spec["id"])
    slot_first(obj, "td_lib_flame")
    return obj


def build_stump(spec):
    stump = join(import_gltf("ph_model_tree_stump_01"), "stump_tmp")
    axe = join(import_gltf("ph_model_wooden_axe"), "axe_tmp")
    top = max(v.co.z for v in stump.data.vertices)
    axe.data.transform(Matrix.Translation((0.1, 0.05, top + 0.12)) @ Matrix.Rotation(math.radians(-18), 4, "X")
                       @ Matrix.Rotation(math.radians(90), 4, "Z"))
    for o in (stump, axe):
        for m in o.data.materials:
            add_snow(m, 0.2 if o is stump else 0.1)
    tint_imported(stump.data.materials[0])
    obj = join([stump, axe], spec["id"])
    slot_first(obj, stump.data.materials[0].name if stump.name in bpy.data.objects else "tree_stump_01")
    return obj


def build_lantern(spec):
    obj = join(import_gltf("ph_model_wooden_lantern_01"), spec["id"])
    base_to_origin(obj)
    glass = next(m for m in obj.data.materials if "glass" in m.name)
    nt = glass.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.name = "Principled BSDF"
    for l in list(bsdf.inputs["Base Color"].links):
        nt.links.remove(l)
    bsdf.inputs["Base Color"].default_value = (1.0, 0.62, 0.25, 1)
    add_tint(glass, None)
    tint_out = next(s for s in nt.nodes["TD_TintMix"].outputs if s.type == "RGBA")
    nt.links.new(tint_out, bsdf.inputs["Emission Color"])
    bsdf.inputs["Emission Strength"].default_value = 2.0
    if "Transmission Weight" in bsdf.inputs:
        bsdf.inputs["Transmission Weight"].default_value = 0.0
    nt.nodes["TD_Tint"].outputs[0].default_value = (1, 1, 1, 1)
    for m in obj.data.materials:
        if m is not glass:
            add_snow(m, 0.3)
    slot_first(obj, glass.name)
    return obj


BUILDERS = {"td_cabin_winter": build_cabin, "td_pine_snowy": build_pine, "ph_fire_pit_lit": build_fire_pit,
            "ph_tree_stump": build_stump, "ph_lantern": build_lantern}


def fit_footprint(obj, target):
    """Uniform scale so the asset's footprint (max of width/depth) matches the catalog, then apply."""
    d = obj.dimensions
    s = max(target[0], target[1]) / max(d.x, d.y)
    obj.data.transform(Matrix.Diagonal(Vector((s, s, s, 1))))
    obj.data.update()
    return s


def main():
    only = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    LIB.mkdir(parents=True, exist_ok=True)
    index = {}
    for spec in CATALOG["library"]:
        if only and spec["id"] not in only:
            continue
        bpy.ops.wm.read_factory_settings(use_empty=True)
        obj = BUILDERS[spec["id"]](spec)
        for o in list(bpy.data.objects):
            if o is not obj:
                bpy.data.objects.remove(o, do_unlink=True)
        if spec["id"] in ("td_cabin_winter", "td_pine_snowy"):
            base_to_origin(obj)
        s = 1.0 if spec.get("fit") == "native" else fit_footprint(obj, spec["footprint_m"])
        obj["td_library_id"] = spec["id"]
        mesh = obj.data
        mesh.calc_loop_triangles()
        bpy.ops.outliner.orphans_purge(do_recursive=True)
        bpy.ops.file.pack_all()
        out = LIB / f"{spec['id']}.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(out), compress=True)
        d = obj.dimensions
        index[spec["id"]] = {"file": out.name, "object": obj.name, "replaces": spec["replaces"],
                             "size_m": [round(d.x, 3), round(d.y, 3), round(d.z, 3)], "fit_scale": round(s, 4),
                             "triangles": len(mesh.loop_triangles),
                             "materials": [m.name for m in mesh.materials],
                             "tint_slot0": "TD_Tint" in mesh.materials[0].node_tree.nodes,
                             "bytes": out.stat().st_size}
        print(f"BUILT {spec['id']}: {index[spec['id']]}")
    old = json.loads((LIB / "index.json").read_text()) if (LIB / "index.json").exists() and only else {}
    (LIB / "index.json").write_text(json.dumps({**old, **index}, indent=2))


main()
