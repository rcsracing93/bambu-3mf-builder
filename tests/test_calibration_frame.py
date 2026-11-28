#!/usr/bin/env python3
"""The [calibration] frame (scripts/bambu3mf/calibjobs.py), with a fake type so it does not depend on the real ones:
labels (closed shells, 0.5 mm deep, fit and floor rules, characters), placement (front margin, bed_exclude_area, rows), the job
rules (one filament, no [[object]], no [temperature_tower], unknown type and key), and a full build of the fake type that validates
strict with its object setting, height range and layer G-code in the file.
Run: python3 -I tests/test_calibration_frame.py"""
import json, os, re, sys, tempfile, types, zipfile
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import calibjobs as cj, package, validate  # noqa: E402
from bambu3mf.calibration import CalibrationError, _Shells  # noqa: E402
from bambu3mf.job import load_job, JobError  # noqa: E402

fails = []
measured = 0


def check(name, cond, extra=""):
    global measured
    measured += 1
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)[:400]) if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


def raises(fn, text):
    try:
        fn()
    except CalibrationError as e:
        return text in str(e), str(e)
    return False, "no error"


# ---- labels ----------------------------------------------------------------------------------------
face = (0.0, 20.0, 0.0, 8.0, 3.0, 3.0)                     # 20 x 8 mm top face at z 3, 3 mm thick
m = cj.label_part("0.95", face)
lo, hi = m.bbox()
check("label: closed manifold shells (edge report 0, 0, 0)", m.edge_report() == (0, 0, 0), m.edge_report())
check("label: cut is 0.5 mm deep (bottom at z 2.5)", abs(lo[2] - 2.5) < 1e-9, lo)
check("label: open at the top (reaches above the face)", hi[2] > 3.0)
check("label: inside the face with the margin", lo[0] >= 0.8 - 1e-9 and hi[0] <= 19.2 + 1e-9 and lo[1] >= 0.8 - 1e-9 and hi[1] <= 7.2 + 1e-9, (lo, hi))
check("label: 4 characters -> digits 0, 9, 5 and a point (segments a..g 6+6+5, point 1 = 18 boxes)", m.n_triangles == 18 * 12, m.n_triangles)
ok, msg = raises(lambda: cj.label_part("1234567", face), "does not fit")             # 21.2 mm on a 20 mm face
check("label: too wide for the face is refused", ok, msg)
ok, msg = raises(lambda: cj.label_part("1", (0, 10, 0, 8, 1.2, 1.2)), "must leave 1.0 mm")
check("label: a face with less than 1.5 mm of material is refused (1.0 mm floor under a 0.5 mm cut)", ok, msg)
ok, msg = raises(lambda: cj.label_part("A1", face), "only digits")
check("label: letters are refused", ok, msg)
check("label: minus sign works", cj.label_part("-5", face).edge_report() == (0, 0, 0))

# ---- placement ---------------------------------------------------------------------------------------
bed, ex = cj.free_area({"printable_area": ["0x0", "256x0", "256x256", "0x256"], "bed_exclude_area": ["0x0", "18x0", "18x28", "0x28"]})
check("placement: bed and exclude area read from the machine preset", bed == (0, 0, 256, 256) and ex == [(0, 0, 18, 28)], (bed, ex))
sizes = [(20.0, 20.0)] * 12
c = cj.place(sizes, bed, ex)
check("placement: 12 objects, none in the exclude area, none closer than 15 mm to the front", not cj.check_placement(c, sizes, bed, ex), cj.check_placement(c, sizes, bed, ex))
check("placement: the first object starts right of the exclude area (x >= 18 + 5)", c[0][0] - 10 >= 23 - 1e-9, c[0])
check("placement: the objects wrap into a second row", len({round(y, 3) for _x, y in c}) >= 2, c)
check("placement: check_placement finds an object at y 5", cj.check_placement([(100, 10)], [(10, 10)], bed, ex) != [])
check("placement: check_placement finds an object in the exclude area", any("exclude" in p for p in cj.check_placement([(25, 40)], [(30, 10)], bed, [(0, 0, 18, 28)] + [(0, 30, 18, 50)])))
ok, msg = raises(lambda: cj.place([(200.0, 200.0)] * 3, bed, ex), "do not fit")
check("placement: too many objects is an error", ok, msg)

# ---- the job, with a fake type ------------------------------------------------------------------------
def fake_build(ctx):
    n = ctx.number("count", 2, lo=1, hi=6, whole=True)
    if n is None:
        return
    for i in range(n):
        sh = _Shells("Pad %d" % (i + 1))
        sh.box(-10.0, 10.0, -5.0, 5.0, 0.0, 3.0)
        lab = cj.label_part(str(i + 1), (-10.0, 10.0, -5.0, 5.0, 3.0, 3.0))
        ctx.add_object("Pad %d" % (i + 1), sh.mesh.finish(), [lab], settings={"wall_loops": 3 + i},
                       height_ranges=[{"min_z": 1.0, "max_z": 2.0, "layer_height": 0.12, "settings": {"outer_wall_speed": 50}}])
    ctx.add_layer_gcode(1.5, "; fake calibration mark")


FAKE = types.SimpleNamespace(KEYS={"count"}, build=fake_build)
real_type_module = cj.type_module
cj.type_module = lambda t: FAKE if t == "flow" else real_type_module(t)
tmp = tempfile.mkdtemp()


def job_text(cal, extra="", nfil=1, studio="2.9.2"):
    p = os.path.join(tmp, "j%d.toml" % len(os.listdir(tmp)))
    fil = "".join('[[filament]]\npreset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"\ncolor = "#FF0000"\n' for _ in range(nfil))
    with open(p, "w") as fh:
        fh.write('studio = "%s"\nprinter = "Bambu Lab P1S 0.4 nozzle"\nprocess = "0.20mm Standard @BBL X1C"\n%s\n%s[calibration]\n%s\n' % (studio, extra, fil, cal))
    return p


def load(p):
    try:
        return load_job(p), None
    except JobError as e:
        return None, "; ".join(e.problems)


for ver in ("2.8.2", "2.9.1", "2.9.2"):
    j, err = load(job_text('type = "flow"\ncount = 3', studio=ver))
    check("fake type %s: loads, 3 objects" % ver, j is not None and len(j.objects) == 3 and j.calib_job["type"] == "flow", err)
    if j is None:
        continue
    out = os.path.join(tmp, "fake_%s.3mf" % ver)
    package.build(j, out)
    r = validate.validate(out, mode="strict")
    check("fake type %s: built file validates strict (0 findings)" % ver, r.ok and not r.findings, [(f.rule, f.msg[:90]) for f in r.findings])
    z = zipfile.ZipFile(out)
    ms = z.read("Metadata/model_settings.config").decode()
    check("fake type %s: per-object wall_loops 3, 4, 5 in the object config" % ver,
          re.findall(r'<metadata key="wall_loops" value="(\d+)"/>', ms) == ["3", "4", "5"], re.findall(r'wall_loops" value="(\d+)"', ms))
    check("fake type %s: the labels are negative parts, with a void under each (3 + 3)" % ver, ms.count('subtype="negative_part"') == 6, ms.count('subtype="negative_part"'))
    check("fake type %s: height ranges written (3 objects)" % ver, z.read("Metadata/layer_config_ranges.xml").decode().count("<object ") == 3)
    gc = z.read("Metadata/custom_gcode_per_layer.xml").decode()
    check("fake type %s: layer G-code at the layer top (1.6 mm for 0.2 mm layers)" % ver, "fake calibration mark" in gc and 'top_z="1.6' in gc, gc[:300])
    pos = [tuple(float(x) for x in t.split()[9:11]) for t in re.findall(r'<item objectid="\d+"[^>]* transform="([^"]+)"', z.read("3D/3dmodel.model").decode())]
    probs = cj.check_placement(pos, [(20.0, 10.0)] * len(pos), bed, ex)
    check("fake type %s: placed objects keep 15 mm from the front and stay out of X 0-18 / Y 0-28 (%d objects)" % (ver, len(pos)), len(pos) == 3 and not probs, (pos, probs))

j, err = load(job_text('type = "flow"\ncount = 2', extra='[[object]]\nname = "x"\n[[object.part]]\nmesh = "%s"\n' % os.path.join(H, "..", "examples", "data", "cube20.stl")))
check("job: [[object]] with [calibration] is refused", j is None and "makes its own objects" in (err or ""), err)
j, err = load(job_text('type = "flow"', nfil=2))
check("job: two filaments refused", j is None and "one filament" in (err or ""), err)
j, err = load(job_text('type = "flow"', extra='[temperature_tower]\nshape = "calibration"'))
check("job: [calibration] with [temperature_tower] refused", j is None and "cannot be in one job" in (err or ""), err)
j, err = load(job_text('type = "speed"'))
check("job: unknown type refused, names the types", j is None and '"flow", "pa", "mvs", "tolerance"' in (err or ""), err)
j, err = load(job_text('type = "flow"\ncolour = 1'))
check("job: unknown key refused, lists the keys", j is None and "unknown key" in (err or "") and "count" in (err or ""), err)
j, err = load(job_text('type = "flow"\ncount = 9'))
check("job: a number out of range is refused", j is None and "outside 1 to 6" in (err or ""), err)
cj.type_module = real_type_module
for t in cj.TYPES:
    j, err = load(job_text('type = "%s"' % t))
    if j is None and "not built yet" in (err or ""):
        check("job: type %s is a stub (not built yet)" % t, True)
    else:
        check("job: type %s is built (loads or gives its own errors)" % t, j is not None or bool(err), err)

print("\n%d checks measured" % measured)
if measured == 0:
    print("NOT CHECKED")
print("%d failed" % len(fails) if fails else "all passed")
sys.exit(1 if fails or measured == 0 else 0)
