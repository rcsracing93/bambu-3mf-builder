#!/usr/bin/env python3
"""Byte identity with an earlier release: every example is built for each given Studio version by this tree and by the
reference tree (a checkout of the earlier release tag), and the two 3MF files must be byte-identical.

Usage: python3 tests/compare_release.py REF_TREE [--versions 2.8.2,2.9.1] [--report FILE] [-j N]
  git worktree add ../ref-1.2.0 v1.2.0 && python3 tests/compare_release.py ../ref-1.2.0
-j N builds N examples at a time (default: the CPU count). The report lines keep the version and example order.
Exit 0 when every pair is identical and at least one pair was compared."""
import hashlib, os, re, subprocess, sys, tempfile

H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, H)
import pool  # noqa: E402
NEW = os.path.normpath(os.path.join(H, ".."))


def job_copy(tree, example, studio):
    """A copy of the example job next to it (mesh paths stay relative), with studio set (None = the line removed: auto)."""
    src = os.path.join(tree, "examples", example)
    text = open(src, encoding="utf-8").read()
    text = re.sub(r'(?m)^studio\s*=.*\n', "", text)
    if studio:
        text = 'studio = "%s"\n' % studio + text
    tag = (studio or "auto").replace(".", "_")
    # the process id keeps copies of parallel test processes apart; every examples/ listing skips names that start with "_"
    dst = os.path.join(tree, "examples", "_cmp_%d_%s_%s" % (os.getpid(), tag, example))
    open(dst, "w", encoding="utf-8").write(text)
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
    examples = sorted(f for f in os.listdir(os.path.join(NEW, "examples")) if f.endswith(".toml") and not f.startswith("_"))
    tmp = tempfile.mkdtemp()
    n = pool.jobs(argv)

    def one(item):
        v, ex = item
        if not os.path.exists(os.path.join(ref, "examples", ex)):
            return ("skip", v, ex, None)
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
