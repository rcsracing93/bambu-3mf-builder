# bambu-3mf-builder

Build a ready-to-slice **Bambu Studio project file (.3mf)** from a short job file, and check any Bambu 3MF against rules
taken from the published Bambu Studio source. It works as a Claude skill, and the scripts also run on their own.

- Printer, process, filament presets and colors, per-object and per-part settings, modifiers and negative parts.
- Height ranges (variable layer height): fine layers for a chamfer or a logo band, normal layers elsewhere, and other settings or a color per band.
- Multi-color projects with Studio's purge volumes, several plates, pauses and temperature changes at a layer, temperature towers.
- Every setting is checked against the Studio option table (name, type, range, allowed values) before anything is written.
- Python 3.8 or newer, standard library only. No network calls.

Built for Bambu Studio **2.8.2** and **2.9.1**. The builder picks the one installed on your computer, or the newest if none is found. A newer Studio release does not stop it: the file is built for the newest supported version with a warning, and Studio opens it with all settings. Not made by or affiliated with Bambu Lab.

## Use it in Claude

Install it (below), then ask in plain words. Claude writes the job, builds, checks and hands you the 3MF and a short summary.

- "Design a phone holder for a 20 mm tool rail in OpenSCAD and make it print-ready for my P1S in PETG Basic black."
- "Make a Bambu Studio project for bracket.stl on my P1S in Bambu PETG Basic black, 4 walls, 20% infill."
- "Make a temperature tower for Bambu PLA Silk+, 235 down to 215."
- "Print cap.stl at 0.24 mm, but use 0.12 mm layers for the bottom 1.5 mm chamfer and 0.08 mm for the logo in the top 3 mm."
- "Change this 3MF to my P1S and swap filament 2 to white PETG Basic."
- "Check this 3MF. Is anything personal in it before I share it?"

Scenarios, what to give Claude, and everything you can ask for: [docs/using-in-claude.md](docs/using-in-claude.md).

## What "tested" means here

Every example in `examples/` is built, then sliced by a real headless Bambu Studio of both versions, and the result is graded
(settings reach the G-code, tool changes and prime tower are right, pauses and temperatures sit on the requested layers, height ranges give the requested layer heights, a Studio
re-save still slices). See [docs/support-matrix.md](docs/support-matrix.md).

With Bambu Studio installed, `--slice-check` slices the file you just built and tells you whether Studio accepted it: `python3 scripts/build_bambu_3mf.py job.toml --slice-check`. The program is found in the usual install places; `--studio-path PATH` (the program, the app or the install folder) or the `BAMBU_STUDIO_PATH` variable overrides that. Tested with Studio 2.8.2 and 2.9.1 on Linux and Studio 2.9.1 on macOS (Apple silicon); not yet run on a Windows install.

A job may name one of your own saved Studio presets (process or filament) instead of a system preset. The builder reads that one file from your Studio data folder at run time (`--user-presets DIR` or `BAMBU_STUDIO_USER_DIR` to point elsewhere). The preset needs a parent system preset. The 3MF carries the parent and the changed values and the build warns that on a computer without your preset the file opens as a project preset. Details in [references/job-format.md](references/job-format.md).

Next to every 3MF the builder writes `<name>.summary.md`: printer, process, plate type, Studio version, each filament slot with its preset, color, nozzle and bed temperatures and overrides, each object and part with its filament and overrides, layer G-code, and the warnings. With `--slice-check` it also holds Studio's print time and filament grams per plate. It holds file names only, no paths. `--no-summary` turns it off.

That is **CI-verified, not print-tested**. A file that slices correctly can still print badly on your machine. Open it in Studio,
look at the preview, and slice it yourself.

## Install

Three ways. Pick one.

**1. Claude Code**

1. In Claude Code, run `/plugin marketplace add rcsracing93/bambu-3mf-builder`.
2. Then run `/plugin install bambu-3mf-builder@bambu-3mf-builder`.

**2. Claude app (web, desktop, mobile)**

1. Download `bambu-3mf-builder-skill.zip` from the latest GitHub release of this repository.
2. In Claude, open Customize, then Skills.
3. Click "+", then "+ Create skill".
4. Choose "Upload a skill" and pick the zip.
5. Code execution must be on, or the skill cannot run its scripts.

**3. Without Claude**

1. Clone the repository.
2. Run `python3 scripts/build_bambu_3mf.py job.toml` (Python 3.8 or newer; nothing else to install).

The release zips are under 10 MB unpacked (`tests/test_release_size.py` enforces it). `bambu-3mf-builder-plugin.zip` is the same content packed as a plugin.

## Use

```
python3 scripts/build_bambu_3mf.py examples/single_p1s.toml --out cube.3mf
python3 scripts/validate_bambu_3mf.py cube.3mf            # add --strict (every class an error except PRIVACY, which stays a warning, SKIPPED, a warning, and TOOL, a note), --privacy (personal-data scan), --json
python3 scripts/build_bambu_3mf.py --list filament --printer "Bambu Lab P1S 0.4 nozzle" --match PETG
```

A job file:

```toml
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"

[[filament]]
preset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"
color = "#C12E1F"

[[object]]
name = "Cube"
[[object.part]]
mesh = "cube.stl"
filament = 1
```

Every key is in [references/job-format.md](references/job-format.md). Twelve working jobs with a gallery are in
[docs/examples.md](docs/examples.md). The validator rules are in [references/rules.md](references/rules.md).

## Patch an existing project

A job with `patch = "file.3mf"` changes a project that Bambu Studio saved and writes a new file: another process or filament preset, a new
colour, a setting (project, object or part), the plate type, a pause or a temperature change. The geometry is never touched and every
other entry is copied byte for byte. The result is checked after writing. It can also add a filament slot, remove the last one if nothing uses it, and patch a sliced file (its G-code is dropped). The designer's credit stays; local ids and paths are removed. It can also change the printer to another one with the same bed (X1 Carbon, X1, X1E, P1S, P1P; A1 and P2S). See "Patch mode" in [references/job-format.md](references/job-format.md).

```toml
patch = "bracket.3mf"
output = "bracket-red.3mf"
[[filament]]
slot = 1
color = "#C12E1F"
[settings]
wall_loops = 4
```

## No AMS?

A pause at a layer (`[[layer_gcode]]` with `pause = true`) lets you swap filament by hand. See [references/multicolor.md](references/multicolor.md)
for colors, purge cost, the prime tower and the pause.

## Limits

- Meshes: warning above 2 million triangles, strong warning above 5 million, stop above 10 million in total unless the job sets `allow_large = true`.
- STEP is not read. Export STL, OBJ or 3MF.
- Per-object overrides of per-extruder (variant) print keys are not supported yet.
- Printers with more than one nozzle (H2D) build and validate, with fewer real-slicer tests than single-nozzle printers.
- Objects are placed in a grid or at the `position` you give. There is no nesting.

## Privacy

The scripts read the files you give them and write the file you ask for. They make no network calls and send nothing anywhere.
The builder writes no account IDs or user names. Text you type yourself goes into the file as you typed it (settings, G-code, and the notes of a user preset); for a user preset the builder warns when one of its values looks like a local path or an e-mail address. The validator reports a file that has them (rule R33, with `--privacy`; a warning, it never changes the exit code),
and CI scans every test fixture. Studio's own saves carry such fields (for example `DesignerUserId`), so before you attach a 3MF
to an issue run:

```
python3 scripts/sanitize_3mf.py in.3mf out.3mf --report
```

## Tests

```
python3 tests/run_all.py
```

runs the unit tests and needs only Python. The real-slicer tests (`tests/studio_slice.py`, `tests/studio_roundtrip.py`) need a
Bambu Studio AppImage and are run by `.github/workflows/studio.yml`; see [CONTRIBUTING.md](CONTRIBUTING.md).

## Related projects

Facts below come from each project's public listing. Where a listing could not be confirmed, the entry says less.

Prior art. Ideas from these two MIT-licensed repos were read and re-implemented. No code was copied (see [NOTICE](NOTICE)).

- [alexjfalcao/bambu-3mf-skill](https://github.com/alexjfalcao/bambu-3mf-skill) - a 3MF builder for Bambu Studio. Ours takes its settings rules from Bambu Studio's own source and pins Studio versions.
- [m-esm/bambu-3mf-export](https://github.com/m-esm/bambu-3mf-export) - a 3MF builder for Bambu Studio. Ours adds option validation, towers, inlays and a validator for real Studio files.

Other tools. These are not 3MF builders. They are listed so you can find them.

- bambu-printer-mcp ([GitHub](https://github.com/offthehook-implication870/bambu-printer-mcp)) - MCP server with a slice tool for STL files. Ours writes the project file and checks it in a real Studio run in CI.
- mcp-3D-printer-server - MCP server for 3D printer control. Ours does not talk to printers.
- bambu-cli (tobiasbischoff) - agent skill for managing Bambu Lab printers. Ours does not talk to printers.
- text-to-cad "bambu-labs" skill (earthtojake) - part of a text-to-CAD tool set that goes from a prompt to a Bambu Lab print. Ours only builds and checks the 3MF.

## License

AGPL-3.0, see [LICENSE](LICENSE). The vendored Bambu profiles in `studio/` are AGPL-3.0 from Bambu Studio. Third-party notices
are in [NOTICE](NOTICE).
