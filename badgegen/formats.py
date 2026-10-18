"""Read-only container checks, independent of Blender's exporter."""
import hashlib
import json
import math
import struct
import zipfile
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect_glb(path):
    data = Path(path).read_bytes()
    if len(data) < 20:
        raise ValueError("Truncated GLB")
    magic, version, length = struct.unpack_from("<4sII", data)
    if magic != b"glTF" or version != 2 or length != len(data):
        raise ValueError("Invalid GLB header or byte length")
    chunks, offset = [], 12
    while offset < length:
        size, kind = struct.unpack_from("<II", data, offset)
        offset += 8
        if size % 4 or offset + size > length:
            raise ValueError("Invalid GLB chunk")
        chunks.append((kind, data[offset:offset + size]))
        offset += size
    if not chunks or chunks[0][0] != 0x4E4F534A:
        raise ValueError("Missing GLB JSON chunk")
    doc = json.loads(chunks[0][1])
    binary = next((b for kind, b in chunks if kind == 0x004E4942), b"")
    for buffer in doc.get("buffers", []):
        if "uri" in buffer or buffer["byteLength"] > len(binary):
            raise ValueError("GLB must contain its buffers")
    for view in doc.get("bufferViews", []):
        if view.get("buffer", 0) != 0 or view.get("byteOffset", 0) + view["byteLength"] > len(binary):
            raise ValueError("Out-of-range GLB buffer view")
    for image in doc.get("images", []):
        if "bufferView" not in image:
            raise ValueError("GLB textures must be embedded")
    if not doc.get("meshes"):
        raise ValueError("GLB has no meshes")
    for mesh in doc["meshes"]:
        for primitive in mesh["primitives"]:
            accessor = doc["accessors"][primitive["attributes"]["POSITION"]]
            if accessor["count"] <= 0 or not all(math.isfinite(x) for key in ("min", "max") for x in accessor[key]):
                raise ValueError("Empty or invalid GLB geometry")
    return {"format": "glb", "bytes": len(data), "meshes": len(doc["meshes"]),
            "materials": doc.get("materials", []), "images": len(doc.get("images", [])),
            "extensions": doc.get("extensionsUsed", [])}


def inspect_usdz(path):
    with zipfile.ZipFile(path) as archive, open(path, "rb") as stream:
        entries = archive.infolist()
        if not entries or not entries[0].filename.endswith((".usd", ".usdc", ".usda")):
            raise ValueError("USDZ first entry must be a USD layer")
        for entry in entries:
            if entry.compress_type != zipfile.ZIP_STORED:
                raise ValueError("USDZ entries must be uncompressed")
            stream.seek(entry.header_offset)
            header = stream.read(30)
            name_len, extra_len = struct.unpack_from("<HH", header, 26)
            if (entry.header_offset + 30 + name_len + extra_len) % 64:
                raise ValueError("USDZ entry is not 64-byte aligned")
            if not entry.file_size:
                raise ValueError("Empty USDZ entry")
            if Path(entry.filename).is_absolute() or ".." in Path(entry.filename).parts:
                raise ValueError("Unsafe USDZ member path")
        if archive.testzip() is not None:
            raise ValueError("USDZ CRC failure")
        return {"format": "usdz", "bytes": Path(path).stat().st_size,
                "members": [entry.filename for entry in entries]}


def inspect_asset(path):
    return {"path": str(path), "sha256": sha256(path),
            **({".glb": inspect_glb, ".usdz": inspect_usdz}[Path(path).suffix.lower()](path))}
