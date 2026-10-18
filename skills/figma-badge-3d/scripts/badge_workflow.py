#!/usr/bin/env python3
"""Figma evidence/assets -> reproducible Badge Generator recipe. No UI/API tokens."""
import argparse
import base64
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
def profiles(generator):
    registry = json.loads((generator / "profiles.json").read_text())
    if not isinstance(registry, dict) or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", k) for k in registry):
        raise ValueError("Profile registry must contain safe ASCII IDs")
    return registry



def parse_link(value):
    url = urllib.parse.urlparse(value)
    if url.scheme != "https" or url.hostname not in {"figma.com", "www.figma.com"}:
        raise ValueError("Expected an HTTPS Figma design URL")
    parts = url.path.strip("/").split("/")
    if len(parts) < 2 or parts[0] not in {"design", "file"}:
        raise ValueError("Expected a Figma Design file, not FigJam/Slides/Make")
    key = parts[1]
    if "branch" in parts:
        index = parts.index("branch")
        if len(parts) <= index + 1:
            raise ValueError("Missing branch key")
        key = parts[index + 1]
    node = urllib.parse.parse_qs(url.query).get("node-id", [""])[0].replace("-", ":")
    if not re.fullmatch(r"\d+:\d+", node):
        raise ValueError("Link must identify a badge node; ask which badge if only a file is supplied")
    return {"file_key": key, "node_id": node,
            "id": "badge-" + hashlib.sha256(key.encode()).hexdigest()[:8] + "-" + node.replace(":", "-")}


def generator_path(value=None):
    config = SKILL / "config.json"
    configured = json.loads(config.read_text()).get("generator_root") if config.exists() else None
    value = value or os.environ.get("BADGEGEN_ROOT") or configured or SKILL.parents[1]
    root = Path(value).expanduser().resolve()
    if not (root / "badgegen/cli.py").is_file():
        raise ValueError("Badge Generator unavailable; set BADGEGEN_ROOT or pass --generator: " + str(root))
    return root


def text_content(path):
    text = Path(path).read_text()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(payload, dict) and "result" in payload:
        payload = payload["result"]
    if isinstance(payload, dict) and "content" in payload:
        return "\n".join(c.get("text", "") for c in payload["content"] if c.get("type") == "text")
    return text


def metadata(path):
    text = text_content(path)
    match = re.search(r"<(frame|section|page|component|instance)\b[\s\S]*", text)
    if not match:
        raise ValueError("Metadata response does not contain a node tree")
    start = match.start()
    # MCP adds prose after the XML. End at the matching root tag.
    tag = match.group(1)
    end = text.rfind("</" + tag + ">")
    xml = text[start:end + len(tag) + 3] if end >= start else text[start:text.index("/>", start) + 2]
    root = ET.fromstring(xml)
    return root, {n.get("id"): n for n in root.iter() if n.get("id")}


def check_url(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.username or parsed.password:
        raise ValueError("Asset URL must not contain credentials")
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"} and parsed.port == 3845 and parsed.path.startswith("/assets/"):
        return
    raise ValueError("Use an observed HTTPS export URL or the local Figma /assets/ endpoint")


def asset_bytes(spec, directory):
    sources = set(spec) & {"url", "local_file", "tool_image_json"}
    if len(sources) != 1:
        raise ValueError("Asset must specify exactly one of url, local_file, tool_image_json")
    if "url" in spec:
        response = directory / spec["source_response"]
        if spec["url"] not in text_content(response):
            raise ValueError("Asset URL was not present in the saved Figma response")
        check_url(spec["url"])
        with urllib.request.urlopen(spec["url"], timeout=30) as stream:
            check_url(stream.geturl())
            data = stream.read(32 * 1024 * 1024 + 1)
    elif "local_file" in spec:
        data = (directory / spec["local_file"]).read_bytes()
    else:
        response = json.loads((directory / spec["tool_image_json"]).read_text())
        if response.get("node_id") != spec["node_id"]:
            raise ValueError("Texture raster response must record the exported node_id")
        content = response["result"].get("content", [])
        images = [c for c in content if c.get("type") == "image" and c.get("mimeType") == "image/png"]
        if len(images) != 1:
            raise ValueError("Expected one PNG image in the native texture-frame result")
        data = base64.b64decode(images[0]["data"], validate=True)
    if not data or len(data) > 32 * 1024 * 1024:
        raise ValueError("Asset is empty or exceeds 32 MiB")
    return data


def prepare(plan_path, generator, destination=None):
    plan_path = Path(plan_path).resolve()
    parent = plan_path.parent
    plan = json.loads(plan_path.read_text())
    link = parse_link(plan["badge_url"])
    registry = profiles(generator)
    if plan.get("profile") not in registry:
        raise ValueError("Choose a verified profile after inspecting the actual design: " + ", ".join(registry))
    if len(plan.get("mapping_reason", "").strip()) < 12:
        raise ValueError("Record the observed geometry/material match in mapping_reason")
    badge, nodes = metadata(parent / plan["badge_metadata"])
    if badge.get("id") != link["node_id"] or badge.tag not in {"frame", "component", "instance"}:
        raise ValueError("Badge metadata must describe exactly the linked badge frame")
    context, context_nodes = metadata(parent / plan["context_metadata"])
    if link["node_id"] not in context_nodes:
        raise ValueError("Context metadata does not contain the linked badge")
    assets = plan["assets"]
    required = set(registry[plan["profile"]]["asset_roles"])
    if required not in ({"border", "texture"}, {"border", "texture", "clip"}):
        raise ValueError("Generic plan helper requires border/texture and optional clip; use a direct recipe for other mappings")
    sample = json.loads((generator / "examples" / (plan["profile"] + ".json")).read_text())
    if len(sample.get("layers", [])) != 1 or len(sample.get("textures", [])) != 1 or sample["textures"][0]["material"] != "Classic Mix":
        raise ValueError("Generic plan helper supports exactly one SVG layer and one Classic Mix texture; use a direct recipe")
    if set(assets) != required:
        raise ValueError("Profile requires fresh assets: " + ", ".join(sorted(required)))
    for role in required - {"texture"}:
        if assets[role].get("node_id") not in nodes:
            raise ValueError(role + " node does not belong to the linked badge")
    texture = context_nodes.get(assets["texture"].get("node_id"))
    if texture is None or texture.get("name") != "Blender贴图" or texture.tag != "frame":
        raise ValueError("Texture must identify a dedicated Blender贴图 frame in current context metadata")
    context_badge = context_nodes[link["node_id"]]
    owners = {c.get("id"): p for p in context.iter() for c in p if c.get("id")}
    if owners.get(texture.get("id")) is not owners.get(context_badge.get("id")):
        raise ValueError("Badge and texture must share a parent for automatic matching")
    if abs(float(texture.get("x")) - float(context_badge.get("x"))) > 1:
        raise ValueError("Ambiguous texture: it does not align with the badge column")
    if float(texture.get("width")) != float(texture.get("height")):
        raise ValueError("Expected a square texture frame")
    png_exports = plan.get("png_exports", {})
    if set(png_exports) != {"color", "gray"}:
        raise ValueError("Supply current Figma color and gray PNG exports")
    if any("tool_image_json" in spec for spec in png_exports.values()):
        raise ValueError("Delivery PNGs require native Figma export files or export URLs; MCP screenshots can include the canvas background")
    if png_exports["color"].get("node_id") != link["node_id"]:
        raise ValueError("Color PNG must come from the linked badge frame")
    gray = context_nodes.get(png_exports["gray"].get("node_id"))
    if (gray is None or gray.get("name") != badge.get("name") + "_灰"
            or owners.get(gray.get("id")) is not owners.get(link["node_id"])
            or abs(float(gray.get("x")) - float(context_badge.get("x"))) > 1):
        raise ValueError("Gray PNG must come from the matching sibling _灰 frame")
    destination = Path(destination).resolve() if destination else parent / "prepared"
    if destination.exists():
        raise ValueError("Destination already exists; choose a fresh run directory to preserve evidence")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".badge-prepare-", dir=destination.parent) as temporary:
        temporary = Path(temporary)
        (temporary / "assets").mkdir()
        provenance = {"badge": link, "display_name": plan.get("display_name", badge.get("name")),
                      "mapping_reason": plan["mapping_reason"], "profile": plan["profile"], "assets": {}}
        files = {}
        all_assets = {**assets, **png_exports}
        for role, spec in all_assets.items():
            data = asset_bytes(spec, parent)
            details = {"node_id": spec["node_id"], "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            if role in {"texture", "color", "gray"}:
                from PIL import Image
                import io
                with Image.open(io.BytesIO(data)) as image:
                    image.verify()
                with Image.open(io.BytesIO(data)) as image:
                    if role == "texture" and (image.format not in {"PNG", "JPEG"} or image.width != image.height or image.width < 128):
                        raise ValueError("Texture must be a square PNG/JPEG of at least 128 px")
                    if role != "texture" and image.format != "PNG":
                        raise ValueError("Figma color and gray exports must be PNG")
                    suffix = ".png" if image.format == "PNG" else ".jpg"
                    details["pixels"] = [image.width, image.height]
            else:
                root = ET.fromstring(data)
                if root.tag.split("}")[-1] != "svg":
                    raise ValueError("Geometry asset is not SVG")
                suffix = ".svg"
            filename = role + suffix
            (temporary / "assets" / filename).write_bytes(data)
            files[role] = str(destination / "assets" / filename)
            details["path"] = "assets/" + filename
            provenance["assets"][role] = details
        recipe = json.loads((generator / "examples" / (plan["profile"] + ".json")).read_text())
        recipe["id"] = link["id"]
        recipe["output_name"] = badge.get("name")
        recipe["png_exports"] = [
            {"name": badge.get("name"), "image": files["color"], "node_id": link["node_id"]},
            {"name": gray.get("name"), "image": files["gray"], "node_id": gray.get("id")}]
        recipe["template"] = str((generator / "examples" / recipe["template"]).resolve())
        recipe.pop("references", None)
        recipe["source"] = {"figma_file_key": link["file_key"], "node_id": link["node_id"],
            "display_name": provenance["display_name"], "border_node_id": assets["border"]["node_id"],
            "texture_node_id": assets["texture"]["node_id"], "provenance": str(destination / "provenance.json")}
        recipe["layers"][0]["svg"] = files["border"]
        if "clip" in files:
            recipe["layers"][0]["clip_svg"] = files["clip"]
            for backing in recipe.get("finishing", {}).get("backings", []):
                backing["outline_svg"] = files["clip"]
        recipe["textures"] = [{"material": "Classic Mix", "image": files["texture"]}]
        # Validate against staged asset locations before committing the new run.
        sys.path.insert(0, str(generator))
        from badgegen.recipe import load_recipe
        staged = copy.deepcopy(recipe)
        for layer in staged["layers"]:
            for key in ("svg", "clip_svg"):
                if key in layer:
                    layer[key] = str(temporary / Path(layer[key]).relative_to(destination))
        for texture_spec in staged["textures"] + staged["png_exports"]:
            texture_spec["image"] = str(temporary / Path(texture_spec["image"]).relative_to(destination))
        for backing in staged.get("finishing", {}).get("backings", []):
            backing["outline_svg"] = str(temporary / Path(backing["outline_svg"]).relative_to(destination))
        check = temporary / "recipe.json"
        check.write_text(json.dumps(staged, ensure_ascii=False, indent=2))
        load_recipe(check)
        check.write_text(json.dumps(recipe, ensure_ascii=False, indent=2))
        evidence = temporary / "evidence"
        evidence.mkdir()
        for role, spec in all_assets.items():
            for key in ("source_response", "tool_image_json"):
                if key in spec:
                    source = parent / spec[key]
                    shutil.copy2(source, evidence / (role + "-" + key + source.suffix))
        for key in ("badge_metadata", "context_metadata"):
            shutil.copy2(parent / plan[key], evidence / (key + ".json"))
        shutil.copy2(plan_path, evidence / "plan.json")
        (temporary / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2))
        shutil.move(str(temporary), str(destination))
    return {"ok": True, "recipe": str(destination / "recipe.json"), "provenance": str(destination / "provenance.json"),
            "id": recipe["id"], "texture_pixels": provenance["assets"]["texture"]["pixels"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generator")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("resolve").add_argument("url")
    command = sub.add_parser("prepare")
    command.add_argument("plan")
    command.add_argument("--destination")
    command = sub.add_parser("build")
    command.add_argument("recipe")
    command.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        root = generator_path(args.generator)
        if args.command == "resolve":
            result = {"ok": True, **parse_link(args.url), "generator": str(root), "profiles": profiles(root)}
        elif args.command == "prepare":
            result = prepare(args.plan, root, args.destination)
        else:
            recipe = Path(args.recipe).resolve()
            process = subprocess.run([sys.executable, "-m", "badgegen", "build", str(recipe), "--output",
                                      str(Path(args.output).resolve() if args.output else root / "output")],
                                     cwd=root, text=True, capture_output=True)
            if process.stdout.strip():
                print(process.stdout, end="")
            if process.stderr:
                print(process.stderr, file=sys.stderr)
            return process.returncode
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
