"""seam_headroom (Studio 2.9.2 seam_embedded_headroom). Run: python3 -I tests/test_seam_headroom.py"""
import json, os, re, shutil, sys, tempfile, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf.job import load_job, JobError      # noqa: E402
from bambu3mf import package, validate           # noqa: E402

fails = []
measured = [0]


def check(name, cond, extra=""):
    measured[0] += 1
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)) if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


CUBE = os.path.join(ROOT, "examples", "data", "cube20.stl")
HEAD = '''studio = "%s"
output = "out.3mf"
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"
%s
[[filament]]
preset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"
color = "#2060C0"

[[object]]
name = "A"
%s
[[object.part]]
mesh = "%s"
%s
'''
tmp = tempfile.mkdtemp()


def text(studio="2.9.2", top="", obj="", part="", mesh=CUBE):
    return HEAD % (studio, top, obj, mesh, part)


def build(txt, name="t"):
    p = os.path.join(tmp, name + ".toml")
    open(p, "w").write(txt)
    return package.build(load_job(p), os.path.join(tmp, name + ".3mf"))


def refused(txt, needle, name):
    try:
        build(txt, "bad")
    except JobError as e:
        msg = " | ".join(e.problems)
        check(name, needle in msg, msg)
        return msg
    check(name, False, "built without an error")
    return ""


def read(path, member):
    return zipfile.ZipFile(path).read(member).decode()


def project(path):
    return json.loads(read(path, "Metadata/project_settings.config"))


def project_diff(cfg):
    d = cfg.get("different_settings_to_system") or []
    return {k for part in d for k in part.split(";") if k}


try:
    # 1. alias and real key both write seam_embedded_headroom with the job value
    for label, key in (("alias", "seam_headroom"), ("real key", "seam_embedded_headroom")):
        s = build(text(top="[settings]\n%s = 20\n" % key), "a")
        cfg = project(s.path)
        check("%s: project value is 20" % label, cfg.get("seam_embedded_headroom") == "20", cfg.get("seam_embedded_headroom"))
        check("%s: listed in different_settings_to_system" % label, "seam_embedded_headroom" in project_diff(cfg))
        check("%s: alias name is not written" % label, "seam_headroom" not in cfg)
    # 2. the P1S preset value 10 applies when the job does not set it, and it is not a builder default
    s0 = build(text(), "d")
    cfg0 = project(s0.path)
    check("default: preset value 10 applies", cfg0.get("seam_embedded_headroom") == "10", cfg0.get("seam_embedded_headroom"))
    check("default: not listed as a changed setting", "seam_embedded_headroom" not in project_diff(cfg0))
    # 3. refused on Studio 2.8.2 and 2.9.1, both spellings, with the version in the message
    for ver in ("2.8.2", "2.9.1"):
        for key in ("seam_headroom", "seam_embedded_headroom"):
            msg = refused(text(studio=ver, top="[settings]\n%s = 20\n" % key), "needs Studio 2.9.2 or newer", "%s refused on %s" % (key, ver))
            check("%s on %s: message names the setting, not a generic unknown key" % (key, ver), "seam_embedded_headroom needs" in msg and "Closest" not in msg, msg)
    # 4. object override: accepted, in the object config
    so = build(text(obj="[object.settings]\nseam_headroom = 5\n"), "o")
    ms = read(so.path, "Metadata/model_settings.config")
    check("object override in model_settings.config", re.search(r'<metadata key="seam_embedded_headroom" value="5"/>', ms) is not None)
    check("object override leaves the project value at the preset's 10", project(so.path).get("seam_embedded_headroom") == "10")
    # 5. refused on a part and in a height range
    refused(text(part="[object.part.settings]\nseam_headroom = 5\n"), "is an object setting", "part override refused")
    refused(text(part="[object.part.settings]\nseam_embedded_headroom = 5\n"), "per part", "part override refused (real key)")
    refused(text(part="[[object.height_range]]\nmin_z = 0\nmax_z = 5\nlayer_height = 0.12\n[object.height_range.settings]\nseam_headroom = 5\n"),
            "per height range", "height range refused")
    # 6. value limits come from Studio's option table (0 to 100)
    refused(text(top="[settings]\nseam_headroom = 150\n"), "seam_embedded_headroom", "value above 100 refused")
    refused(text(top="[settings]\nseam_headroom = -1\n"), "seam_embedded_headroom", "negative value refused")
    # 7. built files validate strict, with a count of what was measured
    for label, s in (("settings", build(text(top="[settings]\nseam_headroom = 20\n"), "v1")), ("object", so),
                     ("example", package.build(load_job(os.path.join(ROOT, "examples", "seam_headroom.toml")), os.path.join(tmp, "ex.3mf")))):
        rep = validate.validate(s.path, mode="strict")
        check("%s file validates strict (0 findings)" % label, not rep.findings, [str(f) for f in rep.findings])
    ex = project(os.path.join(tmp, "ex.3mf"))
    check("example sets 20 (not the preset's 10)", ex.get("seam_embedded_headroom") == "20")
    # 8. the validator reports the key on a part override of a built file
    src = zipfile.ZipFile(so.path)
    patched = os.path.join(tmp, "part.3mf")
    with zipfile.ZipFile(patched, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "Metadata/model_settings.config":
                t = data.decode()
                t2 = t
                m = re.search(r'(<part id="[^"]*"[^>]*>)', t)
                if m:
                    t2 = t.replace(m.group(1), m.group(1) + '\n      <metadata key="seam_embedded_headroom" value="5"/>', 1)
                check("test file: part override was added", t2 != t)
                data = t2.encode()
            out.writestr(info, data)
    rep = validate.validate(patched, mode="strict")
    check("validator reports an object setting on a part", any("object setting" in f.msg for f in rep.findings), [str(f) for f in rep.findings])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("measured %d checks" % measured[0])
if measured[0] == 0:
    print("NOT CHECKED: 0 checks measured")
    sys.exit(1)
if fails:
    print("FAILED: %d" % len(fails))
    sys.exit(1)
print("PASS: %d checks" % measured[0])
