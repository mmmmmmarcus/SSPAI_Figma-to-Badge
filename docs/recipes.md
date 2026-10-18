# Templates, recipes and materials

Recipes follow [recipe.schema.json](../schema/recipe.schema.json); the Python loader is authoritative and validates additional cross-field constraints. Paths are relative to the recipe. `badgegen inspect recipe.json` resolves inputs without launching Blender.

## Minimum recipe

```json
{
  "schema_version": 1,
  "id": "my-badge",
  "output_name": "My Badge",
  "template": "template.blend",
  "layers": [],
  "textures": [],
  "export": {"formats": ["glb", "usdz"], "usdz_lights": false, "usdz_world": false}
}
```

A template contains actual geometry/materials/UVs, not a magic universal converter. Empty `layers` exports prebuilt geometry. For replacement, add an entry with `target` (existing object), `svg`, `material` (existing material), `thickness_m`, `resolution` and `fit`. `template_bounds` independently fits X/Z; use only when proportions match. `template_width_top` keeps uniform scale aligned at the top. Target bounds determine placement. Textures select an existing material with one Image Texture node (or explicit `node` name).

Axis convention: X/Z is the badge plane, -Y front, +Y rear; dimensions are meters. GLB uses exporter Y-up conversion. Demo width .22 m is intentionally enlarged for AR; it is not a physical 5 cm manufacturing file. STEP/STP is not supported.

The reproducible [demo builder](../scripts/create_demo.py) creates a one-piece rounded disk, inset colored face, and a placeholder for the replaceable star. It includes a packed original gradient texture, no camera/light and no branding. Its 1.1 mm outer round is applied in the builder; recipe finishing rounds the star at .12 mm. The demo does **not** exercise engraving or continuous-body reconstruction, and does not validate every production radius path.

## Materials

`base_color` is **linear RGBA**, not sRGB hex. `metallic` and `roughness` are in [0,1]. Shader must contain one Principled BSDF; linked inputs cannot be replaced by scalar overrides.

| Finish | Linear color | Metallic | Roughness |
|---|---|---|---|
| Pale champagne | .88, .78, .60, 1 | 1 | .16 |
| Polished silver | .83, .87, .91, 1 | 1 | .18 |
| Etching | Same as parent metal | 1 | .32 |

These are artistic PBR presets, not measured alloys. Viewer environment/tonemapping changes appearance; no lighting is embedded. Preserve texture UVs and use a dedicated color map without baked metal highlights.

## Finishing for new profiles

- `continuous_bodies`: explicit `target`, `backing`, `surface` object mapping. Rebuilds the backing from a verified exterior contour and removes the old plate. Current routine assumes one enclosing exterior, not arbitrary multiple islands/through holes.
- `engravings`: explicit `target`, `cutter`, `depth_m`, `material`; recess on +Y and removes the cutter. Start with .0002 m only when body thickness permits.
- `bevels`: target, width_m, segments; calibrated exterior selection additionally uses outer_width_m, thin_width_m, thin_feature_m. Outer radius requires continuous-body mapping. Do not label a generic bevel as proof of exported circular R geometry.
- `backings`: explicit outline SVG and thickness for applicable older structures.

Read worker reports for topology, cleanup, cap normals, engraving, actual exported R profile and R surface checks. Unsupported geometry should fail explicitly. Preserve cap triangles for hole-heavy shapes; indiscriminate planar merging can create invalid export triangulation. Native Quick Look / target app inspection remains required for a new production profile.

## Adding a reusable profile

1. Create an original or authorized `.blend` template via a reproducible script, with stable object/material names. Pack images, no external blend libraries or scripts.
2. Create `examples/<safe-profile-id>.json`, validate and export it.
3. Register it in `profiles.json` with `asset_roles`, honest `status` and a structural description. Generic helper supports exactly one geometry replacement and `Classic Mix` texture; direct recipes can have multiple layers/materials.
4. Supply current Figma assets and PNG pair via the plan workflow; inspect both formats in the target viewer. Add regression tests for that mapping.
5. Never commit proprietary templates, designs or source evidence without the rights to redistribute them.

`png_exports` entries have `name`, `image` and `node_id`; native Figma PNG bytes are copied and hashed. Names must remain exact and filesystem-safe. `id` is a separate internal ASCII slug. Prepared recipes use resolved paths; copy dependencies and make paths relative if sharing a portable input bundle.
