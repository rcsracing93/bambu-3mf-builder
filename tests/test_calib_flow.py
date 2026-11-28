#!/usr/bin/env python3
"""[calibration] type = "flow": per-object print_flow_ratio in the object config, labels (one negative part per pad, closed shells),
strict validation of the built file on 2.8.2, 2.9.1 and 2.9.2, refused keys and values, the example job.
Run: python3 -I tests/test_calib_flow.py"""
import os, re, sys, tempfile, zipfile
H = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(H)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, H)
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import package, validate, calibjobs as cj  # noqa: E402
from bambu3mf.job import load_job, JobError  # noqa: E402
import calib_check_flow  # noqa: E402

fails = []
measured = 0


def check(name, cond, extra=""):
    global measured
    measured += 1
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)[:400]) if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


tmp = tempfile.mkdtemp()
n = [0]


def job_file(cal, studio="2.9.2"):
    n[0] += 1
    p = os.path.join(tmp, "j%d.toml" % n[0])
    with open(p, "w") as fh:
        fh.write('studio = "%s"\nprinter = "Bambu Lab P1S 0.4 nozzle"\nprocess = "0.20mm Standard @BBL X1C"\n'
                 '[[filament]]\npreset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"\ncolor = "#FF8000"\n[calibration]\ntype = "flow"\n%s\n' % (studio, cal))
    return p


def errors(cal, studio="2.9.2"):
    try:
        load_job(job_file(cal, studio))
    except JobError as e:
        return " | ".join(e.problems)
    return ""


def build(cal, studio="2.9.2"):
    j = load_job(job_file(cal, studio))
    out = os.path.join(tmp, "o%d.3mf" % n[0])
    package.build(j, out)
    return j, out


def object_blocks(path):
    ms = zipfile.ZipFile(path).read("Metadata/model_settings.config").decode()
    return re.findall(r"<object id=.*?</object>", ms, re.S), ms


for ver in ("2.8.2", "2.9.1", "2.9.2"):
    j, out = build('ratios = [0.90, 0.95, 1.00, 1.05, 1.10]', ver)
    blocks, ms = object_blocks(out)
    got = [float(re.search(r'key="print_flow_ratio" value="([^"]+)"', b).group(1)) for b in blocks]
    check("%s: 5 objects, each with its print_flow_ratio in model_settings.config" % ver, got == [0.9, 0.95, 1.0, 1.05, 1.1], got)
    names = [re.search(r'<metadata key="name" value="([^"]*)"', b).group(1) for b in blocks]
    check("%s: object names carry the ratio" % ver, names == ["Flow 0.90", "Flow 0.95", "Flow 1.00", "Flow 1.05", "Flow 1.10"], names)
    check("%s: one negative part (label) per pad, 5 in all" % ver, ms.count('subtype="negative_part"') == 5 and ms.count('subtype="normal_part"') == 5)
    check("%s: label names match the pads" % ver, all(('value="Label %s"' % s) in ms for s in ("0.90", "0.95", "1.00", "1.05", "1.10")))
    r = validate.validate(out, mode="strict")
    check("%s: built file validates strict (0 findings)" % ver, r.ok and not r.findings, [(f.rule, f.msg[:90]) for f in r.findings])
    check("%s: top_shell_layers 5 and no ironing in the process settings" % ver, j.settings.get("top_shell_layers") in (5, "5") and j.settings.get("ironing_type") == "no ironing", j.settings)

# the labels as meshes: closed shells, 0.5 mm deep, on the front strip of the top face, 1.0 mm floor
j = load_job(job_file('ratios = [0.95, 1.05]'))
labels = [p["mesh_obj"] for o in j.objects for p in o["parts"] if p["type"] == "negative_part"]
check("labels: 2 objects, 2 label meshes", len(j.objects) == 2 and len(labels) == 2)
check("labels: closed manifold shells", all(m.edge_report() == (0, 0, 0) for m in labels))
check("labels: 0.5 mm deep (bottom at z 1.1 on a 1.6 mm pad, 1.1 mm of material under it)", all(abs(m.bbox()[0][2] - 1.1) < 1e-9 for m in labels), [m.bbox() for m in labels])
check("labels: on the front strip, inside the pad", all(m.bbox()[0][1] >= -14.0 + 0.8 - 1e-9 and m.bbox()[1][1] <= -14.0 + 8.0 + 1e-9 for m in labels), [m.bbox() for m in labels])
check("placement: 15 mm from the front, out of the exclude area",
      not cj.check_placement([tuple(o["position"]) for o in j.objects], [(28.0, 28.0)] * 2, (0, 0, 256, 256), [(0, 0, 18, 28)]))
check("job info lists the ratios", j.calib_job["info"]["ratios"] == ["0.95", "1.05"], j.calib_job)
j3 = load_job(job_file('ratios = [0.975, 1.025]'))
def ratios_of(job):
    return [float(o["settings"]["print_flow_ratio"]) for o in job.objects]


check("three decimals: 0.975 label and ratio", ratios_of(j3) == [0.975, 1.025] and j3.objects[0]["name"] == "Flow 0.975")
jd = load_job(job_file(''))
check("default ratios: 0.90 to 1.10 in 0.05 steps", ratios_of(jd) == [0.9, 0.95, 1.0, 1.05, 1.1])
jm = load_job(job_file('ratios = [1.0, 0.9, 1.1]'))
check("the order of ratios is kept", ratios_of(jm) == [1.0, 0.9, 1.1])

# refused
check("refused: unknown key lists ratios", "unknown key" in errors('count = 5') and "ratios" in errors('count = 5'), errors('count = 5'))
check("refused: ratio above 1.5", "outside 0.5 to 1.5" in errors('ratios = [1.0, 1.6]'), errors('ratios = [1.0, 1.6]'))
check("refused: ratio below 0.5", "outside 0.5 to 1.5" in errors('ratios = [0.4, 1.0]'), errors('ratios = [0.4, 1.0]'))
check("refused: one value only", "2 to 9" in errors('ratios = [1.0]'), errors('ratios = [1.0]'))
check("refused: ten values", "2 to 9" in errors('ratios = [0.9,0.92,0.94,0.96,0.98,1.0,1.02,1.04,1.06,1.08]'))
check("refused: not a list", "list of" in errors('ratios = 1.0'), errors('ratios = 1.0'))
check("refused: a string in the list", "list of" in errors('ratios = [1.0, "a"]'), errors('ratios = [1.0, "a"]'))
check("refused: four decimals", "3 decimals" in errors('ratios = [1.0, 1.0125]'), errors('ratios = [1.0, 1.0125]'))
check("refused: repeated value", "more than once" in errors('ratios = [1.0, 1.0, 1.1]'), errors('ratios = [1.0, 1.0, 1.1]'))

# the example job
ex = os.path.join(ROOT, "examples", "calib_flow.toml")
jx = load_job(ex)
out = os.path.join(tmp, "example.3mf")
package.build(jx, out)
r = validate.validate(out, mode="strict")
check("example calib_flow.toml: 5 objects, strict 0 findings", len(jx.objects) == 5 and r.ok and not r.findings, [(f.rule, f.msg[:90]) for f in r.findings])
cc = calib_check_flow.checks({"calibration": {"ratios": [0.9, 0.95, 1.0, 1.05, 1.1]}}, out, [])
check("G-code check module: object ratios pass, a missing G-code is a failure, not a pass", cc[0][1] and not cc[-1][1] and "NOT CHECKED" in cc[-1][0], cc)

print("\n%d checks measured" % measured)
if measured == 0:
    print("NOT CHECKED")
print("%d failed" % len(fails) if fails else "all passed")
sys.exit(1 if fails or measured == 0 else 0)
