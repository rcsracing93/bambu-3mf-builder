#!/usr/bin/env python3
"""Byte identity with an earlier release: every example is built for each given Studio version by this tree and by the
reference tree (a checkout of the earlier release tag), and the two 3MF files must be byte-identical.

Usage: python3 tests/compare_release.py REF_TREE [--versions 2.8.2,2.9.1] [--report FILE] [-j N]
  git worktree add ../ref-1.2.0 v1.2.0 && python3 tests/compare_release.py ../ref-1.2.0
-j N builds N examples at a time (default: the CPU count). The report lines keep the version and example order.
Exit 0 when every pair is identical and at least one pair was compared."""
import atexit, hashlib, os, re, shutil, subprocess, sys, tempfile

H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, H)
import pool  # noqa: E402
NEW = os.path.normpath(os.path.join(H, ".."))


def _vt(text):
    return tuple(int(x) for x in str(text).split(".")[:3])


def pinned(path):
    """The studio = "x.y.z" pin of an example job, or None. An example's pin is the lowest Studio version it is for: the examples
    made for 1.2.0 pin 2.8.2; an example that uses a 2.9.2 function pins 2.9.2."""
    m = re.search(r'(?m)^studio\s*=\s*"([0-9.]+)"', open(path, encoding="utf-8").read())
    return m.group(1) if m else None


def needs(path, version):
    """True when the example can be built for this Studio version (the version is not below the example's pin)."""
    p = pinned(path)
    return p is None or _vt(version) >= _vt(p)


from harness import list_examples  # noqa: E402,F401  (other scripts import it from here too)

_COPIES = []


def _copies_dir():
    if not _COPIES:
        _COPIES.append(tempfile.mkdtemp(prefix="job_copies_"))
        atexit.register(shutil.rmtree, _COPIES[0], True)
    return _COPIES[0]


def job_copy(tree, example, studio):
    """A copy of an example job in a new temp folder (never in examples/), with studio set (None = the line removed: auto).
    Relative mesh paths are made absolute (to the tree's examples/ folder); the copy keeps the example's file name, because the
    summary names the job file. The 3MF holds mesh file names only, so the build is the same as from examples/."""
    src_dir = os.path.join(os.path.abspath(tree), "examples")
    text = open(os.path.join(src_dir, example), encoding="utf-8").read()
    text = re.sub(r'(?m)^studio\s*=.*\n', "", text)
    if studio:
        text = 'studio = "%s"\n' % studio + text

    def absmesh(m):
        p = m.group(2)
        return m.group(1) + '"%s"' % (p if os.path.isabs(p) else os.path.normpath(os.path.join(src_dir, p)).replace("\\", "/"))
    text = re.sub(r'(?m)^(\s*mesh\s*=\s*)"([^"]*)"', absmesh, text)
    dst = os.path.join(tempfile.mkdtemp(dir=_copies_dir()), example)
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write(text)
    return dst


def build(tree, job, out, extra=()):
    env = dict(os.environ, SOURCE_DATE_EPOCH="1700000000")
    r = subprocess.run([sys.executable, "-I", os.path.join(tree, "scripts", "build_bambu_3mf.py"), job, "--out", out, "--no-summary"] + list(extra),
                       capture_output=True, text=True, env=env)
    return r


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest() if os.path.exists(path) else None


def main(argv):
    if not argv or argv[0].startswith("-"):
        print(__doc__.strip())
        return 2
    ref = os.path.abspath(argv[0])
    versions = ["2.8.2", "2.9.1"]
    report = None
    if "--versions" in argv:
        versions = argv[argv.index("--versions") + 1].split(",")
    if "--report" in argv:
        report = argv[argv.index("--report") + 1]
    examples = list_examples()
    tmp = tempfile.mkdtemp()
    n = pool.jobs(argv)

    def one(item):
        v, ex = item
        if not os.path.exists(os.path.join(ref, "examples", ex)):
            return ("skip", v, ex, None)
        if not needs(os.path.join(NEW, "examples", ex), v):
            return ("needs", v, ex, None)
        outs = []
        for name, tree in (("ref", ref), ("new", NEW)):
            j = job_copy(tree, ex, v)
            o = os.path.join(tmp, "%s_%s_%s.3mf" % (name, v, ex[:-5]))
            try:
                r = build(tree, j, o)
            finally:
                os.remove(j)
            outs.append((o, r))
        return ("done", v, ex, outs)

    lines, compared, bad = [], 0, []
    for kind, v, ex, outs in pool.run(one, [(v, ex) for v in versions for ex in examples], n):
        if kind == "skip":
            lines.append("SKIP %s %s (not in the reference tree)" % (v, ex))
            continue
        if kind == "needs":
            lines.append("SKIP %s %s (the example is for Studio %s and newer)" % (v, ex, pinned(os.path.join(NEW, "examples", ex))))
            continue
        (ro, rr), (no, nr) = outs
        if rr.returncode != 0 and nr.returncode != 0:
            lines.append("SAME-REFUSAL %s %s" % (v, ex))
            continue
        compared += 1
        same = rr.returncode == 0 and nr.returncode == 0 and sha(ro) == sha(no)
        lines.append("%s %s %s %s" % ("PASS" if same else "FAIL", v, ex, (sha(no) or "no file")[:16]))
        if not same:
            bad.append("%s %s: ref rc %d, new rc %d %s" % (v, ex, rr.returncode, nr.returncode, (nr.stderr or rr.stderr)[-300:]))
    lines.append("")
    lines.append("%d pairs compared, %d differ" % (compared, len(bad)))
    lines += ["  " + b for b in bad]
    if compared == 0:
        lines.append("NOT CHECKED: no pair was compared")
    text = "\n".join(lines) + "\n"
    print(text, end="")
    if report:
        open(report, "w").write(text)
    return 0 if compared and not bad else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
