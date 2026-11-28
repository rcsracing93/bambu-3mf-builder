---
name: bambu-3mf-builder
description: Turn a print described in plain words into a ready-to-open Bambu Studio project 3MF, with printer, filaments, AMS colors and settings already set (no painting, no clicking through settings). Multi-color from STL, OBJ or 3MF parts (one part per color, inlays), several objects or plates, settings per object or part, pauses, height ranges, temperature towers and calibration prints (flow, pressure advance, max volumetric speed, tolerance), setting recipes and a calibration ledger. Also changes an existing Bambu Studio 3MF (presets, colors, settings, plate, slots) and checks any Bambu 3MF against rules from the Studio source. Use for a ready-to-slice Bambu project (also after designing a part in OpenSCAD), an AMS project, a calibration print, exact Bambu preset names, moving a project to another Bambu printer, or removing personal data from a 3MF. Not for G-code, sending to a printer, repairing or scaling models, STEP files, general print advice, or Orca, Prusa or Cura projects.
---

# Bambu 3MF builder

Builds a Bambu Studio project file (.3mf) from a TOML job file, and validates 3MF files. The rules come from the published
Bambu Studio source (tags v02.08.02.61, v02.09.01.70 and v02.09.02.62), not from guessing. Python 3.8 or newer, standard library only.

The output opens in Studio with every setting as written. It is CI-verified (sliced in real headless Studio), not print-tested
on every printer. Say that to the user if they ask whether a print will work.

## When to use

- The user wants a Studio project file with chosen printer, process, filaments, colors, walls, infill, speeds or temperatures.
- The user wants a temperature tower, a pause at a layer, or a multi-color part built from several STL files.
- The user wants a different layer height (or walls, infill, speed, color) in a z band of one object, for example fine layers for a chamfer or a logo: `[[object.height_range]]` (see `examples/height_ranges.toml`). Heights are mm from the object's bottom.
- The user wants a ready-made calibration temperature tower and has no model: `[temperature_tower] shape = "calibration"` (see `examples/calibration_tower.toml`).
- Claude just designed a part (OpenSCAD, CadQuery) and the user wants to print it: export STL, one file per color, then build.
- The user hand-edited a 3MF or got one from someone else and wants it checked.
- The user has a Studio project (their own or from MakerWorld) and wants a change: printer, presets, colors, settings, plate type, a pause, a filament slot. Use patch mode (below).
- The user wants to remove personal data from a 3MF before sharing it: `scripts/sanitize_3mf.py`.

Do not use it to slice, to make G-code, to change a mesh inside an existing project, or for Orca Slicer, Prusa or Cura projects. STEP files are not read: export STL first.

## Steps

1. Ask only what is missing: printer model and nozzle, filament (type and color per slot), mesh files, and any setting the user cares about.
   Use real preset names. List them with `python3 scripts/build_bambu_3mf.py --list printer|process|filament --studio 2.9.2`
   if the user is unsure (see references/job-format.md for the exact preset rules).
2. Studio version: leave `studio` out and the builder detects the installed one (newest supported if none). Set `studio = "2.8.2"`, `"2.9.1"` or `"2.9.2"` only to force it. A file built for an older version also opens in a newer 2.x. If the installed Studio is newer than the newest supported version, the builder still builds (for the newest supported version) and prints a warning: tell the user that Studio opens the file as an older project and keeps all settings. `strict_version = true` (or `--strict-version`) turns this into a stop.
3. Write the job file (references/job-format.md; examples in examples/). One `[[filament]]` per slot, one `[[object]]` per thing
   on the plate, one `[[object.part]]` per mesh.
4. Build: `python3 scripts/build_bambu_3mf.py job.toml --out project.3mf`. The builder checks the whole job first and prints every
   problem with the key and a fix. Nothing is written until the job is clean.
5. Read the printed summary to the user: plates, objects, filament slots, estimated prime tower, warnings.
6. Validate when the file was not made by this builder: `python3 scripts/validate_bambu_3mf.py file.3mf`. Exit 0 means no errors. `--strict` makes every class an error except PRIVACY (stays a warning), SKIPPED (stays a warning) and TOOL (stays a note); `--privacy` adds the personal-data scan.
   Each finding names its rule (references/rules.md).
7. Tell the user to open it in Studio and slice there. Do not claim it was printed.

## Patch an existing Studio project

When the user already has a Studio project and wants a change (a colour, a preset, a setting, the plate type, a pause) and not a new build,
write a job with `patch = "file.3mf"` (references/job-format.md, "Patch mode") and run the same command. The input must be saved by
Studio 2.8.2, 2.9.1 or 2.9.2 (or a newer 2.x, with a warning) with one nozzle; anything else is refused with the reason. A sliced file is accepted (its G-code is dropped, with a warning).
Geometry is never changed. A new filament slot can be added (`slot = n + 1`); the last slot can be removed if nothing uses it. The designer's credit
(title, designer, licence) stays, local ids and paths are removed. A printer change (`printer = "..."`) works between printers with the same bed and nozzle only (X1 Carbon, X1, X1E, P1S, P1P; A1 and P2S, where the per-variant lists are cut again for the new printer); presets follow Studio's alias match, exactly one candidate or a refusal that names the slot. Print-host settings are blanked and a path in a setting is warned about. A swapped preset drops the designer's changes on that slot (the summary lists them);
`keep_changes = true` keeps them. The output is always a new file; read the summary to the user, including its warnings.

## Rules the builder enforces for you

- Settings go in as Studio names (`wall_loops`, `sparse_infill_density`). A wrong name gets the closest matches.
- Values are checked against the Studio option table: type, range, enum. Per-extruder lists are expanded by the builder.
- Values Studio computes itself (colors, filament ids, flush volumes, preset ids) are refused in `settings`. The builder writes them.
- Parts and the prime tower are checked to be inside the printable area and clear of each other (R37).
- Height ranges are checked against Studio's own limits (R40): layer height inside the nozzle limits, no overlaps, no organic tree supports, and the same ranges for every object on a plate with a prime tower. When objects need different ranges, put them on separate plates.
- Sparse infill defaults to Rectilinear, not Grid; keep that unless the user asks for a pattern. Friendly names (`walls`, `infill`, `nozzle_temp`, `bed_temp`) are listed in references/job-format.md. With Studio installed, `--slice-check` slices the result and says whether it worked. Every build also writes `<name>.summary.md` next to the 3MF (`--no-summary` skips it); read it to check what was built.
- A job may name the user's own saved Studio preset (process or filament) by name; it is read from the local Studio data folder at run time (`--user-presets DIR`, `BAMBU_STUDIO_USER_DIR`). The preset must have a parent system preset. Warn the user that on another computer the file opens as a project preset. Never copy a user preset into the repo or an example.
- No account ids, user names or local paths are written, and the validator fails any file that has them (R33).

## Limits

- Large meshes: warning above 2 million triangles, strong warning above 5 million, stop and ask above 10 million. Say the numbers
  to the user and offer to decimate in their CAD tool first. Set `allow_large = true` in the job only after they agree.
- Per-object overrides of per-extruder (variant) print keys are not supported in v1.
- The builder writes plates in a grid, one `position` per object if the user wants control. It does not nest.
- Printers with more than one nozzle (H2D) build and validate, but have had fewer real-slicer tests. Say so.

## Files

- `scripts/build_bambu_3mf.py` builds. `scripts/validate_bambu_3mf.py` checks. `scripts/sanitize_3mf.py` strips personal data from a 3MF.
- `references/job-format.md` every job key. `references/multicolor.md` colors, purge, prime tower, pause without AMS. `references/rules.md` every validator rule. `references/studio-format.md` how a Bambu 3MF is laid out.
- `docs/support-matrix.md` what was tested on which Studio version.
- `studio/<version>/` vendored Bambu profiles and the option table. Do not edit.

## Privacy

Nothing leaves the machine. The scripts make no network calls. Do not paste a user's real 3MF into a public issue: run
`scripts/sanitize_3mf.py in.3mf out.3mf` first.
