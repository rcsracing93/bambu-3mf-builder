#!/usr/bin/env python3
"""Step 5. A printer, process or filament name that is not an exact system preset of the chosen Studio version fails.
The error names the three closest presets. It never falls back to a near match or a default. Run: python3 -I tests/test_exact_presets.py"""
import os, sys, tempfile
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
from bambu3mf.profiles import ProfileSet, ProfileError  # noqa: E402
from bambu3mf.job import load_job  # noqa: E402

fails = []
def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)) if (extra and not cond) else ""))
    if not cond: fails.append(name)

V282, V291 = "2.8.2.61", "2.9.1.70"
PS = {V282: ProfileSet(V282), V291: ProfileSet(V291)}
GOOD = {"machine": "Bambu Lab P1S 0.4 nozzle", "process": "0.20mm Standard @BBL X1C",
        "filament": "Bambu PLA Basic @BBL P1S 0.4 nozzle"}
NBSP, ZWSP = " ", "​"
# (label, kind, bad name, exact name it is closest to, True when only case/spaces/hidden characters differ)
CASES = [
    ("case", "machine", "bambu lab p1s 0.4 nozzle", GOOD["machine"], True),
    ("case", "process", "0.20mm standard @bbl x1c", GOOD["process"], True),
    ("case", "filament", "bambu pla basic @bbl p1s 0.4 nozzle", GOOD["filament"], True),
    ("extra space", "machine", "Bambu Lab  P1S 0.4 nozzle", GOOD["machine"], True),
    ("extra space", "process", "0.20mm Standard @BBL  X1C", GOOD["process"], True),
    ("extra space", "filament", "Bambu PLA Basic @BBL P1S  0.4 nozzle", GOOD["filament"], True),
    ("trailing space", "machine", GOOD["machine"] + " ", GOOD["machine"], True),
    ("trailing space", "process", GOOD["process"] + " ", GOOD["process"], True),
    ("trailing space", "filament", GOOD["filament"] + " ", GOOD["filament"], True),
    ("missing space", "machine", "Bambu LabP1S 0.4 nozzle", GOOD["machine"], True),
    ("missing space", "process", "0.20mm Standard@BBL X1C", GOOD["process"], True),
    ("missing space", "filament", "Bambu PLA Basic @BBL P1S0.4 nozzle", GOOD["filament"], True),
    ("variant X1C for P1S", "machine", "Bambu Lab X1C 0.4 nozzle", None, False),
    ("variant P1S for X1C", "process", "0.20mm Standard @BBL P1S", "0.20mm Standard @BBL P1P", False),
    ("variant X1C for P1S", "filament", "Bambu PLA Basic @BBL X1C 0.4 nozzle", None, False),
    ("trailing 0.4 nozzle", "machine", "Bambu Lab P1S 0.4 nozzle 0.4 nozzle", GOOD["machine"], False),
    ("trailing 0.4 nozzle", "process", GOOD["process"] + " 0.4 nozzle", None, False),
    ("trailing 0.4 nozzle", "filament", "Bambu PLA Basic @BBL P1S 0.4 nozzle 0.4 nozzle", GOOD["filament"], False),
    ("missing 0.4 nozzle", "filament", "Bambu PLA Basic @BBL P1S", None, False),
    ("non-ASCII look-alike", "machine", "Bambu Lab P1S 0.4 nozzIe".replace("I", "Ι"), GOOD["machine"], False),
    ("non-ASCII look-alike", "process", "0.20mm Standard @BBL X1С", GOOD["process"], False),
    ("non-ASCII look-alike", "filament", "Bambu PLA Basic @BBL P1S 0.4 nozzle".replace("a", "а"), GOOD["filament"], False),
    ("non-breaking space", "machine", GOOD["machine"].replace(" 0.4", NBSP + "0.4"), GOOD["machine"], True),
    ("non-breaking space", "process", GOOD["process"].replace("Standard ", "Standard" + NBSP), GOOD["process"], True),
    ("non-breaking space", "filament", GOOD["filament"] + NBSP, GOOD["filament"], True),
    ("zero-width space", "machine", GOOD["machine"] + ZWSP, GOOD["machine"], True),
    ("zero-width space", "process", ZWSP + GOOD["process"], GOOD["process"], True),
    ("zero-width space", "filament", GOOD["filament"] + ZWSP, GOOD["filament"], True),
]

def miss(ps, kind, name):
    try:
        ps.resolve(kind, name)
    except ProfileError as e:
        return str(e)
    return None

for label, kind, bad, near, only_case in CASES:
    for ver, ps in PS.items():
        w = "%s %s %s" % (kind, label, ver)
        msg = miss(ps, kind, bad)
        check(w + ": fails", msg is not None, "resolved without error")
        if msg is None:
            continue
        hint = msg.split("Closest: ", 1)[1].split("; ") if "Closest: " in msg else []
        check(w + ": names at most three closest presets, all exact", 1 <= len(hint) <= 3 and all(h in ps.names(kind) for h in hint), hint)
        check(w + ": the exact name is among them", near is None or near in hint, (near, hint))
        check(w + ": message says %s" % ("case/spaces only" if only_case else "names are exact"),
              ("only case, spaces" in msg) == only_case and ("exact" in msg), msg[:160])

# the exact names still resolve in both versions
for kind, name in GOOD.items():
    for ver, ps in PS.items():
        check("exact %s %r resolves in %s" % (kind, name, ver), miss(ps, kind, name) is None)
# a preset from the other Studio version
for kind, only in (("machine", "Bambu Lab H3C 0.4 nozzle"), ("process", "0.08mm High Quality @BBL H3C"), ("filament", "Bambu ABS @BBL H3C")):
    check("other version %s: %r exists in 2.9.1" % (kind, only), miss(PS[V291], kind, only) is None)
    msg = miss(PS[V282], kind, only)
    check("other version %s: %r fails in 2.8.2 with closest names" % (kind, only), msg is not None and "Closest: " in msg and "Studio 2.8.2.61" in msg, msg)
check("other version filament: 2.8.2-only preset fails in 2.9.1", miss(PS[V291], "filament", "Bambu PLA Glow @BBL X2D 0.2 nozzle") is not None)

# ---- through the job file: the job fails, never falls back ---------------------------------------------------------------------------
tmp = tempfile.mkdtemp()
BASE = open(os.path.join(H, "data", "mutation_base.toml")).read()
def run(old, new, studio="2.8.2"):
    t = BASE.replace(old, new).replace('studio  = "2.8.2"', 'studio  = "%s"' % studio)
    p = os.path.join(H, "data", "_exact_presets_tmp.toml")      # next to the base job, so its relative mesh paths resolve
    open(p, "w").write(t)
    try:
        load_job(p)
    except Exception as e:
        return str(e)
    finally:
        os.remove(p)
    return None
check("job: exact names load", run("x", "x") is None)
for field, old, bad in (("printer", 'printer = "Bambu Lab P1S 0.4 nozzle"', 'printer = "bambu lab p1s 0.4 nozzle"'),
                        ("process", 'process = "0.20mm Standard @BBL X1C"', 'process = "0.20mm standard @bbl x1c"'),
                        ("filament", 'preset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"', 'preset = "bambu pla basic @bbl p1s 0.4 nozzle"')):
    msg = run(old, bad)
    check("job %s: wrong case fails, no fallback" % field, msg is not None and "No " in msg and "only case, spaces" in msg and "Closest:" in msg, msg and msg[:300])
msg = run('printer = "Bambu Lab P1S 0.4 nozzle"', 'printer = "Bambu Lab H3C 0.4 nozzle"')
check("job printer: 2.9.1 printer in a 2.8.2 job fails", msg is not None and "Closest:" in msg, msg and msg[:300])

print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
