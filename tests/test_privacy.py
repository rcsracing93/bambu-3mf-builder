#!/usr/bin/env python3
"""CI privacy gate (R33). Fails if any fixture or any builder output carries personal data.
Checks: every .3mf under tests/ ; builds every job in tests/jobs and scans the output ; scans text fixtures
(.toml, .stl, .obj) for paths, e-mail and IP addresses ; teeth: a file with DesignerUserId must be caught.
Run: python3 -I tests/test_privacy.py"""
import glob, io, os, re, shutil, sys, tempfile, zipfile
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import privacy, validate, package, summary
from bambu3mf.job import load_job

def _hit(p):          # a privacy finding is a warning (class PRIVACY): it never makes the file fail
    r = validate.validate(p, mode="strict", privacy=True)
    return any(f.rule == "R33" and f.level == "warning" for f in r.findings)

fails = []
def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)) if (extra and not cond) else ""))
    if not cond: fails.append(name)

# 1. every committed 3MF
for f in sorted(glob.glob(os.path.join(H, "**", "*.3mf"), recursive=True)):
    left = privacy.scan_zip(f)
    check("fixture clean: " + os.path.relpath(f, H), not left, left)
    rep = validate.validate(f, mode="strict", privacy=True)
    check("fixture R33 clean: " + os.path.relpath(f, H), not [f for f in rep.findings if f.rule == "R33"], [str(x) for x in rep.findings])

# 1b. committed summary files
for f in sorted(glob.glob(os.path.join(H, "**", "*.summary.md"), recursive=True) + glob.glob(os.path.join(H, "..", "docs", "**", "*.summary.md"), recursive=True)):
    found = privacy.scan_text(os.path.basename(f), open(f, encoding="utf-8").read())
    check("summary fixture clean: " + os.path.basename(f), not found, found)

# 2. job files and meshes
for f in sorted(glob.glob(os.path.join(H, "jobs", "*.toml")) + glob.glob(os.path.join(H, "data", "*.stl")) + glob.glob(os.path.join(H, "data", "*.obj"))):
    txt = open(f, "rb").read().decode("utf-8", "replace") if not f.endswith(".stl") else open(f, "rb").read(200).decode("latin-1")
    found = privacy.scan_text(os.path.basename(f), txt)
    check("text fixture clean: " + os.path.basename(f), not found, found)

# 3. builder output for every job that builds (the 3MF and its summary file)
tmp = tempfile.mkdtemp()
try:
    for f in sorted(glob.glob(os.path.join(H, "jobs", "*.toml"))):
        if os.path.basename(f).startswith(("bad", "_")):
            continue
        out = os.path.join(tmp, os.path.basename(f) + ".3mf")
        try:
            _job = load_job(f)
            _s = package.build(_job, out)
            _sp = summary.write_summary_file(_job, _s)
        except Exception as e:
            check("builds: " + os.path.basename(f), False, e); continue
        check("output clean: " + os.path.basename(f), not privacy.scan_zip(out), privacy.scan_zip(out))
        rep = validate.validate(out, mode="strict", privacy=True)
        _md = open(_sp, encoding="utf-8").read()
        check("summary file clean: " + os.path.basename(f), not privacy.scan_text(os.path.basename(_sp), _md) and tmp not in _md
              and not re.search(r"(^|[\s`(\"'=])(/[A-Za-z0-9_.~-]+){2,}|[A-Za-z]:\\", _md), privacy.scan_text(os.path.basename(_sp), _md))
        check("output validates: " + os.path.basename(f), rep.ok, [str(x) for x in rep.errors])

    # 4. teeth: each of these must be caught
    src = os.path.join(H, "data", "four-color-autocalc-282.3mf")
    def tamper(fn, name):
        zin = zipfile.ZipFile(src); p = os.path.join(tmp, name)
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zo:
            for i in zin.infolist():
                d = fn(i.filename, zin.read(i.filename))
                if d is not None: zo.writestr(i.filename, d)
        return p
    p = tamper(lambda n, d: d.replace(b'<metadata name="DesignerUserId"></metadata>', b'<metadata name="DesignerUserId">12505641</metadata>') if n == "3D/3dmodel.model" else d, "t1.3mf")
    check("teeth: DesignerUserId caught", _hit(p))
    p = tamper(lambda n, d: d.replace(b'key="name" value="Cube"/>', b'key="source_file" value="/Users/someone/x.stl"/>') if n == "Metadata/model_settings.config" else d, "t2.3mf")
    check("teeth: absolute path caught", _hit(p))
    p = tamper(lambda n, d: d.replace(b'"printhost_authorization_type": "key"', b'"printhost_authorization_type": "key",\n    "print_host": "192.168.1.50"') if n == "Metadata/project_settings.config" else d, "t3.3mf")
    check("teeth: print host caught", _hit(p))
    p = tamper(lambda n, d: d.replace(b'<metadata name="Title"></metadata>', b'<metadata name="Title"></metadata>\n <metadata name="Thumbnail_Middle">/Metadata/plate_1.png</metadata>') if n == "3D/3dmodel.model" else d, "t4.3mf")
    check("teeth: dangling thumbnail caught (class FORM: error in strict)", "R33" in validate.validate(p, mode="strict", privacy=True).rules_failed())
    _bad = privacy.scan_text("x.summary.md", "Built /Users/someone/x.3mf from 192.168.1.50")
    check("teeth: summary text with a path or IP address is caught", bool(_bad), _bad)
    check("teeth: dangling thumbnail is not reported in default mode", not [f for f in validate.validate(p).findings if f.rule == "R33"])
finally:
    shutil.rmtree(tmp, ignore_errors=True)
# nit: every key in HOST_KEYS is a real setting of both Studio tags (no invented key)
import json
for _v in ("2.8.2.61", "2.9.1.70"):
    _keys = {o["key"] for o in json.load(open(os.path.join(H, "..", "studio", _v, "options.json")))["options"]}
    check("HOST_KEYS exist in Studio %s" % _v, all(k in _keys for k in privacy.HOST_KEYS), [k for k in privacy.HOST_KEYS if k not in _keys])
print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
