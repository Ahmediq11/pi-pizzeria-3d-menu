"""Build the web 3D models (GLB) for every menu product.

Run inside Blender (tested with Blender 5.2):
  * via Blender MCP:   MENU_ROOT = r"X:/path/to/project"; exec(open(MENU_ROOT + "/blender/build_models.py").read())
  * headless:          blender -b -P blender/build_models.py -- [slug ...]

Inputs : assets/textures/** (made by scripts/prepare_textures.py from the menu photos)
Outputs: models/raw/<slug>.glb   (then optimised by scripts/optimize-models.mjs into models/<slug>.glb)

Modelling approach
------------------
The menu only has one top-down photo per dish, so a 100% faithful reconstruction
is impossible. Each dish is modelled procedurally (crust profile, slab, tray,
bowls, ...) at plausible real-world size, and the photo itself is projected
onto the top surface as the colour texture. A relief map estimated from the
photo raises toppings above the cheese, and a normal map adds surface detail.
"""
import json
import math
import os
import random
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Matrix

ROOT = globals().get("MENU_ROOT") or os.environ.get("MENU_ROOT") or \
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEX = os.path.join(ROOT, "assets", "textures")
RAW = os.path.join(ROOT, "models", "raw")
SOURCES = json.load(open(os.path.join(ROOT, "assets", "sources.json"), encoding="utf-8"))
COLORS = json.load(open(os.path.join(TEX, "_shared", "colors.json")))
TAU = math.tau


# ----------------------------------------------------------------- helpers
def srgb(c, a=1.0):
    """0-255 sRGB -> linear RGBA (what Principled BSDF expects)."""
    def lin(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return (lin(c[0]), lin(c[1]), lin(c[2]), a)


def tex_path(slug, name):
    return os.path.join(TEX, slug, name)


def load_image(path, non_color=False):
    img = bpy.data.images.load(path, check_existing=True)
    if non_color:
        img.colorspace_settings.name = "Non-Color"
    return img


def material(name, color=(200, 200, 200), albedo=None, normal=None, rough=0.6,
             normal_strength=1.0, metallic=0.0):
    m = bpy.data.materials.new(name)
    if bpy.app.version < (5, 0, 0):
        m.use_nodes = True  # always on (and deprecated) since Blender 5
    nt = m.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        nt.nodes.clear()
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    bsdf.inputs["Base Color"].default_value = srgb(color)
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metallic
    if albedo:
        t = nt.nodes.new("ShaderNodeTexImage")
        t.image = load_image(albedo)
        nt.links.new(t.outputs["Color"], bsdf.inputs["Base Color"])
    if normal:
        t = nt.nodes.new("ShaderNodeTexImage")
        t.image = load_image(normal, non_color=True)
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nm.inputs["Strength"].default_value = normal_strength
        nt.links.new(t.outputs["Color"], nm.inputs["Color"])
        nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    return m


_SCENE = None


def link(ob):
    (_SCENE or bpy.context.scene).collection.objects.link(ob)
    return ob


def mesh_object(name, verts, faces, uvs=None, mats=(), face_mat=None, smooth=True, sharp_angle=None):
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in verts], [], [tuple(f) for f in faces])
    me.validate()
    if uvs is not None:
        layer = me.uv_layers.new(name="UVMap")
        idx = np.empty(len(me.loops), dtype=np.int32)
        me.loops.foreach_get("vertex_index", idx)
        layer.data.foreach_set("uv", np.asarray(uvs, np.float32)[idx].ravel())
    uniq = list(dict.fromkeys(mats))
    for m in uniq:
        me.materials.append(m)
    if face_mat is not None:
        remap = np.array([uniq.index(m) for m in mats], np.int32)
        me.polygons.foreach_set("material_index", remap[np.asarray(face_mat, np.int32)])
    # weld coincident vertices (e.g. the UV seam column); UVs are per-loop so seams survive
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-7)
    bm.to_mesh(me)
    bm.free()
    if smooth:
        me.shade_smooth()
        if sharp_angle is not None:
            me.set_sharp_from_angle(angle=sharp_angle)
    me.update()
    return link(bpy.data.objects.new(name, me))


def bm_object(name, bm, mats=(), smooth=True, sharp_angle=None):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for m in mats:
        me.materials.append(m)
    if smooth:
        me.shade_smooth()
        if sharp_angle is not None:
            me.set_sharp_from_angle(angle=sharp_angle)
    return link(bpy.data.objects.new(name, me))


class HeightMap:
    def __init__(self, path, scale_m):
        img = load_image(path, non_color=True)
        w, h = img.size
        px = np.empty(w * h * 4, np.float32)
        img.pixels.foreach_get(px)
        self.a = px.reshape(h, w, 4)[:, :, 0]  # row 0 = bottom (v = 0)
        self.scale = scale_m

    def __call__(self, u, v):
        h, w = self.a.shape
        x = np.clip(u, 0, 1) * (w - 1)
        y = np.clip(v, 0, 1) * (h - 1)
        x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
        x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
        fx, fy = x - x0, y - y0
        a = self.a
        top = a[y0, x0] * (1 - fx) + a[y0, x1] * fx
        bot = a[y1, x0] * (1 - fx) + a[y1, x1] * fx
        return (top * (1 - fy) + bot * fy) * self.scale


def superellipse(theta, n):
    c, s = np.cos(theta), np.sin(theta)
    e = 2.0 / n
    return np.sign(c) * np.abs(c) ** e, np.sign(s) * np.abs(s) ** e


def disc(name, sx, sy, profile, mats, segs=128, n=2.0, height=None, wobble=None, side_repeat=4.0, seed=0,
         uv_rho=None):
    """Revolve a profile around Z over a (super)ellipse footprint.

    profile: list of (rho, z, part, disp) where rho in [0, 1] is the normalised
    radius, part is 0=top (planar UV), 1=side (cylindrical UV), 2=bottom, and
    disp in [0, 1] weights the relief map. The first and last points may have
    rho == 0 (closed caps). uv_rho(rho, part) optionally remaps the radius used
    for planar UVs (e.g. so a pizza's rim samples the photographed crust).
    Each ring repeats its first vertex at theta = 2*pi so side UVs get a proper
    seam (continuous U, valid tangents everywhere)."""
    rng = np.random.default_rng(seed)
    th = np.linspace(0, TAU, segs + 1)  # last column duplicates the first (UV seam)
    ux, uy = superellipse(th, n)
    # smooth random variation around the circumference (organic look)
    wob = np.zeros(len(th))
    if wobble:
        for k in (2, 3, 5, 7):
            wob += rng.normal(0, 1) * np.cos(k * th + rng.uniform(0, TAU)) / k
        wob = wob / (np.abs(wob).max() + 1e-9)
    zmax = max(p[1] for p in profile)
    verts, uvs, faces, fmat, rings = [], [], [], [], []
    for (rho, z, part, disp) in profile:
        if rho == 0:
            x = y = np.zeros(1)
        else:
            x, y = ux * rho * sx, uy * rho * sy
        zz = np.full(x.shape, z, dtype=np.float64)
        u, v = 0.5 + x / (2 * sx), 0.5 + y / (2 * sy)
        if height is not None and disp > 0:
            zz += height(u, v) * disp
        if uv_rho is not None and rho > 0:
            k = uv_rho(rho, part) / rho
            u, v = 0.5 + x * k / (2 * sx), 0.5 + y * k / (2 * sy)
        if wobble and rho > 0:
            zz += wob * wobble * (z / zmax)
        if part == 1 and uv_rho is None:
            u = np.zeros(x.shape) if rho == 0 else th / TAU * side_repeat
            v = np.full(x.shape, z / zmax)
        start = len(verts)
        verts += list(zip(x, y, zz))
        uvs += list(zip(u, v))
        rings.append((start, len(x), part))
    for (a, na, pa), (b, nb, pb) in zip(rings[:-1], rings[1:]):
        mat = max(pa, pb) if pa != 2 and pb != 2 else 2
        if na == 1 and nb > 1:
            for s in range(nb - 1):
                faces.append((a, b + s, b + s + 1)); fmat.append(mat)
        elif na > 1 and nb == 1:
            for s in range(na - 1):
                faces.append((a + s, b, a + s + 1)); fmat.append(mat)
        else:
            for s in range(na - 1):
                faces.append((a + s, b + s, b + s + 1, a + s + 1)); fmat.append(mat)
    fmat = [min(m, len(mats) - 1) for m in fmat]
    return mesh_object(name, verts, faces, uvs, mats, fmat)


def lathe(name, profile, mats, segs=64, uv_radius=None):
    """Simple revolve of (r, z) points; planar top UV if uv_radius given."""
    prof = [(r, z, 0, 0) for r, z in profile]
    r = uv_radius or max(p[0] for p in profile)
    scaled = [(p[0] / r, p[1], 0, 0) for p in prof]
    return disc(name, r, r, scaled, mats, segs=segs)


def rounded_rect(w, h, r, n=8):
    pts = []
    for cx, cy, a0 in ((w / 2 - r, h / 2 - r, 0), (-w / 2 + r, h / 2 - r, 90),
                       (-w / 2 + r, -h / 2 + r, 180), (w / 2 - r, -h / 2 + r, 270)):
        for i in range(n + 1):
            a = math.radians(a0 + 90 * i / n)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def slab_from_outline(name, outline, thickness, mats, uv_scale=(0.1, 0.1), bevel=0.002, z0=0.0):
    """Extrude a 2D outline (CCW) into a slab with planar-projected UVs."""
    bm = bmesh.new()
    vs = [bm.verts.new((x, y, z0)) for x, y in outline]
    f = bm.faces.new(vs)
    ret = bmesh.ops.extrude_face_region(bm, geom=[f])
    top = [g for g in ret["geom"] if isinstance(g, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, verts=top, vec=(0, 0, thickness))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    if bevel:
        bmesh.ops.bevel(bm, geom=bm.edges[:], offset=bevel, segments=2, affect="EDGES",
                        profile=0.5, clamp_overlap=True)
        # bevels on short outline segments can leave sliver faces -> bad normals/tangents
        bmesh.ops.dissolve_degenerate(bm, edges=bm.edges[:], dist=1e-5)
        bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-6)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 4])
    uv = bm.loops.layers.uv.new("UVMap")
    for face in bm.faces:  # box mapping: project along the face's dominant axis (never degenerate)
        n = face.normal
        ax = max(range(3), key=lambda i: abs(n[i]))
        for loop in face.loops:
            co = loop.vert.co
            if ax == 2:
                loop[uv].uv = (co.x / uv_scale[0], co.y / uv_scale[1])
            elif ax == 0:
                loop[uv].uv = (co.y / uv_scale[0], (co.z - z0) / uv_scale[1] * 4)
            else:
                loop[uv].uv = (co.x / uv_scale[0], (co.z - z0) / uv_scale[1] * 4)
    return bm_object(name, bm, mats, smooth=True, sharp_angle=math.radians(40))


def tray(name, w, h, r, height, wall, mats, floor=0.006, handle=None):
    """Serving dish: rounded-rect tray with sloped inner walls (and an optional handle tab)."""
    bm = bmesh.new()
    outline = rounded_rect(w, h, r, 10)
    vs = [bm.verts.new((x, y, 0)) for x, y in outline]
    f = bm.faces.new(vs)
    ret = bmesh.ops.extrude_face_region(bm, geom=[f])
    top_verts = [g for g in ret["geom"] if isinstance(g, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, verts=top_verts, vec=(0, 0, height))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    top = next(fc for fc in bm.faces if fc.normal.z > 0.9 and all(abs(v.co.z - height) < 1e-6 for v in fc.verts))
    rim = bmesh.ops.inset_region(bm, faces=[top], thickness=wall, depth=0)
    bmesh.ops.inset_region(bm, faces=[top], thickness=wall * 0.9, depth=0)
    bmesh.ops.translate(bm, verts=list(top.verts), vec=(0, 0, -(height - floor)))
    bmesh.ops.bevel(bm, geom=[e for e in bm.edges if e.calc_face_angle(0) > 0.3], offset=wall * 0.35,
                    segments=3, affect="EDGES", profile=0.5, clamp_overlap=True)
    bmesh.ops.triangulate(bm, faces=[fc for fc in bm.faces if len(fc.verts) > 4])
    uv = bm.loops.layers.uv.new("UVMap")
    for face in bm.faces:
        for loop in face.loops:
            loop[uv].uv = (loop.vert.co.x / w + 0.5, loop.vert.co.y / h + 0.5)
    ob = bm_object(name, bm, mats, smooth=True, sharp_angle=math.radians(50))
    parts = [ob]
    if handle:
        hw, hl, hole = handle
        tab = rounded_rect(hw, hl, hw * 0.45, 8)
        tab = [(x + w / 2 + hl / 2 - r * 0.6, y) for x, y in [(p[1], p[0]) for p in tab]]
        parts.append(slab_from_outline(name + "_handle", tab, height * 0.18, mats, (w, h), 0.002, z0=height * 0.78))
    return parts


def fresh_scene(slug):
    """One scene per product (kept for inspection in the UI). In background mode
    there is no window to switch scenes, so the default scene is emptied instead."""
    global _SCENE
    if bpy.context.window:
        sc = bpy.data.scenes.new("build_" + slug)
        bpy.context.window.scene = sc
    else:
        sc = bpy.context.scene
        for o in list(sc.objects):
            bpy.data.objects.remove(o, do_unlink=True)
    sc.unit_settings.system = "METRIC"
    _SCENE = sc
    return sc


def finalize(objs):
    """Centre the dish on X/Y and put its lowest point on the ground plane (z = 0),
    so model-viewer frames it and AR places it on the table."""
    bpy.context.view_layer.update()
    pts = []
    for o in objs:
        o.matrix_world = o.matrix_basis  # objects are unparented
        mw = o.matrix_world
        pts += [mw @ v.co for v in o.data.vertices]
    xs = [p.x for p in pts]; ys = [p.y for p in pts]; zs = [p.z for p in pts]
    off = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, min(zs))
    for o in objs:
        o.location = (o.location.x - off[0], o.location.y - off[1], o.location.z - off[2])
    return max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))


def export(slug):
    os.makedirs(RAW, exist_ok=True)
    path = os.path.join(RAW, slug + ".glb")
    bpy.ops.export_scene.gltf(
        filepath=path, export_format="GLB", use_active_scene=True,
        export_apply=True, export_yup=True, export_texcoords=True, export_normals=True, export_tangents=True,
        export_materials="EXPORT", export_image_format="AUTO",
        export_cameras=False, export_lights=False, export_extras=False,
    )
    return path


# ----------------------------------------------------------------- dishes
def wood_material():
    return material("wood", (205, 145, 90), tex_path("_shared", "wood.jpg"),
                    tex_path("_shared", "wood_normal.jpg"), rough=0.72)


def peel(diam):
    """Wooden pizza peel as seen in every pizza photo (blade + tapered handle)."""
    d = diam
    pts = []
    # blade: square-ish with rounded top corners, narrower than the pizza
    hw, top, bottom, rr = 0.42 * d, 0.47 * d, -0.30 * d, 0.09 * d
    for i in range(9):  # top-right corner
        a = math.radians(90 * i / 8)
        pts.append((hw - rr + rr * math.cos(a), top - rr + rr * math.sin(a)))
    for i in range(9):  # top-left corner
        a = math.radians(90 + 90 * i / 8)
        pts.append((-hw + rr + rr * math.cos(a), top - rr + rr * math.sin(a)))
    # left side down, concave taper into the handle, handle, rounded end, back up
    hh = 0.065 * d
    left = [(-hw, bottom)]
    for i in range(1, 9):
        t = i / 8
        left.append((-hw + (hw - hh) * (1 - math.cos(t * math.pi / 2)) ** 0.6, bottom - t * 0.30 * d))
    left += [(-hh * 1.05, -0.80 * d), (-hh * 1.2, -0.93 * d)]
    for i in range(1, 8):
        a = math.radians(180 + 180 * i / 8)
        left.append((hh * 1.2 * math.cos(a), -0.93 * d + hh * 0.8 * math.sin(a)))
    pts += left
    right = [(-x, y) for x, y in reversed(left[:-7])]
    pts += right
    ob = slab_from_outline("peel", pts, 0.012, [wood_material()], uv_scale=(0.09, 0.20), bevel=0.0025, z0=-0.012)
    return ob


def build_pizza(slug):
    meta = json.load(open(tex_path(slug, "height.json")))
    D = meta["diameter_m"]; R = D / 2; crust = meta["crust"]
    hm = HeightMap(tex_path(slug, "height.png"), meta["height_max_m"])
    top = material(slug + "_top", (230, 170, 90), tex_path(slug, "albedo.jpg"),
                   tex_path(slug, "normal.jpg"), rough=0.55)
    bottom = material(slug + "_base", COLORS["bread_crust"], rough=0.85)
    h_base, peak = 0.010, 0.016          # cheese surface, rim bump above it
    rho_in = 1 - crust * 1.25            # where the rim starts rising
    prof = [(0.0, h_base, 0, 1.0)]
    n_in = 34
    for i in range(1, n_in + 1):
        rho = rho_in * (i / n_in) ** 0.85
        fade = min(1.0, (rho_in - rho) / 0.06 + 0.15)
        prof.append((rho, h_base, 0, fade))
    rm, a = (rho_in + 1) / 2, (1 - rho_in) / 2
    for i in range(1, 15):              # rim: upper half-ellipse
        t = math.pi * (1 - i / 14)
        prof.append((rm + a * math.cos(t), h_base + peak * math.sin(t) ** 0.8, 0, 0))
    for i in range(1, 7):               # rounded outer/lower edge
        t = -math.pi / 2 * i / 6
        prof.append((rm + a * math.cos(t) * 0.98 + 0.002, h_base * (1 + math.sin(t)), 1, 0))
    for k in (0.66, 0.33):
        prof.append((rm * k, 0.0, 2, 0))
    prof.append((0.0, 0.0, 2, 0))
    tex_edge = 0.965  # fitted circles hug the crust; stay just inside it

    def rim_uv(rho, part):
        if part != 0:
            return 1 - crust * 0.45          # sides: middle of the photographed crust ring
        if rho <= rho_in:
            return rho
        return rho_in + (rho - rho_in) * (tex_edge - rho_in) / (1 - rho_in)

    pz = disc(slug, R, R, prof, [top, top, bottom], segs=128, height=hm, wobble=0.0018, seed=len(slug),
              uv_rho=rim_uv)
    finalize([pz, peel(D)])  # peel top is at z = 0, the pizza sits on it


def build_garlic_bread(slug):
    top = material(slug + "_top", COLORS["bread_crust"], tex_path(slug, "albedo.jpg"), tex_path(slug, "normal.jpg"), rough=0.6)
    # sides are toasted darker than the cheese-covered top seen in the photo
    crust = material(slug + "_crust", [int(c * 0.78) for c in COLORS["bread_crust"]], rough=0.8)
    crumb = material(slug + "_crumb", COLORS["bread_crumb"], rough=0.9)

    def piece(name, sx, sy, h, seed):
        prof = [(0.0, h, 0, 0)]
        for i in range(1, 14):
            rho = 0.96 * i / 13
            prof.append((rho, h * (1 - 0.10 * rho ** 2.5), 0, 0))
        prof += [(1.0, h * 0.72, 1, 0), (1.0, h * 0.3, 1, 0), (0.97, 0.0, 1, 0), (0.5, 0.0, 2, 0), (0.0, 0.0, 2, 0)]
        return disc(name, sx, sy, prof, [top, crust, crumb], segs=72, n=2.2, wobble=0.0015, seed=seed,
                    side_repeat=2)

    big = piece(slug, 0.056, 0.092, 0.019, 1)
    small = piece(slug + "_2", 0.042, 0.064, 0.017, 2)
    big.rotation_euler.z = math.radians(15)
    big.location.x = -0.03
    small.location = (0.075, 0.07, 0)
    small.rotation_euler.z = math.radians(-35)
    circle = [(0.172 * math.cos(a), 0.172 * math.sin(a)) for a in np.linspace(0, TAU, 96, endpoint=False)]
    board = slab_from_outline("board", circle, 0.018, [wood_material()], uv_scale=(0.09, 0.20), bevel=0.003, z0=-0.018)
    finalize([board, big, small])


def build_lasagna(slug):
    top = material(slug + "_top", COLORS["lasagna_cheese"], tex_path(slug, "albedo.jpg"), tex_path(slug, "normal.jpg"), rough=0.5)
    side = material(slug + "_side", COLORS["lasagna_sauce"], tex_path(slug, "side.jpg"), rough=0.6)
    bottom = material(slug + "_base", COLORS["lasagna_cheese"], rough=0.8)
    h = 0.045
    prof = [(0.0, h + 0.003, 0, 0)]
    for i in range(1, 10):
        rho = 0.93 * i / 9
        prof.append((rho, h + 0.003 * (1 - rho ** 2), 0, 0))
    prof += [(0.98, h * 0.985, 0, 0), (1.0, h * 0.93, 1, 0)]
    for i in range(1, 7):
        prof.append((1.0, h * 0.93 * (1 - i / 6), 1, 0))
    prof += [(0.5, 0.0, 2, 0), (0.0, 0.0, 2, 0)]
    las = disc(slug, 0.048, 0.08, prof, [top, side, bottom], segs=96, n=8, side_repeat=6, wobble=0.0015)
    board = slab_from_outline("board", rounded_rect(0.20, 0.34, 0.03, 6), 0.02, [wood_material()],
                              uv_scale=(0.09, 0.20), bevel=0.002, z0=-0.02)
    finalize([board, las])


def build_fries(slug):
    fry = material(slug + "_fry", COLORS["fries"], tex_path(slug, "fry.jpg"), rough=0.5)
    dish = material("dish_white", (244, 242, 236), rough=0.3)
    parts = tray("dish", 0.17, 0.17, 0.035, 0.03, 0.007, [dish], handle=(0.05, 0.09, 0.012))
    rnd = random.Random(7)
    bm = bmesh.new()
    uv = bm.loops.layers.uv.new("UVMap")
    for i in range(150):
        L = rnd.uniform(0.045, 0.085); t = rnd.uniform(0.0085, 0.0105)
        res = bmesh.ops.create_cube(bm, size=1.0, calc_uvs=False)
        vs = res["verts"]
        bmesh.ops.scale(bm, verts=vs, vec=(t, L, t))
        yaw = rnd.uniform(0, math.pi); pitch = rnd.uniform(-0.35, 0.35)
        rot = Matrix.Rotation(yaw, 4, "Z") @ Matrix.Rotation(pitch, 4, "X")
        bmesh.ops.rotate(bm, verts=vs, cent=(0, 0, 0), matrix=rot)
        rr = rnd.random() ** 0.7
        ang = rnd.uniform(0, TAU)
        x, y = math.cos(ang) * rr * 0.058, math.sin(ang) * rr * 0.058
        z = 0.012 + (1 - rr ** 2) * 0.034 * rnd.uniform(0.3, 1.0)
        bmesh.ops.translate(bm, verts=vs, vec=(x, y, z))
    bmesh.ops.bevel(bm, geom=bm.edges[:], offset=0.0012, segments=1, affect="EDGES", clamp_overlap=True)
    for face in bm.faces:
        n = face.normal
        for loop in face.loops:
            c = loop.vert.co
            loop[uv].uv = ((c.x + c.z) * 12, (c.y + c.z) * 12) if abs(n.z) < 0.7 else (c.x * 12, c.y * 12)
    fr = bm_object(slug, bm, [fry], smooth=True, sharp_angle=math.radians(60))
    finalize(parts + [fr])


def build_salad(slug):
    """One salad board as photographed (first row of the cold page: 3 bowls)."""
    board_m = material("board_black", COLORS["salad_board"], rough=0.35)
    bowl_m = material("bowl_white", (246, 244, 238), rough=0.25)
    w = 0.40
    board = slab_from_outline("board", rounded_rect(w, 0.135, 0.05, 10), 0.014, [board_m], bevel=0.003, z0=-0.014)
    handle = lathe("handle", [(0.0, 0.0), (0.016, 0.0), (0.016, 0.07), (0.02, 0.072), (0.02, 0.09), (0.0, 0.09)], [board_m], segs=32)
    handle.rotation_euler.y = math.radians(90)
    handle.location = (w / 2 - 0.01, 0, 0.004)
    objs = [board, handle]
    for i, x in enumerate((-0.125, 0.0, 0.125)):
        b = lathe(f"bowl{i + 1}", [(0.0, 0.0), (0.034, 0.0), (0.036, 0.003), (0.056, 0.044), (0.0575, 0.047),
                                   (0.0555, 0.0475), (0.052, 0.046), (0.034, 0.008), (0.0, 0.008)], [bowl_m], segs=64)
        food = material(f"{slug}_bowl{i + 1}", (220, 210, 180), tex_path(slug, f"bowl{i + 1}.jpg"),
                        tex_path(slug, f"bowl{i + 1}_normal.jpg"), rough=0.6)
        prof = [(0.0, 0.052, 0, 0)] + [(r, 0.052 - 0.01 * r ** 2, 0, 0) for r in (0.2, 0.4, 0.6, 0.8, 0.93)] + \
               [(1.0, 0.040, 0, 0), (0.0, 0.040, 0, 0)]
        f = disc(f"food{i + 1}", 0.051, 0.051, prof, [food], segs=64, wobble=0.003, seed=i)
        b.location.x = f.location.x = x
        objs += [b, f]
    finalize(objs)


# ----------------------------------------------------------------- preview / poster
def render_preview(path, size=768, elevation_deg=35, azimuth_deg=-90):
    """Render the current dish (used as model-viewer poster + for QA). Camera/light
    objects are not exported (export_cameras/lights=False)."""
    sc = _SCENE or bpy.context.scene
    objs = [o for o in sc.objects if o.type == "MESH"]
    pts = [o.matrix_world @ v.co for o in objs for v in o.data.vertices]
    ext = max(max(p[i] for p in pts) - min(p[i] for p in pts) for i in range(3))
    cz = (max(p.z for p in pts) + min(p.z for p in pts)) / 2
    cam_data = bpy.data.cameras.new("preview_cam")
    cam_data.lens = 50
    cam = bpy.data.objects.new("preview_cam", cam_data)
    link(cam)
    dist = ext * 1.45
    el, az = math.radians(elevation_deg), math.radians(azimuth_deg)
    cam.location = (dist * math.cos(el) * math.cos(az), dist * math.cos(el) * math.sin(az), cz + dist * math.sin(el))
    direction = -cam.location.copy()
    direction.z += cz
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    sc.camera = cam
    sun_data = bpy.data.lights.new("preview_sun", "SUN")
    sun_data.energy = 2.2
    sun_data.angle = math.radians(8)
    sun = bpy.data.objects.new("preview_sun", sun_data)
    sun.rotation_euler = (math.radians(35), math.radians(10), math.radians(-30))
    link(sun)
    world = bpy.data.worlds.get("preview_world") or bpy.data.worlds.new("preview_world")
    if bpy.app.version < (5, 0, 0):
        world.use_nodes = True
    bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
    bg.inputs["Color"].default_value = (1, 1, 1, 1)
    bg.inputs["Strength"].default_value = 0.55
    sc.world = world
    sc.render.engine = "BLENDER_EEVEE"
    sc.render.resolution_x = sc.render.resolution_y = size
    sc.render.film_transparent = True
    fmt = "WEBP" if path.lower().endswith(".webp") else "PNG"
    sc.render.image_settings.file_format = fmt
    sc.render.image_settings.color_mode = "RGBA"
    if fmt == "WEBP":
        sc.render.image_settings.quality = 82
    sc.view_settings.view_transform = "AgX"
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True, scene=sc.name)
    return path


BUILDERS = {slug: build_pizza for slug in SOURCES["pizzas"]}
BUILDERS.update({
    "garlic-bread": build_garlic_bread,
    "lasagna": build_lasagna,
    "fries": build_fries,
    "salad-plate": build_salad,
})


def cleanup():
    for sc in [s for s in bpy.data.scenes if s.name.startswith("build_")]:
        for o in list(sc.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        bpy.data.scenes.remove(sc)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.images):
        for blk in list(coll):
            if blk.users == 0:
                coll.remove(blk)


def build(slugs=None, previews=True):
    cleanup()
    done = {}
    for slug in slugs or BUILDERS:
        fresh_scene(slug)
        BUILDERS[slug](slug)
        done[slug] = export(slug)
        if previews:
            os.makedirs(os.path.join(ROOT, "assets", "posters"), exist_ok=True)
            render_preview(os.path.join(ROOT, "assets", "posters", slug + ".webp"))
    return done


if __name__ == "__main__" and bpy.app.background:  # blender -b -P blender/build_models.py [-- slug ...]
    build(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else None)
