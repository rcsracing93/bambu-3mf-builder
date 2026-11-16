#!/usr/bin/env python3
"""Review 0d, D4: every warning that names a Studio behaviour also names the Studio source line. Synthetic: builder files, edited.
Run: python3 -I tests/test_source_lines.py"""
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
BASE = os.path.join(tmp, "base.3mf")
package.build(load_job(os.path.join(H, "data", "mutation_base.toml")), BASE)
PS, OBJ = "Metadata/project_settings.config", "3D/Objects/object_1.model"


def make(name, edits):
    z = zipfile.ZipFile(BASE)
    p = os.path.join(tmp, name + ".3mf")
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zo:
        for i in z.infolist():
            d = z.read(i.filename)
            if i.filename in edits:
                d = edits[i.filename](d)
            zo.writestr(i.filename, d)
    return p


def cfg(fn):
    def go(b):
        c = json.loads(b); fn(c); return json.dumps(c).encode()
    return go


def msgs(p, rule, mode="default"):
    return [f.msg for f in validate.validate(p, mode=mode).findings if f.rule == rule]


m = msgs(make("open", {OBJ: lambda d: re.sub(rb"<triangle [^>]*/>\s*", b"", d, count=1)}), "R24")
check("R24 open surface (KEEP): names bbs_3mf.cpp with both tags", len(m) == 1 and "bbs_3mf.cpp _generate_volumes, 2.8.2 :5550, 2.9.1 :5940" in m[0], m)
m = msgs(make("b60", {PS: cfg(lambda c: c.__setitem__("version", "02.08.02.60"))}), "R16")
check("R16 other build: names Preset.cpp", any("Preset.cpp load_external_preset (2.8.2 :2263, 2.9.1 :2331)" in x and "not a supported build" in x for x in m), m)
c0 = json.loads(zipfile.ZipFile(BASE).read(PS))
from bambu3mf import rules_config, profiles  # noqa: E402
ps = profiles.ProfileSet("2.8.2.61")
key = sorted(ps.project_options)[0]
m = msgs(make("r13", {PS: cfg(lambda c: c.pop(key, None))}), "R13")
check("R13 project option missing: names PresetBundle.cpp", len(m) == 1 and "PresetBundle.cpp project_config.apply_only, 2.8.2 :4020, 2.9.1 :5574" in m[0], m)
m = msgs(make("s1", {PS: cfg(lambda c: c.pop(key, None))}), "S1")
check("S1 project options missing (2.8.2 note): names PresetBundle.cpp", len(m) == 1 and "PresetBundle.cpp project_config.apply_only, 2.8.2 :4020" in m[0], m)

print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
