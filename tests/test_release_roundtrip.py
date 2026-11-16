"""The trimmed release behaves like the repo. Run: python3 -I tests/test_release_roundtrip.py
Unpacks the skill zip into a temp folder, builds every example from there, validates each (strict), and requires the same bytes
as a build from the repo. Also compares --list output for both Studio versions."""
import os, subprocess, sys, tempfile, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
ENV = dict(os.environ, SOURCE_DATE_EPOCH="1700000000")
fails = []


def run(*a):
    return subprocess.run([sys.executable, "-I", *a], capture_output=True, text=True, env=ENV, cwd=tempfile.gettempdir())


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)[:600]) if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


with tempfile.TemporaryDirectory() as tmp:
    rel = os.path.join(tmp, "rel"); un = os.path.join(tmp, "un"); outd = os.path.join(tmp, "out")
    for d in (rel, un, outd):
        os.makedirs(d)
    r = run(os.path.join(ROOT, "scripts", "make_release.py"), "--out", rel)
    check("release builds", r.returncode == 0, r.stderr)
    zipfile.ZipFile(os.path.join(rel, "bambu-3mf-builder-skill.zip")).extractall(un)
    R = os.path.join(un, "bambu-3mf-builder")
    check("SKILL.md at top", os.path.isfile(os.path.join(R, "SKILL.md")))
    examples = sorted(f for f in os.listdir(os.path.join(ROOT, "examples")) if f.endswith(".toml"))
    check("examples found", len(examples) >= 10)
    for ex in examples:
        a = os.path.join(outd, "repo_" + ex[:-5] + ".3mf"); b = os.path.join(outd, "rel_" + ex[:-5] + ".3mf")
        # both builds run from the example copy inside their own tree so relative mesh paths resolve the same way
        ra = run(os.path.join(ROOT, "scripts", "build_bambu_3mf.py"), os.path.join(ROOT, "examples", ex), "--out", a, "--no-summary")
        rb = run(os.path.join(R, "scripts", "build_bambu_3mf.py"), os.path.join(R, "examples", ex), "--out", b, "--no-summary")
        check("%s builds from release" % ex, ra.returncode == 0 and rb.returncode == 0, ra.stderr + rb.stderr + rb.stdout)
        if rb.returncode == 0 and os.path.exists(b):
            v = run(os.path.join(R, "scripts", "validate_bambu_3mf.py"), b, "--strict")
            check("%s strict validation (release)" % ex, v.returncode == 0, v.stdout + v.stderr)
            check("%s same bytes as repo build" % ex, ra.returncode == 0 and open(a, "rb").read() == open(b, "rb").read())
    for kind in ("printer", "process", "filament"):
        for ver in ("2.8.2", "2.9.1"):
            args = ["--list", kind, "--studio", ver] + (["--printer", "Bambu Lab P1S 0.4 nozzle"] if kind != "printer" else [])
            ra = run(os.path.join(ROOT, "scripts", "build_bambu_3mf.py"), *args)
            rb = run(os.path.join(R, "scripts", "build_bambu_3mf.py"), *args)
            check("--list %s %s same" % (kind, ver), ra.returncode == 0 and ra.stdout == rb.stdout and len(ra.stdout) > 50, rb.stderr)
sys.exit(1 if fails else 0)
