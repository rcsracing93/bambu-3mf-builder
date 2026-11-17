#!/usr/bin/env python3
"""Real-slicer test: build every example job, slice it in a real Bambu Studio, grade the result.

Usage:
  python3 tests/studio_slice.py --studio-dir DIR --version 2.8.2 [--jobs examples/*.toml]
                                [--report report.json] [--other-studio-dir DIR2 --other-version 2.9.1]

DIR is an extracted AppImage (the folder that holds bin/bambu-studio) or any folder with bin/bambu-studio.
Checks per job (names follow the design review):
  T3  plain slice returns Success and one G-code file per plate
  T4  slice with --uptodate returns Success and one G-code file per plate
  T5  G-code config block carries the job intent (settings the builder wrote)
  T6  plain and --uptodate config blocks are equal
  T7  filaments used in the result are exactly the filament slots the job uses
  T8  multi-colour: tool changes and prime tower present; single colour: no prime tower
  T9  log scan: no [error] or [warning] lines
  T10 temperature tower / pause: the change sits on the layer the job asked for
  T11 re-export (--export-3mf) validates, and re-slicing the export succeeds
  T12 (needs --other-studio-dir) the file built for version N slices in the other Studio
  T13 the real prime tower does not overlap the printed model; T14 it sits inside the box R37 reserves
  Expectations of intent come from the job FILE (raw TOML) and the meshes; T5 and T14 compare the written file with the G-code.
  T5b the settings, colors and presets the job file asks for are in the G-code with the job's value; T10p pauses sit on the layer asked for
"""
import argparse, glob, json, os, re, shutil, subprocess, sys, tempfile, time, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from bambu3mf import package, job as jobmod, validate, wipetower, mesh as meshmod  # noqa: E402
sys.path.insert(0, HERE)
import harness as H  # noqa: E402
tomllib = jobmod.tomllib



def run_studio(sdir, args, workdir, timeout=600):
    sdir = os.path.abspath(sdir)                                 # Studio runs in workdir, so a relative path would not resolve
    exe = os.path.join(sdir, "bin", "bambu-studio")
    env = dict(os.environ, LD_LIBRARY_PATH=os.path.join(sdir, "bin"), LC_ALL="C")
    cmd = [exe] + args
    if not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
        cmd = ["xvfb-run", "-a"] + cmd
    t0 = time.time()
    p = subprocess.run(cmd, cwd=workdir, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=timeout)
    out = p.stdout.decode("utf-8", "replace")
    if p.returncode == 127 and not _START_FAIL:                # the program did not start: show why, once
        _START_FAIL.append(1)
        print("Studio did not start (exit 127). Its output:\n" + out[-2000:])
    return p.returncode, out, time.time() - t0


_START_FAIL = []


def read_result(outdir):
    try:
        return json.load(open(os.path.join(outdir, "result.json")))
    except Exception:
        return None







def raw_job(path):
    if path.endswith(".json"):
        return json.load(open(path))
    return tomllib.load(open(path, "rb"))


def expected_slots(raw):
    by_plate = {}
    for o in raw.get("object", []):
        pl = int(o.get("plate", 1))
        for p in o.get("part", []):
            if p.get("type", "normal_part") == "normal_part":
                by_plate.setdefault(pl, set()).add(int(p.get("filament", 1)))
    return by_plate


class Report:
    def __init__(self):
        self.rows = []

    def add(self, job, test, ok, detail=""):
        self.rows.append({"job": job, "test": test, "ok": bool(ok), "detail": detail})
        print("  %-4s %-5s %s" % (test, "PASS" if ok else "FAIL", detail))


def slice_once(sdir, f3mf, outdir, uptodate=False):
    os.makedirs(outdir, exist_ok=True)
    args = ["--debug", "2", "--slice", "0", "--outputdir", outdir]
    if uptodate:
        args.insert(0, "--uptodate")
    args.append(f3mf)
    rc, log, secs = run_studio(sdir, args, os.path.dirname(f3mf))
    return rc, log, secs, read_result(outdir)


def gcode_files(outdir):
    return sorted(glob.glob(os.path.join(outdir, "plate_*.gcode")))


def retarget(jobfile, build_version, work):
    """Copy a TOML job into work with its studio line replaced and mesh paths made absolute."""
    base = os.path.dirname(os.path.abspath(jobfile))
    txt = open(jobfile).read()
    txt = re.sub(r'(?m)^studio\s*=\s*"[^"]*"', 'studio  = "%s"' % build_version, txt)
    txt = re.sub(r'mesh\s*=\s*"([^"/][^"]*)"', lambda m: 'mesh = "%s"' % os.path.join(base, m.group(1)), txt)
    out = os.path.join(work, os.path.basename(jobfile))
    open(out, "w").write(txt)
    return out


def check_job(jobfile, sdir, version, work, rep, other=None, build_version=None, tamper=None):
    """Build, slice and grade one job. Expectations come from the job file and the mesh files (tests/harness.py), never from the
    builder's own output. `tamper(path)` may change the built 3MF before it is sliced (used by the mutation test)."""
    name = os.path.splitext(os.path.basename(jobfile))[0]
    print("== %s (Studio %s%s)" % (name, version, ", file built for %s" % build_version if build_version else ""))
    if build_version:
        jobfile = retarget(jobfile, build_version, work)
    raw = raw_job(jobfile)
    f3 = os.path.join(work, name + ".3mf")
    try:
        job = jobmod.load_job(jobfile)
        summ = package.build(job, f3)
    except Exception as e:  # noqa
        rep.add(name, "T1", False, "build failed: %s" % e)
        return
    read_mesh = meshmod.read_mesh
    if (raw.get("temperature_tower") or {}).get("shape") == "calibration":     # the builder made the mesh: grade against the loaded job's parts
        gen = {p["mesh"]: p["mesh_obj"] for o in job.objects for p in o["parts"]}
        raw["object"] = [{"name": o["name"], "plate": o["plate"], "part": [{"mesh": p["mesh"], "type": p["type"], "filament": p["filament"]} for p in o["parts"]]}
                         for o in job.objects]
        read_mesh = lambda path: gen[os.path.basename(path)]                  # noqa: E731
    if tamper:
        tamper(f3)
    findings = validate.validate(f3, mode="strict", privacy=True).findings
    errors = [f for f in findings if f.level == "error"]
    rep.add(name, "T2", not findings, "validator findings: %d%s" % (len(findings), (": " + "; ".join("%s %s" % (f.rule, f.msg[:60]) for f in findings[:3])) if findings else ""))
    nplates = H.job_plates(raw)                     # from the job file, not from the builder
    n = len(job.filaments)
    root = os.path.dirname(os.path.abspath(jobfile))

    out1, out2 = os.path.join(work, name + ".plain"), os.path.join(work, name + ".uptodate")
    rc1, log1, s1, res1 = slice_once(sdir, f3, out1)
    g_all = gcode_files(out1)
    ok1 = rc1 == 0 and res1 and res1.get("return_code") == 0 and len(g_all) == nplates
    rep.add(name, "T3", ok1, "rc=%s result=%s gcode=%d plates=%d %.0fs" % (
        rc1, res1 and res1.get("error_string"), len(g_all), nplates, s1))
    rc2, log2, s2, res2 = slice_once(sdir, f3, out2, uptodate=True)
    g_all2 = gcode_files(out2)
    ok2 = rc2 == 0 and res2 and res2.get("return_code") == 0 and len(g_all2) == nplates
    rep.add(name, "T4", ok2, "rc=%s result=%s gcode=%d %.0fs" % (rc2, res2 and res2.get("error_string"), len(g_all2), s2))
    # T9 first: a log problem is worth knowing even when the slice failed
    noisy = H.scan_log(log1) + H.scan_log(log2)
    rep.add(name, "T9", not noisy, "%d unexpected log lines%s" % (len(noisy), (": " + noisy[0][-110:]) if noisy else ""))
    if not ok1:
        return

    blk1 = H.config_block(g_all[0])
    differ, missing = H.compare_config(summ.config, blk1, job.profiles.options, n, version)
    rep.add(name, "T5", not differ and not missing, "written config vs G-code: %d differ, %d missing%s" % (
        len(differ), len(missing), (": " + "; ".join(differ[:3] + missing[:3])) if differ or missing else ""))
    intent = H.job_intent(raw, job, blk1, n)
    rep.add(name, "T5b", not intent, "job settings vs G-code: %d wrong%s" % (len(intent), (": " + "; ".join(intent[:3])) if intent else ""))
    if ok2:
        blk2 = H.config_block(g_all2[0])
        diff = sorted(set(blk1) ^ set(blk2)) + [k for k in blk1 if k in blk2 and blk1[k] != blk2[k]]
        rep.add(name, "T6", not diff, "%d keys differ between plain and --uptodate%s" % (
            len(diff), (": " + ", ".join(diff[:6])) if diff else ""))

    # T7 / T8 / T13: per plate, from the job and the mesh files
    parsed = {}
    for k in range(1, nplates + 1):
        gp = os.path.join(out1, "plate_%d.gcode" % k)
        parsed[k] = H.parse_body(gp)
    ids = {i + 1: (job.profiles.resolve("filament", f["preset"])[1] or {}).get("filament_id") for i, f in enumerate(raw.get("filament", []))}
    t7, t8, t13 = [], [], []
    for k, sp in enumerate(res1.get("sliced_plates", []), 1):
        ranges = H.part_ranges(raw, root, read_mesh, k)
        slots = {s for s, _a, _b in ranges}
        ok, d = H.check_filaments_result(sp, slots, ids)
        ok2_, d2 = H.check_layer_tools(parsed[k]["layers"], ranges)
        t7.append((ok and ok2_, "plate %d: %s; %s" % (k, d, d2)))
        ok, d = H.check_tower(sp, len(slots))
        t8.append((ok, "plate %d: %s" % (k, d)))
        if len(slots) > 1:
            ok, d = H.check_tower_clear(parsed[k]["tower"], parsed[k]["layers"])
            t13.append((ok, "plate %d: %s" % (k, d)))
    rep.add(name, "T7", all(x for x, _ in t7) and len(t7) == nplates, " | ".join(d for _, d in t7))
    rep.add(name, "T8", all(x for x, _ in t8) and len(t8) == nplates, " | ".join(d for _, d in t8))
    if t13:
        rep.add(name, "T13", all(x for x, _ in t13), " | ".join(d for _, d in t13))
    # T14: the real tower sits inside the box R37 reserves (and so cannot be on a part)
    for k in range(1, nplates + 1):
        ext = parsed[k]["tower"]
        if ext and len({s for s, _a, _b in H.part_ranges(raw, root, read_mesh, k)}) > 1:
            boxes = [b for b in wipetower.tower_boxes(summ.config, len(summ.config["filament_colour"]), summ.max_height, package.bed_rect(
                job.profiles.resolve("machine", raw["printer"])[0]), k) if b[4] == "file"]
            bx = boxes[0] if boxes else None
            ok = bool(bx) and ext[0] >= bx[0] - 1 and ext[1] >= bx[1] - 1 and ext[2] <= bx[2] + 1 and ext[3] <= bx[3] + 1
            rep.add(name, "T14", ok, "plate %d real tower %s inside reserved box %s" % (
                k, tuple(round(v, 1) for v in ext), tuple(round(v, 1) for v in bx[:4]) if bx else None))

    # T10
    if raw.get("temperature_tower"):
        tw = raw["temperature_tower"]
        if tw.get("shape") == "calibration":
            want = [(g["z"], g["value"]) for g in job.layer_gcode if g.get("tower")]
        else:
            want = [(tw["first_z"] + i * tw["step_height"], tw["start"] + i * tw["step"]) for i in range(int(tw["steps"]))]
        ok, d = H.check_temps(parsed[int(tw.get("plate", 1))], want)
        rep.add(name, "T10", ok, d)
    pz = {}
    for g in raw.get("layer_gcode", []):
        if "pause" in g:
            pz.setdefault(int(g.get("plate", 1)), []).append(g["z"])
    if raw.get("layer_gcode"):
        res = [H.check_pauses(parsed[k], pz.get(k, [])) for k in range(1, nplates + 1)]
        rep.add(name, "T10p", all(x for x, _ in res), " | ".join("plate %d: %s" % (k, d) for k, (_x, d) in enumerate(res, 1)))

    # T11 re-export: what Studio itself saves must still hold the job
    exp3 = os.path.join(work, name + ".reexport.3mf")
    rc3, log3, _ = run_studio(sdir, ["--export-3mf", exp3, f3], work)
    ok = rc3 == 0 and os.path.exists(exp3) and zipfile.is_zipfile(exp3)
    detail = "export rc=%s" % rc3
    if ok:
        tri = {}
        for i, o in enumerate(job.objects, 1):
            for j, pe in enumerate(o["parts"], 1):
                tri[(i, j)] = (pe.get("mesh_obj") or meshmod.read_mesh(pe["mesh"])).n_triangles
        ok, d = H.check_export(H.read_export(exp3), job, tri)
        detail += ", " + d
        if ok:
            rcs, logs, _, ress = slice_once(sdir, exp3, os.path.join(work, name + ".rx2"))
            ok = rcs == 0 and ress and ress.get("return_code") == 0
            detail += ", reslice rc=%s" % rcs
    rep.add(name, "T11", ok, detail)

    # T12 other Studio: same checks that need no second run configuration
    if other:
        osd, over = other
        outo = os.path.join(work, name + ".other")
        rco, logo, so, reso = slice_once(osd, f3, outo)
        ok = rco == 0 and reso and reso.get("return_code") == 0 and len(gcode_files(outo)) == nplates
        detail = "file built for %s sliced in %s: rc=%s %s" % (job.studio, over, rco, reso and reso.get("error_string"))
        if ok:
            res_o = [H.check_filaments_result(sp, {s for s, _a, _b in H.part_ranges(raw, root, read_mesh, k)}, ids)
                     for k, sp in enumerate(reso.get("sliced_plates", []), 1)]
            ok = all(x for x, _ in res_o) and len(res_o) == nplates
            detail += "; " + " | ".join(d for _, d in res_o)
        rep.add(name, "T12", ok, detail)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--studio-dir", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--other-studio-dir")
    ap.add_argument("--other-version")
    ap.add_argument("--jobs", nargs="*")
    ap.add_argument("--build-version", help="build every job for this Studio version (2.8.2 or 2.9.1) instead of the job's own")
    ap.add_argument("--report")
    ap.add_argument("--work")
    a = ap.parse_args()
    jobs = a.jobs or sorted(glob.glob(os.path.join(ROOT, "examples", "*.toml")))
    work = a.work or tempfile.mkdtemp(prefix="studio_slice_")
    os.makedirs(work, exist_ok=True)
    rep = Report()
    other = (a.other_studio_dir, a.other_version) if a.other_studio_dir else None
    for j in jobs:
        check_job(j, a.studio_dir, a.version, work, rep, other, a.build_version)
    bad = [r for r in rep.rows if not r["ok"]]
    print("\n%d checks, %d failed" % (len(rep.rows), len(bad)))
    if a.report:
        json.dump({"studio": a.version, "build": a.build_version or "job default (2.8.2)", "rows": rep.rows}, open(a.report, "w"), indent=1)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
