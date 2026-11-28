"""Setting recipes (recipe = "name"). Run: python3 -I tests/test_recipes.py"""
import json, os, re, shutil, sys, tempfile, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, HERE)
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf.job import load_job, JobError      # noqa: E402
from bambu3mf import package, validate, recipes  # noqa: E402
import recipe_check                               # noqa: E402

fails = []
measured = [0]


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)) if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


CUBE = os.path.join(ROOT, "examples", "data", "cube20.stl")
HEAD = '''studio = "%s"
output = "out.3mf"
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"
%s
[[filament]]
preset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"
color = "#2060C0"

[[object]]
name = "Block"
[[object.part]]
mesh = "%s"
filament = 1
'''
VERSIONS = ("2.8.2", "2.9.1", "2.9.2")
tmp = tempfile.mkdtemp()


def job_text(ver, top):
    # top-level keys must come before the first table, so the caller passes them as one block
    return HEAD % (ver, top, CUBE)


def build(text, name="t"):
    p = os.path.join(tmp, name + ".toml")
    with open(p, "w") as f:
        f.write(text)
    return package.build(load_job(p), os.path.join(tmp, name + ".3mf"))


def refusal(text, name="bad"):
    p = os.path.join(tmp, name + ".toml")
    with open(p, "w") as f:
        f.write(text)
    try:
        load_job(p)
    except JobError as e:
        return " | ".join(e.problems)
    return None


def cfg_of(s):
    with zipfile.ZipFile(s.path) as z:
        return json.loads(z.read("Metadata/project_settings.config").decode())


def has_key_in_diff(cfg, key):
    """different_settings_to_system holds one ';'-joined list per slot: the process slot is the one that lists process keys."""
    return any(key in d.split(";") for d in cfg["different_settings_to_system"])


try:
    # 1. every recipe, on every version: keys and values in project_settings.config, changed ones in different_settings_to_system
    for name, rec in sorted(recipes.RECIPES.items()):
        for ver in VERSIONS:
            top = 'recipe = "%s"\n' % name
            err = refusal(job_text(ver, top), "r_%s_%s" % (name, ver.replace(".", "")))
            missing = [k for k in rec["settings"] if k not in {o["key"] for o in json.load(open(os.path.join(ROOT, "studio", {
                "2.8.2": "2.8.2.61", "2.9.1": "2.9.1.70", "2.9.2": "2.9.2.62"}[ver], "options.json")))["options"]}]
            if missing:
                check("%s on %s is refused (key missing: %s)" % (name, ver, ", ".join(missing)),
                      err is not None and all(m in err for m in missing) and "needs Studio" in err, err)
                continue
            s = build(job_text(ver, top), "b_%s_%s" % (name, ver.replace(".", "")))
            cfg = cfg_of(s)
            for k, v in rec["settings"].items():
                measured[0] += 1
                got = cfg.get(k)
                got = got[0] if isinstance(got, list) else got
                want = ("1" if v else "0") if isinstance(v, bool) else str(v)
                check("%s on %s: %s = %s in project_settings.config" % (name, ver, k, v), got == want or recipe_check._norm(got) == recipe_check._norm(v), got)
            # a value that equals the system preset's value is not a change, so Studio does not need it listed
            from bambu3mf.profiles import ProfileSet
            sysc = ProfileSet({"2.8.2": "2.8.2.61", "2.9.1": "2.9.1.70", "2.9.2": "2.9.2.62"}[ver]).resolve("process", "0.20mm Standard @BBL X1C")[0]
            defaults = {o["key"]: o["default"] for o in json.load(open(os.path.join(ROOT, "studio", {
                "2.8.2": "2.8.2.61", "2.9.1": "2.9.1.70", "2.9.2": "2.9.2.62"}[ver], "options.json")))["options"]}
            for k, v in rec["settings"].items():
                base = sysc[k] if k in sysc else defaults[k]          # a key the preset lacks has Studio's default
                differs = recipe_check._norm(base) != recipe_check._norm(v)
                measured[0] += 1
                check("%s on %s: %s is in different_settings_to_system exactly when it differs from the preset" % (name, ver, k),
                      has_key_in_diff(cfg, k) == differs, (differs, cfg["different_settings_to_system"]))
            rep = validate.validate(s.path, mode="strict")
            check("%s on %s: validates strict, 0 findings" % (name, ver), not rep.findings, [str(f) for f in rep.findings])

    # 2. the job wins: [settings] over a recipe key, with an alias and a real name
    s = build(job_text("2.9.2", 'recipe = "strong"\n[settings]\nwalls = 3\nsparse_infill_pattern = "cubic"\n'), "win")
    c = cfg_of(s)
    check("job key (alias walls) wins over the recipe", c["wall_loops"] == "3", c["wall_loops"])
    check("job key (real name) wins over the recipe", c["sparse_infill_pattern"] == "cubic", c["sparse_infill_pattern"])
    check("recipe keys the job did not set still apply", c["sparse_infill_density"] == "30%" and c["bottom_shell_layers"] == "5",
          (c["sparse_infill_density"], c["bottom_shell_layers"]))
    s = build(job_text("2.9.2", 'recipe = "visible-face"\n[settings]\nwall_sequence = "outer wall/inner wall"\n'), "win2")
    check("job overrides one visible-face key", cfg_of(s)["wall_sequence"] == "outer wall/inner wall" and cfg_of(s)["wall_generator"] == "arachne")
    # the recipe key that a job overrides is not refused on a version that lacks it
    err = refusal(job_text("2.8.2", 'recipe = "visible-face"\n[settings]\nsub_top_surface_pattern = "monotonic"\n'))
    check("a key the job sets itself is the job's problem, not the recipe's (2.8.2 still refuses the job key)", err is not None and "sub_top_surface_pattern" in err, err)

    # 3. refusals
    err = refusal(job_text("2.8.2", 'recipe = "visible-face"\n'))
    check("visible-face on 2.8.2 names the key and the version", err and "sub_top_surface_pattern" in err and "Studio 2.9.1 or newer" in err and "2.8.2" in err, err)
    err = refusal(job_text("2.9.2", 'recipe = "no-such-recipe"\n'))
    check("unknown recipe lists the recipes", err and "unknown recipe" in err and all(n in err for n in recipes.RECIPES), err)
    err = refusal(job_text("2.9.2", 'recipe = 5\n'))
    check("a recipe that is not a name or a list is refused", err and "recipe" in err, err)
    err = refusal(job_text("2.9.2", 'recipe = ["strong", "fast-draft"]\n'))
    check("two recipes that set one key to two values are refused", err and "different values" in err and "sparse_infill_density" in err, err)
    err = refusal(job_text("2.9.2", 'recipe = ["visible-face", "strong"]\n'))
    check("visible-face (3 walls) with strong (4 walls) is refused and says to set wall_loops", err and "wall_loops" in err and "[settings]" in err, err)
    s = build(job_text("2.9.2", 'recipe = ["visible-face", "strong"]\n[settings]\nwall_loops = 4\n'), "two")
    c = cfg_of(s)
    check("a list of two recipes applies both; the job's wall_loops settles the clash", c["wall_loops"] == "4" and c["wall_generator"] == "arachne"
          and c["sparse_infill_pattern"] == "gyroid", (c["wall_loops"], c["wall_generator"], c.get("sparse_infill_pattern")))
    # fake tables: the refusal for a missing key and for a value the version rejects, shown with made-up recipes
    # (every real key exists in 2.9.1 and 2.9.2, and the real refusal on 2.8.2 is tested above)
    saved = dict(recipes.RECIPES)
    try:
        recipes.RECIPES["fake-missing"] = {"purpose": "x", "settings": {"seam_embedded_headroom": 20}}      # new in 2.9.2
        recipes.RECIPES["fake-value"] = {"purpose": "x", "settings": {"ironing_pattern": "ironingarchimedeanspiral"}}   # value new in 2.9.1
        err = refusal(job_text("2.9.1", 'recipe = "fake-missing"\n'))
        check("fake recipe, key missing in 2.9.1: names key and 2.9.2", err and "seam_embedded_headroom" in err and "Studio 2.9.2 or newer" in err, err)
        err = refusal(job_text("2.8.2", 'recipe = "fake-value"\n'))
        check("fake recipe, value not accepted in 2.8.2: names value and 2.9.1", err and "ironingarchimedeanspiral" in err and "Studio 2.9.1 or newer" in err, err)
        err = refusal(job_text("2.9.2", 'recipe = "fake-missing"\n'))
        check("fake recipe builds where the key exists", err is None, err)
    finally:
        recipes.RECIPES.clear()
        recipes.RECIPES.update(saved)

    # 4. the example, and the check module on it (the G-code half runs in tests/forward_check.py)
    from bambu3mf.job import tomllib                  # tomllib, or the vendored tomli on Python 3.8-3.10
    ex = os.path.join(ROOT, "examples", "recipe_visible_face.toml")
    s = package.build(load_job(ex), os.path.join(tmp, "ex.3mf"))
    raw = tomllib.load(open(ex, "rb"))
    names, want = recipe_check.expected(raw)
    check("check module expects 9 keys, with the job's wall_sequence", len(want) == 9 and want["wall_sequence"] == "outer wall/inner wall", want)
    res = recipe_check.checks(raw, s.path, [])
    check("check module: 3MF half passes on the example", res and res[0][1], res)
    check("check module: no G-code is a failure, not a pass", len(res) == 2 and not res[1][1] and "NOT CHECKED" in res[1][2], res)
    # the check module catches a wrong value
    z_in = zipfile.ZipFile(s.path)
    bad = os.path.join(tmp, "bad.3mf")
    with zipfile.ZipFile(bad, "w") as zo:
        for n in z_in.namelist():
            data = z_in.read(n)
            if n == "Metadata/project_settings.config":
                data = data.replace(b'"ironing_type": "top"', b'"ironing_type": "no ironing"')
            zo.writestr(n, data)
    res = recipe_check.checks(raw, bad, [])
    check("check module catches a changed value", res and not res[0][1] and "ironing_type" in res[0][2], res)
    # the docs list every recipe with its exact keys and values
    doc = open(os.path.join(ROOT, "references", "job-format.md")).read()
    for name, rec in recipes.RECIPES.items():
        check("job-format.md has a section for %s" % name, ("`%s`" % name) in doc)
        for k, v in rec["settings"].items():
            vs = ("true" if v else "false") if isinstance(v, bool) else (str(v) if isinstance(v, (int, float)) else '"%s"' % v)
            check("job-format.md lists %s = %s for %s" % (k, vs, name), re.search(r"`%s`\s*\|\s*`%s`" % (re.escape(k), re.escape(vs)), doc) is not None)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

check("measured something (not 0)", measured[0] > 0, measured[0])
print("\n%d value checks measured, %d failure(s)" % (measured[0], len(fails)))
sys.exit(1 if fails else 0)
