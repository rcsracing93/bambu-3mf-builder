"""[calibration] type = "pa": a pressure advance (K) tower. Stdlib only.

One tower of bands. At the start of each band the file has the custom G-code line `M900 K<value>` (a layer G-code step, the same
thing a [[layer_gcode]] with `gcode = "M900 K0.02"` writes). Read the corners of each band and pick the K with the sharpest corners
and no bulge. Nothing here is print-tested: the file is validated, not print-tested.

Why a custom M900 works (checked on Studio 2.9.2.62, P1S, see tests/calib_check_pa.py):
  - For a Bambu printer Studio never writes M900 from the filament settings (GCode.cpp skips set_pressure_advance when
    is_BBL_Printer()), so nothing overrides the custom line. `enable_pressure_advance` and `pressure_advance` do nothing here.
    Studio's own PA calibration mode (Calib_PA_Tower) is not used.
  - The P1S start G-code and the filament start G-code have no M900 line.
So the K of a band stays in force until the next M900 line.

Shape (all in one object, one filament):
  - the tower: a hollow square tube, 30 mm wide, 0.84 mm walls (two 0.42 mm loops), open top, no infill. Four sharp corners per
    layer, thin walls. It stands on the bed (no floor plate).
  - the label stairs, in front of the tower: one step per band. Step i has its flat top face at the TOP of band i, and the K value
    is engraved into that face (0.5 mm deep, at least 1.0 mm of material below). The tower walls are never cut.
"""
from .calibration import CalibrationError, _Shells
from . import calibjobs as cj

KEYS = {"start", "end", "step", "values", "band_height"}

DEFAULT_START, DEFAULT_END, DEFAULT_STEP, DEFAULT_BAND = 0.0, 0.06, 0.01, 5.0
MIN_BANDS, MAX_BANDS = 2, 12
BAND_LO, BAND_HI = 2.0, 20.0         # mm; at least 2 mm so the first stair step has 1.5 mm of material for a label
TOWER, WALL = 30.0, 0.84              # mm
STEP_D, GAP = 6.0, 6.0                # mm: stair depth, space between the stairs and the tower
STEP_MIN_W = 12.0

# What makes the test valid (process settings, applied to the whole job):
PROCESS = {
    "wall_loops": 2,                  # the 0.84 mm wall is exactly two loops
    "sparse_infill_density": "0%",    # nothing inside the tube
    "outer_wall_speed": 200,          # fast enough that the corner pressure shows (16.8 mm3/s at 0.2 mm layers, under PLA's limit)
    "inner_wall_speed": 200,
}
FILAMENT = {
    "slow_down_for_layer_cooling": False,   # a 30 mm tube is a short layer; the cooling slowdown would take the speed away
}


def fmt(v):
    """K as text: up to 4 decimals, no trailing zeros ('0', '0.02', '0.045')."""
    s = ("%.4f" % round(v, 4)).rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def values_from(ctx):
    """The K list from `values`, or from start / end / step. None after an error."""
    kmax = ctx.by["pressure_advance"].get("max")
    hi = float(kmax) if kmax is not None else 2.0
    if "values" in ctx.raw:
        for k in ("start", "end", "step"):
            if k in ctx.raw:
                ctx.err(k, "give either values, or start / end / step, not both")
                return None
        vs = ctx.numbers("values", lo=0.0, hi=hi, min_count=MIN_BANDS, max_count=MAX_BANDS)
        if vs is None:
            return None
        vs = [round(v, 4) for v in vs]
    else:
        start = ctx.number("start", DEFAULT_START, lo=0.0, hi=hi)
        end = ctx.number("end", DEFAULT_END, lo=0.0, hi=hi)
        step = ctx.number("step", DEFAULT_STEP)
        if start is None or end is None or step is None:
            return None
        if step <= 0:
            ctx.err("step", "must be above 0 (the bands go from the lower of start and end up to the higher)")
            return None
        if end < start:
            start, end = end, start
        n = int(round((end - start) / step, 6)) + 1
        if abs(start + (n - 1) * step - end) > 1e-6:
            ctx.err("step", "%s does not divide the range %s to %s into whole steps" % (fmt(step), fmt(start), fmt(end)))
            return None
        if not (MIN_BANDS <= n <= MAX_BANDS):
            ctx.err("step", "start %s, end %s and step %s make %d bands; use %d to %d" % (fmt(start), fmt(end), fmt(step), n, MIN_BANDS, MAX_BANDS))
            return None
        vs = [round(start + i * step, 4) for i in range(n)]
    if len({fmt(v) for v in vs}) != len(vs):
        ctx.err("values", "two values give the same label; the values must differ at the fourth decimal")
        return None
    return vs


def build(ctx):
    vs = values_from(ctx)
    band = ctx.number("band_height", DEFAULT_BAND, lo=BAND_LO, hi=BAND_HI)
    if vs is None or band is None:
        return
    n = len(vs)
    labels_text = [fmt(v) for v in vs]
    lw = max(cj.label_size(t)[0] for t in labels_text)
    stepw = max(STEP_MIN_W, round(lw + 2 * cj.FACE_MARGIN + 0.4, 1))

    sh = _Shells("PA tower")
    # stairs: one closed shell, profile in x-z extruded over the depth. Step i is stepw wide and (i + 1) * band tall.
    poly = [(0.0, 0.0), (n * stepw, 0.0)]
    for i in range(n - 1, -1, -1):
        poly += [((i + 1) * stepw, (i + 1) * band), (i * stepw, (i + 1) * band)]
    sh.prism(poly, 0.0, STEP_D)
    # tower: four wall boxes that overlap at the corners (the slicer joins them); no two shells share a corner point
    ty0 = STEP_D + GAP
    tx0 = (n * stepw - TOWER) / 2.0
    th = n * band
    t, L = WALL, TOWER
    sh.box(tx0, tx0 + L, ty0, ty0 + t, 0.0, th)                                     # front wall
    sh.box(tx0, tx0 + L, ty0 + L - t, ty0 + L, 0.0, th)                             # back wall
    sh.box(tx0 + L - t, tx0 + L, ty0 + t / 2.0, ty0 + L - t / 2.0, 0.0, th)         # right wall
    sh.box(tx0, tx0 + t, ty0 + t / 2.0 + 0.01, ty0 + L - t / 2.0 - 0.01, 0.0, th)   # left wall
    body = sh.mesh.finish()

    labels = []
    try:
        for i, text in enumerate(labels_text):
            top = (i + 1) * band
            labels.append(cj.label_part(text, (i * stepw, (i + 1) * stepw, 0.0, STEP_D, top, top)))
    except CalibrationError as e:
        ctx.err("values", str(e))
        return

    ctx.set_process(PROCESS)
    ctx.set_filament(FILAMENT)
    if str(ctx.value("enable_pressure_advance")).lower() in ("1", "true"):
        ctx.warn("", "the filament preset has enable_pressure_advance on; for a Bambu printer Studio does not write it, so the custom M900 lines decide")
    ctx.add_object("PA tower", body, labels)

    zs = []
    for i, v in enumerate(vs):
        zs.append(ctx.add_layer_gcode(i * band, "M900 K%s" % fmt(v)))
    ctx.info = {"values": [fmt(v) for v in vs], "band_height": band, "layer_tops": zs,
                "note": "M900 K<value> at the first layer of each band; read the sharpest corner without bulge"}
