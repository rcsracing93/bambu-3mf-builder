#!/usr/bin/env python3
"""The slice harness must FAIL on broken input and name the cause. These cases need no Studio:
  partial list match, error line in the slice log, swapped filaments (layer tools), tower on the model (G-code),
  and the validator rules for the three file breakages (tests/mutations.py). The same breakages run through a real
  Studio in tests/studio_mutation.py."""
import os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import harness as H  # noqa: E402
import mutations as M  # noqa: E402
from bambu3mf import job as jobmod, package, validate  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print("  %-70s %s" % (name, "ok" if cond else "FAIL %s" % (extra,)))
    if not cond:
        fails.append(name)


# 1. partial list match in the config compare -------------------------------------------------------------------------------
check("same_value: exact list matches", H.same_value(["1.26", "1.32", "1.26", "1.26"], "1.26,1.32,1.26,1.26", None, 4))
check("same_value: G-code list one entry short does not match", not H.same_value(["1.26", "1.32", "1.26", "1.26"], "1.26,1.32,1.26", None, 4))
check("same_value: written list is a prefix of the G-code list does not match", not H.same_value(["a", "b"], "a,b,c", None, 3))
check("same_value: first entry only does not match a longer list", not H.same_value(["PLA", "PETG"], "PLA", None, 2))
check("same_value: substring of one entry does not match", not H.same_value("35%", "135%"))
differ, missing = H.compare_config({"filament_density": ["1.26", "1.32", "1.26", "1.26"]}, {"filament_density": "1.26,1.32,1.26"}, {}, 4)
check("compare_config reports the short list as differing", len(differ) == 1 and not missing)
differ, missing = H.compare_config({"filament_density": ["1.26", "1.32"]}, {}, {}, 2)
check("compare_config reports a key absent from the G-code as missing", not differ and missing == ["filament_density"])

# 1b. only the ACTIVE variant may match (P1S prints the Direct Drive Standard entries, not High Flow)
cfg2 = {"extruder_type": ["Direct Drive"], "nozzle_volume_type": ["Standard"],
        "filament_extruder_variant": ["Direct Drive Standard", "Direct Drive High Flow"] * 2}
opt = {"preset": "filament", "variant": "filament", "variant_stride": 1}
check("same_value: active variant (Standard) matches", H.same_value(["0.98", "0.985", "0.98", "0.985"], "0.98,0.98", opt, 2, cfg2))
check("same_value: the other variant (High Flow) does not match", not H.same_value(["0.98", "0.985", "0.98", "0.985"], "0.985,0.985", opt, 2, cfg2))
check("same_value: unknown active variant matches nothing", not H.same_value(["0.98", "0.985"], "0.98", opt, 1, {}))

# 1c. job intent is read from the job FILE
raw = {"printer": "P", "process": "Q", "plate_type": "Textured PEI Plate", "settings": {"walls": 4},
       "filament": [{"preset": "A", "color": "#111111", "settings": {"bed_temp": 66}}, {"preset": "B", "color": "#ffffff"}],
       "object": [{"plate": 1}, {"plate": 2}]}
blk = {"wall_loops": "4", "textured_plate_temp": "66,55", "filament_colour": "#111111;#FFFFFF",
       "filament_settings_id": '"A";"B"', "printer_settings_id": '"P"', "print_settings_id": '"Q"'}
check("job_intent: G-code that follows the job file passes", H.job_intent(raw, None, blk, 2) == [], H.job_intent(raw, None, blk, 2))
for k, v, what in (("wall_loops", "2", "process setting (alias walls)"), ("textured_plate_temp", "60,55", "filament setting (alias bed_temp)"),
                   ("filament_colour", "#111111;#00FF00", "filament color"), ("filament_settings_id", '"A";"C"', "filament preset"),
                   ("printer_settings_id", '"X"', "printer preset")):
    b = dict(blk, **{k: v})
    check("job_intent: wrong %s is reported" % what, len(H.job_intent(raw, None, b, 2)) == 1, H.job_intent(raw, None, b, 2))
check("job_plates: plates come from the job file", H.job_plates(raw) == 2)

# 2. error line injected into the slice log ----------------------------------------------------------------------------------
GOOD = "\n".join([
    "[2026-11-15 17:39:19.1] [0x1] [info] cli mode, Current BambuStudio Version: 02.08.02.61",
    "[2026-11-15 17:39:19.2] [0x1] [warning] Slic3r.cpp:100 can not find system preset file: /x/machine_full/a.json",
    "[2026-11-15 17:39:19.3] [0x1] [warning] MultiNozzleUtils.cpp:68 load_nozzle_infos_with_compatibility: building nozzle list from filament map and volume types",
    "[2026-11-15 17:39:19.4] [0x1] [warning] MultiNozzleUtils.cpp:217 load_nozzle_infos_with_compatibility] building nozzle list from filament map and volume types",
    "[2026-11-15 17:39:19.5] [0x1] [warning] no filament colors found in projects",
])
check("scan_log: the benign warnings of a healthy slice are accepted", H.scan_log(GOOD) == [])
check("scan_log: an injected [error] line is reported", len(H.scan_log(GOOD + "\n[2026-11-15 17:39:20.0] [0x1] [error] Slicing failed")) == 1)
check("scan_log: an unknown [warning] line is reported", len(H.scan_log(GOOD + "\n[2026-11-15 17:39:20.0] [0x1] [warning] something new")) == 1)
check("scan_log: a [fatal] line is reported", len(H.scan_log(GOOD + "\n[x] [fatal] boom")) == 1)
check("scan_log: a fatal string without a level tag is reported", len(H.scan_log(GOOD + "\ncould not found extruder_type")) == 1)

# 3. filaments swapped between parts (layer tools against the job) ------------------------------------------------------------
ranges = [(1, 0.0, 2.4), (2, 2.4, 4.8)]        # filament 1 below, filament 2 above (from the job and the meshes)
good = {0.2: {"tools": {0}}, 1.2: {"tools": {0}}, 2.6: {"tools": {1}}, 4.8: {"tools": {1}}}
swapped = {0.2: {"tools": {1}}, 1.2: {"tools": {1}}, 2.6: {"tools": {0}}, 4.8: {"tools": {0}}}
check("check_layer_tools: tools that follow the job pass", H.check_layer_tools(good, ranges)[0])
ok, d = H.check_layer_tools(swapped, ranges)
check("check_layer_tools: swapped tools fail and name the layer", not ok and "z 0.20" in d)

# 4. wipe tower on the model (real G-code) -----------------------------------------------------------------------------------
layers = {0.2: {"xs": [118.0, 138.0], "ys": [118.0, 138.0], "tools": {0}}}
check("check_tower_clear: tower beside the model passes", H.check_tower_clear((160, 200, 190, 230), layers)[0])
ok, d = H.check_tower_clear((125, 125, 155, 155), layers)
check("check_tower_clear: tower on the model fails", not ok)

# 5. validator rules for the three file breakages ---------------------------------------------------------------------------
work = tempfile.mkdtemp(prefix="mut_")
base = os.path.join(work, "base.3mf")
package.build(jobmod.load_job(os.path.join(HERE, "data", "mutation_base.toml")), base)
check("unbroken example: no validator finding", validate.validate(base, mode="strict", privacy=True).findings == [])
import shutil  # noqa: E402
for name, tamper, rules, _checks in M.MUTATIONS:
    f = os.path.join(work, "m.3mf")
    shutil.copy(base, f)
    tamper(f)
    got = {x.rule for x in validate.validate(f, mode="strict", privacy=True).findings}
    if rules:
        check("%s: validator names %s" % (name, ", ".join(rules)), set(rules) <= got)
    else:
        check("%s: only a slice shows it (validator has no job to compare with)" % name, True)

print("\n%s" % ("all mutation checks hold" if not fails else "FAILED: %d" % len(fails)))
sys.exit(1 if fails else 0)
