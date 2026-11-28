#!/usr/bin/env python3
"""Python 3.8-3.10 have no tomllib. Every `import tomllib` in a .py file of the repo (outside the vendored tomli) must sit in
`try:` with an `except ImportError:` that imports the vendored tomli as tomllib, as in scripts/bambu3mf/job.py. Other files take
it from there (`from bambu3mf.job import tomllib`). Static check, no import of the files.
Run: python3 -I tests/test_tomllib_fallback.py"""
import os, re, sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
bad, files, imports = [], 0, 0
for d, dirs, names in os.walk(ROOT):
    dirs[:] = [x for x in dirs if x not in (".git", "_vendor", "__pycache__", "studio")]
    for n in names:
        if not n.endswith(".py"):
            continue
        files += 1
        p = os.path.join(d, n)
        lines = open(p, encoding="utf-8").read().splitlines()
        for i, line in enumerate(lines):
            if not re.match(r"\s*import tomllib\b", line):
                continue
            imports += 1
            before = next((l.strip() for l in reversed(lines[:i]) if l.strip()), "")
            after = "\n".join(lines[i + 1:i + 3])
            if before != "try:" or "except ImportError" not in after or "tomli as tomllib" not in after:
                bad.append("%s:%d" % (os.path.relpath(p, ROOT), i + 1))
print("%d .py files scanned, %d `import tomllib` lines found" % (files, imports))
if files == 0:
    print("NOT CHECKED")
    sys.exit(1)
if bad:
    print("FAIL `import tomllib` without the ImportError fallback (Python 3.8-3.10 have no tomllib): %s" % ", ".join(bad))
    sys.exit(1)
print("ok   every `import tomllib` has the vendored tomli fallback")
sys.exit(0)
