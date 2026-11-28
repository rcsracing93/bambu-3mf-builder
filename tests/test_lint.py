#!/usr/bin/env python3
"""Settings lint (S3): each rule fires and does not fire; no lint warning on any example; the lint never blocks a build; the lint warnings
reach the summary file. Bed temperature 0 on the selected plate is an error: the builder stops and the validator reports R39.
Run: python3 -I tests/test_lint.py"""
import glob, io, json, os, shutil, sys, tempfile, zipfile
H = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(H)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, H)
from harness import list_examples  # noqa: E402
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import package, summary, validate
from bambu3mf.job import load_job, JobError

fails = []
def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)[:400]) if (extra and not cond) else ""))
    if not cond: fails.append(name)

tmp = tempfile.mkdtemp()


def box(path, w, d, h, z0=0.0):
    x0, y0, x1, y1, z1 = 0.0, 0.0, w, d, z0 + h
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    f = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    with open(path, "w") as fh:
        fh.write("solid b\n")
        for a, b, c in f:
            fh.write("facet normal 0 0 0\n outer loop\n")
            for i in (a, b, c):
                fh.write("  vertex %r %r %r\n" % v[i])
            fh.write(" endloop\nendfacet\n")
        fh.write("endsolid b\n")


BASE = os.path.join(tmp, "base.stl"); box(BASE, 20, 20, 10)
THIN = os.path.join(tmp, "thin.stl"); box(THIN, 20, 20, 0.1, z0=10.0)
OK = os.path.join(tmp, "ok.stl"); box(OK, 20, 20, 0.4, z0=10.0)
PLA = "Bambu PLA Basic @BBL P1S 0.4 nozzle"


def job(name, fil_settings=None, parts=None, gcode="", printer="Bambu Lab P1S 0.4 nozzle", process="0.20mm Standard @BBL X1C",
        filament=PLA, plate=""):
    """Writes a job into tmp and returns its path. parts: list of (mesh, filament number)."""
    parts = parts or [(BASE, 1)]
    nf = max(f for _m, f in parts)
    t = 'studio = "2.9.1"\noutput = "%s.3mf"\nprinter = "%s"\nprocess = "%s"\n%s\n' % (name, printer, process, plate)
    for i in range(nf):
        t += '[[filament]]\npreset = "%s"\ncolor = "#%s"\n' % (filament, "112233" if i == 0 else "FFEEDD")
        if fil_settings and i == 0:
            t += "[filament.settings]\n" + "".join('%s = "%s"\n' % kv for kv in fil_settings.items())
    t += gcode
    t += '[[object]]\nname = "O"\n'
    for j, (m, f) in enumerate(parts, 1):
        t += '[[object.part]]\nname = "P%d"\nmesh = "%s"\nfilament = %d\n' % (j, m, f)
    p = os.path.join(tmp, name + ".toml")
    open(p, "w").write(t)
    return p


def lints(path):
    s = package.build(load_job(path), os.path.join(tmp, os.path.basename(path)[:-5] + ".3mf"))
    return [w for w in s.warnings if w.startswith("lint:")], s


def fires(name, path, word):
    w, _s = lints(path)
    check(name + ": fires", any(word in x for x in w), w)


def quiet(name, path):
    w, _s = lints(path)
    check(name + ": does not fire", not w, w)


try:
    # Rule 1 nozzle temperature against nozzle_temperature_range_low / high
    fires("R1 nozzle temperature above the range", job("r1a", {"nozzle_temp": "300"}), "nozzle temperature 300 C is outside")
    fires("R1 first layer temperature below the range", job("r1b", {"nozzle_temp_first_layer": "150"}), "first layer nozzle temperature 150 C is outside")
    quiet("R1 default temperatures", job("r1c"))
    quiet("R1 temperature inside the range", job("r1d", {"nozzle_temp": "215", "nozzle_temp_first_layer": "215"}))

    # Rule 4 chamber_temperatures >= 40 against support_chamber_temp_control = 0
    fires("R4 chamber 60 C on a printer without chamber control", job("r4a", {"chamber_temperatures": "60"}), "support_chamber_temp_control")
    quiet("R4 chamber 30 C on the same printer", job("r4b", {"chamber_temperatures": "30"}))
    try:
        w, _s = lints(job("r4c", {"chamber_temperatures": "60"}, printer="Bambu Lab X1E 0.4 nozzle", filament="Bambu ABS @BBL X1E"))
        check("R4 chamber 60 C on a printer with chamber control (X1E): does not fire", not [x for x in w if "chamber" in x], w)
    except JobError as e:
        check("R4 chamber control printer builds (X1E)", False, e.problems)

    # Rule 5 inlay thinner than one layer (two pauses closer than a layer cannot be built: layer_gcode z must be a layer top)
    fires("R5 inlay 0.1 mm high on 0.2 mm layers", job("r5a", parts=[(BASE, 1), (THIN, 2)]), "less than one layer")
    quiet("R5 inlay 0.4 mm high", job("r5b", parts=[(BASE, 1), (OK, 2)]))
    quiet("R5 thin part with the base filament", job("r5c", parts=[(BASE, 1), (THIN, 1)]))

    # warnings only: the build is not blocked, the console summary and the summary file carry them
    w, s = lints(job("w1", {"nozzle_temp": "300"}))
    check("lint warning does not block the build", os.path.isfile(s.path))
    check("lint warning in the console summary", any(x in summary.format_summary(load_job(os.path.join(tmp, "w1.toml")), s) for x in w))
    j = load_job(os.path.join(tmp, "w1.toml"))
    check("lint warning in the summary file", all(x in summary.format_summary_md(j, s) for x in w) and bool(w))

    # bed temperature 0 on the selected plate is an error, not a lint
    try:
        load_job(job("bed0", {"textured_plate_temp": "0"}, plate='plate_type = "Textured PEI Plate"'))
        check("bed temperature 0 on the selected plate stops the job", False)
    except JobError as e:
        check("bed temperature 0 on the selected plate stops the job", "does not support" in str(e.problems), e.problems)
    good = os.path.join(tmp, "r1c.3mf")
    package.build(load_job(os.path.join(tmp, "r1c.toml")), good)
    def bed0(name, plate_key):
        zin = zipfile.ZipFile(good); p = os.path.join(tmp, name)
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zo:
            for i in zin.infolist():
                d = zin.read(i.filename)
                if i.filename == "Metadata/project_settings.config":
                    c = json.loads(d); c[plate_key] = ["0"] * len(c[plate_key]); d = json.dumps(c, indent=4).encode()
                zo.writestr(i.filename, d)
        return p
    c = json.loads(zipfile.ZipFile(good).read("Metadata/project_settings.config"))
    sel = {"Cool Plate": "cool_plate_temp", "Textured PEI Plate": "textured_plate_temp", "Engineering Plate": "eng_plate_temp",
           "High Temp Plate": "hot_plate_temp"}[c["curr_bed_type"]]
    other = "textured_plate_temp" if sel != "textured_plate_temp" else "cool_plate_temp"
    r = validate.validate(bed0("bed0_sel.3mf", sel), mode="strict")
    check("validator: bed temperature 0 on the selected plate is error R39", "R39" in [f.rule for f in r.errors], [str(f) for f in r.findings])
    r = validate.validate(bed0("bed0_other.3mf", other), mode="strict")
    check("validator: bed temperature 0 on another plate type is not R39", "R39" not in [f.rule for f in r.findings], [str(f) for f in r.findings])

    # no lint warning on any example
    for ex in list_examples(paths=True):
        d = os.path.join(tmp, "ex_" + os.path.basename(ex)[:-5]); os.makedirs(d)
        s = package.build(load_job(ex), os.path.join(d, "o.3mf"))
        lw = [x for x in s.warnings if x.startswith("lint:")]
        if os.path.basename(ex) == "petg_brim_lint.toml":
            # This one example shows the PETG brim warning on purpose (tests/test_brim_lint.py covers the rule): exactly that one warning.
            check("example petg_brim_lint.toml: exactly the PETG brim warning", len(lw) == 1 and "PETG fuses to a tight brim" in lw[0], lw)
            continue
        check("example %s: no lint warning" % os.path.basename(ex), not lw, s.warnings)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n%d failed" % len(fails))
sys.exit(1 if fails else 0)
