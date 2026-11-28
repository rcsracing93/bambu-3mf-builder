# bambu-3mf-builder

You describe a print in plain words. Claude builds a Bambu Studio project file (.3mf) that is ready to open, with the printer,
the filaments, the AMS colors and the settings already set. You do not paint the model and you do not click through settings. You open
the file in Studio, check it, slice it and print it. The skill does not send anything to your printer. It slices only to check the
file (`--slice-check`). It can also check any Bambu 3MF against rules taken from the Bambu Studio source.

## Examples

Each row works today. The file in the last column builds that project (or tests it).

| What you ask Claude | What you get | Example |
|---|---|---|
| "Print this keychain in four colors: base.stl black, ring.stl white, text.stl red, logo.stl blue." | One object with one part per color, each part on its own AMS slot. No painting. | `examples/colors_4.toml` |
| "Make sign.stl white, with the text from text.stl as a red inlay." | A body part and an inlay part in two filaments, in one object. | `examples/inlay_no_penetration.toml` |
| "Put a red cube and a blue cube on one plate." | Two objects on one plate, each in its own filament. Studio changes filament on every layer, so it purges (time and filament). | `examples/objects_two_colors.toml` |
| "Print two copies of part.stl: one with 5 walls and 40% infill, one with 2 walls and 5%." | Settings per object, written so that Studio keeps them when it opens the file. | `examples/per_object_settings.toml` |
| "Make a temperature tower for Bambu PLA Basic." | A ready-made tower with the temperature cut into each band, and the temperature change at each band. | `examples/calibration_tower.toml` |
| "Make a flow calibration, a pressure advance tower, a max volumetric speed test and a tolerance test." (new in 1.3.0) | Calibration prints with each value engraved on the part: flow ratio per pad, `M900 K` per band, a speed per band, and peg and hole clearances. | `examples/calib_flow.toml`, `examples/calib_pa.toml`, `examples/calib_mvs.toml`, `examples/calib_tolerance.toml` |
| "Pause at 10 mm so I can drop in magnets." | A pause at the layer for 10 mm. | `examples/pause.toml` |
| "Use 0.12 mm layers for the bottom 2 mm and 0.08 mm with 4 walls for the top 3 mm." | Height ranges: fine layers where you ask, normal layers elsewhere. | `examples/height_ranges.toml` |
| "Take my file bracket.3mf and change filament 2 to white PETG Basic." | Patch mode: it changes your own Studio file and keeps everything else as it was (the geometry byte for byte). | `tests/test_patch_core.py` |

More prompts and what to give Claude: [docs/using-in-claude.md](docs/using-in-claude.md). All examples with pictures: [docs/examples.md](docs/examples.md).

## Formats and slicers

- 3MF is an open standard (3MF Consortium; ISO/IEC 25422:2025). The builder writes a Bambu Studio *project* 3MF: core 3MF geometry plus
  Bambu's project settings.
- Tested with Bambu Studio 2.8.2, 2.9.1 and 2.9.2 (table below).
- OrcaSlicer 2.5.1 (command line, macOS): it refuses the file unless you add `--allow-newer-file`. With that flag, three examples sliced
  with the same layers, colors, tool changes and settings as Studio. The OrcaSlicer app is not tested yet. PrusaSlicer is not tested.
- `--orca-lint` lists the settings of a job that OrcaSlicer drops or renames, with the place in the Orca source.

## What it does

- Printer, process, filament presets and colors, per-object and per-part settings, modifiers and negative parts.
- Height ranges (variable layer height): fine layers for a chamfer or a logo band, normal layers elsewhere, and other settings or a color per band.
- Multi-color projects with Studio's purge volumes, several plates, pauses and temperature changes at a layer, temperature towers, calibration prints.
- Every setting is checked against the Studio option table (name, type, range, allowed values) before anything is written.
- Python 3.8 or newer, standard library only. No network calls.

Built for Bambu Studio **2.8.2**, **2.9.1** and **2.9.2**. The builder picks the one installed on your computer, or the newest if none is found. A newer Studio release does not stop it: the file is built for the newest supported version with a warning, and Studio opens it with all settings. Not made by or affiliated with Bambu Lab.

| Studio version | Build | Validate | Patch mode | Real-slicer tests |
|---|---|---|---|---|
| 2.8.2.61 | yes | yes | yes | Linux CI |
| 2.9.1.70 | yes | yes | yes | Linux CI, macOS |
| 2.9.2.62 | yes | yes | yes | Linux CI, macOS |
| newer 2.x | yes, for 2.9.2.62, with a warning | yes, with the 2.9.2.62 rules (unknown settings are warnings) | yes, with a warning | - |
| newer major (3.x) | yes, for 2.9.2.62, with a stronger warning and an automatic slice check | tables skipped, with a warning | refused | - |

Studio opens a project from an older 2.x release with all its settings; only a file from a higher MAJOR version opens as geometry only.
`strict_version = true` (or `--strict-version`) stops instead of building for an older version.

## Use it in Claude

Install it (below), then ask in plain words. Claude writes the job, builds, checks and hands you the 3MF and a short summary.
Other things you can ask:

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

With Bambu Studio installed, `--slice-check` slices the file you just built and tells you whether Studio accepted it: `python3 scripts/build_bambu_3mf.py job.toml --slice-check`. The program is found in the usual install places; `--studio-path PATH` (the program, the app or the install folder) or the `BAMBU_STUDIO_PATH` variable overrides that. Tested with Studio 2.8.2 and 2.9.1 on Linux and Studio 2.9.1 and 2.9.2 on macOS (Apple silicon); the Linux CI also runs Studio 2.9.2. Not yet run on a Windows install.

A job may name one of your own saved Studio presets (process or filament) instead of a system preset. The builder reads that one file from your Studio data folder at run time (`--user-presets DIR` or `BAMBU_STUDIO_USER_DIR` to point elsewhere). The preset needs a parent system preset. The 3MF carries the parent and the changed values and the build warns that on a computer without your preset the file opens as a project preset. Details in [references/job-format.md](references/job-format.md).

Next to every 3MF the builder writes `<name>.summary.md`: printer, process, plate type, Studio version, each filament slot with its preset, color, nozzle and bed temperatures and overrides, each object and part with its filament and overrides, layer G-code, and the warnings. With `--slice-check` it also holds Studio's print time per plate and the grams and metres of each filament slot (the console shows them too). It holds file names only, no paths. `--no-summary` turns it off.

That is **CI-verified, not print-tested**. A file that slices correctly can still print badly on your machine. Open it in Studio,
look at the preview, and slice it yourself.

## Install

Read this first:

- This skill is not in Anthropic's plugin directory. A search in Claude finds nothing.
- The marketplace commands work only in Claude Code (terminal or IDE). They do not work in the Claude app.
- In the Claude app (web, desktop, mobile), upload the skill zip (way 2 below).
- Tested with Claude Code 2.1.332. If `/plugin` is unknown, update Claude Code.

Three ways. Pick one.

**1. Claude Code**

Inside a Claude Code session:

1. Run `/plugin marketplace add rcsracing93/bambu-3mf-builder`.
2. Then run `/plugin install bambu-3mf-builder@bambu-3mf-builder`.

Or from a terminal:

```
claude plugin marketplace add rcsracing93/bambu-3mf-builder
claude plugin install bambu-3mf-builder@bambu-3mf-builder
```

`claude plugin details bambu-3mf-builder` then lists the skill.

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

To see which keys of a job OrcaSlicer would drop or rename when it opens the file, run `python3 scripts/build_bambu_3mf.py job.toml --orca-lint`. It writes nothing ([Orca lint](references/job-format.md#orca-lint---orca-lint)).

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
