#!/usr/bin/env python3
"""Patch mode on a file saved by the REAL installed Studio (run on a computer with Bambu Studio installed; no GUI needed).
  1. Build examples/colors_4.toml for the newest supported Studio version.
  2. The installed Studio loads it and saves it (--export-3mf): a real Studio save. Graded: Application and project version are the
     installed Studio, the privacy scan (R33) is clean.
  3. Patch the Studio save with the command line: a colour, wall_loops, the plate type, and seam_embedded_headroom (a 2.9.2 key).
     Graded: exit 0, the patch reports each change and no refusal, strict validation exit 0, the geometry entries are byte-identical
     to the Studio save.
  4. Slice the patched file in a new, empty folder (same freshness checks as tests/forward_check.py) and find each change in the
     G-code config block.
  5. Studio saves the patched file again; it validates (strict) and slices.
Usage: python3 tests/patch_roundtrip_check.py [--studio-path PATH] [--report FILE]
Exit 0 when every check passed and at least one was measured."""
import json, os, re, shutil, subprocess, sys, tempfile, zipfile

H = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(H, ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, H)
from bambu3mf import studio_detect, validate  # noqa: E402
from bambu3mf.profiles import STUDIO_VERSIONS  # noqa: E402
from compare_release import job_copy  # noqa: E402
from forward_check import fresh_slice, sha_size  # noqa: E402
import harness  # noqa: E402

lines, fails = [], []
measured = 0


def check(name, cond, detail=""):
    global measured
    measured += 1
    lines.append("%s %s%s" % ("PASS" if cond else "FAIL", name, (" - " + str(detail)[:400]) if (detail and not cond) else ""))
    if not cond:
        fails.append(name)


def app_and_version(path):
    z = zipfile.ZipFile(path)
    m = re.search(r'<metadata name="Application">([^<]*)<', z.read("3D/3dmodel.model").decode("utf-8", "replace"))
    cfg = json.loads(z.read("Metadata/project_settings.config"))
    return (m.group(1) if m else None), cfg.get("version"), cfg


def main(argv):
    report = argv[argv.index("--report") + 1] if "--report" in argv else None
    spath = argv[argv.index("--studio-path") + 1] if "--studio-path" in argv else None
    exe, how = studio_detect.find_cli(spath)
    det = studio_detect.detect()
    newest = STUDIO_VERSIONS[-1]
    lines.append("Installed Studio: %s. Program: %s. Build target: %s." % (det, exe or "not found (%s)" % how, newest))
    if not exe:
        lines.append("NOT CHECKED")
        print("\n".join(lines))
        return 1
    tmp = tempfile.mkdtemp(prefix="patch_rt_")
    env = dict(os.environ, SOURCE_DATE_EPOCH="1700000000")
    py = [sys.executable, "-I", os.path.join(ROOT, "scripts", "build_bambu_3mf.py")]
    # 1. build
    j = job_copy(ROOT, "colors_4.toml", ".".join(newest.split(".")[:3]))
    built = os.path.join(tmp, "built.3mf")
    try:
        r = subprocess.run(py + [j, "--out", built, "--no-summary"], capture_output=True, text=True, env=env)
    finally:
        os.remove(j)
    check("1 build colors_4 for Studio %s" % newest, r.returncode == 0 and os.path.exists(built), r.stderr[-300:])
    # 2. real Studio save
    saved = os.path.join(tmp, "studio_saved.3mf")
    # Studio writes result.json into its working folder: run it in the temp folder
    p = subprocess.run([exe, "--export-3mf", saved, built], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tmp)
    check("2 Studio saves the file (--export-3mf)", p.returncode == 0 and zipfile.is_zipfile(saved), "exit %d" % p.returncode)
    if not os.path.exists(saved):
        return finish(report, tmp)
    app, ver, _cfg = app_and_version(saved)
    want = "BambuStudio-" + ".".join("%02d" % int(x) for x in det.split(".")) if det else None
    check("2 the save is from the installed Studio: Application %s, project version %s" % (app, ver), app == want and ver == want.split("-", 1)[1], want)
    priv = [f for f in validate.validate(saved, privacy=True).findings if f.rule == "R33"]
    check("2 privacy scan of the Studio save: no R33 finding", not priv, [f.msg[:80] for f in priv])
    # 3. patch
    pj = os.path.join(tmp, "patch.toml")
    out = os.path.join(tmp, "patched.3mf")
    with open(pj, "w") as fh:
        fh.write('patch = "%s"\noutput = "%s"\nplate_type = "Engineering Plate"\n[settings]\nwall_loops = 4\nseam_embedded_headroom = 5\n'
                 '[[filament]]\nslot = 2\ncolor = "#00A0FF"\n' % (saved, out))
    r = subprocess.run(py + [pj], capture_output=True, text=True, env=env)
    check("3 patch exit 0", r.returncode == 0 and os.path.exists(out), (r.stdout + r.stderr)[-400:])
    for word in ("wall_loops", "seam_embedded_headroom", "Engineering Plate", "#00A0FF"):
        check("3 patch summary names the change %s" % word, word.lower() in r.stdout.lower(), r.stdout[-300:])
    if not os.path.exists(out):
        return finish(report, tmp)
    v = validate.validate(out, mode="strict")
    check("3 strict validation of the patched file: 0 findings", v.ok and not v.findings, [(f.rule, f.msg[:80]) for f in v.findings])
    zs, zo = zipfile.ZipFile(saved), zipfile.ZipFile(out)
    geo = [n for n in zs.namelist() if n.startswith("3D/")]
    same = [n for n in geo if n in zo.namelist() and zs.read(n) == zo.read(n)]
    check("3 geometry entries byte-identical to the Studio save (%d of %d)" % (len(same), len(geo)), geo and len(same) == len(geo))
    # 4. slice the patched file
    fs = fresh_slice(exe, out, os.path.join(tmp, "slice_patched"))
    sha, size = sha_size(fs["gcodes"]) if fs["gcodes"] else ("-", 0)
    check("4 fresh slice of the patched file (%.2f s wall, %.2f s CPU, %d B, %s)" % (fs["wall"], fs["cpu"], size, sha[:16]), not fs["problems"], "; ".join(fs["problems"]))
    if fs["gcodes"]:
        blk = harness.config_block(fs["gcodes"][0])
        for k, want_v in (("wall_loops", "4"), ("seam_embedded_headroom", "5"), ("curr_bed_type", "Engineering Plate")):
            check("4 G-code config %s = %s" % (k, want_v), str(blk.get(k)) == want_v, blk.get(k))
        cols = str(blk.get("filament_colour", "")).upper()
        check("4 G-code filament_colour slot 2 is #00A0FF", cols.split(";")[1:2] == ["#00A0FF"], cols)
        loads = []
        with open(fs["gcodes"][0], encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = re.match(r"M620 S(\d+)A", line)
                if m and int(m.group(1)) < 255:
                    loads.append(int(m.group(1)))
        check("4 tool loads still T0-T3 in order", loads == [0, 1, 2, 3], loads)
    # 5. second Studio save of the patched file
    saved2 = os.path.join(tmp, "studio_saved2.3mf")
    p = subprocess.run([exe, "--export-3mf", saved2, out], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=tmp)
    ok = p.returncode == 0 and zipfile.is_zipfile(saved2)
    check("5 Studio saves the patched file again", ok, "exit %d" % p.returncode)
    if ok:
        v2 = validate.validate(saved2, mode="strict")
        errs = [f for f in v2.findings if f.level == "error"]
        check("5 the second save validates (strict, 0 errors)", not errs, [(f.rule, f.msg[:80]) for f in errs])
        _a, _v, cfg2 = app_and_version(saved2)
        check("5 the second save keeps wall_loops 4 and seam_embedded_headroom 5", cfg2.get("wall_loops") == "4" and cfg2.get("seam_embedded_headroom") == "5",
              (cfg2.get("wall_loops"), cfg2.get("seam_embedded_headroom")))
        fs2 = fresh_slice(exe, saved2, os.path.join(tmp, "slice_saved2"))
        check("5 fresh slice of the second save", not fs2["problems"], "; ".join(fs2["problems"]))
    return finish(report, tmp)


def finish(report, tmp):
    lines.append("")
    lines.append("%d checks measured, %d failed" % (measured, len(fails)))
    if measured == 0:
        lines.append("NOT CHECKED")
    text = "\n".join(lines) + "\n"
    print(text, end="")
    if report:
        with open(report, "w") as fh:
            fh.write(text)
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if measured and not fails else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
