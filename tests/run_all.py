#!/usr/bin/env python3
"""Run every test. Usage: python3 tests/run_all.py [-j N] [--report FILE]   (exit 1 if any fails)
One process per test file, N at a time (default: the CPU count; -j 1 runs them one after the other). The PASS/FAIL lines are
printed sorted by file name, with the output of each failed test, then the count and the wall time."""
import os, subprocess, sys, time
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, H)
import pool  # noqa: E402

fixtures = {"test_fixture_282.py": None, "test_oracle_282.py": None}      # need a Studio save passed in; skipped in CI


def run_one(t):
    t0 = time.time()
    r = subprocess.run([sys.executable, "-I", os.path.join(H, t)], capture_output=True, text=True)
    return t, r, time.time() - t0


def main(argv):
    argv = list(argv)
    report = None
    if "--report" in argv:
        i = argv.index("--report")
        report = argv[i + 1]
        del argv[i:i + 2]
    n = pool.jobs(argv)
    tests = sorted(f for f in os.listdir(H) if f.startswith("test_") and f.endswith(".py") and f not in fixtures)
    t0 = time.time()
    results = pool.run(run_one, tests, n)
    bad, lines = [], []
    for t, r, dt in sorted(results, key=lambda x: x[0]):
        lines.append(("PASS " if r.returncode == 0 else "FAIL ") + t)
        if r.returncode:
            bad.append(t)
            lines.append(r.stdout[-1500:] + " " + r.stderr[-1500:])
    slow = sorted(results, key=lambda x: -x[2])[:3]
    lines.append("%d test files, %d failed, %d at a time, wall time %.1f s (slowest: %s)" % (
        len(results), len(bad), n, time.time() - t0, ", ".join("%s %.0f s" % (t, dt) for t, _r, dt in slow)))
    if not results:
        lines.append("NOT CHECKED")
    print("\n".join(lines))
    if report:
        with open(report, "w") as fh:
            fh.write("\n".join(lines) + "\n")
    return 1 if bad or not results else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
