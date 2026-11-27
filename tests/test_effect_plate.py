#!/usr/bin/env python3
"""3D Effect Plate (Studio 2.9.2 and newer): builder, validator and patch mode.
Run: python3 -I tests/test_effect_plate.py"""
import json, os, sys, tempfile, zipfile
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import package, patch, patch_job, validate  # noqa: E402
from bambu3mf.job import load_job, JobError  # noqa: E402
from bambu3mf.patch_job import PatchRefused  # noqa: E402

fails = []
measured = 0
tmp = tempfile.mkdtemp()
EX = os.path.join(H, "..", "examples")
MESH = os.path.join(EX, "data", "cube20.stl")
OLD = ("2.8.2", "2.9.1")


def check(name, cond, extra=""):
    global measured
    measured += 1
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)[:500]) if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


def write(name, text):
    p = os.path.join(tmp, name)
    with open(p, "w") as f:
        f.write(text)
    return p


def job_text(studio, plate='plate_type = "3D Effect Plate"', fset=""):
    return '''studio = "%s"
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"
%s
[[filament]]
preset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"
color = "#FF0000"
%s
[[object]]
name = "Cube"
[[object.part]]
mesh = "%s"
filament = 1
''' % (studio, plate, fset, MESH)


def build(studio, plate="", fset=""):
    p = write("b%d.toml" % len(os.listdir(tmp)), job_text(studio, plate, fset))
    out = p[:-5] + ".3mf"
    package.build(load_job(p), out)
    return out


def cfg_of(path):
    return json.loads(zipfile.ZipFile(path).read("Metadata/project_settings.config"))


def patch_run(base, body):
    jp = os.path.join(tmp, "p%d.toml" % len(os.listdir(tmp)))
    out = jp[:-5] + ".3mf"
    with open(jp, "w") as f:
        f.write('patch = "%s"\noutput = "%s"\n%s' % (base, out, body))
    try:
        patch.run(patch_job.load_patch_job(jp))
        return out, None
    except PatchRefused as e:
        return None, "; ".join(e.problems) if hasattr(e, "problems") else str(e)


# ---- 1. the example builds on 2.9.2 --------------------------------------------------------------------------------------------
ex = os.path.join(EX, "effect_plate.toml")
out = os.path.join(tmp, "example.3mf")
package.build(load_job(ex), out)
cfg = cfg_of(out)
check("1 example: curr_bed_type is 3D Effect Plate", cfg.get("curr_bed_type") == "3D Effect Plate", cfg.get("curr_bed_type"))
check("1 example: effect_plate_temp is 60", cfg.get("effect_plate_temp") == ["60"], cfg.get("effect_plate_temp"))
check("1 example: effect_plate_temp_initial_layer is 60", cfg.get("effect_plate_temp_initial_layer") == ["60"], cfg.get("effect_plate_temp_initial_layer"))
diff = cfg.get("different_settings_to_system") or []
check("1 example: different_settings_to_system has 3 entries", len(diff) == 3, diff)
fil = (diff[1].split(";") if len(diff) > 1 else [])
check("1 example: effect_plate_temp is a changed filament setting", "effect_plate_temp" in fil, diff)
check("1 example: effect_plate_temp_initial_layer is a changed filament setting", "effect_plate_temp_initial_layer" in fil, diff)
r = validate.validate(out, mode="strict")
check("1 example: strict validation has 0 findings", r.ok and not r.findings, [(f.rule, f.msg[:80]) for f in r.findings])

# ---- 2. the real keys work, and the preset value is not marked as changed ---------------------------------------------------------
o2 = build("2.9.2", fset="[filament.settings]\neffect_plate_temp = 58\neffect_plate_temp_initial_layer = 59")
c2 = cfg_of(o2)
check("2 real keys: values 58 and 59 are written", c2.get("effect_plate_temp") == ["58"] and c2.get("effect_plate_temp_initial_layer") == ["59"], (c2.get("effect_plate_temp"), c2.get("effect_plate_temp_initial_layer")))
d2 = (c2.get("different_settings_to_system") or ["", "", ""])[1].split(";")
check("2 real keys: both are changed settings", "effect_plate_temp" in d2 and "effect_plate_temp_initial_layer" in d2, d2)
o2b = build("2.9.2")
d2b = (cfg_of(o2b).get("different_settings_to_system") or ["", "", ""])[1].split(";")
check("2 no bed setting: the preset value 55 is not a changed setting", cfg_of(o2b).get("effect_plate_temp") == ["55"] and "effect_plate_temp" not in d2b, d2b)

# ---- 3. the builder refuses the plate on older Studio versions -----------------------------------------------------------------------
for v in OLD:
    p = write("old%s.toml" % v, job_text(v))
    try:
        load_job(p)
        msg = None
    except JobError as e:
        msg = "; ".join(e.problems)
    check("3 builder %s: refused with 'needs Studio 2.9.2 or newer'" % v, bool(msg) and "needs Studio 2.9.2 or newer" in msg, msg)

# ---- 4. patch mode --------------------------------------------------------------------------------------------------------------------
for v in OLD:
    base = build(v)
    for label, body in (("top level", 'plate_type = "3D Effect Plate"\n'), ("[[plate]]", '[[plate]]\nid = 1\nplate_type = "3D Effect Plate"\n')):
        res, err = patch_run(base, body)
        check("4 patch on a %s file, %s: refused with 'needs Studio 2.9.2 or newer'" % (v, label), res is None and err and "needs Studio 2.9.2 or newer" in err, err)
base = build("2.9.2", plate='plate_type = "Cool Plate"')
check("4 base 2.9.2 file is on the Cool Plate", cfg_of(base).get("curr_bed_type") == "Cool Plate", cfg_of(base).get("curr_bed_type"))
res, err = patch_run(base, 'plate_type = "3D Effect Plate"\n')
check("4 patch on a 2.9.2 file, top level: works", res is not None, err)
if res:
    check("4 patched file: curr_bed_type is 3D Effect Plate", cfg_of(res).get("curr_bed_type") == "3D Effect Plate", cfg_of(res).get("curr_bed_type"))
    r = validate.validate(res, mode="strict")
    check("4 patched file: strict validation has 0 findings", r.ok and not r.findings, [(f.rule, f.msg[:80]) for f in r.findings])
res, err = patch_run(base, '[[plate]]\nid = 1\nplate_type = "3D Effect Plate"\n')
check("4 patch on a 2.9.2 file, [[plate]]: works", res is not None, err)
res, err = patch_run(build("2.9.2", plate='plate_type = "3D Effect Plate"'), 'plate_type = "3D Effect Plate"\n[[filament]]\nslot = 1\n[filament.settings]\neffect_plate_temp = 0\n')
check("4 patch on a 2.9.2 file: a filament with effect_plate_temp 0 is refused", res is None and err and "effect_plate_temp" in err, err)

print("\n%d checks measured" % measured)
if measured == 0:
    print("NOT CHECKED")
print("%d failed" % len(fails) if fails else "all passed")
sys.exit(1 if fails or measured == 0 else 0)
