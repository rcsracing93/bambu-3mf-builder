#!/usr/bin/env python3
"""Forward-compatible mode with the REAL installed Studio (run on a computer with Bambu Studio installed).
Every example is built with studio = "auto" and --slice-check, then sliced again by this script in a new, empty folder. Graded per example:
  - build exit 0, the 3MF exists, the builder's own slice check passed,
  - when the installed Studio is newer than the newest supported version: the forward warning is printed and is in the summary file,
  - fresh slice: the output folder is new and empty before Studio starts; Studio exits 0; result.json says return_code 0;
    result.json and every G-code file are newer than the start of the run; at least one plate_N.gcode is made,
  - the wall-clock time of the whole Studio process (launch to exit), its own CPU time (a hand-off to another Studio would use almost
    none), the G-code size in bytes and the first 16 hex of its SHA-256,
  - content: a temperature tower has each step temperature (M104) on its layer; a pause sits on the job's layer; a multi-colour job
    loads exactly the tools the job asks for, in order (M620 S<n>A, one load per stacked part); no two examples give the same G-code.
Expectations come from the job FILE and the mesh files (tests/harness.py), never from the builder's output.
Usage: python3 tests/forward_check.py [--studio-path PATH] [--report FILE] [--only name,name]
Exit 0 when every example passed and at least one was checked."""
import glob, hashlib, json, os, re, resource, shutil, subprocess, sys, tempfile, time

H = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(H, ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, H)
from bambu3mf import studio_detect, versions, job as jobmod, mesh as meshmod  # noqa: E402
from bambu3mf.profiles import STUDIO_VERSIONS  # noqa: E402
from compare_release import job_copy  # noqa: E402
import harness  # noqa: E402

tomllib = jobmod.tomllib


def fresh_slice(exe, f3mf, outdir, timeout=900):
    """Slice in a folder that must not exist yet. Returns a dict with the facts; 'problems' lists each failed freshness check."""
    probs = []
    if os.path.exists(outdir):
        probs.append("output folder existed before the run")
    os.makedirs(outdir)
    if os.listdir(outdir):
        probs.append("output folder not empty before the run")
    ru0 = resource.getrusage(resource.RUSAGE_CHILDREN)
    t0 = time.time()
    try:
        p = subprocess.run([exe, "--slice", "0", "--outputdir", outdir, os.path.abspath(f3mf)],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        rc = p.returncode
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"problems": probs + ["Studio could not be run: %s" % e], "wall": time.time() - t0, "gcodes": [], "result": None}
    wall = time.time() - t0
    ru1 = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = (ru1.ru_utime - ru0.ru_utime) + (ru1.ru_stime - ru0.ru_stime)   # CPU time of the Studio process itself (shows it sliced, not a hand-off)
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


def content_checks(name, jobfile, gcodes):
    """[(check name, ok, detail)] from the job file. An expectation that cannot be made from the job is a FAIL, never a skip."""
    with open(jobfile, "rb") as fh:
        raw = tomllib.load(fh)
    root = os.path.dirname(os.path.abspath(jobfile))
    by_plate = {int(re.search(r"plate_(\d+)\.gcode$", g).group(1)): g for g in gcodes}
    out = []
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
    checked, bad, shas, n_content = 0, [], {}, 0
    for ex in examples:
        name = ex[:-5]
        if only and name not in only:
            continue
        j = job_copy(ROOT, ex, None)
        out = os.path.join(tmp, name + ".3mf")
        cmd = [sys.executable, "-I", os.path.join(ROOT, "scripts", "build_bambu_3mf.py"), j, "--out", out, "--slice-check", "--studio-path", exe]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True)
            checked += 1
            md = out[:-4] + ".summary.md"
            mdtext = open(md).read() if os.path.exists(md) else ""
            warn_ok = (not expect_warn) or (choice.warning in r.stdout and choice.warning in mdtext)
            build_ok = r.returncode == 0 and os.path.exists(out) and "slice check (" in r.stdout and warn_ok
            probs = [] if build_ok else ["build rc %d, warning shown %s, stderr: %s" % (r.returncode, warn_ok, r.stderr.strip()[-300:])]
            fs = fresh_slice(exe, out, os.path.join(tmp, name + ".slice")) if os.path.exists(out) else {"problems": ["no 3MF"], "wall": 0.0, "gcodes": [], "result": None}
            probs += fs["problems"]
            sha, size = sha_size(fs["gcodes"]) if fs["gcodes"] else ("-", 0)
            if fs["gcodes"]:
                if sha in shas:
                    probs.append("same G-code SHA-256 as %s" % shas[sha])
                shas.setdefault(sha, name)
            cc = content_checks(name, j, fs["gcodes"]) if fs["gcodes"] else []
            n_content += len(cc)
            probs += ["%s: %s" % (c, d) for c, ok, d in cc if not ok]
            plates = []
            for g in fs["gcodes"]:
                t, gr = studio_detect.read_gcode_header(g)
                plates.append("%s %s g" % (t or "n/a", ("%.2f" % gr) if gr is not None else "n/a"))
        finally:
            os.remove(j)
        ok = not probs
        lines.append("%s %-22s %6.2f s wall %6.2f s CPU  %d plate(s)  %8d B  %s  %s" % ("PASS" if ok else "FAIL", name, fs["wall"], fs.get("cpu", 0.0), len(fs["gcodes"]), size, sha[:16], "; ".join(plates)))
        for c, cok, d in cc:
            lines.append("     %s %s: %s" % ("ok  " if cok else "FAIL", c, d))
        if not ok:
            bad.append(name)
            for p in probs:
                lines.append("     problem: %s" % p)
    lines += ["", "%d examples checked, %d failed. %d content checks. %d distinct G-code hashes for %d examples." % (
        checked, len(bad), n_content, len(shas), checked)]
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
