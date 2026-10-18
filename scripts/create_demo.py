"""Run inside Blender to create original demo geometry; never renders previews."""
from pathlib import Path
import math
import bpy

ROOT = Path(__file__).resolve().parents[1]
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.context.scene.unit_settings.system = 'METRIC'
bpy.context.scene.unit_settings.scale_length = 1
metal = bpy.data.materials.new('Metal')
metal.use_nodes = True
bsdf = metal.node_tree.nodes.get('Principled BSDF')
bsdf.inputs['Metallic'].default_value = 1
bsdf.inputs['Roughness'].default_value = .16

def cylinder(name, radius, depth, y):
    bpy.ops.mesh.primitive_cylinder_add(vertices=192, radius=radius, depth=depth,
        rotation=(math.pi / 2, 0, 0), location=(0,y,0))
    obj = bpy.context.object
    obj.name = name
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    obj.data.materials.append(metal)
    return obj

body = cylinder('Body', .11, .007, 0)
mod = body.modifiers.new('Exterior round 1.1mm', 'BEVEL')
mod.width = .0011
mod.segments = 8
mod.limit_method = 'ANGLE'
bpy.ops.object.modifier_apply(modifier=mod.name)
for poly in body.data.polygons:
    poly.use_smooth = abs(poly.normal.y) < .99999
# Placeholder bounds for SVG substitution; the silhouette of Body stays circular.
cylinder('Decoration', .04, .0008, -.0044)
tex = bpy.data.materials.new('Classic Mix')
tex.use_nodes = True
image = bpy.data.images.new('Demo texture', width=256, height=256, alpha=True)
pixels = []
for y in range(256):
    for x in range(256):
        pixels.extend((.03 + .02*y/255, .16 + .12*x/255, .25 + .10*y/255, 1))
image.pixels.foreach_set(pixels)
image.filepath_raw = str(ROOT / 'assets/demo/texture.png')
image.file_format = 'PNG'
image.save()
image.pack()
node = tex.node_tree.nodes.new('ShaderNodeTexImage')
node.image = image
tex.node_tree.links.new(node.outputs['Color'],tex.node_tree.nodes.get('Principled BSDF').inputs['Base Color'])
tex.node_tree.nodes.get('Principled BSDF').inputs['Roughness'].default_value = .3
# A separate inset color face, intentionally smaller than the one-piece metal body.
face = cylinder('ColorSurface', .093, .0002, -.00355)
face.data.materials.clear()
face.data.materials.append(tex)
uv = face.data.uv_layers.active or face.data.uv_layers.new(name='UVMap')
for poly in face.data.polygons:
    for li in poly.loop_indices:
        v = face.data.vertices[face.data.loops[li].vertex_index].co
        uv.data[li].uv = (v.x/.186+.5,v.z/.186+.5)
bpy.ops.wm.save_as_mainfile(filepath=str(ROOT / 'assets/demo/template.blend'))
