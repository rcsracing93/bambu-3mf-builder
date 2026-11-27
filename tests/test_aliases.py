#!/usr/bin/env python3
"""Step 4. The wider alias table (generated from Studio's option labels by tests/gen_aliases.py).
Every alias maps to a real key of its version(s); none equals a real key; the hand table still works; a generated alias works in a
job; an alias with its real key is an error; an alias of one Studio version only is refused in the other; the docs are current.
Run: python3 -I tests/test_aliases.py"""
import json, os, sys, tempfile
H = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(H)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, H)
from bambu3mf.job import load_job, JobError, PRINT_ALIASES, FILAMENT_ALIASES, PLATE_BED_ALIASES, generated_aliases  # noqa: E402
from bambu3mf.profiles import ProfileSet, STUDIO_VERSIONS, min_studio  # noqa: E402
import gen_aliases  # noqa: E402

fails = []
def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)[:300]) if (extra and not cond) else ""))
    if not cond: fails.append(name)

V282, V291 = "2.8.2.61", "2.9.1.70"
PS = {v: ProfileSet(v) for v in STUDIO_VERSIONS}


def short(v):
    return ".".join(v.split(".")[:3])
DATA = json.load(open(gen_aliases.DATA, encoding="utf-8"))
HAND = {"print": set(PRINT_ALIASES), "filament": set(FILAMENT_ALIASES) | set(PLATE_BED_ALIASES)}

# ---- the data file ----------------------------------------------------------------------------------------------------
n = 0
bad_real, bad_key, bad_kind, bad_hand, bad_form = [], [], [], [], []
for kind, table in DATA["aliases"].items():
    for alias, ents in table.items():
        if alias != alias.lower() or not alias.replace("_", "").isalnum() or alias.isdigit():
            bad_form.append(alias)
        seen = []
        for key, vers in ents:
            for v in vers:
                n += 1
                if v in seen: bad_key.append((alias, v, "two keys for one version"))
                seen.append(v)
                o = PS[v].options.get(key)
                if o is None: bad_key.append((alias, v, key)); continue
                if o["preset"] != kind: bad_kind.append((alias, v, key))
                if alias in PS[v].options: bad_real.append((alias, v))
        if alias in HAND[kind]: bad_hand.append(alias)
check("data file has aliases (%d alias/version pairs)" % n, n > 150)
check("every alias maps to a real key of the right kind in each version it names", not bad_key and not bad_kind, (bad_key, bad_kind))
check("no alias equals a real key name (either kind, its own versions)", not bad_real, bad_real)
check("no generated alias repeats a hand-table name", not bad_hand, bad_hand)
check("aliases are lowercase names, never digits only", not bad_form, bad_form)
check("both versions have generated aliases", all(generated_aliases(k, v) for k in ("print", "filament") for v in (V282, V291)))
check("dropped list present with reasons", DATA["dropped"] and all(d["reason"] and d["keys"] for d in DATA["dropped"]))
check("conflicting aliases were dropped, not guessed", any(d["reason"] == "two keys" for d in DATA["dropped"]))
# a dropped alias must not apply in the version it was dropped for
leak = [(d["alias"], v) for d in DATA["dropped"] for v in d["versions"] if d["alias"] in generated_aliases(d["kind"], v)]
check("dropped aliases do not apply in the versions they were dropped for", not leak, leak)
# every hand alias target is a real key of its kind, both versions
hand_bad = [(a, k, v) for a, k in PRINT_ALIASES.items() for v in PS if PS[v].options.get(k, {}).get("preset") != "print"
            # a key that a newer supported Studio adds is not expected in the older versions
            and not (min_studio(k) and tuple(map(int, short(v).split("."))) < tuple(map(int, min_studio(k).split("."))))] + \
           [(a, k, v) for a, k in FILAMENT_ALIASES.items() for v in PS if PS[v].options.get(k, {}).get("preset") != "filament"]
check("hand table targets are real keys in both versions", not hand_bad, hand_bad)

# ---- jobs --------------------------------------------------------------------------------------------------------------
def job_text(studio, process="", filament="", plate=""):
    return ('studio = "%s"\nprinter = "Bambu Lab P1S 0.4 nozzle"\nprocess = "0.20mm Standard @BBL X1C"\n%s\n%s\n'
            '[[filament]]\npreset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"\ncolor = "#111111"\n[filament.settings]\n%s\n'
            '[[object]]\nname = "Cube"\n[[object.part]]\nmesh = "%s"\nfilament = 1\n') % (
        studio, plate, ("[settings]\n" + process) if process else "", filament, os.path.join(ROOT, "examples", "data", "slab1.stl").replace("\\", "/"))

TMP = tempfile.mkdtemp()
def load(text):
    p = os.path.join(TMP, "j.toml")
    open(p, "w", encoding="utf-8").write(text)
    try:
        return load_job(p), []
    except JobError as e:
        return None, e.problems

for v in STUDIO_VERSIONS:
    sv = short(v)
    j, err = load(job_text(sv, process="walls = 3\ninfill = \"20%\"\ninfill_pattern = \"gyroid\"\ntop_layers = 5\nsupports = true\n",
                           filament="nozzle_temp = 225\nflow = 0.97\nbed_temp = 60\n", plate='plate_type = "Textured PEI Plate"'))
    s = j.settings if j else {}
    fs = j.filaments[0]["settings"] if j else {}
    check("%s: hand table still works" % v, j and s.get("wall_loops") == "3" and s.get("sparse_infill_density") == "20%" and
          s.get("sparse_infill_pattern") == "gyroid" and s.get("top_shell_layers") == "5" and s.get("enable_support") == "1" and
          fs.get("nozzle_temperature") == "225" and fs.get("filament_flow_ratio") == "0.97" and fs.get("textured_plate_temp") == "60", err or (s, fs))
    j, err = load(job_text(sv,process="arc_fitting = true\nelephant_foot_compensation = 0.2\nbridge_direction = 45\n",
                           filament="max_volumetric_speed = 12\ndensity = 1.27\n"))
    s = j.settings if j else {}
    fs = j.filaments[0]["settings"] if j else {}
    check("%s: generated aliases work (print and filament)" % v, j and s.get("enable_arc_fitting") == "1" and s.get("elefant_foot_compensation") == "0.2"
          and s.get("bridge_angle") == "45" and fs.get("filament_max_volumetric_speed") == "12" and fs.get("filament_density") == "1.27", err or (s, fs))
    j, err = load(job_text(sv,process="arc_fitting = true\nenable_arc_fitting = false\n"))
    check("%s: alias with its real key together is an error" % v, j is None and any("another spelling of enable_arc_fitting" in e for e in err), err)
    j, err = load(job_text(sv,filament="max_volumetric_speed = 12\nfilament_max_volumetric_speed = 10\n"))
    check("%s: filament alias with its real key together is an error" % v, j is None and any("another spelling" in e for e in err), err)
    j, err = load(job_text(sv,process="flow_ratio = 0.9\n"))
    check("%s: a filament alias in [settings] is refused" % v, j is None, err)

# an alias of one version only: works there, unknown in the other
one = [(k, a, ents[0][0], ents[0][1][0]) for k, t in DATA["aliases"].items() for a, ents in t.items() if len(ents) == 1 and len(ents[0][1]) == 1]
check("some aliases are valid in one version only", len(one) > 0)
for kind, a, key, ver in [x for x in one if x[0] == "print"][:1]:
    other = [v for v in STUDIO_VERSIONS if v != ver][0]
    # use a value the option accepts: take its default
    dflt = PS[ver].options[key]["default"]
    lit = ("true" if dflt in ("1", 1, True) else "false") if PS[ver].options[key]["type"].startswith("coBool") else json.dumps(dflt)
    jv, ev = load(job_text(short(ver), process="%s = %s\n" % (a, lit)))
    check("version-only alias %s works in %s" % (a, ver), jv is not None and key in jv.settings, ev)
    jo, eo = load(job_text(short(other), process="%s = %s\n" % (a, lit)))
    check("version-only alias %s is unknown in %s" % (a, other), jo is None and any("has no setting" in e for e in eo), eo)

# ---- docs --------------------------------------------------------------------------------------------------------------
stale = gen_aliases.stale_docs()
check("docs are current (references/aliases.md, references/job-format.md): rebuild with python3 tests/gen_aliases.py --docs", not stale, stale)
jf = open(gen_aliases.JOB_FORMAT, encoding="utf-8").read()
check("job-format.md lists every hand alias", all("`%s`" % a in jf for a in list(PRINT_ALIASES) + list(FILAMENT_ALIASES) + list(PLATE_BED_ALIASES)))

sys.exit(1 if fails else 0)
