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
- To set one change by hand, give `purge` a table: `purge = { "1>2" = 450 }` (mm3, from slot > to slot). Other pairs stay at the auto values.
  Use it when a dark-to-light change shows the old color on the print. Raise that pair only. The limit is 900 mm3 on Studio 2.8.2 and 2.9.1,
  and 2000 mm3 on 2.9.2. See `examples/purge_matrix.toml`.
- The prime tower is on whenever more than one slot is used. The builder places it and reserves a conservative footprint (R37), so a part
  that would sit on it is refused with a message that says which object to move. The tower size grows with the number of colors and the
  tower width setting; `prime_tower_width` in `settings` changes it.
- A slot that no part uses stays in the project and its purge volume still counts. The builder warns.

## Color penetration between parts (Studio 2.9.2 and newer)

`color_penetration_cross_parts` is the job name for Studio's "Cross-part color penetration" (`cross_part_color_penetration`, default on).
Studio's tooltip says: when on, color from a part's painted top and bottom surfaces can penetrate continuously into adjacent parts of the
same object. When off, the set penetration depth is kept but clipped at the part's own boundary. In the source, a part uses its own value
first, then its object's, then the process value. Only normal parts use it.

The setting acts only on color that is painted onto surfaces in Studio (the color painting tool). Studio uses it in the multi-material
segmentation of painted triangles (`MultiMaterialSegmentation.cpp`). A job from this builder gives each color its own part and paints
nothing, so the setting does not change its G-code: `examples/inlay_no_penetration.toml` sliced with and without it gives the same tool
paths. Set it to `false` when a project has painted top or bottom surfaces next to another part, and you want the painted color to stop at
that part (for example a painted project changed in patch mode). A job for Studio 2.8.2 or 2.9.1 gets an error: the setting needs
Studio 2.9.2 or newer. It is not allowed on a modifier part or in a height range.

## Seam headroom (Studio 2.9.2 and newer)

`seam_headroom` is the job name for Studio's `seam_embedded_headroom` (label "Embedded seam headroom", 0 to 100 mm, default 0; the P1S
process presets set 10). Studio's tooltip says: when a seam can be hidden inside the print, for example at the joint of two materials,
prefer the seam position that leaves at least this much hidden wall in the printing direction before the wall becomes visible. This hides
extrusion defects from unstable nozzle pressure after a filament change. 0 turns it off.

- In Studio's seam code (`SeamPlacer.cpp`) it applies only on layers where more than one region has walls, which is the case for
  parts of different colors that touch. A single-color object is not affected.
- Set it as an object setting, in `[settings]` or `[object.settings]`. Studio reads it per object. A part or a height range cannot set it.
- A job for Studio 2.8.2 or 2.9.1 that sets it is refused. Those versions have no such setting.
- This was checked in the slicer source and in a headless slice. It is not print-tested.

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
