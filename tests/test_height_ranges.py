"""Height ranges ([[object.height_range]], Metadata/layer_config_ranges.xml). Run: python3 -I tests/test_height_ranges.py"""
import io, os, shutil, sys, tempfile, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf.job import load_job, JobError      # noqa: E402
from bambu3mf import package, validate, ranges   # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)) if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


CUBE = os.path.join(ROOT, "examples", "data", "cube20.stl")
HEAD = '''studio = "2.9.1"
output = "out.3mf"
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"
%s
[[filament]]
preset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"
color = "#2060C0"
%s
'''
FIL2 = '''
[[filament]]
preset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"
color = "#FFFFFF"
'''


def obj(name, ranges_toml, filament=1, plate=1):
    return '''
[[object]]
name = "%s"
plate = %d
[[object.part]]
mesh = "%s"
filament = %d
%s''' % (name, plate, CUBE, filament, ranges_toml)


def rng(lo, hi, h, settings=""):
    return "[[object.height_range]]\nmin_z = %s\nmax_z = %s\nlayer_height = %s\n%s\n" % (lo, hi, h, settings)


tmp = tempfile.mkdtemp()


def build(text, name="t"):
    p = os.path.join(tmp, name + ".toml")
    open(p, "w").write(text)
    job = load_job(p)
    return package.build(job, os.path.join(tmp, name + ".3mf"))


def fails_with(text, needle, name):
    try:
        build(text, "bad")
    except JobError as e:
        msg = " | ".join(e.problems)
        check(name, needle in msg, msg)
        return
    check(name, False, "built without an error")


try:
    # 1. the example job: file, layout, object id, options
    s = package.build(load_job(os.path.join(ROOT, "examples", "height_ranges.toml")), os.path.join(tmp, "ex.3mf"))
    z = zipfile.ZipFile(s.path)
    check("ranges file written", ranges.FILE in z.namelist())
    got, probs = ranges.parse(z.read(ranges.FILE).decode())
    check("ranges file parses as Studio reads it", not probs and list(got) == [1], (probs, got))
    r = got.get(1, [])
    check("two ranges, layer heights kept", [(lo, hi, o.get("layer_height")) for lo, hi, o in r] == [(0.0, 2.0, "0.12"), (17.0, 20.0, "0.08")], r)
    check("extruder 0 in every range, as the Studio GUI writes it", all(o.get("extruder") == "0" for _a, _b, o in r), r)
    txt = z.read(ranges.FILE).decode()
    check("keys in Studio's order (sorted)", txt.index("extruder") < txt.index("layer_height") < txt.index("wall_loops"), txt)
    check("friendly name walls -> wall_loops in the range", r and r[1][2].get("wall_loops") == "4", r)
    rep = validate.validate(s.path, mode="strict")
    check("example validates clean (strict)", not rep.findings, [str(f) for f in rep.findings])
    check("no ranges file without ranges", ranges.FILE not in zipfile.ZipFile(build(HEAD % ("", obj("A", ""))).path).namelist())

    # 2. the id is the object's place in the model, not the 3MF object id
    s2 = build(HEAD % ("", obj("A", "") + obj("B", rng(0, 5, 0.12)) + obj("C", rng(10, 12, 0.16))), "ids")
    got2, _ = ranges.parse(zipfile.ZipFile(s2.path).read(ranges.FILE).decode())
    check("ids follow model order (B=2, C=3)", sorted(got2) == [2, 3], sorted(got2))
    check("3MF object ids differ from the model order", s2.objects[1].wid != 2)

    # 3. builder checks
    fails_with(HEAD % ("", obj("A", rng(0, 5, 0.12) + rng(4, 8, 0.16))), "overlaps", "overlap refused")
    fails_with(HEAD % ("", obj("A", rng(0, 5, 0.4))), "outside the nozzle's limits", "layer height above the nozzle limit refused")
    fails_with(HEAD % ("", obj("A", rng(0, 5, 0.04))), "outside the nozzle's limits", "layer height below the nozzle limit refused")
    fails_with(HEAD % ("", obj("A", rng(5, 3, 0.12))), "min_z < max_z", "min above max refused")
    fails_with(HEAD % ("", obj("A", rng(25, 30, 0.12))), "above the top of the object", "range above the object refused")
    fails_with(HEAD % ("", obj("A", "[[object.height_range]]\nmin_z = 0\nmax_z = 2\n")), "needs layer_height", "missing layer_height refused")
    fails_with(HEAD % ("", obj("A", rng(0, 2, 0.12, "[object.height_range.settings]\nenable_support = 1\n"))), "cannot be set in a height range",
               "object-only setting in a range refused")
    fails_with(HEAD % ("", obj("A", rng(0, 2, 0.12, "[object.height_range.settings]\nlayer_height = 0.1\n"))), "on the range itself",
               "layer_height inside settings refused")
    fails_with(HEAD % ("", obj("A", rng(0, 2, 0.12, "bogus = 1\n"))), "unknown key", "unknown range key refused")
    fails_with(HEAD % ("[settings]\nenable_support = 1\nsupport_type = \"tree(auto)\"\nsupport_style = \"tree_organic\"\n", obj("A", rng(0, 2, 0.12))),
               "organic", "organic tree supports with ranges refused")
    s3 = build(HEAD % ("", obj("A", rng(15, 25, 0.12))), "top")
    check("range past the top is a warning", any("ends above the top" in w for w in s3.warnings), s3.warnings)

    # 4. prime tower: objects on one plate need the same layering
    def two_col(objs):
        return (HEAD % ("", objs)).replace('color = "#2060C0"\n', 'color = "#2060C0"\n' + FIL2, 1)
    two = two_col(obj("A", rng(0, 2, 0.12), filament=1) + obj("B", "", filament=2))
    fails_with(two, "prime tower", "different ranges on a plate with a prime tower refused")
    same = two_col(obj("A", rng(0, 2, 0.12), filament=1) + obj("B", rng(0, 2, 0.12), filament=2))
    build(same, "same")
    check("same ranges on a prime tower plate build", True)
    apart = two_col(obj("A", rng(0, 2, 0.12), filament=1) + obj("B", "", filament=2, plate=2))
    build(apart, "apart")
    check("different ranges on separate one-colour plates build", True)
    band = two_col(obj("A", rng(0, 2, 0.12, "filament = 2\n"), filament=1) + obj("B", rng(0, 2, 0.12), filament=1))
    sb = build(band, "band")
    gb, _ = ranges.parse(zipfile.ZipFile(sb.path).read(ranges.FILE).decode())
    check("range filament written as extruder", gb[1][0][2].get("extruder") == "2" and gb[2][0][2].get("extruder") == "0", gb)
    fails_with(two_col(obj("A", rng(0, 2, 0.12, "filament = 2\n"), filament=1) + obj("B", "", filament=1)), "prime tower",
               "a range filament makes the plate two-colour, so the prime tower rule applies")
    fails_with(HEAD % ("", obj("A", rng(0, 2, 0.12, "filament = 3\n"))), "does not exist", "range filament slot must exist")
    one = HEAD % ("", obj("A", rng(0, 2, 0.12)) + obj("B", ""))
    build(one, "one")
    check("different ranges on a single-colour plate (no tower) build", True)

    # 5. validator on hand-edited files
    def tamper(xml_text, name):
        src = zipfile.ZipFile(s.path)
        out = os.path.join(tmp, name + ".3mf")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zo:
            for i in src.infolist():
                zo.writestr(i, xml_text if i.filename == ranges.FILE else src.read(i.filename))
        return [f for f in validate.validate(out, mode="strict").findings if f.rule == "R40"]

    def has(fs, needle):
        return any(needle in f.msg for f in fs)
    fs = tamper('<objects><object id="1"><range min_z="0" max_z="2"><option opt_key="wall_loops">3</option></range></object></objects>', "nolh")
    check("R40: range without layer_height", has(fs, "no layer_height"), [f.msg for f in fs])
    fs = tamper('<objects><object id="0"><range min_z="0" max_z="2"><option opt_key="layer_height">0.1</option></range></object></objects>', "id0")
    check("R40: object id 0", has(fs, "not a positive integer"), [f.msg for f in fs])
    fs = tamper('<objects><object id="4"><range min_z="0" max_z="2"><option opt_key="layer_height">0.1</option></range></object></objects>', "id4")
    check("R40: object id past the model", has(fs, "the model has 1 object"), [f.msg for f in fs])
    fs = tamper('<objects><object id="1"><range min_z="0" max_z="2"><option opt_key="layer_height">0.1</option>'
                '<option opt_key="seam_position">back</option></range></object></objects>', "seam")
    check("R40: object-only setting in a range", has(fs, "cannot be set in a height range"), [f.msg for f in fs])
    fs = tamper('<objects><object id="1"><range min_z="0" max_z="2"><option opt_key="layer_height">0.1</option>'
                '<option opt_key="wall_loops">many</option></range></object></objects>', "badval")
    check("R40: bad value checked like an override", bool(fs), [f.msg for f in fs])
    fs = tamper('<objects><object id="1"><range min_z="0" max_z="2"><option opt_key="layer_height">0.1</option>'
                '<option opt_key="extruder">7</option></range></object></objects>', "ext7")
    check("R40: extruder past the filament count", has(fs, "extruder '7'"), [f.msg for f in fs])
    fs = tamper('<objects><object id="1"><range min_z="0" max_z="2"', "broken")
    check("R40: broken XML", has(fs, "not well-formed"), [f.msg for f in fs])
    d = [f for f in validate.validate(os.path.join(tmp, "nolh.3mf")).findings if f.rule == "R40"]
    check("R40 missing layer_height is an error in default mode too", any(f.level == "error" for f in d), [str(f) for f in d])

    # 6. layer limits follow the nozzle (Slicing.cpp)
    check("limits from the config", ranges.layer_limits({"min_layer_height": ["0.08"], "max_layer_height": ["0.28"]}) == (0.08, 0.28))
    lim = ranges.layer_limits({"min_layer_height": ["0"], "max_layer_height": ["0"], "nozzle_diameter": ["0.4"]})
    check("limits default 0.07 / 0.75 x nozzle", abs(lim[0] - 0.07) < 1e-9 and abs(lim[1] - 0.3) < 1e-9, lim)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n%d failure(s)" % len(fails))
sys.exit(1 if fails else 0)
