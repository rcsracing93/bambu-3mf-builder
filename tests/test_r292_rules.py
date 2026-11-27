#!/usr/bin/env python3
"""Studio 2.9.2.62 rules (1.3.0 phase 2). Each rule is checked on 2.9.2 and on the older versions, in the builder (job) and in the
validator (a built file with one value changed). From the 2.9.1.70 -> 2.9.2.62 source diff:
  - nozzle_temperature / nozzle_temperature_initial_layer: minimum 0 -> 165 (PrintConfig.cpp), 2.9.2 only
  - curr_bed_type: new enum value "3D Effect Plate" with effect_plate_temp(_initial_layer), 2.9.2 only
  - Engineering Plate: the P1S PLA Basic / Matte / Silk+ presets get eng_plate_temp 55 (was 0, so the plate was refused)
  - cross_part_color_penetration: a part setting, not in a height range (Tab.cpp TabPrintLayer), not on a modifier
  - seam_embedded_headroom: an object setting (PrintObjectConfig); P1S system processes set 10
Run: python3 -I tests/test_r292_rules.py [--report FILE]"""
import json, os, sys, tempfile, zipfile
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import package, validate  # noqa: E402
from bambu3mf.job import load_job, JobError  # noqa: E402

fails = []
measured = 0


LOG = []
REPORT = sys.argv[sys.argv.index("--report") + 1] if "--report" in sys.argv else None


def say(text):
    print(text)
    LOG.append(text)


def check(name, cond, extra=""):
    global measured
    measured += 1
    say(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)[:500]) if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


tmp = tempfile.mkdtemp()
MESH = os.path.join(H, "..", "examples", "data", "cube20.stl")
SLAB = os.path.join(H, "..", "examples", "data", "slab1.stl")
OLD = ("2.8.2", "2.9.1")
PLA_BASIC = "Bambu PLA Basic @BBL P1S 0.4 nozzle"


def job(studio, filament=PLA_BASIC, top="", fset="", obj="", part="", extra_part=""):
    p = os.path.join(tmp, "j%d.toml" % len(os.listdir(tmp)))
    with open(p, "w") as f:
        f.write('''studio = "%s"
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"
%s
[[filament]]
preset = "%s"
color = "#FF0000"
%s
[[object]]
name = "Cube"
%s
[[object.part]]
mesh = "%s"
filament = 1
%s
%s
''' % (studio, top, filament, fset, obj, MESH, part, extra_part))
    return p


def try_load(path):
    try:
        return load_job(path), None
    except JobError as e:
        return None, "; ".join(e.problems)


def built(path):
    out = os.path.join(tmp, os.path.basename(path)[:-5] + ".3mf")
    package.build(load_job(path), out)
    return out


def restamp(src, fn):
    z = zipfile.ZipFile(src)
    out = src[:-4] + "_m.3mf"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zo:
        for i in z.infolist():
            d = z.read(i.filename)
            if i.filename == "Metadata/project_settings.config":
                c = json.loads(d)
                fn(c)
                d = json.dumps(c, indent=4).encode()
            zo.writestr(i, d)
    return out


def findings(path, key):
    return [f for f in validate.validate(path).findings if key in f.msg]


# ---- 1. nozzle temperature minimum 165 -----------------------------------------------------------------------------------------
for k_job, k in (("nozzle_temp", "nozzle_temperature"), ("nozzle_temp_first_layer", "nozzle_temperature_initial_layer")):
    j, err = try_load(job("2.9.2", fset="[filament.settings]\n%s = 160" % k_job))
    check("1 job 2.9.2: %s = 160 is refused (minimum 165)" % k, j is None and err and k in err and "165" in err, err)
    j, err = try_load(job("2.9.2", fset="[filament.settings]\n%s = 165" % k_job))
    check("1 job 2.9.2: %s = 165 builds" % k, j is not None, err)
    for v in OLD:
        j, err = try_load(job(v, fset="[filament.settings]\n%s = 160" % k_job))
        check("1 job %s: %s = 160 builds (minimum 0)" % (v, k), j is not None, err)
for v, want_err in (("2.9.2", True), ("2.9.1", False)):
    f = restamp(built(job(v)), lambda c: c.__setitem__("nozzle_temperature", ["160"]))
    fs = [x for x in findings(f, "nozzle_temperature") if x.level == "error"]
    check("1 validator %s: nozzle_temperature 160 in the file is %s" % (v, "an error" if want_err else "not an error"), bool(fs) == want_err, [x.msg for x in fs])

# ---- 2. 3D Effect Plate --------------------------------------------------------------------------------------------------------
j, err = try_load(job("2.9.2", top='plate_type = "3D Effect Plate"'))
check("2 job 2.9.2: plate_type 3D Effect Plate builds", j is not None and j.plate_type == "3D Effect Plate", err)
if j:
    out = os.path.join(tmp, "effect.3mf"); package.build(j, out)
    cfg = json.loads(zipfile.ZipFile(out).read("Metadata/project_settings.config"))
    check("2 2.9.2 file: curr_bed_type is 3D Effect Plate", cfg.get("curr_bed_type") == "3D Effect Plate", cfg.get("curr_bed_type"))
    check("2 2.9.2 file: effect_plate_temp of PLA Basic is 55", cfg.get("effect_plate_temp") == ["55"], cfg.get("effect_plate_temp"))
    r = validate.validate(out, mode="strict")
    check("2 2.9.2 file: strict validation passes", r.ok and not r.findings, [(f.rule, f.msg[:80]) for f in r.findings])
for v in OLD:
    j, err = try_load(job(v, top='plate_type = "3D Effect Plate"'))
    check("2 job %s: 3D Effect Plate refused with a clear message" % v, j is None and err and "needs Studio 2.9.2 or newer" in err, err)
j, err = try_load(job("2.9.2", top='plate_type = "3D Effect Plate"', fset="[filament.settings]\nbed_temp = 60"))
check("2 job 2.9.2: bed_temp alias on 3D Effect Plate sets effect_plate_temp", j is not None and j.filaments[0]["settings"].get("effect_plate_temp") is not None, (err, j and j.filaments[0]["settings"]))
f = restamp(built(job("2.9.1")), lambda c: c.__setitem__("curr_bed_type", "3D Effect Plate"))
check("2 validator 2.9.1: curr_bed_type 3D Effect Plate is an error", any(x.level == "error" for x in findings(f, "3D Effect Plate")), [x.msg for x in findings(f, "3D Effect Plate")])
f = restamp(built(job("2.9.2")), lambda c: c.__setitem__("curr_bed_type", "3D Effect Plate"))
check("2 validator 2.9.2: curr_bed_type 3D Effect Plate is valid", not any(x.level == "error" for x in findings(f, "3D Effect Plate")), [x.msg for x in findings(f, "3D Effect Plate")])

# ---- 3. Engineering Plate ------------------------------------------------------------------------------------------------------
PLA = ("Bambu PLA Basic @BBL P1S 0.4 nozzle", "Bambu PLA Matte @BBL P1S 0.4 nozzle", "Bambu PLA Silk+ @BBL P1S 0.4 nozzle")
PETG = ("Bambu PETG Basic @BBL X1C", "Bambu PETG-CF @BBL X1C 0.4 nozzle", "SUNLU PETG @BBL X1C")
for fil in PLA + PETG:
    j, err = try_load(job("2.9.2", filament=fil, top='plate_type = "Engineering Plate"'))
    check("3 job 2.9.2: %s on Engineering Plate builds" % fil, j is not None, err)
for fil in PLA:
    j, err = try_load(job("2.9.1", filament=fil, top='plate_type = "Engineering Plate"'))
    check("3 job 2.9.1: %s on Engineering Plate is refused (eng_plate_temp 0)" % fil, j is None and err and "eng_plate_temp = 0" in err, err)
for fil in PETG:
    j, err = try_load(job("2.9.1", filament=fil, top='plate_type = "Engineering Plate"'))
    check("3 job 2.9.1: %s on Engineering Plate builds (already non-zero in 2.9.1)" % fil, j is not None, err)

# ---- 4. cross_part_color_penetration ---------------------------------------------------------------------------------------------
j, err = try_load(job("2.9.2", part="[object.part.settings]\ncross_part_color_penetration = false"))
check("4 job 2.9.2: cross_part_color_penetration on a normal part builds", j is not None, err)
j, err = try_load(job("2.9.2", obj="[object.settings]\ncross_part_color_penetration = false"))
check("4 job 2.9.2: cross_part_color_penetration on an object builds", j is not None, err)
j, err = try_load(job("2.9.2", extra_part='[[object.part]]\nmesh = "%s"\ntype = "modifier_part"\n[object.part.settings]\ncross_part_color_penetration = false' % SLAB))
check("4 job 2.9.2: cross_part_color_penetration on a modifier is refused", j is None and err and "normal part only" in err, err)
j, err = try_load(job("2.9.2", obj="[[object.height_range]]\nmin_z = 1\nmax_z = 3\nlayer_height = 0.12\n[object.height_range.settings]\ncross_part_color_penetration = false"))
check("4 job 2.9.2: cross_part_color_penetration in a height range is refused", j is None and err and "cannot be set in a height range" in err, err)
j, err = try_load(job("2.9.1", part="[object.part.settings]\ncross_part_color_penetration = false"))
check("4 job 2.9.1: cross_part_color_penetration is not a setting there", j is None and err and "cross_part_color_penetration" in err, err)

# ---- 5. seam_embedded_headroom --------------------------------------------------------------------------------------------------
j, err = try_load(job("2.9.2"))
cfg = json.loads(zipfile.ZipFile(built(job("2.9.2"))).read("Metadata/project_settings.config"))
check("5 2.9.2 file: seam_embedded_headroom is the P1S process value 10", cfg.get("seam_embedded_headroom") == "10", cfg.get("seam_embedded_headroom"))
p = job("2.9.2", obj="[object.settings]\nseam_embedded_headroom = 4")
j, err = try_load(p)
check("5 job 2.9.2: seam_embedded_headroom per object builds", j is not None, err)
if j:
    r = validate.validate(built(p), mode="strict")
    check("5 2.9.2 file with the object override: strict validation passes", r.ok and not r.findings, [(f.rule, f.msg[:80]) for f in r.findings])
cfg1 = json.loads(zipfile.ZipFile(built(job("2.9.1"))).read("Metadata/project_settings.config"))
check("5 2.9.1 file: no 2.9.2 keys written", not [k for k in ("seam_embedded_headroom", "cross_part_color_penetration", "effect_plate_temp") if k in cfg1])

say("\n%d checks measured" % measured)
if measured == 0:
    say("NOT CHECKED")
say("%d failed" % len(fails) if fails else "all passed")
if REPORT:
    with open(REPORT, "w") as fh:
        fh.write("\n".join(LOG) + "\n")
sys.exit(1 if fails or measured == 0 else 0)
