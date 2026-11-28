#!/usr/bin/env python3
"""[calibration] type = "tolerance": square holes and pegs with labelled clearances.
Meshes closed (edge report 0, 0, 0) for every part; the clearance measured in the mesh equals its label within 0.01 mm (hole side -
peg side, halved, on x and on y); label count and text; strict validation 0 findings; bad keys refused; builds on 2.8.2, 2.9.1, 2.9.2;
the forward-check module (tests/calib_check_tolerance.py) passes on a real build and fails on a changed expectation.
Run: python3 -I tests/test_calib_tolerance.py"""
import os, sys, tempfile
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
sys.path.insert(0, H)
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import calibjobs, package, validate  # noqa: E402
from bambu3mf.job import load_job, JobError  # noqa: E402
import calib_check_tolerance as cc  # noqa: E402

fails = []
measured = 0


def check(name, cond, extra=""):
    global measured
    measured += 1
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)[:400]) if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


tmp = tempfile.mkdtemp()


def job_file(cal, studio="2.9.2"):
    p = os.path.join(tmp, "j%d.toml" % len(os.listdir(tmp)))
    with open(p, "w") as fh:
        fh.write('studio = "%s"\nprinter = "Bambu Lab P1S 0.4 nozzle"\nprocess = "0.20mm Standard @BBL X1C"\n'
                 '[[filament]]\npreset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"\ncolor = "#FF8000"\n[calibration]\ntype = "tolerance"\n%s\n' % (studio, cal))
    return p


def load(cal, studio="2.9.2"):
    try:
        return load_job(job_file(cal, studio)), ""
    except JobError as e:
        return None, "; ".join(e.problems)


# ---- default job: geometry in the job's meshes -----------------------------------------------------------
j, err = load("")
check("defaults: loads", j is not None, err)
objs = {o["name"]: o for o in j.objects}
check("defaults: 5 clearances -> 10 objects, Hole and Peg for each", len(j.objects) == 10 and all(("Hole %s" % c) in objs and ("Peg %s" % c) in objs for c in ("0.1", "0.15", "0.2", "0.25", "0.3")), list(objs))
n_parts = n_closed = n_neg = n_lab_ok = 0
for o in j.objects:
    for p in o["parts"]:
        n_parts += 1
        n_closed += p["mesh_obj"].edge_report() == (0, 0, 0) and p["mesh_obj"].volume() > 0
        n_neg += p["type"] == "negative_part"
check("every part mesh is a closed manifold with positive volume (%d parts)" % n_parts, n_parts == 20 and n_closed == n_parts, (n_parts, n_closed))
check("one negative part (the label) per object, 10 in all", n_neg == 10, n_neg)
nmeas = 0
bad = []
for c in (0.1, 0.15, 0.2, 0.25, 0.3):
    t = cc.fmt(c)
    hg = cc.hole_gap(objs["Hole " + t]["parts"][0]["mesh_obj"])
    ps = cc.peg_size(objs["Peg " + t]["parts"][0]["mesh_obj"])
    for a, b in ((hg[0], ps[0]), (hg[1], ps[1])):
        nmeas += 1
        if abs((a - b) / 2.0 - c) > 0.01:
            bad.append((t, (a - b) / 2.0))
    lab = objs["Hole " + t]["parts"][1]
    n_lab_ok += lab["name"] == "Label " + t and len(cc.shells(lab["mesh_obj"])) == cc.label_boxes(t) == len(cc.shells(objs["Peg " + t]["parts"][1]["mesh_obj"]))
check("clearance measured in the mesh = label within 0.01 mm (%d measurements: x and y of 5 pairs)" % nmeas, nmeas == 10 and not bad, bad)
check("peg is 10 x 10 mm and 6 mm above the flange; hole block is 6 mm thick", all(abs(v - w) < 1e-6 for v, w in zip(cc.peg_size(objs["Peg 0.2"]["parts"][0]["mesh_obj"]), (10, 10, 6)))
      and abs(objs["Hole 0.2"]["parts"][0]["mesh_obj"].bbox()[1][2] - 6) < 1e-6)
check("labels: 5 clearances x 2 objects, name and stroke count match the text (5 pairs)", n_lab_ok == 5, n_lab_ok)
lo, hi = objs["Hole 0.15"]["parts"][1]["mesh_obj"].bbox()
check("label width = label_size of the text, 0.5 mm engraved into the 1.6 mm ledge (bottom at z 1.1)", abs((hi[0] - lo[0]) - calibjobs.label_size("0.15")[0]) < 1e-6 and abs(lo[2] - 1.1) < 1e-6, (lo, hi))
hs = cc.shells(objs["Hole 0.1"]["parts"][0]["mesh_obj"])
check("a hole mesh has four full-height strips around the hole (a real hole, not a negative part) and a 1.6 mm label ledge",
      len(hs) == 5 and sum(1 for b in hs if abs(b[1][2] - 6) < 1e-6) == 4 and sum(1 for b in hs if abs(b[1][2] - 1.6) < 1e-6) == 1, [b[1][2] for b in hs])

# ---- other keys -------------------------------------------------------------------------------------------
j2, err = load('clearances = [0.05, 0.125, 0.4]\nsize = 8\ndepth = 4')
check("own keys: 3 clearances, size 8, depth 4 -> 6 objects", j2 is not None and len(j2.objects) == 6, err)
if j2:
    o2 = {o["name"]: o for o in j2.objects}
    bad = []
    nm = 0
    for c, t in ((0.05, "0.05"), (0.125, "0.125"), (0.4, "0.4")):
        hg, ps = cc.hole_gap(o2["Hole " + t]["parts"][0]["mesh_obj"]), cc.peg_size(o2["Peg " + t]["parts"][0]["mesh_obj"])
        for a, b in ((hg[0], ps[0]), (hg[1], ps[1])):
            nm += 1
            if abs((a - b) / 2.0 - c) > 0.01 or abs(b - 8) > 1e-6:
                bad.append((t, a, b))
    check("own keys: measured clearance = label (%d measurements), peg side 8" % nm, nm == 6 and not bad, bad)
    check("own keys: all parts closed", all(p["mesh_obj"].edge_report() == (0, 0, 0) for o in j2.objects for p in o["parts"]))
    check("own keys: info for the summary", j2.calib_job["info"] == {"clearances": [0.05, 0.125, 0.4], "size": 8.0, "depth": 4.0}, j2.calib_job["info"])

# ---- bad keys refused -------------------------------------------------------------------------------------
for name, cal, text in [
        ("unknown key", 'gap = 0.2', "unknown key"),
        ("another type's key", 'count = 3', "unknown key"),
        ("clearance too small", 'clearances = [0.01, 0.2]', "outside 0.02 to 1"),
        ("clearance too big", 'clearances = [0.2, 1.5]', "outside 0.02 to 1"),
        ("clearances not a list", 'clearances = 0.2', "list of 1 to 8 numbers"),
        ("nine clearances", 'clearances = [0.1, 0.12, 0.14, 0.16, 0.18, 0.2, 0.22, 0.24, 0.26]', "list of 1 to 8 numbers"),
        ("empty clearances", 'clearances = []', "list of 1 to 8 numbers"),
        ("text in clearances", 'clearances = ["a", 0.2]', "list of 1 to 8 numbers"),
        ("duplicate clearance", 'clearances = [0.2, 0.2]', "listed twice"),
        ("size too small", 'size = 2', "outside 4 to 30"),
        ("size too big", 'size = 40', "outside 4 to 30"),
        ("size is text", 'size = "ten"', "not a number"),
        ("depth too small", 'depth = 1', "outside 3 to 20"),
        ("depth too big", 'depth = 25', "outside 3 to 20")]:
    jj, err = load(cal)
    check("refused: %s" % name, jj is None and text in err, err)

# ---- built files: 2.8.2, 2.9.1, 2.9.2 -------------------------------------------------------------------------
for ver in ("2.8.2", "2.9.1", "2.9.2"):
    jv, err = load('clearances = [0.1, 0.2, 0.3]', ver)
    check("%s: loads" % ver, jv is not None, err)
    if jv is None:
        continue
    out = os.path.join(tmp, "tol_%s.3mf" % ver)
    package.build(jv, out)
    r = validate.validate(out, mode="strict")
    check("%s: built file validates strict, 0 findings" % ver, r.ok and not r.findings, [(f.rule, f.msg[:90]) for f in r.findings])
    res = cc.checks({"calibration": {"clearances": [0.1, 0.2, 0.3]}}, out, ["fake_plate_1.gcode"])
    check("%s: forward-check module passes on the build (%d checks)" % (ver, len(res)), len(res) == 5 and all(ok for _n, ok, _d in res), [(n, d) for n, ok, d in res if not ok])
    if ver == "2.9.2":
        wrong = cc.checks({"calibration": {"clearances": [0.1, 0.2, 0.35]}}, out, [])
        check("forward-check module fails when the job asks for other clearances", not wrong[0][1] and not wrong[-1][1], wrong)
        wrong = cc.checks({"calibration": {"clearances": [0.1, 0.2, 0.3], "size": 12}}, out, [])
        check("forward-check module fails when the peg size is not the one asked for", not wrong[2][1], wrong[2])

# 1.0 mm labels floor: the thinnest face is the flange (2 mm, 0.5 mm cut -> 1.5 mm left)
check("label floor: flange 2 mm leaves at least 1.0 mm under the cut", 2.0 - calibjobs.LABEL_DEPTH >= calibjobs.LABEL_MIN_FLOOR)

print("\n%d checks measured" % measured)
if measured == 0:
    print("NOT CHECKED")
print("%d failed" % len(fails) if fails else "all passed")
sys.exit(1 if fails or measured == 0 else 0)
