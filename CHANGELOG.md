# Changelog

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
