---
name: figma-badge-3d
description: "将分层 Figma 徽章转换成 GLB 和 USDZ：识别几何、获取当前 SVG 与专用贴图、建立配方并调用无界面 Blender 程序，同时交付原生彩色和灰色 PNG。用于徽章 2D 转 3D、批量导出、金银材质及现有徽章更新。"
---

# Figma badge to 3D

Use the deterministic Badge Generator for geometry, materials and exports. This Skill handles design interpretation and input provenance. Do not build a UI or require opening Blender. Deliver models and original Figma color/gray PNGs; never render PNG previews.

## Locate the installation

The helper is `scripts/badge_workflow.py` relative to this Skill. Generator discovery is `--generator`, `BADGEGEN_ROOT`, local `config.json`, then the source checkout containing this Skill. Run with the generator checkout's `.venv/bin/python` (Windows `.venv/Scripts/python.exe`). Read its README and `profiles.json`; private installations may have additional calibrated recipes. Run `badgegen doctor` before first use. Missing Figma access or Blender is a concrete dependency failure, not a reason to substitute guessed assets.

```sh
<python> <skill>/scripts/badge_workflow.py resolve '<Figma URL>'
```

## Inspect and map

Read metadata and a visual reference for the exact file/node. Follow the connected Figma tools' prerequisite skills when available. Prefer the user's intended Figma account; desktop and cloud accounts may differ. For a section, enumerate candidates and resolve requested badges from context. Never silently choose the first frame.

Read [mapping instructions](references/figma-mapping.md). Inspect actual contours, raised artwork, masks, texture and material intent. Match a profile by structure, not badge name. The public demo profiles retain a fixed round body and replace only its central decoration; they are not arbitrary silhouette converters. For a new outline/holes/separate pieces, create an explicit template and recipe using the generator's `docs/recipes.md`; verify all retained geometry. Do not claim production support merely because an export finishes.

For fresh Figma links, obtain current vectors and the matching dedicated `Blender贴图` frame. Save actual tool responses and source node IDs. Never use full-badge screenshots as textures or reuse an old texture by name. For a material-only revision, reuse recorded inputs and disclose that no fresh extraction occurred. Native export is preferred; a screenshot may include canvas background and is not a delivery export.

Export the color frame and actual sibling `_灰` frame as native PNG; preserve bytes, transparency and configured resolution. Do not synthesize the gray variant. Keep designs unchanged unless the user authorized an edit. If a required layer or mapping is ambiguous, identify the missing decision.

## Prepare and build

The generic helper supports one replaceable SVG layer plus one square dedicated texture. More complex recipes must explicitly map every geometry layer and material; do not silently drop extra content. Prepare using the plan in the mapping reference:

```sh
<python> <skill>/scripts/badge_workflow.py prepare /absolute/run/plan.json
<python> <skill>/scripts/badge_workflow.py build /absolute/run/prepared/recipe.json
```

Keep exact frame names for model filenames and PNG names; stable ASCII IDs are internal directory keys only. Reject names unsafe for the target filesystem rather than rename silently. Retain provenance, hashes and evidence. `prepare` requires a fresh destination. Check JSON success and process exit code; process dispatch is not completion.

## Finish and verify

- Export both GLB and USDZ without cameras/lights/world. Viewer supplies illumination and reflections.
- Pale champagne metal: linear RGBA `[0.88,0.78,0.60,1]`, metallic 1, roughness .16. Silver: `[0.83,0.87,0.91,1]`, metallic 1, roughness .18. Etched areas can use matching color with roughness .32. Apply actual material names from the template. Preserve colored texture and its UVs.
- Default demo display size is .22 m. Approved production mappings use a larger exterior R (1.1 mm), fine artwork R (.25 mm) and thin detail R (.12 mm), eight segments. Do not apply the exterior radius to all artwork. New dimensions require clearance checks.
- Prefer one closed body with continuous front/side/rear transitions; don't stack misaligned backing and rim. Rear engraving requires a real cutter and verified mapping; never leave a protruding logo and call it etched.
- Preserve flat cap normals, tangent rounded-side normals, contour clearance and UVs. Do not hide mesh defects with roughness changes or global decimation. For continuous-body recipes require component/nonmanifold checks, actual exported R-profile/surface checks and engraving checks when applicable. Empty check lists mean not applicable, not universal geometry validation.
- Inspect both exported containers and report: dimensions, texture packing, absent lights/cameras, round trips, and enabled geometric checks. Check delivery PNG hashes match inputs. Separate structural validation from native Apple Quick Look / target-app visual acceptance.
- New sharp concave or hole-heavy shapes remain experimental until their specific rounds, triangulation and native viewer appearance pass. Explain failures; don't substitute a circular template.

Return direct links to GLB/USDZ and actual exported PNGs, plus any concrete limitation. The offline demo intentionally has no Figma PNG pair. Do not claim fresh Figma or native viewer validation without performing it.
