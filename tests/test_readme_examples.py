#!/usr/bin/env python3
"""The "Examples" table at the top of README.md: the line above it word for word, 6 rows, multi-color first, every file it names
(examples/*.toml or tests/*.py) exists, and every named example job builds and validates strict (0 findings).
Row 2 (patch mode) is run here: a built project is patched to PETG-CF, 5 walls and the Engineering Plate; the result validates strict,
holds the three changes, and its geometry entries are byte-identical to the input.
Run: python3 -I tests/test_readme_examples.py"""
import json, os, re, subprocess, sys, tempfile, zipfile
H = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(H, ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import package, validate  # noqa: E402
from bambu3mf.job import load_job  # noqa: E402

PAIN = "No painting, no clicking through settings, no more forgetting to switch away from grid infill."
fails = []
measured = 0


def check(name, cond, extra=""):
    global measured
    measured += 1
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)[:300]) if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


text = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
m = re.search(r"(?ms)^## Examples\n(.*?)^## ", text)
check("README has an Examples section before the next section", m is not None)
sec = m.group(1) if m else ""
check("the line above the table is Rick's line, word for word", PAIN in sec.split("|")[0])
rows = [l for l in sec.splitlines() if l.startswith("| \"")]
check("the table has 6 rows (%d)" % len(rows), len(rows) == 6, len(rows))
check("the first row is multi-color (an inlay on its own AMS slot)", bool(rows) and "inlay" in rows[0] and "AMS" in rows[0], rows[:1])
check("the two lines under the table are there", "Also: pauses at a height" in sec and "Works for the Bambu printers in Studio's profiles" in sec)
files = []
for r in rows:
    named = re.findall(r"`((?:examples|tests)/[^`]+\.(?:toml|py))`", r)
    check("row names an example or a test: %s" % r[:60], bool(named), r)
    files += named
for f in files:
    check("%s exists" % f, os.path.isfile(os.path.join(ROOT, f)))
tmp = tempfile.mkdtemp()
for f in [f for f in files if f.endswith(".toml")]:
    out = os.path.join(tmp, os.path.basename(f)[:-5] + ".3mf")
    try:
        package.build(load_job(os.path.join(ROOT, f)), out)
        r = validate.validate(out, mode="strict")
        check("%s builds and validates strict (0 findings)" % f, r.ok and not r.findings, [(x.rule, x.msg[:80]) for x in r.findings])
    except Exception as e:                                  # noqa: BLE001
        check("%s builds and validates strict (0 findings)" % f, False, e)

# row 2: patch mode on a built project ("Take my bracket.3mf and change it to PETG-CF, 5 walls, Engineering Plate")
src = os.path.join(tmp, "bracket.3mf")
package.build(load_job(os.path.join(ROOT, "examples", "single_p1s.toml")), src)
pj, out = os.path.join(tmp, "patch.toml"), os.path.join(tmp, "bracket-petg-cf.3mf")
with open(pj, "w") as fh:
    fh.write('patch = "%s"\noutput = "%s"\nplate_type = "Engineering Plate"\n[settings]\nwall_loops = 5\n'
             '[[filament]]\nslot = 1\npreset = "Bambu PETG-CF @BBL X1C 0.4 nozzle"\n' % (src, out))
r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "scripts", "build_bambu_3mf.py"), pj, "--no-summary"], capture_output=True, text=True)
check("row 2: the patch runs (exit 0)", r.returncode == 0 and os.path.exists(out), (r.stdout + r.stderr)[-400:])
if os.path.exists(out):
    v = validate.validate(out, mode="strict")
    check("row 2: the patched file validates strict (0 findings)", v.ok and not v.findings, [(x.rule, x.msg[:80]) for x in v.findings])
    cfg = json.loads(zipfile.ZipFile(out).read("Metadata/project_settings.config"))
    fs = cfg.get("filament_settings_id")
    check("row 2: PETG-CF, 5 walls, Engineering Plate in the file", cfg.get("wall_loops") == "5" and cfg.get("curr_bed_type") == "Engineering Plate"
          and (fs[0] if isinstance(fs, list) else fs) == "Bambu PETG-CF @BBL X1C 0.4 nozzle", (cfg.get("wall_loops"), cfg.get("curr_bed_type"), fs))
    zs, zo = zipfile.ZipFile(src), zipfile.ZipFile(out)
    geo = [n for n in zs.namelist() if n.startswith("3D/")]
    same = [n for n in geo if n in zo.namelist() and zs.read(n) == zo.read(n)]
    check("row 2: geometry entries byte-identical to the input (%d of %d)" % (len(same), len(geo)), geo and len(same) == len(geo))

print("\n%d checks measured" % measured)
if measured == 0:
    print("NOT CHECKED")
print("%d failed" % len(fails) if fails else "all passed")
sys.exit(1 if fails or measured == 0 else 0)
