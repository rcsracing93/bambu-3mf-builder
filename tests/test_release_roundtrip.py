"""The trimmed release behaves like the repo. Run: python3 -I tests/test_release_roundtrip.py [-j N]
Unpacks the skill zip into a temp folder and, for every supported Studio version:
  - builds every example from there, validates each (strict), and requires the same bytes as a build from the repo,
  - runs patch mode from there (one key changed), validates it (strict), and requires the same bytes as the repo's patch,
  - compares --list output.
Also: when STUDIO_VERSIONS drops its oldest version, make_release.py stores the new oldest version with full files and writes no
BASE that points outside the list. The builds and patches run N at a time (default: the CPU count)."""
import os, subprocess, sys, tempfile, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
ENV = dict(os.environ, SOURCE_DATE_EPOCH="1700000000")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from compare_release import job_copy, needs  # noqa: E402
from bambu3mf.profiles import STUDIO_VERSIONS as VERS_FULL  # noqa: E402
import make_release  # noqa: E402
import pool  # noqa: E402
VERS = [".".join(v.split(".")[:3]) for v in VERS_FULL]
N = pool.jobs(sys.argv[1:])
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
    examples = sorted(f for f in os.listdir(os.path.join(ROOT, "examples")) if f.endswith(".toml") and not f.startswith("_"))
    check("examples found", len(examples) >= 10)

    # every example for every supported version: the release stores a preset file once (profiles/BASE), so each version
    # must resolve the same presets from the zip as from the full repo
    def build_pair(item):
        ver, ex = item
        a = os.path.join(outd, "repo_%s_%s.3mf" % (ver, ex[:-5])); b = os.path.join(outd, "rel_%s_%s.3mf" % (ver, ex[:-5]))
        # both builds run from a job copy inside their own tree so relative mesh paths resolve the same way
        ja, jb = job_copy(ROOT, ex, ver), job_copy(R, ex, ver)
        try:
            ra = run(os.path.join(ROOT, "scripts", "build_bambu_3mf.py"), ja, "--out", a, "--no-summary")
            rb = run(os.path.join(R, "scripts", "build_bambu_3mf.py"), jb, "--out", b, "--no-summary")
        finally:
            os.remove(ja); os.remove(jb)
        v = run(os.path.join(R, "scripts", "validate_bambu_3mf.py"), b, "--strict") if rb.returncode == 0 and os.path.exists(b) else None
        return ver, ex, a, b, ra, rb, v

    pairs = [(ver, ex) for ver in VERS for ex in examples if needs(os.path.join(ROOT, "examples", ex), ver)]
    check("build pairs: %d (examples for a newer Studio skip the older versions)" % len(pairs), len(pairs) >= len(examples))
    for ver, ex, a, b, ra, rb, v in pool.run(build_pair, pairs, N):
        name = "%s %s" % (ex, ver)
        check("%s builds from release" % name, ra.returncode == 0 and rb.returncode == 0, ra.stderr + rb.stderr + rb.stdout)
        if v is not None:
            check("%s strict validation (release)" % name, v.returncode == 0, v.stdout + v.stderr)
            check("%s same bytes as repo build" % name, ra.returncode == 0 and open(a, "rb").read() == open(b, "rb").read())

    # patch mode from the release, per version: the input is the repo build of colors_4 for that version
    def patch_pair(ver):
        src = os.path.join(outd, "repo_%s_colors_4.3mf" % ver)
        res = []
        for side, tree in (("repo", ROOT), ("rel", R)):
            d = os.path.join(tmp, "patch_%s_%s" % (side, ver))
            os.makedirs(d)
            pj, out = os.path.join(d, "patch.toml"), os.path.join(d, "patched.3mf")      # same names on both sides
            with open(pj, "w") as fh:
                fh.write('patch = "%s"\noutput = "%s"\n[settings]\nwall_loops = 4\n' % (src, out))
            res.append((run(os.path.join(tree, "scripts", "build_bambu_3mf.py"), pj, "--no-summary"), out))
        v = run(os.path.join(R, "scripts", "validate_bambu_3mf.py"), res[1][1], "--strict") if os.path.exists(res[1][1]) else None
        return ver, res, v

    for ver, ((ra, a), (rb, b)), v in pool.run(patch_pair, VERS, N):
        check("patch %s runs from the release and from the repo" % ver, ra.returncode == 0 and rb.returncode == 0 and os.path.exists(b),
              (ra.stdout + ra.stderr + rb.stdout + rb.stderr)[-600:])
        if v is not None:
            check("patch %s strict validation (release)" % ver, v.returncode == 0, v.stdout + v.stderr)
            check("patch %s same bytes as the repo patch" % ver, os.path.exists(a) and open(a, "rb").read() == open(b, "rb").read())

    base = [v for v in VERS_FULL[1:] if os.path.isfile(os.path.join(R, "studio", v, "profiles", "BASE"))]
    check("the release stores shared preset files once (profiles/BASE in %d of %d later versions)" % (len(base), len(VERS_FULL) - 1),
          len(base) == len(VERS_FULL) - 1)
    for kind in ("printer", "process", "filament"):
        for ver in VERS:
            args = ["--list", kind, "--studio", ver] + (["--printer", "Bambu Lab P1S 0.4 nozzle"] if kind != "printer" else [])
            ra = run(os.path.join(ROOT, "scripts", "build_bambu_3mf.py"), *args)
            rb = run(os.path.join(R, "scripts", "build_bambu_3mf.py"), *args)
            check("--list %s %s same" % (kind, ver), ra.returncode == 0 and ra.stdout == rb.stdout and len(ra.stdout) > 50, rb.stderr)

# ---- the oldest version leaves STUDIO_VERSIONS ------------------------------------------------------------------------------------
if len(VERS_FULL) >= 3:
    dropped, kept = VERS_FULL[0], tuple(VERS_FULL[1:])
    real = make_release._versions
    make_release._versions = lambda: kept
    try:
        ents = make_release.release_entries()
    finally:
        make_release._versions = real
    paths = [p.replace(os.sep, "/") for p, _d in ents]
    check("drop %s: none of its files is released" % dropped, not [p for p in paths if p.startswith("studio/%s/" % dropped)])
    bases = {p.split("/")[1]: d.decode().strip() for p, d in ents if p.replace(os.sep, "/").endswith("/profiles/BASE")}
    check("drop %s: the new oldest version %s has no BASE" % (dropped, kept[0]), kept[0] not in bases, bases)
    check("drop %s: every BASE names a listed version (%s)" % (dropped, bases), bases and all(b in kept for b in bases.values()))
    repo_n = sum(len([n for n in names if n.endswith(".json") and n != "cli_config.json"])          # make_release never ships cli_config.json
                 for d, _dirs, names in os.walk(os.path.join(ROOT, "studio", kept[0], "profiles", "BBL")))
    rel_n = len([p for p in paths if p.startswith("studio/%s/profiles/BBL/" % kept[0]) and p.endswith(".json")])
    check("drop %s: %s is stored with full files (%d of %d preset files)" % (dropped, kept[0], rel_n, repo_n), repo_n > 0 and rel_n == repo_n)
else:
    print("NOT CHECKED: the drop test needs 3 or more supported versions")
sys.exit(1 if fails else 0)
