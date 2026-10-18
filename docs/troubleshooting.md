# Troubleshooting

- **Blender not found:** install Blender separately. Set `BLENDER_BIN` to the actual executable, not its containing directory/app bundle, or pass `--blender` before the CLI subcommand. On macOS the default is `/Applications/Blender.app/Contents/MacOS/Blender`.
- **Missing SVG/USD operator:** run `badgegen doctor`. This project is tested with Blender 5.1.2; older/new major releases may have incompatible export APIs. Install the tested release or report the exact doctor result.
- **Pillow missing:** use the checkout's `.venv` Python, not the system interpreter. Re-run `scripts/setup.py` to install dependencies.
- **Missing template:** run setup to create the original demo template. Private/new profiles need their own template and valid paths.
- **Figma permission:** check the intended desktop/cloud account and exact node. No token is bundled. If only screenshots are available, obtain native exports before proceeding.
- **Unsupported SVG / missing font:** outline strokes/text and flatten masks in an export copy. Do not silently redraw typography or change the original design.
- **Ambiguous texture/gray:** inspect sibling IDs, names and coordinates; use explicit mappings for nonstandard layouts. Never turn the colored PNG gray to fill a missing source.
- **Mesh/radius check failure:** inspect `build.log`; simplify redundant contour segments or fix the template mapping. Keep the approved radius and don't hide defects by changing roughness. Hole-heavy shapes may need constrained triangulation and a different modeling path.
- **Model appears black:** metal needs viewer-provided environment/reflections. Both models deliberately exclude lights and cameras. Inspect in the intended display environment.
- **Output locked:** another build may still own `.badgegen.lock`. Check the PID written there. Remove a stale lock only after confirming no build is active.
- **Prepared bundle moved:** its paths may be absolute; copy all dependencies and rewrite them relative to its recipe.

Report bugs with OS, Python/Blender versions, command, error/log excerpt and an original minimal reproducer. Remove private paths, URLs, tokens and artwork before posting. Do not attach proprietary Figma exports by default.
