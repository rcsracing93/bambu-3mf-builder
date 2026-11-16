# Support matrix

Every example in `examples/` is built, sliced in a real headless Bambu Studio (plain and `--uptodate`), and graded.
A cell shows **ok** with the number of checks that passed, or **FAIL** with the number that failed; a blank cell means no check applies.
CI-verified means the G-code was produced and checked by Studio; it does not mean the print was tested on a machine.
Last full pass: 2026-11-16 (reports produced by `tests/studio_slice.py`; the `real Bambu Studio` workflow produces the same reports for each Studio version).

| Example | Studio 2.8.2, file built for 2.8.2 | Studio 2.9.1, file built for 2.8.2 | Studio 2.9.1, file built for 2.9.1 |
|---|---|---|---|
| calibration_tower | ok (12) | ok (11) | ok (11) |
| colors_2 | ok (13) | ok (12) | ok (12) |
| colors_4 | ok (13) | ok (12) | ok (12) |
| colors_8 | ok (13) | ok (12) | ok (12) |
| modifier_negative | ok (11) | ok (10) | ok (10) |
| multi_plate | ok (11) | ok (10) | ok (10) |
| pause | ok (12) | ok (11) | ok (11) |
| per_object_settings | ok (11) | ok (10) | ok (10) |
| single_a1 | ok (11) | ok (10) | ok (10) |
| single_p1p | ok (11) | ok (10) | ok (10) |
| single_p1s | ok (11) | ok (10) | ok (10) |
| single_x1c | ok (11) | ok (10) | ok (10) |
| temperature_tower | ok (12) | ok (11) | ok (11) |

Checks. What the job asked for (plates, settings, colors, presets, filament per part, pauses, temperatures) comes from the job file
and the mesh files, never from the builder's output. T5 and T14 compare the written file with the G-code, to prove the file reaches the slicer unchanged.
T2 validator finds nothing, T3 slice succeeds, T4 slice with `--uptodate` succeeds, T5 every written setting reaches the G-code,
T5b every setting the job asked for is in the G-code with the job's value, T6 plain and `--uptodate` agree,
T7 filaments used, and the tool on every layer, follow the parts, T8 tool changes and prime tower, T9 no unexpected warning or error in the Studio log,
T10 temperature tower on the requested layers, T10p pauses on the requested layers, T11 Studio re-export holds the job and slices again,
T12 file built for another Studio version slices (only in a local run with `--other-studio-dir`; CI runs each version on its own), T13 the real prime tower does not overlap the model, T14 it sits inside the box R37 reserves.
`tests/studio_roundtrip.py` additionally saves each file three times in Studio and checks that geometry does not move.

## Harness mutation test

Broken copies of a known-good example. Each must fail the harness and name the right rule (`tests/studio_mutation.py`, `tests/test_harness_mutations.py`).

| Broken copy | Must | Studio 2.8.2 | Studio 2.9.1 |
|---|---|---|---|
| unbroken example | all checks pass | PASS: 12 checks, 0 failed | PASS: 12 checks, 0 failed |
| filaments swapped between parts | fail T7 | PASS: failed: T11, T7 | PASS: failed: T11, T7 |
| override missing from different_settings_to_system | fail T2 naming R3 | PASS: failed: T2 | PASS: failed: T2 |
| per-filament list one entry short | fail T2 naming R5 | PASS: failed: T2, T5 | PASS: failed: T2, T5 |
| wipe tower moved onto a part | fail T2, T3 naming R37 | PASS: failed: T2, T3, T4, T9 | PASS: failed: T2, T3, T4, T9 |
| purge matrix changed after the build | fail T5 | PASS: failed: T5 | PASS: failed: T5 |
| loader puts every object on plate 1 | fail T3 | PASS: failed: T3, T4 | PASS: failed: T3, T4 |
| loader changes a process setting | fail T5b | PASS: failed: T5b | PASS: failed: T5b |
| loader changes a filament color | fail T5b | PASS: failed: T5b | PASS: failed: T5b |
