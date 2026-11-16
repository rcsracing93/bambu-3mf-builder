"""Calibration temperature tower: the generated mesh (step S4). Stdlib only.

[temperature_tower] shape = "calibration" calls build_tower(temps). The result is one normal part (the body) and one negative
part per band (the temperature number, cut into the front of the label wall). Every part is made of closed manifold shells. Shells
overlap each other on purpose and never share a vertex (build_tower checks that): the slicer unions them per layer, and shells
that only touched would share edges, which the 3MF reader merges into non-manifold edges.

Coordinates: x runs left to right, y front (-5) to back (+5), z up from the bed. One band is 8 mm high:

    x -15.4 .. -4.2   label wall (11.2 mm), full height, the number (3 digits, 9.0 mm) is cut 0.6 mm into its front face
    x  -4.2 ..  5.8   slot, open front and back, 7 mm high; a 1 mm roof slab bridges it (10 mm span)
    x   5.8 ..  7.4   support wall for the roof
    x   7.4 .. 11.4   overhang wedge: underside rises at 45 degrees for 2 mm, then at 30 degrees for 2 mm
                      (angles from the horizontal plane; the wedge top is flat at the band top)
    pins: two 2.4 mm pins in the slot, 6 mm apart in y, standing on the slot floor (the roof slab of the band below), 6 mm
          tall, so their tops are 1 mm under the roof slab. Moving between them shows stringing.

Band 1 stands on a 1 mm base plate (32.8 x 16 mm, 3 mm wider than the body on each side). Highest temperature at the bottom.
"""
import math

from .mesh import Mesh

BAND_H = 8.0
BASE_T = 1.0
BASE_X = (-18.4, 14.4)
BASE_Y = (-8.0, 8.0)
Y0, Y1 = -5.0, 5.0
WALL_X = (-15.4, -4.2)                 # label wall
SLOT_X = (-4.2, 5.8)
SUP_X = (5.8, 7.4)                     # support wall under the roof
WEDGE_X = (7.4, 11.4)
ROOF_T = 1.0
ROOF_OVERLAP = 0.2                     # the roof slab reaches this far into both walls
SLOT_CENTER = (SLOT_X[0] + SLOT_X[1]) / 2.0
PIN_R, PIN_SIDES, PIN_Y, PIN_H, PIN_SINK = 1.2, 16, 3.0, 6.0, 0.4
OVERHANG_DEG = (45.0, 30.0)            # from the horizontal plane, nearest the wall first
OVERHANG_RUN = 2.0                     # horizontal length of each overhang step
DIGIT_W, DIGIT_H, STROKE, DIGIT_GAP = 2.4, 3.6, 0.6, 0.9
FACE_MARGIN = 1.0                      # least bare face left of the first and right of the last digit
CUT_DEPTH = 0.6
SEG_EXT = 0.15                         # vertical segments reach this far into the horizontal ones
SEGMENTS = {"0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc", "5": "afgcd", "6": "afgedc", "7": "abc",
            "8": "abcdefg", "9": "abcdfg"}
MAX_BANDS = 20


class CalibrationError(Exception):
    pass


# ---- shells ------------------------------------------------------------------------------------
class _Shells:
    """Collects closed shells in one Mesh. Refuses a shell that shares a vertex with an earlier one."""

    def __init__(self, name):
        self.mesh = Mesh(name)
        self.seen = set()
        self.n = 0

    def _add(self, pts, tris):
        keys = [tuple(p) for p in pts]
        clash = [k for k in keys if k in self.seen]
        if clash:
            raise CalibrationError("internal: shell %d shares vertex %s with an earlier shell" % (self.n, clash[0]))
        ids = [self.mesh.vertex(*k) for k in keys]
        self.seen.update(keys)
        for a, b, c in tris:
            self.mesh.triangle(ids[a], ids[b], ids[c])
        self.n += 1

    def box(self, x0, x1, y0, y1, z0, z1):
        p = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
        t = [(0, 2, 1), (0, 3, 2),         # bottom, normal -z
             (4, 5, 6), (4, 6, 7),         # top
             (0, 1, 5), (0, 5, 4),         # front, normal -y
             (2, 3, 7), (2, 7, 6),         # back
             (3, 0, 4), (3, 4, 7),         # left
             (1, 2, 6), (1, 6, 5)]         # right
        self._add(p, t)

    def prism(self, poly, y0, y1):
        """poly: simple polygon in (x, z), counter-clockwise seen from the front (x right, z up). Extruded from y0 to y1."""
        n = len(poly)
        pts = [(x, y0, z) for x, z in poly] + [(x, y1, z) for x, z in poly]
        tris = []
        for a, b, c in _ear_clip(poly):
            tris.append((a, b, c))                          # front face, normal -y
            tris.append((n + a, n + c, n + b))              # back face, normal +y
        for i in range(n):
            j = (i + 1) % n
            tris.append((i, n + i, n + j))                  # side quad, outward on the right of the edge
            tris.append((i, n + j, j))
        self._add(pts, tris)

    def cylinder(self, cx, cy, z0, z1, r, sides):
        pts = [(cx, cy, z0), (cx, cy, z1)]
        for i in range(sides):
            a = 2.0 * math.pi * i / sides
            pts.append((round(cx + r * math.cos(a), 6), round(cy + r * math.sin(a), 6), z0))
            pts.append((round(cx + r * math.cos(a), 6), round(cy + r * math.sin(a), 6), z1))
        tris = []
        for i in range(sides):
            j = (i + 1) % sides
            b0, t0, b1, t1 = 2 + 2 * i, 3 + 2 * i, 2 + 2 * j, 3 + 2 * j
            tris += [(0, b1, b0), (1, t0, t1), (b0, b1, t1), (b0, t1, t0)]
        self._add(pts, tris)


def _area2(poly):
    return sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly)))


def _ear_clip(poly):
    """Triangles (index triples) of a simple counter-clockwise polygon."""
    if _area2(poly) <= 0:
        raise CalibrationError("internal: polygon is not counter-clockwise")
    idx = list(range(len(poly)))
    out = []

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def inside(p, a, b, c):
        return cross(a, b, p) >= -1e-12 and cross(b, c, p) >= -1e-12 and cross(c, a, p) >= -1e-12

    guard = 0
    while len(idx) > 3:
        guard += 1
        if guard > 10000:
            raise CalibrationError("internal: polygon cannot be triangulated")
        for k in range(len(idx)):
            ia, ib, ic = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = poly[ia], poly[ib], poly[ic]
            if cross(a, b, c) <= 1e-12:
                continue
            if any(inside(poly[j], a, b, c) for j in idx if j not in (ia, ib, ic)):
                continue
            out.append((ia, ib, ic))
            idx.pop(k)
            break
        else:
            raise CalibrationError("internal: polygon cannot be triangulated")
    out.append((idx[0], idx[1], idx[2]))
    return out


# ---- geometry ----------------------------------------------------------------------------------
def band_bottom(k):
    """z of the bottom of band k (1 = lowest, hottest)."""
    return BASE_T + BAND_H * (k - 1)


def tower_height(n):
    return BASE_T + BAND_H * n


def _wedge_rises():
    r1 = OVERHANG_RUN * math.tan(math.radians(OVERHANG_DEG[0]))
    r2 = OVERHANG_RUN * math.tan(math.radians(OVERHANG_DEG[1]))
    return r1, r1 + r2


def _wedge_polygon(n):
    xw0, xw1 = WEDGE_X
    xm = xw0 + OVERHANG_RUN
    r1, r2 = _wedge_rises()
    xl = xw0 - 0.5                                          # reaches into the support wall
    z1 = band_bottom(1)
    poly = [(xl, z1), (xw0, z1)]
    for k in range(1, n + 1):
        zb = band_bottom(k)
        poly += [(xm, round(zb + r1, 6)), (xw1, round(zb + r2, 6)), (xw1, zb + BAND_H)]
        if k < n:
            poly.append((xw0, zb + BAND_H))                  # notch tip: the next band's underside starts here
    poly.append((xl, zb + BAND_H))
    return poly


def digit_cells(text):
    """[(digit char, x0)] left edges of the digit cells, the number centered on the label wall."""
    width = len(text) * DIGIT_W + (len(text) - 1) * DIGIT_GAP
    wall_w = WALL_X[1] - WALL_X[0]
    if width > wall_w - 2 * FACE_MARGIN:
        raise CalibrationError("the number %s is too wide for the label wall" % text)
    x = (WALL_X[0] + WALL_X[1]) / 2.0 - width / 2.0
    out = []
    for ch in text:
        out.append((ch, round(x, 6)))
        x += DIGIT_W + DIGIT_GAP
    return out


def segment_boxes(ch, x0, zc):
    """The cut boxes (x0, x1, z0, z1) of one 7-segment digit centered on height zc. Letters a..g as on a 7-segment display."""
    h = STROKE
    top, bot = zc + DIGIT_H / 2.0, zc - DIGIT_H / 2.0
    xr = x0 + DIGIT_W
    rows = {"a": (top - h, top), "g": (zc - h / 2.0, zc + h / 2.0), "d": (bot, bot + h)}
    up = (zc + h / 2.0 - SEG_EXT, top - h + SEG_EXT)
    lo = (bot + h - SEG_EXT, zc - h / 2.0 + SEG_EXT)
    cols = {"f": (x0, x0 + h, up), "b": (xr - h, xr, up), "e": (x0, x0 + h, lo), "c": (xr - h, xr, lo)}
    boxes = []
    for s in SEGMENTS[ch]:
        if s in rows:
            boxes.append((x0, xr, rows[s][0], rows[s][1]))
        else:
            a, b, (z0, z1) = cols[s]
            boxes.append((a, b, z0, z1))
    return boxes


def label_mesh(temp, k):
    sh = _Shells("Label %d" % temp)
    zc = band_bottom(k) + BAND_H / 2.0
    for ch, x0 in digit_cells(str(int(temp))):
        for (xa, xb, za, zb_) in segment_boxes(ch, x0, zc):
            sh.box(xa, xb, Y0 - 0.3, Y0 + CUT_DEPTH, round(za, 6), round(zb_, 6))
    return sh.mesh.finish()


def body_mesh(n):
    sh = _Shells("Tower body")
    top = tower_height(n)
    sh.box(BASE_X[0], BASE_X[1], BASE_Y[0], BASE_Y[1], 0.0, BASE_T)
    sh.box(WALL_X[0], WALL_X[1], Y0, Y1, BASE_T / 2.0, top)
    sh.box(SUP_X[0], SUP_X[1], Y0, Y1, BASE_T / 2.0, top)
    sh.prism(_wedge_polygon(n), Y0, Y1)
    for k in range(1, n + 1):
        zb = band_bottom(k)
        sh.box(SLOT_X[0] - ROOF_OVERLAP, SLOT_X[1] + ROOF_OVERLAP, Y0, Y1, zb + BAND_H - ROOF_T, zb + BAND_H)
        for sy in (-PIN_Y, PIN_Y):
            sh.cylinder(SLOT_CENTER, sy, zb - PIN_SINK, zb + PIN_H, PIN_R, PIN_SIDES)
    return sh.mesh.finish()


def build_tower(temps):
    """temps: nozzle temperatures, band 1 (bottom) first. Returns (body Mesh, [(temp, label Mesh)])."""
    if not 1 <= len(temps) <= MAX_BANDS:
        raise CalibrationError("the tower has %d bands; 1 to %d are supported" % (len(temps), MAX_BANDS))
    return body_mesh(len(temps)), [(t, label_mesh(t, k)) for k, t in enumerate(temps, 1)]


def temperatures(center=None, plus_minus=None, start=None, end=None, step=5):
    """Band temperatures, highest first. Either center +- plus_minus, or start and end. step is a positive whole number."""
    step = abs(int(step))
    if step < 1:
        raise CalibrationError("step must be at least 1")
    if center is not None:
        hi, lo = int(center) + int(plus_minus), int(center) - int(plus_minus)
    else:
        hi, lo = max(int(start), int(end)), min(int(start), int(end))
    if (hi - lo) % step:
        raise CalibrationError("the range %d to %d is not a whole number of %d C steps. Change the range or the step." % (lo, hi, step))
    temps = list(range(hi, lo - 1, -step))
    return temps
