#!/usr/bin/env python3
"""Calibration ledger ([[filament]] spool = "name"): every value becomes a changed filament setting, the summary names them, job settings win,
lookup rule (job key, calibration.toml next to the job, environment variable), clear errors, build + strict validation on 2.8.2, 2.9.1, 2.9.2.
Run: python3 -I tests/test_ledger.py"""
import json, os, sys, tempfile, zipfile
H = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(H)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf.job import load_job, JobError
from bambu3mf import package, summary, validate

fails = []
counts = {"n": 0}
def check(name, cond, extra=""):
    counts["n"] += 1
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)[:300]) if (extra and not cond) else ""))
    if not cond: fails.append(name)

PRESET = "Bambu PLA Basic @BBL P1S 0.4 nozzle"
LEDGER = '''[[spool]]
name = "Test red"
preset = "%s"
flow = 0.96
pa = 0.04
max_volumetric_speed = 17.5
nozzle_temp = 212
nozzle_temp_first_layer = 217
bed_temp = 57
bed_temp_first_layer = 62
date = "2026-01-15"
source = "test print"

[[spool]]
name = "Test white"
preset = "%s"
flow = 0.985
''' % (PRESET, PRESET)
MESH = os.path.join(ROOT, "examples", "data", "cube20.stl")
tmp = tempfile.mkdtemp()
_n = [0]

def head(studio="2.9.2", plate='plate_type = "Textured PEI Plate"\n', extra=""):
    return ('studio = "%s"\nprinter = "Bambu Lab P1S 0.4 nozzle"\nprocess = "0.20mm Standard @BBL X1C"\n%s%s' % (studio, plate, extra))
BODY = '[[object]]\nname = "Cube"\n[[object.part]]\nmesh = "%s"\nfilament = 1\n' % MESH.replace("\\", "/")

def folder():
    _n[0] += 1
    d = os.path.join(tmp, "t%d" % _n[0])
    os.makedirs(d)
    return d

def job(fil, ledger=LEDGER, studio="2.9.2", plate='plate_type = "Textured PEI Plate"\n', extra="", ledger_name="calibration.toml"):
    d = folder()
    if ledger is not None:
        open(os.path.join(d, ledger_name), "w").write(ledger)
    p = os.path.join(d, "j.toml")
    open(p, "w").write(head(studio, plate, extra) + fil + "\n" + BODY)
    return p

def fil(spool="Test red", preset=None, settings=""):
    t = "[[filament]]\n" + (('preset = "%s"\n' % preset) if preset else "") + 'spool = "%s"\ncolor = "#C12E1F"\n' % spool
    return t + (("[filament.settings]\n" + settings) if settings else "")

def errors(p):
    try:
        load_job(p)
    except JobError as e:
        return " | ".join(e.problems)
    return ""

def cfg_of(path_3mf):
    return json.loads(zipfile.ZipFile(path_3mf).read("Metadata/project_settings.config").decode())

# ---- 1. every ledger value is a changed setting ---------------------------------------------------
EXPECT = {"filament_flow_ratio": "0.96", "pressure_advance": "0.04", "filament_max_volumetric_speed": "17.5", "nozzle_temperature": "212",
          "nozzle_temperature_initial_layer": "217", "textured_plate_temp": "57", "textured_plate_temp_initial_layer": "62",
          "enable_pressure_advance": "1"}
for studio in ("2.8.2", "2.9.1", "2.9.2"):
    p = job(fil(), studio=studio)
    j = load_job(p)
    out = os.path.join(tmp, "led_%s.3mf" % studio)
    s = package.build(j, out)
    cfg = cfg_of(out)
    dl = cfg["different_settings_to_system"][1].split(";")          # entries: process, filament slot 1, printer
    for k, v in EXPECT.items():
        check("%s: %s = %s in project_settings.config" % (studio, k, v), cfg[k][0] == v, cfg.get(k))
        check("%s: %s is listed in different_settings_to_system" % (studio, k), k in dl, dl)
    rep = validate.validate(out, mode="strict")
    check("%s: validates clean (strict, 0 findings)" % studio, not rep.errors and not rep.warnings, [str(f) for f in (rep.errors + rep.warnings)][:3])
    check("%s: the slot took the spool's preset (slot has no preset)" % studio, cfg["filament_settings_id"][0] == PRESET, cfg["filament_settings_id"])

# ---- 2. the summary files name the spool and every value -------------------------------------------
j = load_job(job(fil()))
s = package.build(j, os.path.join(tmp, "sum.3mf"))
md = summary.format_summary_md(j, s)
check("summary.md names the spool", "`Test red`" in md)
check("summary.md names the ledger file without a folder", "`calibration.toml`" in md and tmp not in md)
check("summary.md has the date and the source", "2026-01-15" in md and "test print" in md)
check("summary.md lists each value applied", all("`%s` = `%s`" % (k, v) in md for k, v in EXPECT.items()), md)
console = summary.format_summary(j, s)
check("console summary names the spool and its ledger", "Test red" in console and "calibration.toml" in console and "0.96" in console)

# ---- 3. a spool with only some values applies only those ---------------------------------------------
j = load_job(job(fil("Test white")))
check("only flow is applied for a spool with only flow", set(j.filaments[0]["settings"]) == {"filament_flow_ratio"}, j.filaments[0]["settings"])

# ---- 4. preset rule -----------------------------------------------------------------------------------------
j = load_job(job(fil(preset=PRESET)))
check("slot preset equal to the spool's preset is accepted", j.filaments[0]["preset"] == PRESET)
e = errors(job(fil(preset="Bambu PLA Matte @BBL P1S 0.4 nozzle")))
check("preset mismatch is refused, naming both presets and the ledger", "Matte" in e and PRESET in e and "calibration.toml" in e, e)

# ---- 5. job settings win, with a warning ------------------------------------------------------------------
j = load_job(job(fil(settings='filament_flow_ratio = "0.9"\nnozzle_temp = 205\nenable_pressure_advance = 0\n')))
st = j.filaments[0]["settings"]
check("job flow wins over the ledger", st["filament_flow_ratio"] == "0.9", st)
check("job nozzle_temp (alias) wins over the ledger", st["nozzle_temperature"] == "205", st)
check("job enable_pressure_advance wins", st["enable_pressure_advance"] == "0", st)
check("other ledger values still apply", st["pressure_advance"] == "0.04" and st["textured_plate_temp"] == "57", st)
check("a warning for each key set twice", sum("is set in the job and in spool" in w for w in j.warnings) == 3, j.warnings)

# ---- 6. bed_temp needs plate_type ------------------------------------------------------------------------------
e = errors(job(fil(), plate=""))
check("bed_temp without plate_type is refused with a clear message", "plate_type" in e and "Test red" in e and "bed_temp" in e, e)
ok = load_job(job(fil("Test white"), plate=""))
check("a spool without bed_temp needs no plate_type", ok.filaments[0]["settings"]["filament_flow_ratio"] == "0.985")
j = load_job(job(fil(), plate='plate_type = "Cool Plate"\n'))
check("bed_temp follows plate_type (Cool Plate -> cool_plate_temp)", j.filaments[0]["settings"].get("cool_plate_temp") == "57" and "textured_plate_temp" not in j.filaments[0]["settings"], j.filaments[0]["settings"])

# ---- 7. errors --------------------------------------------------------------------------------------------------------
e = errors(job(fil("Test rde")))
check("unknown spool: names the ledger file and the closest names", "calibration.toml" in e and "Test red" in e and "Closest" in e, e)
e = errors(job(fil("zzzzzz")))
check("unknown spool with no close name lists the spools in the file", "calibration.toml" in e and "Test red" in e and "Test white" in e, e)
e = errors(job(fil(), ledger=None))
check("missing ledger: names where it looked", "no calibration ledger found" in e and "calibration.toml" in e and "BAMBU_CALIBRATION_LEDGER" in e and "ledger" in e, e)
e = errors(job(fil(), ledger=None, extra='ledger = "nothere.toml"\n'))
check("ledger key to a missing file is an error that names the file", "nothere.toml" in e and "does not exist" in e, e)
bad = LEDGER.replace("flow = 0.96", "flow = 3.5")
e = errors(job(fil(), ledger=bad))
check("value out of range: names the spool and the key", "Test red" in e and "flow" in e and "filament_flow_ratio" in e and "outside the allowed range" in e, e)
e = errors(job(fil(), ledger=LEDGER.replace("nozzle_temp = 212", "nozzle_temp = 900")))
check("nozzle temperature out of range for 2.9.2 is refused", "Test red" in e and "nozzle_temperature" in e, e)
e = errors(job(fil(), ledger=LEDGER.replace("flow = 0.96", 'flow = "abc"')))
check("a value that is not a number is refused", "flow" in e and "not a number" in e, e)
e = errors(job(fil(), ledger=LEDGER.replace('"2026-01-15"', '"15 Jan"')))
check("a bad date is refused", "date" in e and "YYYY-MM-DD" in e, e)
e = errors(job(fil(), ledger=LEDGER.replace('name = "Test white"', 'name = "Test red"')))
check("duplicate spool name is refused", "used twice" in e and "Test red" in e, e)
e = errors(job(fil(), ledger=LEDGER.replace("flow = 0.96", "flw = 0.96")))
check("an unknown spool key is refused with a close name", "flw" in e and "flow" in e, e)
e = errors(job(fil(), ledger=LEDGER.replace('preset = "%s"\nflow = 0.96' % PRESET, "flow = 0.96")))
check("a spool without preset is refused", "preset is required" in e, e)
e = errors(job(fil(), ledger="this is not toml ["))
check("a ledger that is not TOML is refused", "calibration.toml" in e and "could not be read" in e, e)

# ---- 8. lookup order ------------------------------------------------------------------------------------------------------
d = folder()
os.makedirs(os.path.join(d, "sub"))
open(os.path.join(d, "calibration.toml"), "w").write(LEDGER.replace("flow = 0.96", "flow = 0.91"))
open(os.path.join(d, "sub", "mine.toml"), "w").write(LEDGER.replace("flow = 0.96", "flow = 0.93"))
body = head() + "%s\n" + fil() + "\n" + BODY
open(os.path.join(d, "a.toml"), "w").write(body % 'ledger = "sub/mine.toml"\n')
open(os.path.join(d, "b.toml"), "w").write(body % "")
check("job key ledger wins over calibration.toml next to the job", load_job(os.path.join(d, "a.toml")).filaments[0]["settings"]["filament_flow_ratio"] == "0.93")
check("calibration.toml next to the job is used without a job key", load_job(os.path.join(d, "b.toml")).filaments[0]["settings"]["filament_flow_ratio"] == "0.91")
env_ledger = os.path.join(tmp, "env.toml")
open(env_ledger, "w").write(LEDGER.replace("flow = 0.96", "flow = 0.95"))
pj = job(fil(), ledger=None)
os.environ["BAMBU_CALIBRATION_LEDGER"] = env_ledger
try:
    check("the environment variable is the last choice", load_job(pj).filaments[0]["settings"]["filament_flow_ratio"] == "0.95")
    os.environ["BAMBU_CALIBRATION_LEDGER"] = os.path.join(tmp, "gone.toml")
    e = errors(pj)
    check("environment variable to a missing file is an error that names the variable", "BAMBU_CALIBRATION_LEDGER" in e and "gone.toml" in e, e)
finally:
    del os.environ["BAMBU_CALIBRATION_LEDGER"]

# ---- 9. a job without spool is untouched; the job key alone does nothing ----------------------------------------------
d = folder()
p = os.path.join(d, "n.toml")
open(p, "w").write(head() + 'ledger = "nothere.toml"\n[[filament]]\npreset = "%s"\ncolor = "#C12E1F"\n' % PRESET + BODY)
check("a job with no spool does not read the ledger (a missing file is no error)", errors(p) == "", errors(p))

# ---- 10. the shipped example --------------------------------------------------------------------------------------------------
ex = os.path.join(ROOT, "examples", "ledger_spool.toml")
jx = load_job(ex)
out = os.path.join(tmp, "example.3mf")
package.build(jx, out)
rep = validate.validate(out, mode="strict")
check("examples/ledger_spool.toml builds and validates strict", not rep.errors and not rep.warnings, [str(f) for f in (rep.errors + rep.warnings)][:3])
sys.path.insert(0, H)
from harness import list_examples
check("the ledger file is not in the example list", "calibration.toml" not in list_examples() and "ledger_spool.toml" in list_examples(), list_examples())
from bambu3mf import ledger as L
sp = L.load_ledger(os.path.join(ROOT, "examples", "ledger", "calibration.toml"))
check("the example ledger loads (3 spools, all marked as made up)", len(sp) == 3 and all("made-up" in s.get("source", "") for s in sp.values()), list(sp))

print("%d checks, %d failed" % (counts["n"], len(fails)))
sys.exit(1 if fails else 0)
