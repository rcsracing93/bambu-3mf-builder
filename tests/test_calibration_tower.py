#!/usr/bin/env python3
"""Calibration temperature tower ([temperature_tower] shape = "calibration"): meshes closed with positive volume, digits readable
from the front wall, bands and temperature changes at the right heights, footprint inside the bed, default range = preset +-10 in
5 C steps, start / end / step, option errors, build + strict validation.
Run: python3 -I tests/test_calibration_tower.py"""
import os, sys, tempfile, zipfile
H = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(H)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf.job import load_job, JobError
from bambu3mf import package, calibration as cal, validate

fails = []
def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)[:300]) if (extra and not cond) else ""))
    if not cond: fails.append(name)

HEAD = '''studio = "2.8.2"
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"
[[filament]]
preset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"
color = "#FF8000"
'''
tmp = tempfile.mkdtemp()
def job_from(tower_text, head=HEAD, name="j"):
    p = os.path.join(tmp, name + ".toml")
    open(p, "w").write(head + "[temperature_tower]\n" + tower_text)
    return p

def errors(tower_text, head=HEAD):
    try:
        load_job(job_from(tower_text, head))
    except JobError as e:
        return " | ".join(e.problems)
    return ""

# ---- 1. default range: preset nozzle temperature +-10 in 5 C steps ----------------------------
j = load_job(job_from('shape = "calibration"\n'))
check("default range: 230 down to 210, five bands", j.calibration["temps"] == [230, 225, 220, 215, 210], j.calibration)
check("no [[object]] needed: one object, one body and five labels", len(j.objects) == 1 and len(j.objects[0]["parts"]) == 6)
types = [p["type"] for p in j.objects[0]["parts"]]
check("one normal part (the body), the rest negative", types[0] == "normal_part" and set(types[1:]) == {"negative_part"} and types.count("normal_part") == 1, types)
check("highest temperature at the bottom", j.calibration["temps"] == sorted(j.calibration["temps"], reverse=True))
check("first band temperature is the first-layer temperature",
      j.filaments[0]["settings"]["nozzle_temperature_initial_layer"] == "230" and j.filaments[0]["settings"]["nozzle_temperature"] == "230")

# preset temperature of another filament: Silk+ is 230, so 240 .. 220
head_silk = HEAD.replace("Bambu PLA Basic @BBL P1S 0.4 nozzle", "Bambu PLA Silk+ @BBL P1S 0.4 nozzle")
js = load_job(job_from('shape = "calibration"\n', head_silk))
check("range follows the preset (Silk+ 230)", js.calibration["temps"] == [240, 235, 230, 225, 220], js.calibration)
# a job-level nozzle_temperature is the center
jc = load_job(job_from('shape = "calibration"\n', HEAD + '[filament.settings]\nnozzle_temperature = 210\n'))
check("a filament setting nozzle_temperature is the center", jc.calibration["temps"] == [220, 215, 210, 205, 200], jc.calibration)
j2 = load_job(job_from('shape = "calibration"\ncenter = 200\nplus_minus = 20\nstep = 10\n'))
check("center / plus_minus / step", j2.calibration["temps"] == [220, 210, 200, 190, 180], j2.calibration)

# ---- 2. start / end / step ----------------------------------------------------------------------
j3 = load_job(job_from('shape = "calibration"\nstart = 240\nend = 220\nstep = 10\n'))
check("start / end / step", j3.calibration["temps"] == [240, 230, 220], j3.calibration)
j3b = load_job(job_from('shape = "calibration"\nstart = 200\nend = 220\nstep = 5\n'))
check("start below end: still highest at the bottom", j3b.calibration["temps"] == [220, 215, 210, 205, 200], j3b.calibration)
check("one band is allowed", load_job(job_from('shape = "calibration"\ncenter = 215\nplus_minus = 0\n')).calibration["temps"] == [215])

# ---- 3. option errors ---------------------------------------------------------------------------
check("center with start is refused", "not both" in errors('shape = "calibration"\ncenter = 220\nstart = 230\nend = 210\n'))
check("start without end is refused", "start and end go together" in errors('shape = "calibration"\nstart = 230\n'))
check("range that is not a whole number of steps", "whole number" in errors('shape = "calibration"\nstart = 230\nend = 212\nstep = 5\n'))
check("custom-tower keys are refused for the calibration tower", "belongs to shape" in errors('shape = "calibration"\nsteps = 4\n'))
check("unknown shape", "calibration" in errors('shape = "pyramid"\n'))
check("[[object]] with a calibration tower is refused", "makes its own object" in errors('shape = "calibration"\n', HEAD + '[[object]]\nname = "x"\n[[object.part]]\nmesh = "x.stl"\n'))
check("two filaments are refused", "one filament" in errors('shape = "calibration"\n', HEAD + '[[filament]]\npreset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"\ncolor = "#000000"\n'))
check("more than 20 bands is refused", "1 to 20" in errors('shape = "calibration"\nstart = 300\nend = 100\nstep = 5\n'))
check("custom tower without shape still works as before", errors('start = 230\nstep = -5\nstep_height = 4\nfirst_z = 4\nsteps = 2\n', HEAD + '[[object]]\n[[object.part]]\nmesh = "../examples/data/cube20.stl"\n').find("unknown key") < 0)

# ---- 4. meshes: closed, positive volume, no degenerate triangles ---------------------------------
for temps in ([230, 225, 220, 215, 210], [240], [260, 250]):
    body, labels = cal.build_tower(temps)
    for m in [body] + [l for _t, l in labels]:
        check("%s (%d bands): closed manifold" % (m.name, len(temps)), m.edge_report() == (0, 0, 0), m.edge_report())
        check("%s (%d bands): positive volume" % (m.name, len(temps)), m.volume() > 0, m.volume())
        check("%s (%d bands): no degenerate triangle" % (m.name, len(temps)), m.degenerate() == 0)
        check("%s (%d bands): finite coordinates" % (m.name, len(temps)), all(abs(v) < 1000 for v in m.v))

# ---- 5. digits: read back from the front wall -----------------------------------------------------
def winding_y(mesh, x, z, y0):
    """Winding number of the mesh around the point (x, y0, z): ray along +y through triangles that lie in planes y = const."""
    w = 0
    v, t = mesh.v, mesh.t
    for i in range(0, len(t), 3):
        a, b, c = [(v[t[i + k] * 3], v[t[i + k] * 3 + 1], v[t[i + k] * 3 + 2]) for k in range(3)]
        if not (a[1] == b[1] == c[1]) or a[1] <= y0:
            continue
        # triangle in the (x, z) plane; sign of the y component of the normal gives the crossing direction
        n = (b[0] - a[0]) * (c[2] - a[2]) - (b[2] - a[2]) * (c[0] - a[0])           # y component of the normal is -n (see below)
        def side(p, q):
            return (q[0] - p[0]) * (z - p[2]) - (q[2] - p[2]) * (x - p[0])
        s1, s2, s3 = side(a, b), side(b, c), side(c, a)
        if (s1 >= 0 and s2 >= 0 and s3 >= 0) or (s1 <= 0 and s2 <= 0 and s3 <= 0):
            w += 1 if n < 0 else -1
    return w

STD = {"0": "abcdef", "1": "bc", "2": "abdeg", "3": "abcdg", "4": "bcfg", "5": "acdfg", "6": "acdefg", "7": "abc", "8": "abcdefg", "9": "abcdfg"}   # written out here, not taken from the module

def read_digit(mesh, x0, zc, y):
    """The digit cut in the cell with its left edge at x0, centered on height zc: look at the seven segment centers."""
    h = cal.STROKE
    top, bot = zc + cal.DIGIT_H / 2, zc - cal.DIGIT_H / 2
    xr = x0 + cal.DIGIT_W
    pos = {"a": (x0 + cal.DIGIT_W / 2, top - h / 2), "g": (x0 + cal.DIGIT_W / 2, zc), "d": (x0 + cal.DIGIT_W / 2, bot + h / 2),
           "f": (x0 + h / 2, zc + cal.DIGIT_H / 4), "b": (xr - h / 2, zc + cal.DIGIT_H / 4),
           "e": (x0 + h / 2, zc - cal.DIGIT_H / 4), "c": (xr - h / 2, zc - cal.DIGIT_H / 4)}
    on = "".join(s for s in "abcdefg" if winding_y(mesh, pos[s][0], pos[s][1], y) != 0)
    for ch, segs in STD.items():
        if set(segs) == set(on):
            return ch
    return "?" + on

temps = [230, 225, 220, 215, 210]
body, labels = cal.build_tower(temps)
for k, (t, m) in enumerate(labels, 1):
    zc = cal.band_bottom(k) + cal.BAND_H / 2
    read = "".join(read_digit(m, x0, zc, cal.Y0 + cal.CUT_DEPTH / 2) for _ch, x0 in cal.digit_cells(str(t)))
    check("band %d: digits read back as %d" % (k, t), read == str(t), read)
for d in "0123456789":                                                # every digit, on its own
    sh = cal._Shells("one digit")
    for (xa, xb, za, zb_) in cal.segment_boxes(d, 0.0, 4.0):
        sh.box(xa, xb, cal.Y0 - 0.3, cal.Y0 + cal.CUT_DEPTH, za, zb_)
    got = read_digit(sh.mesh, 0.0, 4.0, cal.Y0 + 0.3)
    check("digit %s is drawn as %s" % (d, d), got == d, got)
for t, m in labels:
    lo, hi = m.bbox()
    check("label %d: cut is %.1f mm deep into the front face and starts outside it" % (t, cal.CUT_DEPTH),
          abs(hi[1] - (cal.Y0 + cal.CUT_DEPTH)) < 1e-9 and lo[1] < cal.Y0)
    check("label %d: digits are 3.6 mm high and inside the label wall" % t,
          abs((hi[2] - lo[2]) - cal.DIGIT_H) < 1e-9 and lo[0] >= cal.WALL_X[0] and hi[0] <= cal.WALL_X[1])

# readable size: 7-segment digits proven on the P1S (0.4 nozzle)
check("digit width >= 2.4, stroke >= 0.6, gap >= 0.9", cal.DIGIT_W >= 2.4 and cal.STROKE >= 0.6 and cal.DIGIT_GAP >= 0.9, (cal.DIGIT_W, cal.STROKE, cal.DIGIT_GAP))
for k, (t, m) in enumerate(labels, 1):
    cells = cal.digit_cells(str(t))
    left = cells[0][1] - cal.WALL_X[0]
    right = cal.WALL_X[1] - (cells[-1][1] + cal.DIGIT_W)
    gaps = [cells[i + 1][1] - (cells[i][1] + cal.DIGIT_W) for i in range(len(cells) - 1)]
    check("band %d (%d): face margin >= 1.0 mm each side (%.2f / %.2f), gaps >= 0.9" % (k, t, left, right),
          left >= 1.0 - 1e-9 and right >= 1.0 - 1e-9 and all(g >= 0.9 - 1e-9 for g in gaps))
    lo, hi = m.bbox()
    check("band %d (%d): cut width %.2f = 3 digits + 2 gaps" % (k, t, hi[0] - lo[0]), abs((hi[0] - lo[0]) - (3 * 2.4 + 2 * 0.9)) < 1e-6)
check("label wall front is at least 11 mm", cal.WALL_X[1] - cal.WALL_X[0] >= 11.0)

# ---- 6. build: heights, bands, temperature changes, footprint ----------------------------------------
out = os.path.join(tmp, "cal.3mf")
j = load_job(job_from('shape = "calibration"\n'))
s = package.build(j, out)
ob = s.objects[0]
check("object height = base plate + 5 bands of 8 mm", abs(ob.h - (2 + 5 * 8)) < 1e-9, ob.h)
bed = package.bed_rect(j.profiles.resolve("machine", j.printer)[0])
x, y = ob.inst[0]
check("footprint inside the bed", x - ob.w / 2 >= bed[0] and x + ob.w / 2 <= bed[2] and y - ob.d / 2 >= bed[1] and y + ob.d / 2 <= bed[3], (x, y, ob.w, ob.d))
check("body is the whole footprint, base plate is 2 mm thick and wider than the body", ob.w > 34 and ob.d > 19 and abs(cal.BASE_T - 2.0) < 1e-9)
zs = [g["z"] for g in j.layer_gcode]
check("four temperature changes, one per band boundary", len(zs) == 4 and all(g["kind"] == "temperature" for g in j.layer_gcode))
for k, g in enumerate(sorted(j.layer_gcode, key=lambda g: g["z"]), 2):
    zb = cal.band_bottom(k)
    check("change %d lands on the first layer of band %d (z %.1f)" % (k - 1, k, zb), abs(g["z"] - (zb + 0.2)) < 1e-6 and g["value"] == temps[k - 1], (g["z"], g["value"]))
gx = zipfile.ZipFile(out).read("Metadata/custom_gcode_per_layer.xml").decode()
check("custom_gcode_per_layer.xml has the four M104 lines", all(("M104 S%d" % t) in gx for t in temps[1:]) and "M104 S230" not in gx)
cfgtxt = zipfile.ZipFile(out).read("Metadata/project_settings.config").decode()
import json
cfg = json.loads(cfgtxt)
check("nozzle_temperature and first layer = band 1", cfg["nozzle_temperature"][0] == "230" and cfg["nozzle_temperature_initial_layer"][0] == "230")
ms = zipfile.ZipFile(out).read("Metadata/model_settings.config").decode()
check("model_settings: one normal part and five negative parts", ms.count('subtype="negative_part"') == 5 and ms.count('subtype="normal_part"') == 1)

# another layer height: 0.16 mm lands each change on the first layer above the band boundary
jl = load_job(job_from('shape = "calibration"\n', HEAD + '[settings]\nlayer_height = 0.16\ninitial_layer_print_height = 0.16\n'))
bad = [g for g in jl.layer_gcode if not (0 < g["z"] - cal.band_bottom(2 + jl.layer_gcode.index(g)) <= 0.16 + 1e-6)]
check("layer height 0.16: change on the first layer above each boundary", not bad, jl.layer_gcode)
package.build(jl, os.path.join(tmp, "cal016.3mf"))
check("layer height 0.16 builds", True)

# start / end / step job builds and validates strict
j3 = load_job(job_from('shape = "calibration"\nstart = 250\nend = 200\nstep = 25\n', name="se"))
s3 = package.build(j3, os.path.join(tmp, "se.3mf"))
check("start/end/step job: 3 bands, 2 changes", j3.calibration["temps"] == [250, 225, 200] and len(j3.layer_gcode) == 2)

for name, path in (("default", out), ("start/end/step", os.path.join(tmp, "se.3mf")), ("layer 0.16", os.path.join(tmp, "cal016.3mf"))):
    rep = validate.validate(path, mode="strict")
    check("%s: validates clean (strict)" % name, not rep.errors and not rep.warnings, [str(f) for f in (rep.errors + rep.warnings)][:3])

# deterministic
s_a = package.build(load_job(job_from('shape = "calibration"\n')), os.path.join(tmp, "a.3mf"))
s_b = package.build(load_job(job_from('shape = "calibration"\n')), os.path.join(tmp, "b.3mf"))
check("same job, same bytes", open(os.path.join(tmp, "a.3mf"), "rb").read() == open(os.path.join(tmp, "b.3mf"), "rb").read())

# a tower taller than the printer is refused with the printer volume in the message
e = errors('shape = "calibration"\nstart = 300\nend = 110\nstep = 10\n', HEAD)      # 20 bands = 161 mm: fits the P1S; use A1 mini limit below
check("20 bands still fit a P1S", e == "", e)

print("%d failed" % len(fails) if fails else "all ok")
sys.exit(1 if fails else 0)
