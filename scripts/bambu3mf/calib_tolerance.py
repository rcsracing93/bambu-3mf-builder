"""[calibration] type = "tolerance": square pegs and square holes with labelled clearances. Stdlib only.

For each clearance c the job makes two objects:
  Hole <c>  a block `depth` thick with a square hole (side size + 2c) through it. The hole is real: the block is four strips around it.
  Peg <c>   a flange 2 mm thick with a square peg (side size, height depth) on it. It prints flange down and pushes into the hole.
The clearance is the gap on each of the four sides between two flat faces: (hole side - peg side) / 2. Faces are flat and square, so
the number is exact in the mesh and does not depend on a polygon's vertices. Each object carries "<c>" engraved in the top face of its
front strip (the 6 mm band at the front of the block, and of the flange).
"""
from . import calibjobs
from .calibration import CalibrationError, _Shells

KEYS = {"clearances", "size", "depth"}
DEFAULT_CLEARANCES = [0.1, 0.15, 0.2, 0.25, 0.3]
DEFAULT_SIZE = 10.0
DEFAULT_DEPTH = 6.0
WALL = 6.0            # mm, width of the block around the hole and of the front band that holds the label
FLANGE = 2.0          # mm, thickness of the peg flange (a 0.5 mm label leaves 1.5 mm)
SINK = 1.0            # mm, the peg and the strips overlap their neighbours by this much (the slicer unions them)


def fmt(c):
    return ("%.4f" % c).rstrip("0").rstrip(".")


def outer(size, c):
    """Side of the square block / flange for a clearance c."""
    return round(size + 2 * c + 2 * WALL, 6)


def hole_body(size, c, depth):
    """Block with a square hole of side size + 2c, centered on x 0, y 0. Returns (mesh, label face)."""
    h = round((size + 2 * c) / 2.0, 6)
    o = round(outer(size, c) / 2.0, 6)
    sh = _Shells("Hole %s" % fmt(c))
    sh.box(-o, o, -o, round(-h, 6), 0.0, depth)                              # front strip (holds the label)
    sh.box(-o, o, h, o, 0.0, depth)                                          # back strip
    sh.box(-o, -h, round(-h - SINK, 6), round(h + SINK, 6), 0.0, depth)     # left strip, overlaps the front and back strips
    sh.box(h, o, round(-h - SINK, 6), round(h + SINK, 6), 0.0, depth)       # right strip
    return sh.mesh.finish(), (-o, o, -o, -h, depth, depth)


def peg_body(size, c, depth):
    """Flange with a square peg of side size on it, centered on x 0, y 0. Returns (mesh, label face)."""
    p = round(size / 2.0, 6)
    o = round(outer(size, c) / 2.0, 6)
    sh = _Shells("Peg %s" % fmt(c))
    sh.box(-o, o, -o, o, 0.0, FLANGE)
    sh.box(-p, p, -p, p, round(FLANGE - SINK, 6), round(FLANGE + depth, 6))
    return sh.mesh.finish(), (-o, o, -o, -p, FLANGE, FLANGE)


def build(ctx):
    cl = ctx.numbers("clearances", list(DEFAULT_CLEARANCES), lo=0.02, hi=1.0, min_count=1, max_count=8)
    size = ctx.number("size", DEFAULT_SIZE, lo=4.0, hi=30.0)
    depth = ctx.number("depth", DEFAULT_DEPTH, lo=3.0, hi=20.0)
    if cl is None or size is None or depth is None:
        return
    cl = [round(c, 4) for c in cl]
    if len(set(cl)) != len(cl):
        ctx.err("clearances", "a clearance is listed twice")
        return
    try:
        for c in cl:
            hole, hface = hole_body(size, c, depth)
            ctx.add_object("Hole %s" % fmt(c), hole, [calibjobs.label_part(fmt(c), hface, "Label %s" % fmt(c))])
            peg, pface = peg_body(size, c, depth)
            ctx.add_object("Peg %s" % fmt(c), peg, [calibjobs.label_part(fmt(c), pface, "Label %s" % fmt(c))])
    except CalibrationError as e:
        ctx.err("", "%s (try a larger size or fewer decimals in the clearances)" % e)
        return
    ctx.info = {"clearances": cl, "size": size, "depth": depth}
