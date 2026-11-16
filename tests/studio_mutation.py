#!/usr/bin/env python3
"""Mutation test for the real-slicer harness: break a known-good job in five ways in the file and three ways in the loaded job and check that the harness FAILS each one
and names the right rule. The two harness-only breakages (partial list match, error line in the slice log) are in
tests/test_harness_mutations.py because they need no Studio.

Usage: python3 tests/studio_mutation.py --studio-dir DIR --version 2.8.2 [--report mutation-report.json] [--work DIR]
"""
import argparse, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import studio_slice as S  # noqa: E402
import mutations as M  # noqa: E402

BASE = os.path.join(HERE, "data", "mutation_base.toml")


def run(sdir, version, work, tamper, job=BASE, loader=None):
    rep = S.Report()
    real = S.jobmod.load_job
    if loader:
        def patched(*a, **k):
            j = real(*a, **k)
            loader(j)
            return j
        S.jobmod.load_job = patched
    try:
        S.check_job(job, sdir, version, work, rep, tamper=tamper)
    finally:
        S.jobmod.load_job = real
    return rep.rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--studio-dir", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--report")
    ap.add_argument("--work")
    a = ap.parse_args()
    work = a.work or tempfile.mkdtemp(prefix="studio_mut_")
    table = []
    base = run(a.studio_dir, a.version, os.path.join(work, "base"), None) if os.makedirs(os.path.join(work, "base"), exist_ok=True) is None else None
    bad = [r for r in base if not r["ok"]]
    table.append({"case": "unbroken example", "expect": "all checks pass", "got": "%d checks, %d failed" % (len(base), len(bad)), "ok": not bad})
    for i, (name, tamper, rules, checks) in enumerate(M.MUTATIONS):
        w = os.path.join(work, "m%d" % i)
        os.makedirs(w, exist_ok=True)
        rows = run(a.studio_dir, a.version, w, tamper)
        failed = {r["test"]: r for r in rows if not r["ok"]}
        miss = [c for c in checks if c not in failed]
        named = [r for r in rules if r not in failed.get("T2", {}).get("detail", "")]
        ok = not miss and not named
        table.append({"case": name, "expect": "fail %s%s" % (", ".join(checks), (" naming " + ", ".join(rules)) if rules else ""),
                      "got": "failed: %s" % (", ".join(sorted(failed)) or "nothing"), "ok": ok,
                      "detail": "; ".join("%s: %s" % (c, failed[c]["detail"][:100]) for c in checks if c in failed)})
    for i, (name, loader, jobname, checks) in enumerate(M.LOADER_MUTATIONS):
        w = os.path.join(work, "l%d" % i)
        os.makedirs(w, exist_ok=True)
        jf = os.path.join(HERE, "..", "examples", jobname + ".toml") if jobname else BASE
        rows = run(a.studio_dir, a.version, w, None, jf, loader)
        failed = {r["test"]: r for r in rows if not r["ok"]}
        miss = [c for c in checks if c not in failed]
        table.append({"case": name, "expect": "fail %s" % ", ".join(checks), "got": "failed: %s" % (", ".join(sorted(failed)) or "nothing"),
                      "ok": not miss, "detail": "; ".join("%s: %s" % (c, failed[c]["detail"][:100]) for c in checks if c in failed)})
    print("\n%-52s %-34s %s" % ("mutation", "must", "result"))
    for t in table:
        print("%-52s %-34s %s  (%s)" % (t["case"], t["expect"], "PASS" if t["ok"] else "FAIL", t["got"]))
    if a.report:
        json.dump({"studio": a.version, "rows": table}, open(a.report, "w"), indent=1)
    return 0 if all(t["ok"] for t in table) else 1


if __name__ == "__main__":
    sys.exit(main())
