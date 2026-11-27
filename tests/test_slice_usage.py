#!/usr/bin/env python3
"""--slice-check usage: print time, grams and metres per filament slot, read from a fake Studio result.json and G-code headers.
No Studio needed. Run: python3 -I tests/test_slice_usage.py"""
import json, os, shutil, sys, tempfile
H = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(H)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf.job import load_job
from bambu3mf import package, summary, studio_detect

fails = []
n = 0
def check(name, cond, extra=""):
    global n
    n += 1
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)[:300]) if (extra and not cond) else ""))
    if not cond: fails.append(name)

RES = {"error_string": "Success.", "return_code": 0, "sliced_plates": [
    {"id": 1, "total_predication": 1073.096923828125, "filaments": [
        {"id": 1, "main_used_g": 0.5, "total_used_g": 1.319079875946045},
        {"id": 2, "main_used_g": 0.5, "total_used_g": 1.400089144706726}]},
    {"id": 2, "total_predication": 3910.4, "filaments": [
        {"id": 3, "main_used_g": 9.0, "total_used_g": 12.345}]}]}
GC1 = ("; HEADER_BLOCK_START\n; model printing time: 11m 38s; total estimated time: 17m 53s\n"
       "; total filament length [mm] : 435.25,461.98,314.38\n; total filament weight [g] : 1.32,1.40,0.95\n"
       "; filament_colour = #111111;#FFFFFF;#C12E1F\n; HEADER_BLOCK_END\nG28\n")
GC2 = ("; HEADER_BLOCK_START\n; model printing time: 1h; total estimated time: 1h 5m 10s\n"
       "; total filament length [mm] : 1000,1000,4141.5\n; total filament weight [g] : 1,1,12.35\n"
       "; filament_colour = #111111;#FFFFFF;#C12E1F\n; HEADER_BLOCK_END\nG28\n")

tmp = tempfile.mkdtemp()
try:
    # fake Studio: copies prepared files into the --outputdir (4th argument)
    prep = os.path.join(tmp, "prep"); os.makedirs(prep)
    json.dump(RES, open(os.path.join(prep, "result.json"), "w"))
    open(os.path.join(prep, "plate_1.gcode"), "w").write(GC1)
    open(os.path.join(prep, "plate_2.gcode"), "w").write(GC2)
    fake = os.path.join(tmp, "fakestudio.sh")
    open(fake, "w").write('#!/bin/sh\ncp "%s"/* "$4"/\nexit 0\n' % prep)
    os.chmod(fake, 0o755)
    ok, msg, plates = studio_detect.slice_details(fake, os.path.join(tmp, "x.3mf"), timeout=30)
    check("slice_details still returns 3 values, ok", ok is True and len(plates) == 2, (ok, msg))
    p1, p2 = plates
    check("plate 1 time from total_predication (17m 53s)", p1["time"] == "17m 53s", p1["time"])
    check("plate 2 time over one hour (1h 5m 10s)", p2["time"] == "1h 5m 10s", p2["time"])
    check("plate 1 grams is the sum of total_used_g", abs(p1["grams"] - (1.319079875946045 + 1.400089144706726)) < 1e-9, p1["grams"])
    check("plate 1 has 2 filament slots, 1 and 2", [f["slot"] for f in p1["filaments"]] == [1, 2], p1["filaments"])
    check("plate 2 slot is the Studio id (3)", [f["slot"] for f in p2["filaments"]] == [3], p2["filaments"])
    check("grams per slot", [round(f["grams"], 2) for f in p1["filaments"] + p2["filaments"]] == [1.32, 1.40, 12.35])
    check("metres per slot from the G-code header", [round(f["metres"], 5) for f in p1["filaments"] + p2["filaments"]] == [0.43525, 0.46198, 4.1415], [f["metres"] for f in p1["filaments"] + p2["filaments"]])
    check("colour per slot", [f["colour"] for f in p1["filaments"]] == ["#111111", "#FFFFFF"] and p2["filaments"][0]["colour"] == "#C12E1F")
    check("source is result.json", p1["source"] == "result.json" and p2["source"] == "result.json")
    lines = studio_detect.usage_lines(plates)
    print("\n".join(lines))
    check("console: per-plate line format kept", "  plate 1: print time 17m 53s, filament 2.72 g" in lines, lines)
    check("console: filament line with slot, colour, g and m", "    filament 1 (#111111): 1.32 g, 0.44 m" in lines, lines)
    check("console: total line", "  total: print time 1h 23m 3s, filament 15.06 g, 5.04 m" in lines, lines)
    check("console: 3 filament lines", sum(1 for l in lines if l.startswith("    filament")) == 3, lines)
    job = load_job(os.path.join(ROOT, "examples", "single_p1s.toml"))
    s = package.build(job, os.path.join(tmp, "x.3mf"))
    md = summary.format_summary_md(job, s, (True, "Studio sliced it: 2 plate(s), no error", plates))
    check("summary: plate line kept", "- plate 1: print time 17m 53s, filament 2.72 g" in md)
    check("summary: table header", "| Plate | Print time | Slot | Colour | Grams | Metres |" in md)
    check("summary: row for slot 2", "| 1 | 17m 53s | 2 | #FFFFFF | 1.40 | 0.46 |" in md, md)
    check("summary: row for slot 3", "| 2 | 1h 5m 10s | 3 | #C12E1F | 12.35 | 4.14 |" in md)
    check("summary: total row", "| total | 1h 23m 3s |" in md)
    check("summary: 3 filament rows", sum(1 for l in md.splitlines() if l.startswith("| ") and l[2:3].isdigit()) == 3)

    # result.json without the fields: fall back to the G-code header and say so
    json.dump({"return_code": 0, "error_string": "Success.", "sliced_plates": []}, open(os.path.join(prep, "result.json"), "w"))
    ok, msg, plates = studio_detect.slice_details(fake, os.path.join(tmp, "x.3mf"), timeout=30)
    p1 = plates[0]
    check("fallback: time from G-code header", p1["time"] == "17m 53s" and p1["source"] == "G-code header", p1)
    check("fallback: grams summed from header (3.67)", round(p1["grams"], 2) == 3.67 and len(p1["filaments"]) == 3, p1)
    check("fallback: console says so", any("G-code header" in l for l in studio_detect.usage_lines(plates)))
    check("fallback: summary says so", "G-code header" in summary.format_summary_md(job, s, (ok, msg, plates)))
    # old-style plate dicts (no filaments) still print
    old = [{"plate": 1, "time": "1h 5m 10s", "grams": 12.34}]
    check("old plate dict: one console line", studio_detect.usage_lines(old) == ["  plate 1: print time 1h 5m 10s, filament 12.34 g"])
    check("old plate dict: no table", studio_detect.usage_table_md(old) == [])
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("\nmeasured %d checks" % n)
if n == 0:
    print("NOT CHECKED"); sys.exit(1)
print("\n%d failed" % len(fails) if fails else "all passed")
sys.exit(1 if fails else 0)
