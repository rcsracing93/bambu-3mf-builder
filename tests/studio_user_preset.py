#!/usr/bin/env python3
"""Step 7, real Studio: build a file from a FAKE user-preset folder (invented names, made here), slice it in a real Bambu Studio and
read the G-code config block. The values the user presets changed must be in the G-code, the system slot must keep its own.
Usage: python3 tests/studio_user_preset.py --studio-dir DIR --version 2.8.2|2.9.1     (DIR holds bin/bambu-studio; needs a display or xvfb-run)
Exit 0 = all checks ok. Nothing is written into the repo."""
import argparse, glob, json, os, shutil, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, HERE)
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import package, validate  # noqa: E402
from bambu3mf.job import load_job  # noqa: E402
import harness as H  # noqa: E402
import studio_slice as SS  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--studio-dir", required=True)
ap.add_argument("--version", required=True, choices=["2.8.2", "2.9.1"])
a = ap.parse_args()

fails = []
def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)) if (extra and not cond) else ""))
    if not cond: fails.append(name)

tmp = tempfile.mkdtemp(prefix="up_studio_")
ID = os.path.join(tmp, "BambuStudio", "user", "42")
for sub in ("filament", "process"):
    os.makedirs(os.path.join(ID, sub))
FIL_PARENT, PRO_PARENT = "Bambu PLA Basic @BBL P1S 0.4 nozzle", "0.20mm Standard @BBL X1C"
json.dump({"type": "filament", "name": "Test PLA Tuned", "from": "User", "inherits": FIL_PARENT, "version": "02.00.00.00",
           "filament_flow_ratio": ["0.91", "0.915"], "nozzle_temperature": ["212", "214"]},
          open(os.path.join(ID, "filament", "Test PLA Tuned.json"), "w"))
json.dump({"type": "process", "name": "Test Process Tuned", "from": "User", "inherits": PRO_PARENT, "version": "02.00.00.00",
           "wall_loops": "5", "sparse_infill_density": "12%"}, open(os.path.join(ID, "process", "Test Process Tuned.json"), "w"))
jp = os.path.join(tmp, "up.toml")
open(jp, "w").write('studio = "%s"\noutput = "up.3mf"\nprinter = "Bambu Lab P1S 0.4 nozzle"\nprocess = "Test Process Tuned"\n'
                    '[settings]\nsparse_infill_pattern = "grid"\n[[filament]]\npreset = "Test PLA Tuned"\ncolor = "#111111"\n'
                    '[[filament]]\npreset = "%s"\ncolor = "#FFFFFF"\n[[object]]\nname = "Cube"\n[[object.part]]\nname = "A"\nmesh = "%s"\nfilament = 1\n'
                    '[[object.part]]\nname = "B"\nmesh = "%s"\nfilament = 2\n' % (
                        a.version, FIL_PARENT, os.path.join(ROOT, "examples", "data", "slab1.stl"), os.path.join(ROOT, "examples", "data", "slab2.stl")))
job = load_job(jp, user_presets=os.path.join(tmp, "BambuStudio"))
s = package.build(job)
check("built file validates, 0 errors", not validate.validate(s.path, mode="strict").errors)
out = os.path.join(tmp, "out")
os.makedirs(out)
rc, log, secs = SS.run_studio(a.studio_dir, ["--debug", "2", "--slice", "0", "--outputdir", out, s.path], tmp)
res = SS.read_result(out)
gc = SS.gcode_files(out)
check("Studio slices it (rc 0, result 0, one plate)", rc == 0 and res and res.get("return_code") == 0 and len(gc) == 1, (rc, res, log[-300:]))
if gc:
    blk = H.config_block(gc[0])
    def vals(k):
        return [x.strip() for x in blk.get(k, "").replace(";", ",").split(",")]
    fr, nt = vals("filament_flow_ratio"), vals("nozzle_temperature")
    check("G-code: slot 1 flow ratio is the user preset's, slot 2 the system value", fr[0] == "0.91" and fr[1] == "0.98", fr)
    check("G-code: slot 1 nozzle temperature is the user preset's, slot 2 the system value", nt[0] == "212" and nt[1] == "220", nt)
    check("G-code: process wall loops and infill density are the user process preset's", blk.get("wall_loops") == "5" and blk.get("sparse_infill_density") == "12%",
          (blk.get("wall_loops"), blk.get("sparse_infill_density")))
    print("  config block: flow %s | nozzle %s | wall_loops %s | infill %s" % (fr, nt, blk.get("wall_loops"), blk.get("sparse_infill_density")))
shutil.rmtree(tmp, ignore_errors=True)
print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
