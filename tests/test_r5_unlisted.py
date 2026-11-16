#!/usr/bin/env python3
"""R5 / R6 / R8 / R35 list sizes. A list of the wrong size in a key that is not in different_settings_to_system is replaced
by the system preset value when the Studio app opens the file (Preset.cpp load_external_preset): class CHANGE, a warning in default mode.
A key that is listed keeps its file value, so a wrong size stays an error. Synthetic: a builder file with one list shortened.
Run: python3 -I tests/test_r5_unlisted.py"""
import json, os, sys, tempfile, zipfile
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
BASE = os.path.join(tmp, "base.3mf")
package.build(load_job(os.path.join(H, "data", "mutation_base.toml")), BASE)
PS = "Metadata/project_settings.config"


def variant(name, fn):
    z = zipfile.ZipFile(BASE)
    p = os.path.join(tmp, name + ".3mf")
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zo:
        for i in z.infolist():
            d = z.read(i.filename)
            if i.filename == PS:
                c = json.loads(d)
                fn(c)
                d = json.dumps(c).encode()
            zo.writestr(i.filename, d)
    return p


def list_it(c, key, slot=1):
    dl = list(c["different_settings_to_system"])
    dl[slot] = (dl[slot] + ";" if dl[slot] else "") + key
    c["different_settings_to_system"] = dl


def hits(p, mode, *rules):
    return [f for f in validate.validate(p, mode=mode).findings if f.rule in rules]


def short(key, listed, keep=None):
    def f(c):
        c[key] = c[key][:keep] if keep else c[key][:-1]
        if listed:
            list_it(c, key)
    return f


for key, rule, keep in (("filament_density", "R5", None), ("nozzle_temperature", "R35", 1), ("nozzle_temperature", "R6", 3)):
    got = hits(variant("u_%s_%s" % (key, rule), short(key, False, keep)), "default", rule)
    check("%s short, not listed: %s is a warning, class CHANGE, says the Studio app replaces it" % (key, rule),
          len(got) == 1 and got[0].level == "warning" and got[0].cls == "CHANGE" and "replaces it with the system preset value" in got[0].msg, [str(f)[:120] for f in got])
    got = hits(variant("us_%s_%s" % (key, rule), short(key, False, keep)), "strict", rule)
    check("%s short, not listed, strict: %s is an error" % (key, rule), len(got) == 1 and got[0].level == "error", [str(f)[:120] for f in got])
    got = hits(variant("l_%s_%s" % (key, rule), short(key, True, keep)), "default", rule)
    check("%s short, listed: %s stays an error in default mode" % (key, rule), len(got) == 1 and got[0].level == "error" and got[0].cls != "CHANGE", [str(f)[:120] for f in got])
check("a listed R35 (Studio misreads it) names the Studio version", "Studio 2.8.2.61 misreads it" in hits(variant("v35", short("nozzle_temperature", True, 1)), "default", "R35")[0].msg)
for key, rule, keep in (("filament_density", "R5", None), ("nozzle_temperature", "R35", 1), ("nozzle_temperature", "R6", 3)):
    m = hits(variant("s_%s_%s" % (key, rule), short(key, False, keep)), "default", rule)[0].msg
    check("%s CHANGE message names the Studio source line, no 'misreads'" % rule, "Preset.cpp load_external_preset (2.8.2 :2263, 2.9.1 :2331)" in m and "misreads" not in m, m)

# ---- Studio's own one-entry default (2.9.1 CLI re-export of a file that lacked the key) is FORM ------------------------------------------------------
B91 = os.path.join(tmp, "b91.3mf")
package.build(load_job(os.path.join(H, "jobs", "four_color_291.toml")), B91)


def variant91(name, fn):
    z = zipfile.ZipFile(B91)
    p = os.path.join(tmp, name + ".3mf")
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zo:
        for i in z.infolist():
            d = z.read(i.filename)
            if i.filename == PS:
                c = json.loads(d)
                fn(c)
                d = json.dumps(c).encode()
            zo.writestr(i.filename, d)
    return p


for key, val, rule in (("filament_pre_heating_enabled", ["1"], "R5"), ("fins_extrude_safe_temp", ["0"], "R5"), ("hotend_cooling_rate_on_rack", ["2"], "R8"), ("filament_retract_length_toolchange", ["nil"], "R35")):
    p = variant91("d91_" + key, lambda c, key=key, val=val: c.__setitem__(key, val))
    rd, rs = validate.validate(p), validate.validate(p, mode="strict")
    check("2.9.1 %s = %s (the option's default, one entry, not listed): %s is FORM, silent in default mode, an error in strict" % (key, val, rule),
          not [f for f in rd.findings if f.rule == rule] and [f.cls for f in rd.silent if f.rule == rule] == ["FORM"] and any(f.rule == rule and f.level == "error" for f in rs.findings),
          [(f.rule, f.cls, f.msg[:60]) for f in rd.findings + rd.silent])
p = variant91("d91_other", lambda c: c.__setitem__("fins_extrude_safe_temp", ["5"]))
check("2.9.1 one entry that is not the default: still CHANGE (warning)", [(f.rule, f.cls, f.level) for f in validate.validate(p).findings if f.rule == "R5"] == [("R5", "CHANGE", "warning")])
p = variant91("d91_listed", lambda c: (c.__setitem__("fins_extrude_safe_temp", ["0"]), c.__setitem__("different_settings_to_system", c.get("different_settings_to_system") or [""] * (len(c["filament_colour"]) + 2)), list_it(c, "fins_extrude_safe_temp")))
check("2.9.1 the default, one entry, but listed in different_settings_to_system: still an error", [(f.rule, f.level) for f in validate.validate(p).findings if f.rule == "R5"] == [("R5", "error")])

print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
