# Validator rules

`python3 scripts/validate_bambu_3mf.py file.3mf [--json] [--strict] [--privacy]` exits 0 when there are no errors, 1 when there are, 2 on bad usage.
The flags may come before or after the file.
Every finding names its rule. Rules were derived from the Bambu Studio source and checked against real Studio saves.
The rules that depend on a table (R3 to R17) use the vendored option table of the Studio version in the file.

## Modes

Every finding has a class. The mode decides the level of each class. A finding never gets a higher level than its rule gives it below.

| Class | Meaning | default | `--strict` |
|---|---|---|---|
| LOAD | Studio rejects the file or misreads it | error | error |
| CHANGE | Studio accepts the file and changes a value when it loads it | warning | error |
| KEEP | Studio uses it as it is, but the print may differ (sunk part, open mesh) | warning | error |
| FORM | Not what Studio writes, but it loads the same | not reported | error |
| PRIVACY | Personal data in the file (rule R33) | only with `--privacy`: warning | only with `--privacy`: warning |
| SKIPPED | A rule group did not run (unsupported Studio version, multi-nozzle printer) | warning | warning |
| TOOL | A limit of this tool | note | note |

`--strict` is the v1.0 behaviour: every class except PRIVACY, SKIPPED and TOOL is an error. PRIVACY findings never change the exit code.
A finding that is not listed in `scripts/bambu3mf/modes.py` is LOAD. `--json` prints one object:
`{"mode", "studio_version", "tables_applied", "findings": [{"rule", "class", "level", "where", "message"}]}`.
`tables_applied` is false when the Studio version in the file has no option table, so the table rules did not run.
The Level column below is the strict level.

| Rule | What is checked | Level |
|---|---|---|
| R1 | Application is BambuStudio-<version> | error |
| R2 | BambuStudio:3mfVersion is 1 | error |
| R3 | Every setting that differs from the system preset is listed in different_settings_to_system. A slot with a user preset is compared with the parent that `inherits_group` names for it; a slot with no parent is skipped (R10 note), never the whole file. A key is reported once (S1 does not repeat it) | error (warning in default mode, class CHANGE: the Studio app resets it) |
| R4 | filament_colour is a list of #RRGGBB, at least one | error |
| R5 | Per-filament lists have exactly n entries (the mixed-color lazy lists `filament_mixed_*` may have 1..n; real Studio saves write them that way). A short list gives the missing filaments filament 1's value | error |
| R6 | Per-filament, per-extruder lists have n x variants entries | error |
| R7 | `filament_extruder_variant` has n x V entries; `filament_self_index` matches | error |
| R8 | Variant-stride lists have the right length | error |
| R9 | printer_model is a Bambu printer | error |
| R10 | Preset names exist, are not base templates, and are made for the printer | error / warning |
| R11 | `different_settings_to_system` and `inherits_group` each may be absent (Studio writes a list only if one entry is not empty; absent = all entries empty). A list that is present has n + 2 entries; Studio pads or cuts a wrong length with "", which loses the printer entry | error |
| R12 | filament_ids has n entries and matches the presets | error |
| R13 | All project options of this Studio version are present | error |
| R14 | flush_volumes_matrix and flush_volumes_vector have the right size, diagonal 0 | error |
| R15 | project_settings is a JSON object of strings and lists of strings, `from` is `project`, name is `project_settings`, version matches the Application; every setting of this Studio version is present (a missing one gets the system preset value in the Studio app and the built-in default in the command-line slicer; the keys real saves leave out are allowed) | error (warning in default mode, class CHANGE) |
| R16 | Every key exists in the version's option table with the right shape, enum value and range. Another build of a covered version (same major.minor.patch, for example 2.8.2.60) uses the tables of the covered build, with one warning, and a key those tables do not know is a warning. A file from a newer 2.x than the newest supported version is checked against the rules of the newest supported version, with one note; a key those tables do not know (in the project settings or as an object or part override) is a warning. Any other version skips the table rules with one warning (`tables_applied` false). A printer with several nozzles skips the list-size, preset-difference and load-simulation rules with one warning | error / warning |
| R17 | Bed type exists, printable_area is a point list, per-plate wipe tower lists have one entry per plate | error |
| R18 | [Content_Types].xml is complete | error |
| R19 | _rels/.rels points to existing parts | error |
| R20 | Every sub-model is listed in `3D/_rels/3dmodel.model.rels`, none in the root `.rels`; every component path exists in the archive | error |
| R21 | Root model has the Production Extension namespace and `requiredextensions="p"`; each object has components whose ids match `model_settings.config` | error |
| R22 | Object ids are unique; UUIDs and counters follow Studio's scheme | error / warning |
| R23 | Build items refer to objects, carry a 12-number transform and printable flag, lie on the plate | error |
| R24 | Meshes: numeric finite vertices, integer triangle indices in range, closed manifold surface, positive volume, mesh type matches the part subtype; more than 9 significant digits is a warning | error / warning |
| R25 | XML is well-formed; model_settings.config is present | error |
| R26 | Part ids match mesh ids; subtypes are valid | error |
| R27 | Extruder numbers are filament slots 1..n | error |
| R28 | Per-object and per-part overrides are valid settings. A numeric vector is written with `,` between the slots (`200,nil`; `nil` is legal for a nullable option), a string vector with `;`; each slot is checked | error |
| R29 | Modifier, negative and support parts have a normal part to act on | error |
| R30 | Plates are numbered 1, 2, 3 in order; `filament_maps` has n entries; each instance has its ids; `identify_id` is a unique positive integer; instances match the build items | error |
| R31 | Object, part and plate names contain none of `< > : / \ ? * "` or the pipe character | error |
| R32 | custom_gcode_per_layer.xml: every layer has a valid `type` (0-5), `extra`, `extruder` and `color`; `top_z` is finite, on a real layer top and below the model top; `mode` is valid; `plate_info id` is a real plate | error |
| R33 | No account ids, user names, e-mail addresses, IP addresses, absolute paths, print host fields or ZIP extra fields; no reference to a file outside the archive. A version number such as 99.0.0.0 is not an IP address | error |
| R34 | `filament_sequence.json` comes after `model_settings.config` in the archive (both versions read it in ZIP order when opening a project) | error |
| R35 | Variant lists are never written short (1 entry where more are needed). Both versions resize a short variant vector by copying the first value. In a key that is not in different_settings_to_system the Studio app replaces the whole key with the system preset value (class CHANGE, as for R5, R6, R8) | error |
| R36 | Multi-extruder lists have one entry per nozzle: `extruder_type`, `extruder_nozzle_stats`, `filament_map`. **Known gap:** the tower reach box per nozzle is not checked (needs H2D evidence). The checks above run, and are tested with a two-nozzle config | error |
| R37 | Only a plate that has a prime tower (GLCanvas3D.cpp 2.8.2 :3906, 2.9.1 :4149: more than one used filament - parts, painted areas, support and support-interface filaments, tool changes - or smooth timelapse or wrapping detection, even with an empty exclude area; no tower in spiral mode, `Print::has_wipe_tower`; no tower on a "by object" plate, plate metadata `print_sequence` else the config value, with other than one printable instance, GLCanvas3D.cpp 2.8.2 :3887, 2.9.1 :4130). On such a plate the tower must be clear of every part. The saved position is used (Studio moves it inside the plate on load: a saved tower that sticks out is class CHANGE, and the overlap test uses the moved tower); the position Studio derives itself is used only when the file has no entry for the plate. A saved position that overruns by 1 mm or less is a warning in strict mode | error / warning |
| R38 | `print_extruder_variant`, `print_extruder_id`, `printer_extruder_variant`, `printer_extruder_id` and `filament_extruder_variant` equal the system preset values; otherwise Studio resets the listed per-extruder overrides in the slots that differ | error |
| R39 | a filament the plate prints with has a bed temperature above 0 for the file's `curr_bed_type` (`cool_plate_temp`, `textured_plate_temp`, ...); otherwise Studio refuses to slice (`Print::validate`: "Plate %d: %s does not support filament %s") | error |
| R40 | `layer_config_ranges.xml` (height ranges) is Studio's form: `<objects><object id><range min_z max_z><option opt_key>`; `id` is the object's 1-based place in the model (build order, bbs_3mf.cpp); every range has `layer_height` (Slicing.cpp reads it unchecked); options are `layer_height`, `extruder` (0..n) or a part setting (PrintRegionConfig), with valid values; ranges do not overlap (CHANGE: Studio trims them) and start below the object top (a range past the top is a warning); the layer height is inside the nozzle limits (CHANGE: Studio drops the object's ranges, `nozzle_range_reset`); no ranges with organic tree supports (Print.cpp); on a plate with a prime tower every object has the same ranges (Print.cpp, "same layer heights"); a range filament counts as a used filament for R37 | error / warning |

## Guards (untrusted input)

The validator opens files from strangers, so it limits what it will read.

| Guard | What is checked |
|---|---|
| G1 | Archive is a readable ZIP, no encrypted entries |
| G2 | Entry count is within the limit |
| G3 | Entry names are safe (no absolute path, backslash, .. or duplicates) |
| G4 | Compression ratio above 1000:1 for an entry over 1 MiB, or total uncompressed size above 6 GiB. The validator stops reading the archive when G1, G3 or G4 fires |
| G5 | Entry size and vertex count are within limits |
| G6 | No DOCTYPE or ENTITY anywhere in any XML entry, sub-models included |
