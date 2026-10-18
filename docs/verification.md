# Verification record

Public baseline 0.3.0, 2026-10-18:

- macOS Apple Silicon, Python 3.14.4, Blender 5.1.2.
- Setup created an isolated venv, installed the package and Pillow, checked required Blender operators and generated the original demo template/texture.
- Gold and silver demo recipes exported GLB and USDZ successfully. Both formats were reimported, with object/bounds/texture checks; exported files contained no cameras or lights.
- Eight automated tests passed with Blender integration enabled: provenance/ownership failures, gray-frame mismatch, Unicode filenames, profile traversal, multi-layer rejection, real exports, original PNG byte preservation and corrupted cached PNG repair.
- `pip check` and Codex Skill structure validation passed.
- A fresh HTTPS clone from the public GitHub repository repeated setup, both demo exports, and all eight tests without private templates or the original virtualenv.
- A temporary Skill installation resolved the public generator/profile registry correctly. The existing private installation retained its original generator location and passed its five applicable workflow tests (one optional Blender test skipped).
- GitHub Actions passed on Python 3.10 and 3.13: [initial CI run](https://github.com/mmmmmmarcus/figma-badge-3d/actions/runs/39124023235).

The demo checks its fine decoration cap normals; it does not exercise private production continuous-body, engraving or exterior R-surface mappings. No native Apple viewer or physical AR-device acceptance is claimed for these new public demos. Windows/Linux Blender exports and live Figma MCP extraction were not revalidated during packaging. The workflow tests use synthetic metadata and PNG fixtures, explicitly not real Figma extraction.

CI performs Python workflow tests only; the Blender test is skipped unless `BADGEGEN_INTEGRATION=1` and setup has generated the demo inputs.
