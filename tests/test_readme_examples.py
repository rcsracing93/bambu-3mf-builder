#!/usr/bin/env python3
"""The "Examples" table at the top of README.md: 7 to 9 rows, multi-color first, and every file it names (examples/*.toml or
tests/*.py) exists; every named example job builds and validates strict (0 findings).
Run: python3 -I tests/test_readme_examples.py"""
import os, re, sys, tempfile
H = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(H, ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import package, validate  # noqa: E402
from bambu3mf.job import load_job  # noqa: E402

fails = []
measured = 0


def check(name, cond, extra=""):
    global measured
    measured += 1
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)[:300]) if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


text = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
m = re.search(r"(?ms)^## Examples\n(.*?)^## ", text)
check("README has an Examples section before the next section", m is not None)
rows = [l for l in (m.group(1) if m else "").splitlines() if l.startswith("| \"")]
check("the table has 7 to 9 rows (%d)" % len(rows), 7 <= len(rows) <= 9, len(rows))
check("the first row is multi-color (colors / AMS)", bool(rows) and ("color" in rows[0].lower()), rows[:1])
files = []
for r in rows:
    named = re.findall(r"`((?:examples|tests)/[^`]+\.(?:toml|py))`", r)
    check("row names at least one example or test: %s" % r[:60], bool(named), r)
    files += named
for f in files:
    check("%s exists" % f, os.path.isfile(os.path.join(ROOT, f)))
tmp = tempfile.mkdtemp()
for f in [f for f in files if f.endswith(".toml")]:
    out = os.path.join(tmp, os.path.basename(f)[:-5] + ".3mf")
    try:
        package.build(load_job(os.path.join(ROOT, f)), out)
        r = validate.validate(out, mode="strict")
        check("%s builds and validates strict (0 findings)" % f, r.ok and not r.findings, [(x.rule, x.msg[:80]) for x in r.findings])
    except Exception as e:                                  # noqa: BLE001
        check("%s builds and validates strict (0 findings)" % f, False, e)

print("\n%d checks measured" % measured)
if measured == 0:
    print("NOT CHECKED")
print("%d failed" % len(fails) if fails else "all passed")
sys.exit(1 if fails or measured == 0 else 0)
