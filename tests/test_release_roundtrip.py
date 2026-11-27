"""The trimmed release behaves like the repo. Run: python3 -I tests/test_release_roundtrip.py
Unpacks the skill zip into a temp folder, builds every example from there, validates each (strict), and requires the same bytes
as a build from the repo, for every supported Studio version. Also compares --list output for every version."""
import os, subprocess, sys, tempfile, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
ENV = dict(os.environ, SOURCE_DATE_EPOCH="1700000000")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from compare_release import job_copy  # noqa: E402
from bambu3mf.profiles import STUDIO_VERSIONS as VERS_FULL  # noqa: E402
VERS = [".".join(v.split(".")[:3]) for v in VERS_FULL]
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
    # every example for every supported version: the release stores a preset file once (profiles/BASE), so each version
    # must resolve the same presets from the zip as from the full repo
    for ver in VERS:
        for ex in examples:
            name = "%s %s" % (ex, ver)
            a = os.path.join(outd, "repo_%s_%s.3mf" % (ver, ex[:-5])); b = os.path.join(outd, "rel_%s_%s.3mf" % (ver, ex[:-5]))
            # both builds run from a job copy inside their own tree so relative mesh paths resolve the same way
            ja, jb = job_copy(ROOT, ex, ver), job_copy(R, ex, ver)
            try:
                ra = run(os.path.join(ROOT, "scripts", "build_bambu_3mf.py"), ja, "--out", a, "--no-summary")
                rb = run(os.path.join(R, "scripts", "build_bambu_3mf.py"), jb, "--out", b, "--no-summary")
            finally:
                os.remove(ja); os.remove(jb)
            check("%s builds from release" % name, ra.returncode == 0 and rb.returncode == 0, ra.stderr + rb.stderr + rb.stdout)
            if rb.returncode == 0 and os.path.exists(b):
                v = run(os.path.join(R, "scripts", "validate_bambu_3mf.py"), b, "--strict")
                check("%s strict validation (release)" % name, v.returncode == 0, v.stdout + v.stderr)
                check("%s same bytes as repo build" % name, ra.returncode == 0 and open(a, "rb").read() == open(b, "rb").read())
    base = [v for v in VERS_FULL[1:] if os.path.isfile(os.path.join(R, "studio", v, "profiles", "BASE"))]
    check("the release stores shared preset files once (profiles/BASE in %d of %d later versions)" % (len(base), len(VERS_FULL) - 1),
          len(base) == len(VERS_FULL) - 1)
    for kind in ("printer", "process", "filament"):
        for ver in VERS:
            args = ["--list", kind, "--studio", ver] + (["--printer", "Bambu Lab P1S 0.4 nozzle"] if kind != "printer" else [])
            ra = run(os.path.join(ROOT, "scripts", "build_bambu_3mf.py"), *args)
            rb = run(os.path.join(R, "scripts", "build_bambu_3mf.py"), *args)
            check("--list %s %s same" % (kind, ver), ra.returncode == 0 and ra.stdout == rb.stdout and len(ra.stdout) > 50, rb.stderr)
sys.exit(1 if fails else 0)
