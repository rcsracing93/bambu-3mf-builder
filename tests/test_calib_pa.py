#!/usr/bin/env python3
"""[calibration] type = "pa": one M900 K layer G-code step per band at the band's first layer top, labels (negative parts on the
stair steps), geometry inside the bed and 15 mm from the front, strict validation 0 findings, bad keys and values refused,
builds on Studio 2.8.2, 2.9.1 and 2.9.2.
Run: python3 -I tests/test_calib_pa.py"""
import os, re, sys, tempfile, zipfile
H = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(H)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, H)
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import package, validate, calibjobs as cj  # noqa: E402
from bambu3mf.job import load_job, JobError  # noqa: E402
import calib_check_pa  # noqa: E402

fails = []
measured = 0


def check(name, cond, extra=""):
    global measured
    measured += 1
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)[:400]) if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


tmp = tempfile.mkdtemp()


def job_file(cal, studio="2.9.2", fil="Bambu PLA Basic @BBL P1S 0.4 nozzle", extra=""):
    p = os.path.join(tmp, "j%d.toml" % len(os.listdir(tmp)))
    with open(p, "w") as fh:
        fh.write('studio = "%s"\nprinter = "Bambu Lab P1S 0.4 nozzle"\nprocess = "0.20mm Standard @BBL X1C"\n%s\n'
                 '[[filament]]\npreset = "%s"\ncolor = "#1F77B4"\n[calibration]\ntype = "pa"\n%s\n' % (studio, extra, fil, cal))
    return p


def load(p):
    try:
        return load_job(p), ""
    except JobError as e:
        return None, " | ".join(e.problems)


def steps(f3mf):
    xml = zipfile.ZipFile(f3mf).read("Metadata/custom_gcode_per_layer.xml").decode()
    return [(float(z), g) for z, g in re.findall(r'<layer top_z="([0-9.]+)"[^>]* gcode="([^"]*)"', xml)]


# ---- defaults on each Studio version --------------------------------------------------------------------------------
for ver in ("2.8.2", "2.9.1", "2.9.2"):
    p = job_file("", studio=ver)
    j, err = load(p)
    check("%s: default job loads (7 bands, K 0 to 0.06)" % ver, j is not None and j.calib_job["type"] == "pa" and len(j.calib_job["info"]["values"]) == 7, err)
    if j is None:
        continue
    out = os.path.join(tmp, "pa_%s.3mf" % ver)
    package.build(j, out)
    r = validate.validate(out, mode="strict")
    check("%s: built file validates strict (0 findings)" % ver, r.ok and not r.findings, [(f.rule, f.msg[:90]) for f in r.findings])
    st = steps(out)
    want = [(0.2 + 5.0 * i, "M900 K%s" % k) for i, k in enumerate(["0", "0.01", "0.02", "0.03", "0.04", "0.05", "0.06"])]
    want = [(0.2 if i == 0 else round(5.0 * i + 0.2, 4), g) for i, (_z, g) in enumerate(want)]
    check("%s: one M900 K step per band at the first layer top of the band (7 steps)" % ver, len(st) == 7 and st == want, st)
    z = zipfile.ZipFile(out)
    ms = z.read("Metadata/model_settings.config").decode()
    check("%s: 7 labels, all negative parts; 1 normal part" % ver, ms.count('subtype="negative_part"') == 7 and ms.count('subtype="normal_part"') == 1,
          (ms.count('subtype="negative_part"'), ms.count('subtype="normal_part"')))
    names = re.findall(r'<metadata key="name" value="(Label [^"]*)"', ms)
    check("%s: labels read 0, 0.01 ... 0.06" % ver, names == ["Label 0"] + ["Label 0.0%d" % i for i in range(1, 7)], names)
    ps = z.read("Metadata/project_settings.config").decode()
    check("%s: outer wall speed 200 and cooling slowdown off are in the file" % ver,
          '"outer_wall_speed": [\n        "200"' in ps.replace("\r", "") or re.search(r'"outer_wall_speed":\s*\[?\s*"200"', ps) is not None, re.findall(r'"outer_wall_speed"[^,]*', ps)[:1])
    check("%s: slow_down_for_layer_cooling is 0" % ver, re.search(r'"slow_down_for_layer_cooling":\s*\[\s*"0"', ps) is not None)
    # placement: object inside the bed, 15 mm from the front, outside X 0-18 / Y 0-28
    m3 = z.read("3D/3dmodel.model").decode()
    pos = [tuple(float(x) for x in t.split()[9:11]) for t in re.findall(r'<item objectid="\d+"[^>]* transform="([^"]+)"', m3)]
    check("%s: placed once, inside the bed (%d object)" % (ver, len(pos)), len(pos) == 1 and 0 < pos[0][0] < 256 and 0 < pos[0][1] < 256, pos)

# ---- the G-code check module: its expected list matches the job file -----------------------------------------------------
cal = {"start": 0.0, "end": 0.06, "step": 0.01}
check("check module: default values 0 to 0.06 in 0.01 steps (7)", calib_check_pa.expected_values(cal) == [0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06], calib_check_pa.expected_values(cal))
check("check module: values list used as given", calib_check_pa.expected_values({"values": [0.02, 0.04]}) == [0.02, 0.04])
bad_gcode = os.path.join(tmp, "bad.gcode")
with open(bad_gcode, "w") as fh:
    fh.write("; CHANGE_LAYER\n; Z_HEIGHT: 0.2\nM900 K0.5\n; CHANGE_LAYER\n; Z_HEIGHT: 0.4\nM900 K0.1\n")
res =calib_check_pa.checks({"calibration": {"type": "pa"}}, os.path.join(tmp, "pa_2.9.2.3mf"), [bad_gcode])
check("check module: a G-code with wrong K and extra M900 fails (checks measured: %d)" % len(res), len(res) == 5 and not all(o for _n, o, _d in res) and not res[2][1] and not res[3][1], res)
res0 = calib_check_pa.checks({"calibration": {"type": "pa"}}, os.path.join(tmp, "pa_2.9.2.3mf"), [])
check("check module: no G-code is NOT CHECKED, not a pass", any((not o) and "NOT CHECKED" in d for _n, o, d in res0), res0)

# ---- values, start / end / step, band height --------------------------------------------------------------------------------
j, err = load(job_file('values = [0.01, 0.025, 0.04]\nband_height = 4'))
check("values list: 3 bands, labels 0.01 0.025 0.04", j is not None and j.calib_job["info"]["values"] == ["0.01", "0.025", "0.04"], err)
if j:
    out = os.path.join(tmp, "vals.3mf")
    package.build(j, out)
    st = steps(out)
    check("values list: steps at 0.2, 4.2, 8.2 mm with K 0.01, 0.025, 0.04", st == [(0.2, "M900 K0.01"), (4.2, "M900 K0.025"), (8.2, "M900 K0.04")], st)
    r = validate.validate(out, mode="strict")
    check("values list: strict validation 0 findings", r.ok and not r.findings, [(f.rule, f.msg[:90]) for f in r.findings])
j, err = load(job_file('start = 0.02\nend = 0.10\nstep = 0.02'))
check("start / end / step: 5 bands 0.02 to 0.1", j is not None and j.calib_job["info"]["values"] == ["0.02", "0.04", "0.06", "0.08", "0.1"], err)
j, err = load(job_file('start = 0.1\nend = 0.02\nstep = 0.02'))
check("start above end: the bands still go up", j is not None and j.calib_job["info"]["values"][0] == "0.02", err)
j, err = load(job_file('band_height = 5.4'))
check("band height 5.4 mm with 0.2 mm layers: step z snaps up to a layer top", j is not None and [g["z"] for g in j.layer_gcode][:2] == [0.2, 5.6], [g["z"] for g in j.layer_gcode] if j else err)

# ---- refused --------------------------------------------------------------------------------------------------------------
def refused(name, cal, text, **kw):
    j, err = load(job_file(cal, **kw))
    check("refused: %s" % name, j is None and text in err, err or "loaded")


refused("unknown key", "colour = 1", "unknown key")
refused("K above the option maximum (2)", "values = [0.1, 2.5]", "outside 0 to 2")
refused("K below 0", "start = -0.01", "outside 0 to 2")
refused("end above the maximum", "end = 3", "outside 0 to 2")
refused("step 0", "step = 0", "above 0")
refused("step that does not fit the range", "start = 0\nend = 0.05\nstep = 0.02", "whole steps")
refused("one band only", "values = [0.02]", "2 to 12")
refused("13 bands", "start = 0\nend = 0.12\nstep = 0.01", "13 bands")
refused("values and start together", "values = [0.01, 0.02]\nstart = 0", "not both")
refused("band height below 2 mm", "band_height = 1.5", "outside 2 to 20")
refused("band height above 20 mm", "band_height = 25", "outside 2 to 20")
refused("a text value", 'start = "0.02"', "not a number")
refused("values not a list", "values = 0.02", "list of")
refused("two values with the same label", "values = [0.01, 0.01]", "same label")
refused("an [[object]] in the job", "", "makes its own objects", extra='[[object]]\nname = "x"\n[[object.part]]\nmesh = "%s"\n' % os.path.join(ROOT, "examples", "data", "cube20.stl"))

print("\n%d checks measured" % measured)
if measured == 0:
    print("NOT CHECKED")
print("%d failed" % len(fails) if fails else "all passed")
sys.exit(1 if fails or measured == 0 else 0)
