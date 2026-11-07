"""Visual-quality experiment: a stylized winter diorama, built in a NEW scene (never the demo scene).

  blender -b --factory-startup --python experiments/winter_diorama.py -- \
      --variant library|procedural [--look polished|pipeline] [--samples 96] [--out experiments/out/NAME]

Objects are placed through the REAL engine with ordinary scene_plan_v1 operations (the same path a
Nemotron plan takes), so the result is identical to what the director would produce:
  --variant library     TD_ASSET_LIBRARY=1 (cabin/tree/campfire/rock/lamp -> assets/library meshes)
  --variant procedural  today's bmesh assets
  --look polished       diorama base + set dressing, HDRI dusk lighting, warm practical lights, DOF, Cycles
  --look pipeline       what the pipeline renders today (worker snapshot: Workbench, auto camera)
Writes NAME.png (NAME.jpg for --look pipeline) and NAME.blend (images packed).
"""

from __future__ import annotations

import math
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def arg(name, default):
    return argv[argv.index(name) + 1] if name in argv else default


VARIANT = arg("--variant", "library")
LOOK = arg("--look", "polished")
SAMPLES = int(arg("--samples", "96"))
PCT = int(arg("--pct", "100"))  # resolution percentage, for quick composition tests
OUT = Path(arg("--out", str(ROOT / "experiments" / "out" / f"diorama_{VARIANT}_{LOOK}")))
os.environ["TD_ASSET_LIBRARY"] = "1" if VARIANT == "library" else "0"

import bmesh  # noqa: E402
import bpy  # noqa: E402
from mathutils import Matrix, Vector, noise  # noqa: E402

from blender_engine import engine, snapshot  # noqa: E402

# ---------------- fresh, separate scene ----------------
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
OUT.parent.mkdir(parents=True, exist_ok=True)

# ---------------- objects via the real engine (same ops Nemotron emits) ----------------
PLAN = [
    {"op": "add", "id": "cabin_1", "asset": "cabin", "position": [0.2, 0.9, 0], "rotation": [0, 0, -8]},
    {"op": "add", "id": "campfire_1", "asset": "campfire", "position": [1.7, -1.7, 0]},
    {"op": "add", "id": "rock_1", "asset": "rock", "position": [-1.9, -1.3, 0], "rotation": [0, 0, 35]},
    {"op": "add", "id": "lamp_1", "asset": "lamp", "position": [-0.65, -0.55, 0]},
    {"op": "add", "id": "tree_1", "asset": "tree", "position": [-2.7, 1.8, 0], "scale": 1.55, "rotation": [0, 0, 20]},
    {"op": "add", "id": "tree_2", "asset": "tree", "position": [-3.4, 0.0, 0], "scale": 1.15, "rotation": [0, 0, 70]},
    {"op": "add", "id": "tree_3", "asset": "tree", "position": [3.1, 2.3, 0], "scale": 1.7, "rotation": [0, 0, 140]},
    {"op": "add", "id": "tree_4", "asset": "tree", "position": [3.9, -0.3, 0], "scale": 1.0, "rotation": [0, 0, 200]},
    {"op": "add", "id": "tree_5", "asset": "tree", "position": [-1.0, 3.5, 0], "scale": 1.9, "rotation": [0, 0, 260]},
    {"op": "add", "id": "tree_6", "asset": "tree", "position": [1.6, 3.7, 0], "scale": 1.35, "rotation": [0, 0, 310]},
    {"op": "add", "id": "tree_7", "asset": "tree", "position": [-3.0, -2.5, 0], "scale": 0.75, "rotation": [0, 0, 15]},
]
if VARIANT == "procedural":  # procedural lamp is a 1.4 m floor lamp; keep it off the cabin wall
    PLAN[3]["position"] = [-1.1, -0.9, 0]
result = engine.make_result("diorama_build", {"schema_version": 1, "request_id": "diorama_build", "mode": "create",
                                              "summary": "winter diorama", "operations": PLAN})
assert result["status"] == "ok", result["errors"]
print("ENGINE", result["status"], [(o["id"], o.get("library", "procedural")) for o in result["scene_objects"]])
objs = engine.director_objects()


def link(obj):
    scene.collection.objects.link(obj)
    return obj


def mat(name, rgb, rough=0.6, emission=0.0, sss=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    lin = tuple(c ** 2.2 for c in rgb) + (1,)
    b.inputs["Base Color"].default_value = lin
    b.inputs["Roughness"].default_value = rough
    m.diffuse_color = lin
    if emission:
        b.inputs["Emission Color"].default_value = lin
        b.inputs["Emission Strength"].default_value = emission
    if sss and "Subsurface Weight" in b.inputs:
        b.inputs["Subsurface Weight"].default_value = sss
        b.inputs["Subsurface Radius"].default_value = (0.9, 0.95, 1.0)
        b.inputs["Subsurface Scale"].default_value = 0.05
    return m


def snow_textured(name):
    """Ground snow from the CC0 snow_02 set (box projection, bump) - falls back to flat white."""
    m = mat(name, (0.9, 0.93, 0.98), rough=0.55, sss=0.12)
    tex = sorted((ROOT / "assets" / "cache" / "ph_tex_snow_02").glob("*diff*.jpg"))
    if not tex:
        return m
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    tc, mp, t = nt.nodes.new("ShaderNodeTexCoord"), nt.nodes.new("ShaderNodeMapping"), nt.nodes.new("ShaderNodeTexImage")
    mp.inputs["Scale"].default_value = (0.6, 0.6, 0.6)
    t.image = bpy.data.images.load(str(tex[0]))
    t.projection = "BOX"
    nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
    nt.links.new(mp.outputs["Vector"], t.inputs["Vector"])
    hsv = nt.nodes.new("ShaderNodeHueSaturation")
    hsv.inputs["Saturation"].default_value, hsv.inputs["Value"].default_value = 0.2, 1.12
    nt.links.new(t.outputs["Color"], hsv.inputs["Color"])
    soft = nt.nodes.new("ShaderNodeMix")  # mostly clean snow; the photo texture only adds subtle variation
    soft.data_type = "RGBA"
    soft.inputs["Factor"].default_value = 0.3
    next(i for i in soft.inputs if i.name == "A" and i.type == "RGBA").default_value = (0.82, 0.86, 0.93, 1)
    nt.links.new(hsv.outputs["Color"], next(i for i in soft.inputs if i.name == "B" and i.type == "RGBA"))
    nt.links.new(next(o for o in soft.outputs if o.type == "RGBA"), b.inputs["Base Color"])
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.15
    nt.links.new(t.outputs["Color"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], b.inputs["Normal"])
    return m


def build_base():
    """Round diorama island: drifted snow top, cut-away earth and rock strata on the sides."""
    R, depth = 5.0, 1.1
    bm = bmesh.new()
    rings, segs = 26, 96
    rnd = random.Random(4)
    top = []
    centre = bm.verts.new((0, 0, 0))
    for i in range(1, rings + 1):
        r_frac = i / rings
        ring = []
        for j in range(segs):
            a = 2 * math.pi * j / segs
            edge = 1.0 + 0.05 * math.sin(a * 5 + 1.3) + 0.03 * math.sin(a * 11)
            r = R * r_frac * edge
            x, y = r * math.cos(a), r * math.sin(a)
            drift = 0.12 * noise.noise(Vector((x * 0.35, y * 0.35, 0.5))) * (r_frac ** 2)
            rim = -0.18 * max(0.0, r_frac - 0.85) / 0.15  # soft rounded rim
            ring.append(bm.verts.new((x, y, drift + rim)))
        top.append(ring)
    for j in range(segs):
        bm.faces.new((centre, top[0][j], top[0][(j + 1) % segs]))
    for i in range(rings - 1):
        for j in range(segs):
            k = (j + 1) % segs
            bm.faces.new((top[i][j], top[i + 1][j], top[i + 1][k], top[i][k]))
    # side strata
    prev = top[-1]
    layers = [(-0.12, 0), (-0.35, 1), (-0.75, 2), (-depth, 3)]
    for z, slot in layers:
        ring = [bm.verts.new((v.co.x * (1 - 0.015 * rnd.random()), v.co.y * (1 - 0.015 * rnd.random()),
                              z + 0.04 * noise.noise(v.co * 1.7))) for v in prev]
        for j in range(segs):
            k = (j + 1) % segs
            f = bm.faces.new((prev[j], ring[j], ring[k], prev[k]))
            f.material_index = slot
        prev = ring
    bm.faces.new(list(reversed(prev))).material_index = 3
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    me = bpy.data.meshes.new("diorama_base")
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    for m in (snow_textured("dio_snow"), mat("dio_soil", (0.28, 0.19, 0.13), 0.9),
              mat("dio_earth", (0.38, 0.27, 0.18), 0.95), mat("dio_rock", (0.36, 0.36, 0.38), 0.85)):
        me.materials.append(m)
    return link(bpy.data.objects.new("TD_DioramaBase", me))


def scatter_dressing():
    """Small touches: log benches by the fire, pebbles, falling snow."""
    bm = bmesh.new()
    rnd = random.Random(9)
    for x, y, s in ():  # drifts read as flat plates at this camera angle; left out (kept as a hook)
        r = bmesh.ops.create_icosphere(bm, subdivisions=3, radius=1.0)
        for v in r["verts"]:
            v.co = Vector((x + v.co.x * s * 1.5, y + v.co.y * s, max(0.0, v.co.z) ** 3.5 * s * 0.22 - 0.01))
    me = bpy.data.meshes.new("mounds")
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    me.materials.append(bpy.data.materials["dio_snow"])
    link(bpy.data.objects.new("TD_SnowMounds", me))

    bark = mat("dio_bench_bark", (0.36, 0.24, 0.16), 0.8)
    bm = bmesh.new()
    for (cx, cy, ang) in ((2.75, -2.45, 120), (0.75, -2.55, 60)):  # tangent to the fire
        a = math.radians(ang)
        d = Vector((math.cos(a), math.sin(a), 0))
        bmesh.ops.create_cone(bm, cap_ends=True, segments=14, radius1=0.13, radius2=0.13, depth=0.95,
                              matrix=Matrix.Translation((cx, cy, 0.12)) @ d.to_track_quat("Z", "Y").to_matrix().to_4x4())
    me = bpy.data.meshes.new("benches")
    bm.to_mesh(me)
    bm.free()
    me.materials.append(bark)
    link(bpy.data.objects.new("TD_LogBenches", me))

    bm = bmesh.new()
    for _ in range(26):
        a, r = rnd.uniform(0, 2 * math.pi), rnd.uniform(1.0, 4.6)
        x, y = r * math.cos(a), r * math.sin(a)
        if (x - 0.2) ** 2 / 2.6 + (y - 0.9) ** 2 / 1.8 < 1.4:
            continue
        bmesh.ops.create_icosphere(bm, subdivisions=1, radius=rnd.uniform(0.05, 0.13),
                                   matrix=Matrix.Translation((x, y, 0.02)))
    me = bpy.data.meshes.new("pebbles")
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mat("dio_pebble", (0.42, 0.42, 0.45), 0.8))
    link(bpy.data.objects.new("TD_Pebbles", me))

    bm = bmesh.new()
    for _ in range(260):
        p = (rnd.uniform(-5.5, 5.5), rnd.uniform(-5.5, 5.5), rnd.uniform(0.3, 5.5))
        bmesh.ops.create_icosphere(bm, subdivisions=1, radius=rnd.uniform(0.007, 0.016),
                                   matrix=Matrix.Translation(p))
    me = bpy.data.meshes.new("snowfall")
    bm.to_mesh(me)
    bm.free()
    me.materials.append(mat("dio_flake", (1, 1, 1), 0.4, emission=0.4))
    link(bpy.data.objects.new("TD_Snowfall", me))


def point_light(name, loc, watts, rgb, radius=0.1):
    L = bpy.data.lights.new(name, "POINT")
    L.energy, L.color, L.shadow_soft_size = watts, rgb, radius
    o = link(bpy.data.objects.new(name, L))
    o.location = loc
    return o


def world_dusk():
    w = bpy.data.worlds.new("TD_DuskWorld")
    scene.world = w
    w.use_nodes = True
    nt = w.node_tree
    bg = nt.nodes["Background"]
    hdr = sorted((ROOT / "assets" / "cache" / "ph_hdri_winter_lake_01").glob("*.hdr"))
    env = nt.nodes.new("ShaderNodeTexEnvironment")
    if hdr:
        env.image = bpy.data.images.load(str(hdr[0]))
    tc, mp = nt.nodes.new("ShaderNodeTexCoord"), nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Rotation"].default_value[2] = math.radians(150)
    nt.links.new(tc.outputs["Generated"], mp.inputs["Vector"])
    nt.links.new(mp.outputs["Vector"], env.inputs["Vector"])
    nt.links.new(env.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = 0.55
    # camera sees a soft dusk gradient instead of the photo; lighting still comes from the HDRI
    grad_bg = nt.nodes.new("ShaderNodeBackground")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(tc.outputs["Generated"], sep.inputs[0])
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position, ramp.color_ramp.elements[1].position = 0.45, 0.75
    ramp.color_ramp.elements[0].color = (0.045, 0.06, 0.11, 1)
    ramp.color_ramp.elements[1].color = (0.13, 0.17, 0.30, 1)
    nt.links.new(sep.outputs["Z"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], grad_bg.inputs["Color"])
    lp = nt.nodes.new("ShaderNodeLightPath")
    mix = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(lp.outputs["Is Camera Ray"], mix.inputs["Fac"])
    nt.links.new(bg.outputs[0], mix.inputs[1])
    nt.links.new(grad_bg.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], nt.nodes["World Output"].inputs["Surface"])


def polished():
    build_base()
    scatter_dressing()
    world_dusk()
    moon = bpy.data.lights.new("TD_Moon", "SUN")
    moon.energy, moon.color, moon.angle = 1.6, (0.62, 0.72, 1.0), math.radians(3)
    mo = link(bpy.data.objects.new("TD_Moon", moon))
    mo.rotation_euler = (math.radians(55), 0, math.radians(-140))
    fire = objs["campfire_1"].location
    point_light("TD_FireLight", (fire.x, fire.y, 0.55), 260, (1.0, 0.52, 0.2), radius=0.25)
    cab = objs["cabin_1"]
    for dx, dy, dz in ((-0.55, -0.55, 0.6), (-1.35, 0.9, 0.6), (1.5, 0.9, 0.6)):  # glow behind the windows
        p = cab.matrix_world @ Vector((dx, dy, dz))
        point_light("TD_WindowGlow", (p.x, p.y, p.z), 35, (1.0, 0.62, 0.3), radius=0.2)
    lamp = objs["lamp_1"].location
    point_light("TD_LanternLight", (lamp.x, lamp.y - 0.05, 0.3), 12, (1.0, 0.6, 0.28), radius=0.05)

    cam_data = bpy.data.cameras.new("TD_Camera")
    cam = link(bpy.data.objects.new("TD_Camera", cam_data))
    cam.location = (10.6, -13.9, 8.9)
    target = Vector((0.1, 0.4, 0.55))
    cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam_data.lens = 50
    cam_data.dof.use_dof = True
    cam_data.dof.focus_distance = (target - cam.location).length
    cam_data.dof.aperture_fstop = 4.0
    scene.camera = cam

    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = SAMPLES
    scene.cycles.use_denoising = True
    scene.cycles.max_bounces = 6
    scene.render.resolution_x, scene.render.resolution_y = 1600, 1000
    scene.render.resolution_percentage = PCT
    scene.view_settings.view_transform = "AgX"
    for look in ("AgX - Medium High Contrast", "Medium High Contrast", "AgX - Punchy", "Punchy"):
        try:
            scene.view_settings.look = look
            break
        except TypeError:
            continue
    scene.view_settings.exposure = 0.35


def pipeline_look():
    """Exactly what the director's visual check sees today: blender_engine/snapshot.py."""
    sun = bpy.data.lights.new("TD_Sun", "SUN")
    sun.energy = 3.0
    so = link(bpy.data.objects.new("TD_Sun", sun))
    so.rotation_euler = (0.7, 0.2, 0.6)


if LOOK == "polished":
    polished()
    scene.render.filepath = str(OUT.with_suffix(".png"))
    scene.render.image_settings.file_format = "PNG"
    import time
    t0 = time.time()
    bpy.ops.render.render(write_still=True)
    print(f"RENDERED {OUT.with_suffix('.png')} in {time.time() - t0:.0f}s")
else:
    pipeline_look()
    snapshot.render(OUT.with_suffix(".jpg"), width=1600, height=1000)  # snapshot.py writes JPEG
    print(f"RENDERED {OUT.with_suffix('.jpg')} (pipeline snapshot)")

bpy.ops.file.pack_all()
bpy.ops.wm.save_as_mainfile(filepath=str(OUT.with_suffix(".blend")), compress=True)
print(f"SAVED {OUT.with_suffix('.blend')}")
