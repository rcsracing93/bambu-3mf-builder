#!/usr/bin/env python3
"""R3 / S1. A key is reported once (R3; the load simulator S1 does not repeat it). A slot whose preset is not a system
preset is compared with the parent that inherits_group names for it (Preset.cpp load_external_preset); a slot with no parent is skipped with a
note, never the whole file. Synthetic: a builder file whose slot names are renamed. Run: python3 -I tests/test_r3_user_presets.py"""
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
sys.path.insert(0, H)
from fixture_lists import add_inherits_group  # noqa: E402
add_inherits_group(BASE)
PS = "Metadata/project_settings.config"
c0 = json.loads(zipfile.ZipFile(BASE).read(PS))
n = len(c0["filament_colour"])
check("fixture: two or more filaments, both lists present", n >= 2 and "inherits_group" in c0 and "different_settings_to_system" in c0, n)


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


def rep(p, mode="default"):
    return validate.validate(p, mode=mode)


def rs(r, *rules):
    return [f for f in r.findings if f.rule in rules]


# ---- each key once ---------------------------------------------------------------------------------------------------------------
blank = variant("blank", lambda c: (c.__setitem__("different_settings_to_system", [""] * (n + 2)), c.__setitem__("inherits_group", [""] * (n + 2))))
r = rep(blank, "strict")
named = {}
for f in r.findings:
    if f.rule in ("R3", "S1"):
        for k in ("wall_loops", "filament_max_volumetric_speed"):
            if k in f.msg:
                named.setdefault(k, []).append(f.rule)
check("blank diff lists: R3 names the changed keys", rs(r, "R3") and "wall_loops" in rs(r, "R3")[0].msg, [str(f)[:100] for f in r.findings])
check("blank diff lists: no key is named by both R3 and S1", all(len(v) == 1 for v in named.values()) and not rs(r, "S1"), (named, [str(f)[:80] for f in rs(r, "S1")]))
r = rep(blank)
check("default mode: R3 is one warning, class CHANGE, and says what Studio does", [(f.level, f.cls) for f in rs(r, "R3")] == [("warning", "CHANGE")] and "Preset.cpp load_external_preset" in rs(r, "R3")[0].msg and not r.errors, [str(f)[:100] for f in r.findings])

# ---- a user preset in slot 1: base = inherits_group parent ---------------------------------------------------------------------------
sys_name = c0["filament_settings_id"][0]


def user_slot(c, listed=True, change=None):
    c["filament_settings_id"][0] = "My own PLA"
    ig = list(c["inherits_group"]); ig[1] = sys_name; c["inherits_group"] = ig
    if change:
        c["filament_max_volumetric_speed"][0] = change
        if listed:
            dl = list(c["different_settings_to_system"])
            dl[1] = (dl[1] + ";" if dl[1] else "") + "filament_max_volumetric_speed"
            c["different_settings_to_system"] = dl


r = rep(variant("user_clean", lambda c: user_slot(c)), "strict")
check("user preset with a parent, nothing changed: no R3 / S1, whole file still checked", not rs(r, "R3", "S1"), [str(f)[:100] for f in r.findings])
notes = [f for f in r.findings if f.rule == "R10"]
check("   its R10 note names the parent as the base", len(notes) == 1 and sys_name in notes[0].msg and "parent" in notes[0].msg, [str(f)[:160] for f in notes])
r = rep(variant("user_unlisted", lambda c: user_slot(c, listed=False, change="3")), "strict")
check("user preset with a parent, a changed value not listed: R3 names it", rs(r, "R3") and "filament_max_volumetric_speed (filament 1)" in rs(r, "R3")[0].msg, [str(f)[:100] for f in r.findings])
r = rep(variant("user_listed", lambda c: user_slot(c, listed=True, change="3")), "strict")
check("user preset with a parent, a changed value that is listed: no R3", not rs(r, "R3", "S1"), [str(f)[:100] for f in r.findings])


# ---- a user preset with no parent: that slot only is skipped -----------------------------------------------------------------------------
def orphan(c):
    c["filament_settings_id"][0] = "Made from scratch"
    ig = list(c["inherits_group"]); ig[1] = ""; c["inherits_group"] = ig
    c["filament_max_volumetric_speed"][0] = "3"                                   # would be an unlisted change in slot 1
    c["filament_max_volumetric_speed"][2] = "4"                                   # slot 2 is a system preset: still checked
    dl = list(c["different_settings_to_system"]); dl[2] = ""; c["different_settings_to_system"] = dl
r = rep(variant("orphan", orphan), "strict")
r3 = rs(r, "R3")
check("slot without a parent: slot 1 is skipped, slot 2 is still checked", len(r3) == 1 and "(filament 2)" in r3[0].msg and "(filament 1)" not in r3[0].msg, [str(f)[:140] for f in r.findings])
check("slot without a parent: one R10 note says the values are not compared", any(f.rule == "R10" and "no parent in inherits_group" in f.msg for f in r.findings))
check("slot without a parent: the file is not skipped as a whole (no 'could not rebuild' note)", not [f for f in r.findings if "could not" in f.msg], [str(f)[:100] for f in r.findings])

# ---- a user process preset and a user printer preset -----------------------------------------------------------------------------------
def user_process(c):
    sysp = c["print_settings_id"]
    c["print_settings_id"] = "My process"
    ig = list(c["inherits_group"]); ig[0] = sysp; c["inherits_group"] = ig
r = rep(variant("user_proc", user_process), "strict")
check("user process preset with a parent: file still checked, no R3 for the unchanged keys", not rs(r, "R3", "S1") and any(f.rule == "R10" and "print_settings_id" in f.msg for f in r.findings), [str(f)[:100] for f in r.findings])
def user_printer(c):
    sysp = c["printer_settings_id"]
    c["printer_settings_id"] = "My printer"
    ig = list(c["inherits_group"]); ig[n + 1] = sysp; c["inherits_group"] = ig
r = rep(variant("user_printer", user_printer), "strict")
check("user printer preset with a parent: file still checked, no R3 / R9", not rs(r, "R3", "S1", "R9") and any(f.rule == "R10" and "printer_settings_id" in f.msg for f in r.findings), [str(f)[:100] for f in r.findings])
def orphan_printer(c):
    c["printer_settings_id"] = "My printer"
r = rep(variant("orphan_printer", orphan_printer), "strict")
check("user printer preset without a parent: R3 / S1 cannot run: one note, not an error", not rs(r, "R3", "S1") or all(f.level != "error" for f in rs(r, "R3", "S1")))

print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
