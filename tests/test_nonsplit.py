#!/usr/bin/env python3
"""The non-split form: File > Export > Export Generic 3MF writes every mesh inline in 3D/3dmodel.model: no p: namespace,
no requiredextensions, no 3D/Objects/, mesh objects 1, 3, 5.. and wrappers 2, 4, 6.. whose components have no p:path.
The fixture is synthetic: a builder cube converted in this test. Run: python3 -I tests/test_nonsplit.py"""
import json, os, re, sys, tempfile, zipfile
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import validate, package, rules_package  # noqa: E402
from bambu3mf.job import load_job  # noqa: E402

fails = []
def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)) if (extra and not cond) else ""))
    if not cond: fails.append(name)

tmp = tempfile.mkdtemp()
SPLIT = os.path.join(tmp, "split.3mf")
package.build(load_job(os.path.join(H, "jobs", "four_color_282.toml")), SPLIT)
SPLIT_T = os.path.join(tmp, "split_t.3mf")        # same job with smooth timelapse: the plate has a prime tower (Print::has_wipe_tower)
package.build(load_job(os.path.join(H, "jobs", "four_color_tower_282.toml")), SPLIT_T)
M3, OBJ, MS, PS = "3D/3dmodel.model", "3D/Objects/object_1.model", "Metadata/model_settings.config", "Metadata/project_settings.config"


def to_nonsplit(src, dst, root_edit=None, edits=None):
    """Inline the sub-model mesh into the root model, as Export Generic 3MF does (mesh object 1, wrapper 2)."""
    z = zipfile.ZipFile(src)
    root = z.read(M3).decode()
    mesh = re.search(r"<object .*?</object>", z.read(OBJ).decode(), re.S).group(0)
    mesh = re.sub(r' p:UUID="[^"]*"', "", mesh)
    root = root.replace(' xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"', "").replace(' requiredextensions="p"', "")
    root = re.sub(r' p:UUID="[^"]*"', "", root)
    root = root.replace(' p:path="/3D/Objects/object_1.model"', "")
    root = root.replace("<resources>\n", "<resources>\n  " + mesh + "\n")
    if root_edit:
        root = root_edit(root)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
        for i in z.infolist():
            n = i.filename
            if n == OBJ or n == "3D/_rels/3dmodel.model.rels":
                continue
            d = z.read(n)
            if n == M3:
                d = root.encode()
            elif edits and n in edits:
                d = edits[n](d)
            zo.writestr(n, d)
    return dst


def show(r):
    return [(f.rule, f.level, f.msg[:70]) for f in r.findings]


GOOD = to_nonsplit(SPLIT, os.path.join(tmp, "ns.3mf"))
z = zipfile.ZipFile(GOOD)
root = z.read(M3).decode()
check("fixture is the non-split form: no p: namespace, no requiredextensions, no 3D/Objects/, no p:path",
      "xmlns:p" not in root and "requiredextensions" not in root and "p:path" not in root and not any(n.startswith("3D/Objects/") for n in z.namelist()))
check("fixture: mesh object 1 and wrapper 2 are both in the root model", re.findall(r'<object id="(\d+)"', root) == ["1", "2"], re.findall(r'<object id="(\d+)"', root))

r = validate.validate(GOOD)
check("non-split cube: default mode 0 errors, 0 warnings", not r.errors and not r.warnings, show(r))
check("non-split cube: nothing hidden (no R21 requiredextensions, no R23 no-build-item)", not r.silent, [(f.rule, f.msg[:60]) for f in r.silent])
rs = validate.validate(GOOD, mode="strict")
check("non-split cube: strict mode 0 errors", not rs.errors, show(rs))

# the mesh is read: R24 runs on the inline mesh
bad = to_nonsplit(SPLIT, os.path.join(tmp, "ns_mesh.3mf"), root_edit=lambda t: t.replace('<triangle v1="2" v2="1" v3="6"/>', '<triangle v1="2" v2="1" v3="99"/>'))
r = validate.validate(bad)
check("non-split, broken mesh: R24 fires (error, default mode)", any(f.rule == "R24" and "does not exist" in f.msg for f in r.errors), show(r))
bad = to_nonsplit(SPLIT, os.path.join(tmp, "ns_open.3mf"), root_edit=lambda t: t.replace('<triangle v1="2" v2="6" v3="5"/>', "", 1))
r = validate.validate(bad)
check("non-split, open surface: R24 warning (KEEP) names the root model", any(f.rule == "R24" and f.level == "warning" and "3D/3dmodel.model object 1" in f.where for f in r.findings), show(r))
bad = to_nonsplit(SPLIT, os.path.join(tmp, "ns_nan.3mf"), root_edit=lambda t: t.replace('<vertex x="-10" y="-10" z="-10"/>', '<vertex x="nan" y="-10" z="-10"/>'))
r = validate.validate(bad)
check("non-split, NaN coordinate: R24 error", any(f.rule == "R24" for f in r.errors), show(r))
bad = to_nonsplit(SPLIT, os.path.join(tmp, "ns_type.3mf"), root_edit=lambda t: t.replace('<object id="1" type="model">', '<object id="1" type="other">'))
r = validate.validate(bad)
check("non-split, mesh type other on a normal part: R24 type finding", any(f.rule == "R24" and "mesh type" in f.msg for f in r.errors), show(r))

# R23 plate checks run on the inline mesh
bad = to_nonsplit(SPLIT, os.path.join(tmp, "ns_off.3mf"), root_edit=lambda t: t.replace("128 128 10", "600 128 10"))
r = validate.validate(bad)
check("non-split, item moved off the plate: R23 does not lie inside plate (error)", any(f.rule == "R23" and "does not lie inside plate" in f.msg for f in r.errors), show(r))
bad = to_nonsplit(SPLIT, os.path.join(tmp, "ns_sunk.3mf"), root_edit=lambda t: t.replace("128 128 10", "128 128 5"))
r = validate.validate(bad)
check("non-split, sunk 5 mm: R23 below the plate (warning in default mode)", any(f.rule == "R23" and "below the plate" in f.msg and f.level == "warning" and "ensure_on_bed" in f.msg and f.cls == "KEEP" for f in r.findings) and not r.errors, show(r))

# model_settings pairing runs on the mesh ids
bad = to_nonsplit(SPLIT, os.path.join(tmp, "ns_part.3mf"), edits={MS: lambda b: b.replace(b'<part id="1"', b'<part id="7"')})
r = validate.validate(bad)
check("non-split, part id 7 for mesh object 1: R26 fires", any(f.rule == "R26" for f in r.errors), show(r))
bad = to_nonsplit(SPLIT, os.path.join(tmp, "ns_noitem.3mf"), root_edit=lambda t: re.sub(r"<item .*?/>", "", t, flags=re.S))
r = validate.validate(bad, mode="strict")
check("non-split, wrapper without a build item: R23 fires (strict; the mesh object 1 is not asked for)", [(f.msg[:34], f.cls) for f in r.errors if f.rule == "R23"] == [("object 2 has no build item: Studio", "LOAD")], show(r))
bad = to_nonsplit(SPLIT, os.path.join(tmp, "ns_comp.3mf"), root_edit=lambda t: t.replace('objectid="1" transform', 'objectid="9" transform'))
r = validate.validate(bad, mode="strict")
check("non-split, component to a missing object: R21 names the root model", any(f.rule == "R21" and "is not an object in 3D/3dmodel.model" in f.msg for f in r.errors), show(r))

# R37 inputs: a part on the prime tower
GOOD_T = to_nonsplit(SPLIT_T, os.path.join(tmp, "ns_t.3mf"))
cfg = json.loads(zipfile.ZipFile(GOOD_T).read(PS))
tx, ty = float(cfg["wipe_tower_x"][0]), float(cfg["wipe_tower_y"][0])
bad = to_nonsplit(SPLIT_T, os.path.join(tmp, "ns_tower.3mf"), root_edit=lambda t: t.replace("128 128 10", "%g %g 10" % (tx + 5, ty + 5)))
r = validate.validate(bad, mode="strict")
check("non-split, part on the prime tower: R37 overlap (strict)", any(f.rule == "R37" and "overlaps the prime tower" in f.msg for f in r.errors), show(r))
r = validate.validate(GOOD_T, mode="strict")
check("non-split, part clear of the tower: no R37 overlap", not any(f.rule == "R37" and "overlaps" in f.msg for f in r.findings))

# R21: requiredextensions is needed only when p: elements are used
bad = to_nonsplit(SPLIT, os.path.join(tmp, "ns_p.3mf"), root_edit=lambda t: t.replace("<model ", '<model xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" ', 1).replace('<build>', '<build p:UUID="2c7c17d8-22b5-4d84-8835-1976022ea369">', 1))
r = validate.validate(bad, mode="strict")
check("non-split with a p: attribute but no requiredextensions: R21 fires", any(f.rule == "R21" and "requiredextensions" in f.msg for f in r.errors), show(r))
bad = os.path.join(tmp, "split_noext.3mf")
zin = zipfile.ZipFile(SPLIT)
with zipfile.ZipFile(bad, "w") as zo:
    for i in zin.infolist():
        d = zin.read(i.filename)
        zo.writestr(i.filename, d.replace(b' requiredextensions="p"', b"") if i.filename == M3 else d)
r = validate.validate(bad, mode="strict")
check("split form without requiredextensions: R21 still fires", any(f.rule == "R21" and "requiredextensions" in f.msg for f in r.errors), show(r))

# a component with p:path = the root model itself resolves to the root
bad = to_nonsplit(SPLIT, os.path.join(tmp, "ns_self.3mf"), root_edit=lambda t: t.replace('<component objectid="1"', '<component p:path="/3D/3dmodel.model" objectid="1"'))
r = validate.validate(bad)
check("component with p:path to the root model resolves in the root (no R20, no R21 'not an object')", not any(f.rule in ("R20",) or "not an object" in f.msg for f in r.findings), show(r))

# the root model is parsed once
calls = []
orig = rules_package.parse_model
rules_package.parse_model = lambda pkg, path, rep, root=False: (calls.append(path), orig(pkg, path, rep, root))[1]
try:
    validate.validate(GOOD, mode="strict", privacy=True)
finally:
    rules_package.parse_model = orig
check("root model parsed once per file", calls.count(M3) == 1, calls)

# the vertex cap applies to a mesh in the root model
saved = validate.MAX_VERTICES
validate.MAX_VERTICES = 5
try:
    r = validate.validate(GOOD)
finally:
    validate.MAX_VERTICES = saved
check("non-split: more vertices than the limit -> G5", any(f.rule == "G5" for f in r.errors), show(r))

# a split file is unchanged
r = validate.validate(SPLIT, mode="strict")
check("split cube: strict mode 0 errors", not r.errors, show(r))

print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
