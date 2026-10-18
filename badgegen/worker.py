"""Blender-side worker. Run only with --background --disable-autoexec."""
import json
import math
import hashlib
import shutil
import sys
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path

import bpy
from mathutils import Matrix, Vector


def bounds(objects):
    points = []
    graph = bpy.context.evaluated_depsgraph_get()
    for obj in objects:
        if obj.type not in {"MESH", "CURVE", "FONT"}:
            continue
        evaluated = obj.evaluated_get(graph)
        mesh = evaluated.to_mesh()
        points.extend(evaluated.matrix_world @ v.co for v in mesh.vertices)
        evaluated.to_mesh_clear()
    if not points or not all(math.isfinite(v) for point in points for v in point):
        raise ValueError("Empty or nonfinite geometry")
    return [[min(p[i] for p in points), max(p[i] for p in points)] for i in range(3)]


def geometry():
    return [o for o in bpy.context.scene.objects if o.type in {"MESH", "CURVE", "FONT"} and not o.hide_render]


def scene_stats():
    objects = geometry()
    images = []
    materials = {m for o in objects for m in o.data.materials if m}
    for mat in materials:
        if mat.node_tree:
            for node in mat.node_tree.nodes:
                if node.type == "UVMAP" and node.uv_map:
                    for obj in objects:
                        if mat in list(obj.data.materials) and obj.type == "MESH" and node.uv_map not in obj.data.uv_layers:
                            raise ValueError("Material UV reference " + node.uv_map + " missing on " + obj.name)
                if node.type == "TEX_IMAGE":
                    img = node.image
                    if img is None or min(img.size) == 0:
                        raise ValueError("Missing texture in material " + mat.name)
                    images.append({"material": mat.name, "size": list(img.size),
                        "packed_sha256": hashlib.sha256(img.packed_file.data).hexdigest() if img.packed_file else None})
    return {"objects": [{"name": o.name, "type": o.type,
                          "materials": [m.name for m in o.data.materials if m]} for o in objects],
            "bounds_m": bounds(objects), "materials": sorted(m.name for m in materials), "images": images}


def sanitize_svg(source, destination):
    """Interpret paint as 3D material; retain contours and their transforms."""
    raw = Path(source).read_text()
    if "<!DOCTYPE" in raw.upper() or "<!ENTITY" in raw.upper():
        raise ValueError("SVG entities/DTDs are not supported")
    root = ET.fromstring(raw)
    ns = "{http://www.w3.org/2000/svg}"
    ET.register_namespace("", ns[1:-1])
    warnings, seen = [], set()
    shapes = {"path", "rect", "circle", "ellipse", "polygon"}

    def clean(element, transforms=()):
        tag = element.tag.split("}")[-1]
        if tag in {"defs", "metadata", "title", "desc"}:
            return None
        if tag not in shapes | {"svg", "g"}:
            raise ValueError("Unsupported SVG element: " + tag + "; flatten to closed vector contours first")
        style = dict(item.split(":", 1) for item in element.get("style", "").split(";") if ":" in item)
        style = {k.strip(): v.strip() for k, v in style.items()}
        if element.get("display", style.get("display")) == "none" or element.get("visibility", style.get("visibility")) == "hidden":
            return None
        for key in ("clip-path", "mask"):
            if element.get(key, style.get(key, "none")) != "none":
                raise ValueError("SVG masks/clips must be flattened before extrusion")
        if element.get("stroke", style.get("stroke", "none")) != "none":
            raise ValueError("Outline SVG strokes before extrusion")
        if element.get("filter", style.get("filter", "none")) != "none":
            warnings.append("SVG shadow/filter omitted: lighting is provided by the 3D scene")
        transforms += (element.get("transform", ""),)
        attrs = {k: v for k, v in element.attrib.items() if k in {
            "d", "x", "y", "width", "height", "viewBox", "preserveAspectRatio", "transform",
            "cx", "cy", "r", "rx", "ry", "points", "fill-rule"}}
        if "fill-rule" in style:
            attrs["fill-rule"] = style["fill-rule"]
        if tag == "path":
            import re
            commands = re.findall(r"[A-DF-Za-df-z]", element.get("d", ""))
            if not commands or commands[-1].upper() != "Z" or any(a.upper() != "Z" and b.upper() == "M" for a, b in zip(commands, commands[1:])):
                raise ValueError("SVG paths must have closed subpaths")
        if tag in shapes:
            key = (tag, tuple(sorted(attrs.items())), transforms)
            if key in seen:
                warnings.append("Duplicate painted contour merged into one solid")
                return None
            seen.add(key)
            attrs["fill"] = "#000000"
        result = ET.Element(ns + tag, attrs)
        for child in element:
            converted = clean(child, transforms)
            if converted is not None:
                result.append(converted)
        return result

    result = clean(root)
    if not seen:
        raise ValueError("SVG has no supported filled geometry")
    ET.ElementTree(result).write(destination, encoding="utf-8", xml_declaration=True)
    return sorted(set(warnings))


def replace_layer(layer, temp_dir):
    target = bpy.context.scene.objects.get(layer["target"])
    if target is None or target.type not in {"MESH", "CURVE"}:
        raise ValueError("Missing template layer " + layer["target"])
    material = bpy.data.materials.get(layer["material"])
    if material is None:
        raise ValueError("Missing template material " + layer["material"])
    box = bounds([target])
    sanitized = temp_dir / (layer["target"].replace("/", "_") + ".svg")
    warnings = sanitize_svg(layer["svg"], sanitized)
    before = set(bpy.data.objects)
    bpy.ops.import_curve.svg(filepath=str(sanitized))
    imported = [o for o in bpy.data.objects if o not in before]
    if not imported or any(o.type != "CURVE" for o in imported):
        raise ValueError("SVG importer did not produce curves")
    bpy.context.view_layer.update()
    for obj in imported:
        obj.data.transform(obj.matrix_world)
        obj.matrix_world = Matrix.Identity(4)
        obj.data.resolution_u = layer.get("resolution", 12)
    bpy.context.view_layer.update()
    svg_box = bounds(imported)
    if svg_box[0][1] <= svg_box[0][0] or svg_box[1][1] <= svg_box[1][0]:
        raise ValueError("Degenerate SVG bounds")
    sx = (box[0][1] - box[0][0]) / (svg_box[0][1] - svg_box[0][0])
    sy = (box[2][1] - box[2][0]) / (svg_box[1][1] - svg_box[1][0])
    if layer.get("fit") == "template_width_top":
        sy = sx
    matrix = Matrix(((sx, 0, 0, box[0][0] - sx * svg_box[0][0]),
                     (0, sy, 0, box[2][1] - sy * svg_box[1][1]),
                     (0, 0, 1, 0), (0, 0, 0, 1)))
    bpy.data.objects.remove(target, do_unlink=True)
    straight_segments = 0
    for index, obj in enumerate(imported):
        obj.name = layer["target"] if index == 0 else layer["target"] + "_" + str(index)
        obj.data.transform(matrix)
        # SVG straight cubic segments can have both handles collapsed onto
        # their endpoints. Sampling these as curves creates tiny uneven edges
        # whose bevel miters fold. VECTOR handles trace exactly the same line.
        for spline in obj.data.splines:
            if spline.type != "BEZIER":
                continue
            points = list(spline.bezier_points)
            following = points[1:] + points[:1] if spline.use_cyclic_u else points[1:]
            for a, b in zip(points, following):
                if (a.handle_right-a.co).length < 1e-10 and (b.handle_left-b.co).length < 1e-10:
                    a.handle_right_type = "VECTOR"
                    b.handle_left_type = "VECTOR"
                    straight_segments += 1
        obj.data.dimensions = "2D"
        obj.data.fill_mode = "BOTH"
        bevel = layer.get("bevel_m", 0)
        obj.data.bevel_depth = bevel
        obj.data.bevel_resolution = 3 if bevel else 0
        obj.data.extrude = layer["thickness_m"] / 2 - bevel
        for spline in obj.data.splines:
            spline.use_smooth = True
            # Curve.transform scales point radii too. Thickness is specified in
            # meters, independent of the SVG's coordinate scale.
            for point in list(spline.bezier_points) + list(spline.points):
                point.radius = 1.0
        obj.rotation_euler = (math.pi / 2, 0, 0)
        obj.location = (0, sum(box[1]) / 2, 0)
        obj.data.materials.clear()
        obj.data.materials.append(material)
    bpy.context.view_layer.update()
    if "clip_svg" in layer:
        names = [obj.name for obj in imported]
        # Use an explicitly supplied filled outline as a closed clipping solid.
        # Its placement comes from the original target, not from the oversized
        # unclipped SVG (for example the 25Q3 lower name plate).
        bpy.ops.mesh.primitive_cube_add(size=1, location=[sum(v) / 2 for v in box])
        clip = bpy.context.object
        clip.name = "__badge_clip"
        clip.dimensions = [v[1] - v[0] for v in box]
        bpy.context.view_layer.update()
        clip_result = replace_layer({"target": clip.name, "svg": layer["clip_svg"],
            "material": layer["material"], "thickness_m": layer["thickness_m"] * 4,
            "resolution": layer.get("resolution", 12)}, temp_dir)
        clip_objects = [o for o in bpy.context.scene.objects if o.name.startswith("__badge_clip")]
        if len(clip_objects) != 1:
            raise ValueError("clip_svg must contain one filled outline")
        clip = clip_objects[0]
        bpy.ops.object.select_all(action="DESELECT")
        for obj in [bpy.data.objects[n] for n in names] + [clip]:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = clip
        bpy.ops.object.convert(target="MESH")
        clip = bpy.data.objects["__badge_clip"]
        imported = [bpy.data.objects[n] for n in names]
        for obj in imported:
            modifier = obj.modifiers.new("ClipToBadgeOutline", "BOOLEAN")
            modifier.operation = "INTERSECT"
            modifier.solver = "EXACT"
            modifier.object = clip
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.modifier_apply(modifier=modifier.name)
        bpy.data.objects.remove(clip, do_unlink=True)
        warnings.append("Geometry clipped to explicit badge outline")
        bpy.context.view_layer.update()
    actual = bounds(imported)
    if abs(actual[1][1] - actual[1][0] - layer["thickness_m"]) > 1e-6:
        raise ValueError("SVG extrusion thickness differs from the recipe")
    return {"target": layer["target"], "source": layer["svg"], "warnings": warnings,
            "bounds_m": actual, "reference_bounds_m": box,
            "straight_segments_canonicalized": straight_segments}


def import_asset(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if Path(path).suffix == ".glb":
        bpy.ops.import_scene.gltf(filepath=str(path))
    else:
        # Blender's USDZ importer reuses same-named extracted textures across
        # imports in one process. Remove only this worker's extraction cache.
        # Every previously imported scene has already been discarded above.
        shutil.rmtree(Path(bpy.app.tempdir) / "usd_textures_tmp", ignore_errors=True)
        bpy.ops.wm.usd_import(filepath=str(path), import_cameras=False, import_lights=False)
    bpy.context.view_layer.update()
    return scene_stats()


def prepare_solid(obj):
    """Use world-space meters, weld imported cap/side seams, preserve UV data."""
    import bmesh
    obj.data = obj.data.copy()
    obj.data.transform(obj.matrix_world)
    obj.matrix_world = Matrix.Identity(4)
    mesh = bmesh.new()
    mesh.from_mesh(obj.data)
    bmesh.ops.remove_doubles(mesh, verts=list(mesh.verts), dist=1e-7)
    bmesh.ops.recalc_face_normals(mesh, faces=list(mesh.faces))
    mesh.to_mesh(obj.data)
    mesh.free()
    obj.data.update()
    bpy.context.view_layer.update()


def back_hit(obj, x, z, y):
    hit, point, normal, index = obj.ray_cast(Vector((x, y, z)), Vector((0, -1, 0)))
    return point.y if hit else None


def lock_planar_cap_normals(obj):
    """Keep the polished XZ faces optically flat across Boolean triangles.

    Weighted normals alone average narrow cap triangles with bevel normals;
    smooth interpolation then creates streaks despite coplanar geometry.
    Preserve their result on curved edges, but author constant cap corners.
    """
    mesh = obj.data
    caps = []
    for face in mesh.polygons:
        if face.area > 1e-12 and abs(face.normal.y) > .99:
            ys = [mesh.vertices[i].co.y for i in face.vertices]
            if max(ys) - min(ys) < 1e-7:
                face.use_smooth = False
                caps.append(face)
    # Split the normal fans at cap/bevel borders. Without this, Blender may
    # average explicitly supplied loop normals back into the same smooth fan.
    cap_ids = {f.index for f in caps}
    edge_faces = {}
    for face in mesh.polygons:
        for loop_index in face.loop_indices:
            edge_faces.setdefault(mesh.loops[loop_index].edge_index, set()).add(face.index in cap_ids)
    for edge_index, kinds in edge_faces.items():
        if len(kinds) > 1:
            mesh.edges[edge_index].use_edge_sharp = True
    mesh.update()
    normals = [tuple(n.vector) for n in mesh.corner_normals]
    for face in caps:
        normal = (0.0, 1.0 if face.normal.y > 0 else -1.0, 0.0)
        for i in face.loop_indices:
            normals[i] = normal
    mesh.normals_split_custom_set(normals)
    mesh.update()
    return len(caps)


def check_exported_engraving(engravings):
    """Measure the actual reimported models, including the final bevels."""
    checks = []
    for spec in engravings:
        heights = []
        for x, z in spec["probes_xz"]:
            origin = Vector((x, spec["surface_y_m"] + .01, z))
            hits = []
            for obj in geometry():
                inverse = obj.matrix_world.inverted()
                hit, point, _, _ = obj.ray_cast(inverse @ origin, inverse.to_3x3() @ Vector((0, -1, 0)))
                if hit:
                    hits.append((obj.matrix_world @ point).y)
            if not hits:
                raise ValueError("Exported engraving has a hole through the backing")
            heights.append(max(hits))
        error = max(abs(y - spec["floor_y_m"]) for y in heights)
        if error > 1e-5:
            raise ValueError("Exported engraving depth differs by " + str(error))
        checks.append({"target": spec["target"], "depth_m": spec["depth_m"],
                       "max_floor_error_m": error, "probe_count": len(heights)})
    return checks


def check_exported_rounds(bevels):
    checks = []
    for spec in bevels:
        samples = spec.get("radius_samples", [])
        if not samples:
            continue
        max_error = 0.0
        for sample in samples:
            expected = Vector(sample["point"])
            direction = Vector(sample["inward"])
            origin = expected-direction*sample["radius_m"]
            hits = []
            for obj in geometry():
                inverse = obj.matrix_world.inverted()
                hit, point, _, _ = obj.ray_cast(inverse @ origin, inverse.to_3x3() @ direction,
                                               distance=sample["radius_m"]*2)
                if hit:
                    hits.append((obj.matrix_world @ point-expected).length)
            error = min(hits, default=float("inf"))
            max_error = max(max_error, error)
            if error > sample["radius_m"]*.03+2e-6:
                raise ValueError("Exported outer R profile differs from its quarter circle: " + str(error))
        checks.append({"target": spec["target"], "sample_count": len(samples),
                       "max_profile_error_m": max_error, "sample_angle_degrees": 45})
    return checks


def read_exported_triangles(path):
    """Read serialized normals directly; importers may weld thin triangle fans."""
    import struct
    batches = []
    if Path(path).suffix == ".usdz":
        from pxr import Usd, UsdGeom
        import numpy as np
        stage = Usd.Stage.Open(path)
        for prim in stage.Traverse():
            if prim.GetTypeName() != "Mesh":
                continue
            mesh = UsdGeom.Mesh(prim)
            # Bulk conversion avoids repeated Python/Gf vector protocol calls.
            points = [Vector(v) for v in np.asarray(mesh.GetPointsAttr().Get()).tolist()]
            normals = np.asarray(mesh.GetNormalsAttr().Get()).tolist()
            mode = mesh.GetNormalsInterpolation()
            indices = np.asarray(mesh.GetFaceVertexIndicesAttr().Get()).tolist()
            triangles, start = [], 0
            for face, count in enumerate(np.asarray(mesh.GetFaceVertexCountsAttr().Get()).tolist()):
                if count != 3:
                    raise ValueError("Normal validation requires triangulated exports")
                ids = indices[start:start + count]
                ns = [normals[start+k] if mode == "faceVarying" else normals[i]
                      if mode in {"vertex", "varying"} else normals[face] for k, i in enumerate(ids)]
                triangles.append(([points[i] for i in ids], [Vector(n) for n in ns]))
                start += count
            batches.append((str(prim.GetPath()), points, triangles))
    else:
        data = Path(path).read_bytes()
        json_length = struct.unpack_from("<I", data, 12)[0]
        doc = json.loads(data[20:20 + json_length])
        binary_start = 20 + json_length + 8
        def accessor(index):
            spec = doc["accessors"][index]
            view = doc["bufferViews"][spec["bufferView"]]
            count = {"SCALAR": 1, "VEC3": 3}[spec["type"]]
            code = {5121: "B", 5123: "H", 5125: "I", 5126: "f"}[spec["componentType"]]
            fmt = "<" + code * count
            offset = binary_start + view.get("byteOffset", 0) + spec.get("byteOffset", 0)
            stride = view.get("byteStride", struct.calcsize(fmt))
            return [struct.unpack_from(fmt, data, offset + i * stride) for i in range(spec["count"])]
        for mesh in doc["meshes"]:
            for primitive in mesh["primitives"]:
                if primitive.get("mode", 4) != 4:
                    raise ValueError("Normal validation requires triangle primitives")
                points = [Vector(v) for v in accessor(primitive["attributes"]["POSITION"])]
                normals = [Vector(v) for v in accessor(primitive["attributes"]["NORMAL"])]
                ids = [v[0] for v in accessor(primitive["indices"])] if "indices" in primitive else list(range(len(points)))
                triangles = [([points[i] for i in ids[k:k+3]], [normals[i] for i in ids[k:k+3]])
                             for k in range(0, len(ids), 3)]
                batches.append((mesh.get("name", "mesh"), points, triangles))
    return batches


def check_exported_cap_normals(path):
    checks = []
    batches = read_exported_triangles(path)
    for name, points, triangles in batches:
        # Finishing bakes object transforms. The thinnest local dimension is
        # the badge depth; glTF's Y-up conversion changes its coordinate axis.
        axis = min(range(3), key=lambda a: max(p[a] for p in points)-min(p[a] for p in points))
        corners, max_angle = 0, 0.0
        for ps, ns in triangles:
            cross = (ps[1]-ps[0]).cross(ps[2]-ps[0])
            if cross.length / 2 <= 1e-10 or max(p[axis] for p in ps)-min(p[axis] for p in ps) >= 1e-8:
                continue
            if abs(cross.normalized()[axis]) <= .99:
                continue
            sign = 1 if cross[axis] > 0 else -1
            for normal in ns:
                dot = normal.normalized()[axis] * sign
                max_angle = max(max_angle, math.degrees(math.acos(max(-1, min(1, dot)))))
                corners += 1
        if corners:
            if max_angle > .1:
                raise ValueError("Exported planar normals deviate on " + name + ": " + str(max_angle))
            checks.append({"object": name, "corner_count": corners,
                           "max_deviation_degrees": max_angle, "source": "serialized_normals"})
    if not checks:
        raise ValueError("No exported planar caps found for normal validation")
    return checks


def check_exported_round_surfaces(path, bodies, bevels):
    """Check every outer round triangle, including strips missed by sparse rays."""
    import numpy as np
    specs = [(body, bevel) for body in bodies for bevel in bevels
             if body["target"] == bevel["target"] and "outer_width_m" in bevel]
    if not specs:
        return []
    batches = read_exported_triangles(path)
    checks = []
    for body, bevel in specs:
        outline = np.array(body["outline_xz"], dtype=float)
        delta = np.roll(outline, -1, axis=0)-outline
        lengths = np.sum(delta*delta, axis=1)
        outward = np.column_stack((delta[:, 1], -delta[:, 0]))
        outward /= np.linalg.norm(outward, axis=1)[:, None]
        tangents = outward + np.roll(outward, 1, axis=0)
        tangents /= np.linalg.norm(tangents, axis=1)[:, None]
        radius = bevel["outer_width_m"]
        for side, plane, sign in (("front", body["front_y_m"], -1), ("back", body["rear_y_m"], 1)):
            count, flipped, max_angle, max_error = 0, 0, 0.0, 0.0
            for name, _, triangles in batches:
                if body["target"] not in name:
                    continue
                points = np.array([[tuple(v) for v in ps] for ps, _ in triangles], dtype=float)
                normals = np.array([[tuple(v) for v in ns] for _, ns in triangles], dtype=float)
                if Path(path).suffix == ".glb":
                    points = points[:, :, [0, 2, 1]]; points[:, :, 1] *= -1
                    normals = normals[:, :, [0, 2, 1]]; normals[:, :, 1] *= -1
                depth = (plane-points[:, :, 1])*sign
                candidate = (np.ptp(points[:, :, 1], axis=1) > 1e-7) & np.all(
                    (depth >= -1e-7) & (depth <= radius+2e-6), axis=1)
                points, normals, depth = points[candidate], normals[candidate], depth[candidate]
                if not len(points):
                    continue
                xz = points[:, :, [0, 2]].reshape(-1, 2)
                distances, directions = [], []
                for start in range(0, len(xz), 2048):
                    diff = xz[start:start+2048, None, :]-outline[None, :, :]
                    t = np.clip(np.sum(diff*delta[None, :, :], axis=2)/lengths, 0, 1)
                    distance = np.linalg.norm(diff-t[:, :, None]*delta[None, :, :], axis=2)
                    nearest = np.argmin(distance, axis=1)
                    rows = np.arange(len(nearest))
                    distances.append(distance[rows, nearest])
                    t = t[rows, nearest, None]
                    directions.append(tangents[nearest]*(1-t)+tangents[(nearest+1)%len(outline)]*t)
                distance = np.concatenate(distances).reshape(-1, 3)
                direction = np.concatenate(directions).reshape(-1, 3, 2)
                selected = np.all(distance <= radius+2e-6, axis=1)
                points, normals, depth, distance, direction = [v[selected] for v in (points, normals, depth, distance, direction)]
                if not len(points):
                    continue
                direction /= np.linalg.norm(direction, axis=2)[:, :, None]
                expected = np.stack((direction[:, :, 0]*(radius-distance), sign*(radius-depth),
                                     direction[:, :, 1]*(radius-distance)), axis=2)
                expected /= np.linalg.norm(expected, axis=2)[:, :, None]
                cross = np.cross(points[:, 1]-points[:, 0], points[:, 2]-points[:, 0])
                flipped += int(np.count_nonzero(np.sum(cross*expected.mean(axis=1), axis=1) <= 0))
                normals /= np.linalg.norm(normals, axis=2)[:, :, None]
                angle = np.degrees(np.arccos(np.clip(np.sum(normals*expected, axis=2), -1, 1)))
                max_angle = max(max_angle, float(np.max(angle)))
                error = np.abs(np.sqrt((radius-distance)**2+(radius-depth)**2)-radius)
                max_error = max(max_error, float(np.max(error)))
                count += len(points)
            if not count or flipped or max_angle > 12 or max_error > 2e-6:
                raise ValueError(f"Exported {side} round surface invalid: triangles={count}, flipped={flipped}, "
                                 f"normal_deviation={max_angle:.3f} degrees, radial_error={max_error:.9f} m")
            checks.append({"target": body["target"], "side": side, "triangle_count": count,
                           "flipped_triangles": flipped, "max_normal_deviation_degrees": max_angle,
                           "max_radial_error_m": max_error, "source": "serialized_triangles_and_normals"})
    return checks


def round_edge_weights(obj, radius, body=None, outer_radius=None, thin_radius=None, thin_feature=None):
    """Use separate display-scale body rounds and fine decorative edge rounds."""
    import bmesh
    from mathutils.bvhtree import BVHTree
    from mathutils.kdtree import KDTree
    from collections import defaultdict
    if outer_radius is not None and body is None:
        raise ValueError("Outer radius requires a mapped continuous body")
    outline = [Vector((x, 0, z)) for x, z in body["outline_xz"]] if body else []
    tree_outline = KDTree(len(outline)) if outline else None
    if outline:
        for i, p in enumerate(outline):
            tree_outline.insert(p, i)
        tree_outline.balance()
    def on_outline(p):
        point = Vector((p.x, 0, p.z))
        for _, i, _ in tree_outline.find_n(point, 3):
            for j in (i, (i-1)%len(outline)):
                a, b = outline[j], outline[(j+1)%len(outline)]
                d = b-a
                if d.length_squared < 1e-16:
                    continue
                t = max(0, min(1, (point-a).dot(d)/d.length_squared))
                if (point-a-d*t).length < 1e-7:
                    return True
        return False
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.normal_update()
    tree = BVHTree.FromBMesh(bm)
    layer = bm.edges.layers.float.get("bevel_weight_edge") or bm.edges.layers.float.new("bevel_weight_edge")
    modifier_radius = max(radius, outer_radius or radius)
    radii, regions, samples = [], defaultdict(list), []
    front_segments = []
    if body:
        front_caps = {f for f in bm.faces if all(abs(v.co.y-body["front_y_m"]) < 1e-7 for v in f.verts)}
        front_segments = [(e.verts[0].co.copy(), e.verts[1].co.copy()) for e in bm.edges
                          if sum(f in front_caps for f in e.link_faces) == 1]
    back_segments = list(zip(outline, outline[1:]+outline[:1]))
    def planar_clearance(point, direction, segments):
        # Double-precision 2D intersections avoid float BVH self-hits at tiny
        # SVG outline edges. The body wall spans the complete badge thickness.
        nearest = float("inf")
        for a, b in segments:
            sx, sz = float(b.x)-a.x, float(b.z)-a.z
            ax, az = float(a.x)-point.x, float(a.z)-point.z
            determinant = direction.x*sz-direction.z*sx
            if abs(determinant) < 1e-16:
                continue
            distance = (ax*sz-az*sx)/determinant
            segment_t = (ax*direction.z-az*direction.x)/determinant
            if distance > 1e-6 and -1e-8 <= segment_t <= 1+1e-8:
                nearest = min(nearest, distance)
        return nearest if math.isfinite(nearest) else 0
    for edge in bm.edges:
        edge[layer] = 0
        if len(edge.link_faces) != 2:
            continue
        caps = [f for f in edge.link_faces if abs(f.normal.y) > .99999]
        if len(caps) != 1:
            continue
        cap = caps[0]
        side = next(f for f in edge.link_faces if f != cap)
        inward = Vector((-side.normal.x, 0, -side.normal.z))
        if inward.length < 1e-6:
            continue
        inward.normalize()
        midpoint = (edge.verts[0].co+edge.verts[1].co)/2
        outer = bool(outer_radius is not None and on_outline(midpoint)
                     and all(on_outline(v.co) for v in edge.verts))
        role = ("outer_front" if cap.normal.y < 0 else "outer_back") if outer else "detail"
        requested = outer_radius if outer else radius
        wall_height = max(v.co.y for v in side.verts)-min(v.co.y for v in side.verts)
        if outer:
            wall_height = body["rear_y_m"]-body["front_y_m"]
        local_radius = min(requested, wall_height*.45)
        if local_radius < 1e-7:
            continue
        inset, clearance = min(1e-6, local_radius*.01), float("inf")
        for t in (.1, .5, .9):
            p = edge.verts[0].co.lerp(edge.verts[1].co, t)
            if outer:
                distance = planar_clearance(p, inward, front_segments if cap.normal.y < 0 else back_segments)
            else:
                origin = p+inward*inset-Vector((0, cap.normal.y*min(wall_height*.2, requested*.5), 0))
                _, _, _, distance = tree.ray_cast(origin, inward)
                distance = distance+inset if distance is not None else 0
            clearance = min(clearance, distance)
        if not outer and thin_radius is not None and clearance < thin_feature:
            role, requested = "thin_detail", thin_radius
        local_radius = min(requested, wall_height*.45, clearance*.45)
        edge[layer] = local_radius/modifier_radius
        if local_radius > 0:
            radii.append((local_radius, requested))
            regions[role].append((local_radius, requested))
            if outer and local_radius >= requested*.999 and edge.calc_length() > .0002:
                # The 45-degree point lies on the 8-segment quarter circle.
                offset = local_radius*(1-math.sqrt(.5))
                point = midpoint+inward*offset-Vector((0, cap.normal.y*offset, 0))
                samples.append({"region": role, "point": list(point), "inward": list(inward), "radius_m": local_radius})
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    if not radii:
        raise ValueError("No supported cap edges for circular R finishing")
    summary = {role: {"edge_count": len(values), "full_radius_edge_count": sum(r >= req*.999 for r, req in values),
                      "requested_radius_m": values[0][1], "min_radius_m": min(r for r, _ in values),
                      "max_radius_m": max(r for r, _ in values)} for role, values in regions.items()}
    if outer_radius is not None and not {"outer_front", "outer_back"}.issubset(summary):
        raise ValueError("Could not identify both external body rims")
    return {"rounded_edge_count": len(radii), "full_radius_edge_count": sum(r >= req*.999 for r, req in radii),
            "max_edge_radius_m": max(r for r, _ in radii), "modifier_radius_m": modifier_radius,
            "radius_regions": summary, "radius_samples": samples[::max(1, len(samples)//64)]}


def clean_finished_mesh(obj):
    """Join flat metal topology while preserving every curved surface triangle."""
    import bmesh
    from collections import Counter, defaultdict
    removed_uvs = []
    if all(mat and mat.use_nodes and not any(n.type in {"TEX_IMAGE", "GROUP"}
           for n in mat.node_tree.nodes) for mat in obj.data.materials):
        for uv in list(obj.data.uv_layers):
            removed_uvs.append(uv.name)
            obj.data.uv_layers.remove(uv)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.triangulate(bm, faces=list(bm.faces), quad_method="BEAUTY", ngon_method="BEAUTY")
    bm.normal_update()
    # SVG transforms leave nanometer-scale Y noise on nominally flat caps.
    # Snap each already planar group to its median plane before retessellation.
    plane_groups = defaultdict(set)
    for face in bm.faces:
        ys = [v.co.y for v in face.verts]
        if abs(face.normal.y) > .99999 and max(ys)-min(ys) < 1e-8:
            plane_groups[round(sum(ys)/len(ys), 6)].update(face.verts)
    max_plane_snap = 0
    for vertices in plane_groups.values():
        values = sorted(v.co.y for v in vertices)
        plane = values[len(values)//2]
        for vertex in vertices:
            max_plane_snap = max(max_plane_snap, abs(vertex.co.y-plane))
            vertex.co.y = plane
    if max_plane_snap > 1e-7:
        bm.free()
        raise ValueError("Planar cleanup exceeds 0.1 micrometer snap tolerance")
    bm.normal_update()
    def signature():
        curves, edges, areas = Counter(), Counter(), defaultdict(float)
        triangles = bm.calc_loop_triangles()
        for tri in triangles:
            points = [loop.vert.co for loop in tri]
            keys = [tuple(round(c, 8) for c in point) for point in points]
            cross = (points[1]-points[0]).cross(points[2]-points[0])
            material = tri[0].face.material_index
            y_span = max(p.y for p in points)-min(p.y for p in points)
            if y_span < 1e-10 or (cross.length > 2e-14 and abs(cross.normalized().y) > .99999 and y_span < 1e-8):
                group = (material, round(sum(p.y for p in points)/3, 7))
                u = [float(points[1][i])-float(points[0][i]) for i in range(3)]
                v = [float(points[2][i])-float(points[0][i]) for i in range(3)]
                areas[group] += math.sqrt(sum((u[(i+1)%3]*v[(i+2)%3]-u[(i+2)%3]*v[(i+1)%3])**2 for i in range(3))) / 2
                for i in range(3):
                    edges[(group, tuple(sorted((keys[i], keys[(i+1)%3]))))] += 1
            else:
                curves[(material, tuple(sorted(keys)))] += 1
        border = Counter({e: count for e, count in edges.items() if count != 2})
        stats = {"vertices": len(bm.verts), "faces": len(bm.faces), "triangles": len(triangles),
                 "boundary_edges": sum(e.is_boundary for e in bm.edges),
                 "nonmanifold_edges": sum(not e.is_manifold for e in bm.edges)}
        return stats, curves, border, areas
    before, curves_before, border_before, areas_before = signature()
    preserved = bm.copy()
    # Only weld essentially identical points; never collapse short R edges.
    bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-9)
    caps = {f for f in bm.faces if abs(f.normal.y) > .99999 and
            max(v.co.y for v in f.verts)-min(v.co.y for v in f.verts) < 1e-8}
    cap_edges = [e for e in bm.edges if len(e.link_faces) == 2 and all(f in caps for f in e.link_faces)
                 and not e.seam and e.link_faces[0].material_index == e.link_faces[1].material_index
                 and e.link_faces[0].normal.dot(e.link_faces[1].normal) > .999999]
    if not bm.loops.layers.uv:
        bmesh.ops.dissolve_edges(bm, edges=cap_edges, use_verts=False, use_face_split=False)
    loose_edges = [e for e in bm.edges if not e.link_faces]
    if loose_edges:
        bmesh.ops.delete(bm, geom=loose_edges, context="EDGES")
    loose_verts = [v for v in bm.verts if not v.link_faces]
    if loose_verts:
        bmesh.ops.delete(bm, geom=loose_verts, context="VERTS")
    bm.normal_update()
    after, curves_after, border_after, areas_after = signature()
    # A float BVH is unreliable on skinny SVG triangles. Instead verify exact
    # curved triangles plus planar boundaries/material regions and their areas.
    area_error = max(abs(areas_before[k]-areas_after[k]) for k in areas_before.keys() | areas_after.keys())
    topology_merged = True
    if (curves_before != curves_after or border_before != border_after or area_error > 1e-9
            or after["triangles"] > before["triangles"]
            or after["nonmanifold_edges"] > before["nonmanifold_edges"]):
        # Concave SVG/Boolean caps can tessellate differently as a large ngon.
        # Keep their original triangles rather than accepting a changed surface.
        bm.free()
        bm = preserved
        topology_merged = False
        after, curves_after, border_after, areas_after = signature()
        area_error = 0.0
    else:
        preserved.free()
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return {"target": obj.name, "before": before, "after": after,
            "max_planar_snap_m": max_plane_snap,
            "planar_merge_applied": topology_merged,
            "unused_uv_layers_removed": removed_uvs, "curved_triangles_preserved": True,
            "planar_boundaries_preserved": True, "max_planar_area_error_m2": area_error}


def mesh_connectivity(obj):
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    unseen = set(bm.verts)
    components = 0
    while unseen:
        components += 1
        pending = [unseen.pop()]
        while pending:
            for edge in pending.pop().link_edges:
                for vertex in edge.verts:
                    if vertex in unseen:
                        unseen.remove(vertex)
                        pending.append(vertex)
    result = {"components": components, "nonmanifold_edges": sum(not e.is_manifold for e in bm.edges)}
    bm.free()
    return result


def rebuild_continuous_body(spec, edge_radius):
    """Derive the entire backing from the current rim's exact outer vertices."""
    import bmesh
    from collections import defaultdict
    from mathutils.kdtree import KDTree
    target, backing, surface = [bpy.data.objects.get(spec[k]) for k in ("target", "backing", "surface")]
    if any(o is None or o.type != "MESH" for o in (target, backing, surface)):
        raise ValueError("Continuous body requires a metal rim, backing and textured surface")
    prepare_solid(target)
    # The template texture slab has a reflected, highly nonuniform transform.
    # Bake it to the same meter coordinates as the body: native USD viewers can
    # otherwise show depth artifacts against the nearby supporting surface.
    prepare_solid(surface)
    rear = bounds([backing])[1][1]
    floor = bounds([surface])[1][1]
    box = bounds([target])
    front = box[1][0]
    if not front < floor < rear or box[1][1] >= rear - 1e-5:
        raise ValueError("Rim must overlap the supported surface and end before the rear plane")
    bm = bmesh.new()
    bm.from_mesh(target.data)
    bm.normal_update()
    caps = {f for f in bm.faces if all(abs(v.co.y-front) < 1e-7 for v in f.verts)}
    boundary = [e for e in bm.edges if sum(f in caps for f in e.link_faces) == 1]
    adjacent = defaultdict(list)
    for edge in boundary:
        a, b = edge.verts
        adjacent[a].append(b)
        adjacent[b].append(a)
    if not adjacent or any(len(vs) != 2 for vs in adjacent.values()):
        bm.free()
        raise ValueError("Rim front must contain closed, nonbranching planar contours")
    loops, unseen = [], set(adjacent)
    while unseen:
        # Stable traversal keeps export fingerprints useful between rebuilds.
        start = min(unseen, key=lambda v: tuple(v.co))
        points, previous, current = [], None, start
        while True:
            points.append(current.co.copy())
            unseen.discard(current)
            following = min((v for v in adjacent[current] if v != previous), key=lambda v: tuple(v.co))
            previous, current = current, following
            if current == start:
                break
        loops.append(points)
    area = lambda ps: sum(a.x*b.z-b.x*a.z for a, b in zip(ps, ps[1:]+ps[:1])) / 2
    outline = max(loops, key=lambda ps: abs(area(ps)))
    if area(outline) < 0:
        outline.reverse()
    # Require all other contours to belong to this one surrounding silhouette.
    def inside(point):
        result = False
        for a, b in zip(outline, outline[1:]+outline[:1]):
            if (a.z > point.z) != (b.z > point.z):
                if point.x < (b.x-a.x)*(point.z-a.z)/(b.z-a.z)+a.x:
                    result = not result
        return result
    if any(not inside(p) for loop in loops if loop is not outline and abs(area(loop)) < abs(area(outline))*.999 for p in loop):
        bm.free()
        raise ValueError("Continuous body requires one enclosing outer rim")
    bm.free()
    n = len(outline)
    points = [(p.x, y, p.z) for y in (floor, rear) for p in outline]
    faces = [tuple(range(n)), tuple(range(n, n*2))]
    faces += [(i, (i+1)%n, (i+1)%n+n, i+n) for i in range(n)]
    mesh = bpy.data.meshes.new("ContinuousBodyOutline")
    mesh.from_pydata(points, [], faces)
    mesh.update()
    plate = bpy.data.objects.new("__continuous_body", mesh)
    bpy.context.collection.objects.link(plate)
    for material in target.data.materials:
        plate.data.materials.append(material)
    prepare_solid(plate)
    bpy.context.view_layer.objects.active = target
    modifier = target.modifiers.new("ContinuousMetalBody", "BOOLEAN")
    modifier.operation = "UNION"
    modifier.solver = "EXACT"
    modifier.object = plate
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    bpy.data.objects.remove(plate, do_unlink=True)
    bpy.data.objects.remove(backing, do_unlink=True)
    bm = bmesh.new()
    bm.from_mesh(target.data)
    bm.normal_update()
    # Boolean union can leave intermediate wall rows at the old extrusion end.
    # A larger round can extend past those rows and turn them inside out. Build
    # full-height outer quads instead of merely dissolving some coplanar joins.
    tree = KDTree(n)
    projected = [Vector((p.x, 0, p.z)) for p in outline]
    for i, point in enumerate(projected):
        tree.insert(point, i)
    tree.balance()
    mapped = {v: tree.find(Vector((v.co.x, 0, v.co.z))) for v in bm.verts}
    def on_outline(vertex):
        point = Vector((vertex.co.x, 0, vertex.co.z))
        for _, i, _ in tree.find_n(point, 3):
            for j in (i, (i-1)%n):
                a, b = projected[j], projected[(j+1)%n]
                d = b-a
                if d.length_squared < 1e-16:
                    continue
                t = max(0, min(1, (point-a).dot(d)/d.length_squared))
                if (point-a-d*t).length < 1e-7:
                    return True
        return False
    outer = {v for v in bm.verts if on_outline(v)}
    walls = [f for f in bm.faces if max(v.co.y for v in f.verts)-min(v.co.y for v in f.verts) > 1e-7
             and all(v in outer for v in f.verts)]
    front_vertices = {mapped[v][1]: v for v in bm.verts if mapped[v][2] < 1e-7 and abs(v.co.y-front) < 1e-7}
    rear_vertices = {mapped[v][1]: v for v in bm.verts if mapped[v][2] < 1e-7 and abs(v.co.y-rear) < 1e-7}
    if len(front_vertices) != n or len(rear_vertices) != n:
        bm.free()
        raise ValueError("Continuous body cap contours do not match the outer wall")
    old_wall_count = len(walls)
    bmesh.ops.delete(bm, geom=walls, context="FACES_ONLY")
    loose_edges = [e for e in bm.edges if not e.link_faces]
    if loose_edges:
        bmesh.ops.delete(bm, geom=loose_edges, context="EDGES")
    loose_vertices = [v for v in bm.verts if not v.link_faces]
    if loose_vertices:
        bmesh.ops.delete(bm, geom=loose_vertices, context="VERTS")
    for i in range(n):
        bm.faces.new((front_vertices[i], front_vertices[(i+1)%n], rear_vertices[(i+1)%n], rear_vertices[i]))
    bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-7)
    loose_edges = [e for e in bm.edges if not e.link_faces]
    if loose_edges:
        bmesh.ops.delete(bm, geom=loose_edges, context="EDGES")
    loose_vertices = [v for v in bm.verts if not v.link_faces]
    if loose_vertices:
        bmesh.ops.delete(bm, geom=loose_vertices, context="VERTS")
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(target.data)
    bm.free()
    target.data.update()
    bpy.context.view_layer.update()
    topology = mesh_connectivity(target)
    if topology != {"components": 1, "nonmanifold_edges": 0}:
        raise ValueError("Continuous metal body is not one closed connected solid: " + str(topology))
    probes = []
    for a, b in zip(outline, outline[1:]+outline[:1]):
        direction = Vector((b.z-a.z, 0, a.x-b.x))
        if direction.length < 1e-7:
            continue
        direction.normalize()
        probes.append({"xz": [(a.x+b.x)/2, (a.z+b.z)/2], "outward": [direction.x, direction.z]})
    margin = max(edge_radius * 1.25, 1e-5)
    if margin * 2 >= rear-front:
        raise ValueError("Continuous body needs a straight sidewall between the R edges")
    return {**spec, "outline_vertex_count": n, "front_y_m": front, "rear_y_m": rear,
            "supported_surface_y_m": floor, "base_thickness_m": rear-floor,
            "total_thickness_m": rear-front, "replaced_outer_wall_faces": old_wall_count,
            "continuous_outer_wall_quads": n,
            "topology": topology, "sidewall_probes": probes,
            "probe_depths_m": [front+margin, (front+rear)/2, rear-margin],
            "old_backing_removed": True, "surface_transform_baked": True, "outline_xz": [[p.x, p.z] for p in outline]}


def smooth_continuous_sidewall(obj, body):
    """Use one tangent normal through the wall height, independent of cap fans."""
    from mathutils.kdtree import KDTree
    outline = [Vector((x, 0, z)) for x, z in body["outline_xz"]]
    n = len(outline)
    directions = []
    for a, b in zip(outline, outline[1:]+outline[:1]):
        directions.append(Vector((b.z-a.z, 0, a.x-b.x)).normalized())
    tangent_normals = [(directions[i-1]+directions[i]).normalized() for i in range(n)]
    tree = KDTree(n)
    for i, point in enumerate(outline):
        tree.insert(point, i)
    tree.balance()
    vertex_normals = {}
    for vertex in obj.data.vertices:
        point = Vector((vertex.co.x, 0, vertex.co.z))
        nearest = tree.find_n(point, 3)
        best = None
        for _, i, _ in nearest:
            for j in (i, (i-1)%n):
                a, b = outline[j], outline[(j+1)%n]
                delta = b-a
                if delta.length_squared < 1e-16:
                    continue
                t = max(0, min(1, (point-a).dot(delta)/delta.length_squared))
                distance = (a+delta*t-point).length
                if distance < 1e-7 and (best is None or distance < best[0]):
                    best = (distance, tangent_normals[j].lerp(tangent_normals[(j+1)%n], t).normalized())
        if best:
            vertex_normals[vertex.index] = tuple(best[1])
    normals = [tuple(normal.vector) for normal in obj.data.corner_normals]
    count = 0
    for face in obj.data.polygons:
        if abs(face.normal.y) > .99:
            continue
        for index in face.loop_indices:
            normal = vertex_normals.get(obj.data.loops[index].vertex_index)
            if normal is not None:
                normals[index] = normal
                count += 1
    obj.data.normals_split_custom_set(normals)
    obj.data.update()
    return count


def check_exported_bodies(bodies):
    checks = []
    for spec in bodies:
        maximum, count = 0.0, 0
        for probe in spec["sidewall_probes"]:
            x, z = probe["xz"]
            dx, dz = probe["outward"]
            direction = Vector((-dx, 0, -dz))
            for y in spec["probe_depths_m"]:
                expected = Vector((x, y, z))
                origin = expected - direction * .0001
                hits = []
                for obj in geometry():
                    inverse = obj.matrix_world.inverted()
                    hit, point, _, _ = obj.ray_cast(inverse @ origin, inverse.to_3x3() @ direction, distance=.0002)
                    if hit:
                        hits.append(obj.matrix_world @ point)
                if not hits:
                    raise ValueError("Exported continuous sidewall contains a gap")
                error = min((p-expected).length for p in hits)
                maximum = max(maximum, error)
                count += 1
        if maximum > 2e-6:
            raise ValueError("Exported continuous sidewall is misaligned: " + str(maximum))
        checks.append({"target": spec["target"], "probe_count": count,
                       "max_sidewall_error_m": maximum})
    return checks


def finish_geometry(spec, temp_dir):
    results = {"backings": [], "engravings": [], "bevels": [], "mesh_cleanup": [], "continuous_bodies": []}
    for body in spec.get("continuous_bodies", []):
        radius = max((max(b["width_m"], b.get("outer_width_m", 0)) for b in spec.get("bevels", []) if b["target"] == body["target"]), default=0)
        results["continuous_bodies"].append(rebuild_continuous_body(body, radius))
    for backing in spec.get("backings", []):
        target = bpy.data.objects.get(backing["target"])
        if not target or target.type != "MESH":
            raise ValueError("Missing backing target")
        box = bounds([target])
        thickness = backing["thickness_m"]
        if thickness >= box[1][1] - box[1][0]:
            raise ValueError("Backing must fit within the existing model thickness")
        bpy.ops.mesh.primitive_cube_add(size=1, location=(sum(box[0])/2, box[1][1]-thickness/2, sum(box[2])/2))
        placeholder = bpy.context.object
        placeholder.name = "__etch_backing"
        placeholder.dimensions = (box[0][1]-box[0][0], thickness, box[2][1]-box[2][0])
        bpy.context.view_layer.update()
        replace_layer({"target": placeholder.name, "svg": backing["outline_svg"],
                       "material": backing["material"], "thickness_m": thickness}, temp_dir)
        additions = [o for o in geometry() if o.name.startswith("__etch_backing")]
        if len(additions) != 1:
            raise ValueError("Backing outline must be one closed shape")
        bpy.ops.object.select_all(action="DESELECT")
        additions[0].select_set(True)
        bpy.context.view_layer.objects.active = additions[0]
        bpy.ops.object.convert(target="MESH")
        plate = bpy.context.object
        prepare_solid(target)
        prepare_solid(plate)
        modifier = target.modifiers.new("SolidRearBacking", "BOOLEAN")
        modifier.operation = "UNION"
        modifier.solver = "EXACT"
        modifier.object = plate
        bpy.context.view_layer.objects.active = target
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        bpy.data.objects.remove(plate, do_unlink=True)
        bpy.context.view_layer.update()
        results["backings"].append(backing)
    for engraving in spec.get("engravings", []):
        target = bpy.data.objects.get(engraving["target"])
        cutter = bpy.data.objects.get(engraving["cutter"])
        if not target or not cutter or target.type != "MESH" or cutter.type != "MESH":
            raise ValueError("Engraving target/cutter must be existing meshes")
        prepare_solid(target)
        prepare_solid(cutter)
        box, cutbox = bounds([target]), bounds([cutter])
        surface, depth = box[1][1], engraving["depth_m"]
        if depth >= (box[1][1] - box[1][0]) / 2:
            raise ValueError("Engraving would penetrate the backing")
        # These badge templates have their rear face along +Y. Probe cap
        # interiors so failure to overlap the backing is never silent.
        cutter.data.calc_loop_triangles()
        caps = []
        for tri in cutter.data.loop_triangles:
            points = [cutter.data.vertices[i].co for i in tri.vertices]
            if all(abs(p.y - cutbox[1][1]) < 1e-6 for p in points):
                caps.append((tri.area, sum(points, Vector()) / 3))
        samples = [p for _, p in sorted(caps, key=lambda v: v[0], reverse=True)[:24]]
        if len(samples) < 3 or any(back_hit(target, p.x, p.z, surface + .01) is None or
                abs(back_hit(target, p.x, p.z, surface + .01) - surface) > 1e-5 for p in samples):
            raise ValueError("Logo is not fully supported by a flat rear surface")
        height = cutbox[1][1] - cutbox[1][0]
        for vertex in cutter.data.vertices:
            vertex.co.y = surface - depth + (vertex.co.y - cutbox[1][0]) / height * (depth + .001)
        material = bpy.data.materials.get(engraving["material"])
        if not material:
            raise ValueError("Missing engraving material")
        if material.name not in target.data.materials:
            target.data.materials.append(material)
        cutter.data.materials.clear()
        for mat in target.data.materials:
            cutter.data.materials.append(mat)
        material_index = list(target.data.materials).index(material)
        for face in cutter.data.polygons:
            face.material_index = material_index
        bpy.context.view_layer.update()
        modifier = target.modifiers.new("EtchedLogo", "BOOLEAN")
        modifier.operation = "DIFFERENCE"
        modifier.solver = "EXACT"
        modifier.object = cutter
        bpy.context.view_layer.objects.active = target
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        bpy.data.objects.remove(cutter, do_unlink=True)
        bpy.context.view_layer.update()
        measured = [back_hit(target, p.x, p.z, surface + .01) for p in samples]
        if any(y is None or abs(y - (surface - depth)) > 1e-5 for y in measured):
            raise ValueError("Logo engraving failed its recessed-surface probes")
        results["engravings"].append({**engraving, "surface_y_m": surface,
            "floor_y_m": surface - depth, "probe_count": len(samples),
            "probes_xz": [[p.x, p.z] for p in samples], "cutter_removed": True})
    for bevel in spec.get("bevels", []):
        target = bpy.data.objects.get(bevel["target"])
        if not target or target.type != "MESH":
            raise ValueError("Missing bevel mesh " + bevel["target"])
        prepare_solid(target)
        faces_before = len(target.data.polygons)
        cap_area_before = sum(f.area for f in target.data.polygons if abs(f.normal.y) > .99999)
        body = next((b for b in results["continuous_bodies"] if b["target"] == target.name), None)
        radius_stats = round_edge_weights(target, bevel["width_m"], body, bevel.get("outer_width_m"),
                                          bevel.get("thin_width_m"), bevel.get("thin_feature_m"))
        modifier = target.modifiers.new("JewelryEdge", "BEVEL")
        modifier.width = radius_stats["modifier_radius_m"]
        modifier.segments = bevel.get("segments", 3)
        # Circular quarter-round on the badge's 90-degree extruded edges.
        # OFFSET equals the radius there; weights protect narrow features locally.
        modifier.offset_type = "OFFSET"
        modifier.profile = .5
        modifier.limit_method = "WEIGHT"
        modifier.use_clamp_overlap = False
        # Sliding along incidental cap edges distorts the requested R near
        # Boolean topology. Keep offsets perpendicular to the rim instead.
        modifier.loop_slide = False
        bpy.context.view_layer.objects.active = target
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        if len(target.data.polygons) <= faces_before:
            raise ValueError("Bevel did not create any rounded edge geometry")
        results["mesh_cleanup"].append(clean_finished_mesh(target))
        for face in target.data.polygons:
            face.use_smooth = True
        target.data.set_sharp_from_angle(angle=math.radians(40))
        normal = target.modifiers.new("PolishedNormals", "WEIGHTED_NORMAL")
        normal.keep_sharp = True
        bpy.ops.object.modifier_apply(modifier=normal.name)
        # Fix normals on the same triangles that both formats will serialize.
        triangulate = target.modifiers.new("ExportTriangles", "TRIANGULATE")
        triangulate.keep_custom_normals = True
        triangulate.ngon_method = "CLIP"
        bpy.ops.object.modifier_apply(modifier=triangulate.name)
        cap_faces = lock_planar_cap_normals(target)
        for body in results["continuous_bodies"]:
            if body["target"] == target.name:
                body["consistent_wall_normal_corners"] = smooth_continuous_sidewall(target, body)
        cap_area_after = sum(f.area for f in target.data.polygons if abs(f.normal.y) > .99999)
        results["bevels"].append({**bevel, "profile": .5, "offset_type": "OFFSET",
                                  "local_clearance_limit": True, **radius_stats, "faces_before": faces_before,
                                  "cap_area_before_m2": cap_area_before, "cap_area_after_m2": cap_area_after,
                                  "faces_after": len(target.data.polygons), "flat_cap_faces": cap_faces})
    for body in results["continuous_bodies"]:
        body["finished_topology"] = mesh_connectivity(bpy.data.objects[body["target"]])
        if body["finished_topology"] != {"components": 1, "nonmanifold_edges": 0}:
            raise ValueError("Finishing broke the continuous metal body")
    bpy.context.view_layer.update()
    return results


def render_asset(path, output, angle):
    stats = import_asset(path)
    scene = bpy.context.scene
    for obj in list(scene.objects):
        if obj.type in {"LIGHT", "CAMERA"}:
            bpy.data.objects.remove(obj, do_unlink=True)
    box = stats["bounds_m"]
    center = Vector([sum(v) / 2 for v in box])
    span = max(v[1] - v[0] for v in box)
    cam_data = bpy.data.cameras.new("ValidationCamera")
    cam = bpy.data.objects.new("ValidationCamera", cam_data)
    scene.collection.objects.link(cam)
    cam.location = center + Vector((span * (0.7 if angle == "oblique" else 0), -span * 3.8, span * (0.35 if angle == "oblique" else 0)))
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = span * 1.14
    scene.camera = cam
    for name, pos, power, size in [("Key", (-1.2, -2, 2), 60, 1.7), ("Fill", (1.5, -1, .2), 30, 1.3), ("Rim", (0, 1, 1), 45, 1)]:
        data = bpy.data.lights.new(name, "AREA")
        data.energy = power * (span / .22) ** 2
        data.shape = "DISK"
        data.size = span * size
        obj = bpy.data.objects.new(name, data)
        scene.collection.objects.link(obj)
        obj.location = center + Vector(pos) * span
        obj.rotation_euler = (center - obj.location).to_track_quat("-Z", "Y").to_euler()
    world = bpy.data.worlds.new("ValidationWorld")
    world.use_nodes = True
    world.node_tree.nodes.get("Background").inputs[0].default_value = (.18, .18, .18, 1)
    world.node_tree.nodes.get("Background").inputs[1].default_value = .6
    scene.world = world
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 24
    scene.cycles.use_denoising = True
    scene.cycles.seed = 0
    scene.render.resolution_x = scene.render.resolution_y = 512
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = True
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "None"
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.filepath = str(output)
    bpy.ops.render.render(write_still=True)
    return stats


def build(job):
    recipe = job["recipe"]
    out = Path(job["output"])
    out.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.open_mainfile(filepath=recipe["template"])
    if bpy.data.libraries:
        raise ValueError("Template must be self-contained; external .blend libraries are unsupported")
    # Source .blend scripts are disabled by the launcher. All changes are in memory.
    layer_results = [replace_layer(layer, out) for layer in recipe.get("layers", [])]
    for spec in recipe.get("textures", []):
        mat = bpy.data.materials.get(spec["material"])
        if not mat or not mat.node_tree:
            raise ValueError("Missing texture material: " + spec["material"])
        nodes = [n for n in mat.node_tree.nodes if n.type == "TEX_IMAGE" and ("node" not in spec or n.name == spec["node"])]
        if len(nodes) != 1:
            raise ValueError("Texture node ambiguous or missing; specify node explicitly")
        nodes[0].image = bpy.data.images.load(spec["image"], check_existing=False)
    for spec in recipe.get("materials", []):
        mat = bpy.data.materials.get(spec["name"])
        nodes = [n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"] if mat and mat.node_tree else []
        if len(nodes) != 1:
            raise ValueError("Material must contain exactly one Principled BSDF")
        for key, socket in [("metallic", "Metallic"), ("roughness", "Roughness"), ("base_color", "Base Color"), ("ior", "IOR")]:
            if key in spec:
                if nodes[0].inputs[socket].is_linked:
                    raise ValueError("Cannot override linked material input " + socket)
                nodes[0].inputs[socket].default_value = spec[key]
    explicit_images = {str(Path(t["image"]).resolve()) for t in recipe.get("textures", [])}
    used_materials = {m for o in geometry() for m in o.data.materials if m}
    trees = [m.node_tree for m in used_materials if m.node_tree]
    if bpy.context.scene.world and bpy.context.scene.world.node_tree:
        trees.append(bpy.context.scene.world.node_tree)
    for tree in trees:
        for node in tree.nodes:
            if node.type in {"TEX_IMAGE", "TEX_ENVIRONMENT"} and node.image:
                image = node.image
                if not image.packed_file and str(Path(bpy.path.abspath(image.filepath)).resolve()) not in explicit_images:
                    raise ValueError("Pack template image or declare it in textures: " + image.name)
    bpy.ops.object.select_all(action="DESELECT")
    objects = geometry()
    for obj in objects:
        obj.hide_set(False)
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.convert(target="MESH")
    finishing = finish_geometry(recipe.get("finishing", {}), out)
    stats = scene_stats()
    exports = recipe["export"]
    paths = {}
    for fmt in exports["formats"]:
        path = out / (recipe.get("output_name", recipe["id"]) + "." + fmt)
        bpy.ops.object.select_all(action="DESELECT")
        for obj in geometry():
            obj.select_set(True)
        if fmt == "glb":
            result = bpy.ops.export_scene.gltf(filepath=str(path), check_existing=False, export_format="GLB",
                use_selection=True, export_animations=False, export_cameras=False, export_lights=False,
                export_apply=True, export_yup=True, export_materials="EXPORT", export_image_format="AUTO")
        else:
            if exports.get("usdz_lights", False):
                for obj in bpy.context.scene.objects:
                    if obj.type == "LIGHT":
                        obj.select_set(True)
            result = bpy.ops.wm.usd_export(filepath=str(path), check_existing=False, selected_objects_only=True,
                export_animation=False, export_materials=True, generate_preview_surface=True,
                generate_materialx_network=False, export_textures_mode="NEW", overwrite_textures=True,
                relative_paths=True, export_cameras=False, export_lights=exports.get("usdz_lights", False),
                convert_world_material=exports.get("usdz_world", False), export_curves=False,
                export_custom_properties=False, triangulate_meshes=True, allow_unicode=False,
                rename_uvmaps=False)
        if "FINISHED" not in result or not path.is_file():
            raise RuntimeError("Export failed: " + fmt)
        paths[fmt] = str(path)
    from pxr import Usd, UsdLux
    import struct
    contents = {}
    for fmt, path in paths.items():
        if fmt == "usdz":
            stage = Usd.Stage.Open(path)
            contents[fmt] = {"cameras": [str(p.GetPath()) for p in stage.Traverse() if p.GetTypeName() == "Camera"],
                             "lights": [str(p.GetPath()) for p in stage.Traverse() if p.HasAPI(UsdLux.LightAPI)]}
        else:
            data = Path(path).read_bytes()
            doc = json.loads(data[20:20 + struct.unpack_from("<I", data, 12)[0]])
            contents[fmt] = {"cameras": doc.get("cameras", []),
                             "lights": doc.get("extensions", {}).get("KHR_lights_punctual", {}).get("lights", [])}
        if contents[fmt]["cameras"] or (not exports.get("usdz_lights", False) and not exports.get("usdz_world", False) and contents[fmt]["lights"]):
            raise ValueError(fmt + " contains unexpected cameras or lights")
    # Reopen the actual exported files. Check geometry and texture resolution.
    reopened = {}
    for fmt, path in paths.items():
        reopened[fmt] = import_asset(path)
        reopened[fmt]["engraving_checks"] = check_exported_engraving(finishing["engravings"])
        reopened[fmt]["continuous_body_checks"] = check_exported_bodies(finishing["continuous_bodies"])
        reopened[fmt]["round_profile_checks"] = check_exported_rounds(finishing["bevels"])
        reopened[fmt]["round_surface_checks"] = check_exported_round_surfaces(str(path), finishing["continuous_bodies"], finishing["bevels"])
        if finishing["bevels"]:
            reopened[fmt]["cap_normal_checks"] = check_exported_cap_normals(path)
    for fmt, check in reopened.items():
        if len(check["objects"]) != len(stats["objects"]):
            raise ValueError(fmt + " round-trip object count differs")
        if len(check["images"]) != len(stats["images"]):
            raise ValueError(fmt + " round-trip texture count differs")
        error = max(abs(a - b) for row, row2 in zip(check["bounds_m"], stats["bounds_m"]) for a, b in zip(row, row2))
        if error > 1e-5:
            raise ValueError(fmt + " round-trip bounds differ by " + str(error) + " m")
    for path in out.glob("*.svg"):
        path.unlink()
    return {"id": recipe["id"], "blender": bpy.app.version_string, "layers": layer_results,
            "scene": stats, "finishing": finishing, "export_contents": contents, "roundtrip": reopened}


def main():
    job_path = Path(sys.argv[sys.argv.index("--") + 1])
    request = json.loads(job_path.read_text())
    results = []
    for job in request["jobs"]:
        try:
            if job["action"] == "build":
                result = build(job)
            elif job["action"] == "render":
                result = render_asset(job["asset"], job["output"], job.get("angle", "front"))
            elif job["action"] == "validate":
                result = import_asset(job["asset"])
            elif job["action"] == "doctor":
                result = {"blender": bpy.app.version_string,
                          "svg": bpy.ops.import_curve.svg.get_rna_type().identifier,
                          "usd": bpy.ops.wm.usd_export.get_rna_type().identifier,
                          "gltf": bpy.ops.export_scene.gltf.get_rna_type().identifier}
            else:
                raise ValueError("Unknown worker action")
            results.append({"ok": True, "result": result})
        except Exception as error:
            traceback.print_exc()
            results.append({"ok": False, "error": str(error)})
        Path(request["result_path"]).write_text(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
