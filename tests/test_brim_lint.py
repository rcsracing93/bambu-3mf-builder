#!/usr/bin/env python3
"""PETG brim lint (lint rule 6): PETG or PETG-CF with a printed brim and brim_object_gap below 0.2 mm warns; 0.2 mm and more, no brim,
and other filaments do not. Object overrides, the filament of the first layer, the summary file, strict validation, and the check module
tests/brim_check.py. Prints the measured count.
Run: python3 -I tests/test_brim_lint.py"""
import os, shutil, sys, tempfile
H = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(H)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, H)
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import package, summary, validate
from bambu3mf.job import load_job
import brim_check

fails = []
measured = [0]
def check(name, cond, extra=""):
    measured[0] += 1
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)[:400]) if (extra and not cond) else ""))
    if not cond: fails.append(name)

tmp = tempfile.mkdtemp()
CUBE = os.path.join(ROOT, "examples", "data", "cube20.stl")
PETG = "Bambu PETG Basic @BBL X1C"
PETG_CF = "Bambu PETG-CF @BBL X1C 0.4 nozzle"
PETG_HF = "Bambu PETG HF @BBL P1S 0.4 nozzle"
PLA = "Bambu PLA Basic @BBL P1S 0.4 nozzle"
TEXT = "PETG fuses to a tight brim; set brim_object_gap = 0.25"


def job(name, filaments=(PETG,), settings="", objects=(("", (1,)),)):
    """objects: list of (object settings text, tuple of part filament numbers)."""
    # Textured PEI: the PETG presets have a cool plate temperature of 0, which Studio refuses to slice (validator rule R39)
    t = ('studio = "2.9.2"\noutput = "%s.3mf"\nprinter = "Bambu Lab P1S 0.4 nozzle"\nprocess = "0.20mm Standard @BBL X1C"\n'
         'plate_type = "Textured PEI Plate"\n' % name)
    if settings:
        t += "[settings]\n" + settings
    for i, f in enumerate(filaments):
        t += '[[filament]]\npreset = "%s"\ncolor = "#%s"\n' % (f, "112233" if i == 0 else "FFEEDD")
    for k, (osettings, parts) in enumerate(objects):
        t += '[[object]]\nname = "O%d"\n' % (k + 1)
        if osettings:
            t += "[object.settings]\n" + osettings
        for j, f in enumerate(parts):
            t += '[[object.part]]\nname = "P%d"\nmesh = "%s"\nfilament = %d\n' % (j + 1, CUBE, f)
    p = os.path.join(tmp, name + ".toml")
    open(p, "w").write(t)
    return p


def warns(path):
    s = package.build(load_job(path), os.path.join(tmp, os.path.basename(path)[:-5] + ".3mf"))
    return [w for w in s.warnings if w.startswith("lint:") and TEXT in w], s


def fires(name, path):
    w, _s = warns(path)
    check(name + ": warns", len(w) >= 1, w)
    return w


def quiet(name, path):
    w, _s = warns(path)
    check(name + ": no brim warning", not w, w)


BRIM = 'brim_type = "outer_only"\nbrim_width = 5\n'
try:
    for label, fil in (("PETG", PETG), ("PETG-CF", PETG_CF), ("PETG HF", PETG_HF)):
        w = fires(label + " gap 0.1 outer_only", job("a_" + label.replace(" ", "_"), (fil,), BRIM + "brim_object_gap = 0.1\n"))
        check(label + ": text names the gap and the object", bool(w) and "0.1 mm" in w[0] and "object O1" in w[0], w)
    fires("PETG gap 0.1 inner_only", job("inner", settings='brim_type = "inner_only"\nbrim_width = 5\nbrim_object_gap = 0.1\n'))
    fires("PETG gap 0.1 outer_and_inner", job("both", settings='brim_type = "outer_and_inner"\nbrim_width = 5\nbrim_object_gap = 0.1\n'))
    fires("PETG gap 0.19", job("g19", settings=BRIM + "brim_object_gap = 0.19\n"))
    fires("PETG gap 0", job("g0", settings=BRIM + "brim_object_gap = 0\n"))
    quiet("PETG gap exactly 0.2", job("g20", settings=BRIM + "brim_object_gap = 0.2\n"))
    quiet("PETG gap 0.25", job("g25", settings=BRIM + "brim_object_gap = 0.25\n"))
    quiet("PETG no_brim, gap 0.1", job("nob", settings='brim_type = "no_brim"\nbrim_object_gap = 0.1\n'))
    quiet("PETG outer_only with brim_width 0", job("w0", settings='brim_type = "outer_only"\nbrim_width = 0\nbrim_object_gap = 0.1\n'))
    quiet("PETG brim_ears, gap 0.1 (not covered)", job("ears", settings='brim_type = "brim_ears"\nbrim_object_gap = 0.1\n'))
    quiet("PLA gap 0.1 outer_only", job("pla", (PLA,), BRIM + "brim_object_gap = 0.1\n"))

    # auto_brim: the preset gap (0.1) alone does not warn; a gap the job sets does, with the "if Studio adds a brim" words
    quiet("PETG default job (auto_brim, preset gap 0.1)", job("auto0"))
    w = fires("PETG auto_brim with gap 0.1 set in the job", job("auto1", settings="brim_object_gap = 0.1\n"))
    check("auto_brim text says 'if Studio adds a brim'", bool(w) and "if Studio adds a brim" in w[0], w)
    quiet("PETG auto_brim with gap 0.25 set in the job", job("auto2", settings="brim_object_gap = 0.25\n"))

    # object overrides
    quiet("object override gap 0.25 over a process gap 0.1", job("o1", settings=BRIM + "brim_object_gap = 0.1\n",
                                                               objects=(("brim_object_gap = 0.25\n", (1,)),)))
    fires("object override gap 0.1 over a process gap 0.25", job("o2", settings=BRIM + "brim_object_gap = 0.25\n",
                                                               objects=(("brim_object_gap = 0.1\n", (1,)),)))
    quiet("object override no_brim over a process outer_only gap 0.1", job("o3", settings=BRIM + "brim_object_gap = 0.1\n",
                                                                         objects=(('brim_type = "no_brim"\n', (1,)),)))
    fires("object override outer_only over a process no_brim", job("o4", settings='brim_type = "no_brim"\nbrim_object_gap = 0.1\n',
                                                                 objects=((BRIM, (1,)),)))
    w, _s = warns(job("o5", settings=BRIM + "brim_object_gap = 0.1\n", objects=(("brim_object_gap = 0.3\n", (1,)), ("", (1,)))))
    check("two objects: only the one with gap 0.1 warns", len(w) == 1 and "object O2" in w[0], w)

    # the first layer decides the filament: slot 2 is PLA, slot 1 is PETG; both parts are on the plate bottom, so PETG warns once
    w, _s = warns(job("m1", (PETG, PLA), BRIM + "brim_object_gap = 0.1\n", objects=(("", (1, 2)),)))
    check("PETG and PLA on the first layer: one warning, for the PETG slot", len(w) == 1 and "filament 1 " in w[0], w)
    quiet("PLA slot 1, PETG slot 2 unused", job("m2", (PLA, PETG), BRIM + "brim_object_gap = 0.1\n", objects=(("", (1,)),)))

    # warning only: the 3MF is built, the console summary and the summary file carry it, strict validation finds nothing new
    p = job("sum", (PETG_HF,), settings=BRIM + "brim_object_gap = 0.1\n")
    j = load_job(p)
    w, s = warns(p)
    check("warning does not block the build", os.path.isfile(s.path))
    check("warning in the console summary", bool(w) and w[0] in summary.format_summary(j, s))
    md = summary.format_summary_md(j, s)
    check("warning in the summary text file content (Warnings section)", bool(w) and w[0] in md and TEXT in md)
    r = validate.validate(s.path, mode="strict")
    check("strict validation: 0 errors", not r.errors, [str(f) for f in r.errors])
    check("strict validation: no finding mentions brim", not [f for f in r.findings if "brim" in str(f).lower()], [str(f) for f in r.findings])

    # tests/brim_check.py on the built files (no G-code here, so only the summary check is graded; the G-code checks must say NOT CHECKED)
    open(os.path.join(tmp, "sum.summary.md"), "w").write(md)
    raw = {"settings": {"brim_type": "outer_only", "brim_width": 5, "brim_object_gap": 0.1}, "filament": [{"preset": PETG}]}
    res = brim_check.checks(raw, os.path.join(tmp, "sum.3mf"), [])
    check("brim_check: summary text expected and found", res[0][1], res)
    check("brim_check: no G-code gives NOT CHECKED, not ok", all(not ok for _n, ok, _d in res[1:]) and len(res) == 3, res)
    raw["settings"]["brim_object_gap"] = 0.25
    res = brim_check.checks(raw, os.path.join(tmp, "sum.3mf"), [])
    check("brim_check: text found when the job does not expect it is a FAIL", not res[0][1], res)
    ok_path = os.path.join(tmp, "g.summary.md")
    open(ok_path, "w").write("nothing\n")
    res = brim_check.checks(raw, os.path.join(tmp, "g.3mf"), [])
    check("brim_check: no text and none expected is ok", res[0][1], res)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\nmeasured %d checks, %d failed" % (measured[0], len(fails)))
if measured[0] == 0:
    print("NOT CHECKED (0 measured)")
    sys.exit(1)
sys.exit(1 if fails else 0)
