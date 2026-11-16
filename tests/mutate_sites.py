#!/usr/bin/env python3
"""Site mutation test for the validator: switch off ONE finding (one rep.err / rep.warn / report call) at a time in a scratch copy and run the
validator tests. A site whose removal leaves every test green is a surviving mutant and is printed.
Usage: python3 tests/mutate_sites.py [--jobs 4] [--out survivors.json]     (about 15 minutes; not part of run_all)"""
import argparse, json, os, re, shutil, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor

H = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(H)
FILES = ["validate.py", "rules_config.py", "rules_package.py", "privacy.py"]
TESTS = ["test_rule_teeth.py", "test_validate.py", "test_teeth.py", "test_hostile.py", "test_privacy.py", "test_skill.py", "test_nonsplit.py", "test_r37_tower.py", "test_r28_vectors.py", "test_r11_lists.py", "test_r16_versions.py", "test_r3_user_presets.py", "test_r5_unlisted.py", "test_r23_builditem.py", "test_modes.py", "test_source_lines.py"]
CALL = re.compile(r"(\brep\.(?:err|warn)\(|\bself\.rep\.err\(|(?<![\w.])report\()")


def sites():
    out = []
    for f in FILES:
        text = open(os.path.join(ROOT, "scripts", "bambu3mf", f)).read()
        for m in CALL.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            out.append((f, m.start(), line, text[m.start():m.start() + 70].split("\n")[0]))
    return out


def run_one(args):
    idx, (f, pos, line, snippet) = args
    d = tempfile.mkdtemp(prefix="mut_%d_" % idx)
    try:
        shutil.copytree(ROOT, d + "/r", ignore=shutil.ignore_patterns(".git", "__pycache__", "*.3mf"))
        p = os.path.join(d, "r", "scripts", "bambu3mf", f)
        s = open(p).read()
        s = s[:pos] + "_nop(" + s[re.match(CALL, s[pos:]).end() + pos:]
        s += "\n_nop = lambda *a, **k: None\n"
        open(p, "w").write(s)
        for t in TESTS:
            r = subprocess.run([sys.executable, "-I", os.path.join(d, "r", "tests", t)], capture_output=True, text=True, timeout=600)
            if r.returncode:
                return idx, True, t
        return idx, False, ""
    except Exception as e:                      # a mutant that breaks the module counts as killed
        return idx, True, "error %s" % e
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--out")
    a = ap.parse_args()
    ss = sites()
    print("%d sites" % len(ss), flush=True)
    surv = []
    with ThreadPoolExecutor(a.jobs) as ex:
        for idx, killed, by in ex.map(run_one, list(enumerate(ss))):
            if not killed:
                f, pos, line, snip = ss[idx]
                surv.append({"file": f, "line": line, "code": snip})
                print("SURVIVED %s:%d  %s" % (f, line, snip), flush=True)
    print("\n%d of %d sites killed, %d survived" % (len(ss) - len(surv), len(ss), len(surv)))
    if a.out:
        json.dump(surv, open(a.out, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
