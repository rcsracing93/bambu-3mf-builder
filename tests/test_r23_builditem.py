#!/usr/bin/env python3
"""R23 "object has no build item" (review 0d, D2). Synthetic: built from the builder's two-object job.
An object nothing uses as a component and without a build item is dropped by Studio on load (bbs_3mf.cpp 2.8.2 :3761, 2.9.1 :4113):
class LOAD, an error in default mode. An object that another object uses as a component is only a different form (class FORM).
Run: python3 -I tests/test_r23_builditem.py"""
import os, re, sys, tempfile, zipfile
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
CUBE = os.path.join(H, "data", "cube20.stl")
toml = os.path.join(tmp, "two.toml")
open(toml, "w").write('''studio  = "2.8.2"
output  = "two.3mf"
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"
[[filament]]
preset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"
color = "#111111"
[[object]]
name = "A"
position = [60, 60]
[[object.part]]
mesh = "%s"
[[object]]
name = "B"
position = [200, 50]
[[object.part]]
mesh = "%s"
''' % (CUBE, CUBE))
TWO = os.path.join(tmp, "two.3mf")
package.build(load_job(toml), TWO)
M3, MS = "3D/3dmodel.model", "Metadata/model_settings.config"


def edit(src, name, edits):
    z = zipfile.ZipFile(src)
    p = os.path.join(tmp, name)
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zo:
        for i in z.infolist():
            d = z.read(i.filename)
            if i.filename in edits:
                d = edits[i.filename](d)
            zo.writestr(i.filename, d)
    return p


def drop_item(d):
    return re.sub(rb'<item [^>]*objectid="4"[^>]*/>\s*', b"", d)


def drop_instance(d):
    t = d.decode()
    t = re.sub(r'<model_instance>(?:(?!</model_instance>).)*?key="object_id" value="4".*?</model_instance>\s*', "", t, flags=re.S)
    return t.encode()


def r23(r):
    return [(f.level, f.cls) for f in r.findings if f.rule == "R23"]


check("clean two-object file: no R23", not r23(validate.validate(TWO, mode="strict")))
GONE = edit(TWO, "gone.3mf", {M3: drop_item, MS: drop_instance})
for mode in ("default", "strict"):
    r = validate.validate(GONE, mode=mode)
    check("object 4 without build item, nothing uses it: LOAD error in %s mode, names the Studio source" % mode,
          r23(r) == [("error", "LOAD")] and "bbs_3mf.cpp" in [f for f in r.findings if f.rule == "R23"][0].msg, [str(f)[:120] for f in r.findings])
check("... and the file does not pass in default mode", not validate.validate(GONE).ok)
# object 4 used as a component by a new object 9: only the form differs
USED = edit(GONE, "used.3mf", {M3: lambda d: d.replace(b"</resources>", b'<object id="9" type="model"><components><component objectid="4"/></components></object></resources>', 1)})
r = validate.validate(USED, mode="default")
check("object 4 used as a component by another object: R23 is FORM (silent in default mode)",
      not [f for f in r.findings if f.rule == "R23" and "object 4 has no build item" in f.msg] and any(f.rule == "R23" and f.cls == "FORM" and "object 4 has no build item" in f.msg for f in r.silent),
      [(f.rule, f.cls, f.msg[:60]) for f in r.findings + r.silent])

print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
