"""Build the web 3D models (GLB) for every menu product.

Run inside Blender (tested with Blender 5.2):
  * via Blender MCP:   MENU_ROOT = r"X:/path/to/project"; exec(open(MENU_ROOT + "/blender/build_models.py").read())
  * headless:          blender -b -P blender/build_models.py -- [slug ...]

Inputs : assets/textures/** (made by scripts/prepare_textures.py from the menu photos)
Outputs: models/raw/<slug>.glb   (then optimised by scripts/optimize-models.mjs into models/<slug>.glb)

Modelling approach
------------------
Every dish was photographed straight from above. scripts/estimate_depth.py
measures the relief of each photo with a depth model, and
scripts/prepare_textures.py turns it into a height field (metres), the photo
albedo and a detail normal map. Here each dish becomes a dense relief mesh
(polar grid following the real outline), decimated for mobile, and boards are
cut out along the outline measured in the photo.
"""
import json
import math
import os
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Vector

ROOT = globals().get("MENU_ROOT") or os.environ.get("MENU_ROOT") or \
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEX = os.path.join(ROOT, "assets", "textures")
RAW = os.path.join(ROOT, "models", "raw")
SOURCES = json.load(open(os.path.join(ROOT, "assets", "sources.json"), encoding="utf-8"))
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


def finalize(objs, center=True):
    """Centre the dish on X/Y (unless center=False: keep the origin, e.g. the pizza's
    centre) and put its lowest point on the ground plane (z = 0), so model-viewer
    frames it and AR places it on the table."""
    bpy.context.view_layer.update()
    pts = []
    for o in objs:
        o.matrix_world = o.matrix_basis  # objects are unparented
        mw = o.matrix_world
        pts += [mw @ v.co for v in o.data.vertices]
    xs = [p.x for p in pts]; ys = [p.y for p in pts]; zs = [p.z for p in pts]
    off = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, min(zs)) if center else (0.0, 0.0, min(zs))
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
def read_height(path, hmax):
    """16-bit height PNG -> metres; row 0 = bottom (UV v = 0) as Blender stores it."""
    img = load_image(path, non_color=True)
    w, h = img.size
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    a = px.reshape(h, w, 4)[:, :, 0] * hmax
    bpy.data.images.remove(img)
    return a


def bilinear(a, u, v):
    h, w = a.shape
    x = np.clip(u, 0, 1) * (w - 1)
    y = np.clip(v, 0, 1) * (h - 1)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    fx, fy = x - x0, y - y0
    top = a[y0, x0] * (1 - fx) + a[y0, x1] * fx
    bot = a[y1, x0] * (1 - fx) + a[y1, x1] * fx
    return top * (1 - fy) + bot * fy


def polar_relief(name, height, extent, outline, mat, n_th=512, n_r=200, tuck=0.985, target_tris=32000,
                 edge_drop=0.45, rim_peak_m=None, base_z=0.0):
    """Relief mesh over a star-shaped outline, decimated for the web.

    height(u, v) -> metres; outline: radius (m) per photo angle (evenly spaced,
    image convention: clockwise from +x because image rows grow downward).
    UVs are the top-down projection of the photo square of half-size `extent`
    (the albedo is edge-extended outside the outline, so the sides pick up the
    rim colour). The dense top is decimated first; the rounded side and the flat
    bottom (z = 0) are then built from its boundary so they stay clean.
    rim_peak_m: distance of the crust's top from the edge. Outside it the UVs are
    unrolled outwards by the drop below the crust top, so the outer slope and the
    side get undistorted crust texture instead of stretched edge pixels."""
    th = np.linspace(0, TAU, n_th, endpoint=False)
    src = np.asarray(outline, np.float64)
    t = th / TAU * len(src)
    i0 = np.floor(t).astype(int) % len(src)
    f = t - np.floor(t)
    R = src[i0] * (1 - f) + src[(i0 + 1) % len(src)] * f
    ct, st = np.cos(th), -np.sin(th)
    verts = [(0.0, 0.0, float(height(np.array([0.5]), np.array([0.5]))[0]))]
    for rho in np.arange(1, n_r + 1) / n_r:
        x, y = rho * R * ct, rho * R * st
        verts.extend(zip(x, y, height(0.5 + x / (2 * extent), 0.5 + y / (2 * extent))))

    def ring(k, j):
        return 1 + k * n_th + (j % n_th)

    faces = [(0, ring(0, j + 1), ring(0, j)) for j in range(n_th)]
    for k in range(n_r - 1):
        faces += [(ring(k, j), ring(k, j + 1), ring(k + 1, j + 1), ring(k + 1, j)) for j in range(n_th)]
    me = bpy.data.meshes.new(name + "_dense")
    me.from_pydata(verts, [], faces)
    dense = link(bpy.data.objects.new(name + "_dense", me))
    tris = len(faces) * 2
    if target_tris and tris > target_tris:
        mod = dense.modifiers.new("decimate", "DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = target_tris / tris
        mod.use_collapse_triangulate = True
    bpy.context.view_layer.update()
    low = bpy.data.meshes.new_from_object(dense.evaluated_get(bpy.context.evaluated_depsgraph_get()))
    bpy.data.objects.remove(dense, do_unlink=True)
    bpy.data.meshes.remove(me)

    bm = bmesh.new()
    bm.from_mesh(low)
    bpy.data.meshes.remove(low)

    def extrude(edges):
        ret = bmesh.ops.extrude_edge_only(bm, edges=edges)
        vs = [g for g in ret["geom"] if isinstance(g, bmesh.types.BMVert)]
        es = [g for g in ret["geom"] if isinstance(g, bmesh.types.BMEdge)]
        vset = set(vs)
        return vs, [e for e in es if e.verts[0] in vset and e.verts[1] in vset]

    v1, e1 = extrude([e for e in bm.edges if e.is_boundary])
    for v in v1:                        # rounded lower edge
        v.co.z *= edge_drop
    v2, e2 = extrude(e1)
    for v in v2:                        # tucked under, onto the board (or a bowl's floor)
        v.co.x *= tuck
        v.co.y *= tuck
        v.co.z = base_z
    bmesh.ops.triangle_fill(bm, use_beauty=True, use_dissolve=False, edges=e2)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bmesh.ops.triangulate(bm, faces=[fc for fc in bm.faces if len(fc.verts) > 3])
    bm.verts.index_update()
    co = np.array([v.co[:] for v in bm.verts])
    r = np.hypot(co[:, 0], co[:, 1])
    r_uv = r.copy()
    if rim_peak_m:
        a = np.mod(np.arctan2(-co[:, 1], co[:, 0]), TAU) / TAU * len(src)
        k0 = np.floor(a).astype(int) % len(src)
        fa = a - np.floor(a)
        r_pk = src[k0] * (1 - fa) + src[(k0 + 1) % len(src)] * fa - rim_peak_m
        out = r > r_pk
        dx, dy = co[out, 0] / np.maximum(r[out], 1e-9), co[out, 1] / np.maximum(r[out], 1e-9)
        z_pk = height(0.5 + dx * r_pk[out] / (2 * extent), 0.5 + dy * r_pk[out] / (2 * extent))
        r_uv[out] = r[out] + np.maximum(z_pk - co[out, 2], 0)
    scale = np.where(r > 1e-9, r_uv / np.maximum(r, 1e-9), 1.0)
    uvs = np.c_[0.5 + co[:, 0] * scale / (2 * extent), 0.5 + co[:, 1] * scale / (2 * extent)]
    uv = bm.loops.layers.uv.new("UVMap")
    for fc in bm.faces:
        for loop in fc.loops:
            loop[uv].uv = uvs[loop.vert.index]
    return bm_object(name, bm, [mat], smooth=True, sharp_angle=math.radians(70))


def resample_closed(pts, spacing):
    """Evenly resample a closed polyline (and make it counter-clockwise)."""
    p = np.asarray(pts, np.float64)
    if np.sum(p[:, 0] * np.roll(p[:, 1], -1) - np.roll(p[:, 0], -1) * p[:, 1]) < 0:
        p = p[::-1]
    seg = np.linalg.norm(np.roll(p, -1, axis=0) - p, axis=1)
    s = np.r_[0, np.cumsum(seg)]
    n = max(16, int(s[-1] / spacing))
    q = np.linspace(0, s[-1], n, endpoint=False)
    loop = np.vstack([p, p[:1]])
    return np.c_[np.interp(q, s, loop[:, 0]), np.interp(q, s, loop[:, 1])]


def offset_loop(p, d):
    """Offset a smooth loop by d towards its left (= inside for a CCW outline)."""
    tng = np.roll(p, -1, axis=0) - np.roll(p, 1, axis=0)
    tng /= np.linalg.norm(tng, axis=1, keepdims=True) + 1e-12
    return p + np.c_[-tng[:, 1], tng[:, 0]] * d


def board_from_outline(name, outline, hole, thickness, uv_rect, mat, bevel=0.0025, spacing=0.002):
    """Flat board cut along a measured outline (+ optional round hole) with rounded
    edges; top face at z = 0; UVs = top-down projection of the photo rectangle
    uv_rect = [x0, y0, width, height] (metres)."""
    loops = [resample_closed(outline, spacing)]
    if hole:
        hx, hy, hr = hole
        a = np.linspace(0, TAU, 40, endpoint=False)[::-1]  # clockwise: the board is on its left too
        loops.append(np.c_[hx + hr * np.cos(a), hy + hr * np.sin(a)])
    # profile (inset from the outline, z): rounded top edge, straight side, rounded bottom edge
    prof = [(bevel * (1 - math.sin(a)), -bevel + bevel * math.cos(a)) for a in np.linspace(0, math.pi / 2, 4)]
    prof += [(bevel * (1 - math.sin(a)), -(thickness - bevel) + bevel * math.cos(a))
             for a in np.linspace(math.pi / 2, math.pi, 4)]
    bm = bmesh.new()
    caps = {0: [], len(prof) - 1: []}
    for lp in loops:
        n = len(lp)
        rings = [[bm.verts.new((x, y, z)) for x, y in offset_loop(lp, d)] for d, z in prof]
        for a, b in zip(rings[:-1], rings[1:]):
            for j in range(n):
                bm.faces.new((a[j], b[j], b[(j + 1) % n], a[(j + 1) % n]))
        for k in caps:
            caps[k] += [bm.edges.get((rings[k][j], rings[k][(j + 1) % n])) for j in range(n)]
    for edges in caps.values():
        bmesh.ops.triangle_fill(bm, use_beauty=True, use_dissolve=False, edges=edges)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    uv = bm.loops.layers.uv.new("UVMap")
    x0, y0, w, h = uv_rect
    for face in bm.faces:
        for loop in face.loops:
            loop[uv].uv = ((loop.vert.co.x - x0) / w, (loop.vert.co.y - y0) / h)
    return bm_object(name, bm, [mat], smooth=True, sharp_angle=math.radians(35))


def build_pizza(slug):
    meta = json.load(open(tex_path(slug, "model.json")))
    H = read_height(tex_path(slug, "height.png"), meta["height_max_m"])
    top = material(slug + "_top", (230, 170, 90), tex_path(slug, "albedo.jpg"),
                   tex_path(slug, "normal.jpg"), rough=0.5)
    pz = polar_relief(slug, lambda u, v: bilinear(H, u, v), meta["half_extent_m"], meta["outline_m"], top,
                      rim_peak_m=meta["rim_peak_from_edge_m"])
    peel = meta["peel"]
    wood = material(slug + "_board", (205, 145, 90), tex_path(slug, "board.jpg"), rough=0.58)
    board = board_from_outline("peel", peel["outline_m"], peel["hole_m"], peel["thickness_m"],
                               peel["uv_rect_m"], wood)
    finalize([board, pz], center=False)  # pizza centred on the origin (the viewer targets it), board below


def bowl_mesh(name, radius, height, wall, mat, segs=96):
    """Round ceramic bowl (lathe): foot ring, flared wall, rounded rim, inner wall."""
    R, H, w = radius, height, wall
    prof = [(0.0, 0.0), (0.55 * R, 0.0), (0.6 * R, 0.004), (0.9 * R, 0.5 * H), (R, H - 0.4 * w),
            (R - 0.35 * w, H), (R - 0.8 * w, H - 0.3 * w), (R - w, H - 0.7 * w), (0.9 * R - w, 0.5 * H),
            (0.55 * R, 0.013), (0.0, 0.013)]
    return lathe(name, prof, [mat], segs=segs)


def build_parts(slug):
    """Generic dish: relief items (food / dish bodies), flat boards and bowls, as
    described by assets/textures/<slug>/model.json (scripts/prepare_textures.py)."""
    meta = json.load(open(tex_path(slug, "model.json")))
    objs = []
    for p in meta["parts"]:
        name = f"{slug}_{p['name']}"
        if p["type"] == "relief":
            H = read_height(tex_path(slug, p["height"]), p["height_max_m"])
            mat = material(name, (200, 180, 150), tex_path(slug, p["albedo"]), tex_path(slug, p["normal"]),
                           rough=p.get("roughness", 0.55))
            ob = polar_relief(name, lambda u, v, H=H: bilinear(H, u, v), p["half_extent_m"], p["outline_m"], mat,
                              tuck=p.get("tuck", 0.97), edge_drop=p.get("edge_drop", 0.5),
                              rim_peak_m=p.get("rim_peak_m"), target_tris=p.get("target_tris", 12000),
                              base_z=p.get("base_z", 0.0))
            ob.location = p["offset_m"]
        elif p["type"] == "board":
            tex = tex_path(slug, p["albedo"]) if p.get("albedo") else None
            mat = material(name, tuple(p.get("color", (205, 145, 90))), tex, rough=p.get("roughness", 0.6))
            pts = np.asarray(p["outline_m"])
            uv_rect = p.get("uv_rect_m") or [pts[:, 0].min(), pts[:, 1].min(), np.ptp(pts[:, 0]), np.ptp(pts[:, 1])]
            ob = board_from_outline(name, p["outline_m"], p.get("hole_m"), p["thickness_m"], uv_rect, mat)
            ob.location.z = p.get("z_top_m", 0.0)
        elif p["type"] == "bowl":
            mat = material(name, tuple(p.get("color", (246, 244, 238))), rough=p.get("roughness", 0.2))
            ob = bowl_mesh(name, p["radius_m"], p["height_m"], p["wall_m"], mat)
            ob.location = (p["center_m"][0], p["center_m"][1], 0.0)
        else:
            raise ValueError(f"{slug}: unknown part type {p['type']}")
        objs.append(ob)
    finalize(objs)


# ----------------------------------------------------------------- preview / poster
# Poster camera per category / product: elevation, azimuth (deg, Blender: 0 = +X), distance factor
# and optional look-at point (Blender metres). Mirrors the site viewer's opening camera
# (model-viewer theta = azimuth + 90, phi = 90 - elevation).
POSTER_VIEW = {
    "pizza": (42, 90, 1.25, None),
    "garlic-bread": (38, -20, 1.0, (0.05, 0.028, 0.03)),
    "lasagna": (40, -55, 1.55, None),
    "fries": (42, -115, 1.5, None),
    "salad-plate": (35, -105, 1.45, None),
}


def preview_world(sc, strength=0.85):
    """Neutral white surround (like the site viewer's 'neutral' environment): the photo
    albedo then renders with its own colours; a soft sun adds shading and a shadow."""
    world = bpy.data.worlds.get("preview_world") or bpy.data.worlds.new("preview_world")
    if bpy.app.version < (5, 0, 0):
        world.use_nodes = True
    bg = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
    bg.inputs["Color"].default_value = (1, 1, 1, 1)
    bg.inputs["Strength"].default_value = strength
    sc.world = world


def render_preview(path, size=768, elevation_deg=35, azimuth_deg=-90, dist_factor=1.45, target=None):
    """Render the current dish (model-viewer poster + QA). Camera/lights are not exported."""
    sc = _SCENE or bpy.context.scene
    dg = bpy.context.evaluated_depsgraph_get()
    pts = []
    for o in sc.objects:
        if o.type == "MESH":
            me = o.evaluated_get(dg).to_mesh()
            pts += [o.matrix_world @ v.co for v in me.vertices]
            o.evaluated_get(dg).to_mesh_clear()
    ext = max(max(p[i] for p in pts) - min(p[i] for p in pts) for i in range(3))
    cz = (max(p.z for p in pts) + min(p.z for p in pts)) / 2
    cam = sc.objects.get("preview_cam")
    if cam is None:
        cam = link(bpy.data.objects.new("preview_cam", bpy.data.cameras.new("preview_cam")))
        cam.data.lens = 50
        sun = link(bpy.data.objects.new("preview_sun", bpy.data.lights.new("preview_sun", "SUN")))
        sun.data.energy = 0.5
        sun.data.angle = math.radians(25)
        sun.rotation_euler = (math.radians(30), math.radians(12), math.radians(-30))
    dist = ext * dist_factor
    el, az = math.radians(elevation_deg), math.radians(azimuth_deg)
    tx, ty, tz = target or (0.0, 0.0, cz)
    cam.location = (tx + dist * math.cos(el) * math.cos(az), ty + dist * math.cos(el) * math.sin(az),
                    tz + dist * math.sin(el))
    direction = Vector((tx, ty, tz)) - cam.location
    cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    sc.camera = cam
    preview_world(sc)
    sc.render.engine = "BLENDER_EEVEE"
    sc.render.resolution_x = sc.render.resolution_y = size
    sc.render.film_transparent = True
    fmt = "WEBP" if path.lower().endswith(".webp") else ("JPEG" if path.lower().endswith(".jpg") else "PNG")
    sc.render.image_settings.file_format = fmt
    sc.render.image_settings.color_mode = "RGB" if fmt == "JPEG" else "RGBA"
    if fmt in ("WEBP", "JPEG"):
        sc.render.image_settings.quality = 85
    try:
        sc.view_settings.view_transform = "Khronos PBR Neutral"  # same tone mapping as the site viewer
    except TypeError:
        sc.view_settings.view_transform = "Standard"
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True, scene=sc.name)
    return path


def qa_views(slug, folder):
    """Several angles of the current dish for visual review (QA_DIR=...)."""
    os.makedirs(folder, exist_ok=True)
    for tag, el, az in (("top", 89, -90), ("front", 30, -90), ("side", 18, 0), ("back", 40, 120)):
        render_preview(os.path.join(folder, f"{slug}_{tag}.jpg"), 640, el, az, 1.2)


BUILDERS = {slug: build_pizza for slug in SOURCES["pizzas"]}
BUILDERS.update({slug: build_parts for slug in SOURCES["dishes"] if not slug.startswith("_")})


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
        if os.environ.get("QA_DIR"):
            qa_views(slug, os.environ["QA_DIR"])
        if previews:
            os.makedirs(os.path.join(ROOT, "assets", "posters"), exist_ok=True)
            el, az, k, tgt = POSTER_VIEW.get("pizza" if slug in SOURCES["pizzas"] else slug, (35, -90, 1.45, None))
            render_preview(os.path.join(ROOT, "assets", "posters", slug + ".webp"), 768, el, az, k, tgt)
    return done


if __name__ == "__main__" and bpy.app.background:  # blender -b -P blender/build_models.py [-- slug ...]
    build(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else None)
