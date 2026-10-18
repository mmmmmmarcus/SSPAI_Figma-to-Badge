# Figma mapping and plan

The helper reads `<generator>/profiles.json`. A profile references `examples/<profile>.json`. Register only reviewed local recipes, using `asset_roles: ["border","texture"]` or adding `"clip"` for explicit clipping. Public demo profiles are examples, not calibrated mappings for arbitrary designs.

Required saved metadata is Figma MCP XML or its JSON text-content envelope. The badge tree must have the exact linked frame ID and all geometry IDs. Context must contain badge, gray and texture frames as siblings, aligned within 1 px in X. The texture must be a square frame named `Blender贴图`; the gray frame must be `<badge name>_灰`. These checks establish ownership, not visual equivalence. Nonconforming layouts need explicit mapping and a direct recipe instead of guessing.

## Plan example

IDs below are synthetic. Replace all nodes and paths with observed evidence; never call Figma with example IDs.

```json
{
  "badge_url": "https://www.figma.com/design/EXAMPLE/Badge?node-id=1-2",
  "profile": "demo-silver",
  "badge_metadata": "badge.xml",
  "context_metadata": "context.xml",
  "mapping_reason": "Fixed round disk and texture face match; only the central decoration is replaced.",
  "assets": {
    "border": {"node_id": "1:3", "local_file": "decoration.svg"},
    "texture": {"node_id": "1:5", "local_file": "texture.png"}
  },
  "png_exports": {
    "color": {"node_id": "1:2", "local_file": "Badge.png"},
    "gray": {"node_id": "1:4", "local_file": "Badge_灰.png"}
  }
}
```

`border` is the generic historical name for the replaceable geometry role. In demo profiles it is the central star, **not the outer disk**. Inspect the profile before mapping. The template target is `Decoration`; its bounds control placement and size. A new outer silhouette needs a new template/body mapping.

An asset may instead use `url` plus `source_response` (a saved response containing that exact URL). Only observed HTTPS assets or localhost:3845/assets exports are allowed. Download before URLs expire. `local_file` is relative to the plan (absolute paths also work). Obtain it from native Figma export, not a screenshot or redraw. Geometry must be closed SVG fills; outline text/strokes and flatten masks. `clip` must belong to the linked frame.

The dedicated texture must be PNG/JPEG, square and at least 128 px. Prefer full-resolution native export. The helper also accepts a saved tool image envelope for texture only, but use it only if the tool truly isolates the texture frame without canvas/other layers. Full color/gray deliverables must be native exports.

`prepare` copies source assets, evidence and hashes into a new directory, keeps the profile's template reference, and writes a recipe. It works offline while those paths remain available; to move a prepared bundle between machines, copy its template too and rewrite paths relative to the recipe. Never publish private provenance/exports to the code repository.

The helper currently replaces exactly one SVG role and one `Classic Mix` texture material. Multi-layer/multi-material templates require a direct JSON recipe (see `docs/recipes.md` in the generator), with the same provenance and delivery checks. Do not present this helper as a universal Figma parser.
