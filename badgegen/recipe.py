"""Validate and resolve recipes before starting Blender."""
import json
import math
import re
from pathlib import Path


def filename_stem(value):
    if (not isinstance(value, str) or not value.strip() or value in {".", ".."}
            or any(c in value for c in '/\\:<>"|?*') or any(ord(c) < 32 for c in value)
            or value.endswith((" ", "."))
            or value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *["COM" + str(i) for i in range(1,10)], *["LPT" + str(i) for i in range(1,10)]}
            or len(value.encode("utf-8")) > 240):
        raise ValueError("Layer name cannot be used unchanged as a filename: " + repr(value))
    return value


def load_recipe(path):
    path = Path(path).resolve()
    recipe = json.loads(path.read_text())
    allowed = {"schema_version", "id", "output_name", "png_exports", "finishing", "template", "source", "layers", "textures", "materials", "export", "references"}
    unknown = set(recipe) - allowed
    if unknown:
        raise ValueError("Unknown recipe keys: " + ", ".join(sorted(unknown)))
    if recipe.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    if not isinstance(recipe.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", recipe["id"]):
        raise ValueError("id must be a safe ASCII slug, up to 96 characters")
    dependencies = []
    filename_stem(recipe.get("output_name", recipe["id"]))

    def resolve(value, suffixes):
        if not isinstance(value, str):
            raise ValueError("Asset paths must be strings")
        resolved = (path.parent / value).resolve()
        if resolved.suffix.lower() not in suffixes or not resolved.is_file():
            raise ValueError("Missing or unsupported asset: " + str(resolved))
        dependencies.append(str(resolved))
        return str(resolved)

    recipe["template"] = resolve(recipe.get("template"), {".blend"})
    png_names = set()
    for png in recipe.get("png_exports", []):
        if set(png) - {"name", "image", "node_id"}:
            raise ValueError("Unknown PNG export option")
        name = filename_stem(png.get("name"))
        if name.casefold() in png_names:
            raise ValueError("Duplicate PNG export names")
        png_names.add(name.casefold())
        png["image"] = resolve(png.get("image"), {".png"})
        if not Path(png["image"]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("PNG export is not a PNG file")
    targets = set()
    for layer in recipe.get("layers", []):
        if set(layer) - {"target", "svg", "material", "thickness_m", "bevel_m", "resolution", "fit", "clip_svg"}:
            raise ValueError("Unknown layer option")
        if not isinstance(layer.get("target"), str) or layer["target"] in targets:
            raise ValueError("Each layer needs a unique target object")
        targets.add(layer["target"])
        if not isinstance(layer.get("material"), str):
            raise ValueError("Each SVG layer needs an explicit material")
        layer["svg"] = resolve(layer.get("svg"), {".svg"})
        if "clip_svg" in layer:
            layer["clip_svg"] = resolve(layer["clip_svg"], {".svg"})
        if layer.get("fit", "template_bounds") not in {"template_bounds", "template_width_top"}:
            raise ValueError("fit must be template_bounds or template_width_top")
        for key, default, positive in [("thickness_m", None, True), ("bevel_m", 0, False)]:
            value = layer.get(key, default)
            if not isinstance(value, (float, int)) or isinstance(value, bool) or not math.isfinite(value) or value < 0 or (positive and value == 0):
                raise ValueError(key + " must be a finite " + ("positive" if positive else "nonnegative") + " number")
        if layer.get("bevel_m", 0) * 2 >= layer["thickness_m"]:
            raise ValueError("bevel_m must be less than half the thickness")
        if not isinstance(layer.get("resolution", 12), int) or not 1 <= layer.get("resolution", 12) <= 64:
            raise ValueError("resolution must be an integer in 1..64")
    for texture in recipe.get("textures", []):
        if set(texture) - {"material", "image", "node"} or not isinstance(texture.get("material"), str):
            raise ValueError("Texture needs material and image; node is optional")
        texture["image"] = resolve(texture.get("image"), {".png", ".jpg", ".jpeg"})
    for material in recipe.get("materials", []):
        if set(material) - {"name", "metallic", "roughness", "base_color", "ior"} or not isinstance(material.get("name"), str):
            raise ValueError("Invalid material override")
        if "ior" in material and (not isinstance(material["ior"], (int, float)) or not math.isfinite(material["ior"]) or not 1 <= material["ior"] <= 3):
            raise ValueError("ior must be in 1..3")
        for key in ("metallic", "roughness", "base_color"):
            if key not in material:
                continue
            values = material[key] if key == "base_color" else [material[key]]
            if key == "base_color" and (not isinstance(values, list) or len(values) != 4):
                raise ValueError("base_color must be four linear RGBA values")
            if not all(isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v <= 1 for v in values):
                raise ValueError("Material values must be in 0..1")
    finishing = recipe.get("finishing", {})
    if not isinstance(finishing, dict) or set(finishing) - {"engravings", "bevels", "backings", "continuous_bodies"}:
        raise ValueError("Unknown finishing option")
    bodies = finishing.get("continuous_bodies", [])
    if not isinstance(bodies, list):
        raise ValueError("continuous_bodies must be an array")
    used_body_objects = set()
    for body in bodies:
        if (not isinstance(body, dict) or set(body) != {"target", "backing", "surface"}
                or any(not isinstance(v, str) or not v for v in body.values())
                or len(set(body.values())) != 3 or used_body_objects.intersection(body.values())):
            raise ValueError("Continuous body needs distinct target, backing and surface objects")
        used_body_objects.update(body.values())
        if any(item.get("target") == body["backing"] for kind in ("bevels", "engravings", "backings") for item in finishing.get(kind, [])):
            raise ValueError("Finishing must target the unified body, not its removed backing")
    for kind, keys in [("engravings", {"target", "cutter", "depth_m", "material"}),
                       ("bevels", {"target", "width_m", "segments", "outer_width_m", "thin_width_m", "thin_feature_m"}),
                       ("backings", {"target", "outline_svg", "thickness_m", "material"})]:
        specs = finishing.get(kind, [])
        if not isinstance(specs, list):
            raise ValueError(kind + " must be an array")
        seen = set()
        for spec in specs:
            if not isinstance(spec, dict) or set(spec) - keys or not isinstance(spec.get("target"), str) or spec["target"] in seen:
                raise ValueError("Invalid or duplicate finishing target")
            seen.add(spec["target"])
            key = {"engravings": "depth_m", "bevels": "width_m", "backings": "thickness_m"}[kind]
            v = spec.get(key)
            if isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or not 0 < v <= .001:
                raise ValueError(key + " must be in (0, 0.001] meters")
            if kind == "backings":
                spec["outline_svg"] = resolve(spec.get("outline_svg"), {".svg"})
                if not isinstance(spec.get("material"), str):
                    raise ValueError("Backing needs a material")
            elif kind == "engravings":
                if not isinstance(spec.get("cutter"), str) or spec["cutter"] == spec["target"] or not isinstance(spec.get("material"), str):
                    raise ValueError("Engraving needs a distinct cutter and material")
            elif type(spec.get("segments", 3)) is not int or not 2 <= spec.get("segments", 3) <= 8:
                raise ValueError("Bevel segments must be in 2..8")
            if kind == "bevels":
                for extra, limit in (("outer_width_m", .003), ("thin_width_m", .001), ("thin_feature_m", .01)):
                    if extra in spec:
                        v = spec[extra]
                        if type(v) not in (int, float) or not math.isfinite(v) or not 0 < v <= limit:
                            raise ValueError(extra + " exceeds its radius/feature range")
                if ("thin_width_m" in spec) != ("thin_feature_m" in spec):
                    raise ValueError("Thin radius and feature width must be specified together")
                if spec.get("thin_width_m", 0) > spec["width_m"]:
                    raise ValueError("Thin detail radius must not exceed the general detail radius")
                if "outer_width_m" in spec and not any(b["target"] == spec["target"] for b in bodies):
                    raise ValueError("Outer radius requires a continuous body mapping")

    exports = recipe.setdefault("export", {})
    if set(exports) - {"formats", "usdz_lights", "usdz_world"}:
        raise ValueError("Unknown export option")
    formats = exports.setdefault("formats", ["glb", "usdz"])
    if not isinstance(formats, list) or not formats or len(set(formats)) != len(formats) or set(formats) - {"glb", "usdz"}:
        raise ValueError("formats must contain glb and/or usdz, without duplicates")
    for key in ("usdz_lights", "usdz_world"):
        if key in exports and not isinstance(exports[key], bool):
            raise ValueError(key + " must be boolean")
        exports.setdefault(key, False)
    for key, value in recipe.get("references", {}).items():
        if key not in {"glb", "usdz", "png"}:
            raise ValueError("Unknown reference format")
        recipe["references"][key] = resolve(value, {"." + key})
    return recipe, sorted(set(dependencies))
