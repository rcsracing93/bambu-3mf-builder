#!/usr/bin/env python3
"""Large-mesh benchmark and real-Studio check on a user's own computer. Grades itself and writes a report.

Usage: python3 tests/machine_check.py [--sizes 1000000,2000000,5000000,10000000] [--studio-path PATH] [--report FILE] [--no-slice]

What it does:
  1. For each size: writes a closed test mesh with that many triangles, builds a project from it, validates the result.
     Each size runs in its own process, so a size that runs out of memory fails alone. Records time and peak memory.
  2. Finds the installed Bambu Studio (or --studio-path) and slices: a small two-color job, and the 1M and 2M projects.
  3. Writes a Markdown report (default: machine-check-report.md beside this repo) with PASS / FAIL per check.
Temporary files go to a temp folder that is removed at the end. Nothing is uploaded anywhere."""
import argparse, json, os, platform, shutil, struct, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

# what each size must do (plan: warn above 2M, strong warning above 5M, stop above 10M unless allow_large)
EXPECT = {1_000_000: "none", 2_000_000: "none", 5_000_000: "warn", 10_000_000: "strong"}
MAX_BUILD_S = 900            # a build slower than 15 min fails the check


def peak_rss_mb():
    import resource
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / 1024 / 1024 if sys.platform == "darwin" else r / 1024     # macOS gives bytes, Linux gives KiB


def write_grid_stl(path, target):
    """Closed box: top and bottom are k x k grids, sides are strips. Triangles = 4k^2 + 8k (as close to target as k allows)."""
    k = 1
    while 4 * (k + 1) ** 2 + 8 * (k + 1) <= target:
        k += 1
    L, H = 100.0, 10.0
    s = L / k
    n = 4 * k * k + 8 * k
    pk = struct.Struct("<12fH").pack
    with open(path, "wb") as f:
        f.write(b"\0" * 80 + struct.pack("<I", n))
        w = f.write
        for i in range(k):
            x0, x1 = i * s, (i + 1) * s
            rows = []
            for j in range(k):
                y0, y1 = j * s, (j + 1) * s
                rows.append(pk(0, 0, 1, x0, y0, H, x1, y0, H, x1, y1, H, 0))
                rows.append(pk(0, 0, 1, x0, y0, H, x1, y1, H, x0, y1, H, 0))
                rows.append(pk(0, 0, -1, x0, y0, 0, x0, y1, 0, x1, y1, 0, 0))
                rows.append(pk(0, 0, -1, x0, y0, 0, x1, y1, 0, x1, y0, 0, 0))
            w(b"".join(rows))
        for i in range(k):
            a, b = i * s, (i + 1) * s
            for q in (((a, 0, 0), (b, 0, 0), (b, 0, H), (a, 0, H)), ((L, a, 0), (L, b, 0), (L, b, H), (L, a, H)),
                      ((b, L, 0), (a, L, 0), (a, L, H), (b, L, H)), ((0, b, 0), (0, a, 0), (0, a, H), (0, b, H))):
                p0, p1, p2, p3 = q
                w(pk(0, 0, 0, *p0, *p1, *p2, 0))
                w(pk(0, 0, 0, *p0, *p2, *p3, 0))
    return n


JOB = '''studio  = "2.8.2"
output  = "{out}"
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"
[[filament]]
preset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"
color = "#808080"
[[object]]
name = "Mesh"
[[object.part]]
name = "Grid"
mesh = "{mesh}"
filament = 1
'''


def child(size, work):
    """One size, in its own process. Prints one JSON line."""
    from bambu3mf import package, validate
    from bambu3mf.job import load_job
    r = {"size": size}
    stl = os.path.join(work, "mesh_%d.stl" % size)
    t = time.time()
    r["triangles"] = write_grid_stl(stl, size)
    r["stl_mb"] = os.path.getsize(stl) / 1e6
    r["write_s"] = time.time() - t
    out = os.path.join(work, "mesh_%d.3mf" % size)
    jf = os.path.join(work, "mesh_%d.toml" % size)
    open(jf, "w").write(JOB.format(out=out, mesh=stl))
    t = time.time()
    try:
        job = load_job(jf)
        s = package.build(job, out)
        r["build_s"] = time.time() - t
        r["warnings"] = [w for w in (getattr(s, "warnings", None) or getattr(job, "warnings", None) or []) if "triangles" in w]
        r["mf_mb"] = os.path.getsize(out) / 1e6
    except Exception as e:                       # the stop above 10M lands here
        r["build_s"] = time.time() - t
        r["build_error"] = str(getattr(e, "problems", e))[:300]
        r["peak_mb"] = peak_rss_mb()
        print(json.dumps(r))
        return
    os.remove(stl)
    t = time.time()
    v = validate.validate(out, mode="strict", privacy=True)
    r["validate_s"] = time.time() - t
    r["validate_errors"] = [str(f)[:120] for f in v.errors]
    r["peak_mb"] = peak_rss_mb()
    print(json.dumps(r))


def total_ram_mb():
    try:
        if sys.platform == "darwin":
            return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout) / 1024 / 1024
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 / 1024
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="1000000,2000000,5000000,10000000")
    ap.add_argument("--studio-path")
    ap.add_argument("--report", default=os.path.join(os.path.dirname(ROOT), "machine-check-report.md"))
    ap.add_argument("--no-slice", action="store_true")
    ap.add_argument("--child", type=int)
    ap.add_argument("--work")
    a = ap.parse_args()
    if a.child:
        child(a.child, a.work)
        return 0

    from bambu3mf import studio_detect, package
    from bambu3mf.job import load_job
    work = tempfile.mkdtemp(prefix="bambu3mf_check_")
    rows, checks = [], []
    t_all = time.time()
    try:
        for size in [int(x) for x in a.sizes.split(",")]:
            print("size %s: building ..." % format(size, ","), flush=True)
            try:
                p = subprocess.run([sys.executable, __file__, "--child", str(size), "--work", work], capture_output=True, text=True,
                                   timeout=MAX_BUILD_S * 2)
                line = [l for l in p.stdout.splitlines() if l.startswith("{")]
                r = json.loads(line[-1]) if line else {"size": size, "crash": (p.stderr or "no output")[-300:]}
            except subprocess.TimeoutExpired:
                r = {"size": size, "crash": "timed out after %d s" % (MAX_BUILD_S * 2)}
            rows.append(r)
            ok = "crash" not in r and "build_error" not in r and r.get("build_s", 1e9) <= MAX_BUILD_S and not r.get("validate_errors")
            want = EXPECT.get(size)
            ws = " ".join(r.get("warnings", []))
            if want == "none":
                ok = ok and "is large" not in ws and "very large" not in ws
            elif want == "warn":
                ok = ok and "is large" in ws
            elif want == "strong":
                ok = ok and "very large" in ws
            checks.append(("mesh %s triangles: builds, validates clean, right size warning (%s)" % (format(size, ","), want or "any"), ok,
                           r.get("crash") or r.get("build_error") or "; ".join(r.get("validate_errors", [])[:2]) or ws[:120]))
            print("   %s" % ("PASS" if ok else "FAIL"), flush=True)

        # the stop above 10M: checked with the real loader on a mesh that only claims 10,000,001 triangles (no 500 MB file needed)
        from bambu3mf import mesh as meshmod
        class Big(meshmod.Mesh):
            @property
            def n_triangles(self):
                return 10_000_001
        e, _w = meshmod.check_mesh(Big(), "part")
        checks.append(("above 10M triangles the build stops and names allow_large", bool(e) and "allow_large" in e[0], e[0][:120] if e else "no stop"))

        studio = None
        if not a.no_slice:
            exe, how = studio_detect.find_cli(a.studio_path)
            ver = studio_detect.detect()
            studio = {"exe": exe, "how": how, "version": ver}
            checks.append(("Bambu Studio found (%s)" % how, bool(exe), exe or how))
            if exe:
                jobs = [("small two-color job", os.path.join(HERE, "data", "mutation_base.toml"))]
                for size in (1_000_000, 2_000_000):
                    f = os.path.join(work, "mesh_%d.3mf" % size)
                    if os.path.exists(f):
                        jobs.append(("%s triangle project" % format(size, ","), f))
                for label, src in jobs:
                    f = src
                    if src.endswith(".toml"):
                        f = os.path.join(work, "small.3mf")
                        package.build(load_job(src), f)
                    t = time.time()
                    ok, msg = studio_detect.slice_check(exe, f, timeout=1800)
                    checks.append(("--slice-check, %s (%.0f s)" % (label, time.time() - t), ok, msg[:160]))
                    print("slice %s: %s" % (label, "PASS" if ok else "FAIL"), flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    npass = sum(1 for _c, ok, _d in checks if ok)
    L = ["# 3MF builder - check on this computer (%s)" % platform.node(), "",
         "Run %s, %.0f min. %s, Python %s, RAM %s MB." % (time.strftime("%Y-%m-%d %H:%M"), (time.time() - t_all) / 60, platform.platform(),
                                                         platform.python_version(), "%.0f" % total_ram_mb() if total_ram_mb() else "?"),
         "", "**Result: %d of %d checks PASS.**" % (npass, len(checks)), "", "| Check | Result | Detail |", "|---|---|---|"]
    for c, ok, d in checks:
        L.append("| %s | %s | %s |" % (c, "PASS" if ok else "**FAIL**", str(d).replace("|", "/").replace("\n", " ")))
    L += ["", "## Large-mesh benchmark", "", "| Triangles | STL MB | Build s | 3MF MB | Validate s | Peak memory MB | Size warning |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        if "crash" in r:
            L.append("| %s | | | | | | crashed: %s |" % (format(r["size"], ","), r["crash"][:100].replace("\n", " ")))
            continue
        L.append("| %s | %.0f | %.1f | %s | %s | %.0f | %s |" % (
            format(r.get("triangles", r["size"]), ","), r.get("stl_mb", 0), r.get("build_s", 0), "%.0f" % r["mf_mb"] if "mf_mb" in r else "-",
            "%.1f" % r["validate_s"] if "validate_s" in r else "-", r.get("peak_mb", 0),
            (r.get("build_error") or " ".join(r.get("warnings", [])) or "none")[:90].replace("|", "/")))
    if studio:
        L += ["", "Studio: %s (found by %s), installed version %s." % (studio["exe"], studio["how"], studio["version"])]
    L.append("")
    open(a.report, "w").write("\n".join(L))
    print("\n%d of %d checks PASS. Report: %s" % (npass, len(checks), a.report))
    return 0 if npass == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
