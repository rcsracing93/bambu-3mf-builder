#!/usr/bin/env python3
"""Write docs/support-matrix.md from the JSON reports of tests/studio_slice.py (and tests/studio_mutation.py).
Usage: python3 tests/make_matrix.py [--date YYYY-MM-DD] [--mutation m_2.8.2.json ...] report_2.8.2.json report_2.9.1.json > docs/support-matrix.md
The date is the day the reports were produced; CI uploads the same reports as artifacts of the `real Bambu Studio` workflow."""
import json, sys
args = sys.argv[1:]
date, mut = None, []
while args and args[0] in ("--date", "--mutation"):
    if args[0] == "--date":
        date = args[1]
    else:
        mut.append(args[1])
    args = args[2:]
rep = [json.load(open(p)) for p in args]
jobs = sorted({r["job"] for d in rep for r in d["rows"]})
tests = ["T2", "T3", "T4", "T5", "T5b", "T6", "T7", "T8", "T9", "T10", "T10p", "T11", "T12", "T13", "T14"]
print("# Support matrix\n")
print("Every example in `examples/` is built, sliced in a real headless Bambu Studio (plain and `--uptodate`), and graded.")
print("A cell shows **ok** with the number of checks that passed, or **FAIL** with the number that failed; a blank cell means no check applies.")
print("CI-verified means the G-code was produced and checked by Studio; it does not mean the print was tested on a machine.")
if date:
    print("Last full pass: %s (reports produced by `tests/studio_slice.py`; the `real Bambu Studio` workflow produces the same reports for each Studio version)." % date)
print()
print("| Example | " + " | ".join("Studio %s, file built for %s" % (d["studio"], d["build"].split()[-1].strip("()")) for d in rep) + " |")
print("|---|" + "---|" * len(rep))
for j in jobs:
    cells = []
    for d in rep:
        rows = [r for r in d["rows"] if r["job"] == j and r["test"] in tests]
        bad = [r for r in rows if not r["ok"]]
        cells.append("" if not rows else ("FAIL (%d of %d)" % (len(bad), len(rows)) if bad else "ok (%d)" % len(rows)))
    print("| %s | %s |" % (j, " | ".join(cells)))
print("\nChecks. What the job asked for (plates, settings, colors, presets, filament per part, pauses, temperatures) comes from the job file")
print("and the mesh files, never from the builder's output. T5 and T14 compare the written file with the G-code, to prove the file reaches the slicer unchanged.")
print("T2 validator finds nothing, T3 slice succeeds, T4 slice with `--uptodate` succeeds, T5 every written setting reaches the G-code,")
print("T5b every setting the job asked for is in the G-code with the job's value, T6 plain and `--uptodate` agree,")
print("T7 filaments used, and the tool on every layer, follow the parts, T8 tool changes and prime tower, T9 no unexpected warning or error in the Studio log,")
print("T10 temperature tower on the requested layers, T10p pauses on the requested layers, T11 Studio re-export holds the job and slices again,")
print("T12 file built for another Studio version slices (only in a local run with `--other-studio-dir`; CI runs each version on its own), T13 the real prime tower does not overlap the model, T14 it sits inside the box R37 reserves.")
print("`tests/studio_roundtrip.py` additionally saves each file three times in Studio and checks that geometry does not move.")
if mut:
    print("\n## Harness mutation test\n")
    print("Broken copies of a known-good example. Each must fail the harness and name the right rule (`tests/studio_mutation.py`, `tests/test_harness_mutations.py`).\n")
    ms = [json.load(open(p)) for p in mut]
    print("| Broken copy | Must | " + " | ".join("Studio %s" % m["studio"] for m in ms) + " |")
    print("|---|---|" + "---|" * len(ms))
    for i, row in enumerate(ms[0]["rows"]):
        print("| %s | %s | %s |" % (row["case"], row["expect"], " | ".join(("PASS: " if m["rows"][i]["ok"] else "FAIL: ") + m["rows"][i]["got"] for m in ms)))
