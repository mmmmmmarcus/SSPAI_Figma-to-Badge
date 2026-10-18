import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from . import __version__
from .formats import inspect_asset, sha256
from .recipe import load_recipe

WORKER = Path(__file__).with_name("worker.py")


def blender_path(value=None):
    value = value or os.environ.get("BLENDER_BIN") or shutil.which("blender")
    path = Path(value or "/Applications/Blender.app/Contents/MacOS/Blender")
    if not path.is_file():
        raise ValueError("Blender not found; set BLENDER_BIN or --blender")
    return str(path.resolve())


def run_worker(blender, jobs, log_path, timeout=900):
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="badgegen-") as temporary:
        request = Path(temporary) / "jobs.json"
        results = Path(temporary) / "results.json"
        request.write_text(json.dumps({"jobs": jobs, "result_path": str(results)}, ensure_ascii=False))
        with log_path.open("w") as log:
            process = subprocess.run([blender, "--background", "--factory-startup", "--disable-autoexec",
                                      "--python-exit-code", "1", "--python", str(WORKER), "--", str(request)],
                                     stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        if process.returncode or not results.is_file():
            raise RuntimeError("Blender worker failed; see " + str(log_path))
        values = json.loads(results.read_text())
        if len(values) != len(jobs):
            raise RuntimeError("Incomplete worker result; see " + str(log_path))
        return values


@contextmanager
def output_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".badgegen.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError("Output directory is locked by another build: " + str(lock))
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        lock.unlink(missing_ok=True)


def fingerprint(recipe, dependencies, blender):
    payload = {"recipe": recipe, "inputs": {p: sha256(p) for p in dependencies},
               "code": {str(p.name): sha256(p) for p in Path(__file__).parent.glob("*.py")},
               "version": __version__, "blender": blender,
               "blender_binary": [Path(blender).stat().st_size, Path(blender).stat().st_mtime_ns]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def build_many(paths, output, blender, force=False):
    root = Path(output).resolve()
    loaded = [load_recipe(p) for p in paths]
    identifiers = [r["id"] for r, _ in loaded]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Duplicate badge ids in batch")
    for _, dependencies in loaded:
        if any(root == Path(p) or root in Path(p).parents for p in dependencies):
            raise ValueError("Output directory must not contain source assets")
    results, pending, jobs = [], [], []
    started = time.monotonic()
    with output_lock(root), tempfile.TemporaryDirectory(prefix=".staging-", dir=root) as temp:
        for recipe, dependencies in loaded:
            digest = fingerprint(recipe, dependencies, blender)
            destination = root / recipe["id"]
            report_path = destination / "report.json"
            if not force and report_path.is_file():
                try:
                    old = json.loads(report_path.read_text())
                    if old["fingerprint"] == digest and all(sha256(destination / a["file"]) == a["sha256"] for a in old["artifacts"]):
                        results.append({"id": recipe["id"], "status": "cached", "report": str(report_path)})
                        continue
                except (KeyError, OSError, ValueError):
                    pass
            staging = Path(temp) / recipe["id"]
            jobs.append({"action": "build", "recipe": recipe, "output": str(staging)})
            pending.append((recipe, digest, staging, destination))
        if jobs:
            worker_results = run_worker(blender, jobs, root / "build.log")
            for (recipe, digest, staging, destination), item in zip(pending, worker_results):
                if not item["ok"]:
                    results.append({"id": recipe["id"], "status": "failed", "error": item["error"]})
                    continue
                try:
                    artifacts = []
                    for fmt in recipe["export"]["formats"]:
                        path = staging / (recipe.get("output_name", recipe["id"]) + "." + fmt)
                        asset = inspect_asset(path)
                        asset.pop("path")
                        artifacts.append({"file": path.name, **asset})
                    for png in recipe.get("png_exports", []):
                        path = staging / (png["name"] + ".png")
                        shutil.copyfile(png["image"], path)
                        artifacts.append({"file": path.name, "format": "png", "bytes": path.stat().st_size,
                                          "sha256": sha256(path), "node_id": png.get("node_id")})
                    report = {"schema_version": 1, "generator_version": __version__, "fingerprint": digest,
                              "source": recipe.get("source"), **item["result"], "artifacts": artifacts}
                    (staging / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
                    # Keep the old successful output until the replacement is completely checked.
                    backup = Path(temp) / (recipe["id"] + ".previous")
                    if destination.exists():
                        destination.rename(backup)
                    try:
                        staging.rename(destination)
                    except Exception:
                        if backup.exists():
                            backup.rename(destination)
                        raise
                    results.append({"id": recipe["id"], "status": "built", "report": str(destination / "report.json")})
                except Exception as error:
                    results.append({"id": recipe["id"], "status": "failed", "error": str(error)})
    return {"ok": all(r["status"] != "failed" for r in results), "results": results,
            "seconds": round(time.monotonic() - started, 3)}


def comparison(paths, output, blender, reference_format=None):
    # Optional inspection dependency; normal builds use only Python's standard library.
    from PIL import Image, ImageChops, ImageStat
    root = Path(output).resolve()
    jobs, pairs = [], []
    for recipe_path in paths:
        recipe, _ = load_recipe(recipe_path)
        destination = root / recipe["id"]
        check_dir = destination / "comparison"
        check_dir.mkdir(parents=True, exist_ok=True)
        for fmt in recipe["export"]["formats"]:
            baseline = reference_format or fmt
            if baseline not in recipe.get("references", {}):
                raise ValueError("Missing reference " + baseline + " for " + recipe["id"])
            generated = destination / (recipe.get("output_name", recipe["id"]) + "." + fmt)
            if not generated.is_file():
                raise ValueError("Build first: " + str(generated))
            for angle in ("front", "oblique"):
                images = []
                for label, asset in [("reference", recipe["references"][baseline]), ("generated", str(generated))]:
                    image = check_dir / (fmt + "-" + angle + "-" + label + ".png")
                    jobs.append({"action": "render", "asset": asset, "output": str(image), "angle": angle})
                    images.append(image)
                pairs.append((recipe["id"], fmt, baseline, angle, images))
    worker_results = run_worker(blender, jobs, root / "comparison.log", timeout=1800)
    errors = [r["error"] for r in worker_results if not r["ok"]]
    if errors:
        raise RuntimeError("Comparison render failed: " + "; ".join(errors))
    metrics, contact_rows = [], []
    for identifier, fmt, baseline, angle, images in pairs:
        a, b = [Image.open(p).convert("RGBA") for p in images]
        aa, ba = [im.getchannel("A").point(lambda x: 255 if x > 127 else 0) for im in (a, b)]
        union = ImageChops.lighter(aa, ba)
        intersection = ImageChops.darker(aa, ba)
        union_sum = ImageStat.Stat(union).sum[0]
        iou = ImageStat.Stat(intersection).sum[0] / union_sum if union_sum else 0
        # Compare premultiplied color over a dark background to handle edge alpha.
        composites = []
        for im in (a, b):
            bg = Image.new("RGBA", im.size, (35, 35, 35, 255))
            composites.append(Image.alpha_composite(bg, im).convert("RGB"))
        difference = ImageChops.difference(*composites)
        mae = sum(ImageStat.Stat(difference, mask=union).mean) / 3 / 255
        metric = {"id": identifier, "format": fmt, "reference_format": baseline, "angle": angle, "silhouette_iou": round(iou, 6),
                  "foreground_rgb_mae": round(mae, 6), "reference": str(images[0]), "generated": str(images[1]),
                  "passes": iou >= .98 and mae <= .04}
        metrics.append(metric)
        from PIL import ImageDraw
        row = Image.new("RGB", (1536, 548), (35, 35, 35))
        row.paste(composites[0], (0, 36)); row.paste(composites[1], (512, 36)); row.paste(difference, (1024, 36))
        ImageDraw.Draw(row).text((12, 10), identifier + " / " + fmt + " / " + angle + "   |   reference " + baseline + "   /   generated   /   difference", fill="white")
        row.save(images[0].parent / (fmt + "-" + angle + "-comparison.png"))
        contact_rows.append(row.resize((768, 274)))
    sheet = Image.new("RGB", (768, 274 * len(contact_rows)))
    for i, row in enumerate(contact_rows):
        sheet.paste(row, (0, 274 * i))
    sheet.save(root / "comparison.png")
    result = {"ok": all(m["passes"] for m in metrics), "metrics": metrics,
              "thresholds": {"silhouette_iou_min": .98, "foreground_rgb_mae_max": .04},
              "note": "Same Blender importer, camera, Cycles lighting and AgX for both assets. Not Apple Quick Look acceptance."}
    (root / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Headless badge generator. stdout is JSON; Blender output goes to log files.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--blender", help="Blender executable (or BLENDER_BIN)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check Blender and required import/export operators")
    for name in ("build", "batch", "compare"):
        command = sub.add_parser(name)
        command.add_argument("recipes", nargs="+" if name != "build" else 1)
        command.add_argument("--output", default="output")
        if name == "compare":
            command.add_argument("--reference-format", choices=["glb", "usdz"], help="Use one trusted reference format for both generated formats")
        if name != "compare":
            command.add_argument("--force", action="store_true", help="Ignore cache; replace only after validation")
    command = sub.add_parser("validate")
    command.add_argument("assets", nargs="+")
    command = sub.add_parser("inspect", help="Resolve and validate recipe without running Blender")
    command.add_argument("recipe")
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            recipe, deps = load_recipe(args.recipe)
            result = {"ok": True, "recipe": recipe, "dependencies": deps}
        else:
            blender = blender_path(args.blender)
            if args.command in ("build", "batch"):
                result = build_many(args.recipes, args.output, blender, args.force)
            elif args.command == "compare":
                with output_lock(Path(args.output).resolve()):
                    result = comparison(args.recipes, args.output, blender, args.reference_format)
            elif args.command == "doctor":
                with tempfile.TemporaryDirectory() as temp:
                    result = run_worker(blender, [{"action": "doctor"}], Path(temp) / "doctor.log")[0]
            else:
                paths = [Path(p).resolve() for p in args.assets]
                containers = [inspect_asset(p) for p in paths]
                with tempfile.TemporaryDirectory() as temp:
                    checks = run_worker(blender, [{"action": "validate", "asset": str(p)} for p in paths], Path(temp) / "validate.log")
                result = {"ok": all(c["ok"] for c in checks), "assets": [dict(container=c, roundtrip=r) for c, r in zip(containers, checks)]}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1
