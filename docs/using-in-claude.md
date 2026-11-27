# Using it in Claude

You talk to Claude in plain words. Claude writes the job file, runs the scripts, checks the result and gives you the 3MF. You never
open a job file or a terminal.

## What to give Claude

- **The model:** STL, OBJ or 3MF files, attached or in a folder Claude can reach. STEP is not read: export STL first.
- **The printer and nozzle,** for example "P1S, 0.4 nozzle".
- **The filament per color:** type, brand and color, for example "Bambu PETG Basic, black". "What's in my AMS" works if you list it.
- **Anything you care about:** walls, infill, layer height, speed, temperatures, supports, plate type.

Claude asks for anything missing. Anything you do not set stays at Bambu's preset value, with one exception: sparse infill is
Rectilinear, not Grid.

## What you get back

- A `.3mf` project that opens in Bambu Studio with the settings you asked for.
- A short summary: printer, process, each filament slot, each object, pauses and warnings. The same summary is saved next to the file as `<name>.summary.md`.
- If Claude runs on the computer that has Bambu Studio installed, you also get a test slice with the print time and the grams per plate. This is tested on macOS and Linux, not yet on Windows.

Then open it in Studio, look at the preview, slice and print. The skill is tested by slicing its example projects in Bambu Studio. The projects are not printed on every printer.

## Main scenarios

Each scenario shows what you type to Claude.

### 1. Design it and make it print-ready

Describe the part. Claude designs it and makes the print file.

> Design a phone holder for a 20 mm tool rail in OpenSCAD, then make a print-ready project for my P1S in Bambu PETG Basic black.

Claude writes the model (OpenSCAD, CadQuery or similar), exports it to STL and builds the 3MF from it in one go. For two colors,
Claude exports each color as its own STL from the same design, so the parts line up. Ask for changes ("make it 5 mm thicker",
"add a chamfer") and you get a new 3MF each time.

Claude needs a tool that can export the model: OpenSCAD on your computer, or a CAD library in Claude's own workspace. The skill
itself does not design models; it takes the exported file.

### 2. Make a project from a model you have

Attach the model file (or tell Claude which folder it is in), then say what you want.

One model:

> Make a print-ready project from bracket.stl for my P1S with a 0.4 nozzle. Use Bambu PETG Basic in black, 4 walls and 20% infill.

Several models on one plate:

> Put these three STL files on one plate for my P1S. Use Bambu PLA Basic in white with 0.16 mm layers.

### 3. Multi-color

Each color is its own model file. Attach them and say which color goes where.

With an AMS:

> base.stl and text.stl are one object. Print base.stl in black PLA and text.stl in white PLA.

The filament numbers in the project do not have to match your AMS trays. When you print, Studio maps each project filament to a tray (by color and type), and you can change the mapping there.

Without an AMS:

> Print sign.stl in one filament and pause at 6 mm so I can change the filament by hand.

Without an AMS, the color can change only at a layer height. Two colors cannot sit side by side in one layer.

### 4. Temperature tower

> Make a temperature tower for Bambu PLA Silk+, from 235 down to 215 in 5-degree steps.

Claude builds a labeled tower (each band shows its temperature, with an overhang, a bridge and stringing pins) when you have no
model. With your own tower model it sets one temperature per band height.

### 5. Fine layers where they matter (height ranges)

> Print cap.stl at 0.24 mm, but use 0.12 mm layers for the bottom 1.5 mm chamfer and 0.08 mm layers for the logo in the top 3 mm.

> Use 4 walls in the top 10 mm only.

> Print the top 2 mm of the tag in white, the rest in black.

Give the heights in mm from the bottom of the model as it sits on the plate. Claude sets one band for each, and the rest of the model
keeps the process layer height. Claude tells you when a band cannot work, for example a layer height the nozzle cannot print, or
two models with different bands on one multi-color plate (put them on separate plates).

### 6. Change a project you already have

Attach the 3MF, for example a file from MakerWorld, a file from a friend, or a project you saved earlier. Then say what to change.
The result is always a new file. The original file is not changed.

> Change this project to my P1S.

This works only between printers with the same bed and nozzle size (see the table below). Claude tells you when a change is not possible.

> Change filament 2 to Bambu PETG Basic in white, and set the plate to Textured PEI.

> Add a pause at layer 40, and set 3 walls on the object called "Lid".

> Add a fourth color: red Bambu PLA Basic in slot 4.

> Remove my local user data from this file before I share it.

### 7. Check a file

Attach the 3MF and ask.

> Check this 3MF. Will it open and slice in Studio 2.9.1?

> Is there anything personal in this file before I post it?

Claude reports each problem in plain words and tells you what Studio would do with the file. The check uses rules taken from the Studio source code. A real test slice needs Claude on the computer that has Bambu Studio.

### 8. Use your own saved Studio preset

> Use my "PLA Basic Flow Calibrated" filament preset for slot 1.

This works for your own filament and process presets, not for your own printer presets. It works only when Claude runs on the
computer that has your Bambu Studio presets. The preset must be saved from a Bambu preset made for that printer. If it is not, Claude says why and how to save it again. The 3MF holds the name of your preset, so check the name before you share the file.

## Everything you can ask for

| Area | You can ask Claude to |
|---|---|
| Printer | Pick the printer and nozzle; change the printer of an existing file with the same nozzle size (X1 Carbon, X1, X1E, P1S and P1P with each other; A1 with P2S). Other changes are refused, for example to an A1 mini or H2D, because the bed is different |
| Presets | List the exact preset names for a printer; pick or swap the process and filament presets; keep or drop the designer's own changes when a preset is swapped; use your own saved preset |
| Filament | Set the color per slot; set which slot an object or a part uses; add a slot; remove the last slot if nothing uses it; use Studio's automatic purge volumes or one fixed value |
| Settings | Set any Studio setting for the whole project, one object, or one part (walls, infill, speed, temperature, supports, seam, ironing, ...) |
| Objects | Put several models on a plate or on several plates; set the position; make copies; use a model made in inches; set a part as modifier, negative part, support blocker or support enforcer |
| Layers | Pause, change temperature, or add custom G-code at a height or a layer; set height ranges with their own layer height, walls, infill, speed or color |
| Temperature tower | Make a labeled tower (1 to 20 temperature steps, one filament), or set the temperature steps on your own tower model |
| Plate | Set the plate type for the project or per plate |
| Check | Check any Bambu 3MF; scan it for personal data; clean it before sharing |
| Slice | Test-slice in your installed Bambu Studio (only on the computer that has it) |

## Words that bring it up

Claude uses this skill when you mention a **Bambu Studio project**, a **3MF**, a **temperature tower**, a **pause** or a
**filament swap** at a layer, **variable layer height** or **height range**, **multi-color** or **AMS**, a file from **MakerWorld**, a part you want to **design** (for example in **OpenSCAD**) and print, or a printer by name (P1S, X1C, A1, P2S)
together with a model you want ready to print. "Use the 3MF builder" always works.

## What it does not do

- It does not slice to G-code, send to the printer, or start a print.
- It does not change the shape of a model (no scaling, cutting or repair).
- It does not rotate or auto-orient a model. Export the model in the orientation you want to print.
- It does not paint colors on a model. Each color must be a separate model file, exported from the same CAD file so the parts line up.
- In a project you already have, it does not add, remove or move objects. It changes settings, presets, colors, filament slots, plate type and layer actions only.
- It changes projects saved by Bambu Studio 2.8.2, 2.9.1 and 2.9.2 (and a newer 2.x, with a warning) for one-nozzle printers. Save an older project in Studio 2.9.2 first. A two-nozzle project (H2D, X2D) cannot be changed.
- It makes no flow or pressure advance calibration.
- It does not work with Orca Slicer, PrusaSlicer or Cura projects.
- It does not arrange objects to fit the plate like Studio's auto-arrange; it places them in a grid or where you say.
- When Claude runs in the cloud (the Claude app on the web or a phone), it cannot reach your installed Bambu Studio: no test slice
  and no user presets. Claude Code on the computer that has Studio can do both.
