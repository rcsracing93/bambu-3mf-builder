#!/usr/bin/env python3
"""color_penetration_cross_parts (alias of Studio 2.9.2 cross_part_color_penetration): where the alias is written, what is refused.
Run: python3 -I tests/test_color_penetration.py"""
import json, os, sys, tempfile, zipfile
import xml.etree.ElementTree as ET
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import package, validate  # noqa: E402
from bambu3mf.job import load_job, JobError  # noqa: E402

ALIAS, KEY = "color_penetration_cross_parts", "cross_part_color_penetration"
fails = []
measured = 0
tmp = tempfile.mkdtemp()
D = os.path.join(H, "..", "examples", "data")
BODY, INLAY = os.path.join(D, "slab1.stl"), os.path.join(D, "slab2.stl")
N = [0]


def check(name, cond, extra=""):
    global measured
    measured += 1
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)[:500]) if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


def job(studio, top="", obj="", part1="", part2="", extra=""):
    N[0] += 1
    p = os.path.join(tmp, "j%d.toml" % N[0])
    with open(p, "w") as f:
        f.write('''studio = "%s"
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"
%s
[[filament]]
preset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"
color = "#111111"
[[filament]]
preset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"
color = "#FFFFFF"
[[object]]
name = "Body and inlay"
%s
[[object.part]]
name = "Body"
mesh = "%s"
filament = 1
%s
[[object.part]]
name = "Inlay"
mesh = "%s"
filament = 2
%s
%s
''' % (studio, top, obj, BODY, part1, INLAY, part2, extra))
    return p


def try_load(path):
    try:
        return load_job(path), None
    except JobError as e:
        return None, "; ".join(e.problems)


def build(path):
    out = path[:-5] + ".3mf"
    package.build(load_job(path), out)
    return out


def object_meta(f):
    root = ET.fromstring(zipfile.ZipFile(f).read("Metadata/model_settings.config"))
    obj = root.find("object")
    own = {m.get("key"): m.get("value") for m in obj.findall("metadata") if m.get("key")}
    parts = {}
    for p in obj.findall("part"):
        name = [m.get("value") for m in p.findall("metadata") if m.get("key") == "name"][0]
        parts[name] = {m.get("key"): m.get("value") for m in p.findall("metadata") if m.get("key")}
    return own, parts


def proj(f):
    z = zipfile.ZipFile(f)
    return json.loads(z.read("Metadata/project_settings.config"))


# 1. object setting through the alias
f = build(job("2.9.2", obj="[object.settings]\n%s = false" % ALIAS))
own, parts = object_meta(f)
check("1 object: alias writes %s = 0 in the object config" % KEY, own.get(KEY) == "0", own)
check("1 object: the alias name itself is not written", ALIAS not in own)
check("1 object: no part has the key", all(KEY not in p for p in parts.values()), parts)
check("1 object: project value stays at the default 1", proj(f).get(KEY) == "1", proj(f).get(KEY))
r = validate.validate(f, mode="strict")
check("1 object: built file validates with 0 findings", len(r.findings) == 0, [x.msg for x in r.findings][:5])

# 2. global setting
f = build(job("2.9.2", top="[settings]\n%s = false" % ALIAS))
cfg = proj(f)
check("2 [settings]: project_settings.config has %s = 0" % KEY, cfg.get(KEY) == "0", cfg.get(KEY))
check("2 [settings]: listed in different_settings_to_system", KEY in str(cfg.get("different_settings_to_system")), cfg.get("different_settings_to_system"))
own, parts = object_meta(f)
check("2 [settings]: the object config does not repeat it", KEY not in own)
r = validate.validate(f, mode="strict")
check("2 [settings]: built file validates with 0 findings", len(r.findings) == 0, [x.msg for x in r.findings][:5])

# 3. part override (object true, one part false)
f = build(job("2.9.2", obj="[object.settings]\n%s = true" % ALIAS, part1="[object.part.settings]\n%s = false" % ALIAS))
own, parts = object_meta(f)
check("3 part: Body has %s = 0" % KEY, parts["Body"].get(KEY) == "0", parts["Body"])
check("3 part: Inlay has no own value", KEY not in parts["Inlay"], parts["Inlay"])
check("3 part: object keeps 1", own.get(KEY) == "1", own)
r = validate.validate(f, mode="strict")
check("3 part: built file validates with 0 findings", len(r.findings) == 0, [x.msg for x in r.findings][:5])

# 4. refusals through the alias
j, err = try_load(job("2.9.2", extra='[[object.part]]\nname = "Mod"\nmesh = "%s"\ntype = "modifier_part"\n[object.part.settings]\n%s = false' % (INLAY, ALIAS)))
check("4 modifier: refused through the alias", j is None and err and "normal part only" in err, err)
j, err = try_load(job("2.9.2", obj="[[object.height_range]]\nmin_z = 1\nmax_z = 3\nlayer_height = 0.12\n[object.height_range.settings]\n%s = false" % ALIAS))
check("4 height range: refused through the alias", j is None and err and "cannot be set in a height range" in err, err)

# 5. old Studio versions
for v in ("2.8.2", "2.9.1"):
    for where, kw in (("[settings]", dict(top="[settings]\n%s = false" % ALIAS)),
                      ("[object.settings]", dict(obj="[object.settings]\n%s = false" % ALIAS)),
                      ("[object.part.settings]", dict(part1="[object.part.settings]\n%s = false" % ALIAS))):
        j, err = try_load(job(v, **kw))
        check("5 %s %s: refused with 'needs Studio 2.9.2 or newer'" % (v, where), j is None and err and "needs Studio 2.9.2 or newer" in err, err)
j, err = try_load(job("2.9.1", obj="[object.settings]\n%s = false" % KEY))
check("5 2.9.1: the real key gets the same clear error", j is None and err and "needs Studio 2.9.2 or newer" in err, err)

# 6. alias and real key together
j, err = try_load(job("2.9.2", obj="[object.settings]\n%s = false\n%s = false" % (ALIAS, KEY)))
check("6 alias and real key together are refused", j is None and err and "another spelling" in err, err)

print("measured %d checks" % measured)
if measured == 0:
    print("NOT CHECKED")
    sys.exit(2)
if fails:
    print("FAIL: %d of %d" % (len(fails), measured))
    sys.exit(1)
print("PASS: %d checks" % measured)
