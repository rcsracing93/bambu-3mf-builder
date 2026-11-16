#!/usr/bin/env python3
"""Hostile-input tests for the validator. Every malformed file must give findings, never a traceback,
and must not make the validator read huge amounts of data. Run: python3 -I tests/test_hostile.py"""
import io, json, os, re, sys, tempfile, time, zipfile
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import validate, package
from bambu3mf.job import load_job

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)) if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


tmp = tempfile.mkdtemp()
good = os.path.join(tmp, "good.3mf")
package.build(load_job(os.path.join(H, "jobs", "four_color_282.toml")), good)


def tamper(fn, name, extra=None):
    zin = zipfile.ZipFile(good)
    p = os.path.join(tmp, name)
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zo:
        for i in zin.infolist():
            d = fn(i.filename, zin.read(i.filename))
            if d is not None:
                zo.writestr(i.filename, d)
        for n, d in (extra or []):
            zo.writestr(n, d)
    return p


def cfg_edit(f):
    def fn(n, d):
        if n != "Metadata/project_settings.config":
            return d
        return f(d)
    return fn


def jset(key, value=None, delete=False):
    def f(d):
        c = json.loads(d)
        if delete:
            c.pop(key, None)
        else:
            c[key] = value
        return json.dumps(c).encode()
    return f


def first_sub(n, d, f):
    return f(d) if n.startswith("3D/Objects/") else d


def run(name, path, want_rule=None):
    try:
        r = validate.validate(path, mode="strict", privacy=True)
    except Exception as e:                                    # noqa
        check(name + ": no traceback", False, "%s: %s" % (type(e).__name__, e))
        return None
    ok = bool(r.errors) and (want_rule is None or want_rule in r.rules_failed())
    check(name + (" -> %s" % want_rule if want_rule else ""), ok, [str(x) for x in r.errors][:4])
    return r


cases = [
    ("project_settings is a list", cfg_edit(lambda d: b"[1,2,3]"), "R15"),
    ("project_settings is null", cfg_edit(lambda d: b"null"), "R15"),
    ("project_settings is a string", cfg_edit(lambda d: b'"abc"'), "R15"),
    ("project_settings nested 100k deep", cfg_edit(lambda d: b"[" * 100000 + b"]" * 100000), "R15"),
    ("filament_colour has numbers", cfg_edit(jset("filament_colour", [1, 2])), "R15"),
    ("printer_settings_id is a list", cfg_edit(jset("printer_settings_id", ["a"])), None),
    ("print_settings_id missing", cfg_edit(jset("print_settings_id", delete=True)), None),
    ("printer_settings_id missing", cfg_edit(jset("printer_settings_id", delete=True)), None),
    ("custom G-code top_z nan", lambda n, d: d, "R32"),
    ("triangle index -1", lambda n, d: first_sub(n, d, lambda x: re.sub(rb'v1="\d+"', b'v1="-1"', x, count=1)), "R24"),
    ("triangle index too big", lambda n, d: first_sub(n, d, lambda x: re.sub(rb'v1="\d+"', b'v1="99999999999999999999"', x, count=1)), "R24"),
    ("vertex outside any object", lambda n, d: first_sub(n, d, lambda x: x.replace(b"<resources>", b'<resources><vertex x="1" y="1" z="1"/>', 1)), None),
]
for name, fn, rule in cases:
    extra = None
    if name == "custom G-code top_z nan":
        extra = [("Metadata/custom_gcode_per_layer.xml",
                  b'<?xml version="1.0" encoding="utf-8"?>\n<custom_gcodes_per_layer>\n<plate>\n<plate_info id="1"/>\n'
                  b'<layer top_z="nan" type="4" extra="M117" extruder="1" color="" gcode=""/>\n<mode value="SingleExtruder"/>\n</plate>\n</custom_gcodes_per_layer>\n')]
    run(name, tamper(fn, re.sub(r"\W+", "_", name) + ".3mf", extra), rule)

# zip bomb: a 1 GiB text entry compresses to about 1 MB. The guard must stop the read (fast, small memory).
bomb = os.path.join(tmp, "bomb.3mf")
with zipfile.ZipFile(good) as zin, zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zo:
    for i in zin.infolist():
        zo.writestr(i.filename, zin.read(i.filename))
    with zo.open("Metadata/notes.txt", "w") as f:
        chunk = b" " * (1024 ** 2)
        for _ in range(1024):
            f.write(chunk)
t0 = time.time()
r = run("1 GiB text entry in a 1 MB file", bomb, "G4")
check("bomb rejected in under 5 s", time.time() - t0 < 5, "%.1f s" % (time.time() - t0))

# G6 bypass: DOCTYPE after a long comment in a sub-model
def late_doctype(n, d):
    if not n.startswith("3D/Objects/"):
        return d
    head = b'<?xml version="1.0" encoding="UTF-8"?>\n<!--' + b"x" * 6000 + b'-->\n<!DOCTYPE lolz [<!ENTITY lol "lol">]>\n'
    return head + d.split(b"?>", 1)[1]
run("DOCTYPE after 6 KB in a sub-model", tamper(late_doctype, "late_doctype.3mf"), "G6")

# R33: a version number is not an IP address
r = validate.validate(tamper(cfg_edit(jset("version", "99.0.0.0")), "ver.3mf"), mode="strict", privacy=True)
check("version 99.0.0.0 is not reported as an IP", not [f for f in r.findings if "IP address" in f.msg], [str(x) for x in r.findings][:3])
# R27/R30: Unicode digits are not slot numbers
def arabic_digit(n, d):
    return d.replace(b'key="extruder" value="1"', 'key="extruder" value="١"'.encode(), 1) if n == "Metadata/model_settings.config" else d
run("Unicode digit as extruder", tamper(arabic_digit, "uni.3mf"), "R27")


# A sub-model below the general 1000:1 cap but far above what real mesh XML reaches (about 10:1): 64 MiB inside an XML comment,
# mostly spaces with random bytes, ratio about 400:1. It must stop at G4 at once, and nothing after the guard may read it.
def low_ratio_bomb():
    import random
    rnd = random.Random(7)
    blk = bytearray(b" " * (1 << 20))
    for i in range(0, len(blk), 400):
        blk[i] = rnd.choice(b"abcdefghijklmnopqrstuvwxyz")
    zin = zipfile.ZipFile(good)
    p = os.path.join(tmp, "bomb_sub_lowratio.3mf")
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zo:
        for i in zin.infolist():
            if i.filename == "3D/Objects/object_1.model":
                continue
            zo.writestr(i.filename, zin.read(i.filename))
        sub = zin.read("3D/Objects/object_1.model")
        k = sub.index(b"<model")
        with zo.open("3D/Objects/object_1.model", "w", force_zip64=True) as f:
            f.write(sub[:k] + b"<!--")
            for _ in range(64):
                f.write(bytes(blk))
            f.write(b"-->" + sub[k:])
    return p


p = low_ratio_bomb()
ratio = [i.file_size / i.compress_size for i in zipfile.ZipFile(p).infolist() if i.filename.startswith("3D/Objects/")][0]
t0 = time.time()
r = validate.validate(p, mode="strict", privacy=True)
dt = time.time() - t0
check("low-ratio sub-model bomb (%.0f:1) stops at G4" % ratio, "G4" in [f.rule for f in r.errors] and 100 < ratio < 1000, [str(f)[:80] for f in r.findings][:3])
check("low-ratio sub-model bomb: nothing else runs after G4 (only G4 findings)", {f.rule for f in r.findings} == {"G4"}, sorted({f.rule for f in r.findings}))
check("low-ratio sub-model bomb is refused in under 2 s (%.2f s)" % dt, dt < 2.0)
opened = []
_real_open = zipfile.ZipFile.open


def _spy(self, name, *a, **k):
    opened.append(name.filename if isinstance(name, zipfile.ZipInfo) else name)
    return _real_open(self, name, *a, **k)


zipfile.ZipFile.open = _spy
try:
    validate.validate(p, mode="strict", privacy=True)
finally:
    zipfile.ZipFile.open = _real_open
check("after G4 no entry of the archive is opened (stop, not just report)", opened == [], opened[:5])

print("\n%d failed" % len(fails))
sys.exit(1 if fails else 0)
