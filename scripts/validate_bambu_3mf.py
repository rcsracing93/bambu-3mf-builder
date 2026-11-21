#!/usr/bin/env python3
"""Validate a Bambu Studio project 3MF.
Usage: validate_bambu_3mf.py FILE.3mf [--json] [--strict] [--privacy]      (flags may come before or after the file)
  default   errors only for what Studio rejects or misreads; what Studio changes on load or keeps is a warning;
            what only differs from Studio's own form is not reported; limits of this tool are notes
  --strict  every class except PRIVACY and TOOL is an error (the v1.0 behaviour)
  --privacy also scan for personal data (reported as warnings; never changes the exit code)
  --json    print one JSON object: {"mode", "studio_version", "tables_applied", "findings": [...]}
Exit 0 = no errors, 1 = errors found, 2 = usage. Every finding names its rule (R1-R40) or guard (G1-G6)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bambu3mf import validate    # noqa: E402

FLAGS = ("--json", "--strict", "--privacy")


def main(argv):
    if not argv or "-h" in argv or "--help" in argv:
        print(__doc__.strip()); return 2
    flags = [a for a in argv if a.startswith("--")]
    files = [a for a in argv if not a.startswith("--")]
    bad = [a for a in flags if a not in FLAGS]
    if bad or len(files) != 1:
        print(("unknown flag %s\n" % bad[0] if bad else "need exactly one file\n") + __doc__.strip(), file=sys.stderr); return 2
    rep = validate.validate(files[0], mode="strict" if "--strict" in flags else "default", privacy="--privacy" in flags)
    if "--json" in flags:
        print(json.dumps(rep.as_dict(), indent=2))
    else:
        for f in rep.findings:
            print(f)
        print("%s: %d error(s), %d warning(s), %d note(s)" % ("FAIL" if rep.errors else "ok", len(rep.errors), len(rep.warnings), len(rep.notes)))
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
