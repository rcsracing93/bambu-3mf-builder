#!/usr/bin/env python3
"""Summary file (<name>.summary.md): written next to every built 3MF, matches the built config, lists every job override,
holds no absolute path; --no-summary writes none; Studio's time and grams are read from a G-code header.
Run: python3 -I tests/test_summary_file.py"""
import glob, os, re, shutil, subprocess, sys, tempfile
H = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(H)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf.job import load_job
from bambu3mf import package, summary, studio_detect, privacy

fails = []
def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)[:300]) if (extra and not cond) else ""))
    if not cond: fails.append(name)

ABS = re.compile(r"(^|[\s`(\"'=])(/[A-Za-z0-9_.~-]+){2,}|[A-Za-z]:\\")
CLI = os.path.join(ROOT, "scripts", "build_bambu_3mf.py")
tmp = tempfile.mkdtemp()
try:
    for ex in sorted(glob.glob(os.path.join(ROOT, "examples", "*.toml"))):
        name = os.path.splitext(os.path.basename(ex))[0]
        d = os.path.join(tmp, name); os.makedirs(d)
        out = os.path.join(d, name + ".3mf")
        r = subprocess.run([sys.executable, "-I", CLI, ex, "--out", out], capture_output=True, text=True)
        sp = os.path.join(d, name + ".summary.md")
        check(name + ": builds", r.returncode == 0, r.stderr)
        check(name + ": summary file exists", os.path.isfile(sp))
        if not os.path.isfile(sp):
            continue
        txt = open(sp, encoding="utf-8").read()
        job = load_job(ex)
        s = package.build(job, os.path.join(d, "again.3mf"))
        cfg = s.config
        for i, f in enumerate(job.filaments):
            check("%s: slot %d preset and color as built" % (name, i + 1),
                  cfg["filament_settings_id"][i] in txt and cfg["filament_colour"][i] in txt)
        check(name + ": printer, process, Studio version", job.printer in txt and job.process in txt and summary._studio_text(job.studio) in txt)
        check(name + ": plate type", (cfg.get("curr_bed_type") or "default") in txt)
        keys = set(job.settings)
        for f in job.filaments: keys |= set(f["settings"])
        for o in job.objects:
            keys |= set(o["settings"])
            for p in o["parts"]: keys |= set(p["settings"])
        miss = [k for k in sorted(keys) if "`%s`" % k not in txt]
        check(name + ": every override listed (%d)" % len(keys), not miss, miss)
        for g in job.layer_gcode:
            check(name + ": layer g-code z=%s" % g["z"], ("z %s mm" % summary._f(g["z"])) in txt)
        for w in s.warnings:
            check(name + ": warning listed", w in txt)
        check(name + ": no absolute path", not ABS.search(txt) and d not in txt and ROOT not in txt, ABS.search(txt))
        check(name + ": privacy scan clean", not privacy.scan_text(os.path.basename(sp), txt), privacy.scan_text(os.path.basename(sp), txt))
        check(name + ": summary name is the 3MF name", os.path.basename(sp) == os.path.basename(out)[:-4] + ".summary.md")

    # --no-summary
    d = os.path.join(tmp, "nosum"); os.makedirs(d)
    r = subprocess.run([sys.executable, "-I", CLI, os.path.join(ROOT, "examples", "single_p1s.toml"), "--out", os.path.join(d, "a.3mf"), "--no-summary"], capture_output=True, text=True)
    check("--no-summary: 3MF built, no summary written", r.returncode == 0 and os.path.isfile(os.path.join(d, "a.3mf")) and not glob.glob(os.path.join(d, "*.md")), r.stderr)

    # Studio time and grams from a G-code header, and the slice section
    gc = os.path.join(tmp, "plate_1.gcode")
    open(gc, "w").write("; HEADER_BLOCK_START\n; model printing time: 1h 2m 3s; total estimated time: 1h 5m 10s\n; total filament weight [g] : 12.34\n; HEADER_BLOCK_END\nG28\n")
    check("G-code header: time and grams", studio_detect.read_gcode_header(gc) == ("1h 5m 10s", 12.34), studio_detect.read_gcode_header(gc))
    check("G-code header: missing file gives None", studio_detect.read_gcode_header(os.path.join(tmp, "none")) == (None, None))
    job = load_job(os.path.join(ROOT, "examples", "single_p1s.toml"))
    s = package.build(job, os.path.join(tmp, "x.3mf"))
    md = summary.format_summary_md(job, s, (True, "Studio sliced it: 1 plate(s), no error", [{"plate": 1, "time": "1h 5m 10s", "grams": 12.34}]))
    check("slice section: time and grams", "plate 1: print time 1h 5m 10s, filament 12.34 g" in md)
    check("no slice section without --slice-check", "slice check" not in summary.format_summary_md(job, s).lower())
    # slice_check keeps its two values
    plain = os.path.join(tmp, "plain.sh"); open(plain, "w").write("#!/bin/sh\nexit 0\n"); os.chmod(plain, 0o755)
    res = studio_detect.slice_check(plain, os.path.join(tmp, "x.3mf"), timeout=20)
    check("slice_check still returns (ok, message)", len(res) == 2 and res[0] is False)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
