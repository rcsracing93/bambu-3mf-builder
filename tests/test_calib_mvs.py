#!/usr/bin/env python3
"""[calibration] type = "mvs" (scripts/bambu3mf/calib_mvs.py): one height range per band with the speed that gives the flow, labels on
flat top faces, the limiters set, strict validation, bad keys refused, builds on 2.8.2 / 2.9.1 / 2.9.2.
Run: python3 -I tests/test_calib_mvs.py"""
import math, os, re, sys, tempfile, zipfile
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import package, validate, calibjobs as cj  # noqa: E402
from bambu3mf.job import load_job, JobError  # noqa: E402

fails = []
measured = 0


def check(name, cond, extra=""):
    global measured
    measured += 1
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)[:400]) if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


tmp = tempfile.mkdtemp()


def job_text(cal, studio="2.9.2", settings=""):
    p = os.path.join(tmp, "j%d.toml" % len(os.listdir(tmp)))
    with open(p, "w") as fh:
        fh.write('studio = "%s"\nprinter = "Bambu Lab P1S 0.4 nozzle"\nprocess = "0.20mm Standard @BBL X1C"\n%s\n'
                 '[[filament]]\npreset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"\ncolor = "#2060C0"\n[calibration]\ntype = "mvs"\n%s\n' % (studio, settings, cal))
    return p


def load(p):
    try:
        return load_job(p), None
    except JobError as e:
        return None, "; ".join(e.problems)


AREA = 0.2 * (0.42 - 0.2 * (1 - math.pi / 4))     # Flow::mm3_per_mm, 0.42 mm line, 0.2 mm layer: 0.075429 mm2

# ---- default job, all three versions ----------------------------------------------------------------------
for ver in ("2.8.2", "2.9.1", "2.9.2"):
    j, err = load(job_text("", studio=ver))
    check("%s: default job loads" % ver, j is not None, err)
    if j is None:
        continue
    out = os.path.join(tmp, "mvs_%s.3mf" % ver)
    package.build(j, out)
    r = validate.validate(out, mode="strict")
    check("%s: strict validation, 0 findings" % ver, r.ok and not r.findings, [(f.rule, f.msg[:90]) for f in r.findings])
    z = zipfile.ZipFile(out)
    rng = z.read("Metadata/layer_config_ranges.xml").decode()
    bands = re.findall(r'<range min_z="([0-9.]+)" max_z="([0-9.]+)">(.*?)</range>', rng, re.S)
    check("%s: 11 bands, one height range each (10 to 30 mm3/s in steps of 2)" % ver, len(bands) == 11, len(bands))
    sp = [float(re.search(r'opt_key="outer_wall_speed">([0-9.]+)<', b[2]).group(1)) for b in bands]
    want = [round((10 + 2 * i) / AREA, 2) for i in range(11)]
    check("%s: outer_wall_speed per band = flow / (0.42 x 0.2 line area), %d bands measured" % (ver, len(sp)), sp == want, (sp, want))
    check("%s: bands are 5 mm, meet, and carry the 0.2 mm layer height" % ver,
          [(float(b[0]), float(b[1])) for b in bands] == [(5.0 * i, 5.0 * (i + 1)) for i in range(11)] and all('layer_height">0.2<' in b[2] for b in bands))
    ms = z.read("Metadata/model_settings.config").decode()
    names = re.findall(r'<metadata key="name" value="(Flow [0-9.]+)"/>', ms)
    check("%s: one label per band, text = the flow (Flow 10 ... Flow 30), %d measured" % (ver, len(names)),
          names == ["Flow %d" % (10 + 2 * i) for i in range(11)], names)
    check("%s: the labels are negative parts" % ver, ms.count('subtype="negative_part"') == 11, ms.count('subtype="negative_part"'))
    ps = z.read("Metadata/project_settings.config").decode()
    fmax = re.search(r'"filament_max_volumetric_speed": \[\s*"([0-9.]+)"', ps)
    check("%s: filament_max_volumetric_speed above the top band (30)" % ver, fmax is not None and float(fmax.group(1)) > 30, fmax and fmax.group(1))
    check("%s: layer-time slowdown and overhang speed off" % ver,
          re.search(r'"slow_down_for_layer_cooling": \[\s*"0"', ps) is not None and re.search(r'"enable_overhang_speed": \[\s*"0"', ps) is not None)

# ---- geometry: labels on flat top faces, a 1.0 mm floor ---------------------------------------------------------
j, _ = load(job_text(""))
ent = j.objects[0]
body = ent["parts"][0]["mesh_obj"]
labs = [p["mesh_obj"] for p in ent["parts"][1:]]
tops = sorted(round(m.bbox()[1][2] - 0.3, 3) for m in labs)           # the cut reaches 0.3 mm above its face
check("labels sit on the tab tops at 5, 10 ... 55 mm (11 measured)", tops == [5.0 * (i + 1) for i in range(11)], tops)
check("every label cut is 0.5 mm deep", all(abs(m.bbox()[1][2] - m.bbox()[0][2] - 0.8) < 1e-6 for m in labs))
check("every label shell is closed (edge report 0, 0, 0)", all(m.edge_report() == (0, 0, 0) for m in labs) and body.edge_report() == (0, 0, 0))
lo, hi = body.bbox()
check("the body is 55 mm high and sits on the bed", abs(hi[2] - 55.0) < 1e-9 and abs(lo[2]) < 1e-9, (lo, hi))
check("the tube bbox is centered on 0, 0 (placement centers it)", abs((lo[0] + hi[0]) / 2) < 1e-6 and abs((lo[1] + hi[1]) / 2) < 1e-6, (lo, hi))
check("every label sits at least 1.0 mm above the tab bottom (floor)", all(m.bbox()[0][2] >= 1.0 for m in labs))
check("the info for the summary lists 11 speeds", len(j.calib_job["info"]["outer_wall_speeds_mm_per_s"]) == 11)

# ---- other keys ------------------------------------------------------------------------------------------------------
j, err = load(job_text("flows = [8, 12.5, 20]\nband_height = 4"))
check("flows list: 3 bands of 4 mm", j is not None and len(j.objects[0]["height_ranges"]) == 3 and j.objects[0]["height_ranges"][2]["min_z"] == 8.0, err)
if j:
    check("flows list: label 12.5 is made", any(p["name"] == "Flow 12.5" for p in j.objects[0]["parts"]))
    check("flows list: filament_max_volumetric_speed is 22 (top 20 + 2)", j.filaments[0]["settings"].get("filament_max_volumetric_speed") in (22, 22.0, "22"), j.filaments[0]["settings"])
j, err = load(job_text("start = 10\nend = 20\nstep = 5"))
check("start / end / step: 3 bands (10, 15, 20)", j is not None and len(j.objects[0]["height_ranges"]) == 3, err)

# ---- refused ------------------------------------------------------------------------------------------------------
for cal, text in (
        ("colour = 1", "unknown key"),
        ("flows = [10, 20]\nstart = 5", "not both"),
        ("start = 30\nend = 10", "larger than start"),
        ("flows = [20, 10]", "increase"),
        ("band_height = 1", "outside"),
        ("band_height = 5.1", "whole number"),
        ("start = 10\nend = 30\nstep = 0.1", "bands"),
        ("flows = [10.123, 12]", "decimals"),
        ("flows = [10, 90]", "axis limit"),
        ("start = \"a\"", "not a number"),
        ("start = true", "not a number")):
    j, err = load(job_text(cal))
    check("refused: %s" % cal.replace("\n", "; "), j is None and text in (err or ""), err)
j, err = load(job_text("band_height = 4.8", settings='[settings]\nlayer_height = 0.16'))
check("refused: layer height 0.16 and initial layer 0.2 do not meet the bands", j is None and "first layer" in (err or ""), err)

print("%d checks measured" % measured)
if measured == 0:
    print("NOT CHECKED")
    sys.exit(1)
print("FAILED: %s" % fails if fails else "all passed")
sys.exit(1 if fails else 0)
