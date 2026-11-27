#!/usr/bin/env python3
"""1.3.0 forward-compatible mode: a new Studio release never stops a build before the builder is updated.
The installed Studio is faked (no Studio needed): same as supported, newer 2.x, newer major, older, between two supported versions,
--strict-version and strict_version = true, and a pinned version. Also patch mode and the validator on a file saved by a newer 2.x
with unknown keys (made from the sanitized real 2.9.1 save in tests/data, never a private file).
Run: python3 -I tests/test_forward_mode.py"""
import contextlib, io, json, os, sys, tempfile, zipfile
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import job as jobmod, package, patch, patch_job, patch_verify, studio_detect, versions  # noqa: E402
from bambu3mf.profiles import STUDIO_VERSIONS  # noqa: E402
import build_bambu_3mf as cli  # noqa: E402

fails = []
measured = 0


def check(name, cond, extra=""):
    global measured
    measured += 1
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)[:400]) if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


tmp = tempfile.mkdtemp()
MESH = os.path.join(H, "..", "examples", "data", "slab1.stl")
NEWEST = STUDIO_VERSIONS[-1]
OLDEST = STUDIO_VERSIONS[0]
NEWER_2X = "2.%d.0.10" % (int(NEWEST.split(".")[1]) + 1)          # always newer than the newest supported 2.x
NEWER_3X = "3.0.0.1"


def job_file(extra=""):
    p = os.path.join(tmp, "j%d.toml" % len(os.listdir(tmp)))
    open(p, "w").write('''%s
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"
[[filament]]
preset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"
color = "#FF0000"
[[object]]
name = "Block"
[[object.part]]
mesh = "%s"
filament = 1
''' % (extra, MESH))
    return p


@contextlib.contextmanager
def fake_studio(version, exe=None, slice_ok=True):
    old = (studio_detect.detect, studio_detect.find_cli, studio_detect.slice_details)
    studio_detect.detect = lambda: version
    studio_detect.find_cli = lambda override=None: (exe, "fake") if exe else (None, "no Bambu Studio program found (fake)")
    studio_detect.slice_details = lambda e, f, timeout=900: (slice_ok, "fake slice %s" % ("OK" if slice_ok else "failed"), [])
    try:
        yield
    finally:
        studio_detect.detect, studio_detect.find_cli, studio_detect.slice_details = old


def run_cli(args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = cli.main(args)
    return rc, out.getvalue(), err.getvalue()


# ---- A. versions.choose ------------------------------------------------------------------------------------------------------
S = STUDIO_VERSIONS
c = versions.choose("auto", NEWEST, S)
check("A1 installed = newest supported: builds for it, no warning", c.version == NEWEST and c.warning is None and c.mode == "exact")
c = versions.choose("auto", OLDEST, S)
check("A2 installed = oldest supported: builds for it, no warning", c.version == OLDEST and c.warning is None)
c = versions.choose("auto", NEWER_2X, S)
check("A3 newer 2.x: builds for the newest supported, with the forward warning", c.version == NEWEST and c.mode == "newer"
      and "Built for Studio %s. Your Studio is %s. Studio opens this as an older project and keeps all settings. A builder update may follow." % (NEWEST, NEWER_2X) == c.warning, c.warning)
check("A4 newer 2.x: no automatic slice check", c.auto_slice_check is False)
c = versions.choose("auto", NEWER_3X, S)
check("A5 newer major: builds for the newest supported with a stronger warning and an automatic slice check",
      c.version == NEWEST and c.mode == "newer-major" and c.auto_slice_check and "newer MAJOR" in c.warning and "geometry only" in c.warning, c.warning)
try:
    versions.choose("auto", "2.7.0.1", S); msg = None
except versions.VersionError as e:
    msg = str(e)
check("A6 older than supported: clear error that names the oldest version and the pin", msg and "older than the oldest supported" in msg and OLDEST in msg and "studio =" in msg, msg)
c = versions.choose(OLDEST, "2.7.0.1", S)
check("A7 older installed but the job pins a supported version: builds", c.version == OLDEST and c.mode == "pinned")
for det in (NEWER_2X, NEWER_3X):
    try:
        versions.choose("auto", det, S, strict=True); msg = None
    except versions.VersionError as e:
        msg = str(e)
    check("A8 strict mode keeps the 1.2.0 hard stop for %s" % det, msg and msg.startswith("Installed Bambu Studio is %s. Supported: " % det), msg)
c = versions.choose(NEWEST, NEWER_3X, S, strict=True)
check("A9 strict mode with a pinned version still builds", c.version == NEWEST)
c = versions.choose("auto", None, S)
check("A10 no Studio found: newest supported, a note and no warning", c.version == NEWEST and c.note and c.warning is None)
if len(S) >= 2:
    a, b = versions.vtuple(S[0]), versions.vtuple(S[1])
    between = "%d.%d.%d.%d" % (a[0], a[1], a[2], a[3] + 1)
    if versions.vtuple(between) < b:
        c = versions.choose("auto", between, S)
        check("A11 between two supported versions: the nearest one below, with a warning", c.version == S[0] and c.mode == "between" and c.warning, c.warning)
check("A12 file_choice: newer 2.x file uses the newest tables", versions.file_choice(NEWER_2X, S) == (NEWEST, "newer"))
check("A13 file_choice: newer major file gets no tables", versions.file_choice(NEWER_3X, S) == (None, None))
check("A14 file_choice: another build of a supported version", versions.file_choice(NEWEST.rsplit(".", 1)[0] + ".1", S) == (NEWEST, "build"))

# ---- B. load_job with a faked installed Studio -------------------------------------------------------------------------------
with fake_studio(NEWER_2X):
    j = jobmod.load_job(job_file())
    check("B1 newer 2.x: job loads for the newest supported", j.studio == NEWEST)
    check("B2 newer 2.x: the warning is in the job warnings (console and summary)", any("Built for Studio %s" % NEWEST in w for w in j.warnings), j.warnings)
    try:
        jobmod.load_job(job_file('strict_version = true')); msg = None
    except jobmod.JobError as e:
        msg = "; ".join(e.problems)
    check("B3 strict_version = true in the job: hard stop", msg and "Installed Bambu Studio is %s" % NEWER_2X in msg, msg)
    try:
        jobmod.load_job(job_file(), strict_version=True); msg = None
    except jobmod.JobError as e:
        msg = "; ".join(e.problems)
    check("B4 strict_version argument (the --strict-version flag): hard stop", msg and "Installed Bambu Studio is" in msg, msg)
    j = jobmod.load_job(job_file('studio = "%s"' % ".".join(OLDEST.split(".")[:3])))
    check("B5 pinned version builds for the pin, no warning", j.studio == OLDEST and not any("Built for Studio" in w for w in j.warnings))
    try:
        jobmod.load_job(job_file('strict_version = "yes"')); msg = None
    except jobmod.JobError as e:
        msg = "; ".join(e.problems)
    check("B6 strict_version must be a boolean", msg and "strict_version" in msg, msg)
with fake_studio("2.7.0.1"):
    try:
        jobmod.load_job(job_file()); msg = None
    except jobmod.JobError as e:
        msg = "; ".join(e.problems)
    check("B7 older installed Studio: clear error", msg and "older than the oldest supported" in msg, msg)

# ---- C. the command line ------------------------------------------------------------------------------------------------------
jp = job_file()
with fake_studio(NEWER_2X):
    rc, out, err = run_cli([jp, "--out", os.path.join(tmp, "c1.3mf")])
    check("C1 newer 2.x: exit 0", rc == 0, err)
    check("C2 newer 2.x: the warning is printed", "Built for Studio %s. Your Studio is %s" % (NEWEST, NEWER_2X) in out, out)
    md = open(os.path.join(tmp, "c1.summary.md")).read()
    check("C3 newer 2.x: the warning is in the summary file", "Built for Studio %s" % NEWEST in md)
    rc, out, err = run_cli([jp, "--strict-version", "--out", os.path.join(tmp, "c2.3mf")])
    check("C4 --strict-version: exit 1 with the hard stop", rc == 1 and "Installed Bambu Studio is %s" % NEWER_2X in err, (rc, err))
with fake_studio(NEWER_3X):
    rc, out, err = run_cli([jp, "--out", os.path.join(tmp, "c3.3mf")])
    check("C5 newer major, no Studio program found: exit 0, slice check reported as not run", rc == 0 and "slice check: not run" in out, (rc, out, err))
with fake_studio(NEWER_3X, exe="/fake/bambu-studio", slice_ok=True):
    rc, out, err = run_cli([jp, "--out", os.path.join(tmp, "c4.3mf")])
    check("C6 newer major with Studio: slice check runs automatically and passes", rc == 0 and "run automatically" in out and "fake slice OK" in out, (rc, out))
with fake_studio(NEWER_3X, exe="/fake/bambu-studio", slice_ok=False):
    rc, out, err = run_cli([jp, "--out", os.path.join(tmp, "c5.3mf")])
    check("C7 newer major with Studio: a failed automatic slice check is exit 1", rc == 1 and "fake slice failed" in out, (rc, out))
with fake_studio(NEWEST):
    rc, out, err = run_cli([jp, "--out", os.path.join(tmp, "c6.3mf")])
    check("C8 supported Studio: no forward warning", rc == 0 and "Built for Studio" not in out, out)
check("C9 the 3MF built under a newer 2.x is byte-identical to the one built for the newest supported version",
      open(os.path.join(tmp, "c1.3mf"), "rb").read() == open(os.path.join(tmp, "c6.3mf"), "rb").read())

# ---- D. patch mode and the validator on a file saved by a newer 2.x ---------------------------------------------------------
PS, MS, M3 = "Metadata/project_settings.config", "Metadata/model_settings.config", "3D/3dmodel.model"
src = os.path.join(H, "data", "four-color-autocalc-291.3mf")
newer = os.path.join(tmp, "saved-by-newer.3mf")
nv = versions.vtuple(NEWER_2X)
stamp = "%02d.%02d.%02d.%02d" % nv
with zipfile.ZipFile(src) as z, zipfile.ZipFile(newer, "w", zipfile.ZIP_DEFLATED) as zo:
    for i in z.infolist():
        data = z.read(i.filename)
        if i.filename == M3:
            data = data.replace(b"BambuStudio-02.09.01.70", ("BambuStudio-" + stamp).encode())
        elif i.filename == PS:
            cfg = json.loads(data.decode("utf-8"))
            cfg["version"] = stamp
            cfg["future_setting_from_newer_studio"] = "1"
            cfg = dict(sorted(cfg.items()))
            data = (json.dumps(cfg, indent=4, ensure_ascii=False) + "\n").encode("utf-8")
        elif i.filename == MS:
            data = data.replace(b'<metadata key="extruder" value="1"/>',
                                b'<metadata key="extruder" value="1"/>\n    <metadata key="future_object_key" value="1"/>', 1)
        zo.writestr(i, data)

d_def, d_strict = patch_verify.validate_both(newer)
errs = [f for f in d_def if f.level == "error"]
check("D1 validator: a newer-2.x file has no errors in default mode", not errs, ["%s %s" % (f.rule, f.msg[:120]) for f in errs])
check("D2 validator: the note says it is checked against the newest supported rules",
      any(f.rule == "R16" and "newer than the newest supported" in f.msg and NEWEST in f.msg for f in d_def), [f.msg[:100] for f in d_def if f.rule == "R16"])
check("D3 validator: the unknown project key is a warning", any("future_setting_from_newer_studio" in f.msg and f.level == "warning" for f in d_def))
check("D4 validator: the unknown object key is a warning", any("future_object_key" in f.msg and f.level == "warning" for f in d_def),
      [(f.rule, f.level, f.msg[:80]) for f in d_def])

jpp = os.path.join(tmp, "patch.toml")
outp = os.path.join(tmp, "patched.3mf")
open(jpp, "w").write('patch = "%s"\noutput = "%s"\n[settings]\nwall_loops = 5\n' % (newer, outp))
try:
    r = patch.run(patch_job.load_patch_job(jpp)); msg = None
except Exception as e:                                     # noqa: BLE001
    r, msg = None, "%s: %s" % (type(e).__name__, getattr(e, "problems", e))
check("D5 patch mode: a newer-2.x file is patched (no refusal)", r is not None, msg)
if r is not None:
    check("D6 patch mode: the summary warns that the file is from a newer Studio", any("newer than the newest supported" in w for w in r.warnings), r.warnings)
    rc, out, err = run_cli([jpp, "--out", os.path.join(tmp, "patched2.3mf")])
    check("D7 patch mode from the command line: exit 0", rc == 0, err[-400:])
    with zipfile.ZipFile(newer) as a, zipfile.ZipFile(outp) as b:
        cfg_out = json.loads(b.read(PS).decode("utf-8"))
        check("D8 the patched setting is set", cfg_out.get("wall_loops") == "5", cfg_out.get("wall_loops"))
        check("D9 the unknown key is kept as it is", cfg_out.get("future_setting_from_newer_studio") == "1")
        same = [n for n in a.namelist() if n not in (PS,) and n in b.namelist()]
        diff = [n for n in same if a.read(n) != b.read(n)]
        check("D10 every untouched entry is byte-identical (%d entries)" % len(same), same and not diff, diff)
    d_def2, _ = patch_verify.validate_both(outp)
    errs2 = [f for f in d_def2 if f.level == "error"]
    check("D11 the patched file validates with warnings only", not errs2, ["%s %s" % (f.rule, f.msg[:120]) for f in errs2])

too_new = os.path.join(tmp, "saved-by-3x.3mf")
with zipfile.ZipFile(newer) as z, zipfile.ZipFile(too_new, "w", zipfile.ZIP_DEFLATED) as zo:
    for i in z.infolist():
        data = z.read(i.filename)
        if i.filename in (M3, PS):
            data = data.replace(stamp.encode(), b"03.00.00.01")
        zo.writestr(i, data)
open(jpp, "w").write('patch = "%s"\noutput = "%s"\n[settings]\nwall_loops = 5\n' % (too_new, os.path.join(tmp, "p3.3mf")))
try:
    patch.run(patch_job.load_patch_job(jpp)); msg = None
except Exception as e:                                     # noqa: BLE001
    msg = "; ".join(getattr(e, "problems", [str(e)]))
check("D12 patch mode refuses a file from a newer MAJOR Studio, with the reason", bool(msg) and "3.0.0.1" in msg, msg)

print("\n%d checks measured, %d failed" % (measured, len(fails)))
if measured == 0:
    print("NOT CHECKED")
    sys.exit(1)
sys.exit(1 if fails else 0)
