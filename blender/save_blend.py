"""Collect every built model into one Blender file (one scene per product).

Run after build_models.py:  blender -b --factory-startup -P blender/save_blend.py
Reads models/raw/*.glb and writes models/source/menu-models.blend (textures packed).
"""
import glob
import os

import bpy

ROOT = os.environ.get("MENU_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
first = bpy.context.scene
for o in list(first.objects):
    bpy.data.objects.remove(o, do_unlink=True)
for path in sorted(glob.glob(os.path.join(ROOT, "models", "raw", "*.glb"))):
    slug = os.path.splitext(os.path.basename(path))[0]
    sc = bpy.data.scenes.new(slug)
    sc.unit_settings.system = "METRIC"
    with bpy.context.temp_override(scene=sc):
        bpy.ops.import_scene.gltf(filepath=path)
    # the importer links into the context scene; make sure everything landed in this one
    for o in bpy.context.selected_objects:
        if o.name not in sc.objects:
            sc.collection.objects.link(o)
        for c in list(o.users_collection):
            if c != sc.collection and c.name not in [x.name for x in sc.collection.children_recursive]:
                c.objects.unlink(o)
bpy.data.scenes.remove(first)
bpy.ops.file.pack_all()
out = os.path.join(ROOT, "models", "source", "menu-models.blend")
os.makedirs(os.path.dirname(out), exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=out, compress=True)
print("saved", out, len(bpy.data.scenes), "scenes")
