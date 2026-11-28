"""Calibration print jobs: [calibration] type = "flow" | "pa" | "mvs" | "tolerance" (1.3.0). Stdlib only.

This module is the shared frame. Each type has its own module (calib_flow.py, calib_pa.py, calib_mvs.py, calib_tolerance.py) with
    KEYS   the [calibration] keys the type takes (besides "type")
    build(ctx)   adds the objects, settings and layer G-code through the Ctx below; reports problems with ctx.err
The result is a normal Studio project, written by the normal writers: plain objects, per-object settings, height ranges and layer
G-code. No new file format.

Rules every type follows (the frame checks them, so a type cannot break them):
  - One filament (the job's single [[filament]] table). No [[object]] tables: the type makes its objects.
  - Labels: shallow engraved marks, LABEL_DEPTH (0.5 mm) deep, cut into a flat TOP face as negative parts (label_part). A label
    never cuts a load-bearing section: the face must leave at least LABEL_MIN_FLOOR (1.0 mm) of solid material under the cut, and
    every stroke stays FACE_MARGIN inside the face.
  - Placement (place): every object stays on the bed, outside the printer's bed_exclude_area (from the machine preset; on the P1S
    X 0-18, Y 0-28) and at least FRONT_MARGIN (15 mm) from the front edge, clear of the nozzle load line the start G-code draws
    there (Y -3 to 1 on the P1S). Objects are packed in rows from the front-left of the free area, GAP apart.
  - Settings go through the job's own setting checks (names, types, ranges, aliases) like settings a user writes.
"""
import importlib

from .calibration import CalibrationError, _Shells, SEGMENTS

TYPES = ("flow", "pa", "mvs", "tolerance")
COMMON_KEYS = {"type"}
LABEL_DEPTH = 0.5          # mm, engraved marks
LABEL_MIN_FLOOR = 1.0      # mm of solid material left under a label cut
FACE_MARGIN = 0.8          # mm, least bare face around a label
FRONT_MARGIN = 15.0        # mm from the front edge of the bed (nozzle load line)
GAP = 5.0                  # mm between calibration objects
# label characters: 7-segment digits, the decimal point and the minus sign. Size in mm.
CHAR_W, CHAR_H, STROKE, CHAR_GAP, DOT_W = 2.4, 3.6, 0.6, 0.7, 0.6
SEG_EXT = 0.15


class Ctx:
    """What a calibration type sees. Built by run() from the loaded job."""

    def __init__(self, raw, job, ps, by, chk, ver, settings_table):
        self.raw, self.job, self.ps, self.by, self.chk, self.ver = raw, job, ps, by, chk, ver
        self.type = raw.get("type")
        self.where = "[calibration]"
        self._settings_table = settings_table
        self.filament = job.filaments[0]
        self.fcfg = ps.resolve("filament", self.filament["preset"])[0]
        self.pcfg = ps.resolve("process", job.process)[0]
        self.mcfg = ps.resolve("machine", job.printer)[0]
        self.objects = []                                   # [(entry, (w, d))] in the order added; placed by run()

    # ---- problems -----------------------------------------------------------------------
    def err(self, key, msg):
        self.chk.err("%s.%s" % (self.where, key) if key else self.where, msg)

    def warn(self, key, msg):
        self.chk.warn("%s.%s" % (self.where, key) if key else self.where, msg)

    # ---- reading the [calibration] table ------------------------------------------------
    def number(self, key, default=None, lo=None, hi=None, whole=False):
        """A number from the table (or default). Out of range or not a number: an error, and None."""
        if key not in self.raw:
            return default
        v = self.raw[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or (whole and v != int(v)):
            self.err(key, "%r is not a %s" % (v, "whole number" if whole else "number"))
            return None
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            self.err(key, "%s is outside %s to %s" % (_g(v), _g(lo) if lo is not None else "-", _g(hi) if hi is not None else "-"))
            return None
        return int(v) if whole else float(v)

    def numbers(self, key, default=None, lo=None, hi=None, min_count=1, max_count=20):
        """A list of numbers from the table (or default)."""
        if key not in self.raw:
            return default
        v = self.raw[key]
        if not isinstance(v, list) or not (min_count <= len(v) <= max_count) or any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in v):
            self.err(key, "must be a list of %d to %d numbers" % (min_count, max_count))
            return None
        bad = [x for x in v if (lo is not None and x < lo) or (hi is not None and x > hi)]
        if bad:
            self.err(key, "%s outside %s to %s" % (", ".join(_g(x) for x in bad), _g(lo) if lo is not None else "-", _g(hi) if hi is not None else "-"))
            return None
        return [float(x) for x in v]

    # ---- values from the presets and the job -------------------------------------------------
    def value(self, key):
        """The value that applies: the job's setting (process or filament), else the preset's. Lists give their first entry."""
        for src in (self.job.settings, self.filament["settings"], self.pcfg, self.fcfg):
            if key in src:
                v = src[key]
                return v[0] if isinstance(v, list) else v
        return None

    def num_value(self, key):
        v = self.value(key)
        return None if v is None else float(str(v).rstrip("%"))

    def layer_top(self, z):
        """z of the top of the first layer that starts at or above z (where a layer change prints from). Uses the first layer
        height and the layer height that apply."""
        first, lh = self.num_value("initial_layer_print_height"), self.num_value("layer_height")
        if z <= first + 1e-9:
            return round(first, 4)
        return round(first + (int((z - first) / lh + 1e-6) + 1) * lh, 4)

    # ---- writing ----------------------------------------------------------------------------
    def settings(self, tbl, where):
        """Process settings, checked like a user's [settings] (names, types, ranges, aliases). Returns the checked dict."""
        return self._settings_table(tbl, ("print",), self.by, self.ver, "%s %s" % (self.where, where), self.chk, "%s %s (process settings)" % (self.where, where))

    def set_process(self, tbl):
        self.job.settings.update(self.settings(tbl, "process"))

    def set_filament(self, tbl):
        """Filament settings on the job's filament (written as changed settings)."""
        self.filament["settings"].update(self._settings_table(tbl, ("filament",), self.by, self.ver, self.where + " filament", self.chk,
                                                              self.where + " (filament settings)"))

    def add_object(self, name, body, labels=(), settings=None, height_ranges=None):
        """One object: body = Mesh (normal part), labels = [Mesh] from label_part (negative parts), settings = process settings of
        the object, height_ranges = [{"min_z", "max_z", "layer_height", "settings"}] (settings a part may have)."""
        lo, hi = body.bbox()
        if lo[2] < -1e-6:
            raise CalibrationError("internal: object %s reaches below the bed (z %.3f)" % (name, lo[2]))
        parts = [{"name": name, "mesh": "calib_%s_%d" % (self.type, len(self.objects) + 1), "mesh_obj": body, "filament": 1,
                  "type": "normal_part", "units": "mm", "settings": {}}]
        for i, m in enumerate(labels, 1):
            parts.append({"name": m.name or "Label %d" % i, "mesh": "calib_%s_%d_label_%d" % (self.type, len(self.objects) + 1, i),
                          "mesh_obj": m, "filament": 1, "type": "negative_part", "units": "mm", "settings": {}})
        rng = []
        for r in height_ranges or []:
            st = self.settings(dict(r.get("settings") or {}), "%s height range" % name)
            bad = sorted(k for k in st if k not in self.ps.range_options)
            if bad:
                raise CalibrationError("internal: %s cannot be set in a height range" % ", ".join(bad))
            rng.append({"min_z": float(r["min_z"]), "max_z": float(r["max_z"]), "layer_height": float(r["layer_height"]), "filament": 0,
                        "settings": st})
        ent = {"name": name, "plate": 1, "position": None, "copies": 1, "parts": parts,
               "settings": self.settings(dict(settings or {}), name), "height_ranges": rng}
        self.objects.append((ent, (hi[0] - lo[0], hi[1] - lo[1])))
        return ent

    def add_layer_gcode(self, z, gcode):
        """Custom G-code at the layer that starts at or above z (snapped with layer_top). Returns the z used."""
        zt = self.layer_top(z)
        self.job.layer_gcode.append({"plate": 1, "z": zt, "kind": "gcode", "value": gcode, "calibration": True})
        return zt


def _g(v):
    return ("%d" % v) if float(v) == int(v) else ("%g" % v)


# ---- labels ------------------------------------------------------------------------------------
def label_size(text):
    """(width, height) in mm of a label."""
    w = 0.0
    for i, ch in enumerate(text):
        w += DOT_W if ch == "." else CHAR_W
        if i:
            w += CHAR_GAP
    return w, CHAR_H


def label_part(text, face, name=None):
    """Negative-part Mesh that engraves text into a flat top face. face = (x0, x1, y0, y1, z_top, thickness): the rectangle of the
    face, its height, and the solid material under it. The text reads from the front (+y is up on the label), centered on the face.
    Characters: 0-9 . - . Raises CalibrationError when the text does not fit, or when the cut would leave less than
    LABEL_MIN_FLOOR of material under it."""
    x0, x1, y0, y1, ztop, thick = face
    bad = [c for c in text if c not in SEGMENTS and c not in ".-"]
    if not text or bad:
        raise CalibrationError("label %r: only digits, '.' and '-' can be engraved" % text)
    if thick - LABEL_DEPTH < LABEL_MIN_FLOOR - 1e-9:
        raise CalibrationError("label %r: the face has %.2f mm of material; a %.1f mm cut must leave %.1f mm" % (text, thick, LABEL_DEPTH, LABEL_MIN_FLOOR))
    w, h = label_size(text)
    if w > (x1 - x0) - 2 * FACE_MARGIN + 1e-9 or h > (y1 - y0) - 2 * FACE_MARGIN + 1e-9:
        raise CalibrationError("label %r (%.1f x %.1f mm) does not fit on a %.1f x %.1f mm face with %.1f mm margins" % (
            text, w, h, x1 - x0, y1 - y0, FACE_MARGIN))
    sh = _Shells(name or "Label %s" % text)
    x = (x0 + x1) / 2.0 - w / 2.0
    yc = (y0 + y1) / 2.0
    zb, zt = round(ztop - LABEL_DEPTH, 6), round(ztop + 0.3, 6)       # the cut reaches above the face so the top is open
    for ch in text:
        for (ax, bx, ay, by) in _char_boxes(ch, x, yc):
            sh.box(round(ax, 6), round(bx, 6), round(ay, 6), round(by, 6), zb, zt)
        x += (DOT_W if ch == "." else CHAR_W) + CHAR_GAP
    return sh.mesh.finish()


def _char_boxes(ch, x0, yc):
    """Stroke rectangles (x0, x1, y0, y1) of one character on the top face; y up = away from the viewer at the front."""
    h = STROKE
    if ch == ".":
        b = yc - CHAR_H / 2.0
        return [(x0, x0 + DOT_W, b, b + DOT_W)]
    if ch == "-":
        return [(x0, x0 + CHAR_W, yc - h / 2.0, yc + h / 2.0)]
    top, bot = yc + CHAR_H / 2.0, yc - CHAR_H / 2.0
    xr = x0 + CHAR_W
    rows = {"a": (top - h, top), "g": (yc - h / 2.0, yc + h / 2.0), "d": (bot, bot + h)}
    up = (yc + h / 2.0 - SEG_EXT, top - h + SEG_EXT)
    lo = (bot + h - SEG_EXT, yc - h / 2.0 + SEG_EXT)
    cols = {"f": (x0, x0 + h, up), "b": (xr - h, xr, up), "e": (x0, x0 + h, lo), "c": (xr - h, xr, lo)}
    out = []
    for s in SEGMENTS[ch]:
        if s in rows:
            out.append((x0, xr, rows[s][0], rows[s][1]))
        else:
            a, b, (ya, yb) = cols[s]
            out.append((a, b, ya, yb))
    return out


# ---- placement ---------------------------------------------------------------------------------
def free_area(mcfg):
    """(bed rect (x0, y0, x1, y1), exclude rects) from the machine preset."""
    pts = [tuple(float(c) for c in s.split("x")) for s in mcfg.get("printable_area", [])]
    bed = (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))
    ex = [tuple(float(c) for c in s.split("x")) for s in mcfg.get("bed_exclude_area", []) if s]
    rects = [(min(p[0] for p in ex), min(p[1] for p in ex), max(p[0] for p in ex), max(p[1] for p in ex))] if len(ex) >= 3 else []
    return bed, rects


def place(sizes, bed, excludes, front=FRONT_MARGIN, gap=GAP):
    """Centers (x, y) for objects of the given (w, d) sizes: rows from the front-left, starting FRONT_MARGIN in from the front edge,
    each row skipping the exclude rects. Raises CalibrationError when they do not fit."""
    x0, y0, x1, y1 = bed
    out = []
    y = y0 + front
    x = x0 + gap
    rowd = 0.0
    for w, d in sizes:
        for _ in range(1000):
            if x + w > x1 - gap:
                x, y, rowd = x0 + gap, y + rowd + gap, 0.0
            if y + d > y1 - gap:
                raise CalibrationError("the calibration objects do not fit on the bed (%.0f x %.0f mm free)" % (x1 - x0, y1 - y0 - front))
            box = (x, y, x + w, y + d)
            hit = [r for r in excludes if box[0] < r[2] + gap and box[2] > r[0] - gap and box[1] < r[3] + gap and box[3] > r[1] - gap]
            if not hit:
                break
            x = max(r[2] for r in hit) + gap                # step right past the exclude area
        out.append((x + w / 2.0, y + d / 2.0))
        x += w + gap
        rowd = max(rowd, d)
    return out


def check_placement(centers, sizes, bed, excludes, front=FRONT_MARGIN):
    """[problem] for objects outside the bed, closer than front to the front edge, or touching an exclude rect."""
    probs = []
    for i, ((cx, cy), (w, d)) in enumerate(zip(centers, sizes), 1):
        b = (cx - w / 2.0, cy - d / 2.0, cx + w / 2.0, cy + d / 2.0)
        if b[0] < bed[0] or b[2] > bed[2] or b[3] > bed[3]:
            probs.append("object %d is outside the bed" % i)
        if b[1] < bed[1] + front - 1e-6:
            probs.append("object %d starts at y %.1f, less than %.0f mm from the front edge" % (i, b[1], front))
        for r in excludes:
            if b[0] < r[2] and b[2] > r[0] and b[1] < r[3] and b[3] > r[1]:
                probs.append("object %d is in the exclude area x %g-%g, y %g-%g" % (i, r[0], r[2], r[1], r[3]))
    return probs


# ---- the job ------------------------------------------------------------------------------------
def type_module(t):
    return importlib.import_module("bambu3mf.calib_%s" % t)


def run(raw, job, ps, by, chk, ver, settings_table, nfil):
    """[calibration]: check the frame rules, call the type, place its objects."""
    w = "[calibration]"
    if not isinstance(raw, dict):
        chk.err(w, "must be a table with type = \"flow\", \"pa\", \"mvs\" or \"tolerance\"")
        return
    t = raw.get("type")
    if t not in TYPES:
        chk.err(w + ".type", "%r. Use one of: %s" % (t, ", ".join('"%s"' % x for x in TYPES)))
        return
    mod = type_module(t)
    allowed = COMMON_KEYS | set(mod.KEYS)
    for k in raw:
        if k not in allowed:
            chk.err("%s.%s" % (w, k), "unknown key for type = \"%s\". Keys: %s" % (t, ", ".join(sorted(allowed))))
    if nfil != 1:
        chk.err("filament", "a calibration job prints in one filament; the job has %d [[filament]] tables" % nfil)
        return
    if "meta" not in job.filaments[0] or chk.errors:
        return                                              # the filament / preset errors are reported already
    ctx = Ctx(raw, job, ps, by, chk, ver, settings_table)
    try:
        mod.build(ctx)
        if chk.errors:
            return
        if not ctx.objects:
            raise CalibrationError("internal: type %s made no object" % t)
        bed, ex = free_area(ctx.mcfg)
        sizes = [s for _e, s in ctx.objects]
        centers = place(sizes, bed, ex)
        probs = check_placement(centers, sizes, bed, ex)
        if probs:
            raise CalibrationError("internal: placement: " + "; ".join(probs))
    except CalibrationError as e:
        chk.err(w, str(e))
        return
    for (ent, _s), (cx, cy) in zip(ctx.objects, centers):
        ent["position"] = (round(cx - bed[0], 4), round(cy - bed[1], 4))   # plate-local, like [[object]] position
        job.objects.append(ent)
    job.calib_job = {"type": t, "objects": [e["name"] for e, _s in ctx.objects], "info": getattr(ctx, "info", {})}
