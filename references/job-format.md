# Job file format

TOML (JSON with the same keys also works). Paths are relative to the job file. The builder reads the whole file, collects every
problem with its key name, and builds nothing until the file is clean.

## Top level

| Key | Required | Meaning |
|---|---|---|
| `studio` | no | `"auto"` (default: the Studio installed on this computer, or the newest supported one if none is found), `"2.9.2"`, `"2.9.1"` or `"2.8.2"`. The full tag (`"2.9.2.62"`) also works. A pinned version always builds for that version. With `"auto"` and a Studio that is newer than the newest supported version, the file is built for the newest supported version with a warning in the console and the summary file (Studio opens an older 2.x project with all its settings). A newer MAJOR version (3.x) gets a stronger warning, and `--slice-check` runs by itself when the Studio program is found. An installed Studio older than the oldest supported version is an error, unless the job pins a supported version. A Studio between two supported versions builds for the nearest one below it, with a warning. |
| `strict_version` | no | `true` keeps the old hard stop: with `"auto"`, any installed Studio that is not a supported version exactly is an error. The `--strict-version` flag does the same. Default `false`. |
| `output` | no | Output file name. `--out` on the command line wins. |
| `title` | no | Project title shown by Studio. |
| `printer` | yes | Printer preset, e.g. `"Bambu Lab P1S 0.4 nozzle"`. |
| `process` | yes | Process preset made for that printer, e.g. `"0.20mm Standard @BBL X1C"`. |
| `plate_type` | no | Bed type, e.g. `"Textured PEI Plate"`. The builder refuses a type the filaments do not support (bed temperature 0 in the preset). `"3D Effect Plate"` needs Studio 2.9.2; its bed temperature keys are `effect_plate_temp` and `effect_plate_temp_initial_layer`. In 2.9.2 the Bambu PLA Basic, Matte and Silk+ presets also support the Engineering Plate (55 °C). |
| `allow_large` | no | `true` lets a build go on above 10 million triangles in total (default `false`). Use it only after the user agreed. |
| `purge` | no | `"auto"` (default: Studio's color-based purge volumes) or `"default"` (140 + 140 mm3 for every pair), or a table of hand-set volumes, e.g. `{ "1>2" = 450, "2>1" = 300 }` (see "Manual purge matrix" below). |
| `settings` | no | Table of Studio process settings, applied to the whole project. |

List real preset names with `python3 scripts/build_bambu_3mf.py --list printer|process|filament [--printer NAME] [--studio V] [--match text]`.
Base templates (names starting with `fdm_`) are not selectable presets and are refused.

### Your own Studio presets (user presets)

`process` and a filament `preset` may name one of your own saved Studio presets in place of a system preset. The builder reads that one preset file from your Bambu Studio data folder when it runs (macOS `~/Library/Application Support/BambuStudio`, Windows `%APPDATA%\BambuStudio`, Linux `~/.config/BambuStudio`; the file is `user/<id>/filament/<name>.json` or `user/<id>/process/<name>.json`). Nothing else in that folder is read, and a job that names only system presets never opens it.

- Where to look: `--user-presets DIR`, else the `BAMBU_STUDIO_USER_DIR` variable, else the data folder above. DIR may be the data folder, its `user` folder or one id folder. A missing folder or file is an error that says where it looked.
- The preset needs a parent: it must have been saved from a Bambu system preset. A preset made from scratch (empty `inherits`) is refused in this version. A printer (machine) user preset is not supported; name the system printer.
- The 3MF holds the preset's name in `filament_settings_id` / `print_settings_id`, the parent system preset in `inherits_group`, and every key that differs from the parent (your preset's values plus any job override) in `different_settings_to_system`. This is the form Studio writes.
- Warning in the console and the summary file: the 3MF holds the preset's name and changed values, not the preset. On a computer without a user preset of that name, Studio opens the slot as the parent system preset with those values applied as edits (a project preset). The file also carries the name of your preset, so check it before you share the file.
- Presets and values from your data folder are never copied into the repo, a fixture or an example. The tests use a generated fake folder.

## `[[filament]]` (one per slot, up to 16)

| Key | Required | Meaning |
|---|---|---|
| `preset` | yes | Filament preset made for the printer. |
| `color` | yes | `"#RRGGBB"`. |
| `settings` | no | Table of Studio filament settings for this slot, e.g. `nozzle_temperature = "225"`. |

Slots are numbered 1, 2, 3 in file order. A slot no part uses stays in the project and its purge volume is still counted (warning).

### Manual purge matrix

`purge` can be a table. Each key is `"N>M"`: the purge volume in mm3 when the print changes from slot N to slot M. Each value is a number.

```toml
purge = { "1>2" = 450, "2>1" = 300 }
```

- Pairs you do not list keep the same values as `purge = "auto"`.
- Both slots must exist, and N must differ from M.
- The value must be 0 or more, and at most the manual limit of the job's Studio version: 900 mm3 for 2.8.2 and 2.9.1, 2000 mm3 for 2.9.2.
  Studio 2.9.2 raised the limit for hand-typed values. Its own auto calculation still stops at 900 mm3.
- The builder refuses a bad entry and names the pair, the value and the limit.
- The builder writes the values in `flush_volumes_matrix`: row = from slot, column = to slot. Studio needs no extra flag. It keeps the
  matrix when it opens the file, and it recalculates a matrix only when the user changes a filament color in the GUI.
- Patch mode does not take this table. It keeps the existing matrix and calculates the new slot's row and column itself.
- The result is validated, not print-tested.

## `[[object]]`

| Key | Meaning |
|---|---|
| `name` | Object name. Must not contain `< > : / \ ? * "` or a pipe character; the builder refuses it (R31). |
| `plate` | Plate number, from 1. Default 1. |
| `position` | `[x, y]` in mm on the plate. Default: the builder places objects in a grid. R37 checks the result. |
| `copies` | Number of identical instances. Default 1. |
| `settings` | Per-object process settings (same names and checks as top-level `settings`). |
| `height_range` | Height ranges, see `[[object.height_range]]` below. |

Each object needs at least one `[[object.part]]`.

## `[[object.part]]`

| Key | Meaning |
|---|---|
| `mesh` | Path to `.stl`, `.obj` or `.3mf`. STEP is not supported. |
| `name` | Part name. Same character rule as the object name. |
| `filament` | Filament slot number for this part. |
| `type` | `normal_part` (default), `modifier_part`, `negative_part`, `support_blocker`, `support_enforcer`. Needs a normal part in the same object (R29). |
| `units` | `"mm"` (default) or `"inch"`. |
| `settings` | Per-part process settings. |

Mesh limits, counted over all parts together and per part: warning above 2 million triangles, strong warning above 5 million, refused
above 10 million unless the job sets `allow_large = true` (decimate in the CAD tool first). Parts must be closed, correctly oriented surfaces (R24); an inside-out mesh is an error because
Measured on a Mac (Apple silicon, 32 GB, `tests/machine_check.py`): 10 million triangles build in 26 s and validate in 54 s with 4 GB peak memory; 2 million take 5 s and 10 s.
Studio would flip it silently and hide the mistake.

## `[[object.height_range]]` (height ranges: a layer height, and other settings, for a band of one object)

Studio's "height range modifier" (Objects list > right click > Height range Modifier). Stored in `Metadata/layer_config_ranges.xml`.

| Key | Meaning |
|---|---|
| `min_z`, `max_z` | The band, in mm from the bottom of the object. `0 <= min_z < max_z`. |
| `layer_height` | Layer height in the band, in mm. Required. Must be inside the nozzle's limits (`min_layer_height` / `max_layer_height` of the printer; 0.08-0.28 mm for a P1S 0.4 nozzle), or Studio drops every range of the object. |
| `filament` | Optional filament slot for the band (a color band). Default: the object's own filament. |
| `settings` | Optional `[object.height_range.settings]`: the settings a part can have (walls, infill, speeds, ...). Settings that only an object can have (supports, seam, brim, ...) are refused. |

```toml
[[object.height_range]]          # fine layers for a chamfer at the bottom
min_z = 0
max_z = 1.5
layer_height = 0.12

[[object.height_range]]          # fine layers and 4 walls for a logo at the top
min_z = 22
max_z = 25.4
layer_height = 0.08
[object.height_range.settings]
walls = 4
```

Outside the ranges the object uses the process layer height. The first layer keeps `initial_layer_print_height`.

The builder refuses:
- ranges that overlap (Studio would trim them), or a range that starts above the object;
- a layer height outside the nozzle's limits;
- height ranges together with organic tree supports (`support_style = "tree_organic"`; Studio does not slice that);
- on a plate with a prime tower (more than one filament, including a range `filament`): objects with different ranges. Studio needs one
  layer height series for every object on such a plate. Give them the same ranges, or put them on separate plates.

A range that ends above the top of the object is a warning (Studio stops it at the top). Rule R40 checks all of this in any 3MF.

## `[[layer_gcode]]` (pause, temperature change, custom G-code at a height)

| Key | Meaning |
|---|---|
| `z` | Top of the layer where it applies, in mm. Must be a real layer top (first layer, then each layer height). R32 checks it. |
| `plate` | Plate number. Default 1. |
| exactly one of | `temperature = 215` (nozzle, checked against the option range), `pause = true`, or `gcode = "M117 hello"`. |

## `[temperature_tower]` (shortcut that writes one temperature change per step)

| Key | Meaning |
|---|---|
| `shape` | `"custom"` (default, your mesh) or `"calibration"` (see below). |
| `start` | Temperature of the first block. |
| `step` | Change per block (negative to go down). |
| `step_height` | Height of one block in mm. |
| `first_z` | Top of the first block in mm. |
| `steps` | Number of blocks. |
| `plate` | Plate number. Default 1. |

With the default shape the tower geometry is your mesh. The builder only writes the temperature changes at the heights you give.
A temperature change at layer top `z` is printed from that layer on, so `first_z` is the top of the first layer printed at `start`.

### `shape = "calibration"` (the builder makes the tower)

No `[[object]]` is needed (giving one is an error). One `[[filament]]` only.

```toml
[temperature_tower]
shape = "calibration"
# range 1: center +- plus_minus (defaults: the filament's nozzle_temperature, 10)
# center = 220
# plus_minus = 10
# range 2: start and end (either order; the hotter one is the bottom band)
# start = 240
# end = 220
step = 5          # default 5; the range must be a whole number of steps; 1 to 20 bands
```

| Key | Meaning |
|---|---|
| `shape` | `"calibration"`. Any other value than `"custom"` (the default) is an error. |
| `center`, `plus_minus` | Range `center - plus_minus` to `center + plus_minus`. `center` defaults to the filament's `nozzle_temperature` (a `[filament.settings]` value wins over the preset), `plus_minus` to 10. |
| `start`, `end` | The other way to give the range. Not together with `center` / `plus_minus`. |
| `step` | Degrees between bands, default 5. |
| `plate` | Must be 1. |

The default range is five bands (preset temperature +10, +5, 0, -5, -10), highest at the bottom. The first band's temperature is also the
first-layer temperature: the builder sets `nozzle_temperature` and `nozzle_temperature_initial_layer` to it (a warning says so if your job set
other values). Each later band starts with a temperature change on its first layer.

Each band is 8 mm high on a 2 mm base plate under band 1 (35.2 x 20 mm; the body is 29.2 x 14 mm). A band has:

- the temperature number, 3.6 mm high, 0.6 mm deep in the front face (7-segment digits, negative parts);
- an overhang on the right: the underside rises at 45 degrees for 2 mm, then at 30 degrees for 2 mm (angles from the horizontal plane),
  held by a 4 mm wall;
- a slot with a 1.2 mm roof that bridges 10 mm;
- two 3 mm pins, side by side 6 mm apart, 5.6 mm tall, in the slot. Seen from the front, stringing shows in the gap between them.
  Nothing needs support.

For a tower that survives removal from the plate, use at least 3 walls and 15% infill; silk filaments in particular have weak layer bonds.

The body is one normal part; every number is a negative part. All parts are closed manifold meshes. The tower prints in about 25 minutes at 0.2 mm
(layer-cooling slowdown is part of that; a temperature test needs it).

## `[calibration]` (calibration prints: flow, pressure advance, max volumetric speed, tolerance)

`[calibration]` with `type = "flow"`, `"pa"`, `"mvs"` or `"tolerance"` makes a calibration print. The builder makes the objects. The
result is a normal Studio project: plain objects, per-object settings, height ranges and layer G-code. It builds for every supported
Studio version.

Rules for every type:

- The job has one `[[filament]]` table and no `[[object]]` tables. `[calibration]` and `[temperature_tower]` cannot be in one job.
- Each object carries its value as an engraved label: 0.5 mm deep marks on a flat top face (digits, `.` and `-`). A label never cuts
  a load-bearing section: it leaves at least 1.0 mm of solid material under the cut.
- Placement: every object stays outside the printer's `bed_exclude_area` (on the P1S X 0-18, Y 0-28) and at least 15 mm from the
  front edge, clear of the nozzle load line the start G-code draws there.
- Settings the type writes are checked like the settings you write (names, types, ranges).
- `[settings]` and `[[filament]]` settings still apply; a key the type sets wins over the same key in `[settings]`.

### `type = "flow"`

A set of flat pads. Each pad has its own object flow ratio (Studio's `print_flow_ratio`) and an engraved label with that value. The
pads are 28 x 28 x 3 mm. The label is on the front edge of the top face; the rest of the top face stays flat so you can see over- or
under-extrusion. The test settings are 5 top layers and no ironing (ironing would hide the difference).

| Key | Meaning | Default |
|---|---|---|
| `ratios` | List of 2 to 9 object flow ratios, 0.5 to 1.5, at most 3 decimals, no repeats. One pad per value, in this order. | `[0.90, 0.95, 1.00, 1.05, 1.10]` |

What the label means: it is the per-object factor, not the final flow. Studio multiplies the extruded volume of every path in the
object by `print_flow_ratio`. The filament's own `filament_flow_ratio` (for Bambu PLA Basic, 0.98) is already in the path flow, so
the two multiply: effective flow = filament flow ratio x label. A pad labelled 1.05 prints 5% more than a pad labelled 1.00.
(Studio source: `GCode.cpp`, `_mm3_per_mm = path.mm3_per_mm * print_flow_ratio`.) The first layer and top solid infill have their own
extra ratios (`initial_layer_flow_ratio`, `top_solid_infill_flow_ratio`); they apply to every pad in the same way.

To use the result: pick the pad with the best top surface. Its label is the factor to apply to your current filament flow ratio.
New `filament_flow_ratio` = old value x label. Example: pad 0.95 looks best with a filament ratio of 0.98, so use 0.98 x 0.95 = 0.931.
The file is validated, not print-tested.

```toml
[calibration]
type = "flow"
ratios = [0.95, 0.975, 1.00, 1.025, 1.05]
```

The type works for Studio 2.8.2, 2.9.1 and 2.9.2 (`print_flow_ratio` exists in all three). The example pins 2.9.2.

### `type = "pa"`

Not built yet.

### `type = "mvs"`

Not built yet.

### `type = "tolerance"`

Not built yet.

## Settings

Use Studio's own setting names (`wall_loops`, `sparse_infill_density`, `outer_wall_speed`, `nozzle_temperature`). The builder:

- refuses a name Studio does not have in that version and suggests the closest ones,
- refuses a value of the wrong type, outside the range, or not in the enum list,
- refuses a filament key in a process table and the reverse,
- takes one value for per-extruder keys and writes every extruder variant itself,
- refuses keys it writes itself: `filament_colour`, `filament_ids`, `filament_settings_id`, `print_settings_id`,
  `printer_settings_id`, `inherits`, `inherits_group`, `different_settings_to_system`, `filament_self_index`, `flush_volumes_*`,
  `flush_multiplier*`, `filament_map*`, `filament_volume_map`, `filament_nozzle_map`, `compatible_printers*`,
  `print_compatible_printers`, `extruder`.

Per-object overrides of per-extruder (variant) print keys are not supported in v1.

`seam_headroom` (Studio's `seam_embedded_headroom`, mm, 0 to 100) needs Studio 2.9.2 or newer. It is an object setting: use it in `[settings]`
or `[object.settings]`. The builder refuses it on a part and in a height range. Without it, the preset value applies (10 for the P1S
presets). See [multicolor.md](multicolor.md).

## Defaults that differ from Bambu's presets

One: sparse infill is **Rectilinear**, not Grid. Grid crosses itself on every layer, so the nozzle drags over infill it just printed.
Set `infill_pattern` (or `sparse_infill_pattern`) in `[settings]` or per object to use another pattern, for example `infill_pattern = "grid"`.
Patterns can be written by the name Studio shows: `rectilinear` and `aligned_rectilinear` are stored as Studio's `zig-zag` and `alignedrectilinear`.

## Friendly names

Settings can be written under a second spelling. It is only a name: the job is built with the real Studio key. Setting both an alias and its real key is an error.

<!-- aliases:begin (generated by tests/gen_aliases.py; do not edit) -->

| You write | Studio key | Where |
|---|---|---|
| `walls` | `wall_loops` | process |
| `infill` | `sparse_infill_density` | process |
| `infill_pattern` | `sparse_infill_pattern` | process |
| `top_layers` | `top_shell_layers` | process |
| `bottom_layers` | `bottom_shell_layers` | process |
| `supports` | `enable_support` | process |
| `brim_size` | `brim_width` | process |
| `color_penetration_cross_parts` | `cross_part_color_penetration` | process |
| `seam_headroom` | `seam_embedded_headroom` | process |
| `nozzle_temp` | `nozzle_temperature` | filament |
| `nozzle_temp_first_layer` | `nozzle_temperature_initial_layer` | filament |
| `flow` | `filament_flow_ratio` | filament |
| `bed_temp` | the bed temperature key of the job's `plate_type` (for example `textured_plate_temp`); `plate_type` must be set | filament |
| `bed_temp_first_layer` | the same key with `_initial_layer` | filament |

Many more names work: every print and filament setting can also be written as the label Studio shows for it, lowercased with underscores (the label "Arc fitting" is `arc_fitting` for `enable_arc_fitting`; "Elephant foot compensation" is `elephant_foot_compensation` for `elefant_foot_compensation`). Labels that are the same as the real key add nothing and are left out. The full generated list is in [aliases.md](aliases.md). An alias that would mean two different keys is not offered.

<!-- aliases:end -->

Any other unknown name fails with the three closest real names.

## 3D Effect Plate

`plate_type = "3D Effect Plate"` needs Studio 2.9.2 or newer. A job for an older Studio is refused with a message that says so. Set `studio = "2.9.2"`.

- The bed temperature keys are `effect_plate_temp` and `effect_plate_temp_initial_layer` (whole degrees, 0 to 120). A filament with 0 does not support the plate, and the builder refuses it.
- With this plate type, `bed_temp` and `bed_temp_first_layer` set those two keys. You can also write the keys under `[filament.settings]`. Either way the value is a changed setting in the file.
- Patch mode: `plate_type = "3D Effect Plate"` (top level or in a `[[plate]]`) works on a file from Studio 2.9.2. It is refused on a file from 2.8.2 or 2.9.1. The 0 check for the used filaments applies as for the other plates.

See `examples/effect_plate.toml`.

## Lint

After the config is built, the builder checks the settings against fields of the Studio presets and prints a warning for each finding. Lint warnings start with `lint:`, appear in the console summary and in the summary file, and never stop a build. Every rule uses a Studio field; a check without a field is not a rule.

| Check | Fields (Studio source) |
|---|---|
| Nozzle temperature or first layer temperature outside the filament's recommended range | `nozzle_temperature`, `nozzle_temperature_initial_layer` against `nozzle_temperature_range_low` / `nozzle_temperature_range_high` (Studio shows the same warning, `ConfigManipulation.cpp`) |
| Chamber temperature of 40 C or more on a printer that cannot control the chamber | filament `chamber_temperatures` (40 C is Studio's "high temperature" limit, `SelectMachine.cpp`), printer `support_chamber_temp_control = 0` |
| Inlay thinner than one layer: a normal part of another filament than the tallest normal part of its object, lower than `layer_height` (object or part override, else the process value) | geometry and `layer_height` |

Not lint rules:

- **Bed temperature 0 on the selected plate** is an error. Studio refuses to slice it (`Print::validate`: "Plate %d: %s does not support filament %s"). The builder stops with a message, and the validator reports rule R39.
- **Bed temperature outside a range:** no filament preset has a bed range, so there is no rule.
- **Abrasive filament on a soft nozzle:** the filament has `required_nozzle_HRC`, but no printer preset has a nozzle type. Studio reads the nozzle from the connected printer, so there is nothing to compare.
- **Enclosure needed on an open printer:** Studio's own warning reads the connected printer's chamber and door. The preset-side check is the chamber temperature rule above.

## Patch mode (`patch = "file.3mf"`)

A job with `patch` changes an existing Bambu Studio project and writes a new file. The input is never touched. Geometry is never
changed: `mesh`, `position`, `copies` and part `type` are refused. The Studio version comes from the file, so `studio`, `title`,
`purge` and `allow_large` are refused too.

The input must be saved by a supported Bambu Studio version (any build; a build other than the supported one gives a warning), with one nozzle,
and must validate with 0 errors in default mode. A file saved by a newer 2.x Studio is accepted with a warning: it is patched with the
rules of the newest supported version, a setting that version does not know is a warning and is kept as it is, and every entry the patch
does not change stays byte-identical. A file from a newer MAJOR version (3.x) is refused. Orca Slicer, PrusaSlicer, older Studio versions and X2D / H2D
files are refused with "open and save it in Bambu Studio 2.9.2 first". A sliced file (it holds G-code) is accepted: the G-code and
everything that belongs to it is dropped and a warning says so (see "Sliced input" below).
A project that already holds user presets (a slot with a name that is not a system preset) is accepted.

```toml
patch  = "in.3mf"                        # required; the output must be another file
output = "out.3mf"                       # --out on the command line wins
process = "0.16mm Optimal @BBL X1C"     # optional: swap the process preset
printer = "Bambu Lab P1S 0.4 nozzle"    # optional: change the printer (same bed only, see "Printer change")
keep_changes = false                     # optional: with process (or printer), keep the designer's process changes
plate_type = "Textured PEI Plate"       # optional: every plate
reset_to_preset = false                  # optional: drop the designer's project-level changes (process and filament slots)

[settings]                               # project level, process settings
sparse_infill_density = "15%"

[[filament]]                             # only the listed slots change; slot numbers count from 1
slot = 2
preset = "Bambu PETG HF @BBL P1S 0.4 nozzle"   # optional
color = "#FF8800"                        # optional
keep_changes = false                     # optional, with preset (or a printer change): keep this slot's designer changes
[filament.settings]
nozzle_temperature = 250

[[filament]]                             # slot = n + 1 adds a slot at the end (preset and color are both required)
slot = 5
preset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"
color = "#000000"

[[filament]]                             # remove = true removes the LAST slot, if nothing uses it (alone in the job: no add in the same job)
slot = 4
remove = true

[[object]]                               # select by name, by plate, or both
name = "Body"
plate = 3
filament = 2                             # the object and its parts that carry a filament
[object.settings]
wall_loops = 4
[[object.part]]
name = "Logo"
filament = 3
[object.part.settings]
sparse_infill_density = "30%"

[[plate]]                                # bed type of one plate
id = 2
plate_type = "Cool Plate"

[[layer_gcode]]                          # added to the file's own layer G-code; same form as the builder
plate = 1
z = 10.2
pause = "change to red"
```

What happens:

- **Presets.** Preset names must match exactly and be made for the file's printer (a refusal lists the closest names). A swapped
  slot or process takes all its values from the new preset. The designer's own changes on that slot are dropped (Studio's "Discard")
  and listed in the summary; `keep_changes = true` keeps them. `reset_to_preset = true` drops them for the process and every slot.
  `keep_changes` and `reset_to_preset` together are refused.
- **Settings** you list are changed in the named place only. Every other value stays as it is. The diff lists
  (`different_settings_to_system`, `inherits_group`) are updated so Studio keeps your values when it opens the file.
- **Colour.** The slot's colour changes. Studio's purge volumes for that slot (its row and column) are recalculated when the file's
  values are Studio's automatic ones; if the designer edited them, they stay and a warning says so. A slot that holds a
  multi-colour or mixed filament is refused.
- **Selection.** A name that matches no object or more than one is refused with the matches listed (add `plate`). A plate selects
  every object that has an instance on it. An object on several plates gets a warning: the change applies to every instance.
- **Object `filament`** sets the object and each of its parts that already carry a filament; a part listed with its own `filament` keeps that one.
- **Plate type** is refused if a filament in use has bed temperature 0 for it (Studio would refuse to slice).
- **Layer G-code** is added to the file's `custom_gcode_per_layer.xml` (a new one if there is none). A layer at a z that already has one is refused.
  A colour change of a slot also recolours that slot's colour-change and tool-change layers.
- **New slot.** `slot = n + 1` (then `n + 2` and so on, no gaps) adds a slot at the end with the preset and colour you give. Every
  per-slot list of the project settings gets the new slot's values (the same ones the builder writes: the output of a job that adds
  slot 5 to a 4-filament file equals the builder's own 5-filament config, apart from the prime tower position), the purge matrix
  grows to the new size with Studio's automatic values in the new row and column (old cells stay), each plate's `filament_maps` and
  `filament_volume_maps` get one more entry, and the diff lists get a new entry before the printer entry. Settings you list for the new slot
  are applied to it. Objects and parts of the same job may use the new slot. A plate's `first_layer_print_sequence` and `other_layers_print_sequence`
  (the print order of the filaments, when the file has them) follow Studio's own rule: the new slot is appended to each order list; on removal the
  removed slot leaves it (`PartPlate.cpp` `update_first_layer_print_sequence` and `..._when_delete_filament`, both Studio versions). A list that is not in Studio's form is left as it is, with a warning.
- **Removing a slot.** Only the last slot, and only if nothing names it: a part or object with that filament (modifiers count; negative
  parts, enforcers and blockers do not), a painted area, a per-object or per-part filament key (`wall_filament`, `sparse_infill_filament`,
  `solid_infill_filament`, `support_filament`, `support_interface_filament`) or the same key at project level, a colour-change or
  tool-change layer, a height range with that filament, a sequence in `filament_sequence.json`. Slots are never renumbered, so
  a slot in the middle cannot be removed; the refusal lists what uses the last slot. To free it, move those parts and settings in a first patch.
- **Designer credit (rule 5).** The root model's metadata block is the only part of that entry that can change: Title, Designer,
  License, Origin, Description, Copyright, the designer and profile ids that name a person (`ProfileUserId`, `ProfileUserName`,
  `DesignerUserId` when a designer is named), the 3MF version, dates and any key the patcher does not know stay as they are. Removed:
  `DesignerUserId` when `Designer` is empty (then Studio put the local user's id in it), `Region` (the local app's country code), and
  any value that holds an absolute path in a key that is not the designer's credit (a path inside Title, Description, License, Origin or another credit key stays, with a warning that names the key). A `source_file` that holds a path is cut to its file name. Every byte of the entry
  from `<resources` on (the meshes) is copied unchanged; the summary lists what was removed (key names only) and what was kept.
- **Sliced input.** A file with G-code is patched as an unsliced project: `Metadata/plate_N.gcode`, its `.md5`,
  `Metadata/_rels/model_settings.config.rels`, the `gcode_file` key of each plate, the plate entries of `slice_info.config` and the
  sequence lists of `filament_sequence.json` are removed or emptied. A warning says to slice the output again in Studio.
- **Printer change** (`printer = "..."`, the exact name of a Bambu system printer preset). Allowed only between single-nozzle printers with
  the same `printable_area`, `printable_height` and `bed_exclude_area` and the same nozzle diameter, because another bed moves every plate
  cell (plate origin = column x bed width x 1.2) and parts are never moved. The groups come from the profiles: X1 Carbon, X1, X1E, P1S and
  P1P (0.4 nozzle: `Bambu Lab X1 Carbon 0.4 nozzle` and so on) can be changed to each other, and A1 and P2S. A1 mini, H2S, H2C, H2D and X2D
  have no partner. A2L and H3C (2.9.1 only) have the same bed, but the H3C filament presets carry 2 extruder variants for the printer's 3,
  so that change is refused. A refusal says which of the checks failed and lists the printers the file can go to.
  **Another extruder variant count (A1: 1, P2S: 3).** Every list with one value per variant (`printer_extruder_variant`,
  `filament_extruder_variant` and the settings of each kind) is cut again for the new printer: the values are the new presets'. A designer
  change that is kept (`keep_changes = true`, or a preset that stays) is carried over by variant name: A1 -> P2S keeps the
  `Direct Drive Standard` value and the two High Flow variants take the preset's value; P2S -> A1 keeps the Standard value and drops the
  High Flow ones (the summary says so). Object, part and plate settings that hold one value per variant are refused, because they would not
  fit.
  What changes: every printer setting becomes the new preset's value (the printer entry of the diff lists is empty afterwards; the
  designer's own printer settings are dropped and listed); `printer_settings_id` follows. Presets are matched the way the Studio app does it
  when you pick another printer: a process or filament preset that is made for the new printer too stays as it is; one that is not is
  replaced by the preset made for the new printer with the same alias (the name up to `@`, for example `Bambu PLA Basic`), and the swap
  follows the rule above (the designer's changes on that slot are dropped, `keep_changes = true` on the slot keeps them; at the top of the job
  it belongs to the process). Exactly one candidate is required: with none or several the patch is refused, naming the slot and the
  candidates, and you resolve it with `process = "..."` or `preset = "..."` for the slot (an explicit preset is only checked against the
  new printer). A slot with a user preset that has no system parent cannot be matched and needs an explicit preset. The purge matrix is
  recalculated for the cells that change when the file's values are Studio's automatic ones, otherwise kept with a warning. The tower
  position (R37) and the plates (R23) are checked again on the new printer; if anything no longer fits the patch is refused. A printer
  change cannot be combined with adding or removing a slot, and a file that stores its printer preset inside it is refused.
- **Print-host settings (rule 5).** The keys `print_host`, `printhost_apikey`, `printhost_user`, `printhost_password`, `printhost_port`,
  `printhost_cafile` and `print_host_webui` are blanked when they hold a value (they belong to the
  designer's own printer). The summary names the keys, never the values. A setting whose value holds an absolute path is not changed;
  a warning names the key so you can clear it in Studio before sharing the file.
- **Plate pictures, thumbnails and plate JSON** are copied as they are and may show the old colours until Studio saves the project again.

Every other entry of the 3MF is copied with the same bytes, name, order and name-encoding flag. After writing, the patcher checks
the output file: it validates with 0 errors in default mode, adds no strict finding the input did not have, every untouched entry
is byte-identical, and the settings that differ are exactly the ones the job asked for. If a check fails, nothing is written.
The summary file `<output name>.summary.md` names the source file and lists every change, the dropped or kept designer changes and the warnings.

## Summary file

Every build writes `<output name>.summary.md` next to the 3MF: plain Markdown with printer, process, plate type, Studio version, the filament slots (preset, color, nozzle and bed temperature, overrides), the objects and parts (filament, type, overrides), the layer G-code and the warnings (including lint warnings). With `--slice-check` it adds Studio's print time and grams per plate. File names only, no paths. `--no-summary` on the command line turns it off.

## Minimal job

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

More in `examples/` and `docs/examples.md`.
