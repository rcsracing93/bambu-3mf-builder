# How a Bambu Studio 3MF is laid out

Read from Bambu Studio v02.08.02.61 and v02.09.01.70 (`src/libslic3r/Format/bbs_3mf.cpp`, `PrintConfig.cpp`, `Preset.cpp`). A Bambu
3MF is an OPC ZIP.

| Entry | Content |
|---|---|
| `[Content_Types].xml`, `_rels/.rels` | Standard OPC parts (R18, R19). |
| `3D/3dmodel.model` | Root model. One wrapper object per object, each with component references to `3D/Objects/object_N.model`, plus the build items (position per instance). Metadata `Application` must be `BambuStudio-<version>` or Studio drops every setting (R1). |
| `3D/Objects/object_N.model` | The meshes, one `<object>` per part. |
| `Metadata/project_settings.config` | JSON, every project, printer, filament and process setting. Lists have one entry per filament, per extruder, or per extruder variant. |
| `Metadata/model_settings.config` | Per-object and per-part names, extruder (filament slot), subtype, per-object settings, and the plate list with instance ids. |
| `Metadata/custom_gcode_per_layer.xml` | Pauses, temperature changes and custom G-code by layer height. |
| `Metadata/slice_info.config`, `Metadata/filament_sequence.json` | Plate info. `filament_sequence.json` must come after `model_settings.config` in the archive in 2.8.2 (R34). |

## Things that bite

- **Diff lists.** `different_settings_to_system` has n + 2 entries (process, each filament, printer). Anything that differs from the
  system preset and is not listed is reset when Studio loads the file (R3, R11).
- **Short lists.** Per-extruder-variant lists written with one entry where two or more are needed are misread by both versions: Studio
  resizes them by copying the first value (R35). A short per-filament list gives the missing filaments the value of filament 1 (R5).
  The `filament_mixed_*` lists are the exception: real Studio saves write them with any length from 1 to n (seen in saves, not found
  as a source line).
- **Variant names and ids.** Studio keeps a listed per-extruder override only in slots whose variant name and id match the system
  preset. The builder writes the system values (R38).
- **printer_model.** If it is not a Bambu printer, Studio drops the project config (R9).
- **Part matrix.** The builder centers each part mesh on its own bounding box (its choice, not a Studio rule). The part offset lives
  in the component transform and in the `matrix` metadata. Studio's own re-save multiplies the `matrix` metadata again each time it loads and saves. That is cosmetic:
  meshes and component transforms stay stable.
- **Prime tower.** Studio does not recompute `wipe_tower_x/y` on load, in either version. It only clamps the saved position back
  into the plate; it never moves it away from parts. The builder writes the position and reserves the conservative tower size (R37).
- **2.9.1 additions.** `filament_diameter_map`, `extruder_nozzle_slot`, and aux-fan keys that fall back from additional to optional.
- **Personal data.** Studio-saved files carry `DesignerUserId` and similar. The builder never writes them, R33 fails files that have them.

The vendored profiles in `studio/<version>/profiles/` are copied unchanged from the tag, and `studio/<version>/options.json` is
generated from the source by `scripts/gen_bambu_options.py`.
