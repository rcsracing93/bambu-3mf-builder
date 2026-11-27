#!/usr/bin/env python3
"""Versions and multi-nozzle printers. Same major.minor.patch with another build (2.8.2.60): the tables of the supported
build apply, one warning, an unknown key is a warning. A newer 2.x: the newest supported tables apply, one note, an unknown key is a warning. Any other version: tables skipped, one warning, tables_applied false. A printer with
several nozzles: one warning, no list-size, preset-difference or load-simulation findings. Synthetic: a builder cube, restamped.
Run: python3 -I tests/test_r16_versions.py"""
import json, os, re, sys, tempfile, zipfile
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import validate, package  # noqa: E402
from bambu3mf.job import load_job  # noqa: E402

fails = []
def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)) if (extra and not cond) else ""))
    if not cond: fails.append(name)

tmp = tempfile.mkdtemp()
GOOD = os.path.join(tmp, "good.3mf")
package.build(load_job(os.path.join(H, "jobs", "four_color_282.toml")), GOOD)
PS, M3 = "Metadata/project_settings.config", "3D/3dmodel.model"


def variant(name, cfg_fn=None, build=None):
    z = zipfile.ZipFile(GOOD)
    p = os.path.join(tmp, name + ".3mf")
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zo:
        for i in z.infolist():
            d = z.read(i.filename)
            if i.filename == PS and cfg_fn:
                c = json.loads(d)
                cfg_fn(c)
                d = json.dumps(c).encode()
            if i.filename == M3 and build:
                d = d.decode().replace("BambuStudio-02.08.02.61", "BambuStudio-" + build).encode()
            zo.writestr(i.filename, d)
    return p


def show(r):
    return [(f.rule, f.level, f.cls, f.msg[:80]) for f in r.findings]


# ---- another build of a supported version --------------------------------------------------------------------------------------
c0 = json.loads(zipfile.ZipFile(GOOD).read(PS))
check("fixture is 2.8.2.61", c0["version"] == "02.08.02.61", c0["version"])
p60 = variant("b60", lambda c: c.__setitem__("version", "02.08.02.60"), build="02.08.02.60")
r = validate.validate(p60)
check("build .60: tables applied, studio_version reported", r.tables_applied and r.studio_version == "02.08.02.60", (r.tables_applied, r.studio_version))
check("build .60: 0 errors, exactly one warning, which names the build", not r.errors and len(r.warnings) == 1 and "2.8.2.60" in r.warnings[0].msg and r.warnings[0].rule == "R16", show(r))
rs = validate.validate(p60, mode="strict")
check("build .60 strict: still 0 errors (the build warning is a warning)", not rs.errors, show(rs))
p60k = variant("b60k", lambda c: (c.__setitem__("version", "02.08.02.60"), c.__setitem__("zz_unknown_key", "1")), build="02.08.02.60")
r = validate.validate(p60k)
unk = [f for f in r.findings if "zz_unknown_key" in f.msg]
check("build .60: a key the supported build does not know is a warning", len(unk) == 1 and unk[0].level == "warning" and not r.errors, show(r))
r = validate.validate(variant("b61k", lambda c: c.__setitem__("zz_unknown_key", "1")))
check("build .61: the same key is an error", any("zz_unknown_key" in f.msg and f.level == "error" for f in r.findings), show(r))
p60t = variant("b60t", lambda c: (c.__setitem__("version", "02.08.02.60"), c.__setitem__("wall_loops", "abc")), build="02.08.02.60")
check("build .60: the table still catches a bad value (R16 error)", any(f.rule == "R16" and f.level == "error" for f in validate.validate(p60t).findings))
bad_app = variant("b60a", lambda c: c.__setitem__("version", "02.08.02.60"), build="02.08.02.61")
check("build .60 in project_settings but .61 in the model: R15 Application mismatch", any(f.rule == "R15" and "Application is" in f.msg for f in validate.validate(bad_app).findings))

# ---- an unsupported version ------------------------------------------------------------------------------------------------------
for ver in ("02.07.01.50", "02.08.03.10", "03.00.00.01"):
    p = variant("v" + ver, lambda c, ver=ver: (c.__setitem__("version", ver), c.__setitem__("zz_unknown_key", "1"), c.__setitem__("wall_loops", "abc")), build="02.08.02.61")
    r = validate.validate(p)
    notes = [f for f in r.findings if f.rule == "R16"]
    rs = validate.validate(p, mode="strict")
    check("version %s: tables skipped, one R16 warning in default and in strict mode, tables_applied false" % ver, not r.tables_applied and len(notes) == 1 and notes[0].level == "warning" and notes[0].cls == "SKIPPED" and "skipped" in notes[0].msg
          and [(f.level, f.cls) for f in rs.findings if f.rule == "R16"] == [("warning", "SKIPPED")] and r.ok and rs.ok, show(r))
    check("version %s: no table-rule finding (unknown key and bad value are not reported)" % ver, not [f for f in r.findings if f.rule in ("R3", "R5", "R6") or (f.rule == "R16" and f.cls != "SKIPPED")], show(r))
    check("version %s: json says tables_applied false" % ver, json.loads(json.dumps(r.as_dict()))["tables_applied"] is False)

# ---- a newer 2.x than the newest supported (1.3.0 forward-compatible mode) -------------------------------------------------------
NV = "02.99.00.01"
pn = variant("vnewer", lambda c: (c.__setitem__("version", NV), c.__setitem__("zz_unknown_key", "1")), build=NV)
r = validate.validate(pn)
note = [f for f in r.findings if f.rule == "R16" and "newer than the newest supported" in f.msg]
check("newer 2.x: tables applied, one note that names the rules used", r.tables_applied and len(note) == 1 and note[0].level == "warning", show(r))
unk = [f for f in r.findings if "zz_unknown_key" in f.msg]
check("newer 2.x: an unknown key is a warning", len(unk) == 1 and unk[0].level == "warning", show(r))
pnb = variant("vnewerb", lambda c: (c.__setitem__("version", NV), c.__setitem__("wall_loops", "abc")), build=NV)
check("newer 2.x: the table still catches a bad value (R16 error)", any(f.rule == "R16" and f.level == "error" and "wall_loops" in f.msg for f in validate.validate(pnb).findings))

# ---- several nozzles -------------------------------------------------------------------------------------------------------------
def two(c):
    c["nozzle_diameter"] = ["0.4", "0.4"]
    c["extruder_type"] = ["0", "0"]
    c["filament_map"] = ["1", "1", "1", "1"]
    c["extruder_nozzle_stats"] = ["Standard#1", "Standard#1"]
    c["flush_volumes_vector"] = c["flush_volumes_vector"][:5]            # wrong for any model
    c["filament_flush_temp"] = c["filament_flush_temp"][:3]
r = validate.validate(variant("multi", two))
notes = [f for f in r.findings if "multi-nozzle printer, table rules skipped" in f.msg]
check("two nozzles: exactly one warning 'multi-nozzle printer, table rules skipped' (class SKIPPED, also in strict mode)", len(notes) == 1 and notes[0].level == "warning" and notes[0].cls == "SKIPPED"
      and [f.level for f in validate.validate(variant("multi1", two), mode="strict").findings if "multi-nozzle printer, table rules skipped" in f.msg] == ["warning"], show(r))
check("two nozzles: no R5 / R6 / R8 / R14 / R35 / R3 / R38 / S1 finding", not [f for f in r.findings + r.silent if f.rule in ("R5", "R6", "R8", "R14", "R35", "R3", "R38", "S1")], show(r))
check("two nozzles: tables_applied is false", not r.tables_applied)
rs = validate.validate(variant("multi2", two), mode="strict")
check("two nozzles, strict: the same, no list-size error", not [f for f in rs.findings if f.rule in ("R5", "R6", "R8", "R14", "R35", "R3", "R38", "S1")], show(rs))
check("two nozzles: R36 still runs (extruder_type needs one entry per nozzle)", any(f.rule == "R36" for f in validate.validate(variant("multi3", lambda c: (two(c), c.__setitem__("extruder_type", ["0"])))).findings))
r1 = validate.validate(GOOD)
check("one nozzle: no multi-nozzle note, tables applied", r1.tables_applied and not [f for f in r1.findings if "multi-nozzle" in f.msg], show(r1))

print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
