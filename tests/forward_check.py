#!/usr/bin/env python3
"""Forward-compatible mode with the REAL installed Studio (run on a computer with Bambu Studio installed).
Every example is built with studio = "auto" and --slice-check, then sliced again by this script in a new, empty folder. Graded per example:
  - build exit 0, the 3MF exists, the builder's own slice check passed,
  - when the installed Studio is newer than the newest supported version: the forward warning is printed and is in the summary file;
    when it is supported exactly: no forward warning. The built file's project version is the version the build chose,
  - fresh slice: the output folder is new and empty before Studio starts; Studio exits 0; result.json says return_code 0;
    result.json and every G-code file are newer than the start of the run; at least one plate_N.gcode is made,
  - the wall-clock time of the whole Studio process (launch to exit), its own CPU time (a hand-off to another Studio would use almost
    none), the G-code size in bytes and the first 16 hex of its SHA-256,
  - content: a temperature tower has each step temperature (M104) on its layer; a pause sits on the job's layer; a multi-colour job
    loads exactly the tools the job asks for, in order (M620 S<n>A, one load per stacked part); no two examples give the same G-code.
Expectations come from the job FILE and the mesh files (tests/harness.py), never from the builder's output.
Usage: python3 tests/forward_check.py [--studio-path PATH] [--report FILE] [--only name,name] [-j N]
-j N runs N examples at a time (default: the CPU count); the report keeps the example order.
Exit 0 when every example passed and at least one was checked."""
import glob, hashlib, json, os, re, shutil, subprocess, sys, tempfile, threading, time, zipfile

H = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(H, ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, H)
from bambu3mf import studio_detect, versions, job as jobmod, mesh as meshmod  # noqa: E402
from bambu3mf.profiles import STUDIO_VERSIONS  # noqa: E402
from compare_release import job_copy, needs, pinned  # noqa: E402
import harness  # noqa: E402
import pool  # noqa: E402

tomllib = jobmod.tomllib


def fresh_slice(exe, f3mf, outdir, timeout=900):
    """Slice in a folder that must not exist yet. Returns a dict with the facts; 'problems' lists each failed freshness check."""
    probs = []
    if os.path.exists(outdir):
        probs.append("output folder existed before the run")
    os.makedirs(outdir)
    if os.listdir(outdir):
        probs.append("output folder not empty before the run")
    t0 = time.time()
    try:
        # Studio's console output goes to a log next to the folder; os.wait4 gives the CPU time of this one child, so the
        # number stays right when several slices run at once (shows the process sliced, not a hand-off)
        with open(outdir + ".log", "wb") as log:
            p = subprocess.Popen([exe, "--slice", "0", "--outputdir", outdir, os.path.abspath(f3mf)], stdout=log, stderr=subprocess.STDOUT,
                                 cwd=os.path.dirname(outdir))
            timer = threading.Timer(timeout, p.kill)
            timer.start()
            try:
                _pid, status, ru = os.wait4(p.pid, 0)
            finally:
                timer.cancel()
            p.returncode = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -os.WTERMSIG(status)
        rc = p.returncode
    except OSError as e:
        return {"problems": probs + ["Studio could not be run: %s" % e], "wall": time.time() - t0, "cpu": 0.0, "gcodes": [], "result": None}
    wall = time.time() - t0
    cpu = ru.ru_utime + ru.ru_stime
    if rc != 0:
        probs.append("Studio exit %d" % rc)
    rj = os.path.join(outdir, "result.json")
    res = None
    if not os.path.exists(rj):
        probs.append("no result.json")
    else:
        if os.path.getmtime(rj) < t0 - 1.0:              # 1 s margin for file-system time resolution
            probs.append("result.json is older than the start of the run")
        try:
            with open(rj) as fh:
                res = json.load(fh)
        except ValueError:
            probs.append("result.json does not parse")
        if res is not None and res.get("return_code") != 0:
            probs.append("result.json return_code %s: %s" % (res.get("return_code"), res.get("error_string")))
    gcodes = sorted(glob.glob(os.path.join(outdir, "plate_*.gcode")))
    if not gcodes:
        probs.append("no G-code file made")
    for g in gcodes:
        if os.path.getmtime(g) < t0 - 1.0:
            probs.append("%s is older than the start of the run" % os.path.basename(g))
    return {"problems": probs, "wall": wall, "cpu": cpu, "gcodes": gcodes, "result": res}


def sha_size(paths):
    h = hashlib.sha256()
    n = 0
    for p in paths:
        with open(p, "rb") as fh:
            d = fh.read()
        h.update(d)
        n += len(d)
    return h.hexdigest(), n


def tool_loads(gcode):
    """The tools loaded by Studio's filament-change macro, in order (M620 S<n>A, n < 255)."""
    out = []
    with open(gcode, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = re.match(r"M620 S(\d+)A", line)
            if m and int(m.group(1)) < 255:
                out.append(int(m.group(1)))
    return out


def _num(v):
    return ("%d" % v) if float(v) == int(v) else ("%g" % v)


def checks_292(raw, f3mf, gcodes):
    """1.3.0 phase 3 functions, from the job file: seam_headroom (project config and G-code), color_penetration_cross_parts on an
    object (object config), a manual purge table (project config and G-code), the plate type and its bed temperatures (G-code)."""
    out = []
    z = zipfile.ZipFile(f3mf)
    cfg = json.loads(z.read("Metadata/project_settings.config"))
    blk = harness.config_block(gcodes[0]) if gcodes else {}
    st = raw.get("settings") or {}
    for k in ("seam_headroom", "seam_embedded_headroom"):
        if k in st:
            want = _num(st[k])
            out.append(("seam_headroom", cfg.get("seam_embedded_headroom") == want and str(blk.get("seam_embedded_headroom")) == want,
                        "%s = %s: project config %s, G-code %s" % (k, want, cfg.get("seam_embedded_headroom"), blk.get("seam_embedded_headroom"))))
    ms = z.read("Metadata/model_settings.config").decode("utf-8")
    for i, o in enumerate(raw.get("object") or [], 1):
        os_ = o.get("settings") or {}
        for k in ("color_penetration_cross_parts", "cross_part_color_penetration"):
            if k in os_:
                want = "1" if os_[k] else "0"
                objs = re.findall(r"<object id=\"\d+\">(.*?)</object>", ms, re.S)
                got = re.findall(r'<metadata key="cross_part_color_penetration" value="([^"]*)"/>', objs[i - 1].split("<part")[0]) if len(objs) >= i else []
                out.append(("color penetration", got == [want], "object %d %s = %s: object config %s" % (i, k, want, got or "missing")))
    if isinstance(raw.get("purge"), dict):
        m = cfg.get("flush_volumes_matrix") or []
        n = len(raw.get("filament") or [])
        g = str(blk.get("flush_volumes_matrix", "")).split(",")
        for key, v in sorted(raw["purge"].items()):
            a, b = (int(x) - 1 for x in key.split(">"))
            want = _num(v)
            cell = m[a * n + b] if len(m) == n * n else None
            gcell = g[a * n + b].strip() if len(g) == n * n else None
            out.append(("purge %s" % key, cell == want and gcell is not None and float(gcell) == float(want),
                        "%s mm3: project config %s, G-code %s" % (want, cell, gcell)))
    pt = raw.get("plate_type")
    if pt:
        out.append(("plate type", blk.get("curr_bed_type") == pt, "G-code curr_bed_type %s, want %s" % (blk.get("curr_bed_type"), pt)))
        fs = ((raw.get("filament") or [{}])[0].get("settings") or {})
        if "bed_temp_first_layer" in fs and gcodes:
            want = int(fs["bed_temp_first_layer"])
            m140 = m190 = None
            with open(gcodes[0], encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if m140 is None and line.startswith("M140 S"):
                        m140 = int(float(line.split()[1][1:]))
                    if m190 is None and line.startswith("M190 S"):
                        m190 = int(float(line.split()[1][1:]))
                    if m140 is not None and m190 is not None:
                        break
            out.append(("bed temperature", m140 == want and m190 == want, "first M140 S%s, first M190 S%s, want %d" % (m140, m190, want)))
        if "bed_temp" in fs:
            key = {"3D Effect Plate": "effect_plate_temp"}.get(pt)
            if key:
                out.append(("bed temperature key", str(blk.get(key)) == str(fs["bed_temp"]), "G-code %s = %s, want %s" % (key, blk.get(key), fs["bed_temp"])))
    return out


def content_checks(name, jobfile, gcodes, f3mf=None):
    """[(check name, ok, detail)] from the job file. An expectation that cannot be made from the job is a FAIL, never a skip."""
    with open(jobfile, "rb") as fh:
        raw = tomllib.load(fh)
    root = os.path.dirname(os.path.abspath(jobfile))
    by_plate = {int(re.search(r"plate_(\d+)\.gcode$", g).group(1)): g for g in gcodes}
    out = checks_292(raw, f3mf, gcodes) if f3mf else []
    tw = raw.get("temperature_tower")
    if tw and tw.get("shape") != "calibration":
        want = [(tw["first_z"] + i * tw["step_height"], tw["start"] + i * tw["step"]) for i in range(int(tw["steps"]))]
        g = by_plate.get(int(tw.get("plate", 1)))
        if not g:
            out.append(("tower temps", False, "no G-code for the tower plate"))
        else:
            ok, d = harness.check_temps(harness.parse_body(g), want)
            out.append(("tower temps", ok, "M104 steps " + d))
    pz = {}
    for lg in raw.get("layer_gcode", []):
        if lg.get("pause"):
            pz.setdefault(int(lg.get("plate", 1)), []).append(lg["z"])
    for k, zs in sorted(pz.items()):
        g = by_plate.get(k)
        if not g:
            out.append(("pause", False, "no G-code for plate %d" % k))
            continue
        ok, d = harness.check_pauses(harness.parse_body(g), zs)
        out.append(("pause", ok, "plate %d %s" % (k, d)))
    if len(raw.get("filament", [])) > 1 and name.startswith("colors_"):
        for k, g in sorted(by_plate.items()):
            rng = sorted(harness.part_ranges(raw, root, meshmod.read_mesh, k), key=lambda r: r[1])
            overlap = any(b[1] < a[2] - 0.005 for a, b in zip(rng, rng[1:]))
            if overlap:
                out.append(("tool changes", False, "plate %d: parts overlap in Z, no exact expectation from the job" % k))
                continue
            want = [s - 1 for s, _a, _b in rng]
            got = tool_loads(g)
            out.append(("tool changes", got == want, "plate %d: want loads %s (%d), got %s (%d)" % (
                k, ["T%d" % t for t in want], len(want), ["T%d" % t for t in got], len(got))))
    return out


def main(argv):
    report = argv[argv.index("--report") + 1] if "--report" in argv else None
    spath = argv[argv.index("--studio-path") + 1] if "--studio-path" in argv else None
    only = set(argv[argv.index("--only") + 1].split(",")) if "--only" in argv else None
    det = studio_detect.detect()
    choice = versions.choose("auto", det, STUDIO_VERSIONS)
    expect_warn = choice.mode in ("newer", "newer-major", "between", "unreadable")
    exe, how = studio_detect.find_cli(spath)
    tmp = tempfile.mkdtemp(prefix="forward_check_")
    lines = ["Installed Studio: %s. Builder supports: %s. Mode: %s (builds for %s)." % (det, ", ".join(STUDIO_VERSIONS), choice.mode, choice.version),
             "Studio program: %s" % (exe or "not found (%s)" % how),
             "Columns: result, example, Studio wall-clock (launch to exit), CPU time of the Studio process, plates, G-code bytes, SHA-256 first 16 hex, print time / grams per plate.",
             ""]
    if not exe:
        lines += ["0 examples checked", "NOT CHECKED"]
        print("\n".join(lines))
        return 1
    examples = sorted(f for f in os.listdir(os.path.join(ROOT, "examples")) if f.endswith(".toml") and not f.startswith("_"))
    examples = [ex for ex in examples if not only or ex[:-5] in only]
    skipped = [ex for ex in examples if not needs(os.path.join(ROOT, "examples", ex), choice.version)]
    examples = [ex for ex in examples if ex not in skipped]           # an example for a newer Studio than the build target
    lines += ["SKIP %s (the example is for Studio %s and newer)" % (ex[:-5], pinned(os.path.join(ROOT, "examples", ex))) for ex in skipped]
    t_all = time.time()

    def one(ex):
        name = ex[:-5]
        j = job_copy(ROOT, ex, None)
        out = os.path.join(tmp, name + ".3mf")
        cmd = [sys.executable, "-I", os.path.join(ROOT, "scripts", "build_bambu_3mf.py"), j, "--out", out, "--slice-check", "--studio-path", exe]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True)
            md = out[:-4] + ".summary.md"
            mdtext = open(md).read() if os.path.exists(md) else ""
            if expect_warn:
                warn_ok = choice.warning in r.stdout and choice.warning in mdtext
            else:                                     # the installed Studio is supported exactly: no forward warning at all
                warn_ok = "Built for Studio" not in r.stdout and "Built for Studio" not in mdtext
            if os.path.exists(out):
                fver = json.loads(zipfile.ZipFile(out).read("Metadata/project_settings.config")).get("version")
                want_ver = ".".join("%02d" % int(x) for x in choice.version.split("."))
                if fver != want_ver:
                    warn_ok = False
                    r = subprocess.CompletedProcess(r.args, r.returncode, r.stdout, r.stderr + " file version %s, expected %s" % (fver, want_ver))
            build_ok = r.returncode == 0 and os.path.exists(out) and "slice check (" in r.stdout and warn_ok
            probs = [] if build_ok else ["build rc %d, warning and file version as expected: %s, stderr: %s" % (r.returncode, warn_ok, r.stderr.strip()[-300:])]
            fs = fresh_slice(exe, out, os.path.join(tmp, name + ".slice")) if os.path.exists(out) else {"problems": ["no 3MF"], "wall": 0.0, "gcodes": [], "result": None}
            probs += fs["problems"]
            sha, size = sha_size(fs["gcodes"]) if fs["gcodes"] else ("-", 0)
            cc = content_checks(name, j, fs["gcodes"], out) if fs["gcodes"] else []
            probs += ["%s: %s" % (c, d) for c, ok, d in cc if not ok]
            plates = []
            for g in fs["gcodes"]:
                t, gr = studio_detect.read_gcode_header(g)
                plates.append("%s %s g" % (t or "n/a", ("%.2f" % gr) if gr is not None else "n/a"))
        finally:
            os.remove(j)
        return name, probs, fs, sha, size, cc, plates

    checked, bad, shas, n_content = 0, [], {}, 0
    for name, probs, fs, sha, size, cc, plates in pool.run(one, examples, pool.jobs(argv)):
        checked += 1
        n_content += len(cc)
        if fs["gcodes"]:                                  # in example order, after all ran
            if sha in shas:
                probs.append("same G-code SHA-256 as %s" % shas[sha])
            shas.setdefault(sha, name)
        ok = not probs
        lines.append("%s %-22s %6.2f s wall %6.2f s CPU  %d plate(s)  %8d B  %s  %s" % ("PASS" if ok else "FAIL", name, fs["wall"], fs.get("cpu", 0.0), len(fs["gcodes"]), size, sha[:16], "; ".join(plates)))
        for c, cok, d in cc:
            lines.append("     %s %s: %s" % ("ok  " if cok else "FAIL", c, d))
        if not ok:
            bad.append(name)
            for p in probs:
                lines.append("     problem: %s" % p)
    lines += ["", "%d examples checked, %d failed. %d content checks. %d distinct G-code hashes for %d examples. Wall time %.1f s." % (
        checked, len(bad), n_content, len(shas), checked, time.time() - t_all)]
    if checked == 0:
        lines.append("NOT CHECKED")
    text = "\n".join(lines) + "\n"
    print(text, end="")
    if report:
        with open(report, "w") as fh:
            fh.write(text)
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if checked and not bad else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
