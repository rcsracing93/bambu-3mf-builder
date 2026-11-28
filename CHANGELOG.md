# Changelog

## 1.3.0 (2026-11-27)

Everything here is validated (built, checked and sliced in a real Bambu Studio), not print-tested.

### A new Studio release never stops a build
- With `studio = "auto"` and a Studio newer than the newest supported version, the file is built for the newest supported version with a
  warning. Studio opens an older 2.x project with all settings. A newer major version gets a stronger warning and an automatic slice check.
  If the installed version cannot be read, the build uses the newest supported version with a warning.
  `strict_version = true` or `--strict-version` keeps the old stop.
- Patch mode and the validator accept files from a newer 2.x, with warnings for settings they do not know.

### Bambu Studio 2.9.2
- Supported versions: 2.8.2.61, 2.9.1.70 and 2.9.2.62. Files built for 2.8.2 and 2.9.1 are byte-identical to 1.2.0.
- 2.9.2 rules: nozzle temperature minimum 165 °C, the 3D Effect Plate, the Engineering Plate for Bambu PLA Basic, Matte and Silk+.
- A setting that only a newer supported version has gives "needs Studio X or newer" on an older one.

### New 2.9.2 functions
- `seam_headroom` (Studio's embedded seam headroom; an object setting).
- `color_penetration_cross_parts`. It acts only on color painted in Studio, so it does not change jobs that use one part per color.
- Manual purge matrix: `purge = { "1>2" = 450 }`, 0 to 900 mm3 (2.8.2, 2.9.1) or 0 to 2000 mm3 (2.9.2). Other pairs keep the automatic values.
- `plate_type = "3D Effect Plate"` with its bed temperatures, in builds and in patch mode.
- `--slice-check` shows the print time per plate and the grams and metres per filament, in the console and in the summary file.

### Calibration prints
- `[calibration] type = "flow"`, `"pa"`, `"mvs"` or `"tolerance"`: the builder makes the objects, with each value engraved 0.5 mm deep on
  a top face. Flow: pads with their own flow ratio. Pressure advance: a tower with an `M900 K` line per band. Max volumetric speed: a tube
  with a faster outer wall per band. Tolerance: square pegs and holes with labelled clearances.
- Calibration ledger: a `calibration.toml` with your measured values per spool, and `[[filament]] spool = "name"` to use them. The values
  become changed settings, and the summary names them.

### Other
- Setting recipes: `recipe = "visible-face"`, `"strong"` or `"fast-draft"` set a documented group of Studio settings. Your own settings win.
- PETG brim lint: a warning when PETG prints with a brim gap under 0.2 mm.
- `--orca-lint` lists the settings of a job that OrcaSlicer drops or renames, with the place in the Orca source. It writes nothing.
- The README starts with what the skill does and a table of examples.

### Install
- The README now says: the skill is not in Anthropic's plugin directory, so a search in Claude finds nothing. The marketplace commands
  work only in Claude Code (`/plugin ...` in a session, or `claude plugin ...` in a terminal). In the Claude app, upload the skill zip.

## 1.2.0 (2026-11-21)

### Height ranges (variable layer height)
- New `[[object.height_range]]` in a job: a layer height for a band of one object, for example 0.12 mm for a chamfer at the bottom and
  0.08 mm for a logo at the top, with the process layer height everywhere else. A band can also have its own walls, infill, speeds and
  other part settings, and its own filament (a color band). This is Studio's "height range modifier".
- The builder refuses what Studio would drop or not slice: overlapping bands, a layer height outside the nozzle's limits, bands with
  organic tree supports, and different bands for objects that share a plate with a prime tower.
- New validator rule R40 checks the height ranges of any 3MF, including files saved by Studio or edited by hand.
- Patch mode keeps the height ranges of the file it changes.
- The summary lists each band.
- New example: `examples/height_ranges.toml`.

### Docs
- The guide says that filament numbers in a project do not need to match your AMS slots: Studio matches them when you print.

## 1.1.1 (2026-11-17)

### Calibration tower
- Stronger tower that does not break off the plate or snap when you take it off: base 2 mm thick and wider, body 14 mm deep (was 10), a 4 mm wall under the overhang, 1.2 mm roof.
- The two stringing pins are now side by side across the tower, so you can see the strings between them from the front. Pins are 3 mm wide.

## 1.1.0 (2026-11-16)

First public release. Supports Bambu Studio 2.8.2.61 and 2.9.1.70.

### Build
- Job file (TOML) to a Bambu Studio project 3MF. Printer, process and filament presets come from Bambu's own profiles for the pinned Studio version; every setting is checked against Studio's option table.
- Exact preset names; a near miss fails with the closest names. `--list printer|process|filament` shows the selectable names.
- Friendly setting names (`walls`, `infill`, `nozzle_temp`, `bed_temp`, ...), listed in `references/job-format.md`.
- Multi-color projects with Studio's automatic purge volumes, several plates, modifier, negative and support parts, per-object and per-part settings.
- Layer pauses, temperature changes and temperature towers. `shape = "calibration"` makes a labeled calibration tower: one band per temperature, each with an overhang, a bridge and stringing pins.
- The prime tower footprint is reserved and checked.
- Your own saved Studio presets can be used as a base. Only the named preset file is read, at run time; the 3MF records its parent preset the way Studio does.
- Sparse infill defaults to Rectilinear instead of Grid (a job can set any pattern).
- A summary file `<name>.summary.md` next to every 3MF: presets, colors, temperatures, overrides, layer G-code and warnings. `--no-summary` turns it off.
- Settings lint (warnings only): temperatures outside the filament's range, chamber temperature on a printer without chamber control, inlays thinner than one layer.
- `--slice-check` slices the built file in your installed Bambu Studio and reports print time and filament per plate (Linux and macOS).

### Validate
- 39 format rules and 6 untrusted-input guards, each traced to the Bambu Studio source (`references/rules.md`).
- Modes: default (errors only for what Studio rejects or misreads), `--strict`, and `--privacy` (personal-data scan, never an error). `--json` output.
- Reads both forms Studio writes: the normal project file and File > Export > Export Generic 3MF.

### Patch an existing project
- `patch = "file.3mf"` changes a Studio 2.8.2 or 2.9.1 project and writes a new file: process and filament presets, colors, settings (project, object, part), filament per object or part, plate type, pauses, temperature changes and custom layer G-code.
- Add a filament slot, or remove the last one when nothing uses it.
- Change the printer between printers with the same bed: X1 Carbon, X1, X1E, P1S and P1P, and A1 and P2S. Presets follow the same match Studio makes when you pick another printer.
- The designer's changes on a swapped preset are dropped and listed; `keep_changes = true` keeps them.
- The designer's credit stays (title, designer, licence, description). Only local data is removed: local user ids, print-host settings and absolute paths.
- Every entry the job does not touch keeps its exact bytes. The output is read back and checked before it replaces anything.

### Privacy
- The builder writes no account ids, user names or local paths. `scripts/sanitize_3mf.py` cleans a file before you share it.

### Tests
- Unit tests, plus real headless Bambu Studio slicing and save round trips for both versions. Sliced and checked by Studio, not print-tested on every printer (`docs/support-matrix.md`).

### Install
- Claude Code: `/plugin marketplace add rcsracing93/bambu-3mf-builder`, or download the skill or plugin zip from the release.
