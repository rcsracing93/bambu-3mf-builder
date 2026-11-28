"""[calibration] type = "flow": flat pads, each with its own print_flow_ratio, each labelled with that value. Stdlib only.

What the number means (Studio source, GCode.cpp: the extruded volume per mm of every path is multiplied by the object's
print_flow_ratio; the filament's filament_flow_ratio is already part of the path flow, so the two multiply). The label is the
per-object factor. The effective flow of a pad is filament_flow_ratio x label. Top solid infill and the first layer have their own
extra ratios (top_solid_infill_flow_ratio, initial_layer_flow_ratio); they apply on every pad the same way.
Job table:  ratios = [0.90, 0.95, 1.00, 1.05, 1.10]   (2 to 9 values, 0.5 to 1.5, at most 3 decimals, no repeats)
"""
from . import calibjobs
from .calibration import _Shells

KEYS = {"ratios"}
DEFAULT_RATIOS = [0.90, 0.95, 1.00, 1.05, 1.10]
PAD = 28.0          # mm, side of a pad
THICK = 3.0         # mm; the label cut leaves 2.5 mm under it
LABEL_STRIP = 8.0   # mm of the front edge of the top face that holds the label; the rest of the top face stays flat for reading
TOP_LAYERS = 5


def _fmt(v):
    """Two decimals (0.90, 1.00), three when the value needs them (0.975)."""
    return "%.2f" % v if abs(round(v, 2) - v) < 1e-9 else "%.3f" % v


def build(ctx):
    ratios = ctx.numbers("ratios", DEFAULT_RATIOS, lo=0.5, hi=1.5, min_count=2, max_count=9)
    if ratios is None:
        return
    bad = [v for v in ratios if abs(round(v, 3) - v) > 1e-9]
    if bad:
        ctx.err("ratios", "%s has more than 3 decimals" % ", ".join("%g" % v for v in bad))
        return
    if len({round(v, 3) for v in ratios}) != len(ratios):
        ctx.err("ratios", "the same value is in the list more than once")
        return
    # Why these settings: the test reads the top surface. Five top layers make it a closed, solid skin on every pad, and
    # ironing would smooth the surface and hide the difference between the pads.
    ctx.set_process({"top_shell_layers": TOP_LAYERS, "ironing_type": "no ironing"})
    for v in ratios:
        sh = _Shells("Flow pad %s" % _fmt(v))
        sh.box(-PAD / 2, PAD / 2, -PAD / 2, PAD / 2, 0.0, THICK)
        # label on the front strip (y near -PAD/2, the front of the pad); the rest of the top face is left flat
        face = (-PAD / 2, PAD / 2, -PAD / 2, -PAD / 2 + LABEL_STRIP, THICK, THICK)
        lab = calibjobs.label_part(_fmt(v), face, name="Label %s" % _fmt(v))
        ctx.add_object("Flow %s" % _fmt(v), sh.mesh.finish(), [lab], settings={"print_flow_ratio": v})
    ctx.info = {"ratios": [_fmt(v) for v in ratios],
                "meaning": "each pad prints with its label as the object flow ratio; effective flow = filament flow ratio x label"}
