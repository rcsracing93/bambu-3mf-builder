"""[calibration] type = "mvs": maximum volumetric speed (1.3.0). Stdlib only.

One hollow rectangular tube, printed in bands. Each band raises the outer-wall speed so that the volumetric flow steps up. The band's
flow in mm3/s is engraved on a stepped tab on the front of the tube. You print it, look at the walls, and read the flow of the last
band that still looks clean.

Flow and speed. Studio computes the cross-section of an outer-wall line as a rounded rectangle (Flow.cpp, 2.9.2.62):
    Flow::mm3_per_mm()   line 235-246:  area = height * (width - height * (1 - pi/4))      "~ h (w - 0.215 h)"
    line 214-222 (rounded_rectangle_extrusion_spacing) uses the same term for the spacing.
The width is outer_wall_line_width (0 = auto, 1.125 * nozzle: Flow.cpp :22-35, :145-147) and the height is the layer height. So
    speed (mm/s) = flow (mm3/s) / area (mm2)
and the G-code feed rate is F = 60 * speed (mm/min). Studio caps a path at filament_max_volumetric_speed / mm3_per_mm
(GCode.cpp get_path_speed, :6555-6560); the type sets that filament value above the top band so the cap never acts.

What else could change the wall speed, and what the type does:
  - filament_max_volumetric_speed (above): set to the top flow + 2.
  - layer-time slowdown (slow_down_for_layer_cooling, slow_down_layer_time, slow_down_min_speed): slow_down_for_layer_cooling = 0.
  - overhang speeds (overhang_*_speed, GCode.cpp get_path_speed :6516-6531): enable_overhang_speed = 0. The walls are vertical, so
    none apply anyway.
  - the first layer prints at initial_layer_speed (GCode.cpp :6545-6551). It is not a measuring layer.
"""
import math

from .calibration import CalibrationError, _Shells
from . import calibjobs as cj

KEYS = {"start", "end", "step", "flows", "band_height"}

DEFAULTS = {"start": 10.0, "end": 30.0, "step": 2.0, "band_height": 5.0}
MAX_BANDS = 14
MIN_BANDS = 2
MIN_BAND_HEIGHT = 2.0       # a label needs 0.5 mm cut + 1.0 mm floor under the tab top; 2 mm keeps a safe margin
MAX_BAND_HEIGHT = 20.0
TUBE_W = 30.0               # mm, tube depth (y)
WALL = 0.9                  # mm, tube wall: two loops of 0.42 and 0.45 mm
MIN_LEN = 60.0              # mm, shortest tube: the long front wall is where the speed is reached
TAB_D = 6.0                 # mm, tab depth in front of the tube (label face 5.6 mm deep needs 3.6 + 2 * 0.8)
TAB_OVERLAP = 0.2           # mm, neighbouring tabs overlap so that no two shells share a vertex
TOP_MARGIN = 2.0            # filament_max_volumetric_speed = top flow + this, mm3/s


def line_area(width, height):
    """Cross-section of an extrusion line in mm2: Studio Flow::mm3_per_mm (Flow.cpp :235-246), rounded-rectangle model."""
    return height * (width - height * (1.0 - math.pi / 4.0))


def speed_for_flow(flow, width, height):
    """Outer-wall speed in mm/s that gives the flow in mm3/s."""
    return flow / line_area(width, height)


def _label(v):
    return ("%d" % round(v)) if abs(v - round(v)) < 1e-9 else ("%.2f" % v).rstrip("0")


def _flows(ctx):
    """The flows (mm3/s) of the bands, ascending. None after an error."""
    has_list = "flows" in ctx.raw
    given = [k for k in ("start", "end", "step") if k in ctx.raw]
    if has_list and given:
        ctx.err("flows", "give either flows or start / end / step, not both")
        return None
    if has_list:
        fl = ctx.numbers("flows", lo=0.5, hi=100, min_count=MIN_BANDS, max_count=MAX_BANDS)
        if fl is None:
            return None
        if any(b <= a for a, b in zip(fl, fl[1:])):
            ctx.err("flows", "must increase from one band to the next")
            return None
    else:
        a = ctx.number("start", DEFAULTS["start"], lo=0.5, hi=100)
        b = ctx.number("end", DEFAULTS["end"], lo=0.5, hi=100)
        s = ctx.number("step", DEFAULTS["step"], lo=0.05, hi=50)
        if a is None or b is None or s is None:
            return None
        if b <= a:
            ctx.err("end", "must be larger than start (%s)" % cj._g(a))
            return None
        n = int((b - a) / s + 1e-9) + 1
        if not (MIN_BANDS <= n <= MAX_BANDS):
            ctx.err("step", "gives %d bands; use %d to %d" % (n, MIN_BANDS, MAX_BANDS))
            return None
        fl = [round(a + i * s, 6) for i in range(n)]
    bad = [f for f in fl if abs(round(f, 2) - f) > 1e-9]
    if bad:
        ctx.err("flows" if has_list else "step", "%s has more than 2 decimals; the label shows 2 at most" % cj._g(bad[0]))
        return None
    return fl


def build(ctx):
    flows = _flows(ctx)
    bh = ctx.number("band_height", DEFAULTS["band_height"], lo=MIN_BAND_HEIGHT, hi=MAX_BAND_HEIGHT)
    if flows is None or bh is None:
        return
    lh = ctx.num_value("layer_height")
    width = ctx.num_value("outer_wall_line_width")
    if not width:                                               # 0 = auto: 1.125 * nozzle (Flow.cpp :22-35)
        width = 1.125 * float(str(ctx.mcfg["nozzle_diameter"][0] if isinstance(ctx.mcfg["nozzle_diameter"], list) else ctx.mcfg["nozzle_diameter"]))
    if lh is None or lh <= 0:
        ctx.err("", "no layer height found")
        return
    q = bh / lh
    if abs(q - round(q)) > 1e-6:
        ctx.err("band_height", "%s mm is not a whole number of %s mm layers" % (cj._g(bh), cj._g(lh)))
        return
    first = ctx.num_value("initial_layer_print_height")
    if first is not None and abs(first - lh) > 1e-9:
        ctx.err("band_height", "the first layer (%s mm) and the layer height (%s mm) differ, so the bands would not meet the layers" % (cj._g(first), cj._g(lh)))
        return
    speeds = [round(speed_for_flow(f, width, lh), 2) for f in flows]
    vmax = min(float(x) for x in ctx.mcfg.get("machine_max_speed_x", ["500"])[:1] + ctx.mcfg.get("machine_max_speed_y", ["500"])[:1])
    for f, s in zip(flows, speeds):
        if s > vmax:
            ctx.err("flows", "%s mm3/s needs %.0f mm/s, above the printer's %.0f mm/s axis limit (outer wall %s mm wide, layer %s mm)" % (
                cj._g(f), s, vmax, cj._g(width), cj._g(lh)))
            return
    if min(speeds) < 5:
        ctx.err("flows", "the lowest band would print at %.1f mm/s; start higher" % min(speeds))
        return

    # geometry (x along the tube, y front to back): a hollow tube, with one tab per band on the front wall
    n = len(flows)
    texts = [_label(f) for f in flows]
    tab_w = max(math.ceil((cj.label_size(t)[0] + 2 * cj.FACE_MARGIN + 0.4) * 2) / 2.0 for t in texts)
    pitch = tab_w - TAB_OVERLAP
    length = max(MIN_LEN, n * pitch + TAB_OVERLAP)
    h_total = n * bh
    ox, oy = -length / 2.0, -(TUBE_W - TAB_D) / 2.0               # bbox center at 0, 0: x -L/2..L/2, y -TAB_D..W
    sh = _Shells("MVS tube")
    t = WALL
    sh.box(ox, ox + length, oy, oy + t, 0.0, h_total)                                       # front wall
    sh.box(ox, ox + length, oy + TUBE_W - t, oy + TUBE_W, 0.0, h_total)                     # back wall
    sh.box(ox, ox + t, oy + t / 2.0, oy + TUBE_W - t / 2.0, 0.0, h_total)                  # left wall
    sh.box(ox + length - t, ox + length, oy + t / 2.0, oy + TUBE_W - t / 2.0, 0.0, h_total)  # right wall
    labels, ranges = [], []
    for i, (f, s, text) in enumerate(zip(flows, speeds, texts)):
        x0 = ox + i * pitch
        x1 = x0 + tab_w
        ztop = (i + 1) * bh
        sh.box(round(x0, 6), round(x1, 6), oy - TAB_D, oy + 0.6 * t, 0.0, ztop)
        # the face of the label: the tab top, less the part a taller neighbour overlaps
        fx0 = x0 + (TAB_OVERLAP if i else 0.0)
        face = (fx0, x1 - (TAB_OVERLAP if i < n - 1 else 0.0), oy - TAB_D, oy, ztop, ztop)
        labels.append(cj.label_part(text, face, name="Flow %s" % text))
        ranges.append({"min_z": i * bh, "max_z": ztop, "layer_height": lh, "settings": {"outer_wall_speed": s}})

    # no limiter may change the outer-wall speed
    ctx.set_filament({"filament_max_volumetric_speed": max(math.ceil(flows[-1]) + TOP_MARGIN, ctx.num_value("filament_max_volumetric_speed") or 0),
                      "slow_down_for_layer_cooling": False})
    ctx.set_process({"enable_overhang_speed": False})
    ctx.add_object("MVS tube", sh.mesh.finish(), labels, settings={"wall_loops": 2}, height_ranges=ranges)
    ctx.info = {"flows_mm3_per_s": flows, "outer_wall_speeds_mm_per_s": speeds, "band_height_mm": bh, "line_width_mm": width, "layer_height_mm": lh,
                "area_mm2": round(line_area(width, lh), 5)}
