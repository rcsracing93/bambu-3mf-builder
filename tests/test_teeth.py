#!/usr/bin/env python3
"""T12 teeth test: the validator must fail six broken files, each for its own reason, and pass the good one.
Also: the load simulator (S1) must flag the three breakages that make Studio drop or reset settings.
Run: python3 -I tests/test_teeth.py"""
import json, os, re, shutil, sys, tempfile, zipfile
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import validate, package
from bambu3mf.job import load_job

fails = []
def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)) if (extra and not cond) else ""))
    if not cond: fails.append(name)

tmp = tempfile.mkdtemp()
try:
    good = os.path.join(tmp, "good.3mf")
    package.build(load_job(os.path.join(H, "jobs", "p1s_two_color.toml")), good)   # has wall_loops, infill, flow and temperature overrides
    r = validate.validate(good, mode="strict", privacy=True)
    check("good file passes with no findings", r.findings == [], [str(x) for x in r.findings])

    def broken(name, entry, fn):
        zin = zipfile.ZipFile(good); p = os.path.join(tmp, name + ".3mf")
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zo:
            for i in zin.infolist():
                d = zin.read(i.filename)
                if i.filename == entry: d = fn(d)
                zo.writestr(i.filename, d)
        return validate.validate(p, mode="strict", privacy=True)

    def cfgedit(fn):
        def f(d):
            c = json.loads(d); fn(c); return (json.dumps(c, indent=4) + "\n").encode()
        return f

    cases = [
        ("1 Application not BambuStudio", "3D/3dmodel.model", lambda d: d.replace(b"BambuStudio-02", b"Slicer-02"), "R1", True),
        ("2 overrides not listed in the diff lists", "Metadata/project_settings.config",
         cfgedit(lambda c: (c.__setitem__("different_settings_to_system", [""] * 4), c.__setitem__("inherits_group", [""] * 4))), "R3", False),   # R3 reports each key once; S1 does not repeat it
        ("3 printer_model not a Bambu printer", "Metadata/project_settings.config", cfgedit(lambda c: c.__setitem__("printer_model", "Generic")), "R9", True),
        ("4 per-filament vector with the wrong size", "Metadata/project_settings.config", cfgedit(lambda c: c["filament_density"].pop()), "R5", False),
        ("5 sub-model not listed in the relationships", "3D/_rels/3dmodel.model.rels", lambda d: re.sub(rb" <Relationship [^>]*/>\n", b"", d), "R20", False),
        ("6 account id left in the file", "3D/3dmodel.model", lambda d: d.replace(b'<metadata name="DesignerUserId"></metadata>', b'<metadata name="DesignerUserId">12505641</metadata>'), "R33", False),
    ]
    for name, entry, fn, rule, sim in cases:
        rep = broken(name.replace(" ", "_"), entry, fn)
        if rule == "R33":                      # class PRIVACY: reported as a warning, never an error
            check("broken: %s -> %s (warning, rc 0)" % (name, rule), rep.ok and any(f.rule == rule and f.level == "warning" for f in rep.findings), [str(f)[:80] for f in rep.findings])
            continue
        check("broken: %s -> %s" % (name, rule), (not rep.ok) and rule in rep.rules_failed(), rep.rules_failed())
        if sim:
            check("   load simulator flags it (S1)", "S1" in rep.rules_failed(), rep.rules_failed())
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
