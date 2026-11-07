# Asset catalog

The source of truth is [`assets/catalog.json`](../assets/catalog.json). Downloads go to `assets/cache/` and built meshes go to `assets/library/`. Both folders are gitignored and can be rebuilt from the catalog:

```bash
python assets/fetch.py                                          # CC0 downloads, MD5-checked (25 MB)
blender -b --factory-startup --python assets/build_library.py  # builds assets/library/*.blend + index.json (40 MB)
```

## Library assets (what Nemotron can place)
Plans don't change. A plan still says `{"op": "add", "asset": "cabin"}`. When the worker runs with `TD_ASSET_LIBRARY=1` (or `-- --library`), the engine adds the library mesh listed under **replaces** below. If the library isn't built, it falls back to the procedural mesh.

| ID | Name | Replaces | Size W×D×H (m) | Triangles | `color` tints | Source / license |
|---|---|---|---|---|---|---|
| `td_cabin_winter` | Snowy log cabin | `cabin` | 3.0 × 2.39 × 2.51 | 2,530 | log walls | Geometry by TinyDirector. Textures CC0: pine_bark (D. Savva), wooden_planks (D. Barresi, C. Baglioni), roof_slates_02 (R. Tuytel) |
| `td_pine_snowy` | Stylized snowy pine | `tree` | 1.5 × 1.46 × 2.49 | 456 | needles | Geometry by TinyDirector. Bark texture CC0 (D. Savva) |
| `ph_fire_pit_lit` | Stone fire pit, logs, flame | `campfire` | 1.08 × 1.07 × 0.60 | 5,459 | **flame** | stone_fire_pit CC0 (Sebastian Platen); logs and flame by TinyDirector |
| `ph_tree_stump` | Tree stump with axe | `rock` | 0.96 × 1.07 × 0.73 | 44,394 | stump wood | tree_stump_01 CC0 (Rob Tuytel), wooden_axe CC0 (Ulan Cabanilla) |
| `ph_lantern` | Glowing wooden lantern | `lamp` | 0.22 × 0.24 × 0.53 | 8,321 | **glow** | wooden_lantern_01 CC0 (James Ray Cock) |

**Supported parameters (every asset):**
- `position`: base center; z=0 is on the ground.
- `rotation`: degrees.
- `scale`: uniform.
- `color`: sets the `TD_Tint` node on slot 0. It keeps the texture detail and shifts the hue. Read-back reports the tint; an asset never recolored reports `#FFFFFF`.
- `animate`, `bounce`, `delete`: work exactly as on procedural assets.

**Every object can be identified by:**
- Blender name = director ID
- custom props `director_id`, `director_asset`, `director_library`
- `library` field in the scene read-back
- the `TinyDirector` collection

## Sources (all CC0-1.0, Poly Haven)
| ID | Kind | Poly Haven asset | Author |
|---|---|---|---|
| `ph_hdri_winter_lake_01` | HDRI 2k | [winter_lake_01](https://polyhaven.com/a/winter_lake_01) | Sergej Majboroda |
| `ph_tex_snow_02` | texture 1k | [snow_02](https://polyhaven.com/a/snow_02) | Rob Tuytel |
| `ph_tex_pine_bark` | texture 1k | [pine_bark](https://polyhaven.com/a/pine_bark) | Dimitrios Savva |
| `ph_tex_wooden_planks` | texture 1k | [wooden_planks](https://polyhaven.com/a/wooden_planks) | Dario Barresi, Charlotte Baglioni |
| `ph_tex_roof_slates_02` | texture 1k | [roof_slates_02](https://polyhaven.com/a/roof_slates_02) | Rob Tuytel |
| `ph_model_stone_fire_pit` | glTF 1k | [stone_fire_pit](https://polyhaven.com/a/stone_fire_pit) | Sebastian Platen |
| `ph_model_tree_stump_01` | glTF 1k | [tree_stump_01](https://polyhaven.com/a/tree_stump_01) | Rob Tuytel |
| `ph_model_wooden_axe` | glTF 1k | [wooden_axe](https://polyhaven.com/a/wooden_axe) | Ulan Cabanilla |
| `ph_model_wooden_lantern_01` | glTF 1k | [wooden_lantern_01](https://polyhaven.com/a/wooden_lantern_01) | James Ray Cock |

CC0 requires no attribution; we credit the authors anyway. **Sketchfab and Poly Pizza are not used.** Sketchfab licenses vary per model (many require attribution, and some forbid commercial use), and downloading needs an account token. Any Sketchfab model added later must have its exact license recorded per entry.

## Art-style fit
Downloaded assets were not assumed to fit the style.
- Poly Haven has **no cabin**, and its pines are 2–17 million polygon photoscans, so the hero cabin and pines are stylized meshes built here and textured with CC0 materials.
- The small scanned props get a shared snow shader (`TD_SnowCover`) so they match the scene.
- Weakest fit: `ph_tree_stump` keeps its scanned dirt mound, which reads as slightly realistic next to the stylized pines.

## Adding an asset
1. Add a `sources` entry, then run `python assets/fetch.py`.
2. Add a `library` entry and a builder function in `assets/build_library.py`.
3. Rebuild the library, then check the render.

New *asset names* would be a schema change. Today the library only re-skins existing names.
