# Multi-color and no-AMS projects

What the builder does for you, and what to tell the user. Everything here is checked by the real-slicer tests (colors_2, colors_4, colors_8,
pause); none of it is print-tested.

## How colors are built

- One `[[filament]]` per color slot, up to 16. Each `[[object.part]]` names its slot with `filament = N`. A color change inside one part is not
  possible; split the model into one mesh per color in CAD (the `examples/data/slab*.stl` files show the idea).
- Parts of one object must be aligned in the same coordinates (export them together from one CAD file). The builder keeps each part where
  its mesh says.
- A color inlay needs the colors to meet exactly. Plan the boundary between two colors at a layer top (the first layer, then each layer height),
  so each layer is one color.
- Every slot a part uses is purged on a change. With `purge = "auto"` the builder uses Studio's color-based volumes (the same formula as
  Auto-Calc, checked against a real save). `purge = "default"` uses 140 + 140 mm3 for every pair, which wastes more but never underpurges.
  The build summary prints the grams per change so the user can see the cost before slicing.
- The prime tower is on whenever more than one slot is used. The builder places it and reserves a conservative footprint (R37), so a part
  that would sit on it is refused with a message that says which object to move. The tower size grows with the number of colors and the
  tower width setting; `prime_tower_width` in `settings` changes it.
- A slot that no part uses stays in the project and its purge volume still counts. The builder warns.

## No AMS (single spool, change by hand)

Use `[[layer_gcode]]` with `pause = true` at the layer where the user swaps the filament, and set the next color in the printer's own change
prompt. The pause is written as Studio's pause type and is checked in the G-code (it sits on the layer asked for). A pause is exact only at a
layer top, so give `z` as a real layer top (first layer, then each layer height).

```toml
[[layer_gcode]]
z = 10.0
pause = true
```

## Things to tell the user

- Colors that are close in brightness need less purge; dark to light needs the most. Reordering slots does not change the cost per change,
  only which changes happen.
- More colors means a taller and wider tower and longer prints. Past about four colors the tower is a large share of the plate.
- Parts of different materials in one project (PLA with PETG) need compatible temperatures; the builder does not judge that.
