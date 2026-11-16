#!/usr/bin/env python3
"""Step 7: user presets as a base. A job names a preset that is not a system preset; the builder reads it from the Studio data folder at
run time. Everything here uses a FAKE data folder made in this test from system presets with changed values and invented names.
No real user preset, value or name is in the repo (the last checks scan the whole tree for that).
Run: python3 -I tests/test_user_presets.py"""
import glob, json, os, shutil, subprocess, sys, tempfile, zipfile
H = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(H)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
os.environ.pop("BAMBU_STUDIO_USER_DIR", None)
from bambu3mf import package, summary, validate, userpresets  # noqa: E402
from bambu3mf.job import load_job, JobError  # noqa: E402
from bambu3mf.profiles import ProfileSet  # noqa: E402

fails = []
def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)) if (extra and not cond) else ""))
    if not cond: fails.append(name)

tmp = tempfile.mkdtemp(prefix="up_test_")
DATA = os.path.join(tmp, "BambuStudio")                 # a fake Studio data folder
ID = os.path.join(DATA, "user", "1234567890")
for sub in ("filament", "process", "machine"):
    os.makedirs(os.path.join(ID, sub))

FIL_PARENT = "Bambu PLA Basic @BBL P1S 0.4 nozzle"
PRO_PARENT = "0.20mm Standard @BBL X1C"
PRINTER = "Bambu Lab P1S 0.4 nozzle"
U_FIL, U_PRO, U_ORPHAN, U_BADPARENT = "Test PLA Tuned", "Test Process Tuned", "Test Scratch PLA", "Test Lost Parent PLA"


def put(kind, name, d):
    with open(os.path.join(ID, kind, name + ".json"), "w") as f:
        json.dump(d, f, indent=4)


def make_folder(ver):
    ps = ProfileSet(ver)
    put("filament", U_FIL, {"type": "filament", "name": U_FIL, "from": "User", "inherits": FIL_PARENT, "version": "02.00.00.00",
                            "setting_id": "", "filament_flow_ratio": ["0.91", "0.915"], "nozzle_temperature": ["212", "214"],
                            "wall_loops": "9"})          # a process key in a filament file: Studio drops it
    put("process", U_PRO, {"type": "process", "name": U_PRO, "from": "User", "inherits": PRO_PARENT, "version": "02.00.00.00",
                           "wall_loops": "5", "sparse_infill_density": "12%"})
    put("filament", U_ORPHAN, {"type": "filament", "name": U_ORPHAN, "from": "User", "inherits": "", "version": "02.00.00.00",
                               "filament_flow_ratio": ["0.9"]})
    put("filament", "Test Old Parent PLA", {"type": "filament", "name": "Test Old Parent PLA", "from": "User",
                                            "inherits": "Bambu PLA Basic @BBL X1C", "version": "2.3.0.5", "filament_flow_ratio": ["0.9"]})
    put("filament", U_BADPARENT, {"type": "filament", "name": U_BADPARENT, "from": "User", "inherits": "No Such Parent", "version": "02.00.00.00"})
    return ps


def write_job(path, ver, fil1, proc, extra=""):
    with open(path, "w") as f:
        f.write('studio = "%s"\noutput = "%s"\nprinter = "%s"\nprocess = "%s"\n[settings]\nsparse_infill_pattern = "grid"\n%s\n'
                '[[filament]]\npreset = "%s"\ncolor = "#111111"\n[[filament]]\npreset = "%s"\ncolor = "#FFFFFF"\n'
                '[[object]]\nname = "Cube"\n[[object.part]]\nname = "A"\nmesh = "%s"\nfilament = 1\n'
                '[[object.part]]\nname = "B"\nmesh = "%s"\nfilament = 2\n' % (
                    ver, os.path.splitext(os.path.basename(path))[0] + ".3mf", PRINTER, proc, extra, fil1, FIL_PARENT,
                    os.path.join(ROOT, "examples", "data", "slab1.stl"), os.path.join(ROOT, "examples", "data", "slab2.stl")))
    return path


for ver_short, ver in (("2.8.2", "2.8.2.61"), ("2.9.1", "2.9.1.70")):
    pre = "[%s] " % ver_short
    make_folder(ver)
    job_path = write_job(os.path.join(tmp, "job%s.toml" % ver_short.replace(".", "")), ver_short, U_FIL, U_PRO)

    # ---- build from the fake folder (explicit folder) ----------------------------------------------------------------------------
    job = load_job(job_path, user_presets=DATA)
    s = package.build(job)
    cfg = json.loads(zipfile.ZipFile(s.path).read("Metadata/project_settings.config"))
    check(pre + "build names the user presets in the project", cfg["print_settings_id"] == U_PRO and cfg["filament_settings_id"][0] == U_FIL, cfg["filament_settings_id"])
    check(pre + "inherits_group: parents of the user slots, system slot has none", cfg["inherits_group"] == [PRO_PARENT, FIL_PARENT, "", ""], cfg["inherits_group"])
    dl = cfg["different_settings_to_system"]
    check(pre + "different_settings_to_system: exactly the changed keys, process slot", dl[0].split(";") == ["sparse_infill_density", "wall_loops"], dl[0])
    check(pre + "different_settings_to_system: exactly the changed keys, user filament slot", dl[1].split(";") == ["filament_flow_ratio", "nozzle_temperature"], dl[1])
    check(pre + "different_settings_to_system: system slot and printer slot empty", dl[2] == "" and dl[3] == "", dl)
    check(pre + "the changed values are in the file", cfg["filament_flow_ratio"][:2] == ["0.91", "0.915"] and cfg["wall_loops"] == "5"
          and cfg["sparse_infill_density"] == "12%" and cfg["nozzle_temperature"][:2] == ["212", "214"], (cfg["filament_flow_ratio"][:2], cfg["wall_loops"]))
    check(pre + "slot 2 (system preset) keeps the system value", cfg["filament_flow_ratio"][2:4] == ["0.98", "0.985"], cfg["filament_flow_ratio"])
    check(pre + "the process key in the filament file was dropped (wall_loops stays the process preset's)", cfg["wall_loops"] == "5")

    for mode in ("strict", "default"):
        r = validate.validate(s.path, mode=mode)
        check(pre + "validator %s: 0 errors" % mode, not r.errors, [str(f)[:160] for f in r.errors])
        if mode == "strict":
            check(pre + "validator strict: no R3 or S1 finding", not [f for f in r.findings if f.rule in ("R3", "S1")], [str(f)[:120] for f in r.findings if f.rule in ("R3", "S1")])

    # ---- warnings in the summary, the summary file and the console ----------------------------------------------------------
    txt = summary.format_summary(job, s)
    check(pre + "console summary warns about the user preset (project preset on another computer)",
          "warning:" in txt and "user preset" in txt and "project preset" in txt and U_FIL in txt, txt[-600:])
    md = summary.format_summary_md(job, s)
    check(pre + "summary file warns too", "## Warnings" in md and "user preset" in md.split("## Warnings")[1], md[-500:])
    check(pre + "two warnings: process and filament", sum(1 for w in s.warnings if "user preset of this computer" in w) == 2, s.warnings)

    # ---- the unchanged-value case: a user preset equal to its parent still names the parent --------------------------------------
    put("filament", "Test Same As Parent", {"type": "filament", "name": "Test Same As Parent", "from": "User", "inherits": FIL_PARENT, "version": "02.00.00.00"})
    j2 = load_job(write_job(os.path.join(tmp, "same%s.toml" % ver_short.replace(".", "")), ver_short, "Test Same As Parent", PRO_PARENT), user_presets=DATA)
    c2 = json.loads(zipfile.ZipFile(package.build(j2).path).read("Metadata/project_settings.config"))
    check(pre + "user preset equal to its parent: parent named, no changed keys for the slot",
          c2["inherits_group"][1] == FIL_PARENT and c2.get("different_settings_to_system", ["", "", "", ""])[1] == "", (c2.get("inherits_group"), c2.get("different_settings_to_system")))
    check(pre + "   and the validator still gives 0 errors", not validate.validate(package.build(j2).path, mode="strict").errors)

    # ---- a job setting on top of a user preset is listed with the user's own keys ---------------------------------------------
    j3 = load_job(write_job(os.path.join(tmp, "over%s.toml" % ver_short.replace(".", "")), ver_short, U_FIL, PRO_PARENT,
                            extra=""), user_presets=DATA)
    j3.filaments[0]["settings"]["filament_max_volumetric_speed"] = "11"
    c3 = json.loads(zipfile.ZipFile(package.build(j3).path).read("Metadata/project_settings.config"))
    check(pre + "a job override on a user filament joins the changed keys",
          c3["different_settings_to_system"][1].split(";") == ["filament_flow_ratio", "filament_max_volumetric_speed", "nozzle_temperature"], c3["different_settings_to_system"][1])

    # ---- refusals and errors -------------------------------------------------------------------------------------------------
    def problems(fil, user=DATA, proc=PRO_PARENT):
        p = write_job(os.path.join(tmp, "bad.toml"), ver_short, fil, proc)
        try:
            load_job(p, user_presets=user)
        except JobError as e:
            return "\n".join(e.problems)
        return ""
    pr = problems(U_ORPHAN)
    check(pre + "empty inherits is refused, with the reason", "no parent" in pr and "made from scratch" in pr, pr[:300])
    pr = problems("Test Old Parent PLA")
    check(pre + "a user preset whose parent is not made for the printer is refused like in Studio, and says why and how to fix it",
          "is not made for" in pr and "Its parent 'Bambu PLA Basic @BBL X1C'" in pr and "Studio also hides it" in pr
          and "pick 'Bambu PLA Basic @BBL P1S 0.4 nozzle'" in pr, pr[:600])
    pr = problems(U_BADPARENT)
    check(pre + "a parent that is not a system preset is refused", "is not a Bambu system preset" in pr, pr[:300])
    pr = problems("Test No File At All")
    check(pre + "a name with no file: clear error, names the folder", "no file filament/Test No File At All.json" in pr, pr[:300])
    pr = problems(U_FIL, user=os.path.join(tmp, "does-not-exist"))
    check(pre + "a missing user folder: clear error says where it looked and how to fix it", "was not found at" in pr and "--user-presets" in pr, pr[:400])
    pr = problems("../" + U_FIL)
    check(pre + "a name with a path part is refused (only the named file is ever read)", "not a valid preset name" in pr, pr[:300])
    pr = problems(FIL_PARENT.replace("Basic", "Basik"))
    check(pre + "a typo in a system name still gets the closest-name hint", "Closest" in pr, pr[:300])

    # ---- a job with system presets only never touches the user folder -----------------------------------------------------------
    sysjob = write_job(os.path.join(tmp, "sys.toml"), ver_short, FIL_PARENT, PRO_PARENT)
    try:
        load_job(sysjob, user_presets=os.path.join(tmp, "does-not-exist"))
        ok = True
    except JobError as e:
        ok = False
    check(pre + "system-only job: the user folder is not read (a missing one is no error)", ok)

# ---- the command line: option, variable, default folder -----------------------------------------------------------------------------
CLI = os.path.join(ROOT, "scripts", "build_bambu_3mf.py")
job_path = os.path.join(tmp, "job291.toml")


def run(args, env_extra=None, drop=()):
    env = dict(os.environ)
    for k in drop:
        env.pop(k, None)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-I", CLI] + args, capture_output=True, text=True, env=env)


r = run([job_path, "--user-presets", DATA, "--out", os.path.join(tmp, "cli1.3mf")])
check("CLI --user-presets: builds, console warns", r.returncode == 0 and "warning:" in r.stdout and "user preset" in r.stdout, r.stdout[-300:] + r.stderr[-300:])
check("CLI: summary file written with the warning", "user preset" in open(os.path.join(tmp, "cli1.summary.md")).read())
r = run([job_path, "--out", os.path.join(tmp, "cli2.3mf")], {"BAMBU_STUDIO_USER_DIR": os.path.join(DATA, "user")})
check("CLI BAMBU_STUDIO_USER_DIR (the user folder): builds", r.returncode == 0, r.stdout[-300:] + r.stderr[-300:])
r = run([job_path, "--out", os.path.join(tmp, "cli3.3mf")], {"BAMBU_STUDIO_USER_DIR": ID})
check("CLI BAMBU_STUDIO_USER_DIR (one id folder): builds", r.returncode == 0, r.stdout[-300:] + r.stderr[-300:])
r = run([job_path, "--user-presets", os.path.join(tmp, "nope"), "--out", os.path.join(tmp, "cli4.3mf")], {"BAMBU_STUDIO_USER_DIR": DATA})
check("CLI: the option wins over the variable", r.returncode == 1 and "was not found at" in r.stderr and "--user-presets" in r.stderr, r.stderr[-300:])
r = run([job_path, "--user-presets"])
check("CLI: --user-presets without a value says so", r.returncode == 2 and "--user-presets needs" in r.stderr, r.stderr)
if sys.platform.startswith("linux"):
    r = run([job_path, "--out", os.path.join(tmp, "cli5.3mf")], {"XDG_CONFIG_HOME": tmp}, drop=("BAMBU_STUDIO_USER_DIR",))
    check("CLI default: the Studio data folder of this computer", r.returncode == 0 and "user preset" in r.stdout, r.stdout[-300:] + r.stderr[-300:])
    empty = tempfile.mkdtemp(prefix="up_empty_")
    r = run([job_path, "--out", os.path.join(tmp, "cli6.3mf")], {"XDG_CONFIG_HOME": empty}, drop=("BAMBU_STUDIO_USER_DIR",))
    check("CLI default, no Studio data folder: clear error", r.returncode == 1 and "was not found at" in r.stderr, r.stderr[-300:])
    shutil.rmtree(empty)

# ---- D7: free text in a user preset is scanned for local paths and e-mail addresses (build time) -----------------------------------------
U_NOTE, U_PNOTE = "Test PLA Notes", "Test Process Notes"
put("filament", U_NOTE, {"type": "filament", "name": U_NOTE, "from": "User", "inherits": FIL_PARENT, "version": "02.00.00.00", "setting_id": "",
                         "filament_notes": "tuned in C:\\Users\\fake\\Desktop\\spool.txt, ask fake.person@example.com"})
put("process", U_PNOTE, {"type": "process", "name": U_PNOTE, "from": "User", "inherits": PRO_PARENT, "version": "02.00.00.00", "setting_id": "",
                         "process_notes": "profile from /home/fake/profiles/a.json"})
jp = write_job(os.path.join(tmp, "jobnotes.toml"), "2.8.2", U_NOTE, U_PNOTE)
sN = package.build(load_job(jp, user_presets=DATA))
wN = [w for w in sN.warnings if "holds what looks like a local path or an e-mail" in w]
check("D7: a path or an e-mail address in user-preset notes is a warning that names the key (filament_notes, process_notes)",
      any("filament_notes" in w for w in wN) and any("process_notes" in w for w in wN), sN.warnings)
check("D7: ... and the warning never prints the value", not any("fake" in w or "Users" in w for w in wN), wN)
put("filament", U_NOTE, {"type": "filament", "name": U_NOTE, "from": "User", "inherits": FIL_PARENT, "version": "02.00.00.00", "setting_id": "",
                         "filament_notes": "dried 4 h, works at 215 C"})
sN = package.build(load_job(jp, user_presets=DATA))
check("D7: plain notes give no such warning", not [w for w in sN.warnings if "filament_notes" in w], sN.warnings)

# ---- the validator never reads the user folder -----------------------------------------------------------------------------------
check("ProfileSet: user presets are off unless the builder turns them on", ProfileSet("2.9.1.70").user_enabled is False)
try:
    ProfileSet("2.9.1.70").resolve("filament", U_FIL)
    check("validator side: a user preset name does not resolve", False)
except Exception:
    check("validator side: a user preset name does not resolve", True)

# ---- privacy gate: no real user preset, value or name in any repo file -------------------------------------------------------------
# The pieces are joined here so this file does not hold the strings it forbids.
FORBIDDEN = ["Bambu PLA Basic " + "Flow Rate " + "Calibrated", "Flow Rate " + "Calibrated", "0.88" + "445"]
hits = []
for dp, dn, fn in os.walk(ROOT):
    dn[:] = [d for d in dn if d not in (".git", "__pycache__")]
    for f in fn:
        p = os.path.join(dp, f)
        try:
            data = open(p, "rb").read()
        except OSError:
            continue
        for bad in FORBIDDEN:
            if bad.encode() in data:
                hits.append((os.path.relpath(p, ROOT), bad[:12]))
check("privacy gate: the real preset name and value are in no repo file", not hits, hits)
fake_hits = []
for dp, dn, fn in os.walk(ROOT):
    dn[:] = [d for d in dn if d not in (".git", "__pycache__")]
    for f in fn:
        p = os.path.join(dp, f)
        rel = os.path.relpath(p, ROOT)
        if rel in (os.path.join("tests", "test_user_presets.py"), os.path.join("tests", "studio_user_preset.py")):
            continue
        if f.endswith((".3mf", ".zip", ".stl", ".png", ".pyc")):
            continue
        try:
            data = open(p, "rb").read()
        except OSError:
            continue
        if any(n.encode() in data for n in (U_FIL, U_PRO, U_ORPHAN)):
            fake_hits.append(rel)
check("privacy gate: the test's preset names are in no other repo file (no fixture, example or doc carries a user preset)", not fake_hits, fake_hits)
leftover = [p for p in glob.glob(os.path.join(ROOT, "**", "*.json"), recursive=True) if "Test PLA Tuned" in p]
check("privacy gate: no user preset file was written inside the repo", not leftover, leftover)

shutil.rmtree(tmp, ignore_errors=True)
print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
