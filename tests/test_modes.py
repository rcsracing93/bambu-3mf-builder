#!/usr/bin/env python3
"""Validator modes: the class x mode table, flags in any order, the JSON object, PRIVACY never changes the exit code.
Run: python3 -I tests/test_modes.py"""
import json, os, re, subprocess, sys, tempfile, zipfile
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import validate, package, modes  # noqa: E402
from bambu3mf.job import load_job  # noqa: E402

fails = []
def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)) if (extra and not cond) else ""))
    if not cond: fails.append(name)

tmp = tempfile.mkdtemp()
GOOD = os.path.join(tmp, "good.3mf")
package.build(load_job(os.path.join(H, "jobs", "four_color_282.toml")), GOOD)
LISTS = os.path.join(tmp, "lists.3mf")
package.build(load_job(os.path.join(H, "data", "mutation_base.toml")), LISTS)
PS, M3 = "Metadata/project_settings.config", "3D/3dmodel.model"
CLI = os.path.join(H, "..", "scripts", "validate_bambu_3mf.py")


def make(src, edits, name):
    zin = zipfile.ZipFile(src)
    p = os.path.join(tmp, name)
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zo:
        for i in zin.infolist():
            d = zin.read(i.filename)
            if i.filename in edits:
                d = edits[i.filename](d)
            zo.writestr(i.filename, d)
    return p


def cfgedit(fn):
    def go(b):
        c = json.loads(b); fn(c); return json.dumps(c, indent=4).encode()
    return go


def sub(pat, rep):
    return lambda b: re.sub(pat, rep, b.decode(), count=1).encode()


def cli(*args):
    r = subprocess.run([sys.executable, "-I", CLI] + list(args), capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


# 1. the table itself: every class, both modes
want = {"default": dict(LOAD="error", CHANGE="warning", KEEP="warning", FORM="silent", PRIVACY="warning", SKIPPED="warning", TOOL="note"),
        "strict": dict(LOAD="error", CHANGE="error", KEEP="error", FORM="error", PRIVACY="warning", SKIPPED="warning", TOOL="note")}
for mode in ("default", "strict"):
    for cls in modes.CLASSES:
        check("table %s %s -> %s" % (mode, cls, want[mode][cls]), modes.TABLE[mode][cls] == want[mode][cls], modes.TABLE[mode].get(cls))
check("a call-site warning is never raised by the table (strict FORM stays a warning)", modes.level_for("FORM", "strict", "warning") == "warning")
check("a call-site error is lowered by the table (default CHANGE)", modes.level_for("CHANGE", "default", "error") == "warning")
check("an unlisted finding is LOAD", modes.classify(validate.Finding("R99", "error", "x", "anything")) == "LOAD")
try:
    validate.validate(GOOD, mode="loose"); check("unknown mode refused", False)
except ValueError:
    check("unknown mode refused", True)

# 2. one real finding per class, in both modes (file, rule, expected class, level in default, level in strict, run with privacy)
cases = [
    ("LOAD", "R1", make(GOOD, {M3: sub(r"BambuStudio-02", "Slicer-02")}, "load.3mf"), "error", "error"),
    ("CHANGE", "R3", make(LISTS, {PS: cfgedit(lambda c: (c.__setitem__("different_settings_to_system", [""] * 4), c.__setitem__("inherits_group", [""] * 4)))}, "change.3mf"), "warning", "error"),
    ("KEEP", "R23", make(GOOD, {M3: sub(r'(transform="1 0 0 0 1 0 0 0 1 128 128 )[^"]+', r"\g<1>-1")}, "keep.3mf"), "warning", "error"),
    ("FORM", "R21", make(GOOD, {M3: sub(r' requiredextensions="p"', "")}, "form.3mf"), None, "error"),
    ("SKIPPED", "R16", make(GOOD, {PS: cfgedit(lambda c: c.__setitem__("version", "99.0.0.0"))}, "tool.3mf"), "warning", "warning"),
]
for cls, rule, path, dflt, strict in cases:
    for mode, lvl in (("default", dflt), ("strict", strict)):
        r = validate.validate(path, mode=mode)
        hit = [f for f in r.findings if f.rule == rule and f.cls == cls]
        if lvl is None:
            check("%s %s in %s mode: not reported" % (cls, rule, mode), not [f for f in r.findings if f.rule == rule] and [f for f in r.silent if f.rule == rule], [str(f)[:80] for f in r.findings])
        else:
            check("%s %s in %s mode: %s" % (cls, rule, mode, lvl), bool(hit) and all(f.level == lvl for f in hit), [(f.rule, f.cls, f.level) for f in r.findings][:4])
r = validate.validate(cases[3][2], mode="default")
check("FORM only: default mode exits 0", r.ok, [str(f)[:80] for f in r.errors])
r = validate.validate(cases[1][2], mode="default")
check("CHANGE only: default mode exits 0", r.ok, [str(f)[:80] for f in r.errors])

# 3. PRIVACY: runs only with the flag; reported as a warning; never changes the exit code
priv = make(GOOD, {M3: lambda d: d.replace(b'<metadata name="DesignerUserId"></metadata>', b'<metadata name="DesignerUserId">12505641</metadata>')}, "priv.3mf")
for mode in ("default", "strict"):
    r0 = validate.validate(priv, mode=mode)
    r1 = validate.validate(priv, mode=mode, privacy=True)
    check("PRIVACY %s: not run without privacy" % mode, not [f for f in r0.findings if f.cls == "PRIVACY"])
    check("PRIVACY %s: reported with privacy, as a warning" % mode, any(f.cls == "PRIVACY" and f.level == "warning" for f in r1.findings), [str(f)[:80] for f in r1.findings])
    check("PRIVACY %s: same ok value with and without" % mode, r0.ok == r1.ok and r1.ok)
rc0 = cli(priv)[0]
rc1 = cli(priv, "--privacy")[0]
rc2 = cli("--privacy", "--strict", priv)[0]
check("PRIVACY never changes the exit code (cli)", rc0 == rc1 == rc2 == 0, (rc0, rc1, rc2))
bad = cases[0][2]
check("PRIVACY does not hide a real error (exit stays 1)", cli(bad)[0] == 1 and cli(bad, "--privacy")[0] == 1)

# 4. flags anywhere
a = cli(GOOD, "--json", "--strict")
b = cli("--json", GOOD, "--strict")
c = cli("--strict", "--json", GOOD)
check("flags in any position give the same output", a[1] == b[1] == c[1] and a[0] == b[0] == c[0] == 0, (a[0], a[2][:100]))
check("--json FILE and FILE --json both work", json.loads(cli("--json", GOOD)[1]) == json.loads(cli(GOOD, "--json")[1]))
check("--strict changes the exit code of a CHANGE-only file", cli(cases[1][2])[0] == 0 and cli(cases[1][2], "--strict")[0] == 1 and cli("--strict", cases[1][2])[0] == 1)
check("unknown flag: exit 2", cli(GOOD, "--bogus")[0] == 2)
check("two files: exit 2", cli(GOOD, GOOD)[0] == 2)
check("no file: exit 2", cli("--json")[0] == 2)
text = cli(GOOD)[1]
check("plain output ends with error, warning and note counts", re.search(r"^ok: 0 error\(s\), \d+ warning\(s\), \d+ note\(s\)$", text, re.M) is not None, text[-120:])

# 5. JSON object
for mode_flag, mode in (([], "default"), (["--strict"], "strict")):
    j = json.loads(cli(cases[1][2], "--json", *mode_flag)[1])
    check("json is an object (%s)" % mode, isinstance(j, dict))
    check("json fields mode, studio_version, tables_applied, findings (%s)" % mode, set(j) == {"mode", "studio_version", "tables_applied", "findings"}, sorted(j))
    check("json mode is %s" % mode, j["mode"] == mode)
    check("json studio_version is the version in the file", j["studio_version"] == "02.08.02.61", j["studio_version"])
    check("json tables_applied is true for a supported version", j["tables_applied"] is True)
    check("json findings carry rule, class, level, where, message", all(set(f) == {"rule", "class", "level", "where", "message"} for f in j["findings"]) and j["findings"], j["findings"][:1])
j = json.loads(cli(cases[4][2], "--json")[1])
check("json tables_applied is false when the version is not supported", j["tables_applied"] is False and j["studio_version"] == "99.0.0.0", (j["tables_applied"], j["studio_version"]))
j = json.loads(cli(GOOD, "--json")[1])
check("json of a clean file: empty findings list", j["findings"] == [] and j["tables_applied"] is True)

# 6. one assertion per row of modes.SITES (review 0d, D6): a finding the validator makes from a fixture gets the expected class, and the row that
#    matches it is that row. A row without a fixture here fails the test; a row whose pattern no longer matches its finding, or whose class was
#    changed, fails too. EXPECT holds the class the row must give, written out on its own (not read from the table).
from bambu3mf import rules_package  # noqa: E402
TL = os.path.join(tmp, "tl.3mf")
package.build(load_job(os.path.join(H, "jobs", "four_color_tower_282.toml")), TL)
OBJ1 = "3D/Objects/object_1.model"
MSC = "Metadata/model_settings.config"


def resub(pat, rep, flags=0):
    return lambda b: re.sub(pat, rep, b.decode(), count=1, flags=flags).encode()


def dup_triangle(b):
    t = b.decode()
    tr = re.findall(r"<triangle [^>]*/>", t)
    return t.replace(tr[1], tr[0], 1).encode()


def drop_key(key):
    return cfgedit(lambda c: c.pop(key))


FX = {   # (rule, pattern) -> (file or None, expected class). None: no file reaches the finding; the message is built from the call site's text
    ("R21", r"requiredextensions"): (make(GOOD, {M3: sub(r' requiredextensions="p"', "")}, "s_reqext.3mf"), "FORM"),
    ("R21", r"has no components"): (make(LISTS, {M3: resub(r"<components>.*?</components>", "<components></components>", re.S)}, "s_nocomp.3mf"), "FORM"),
    ("R21", r"is not an object in"): (make(GOOD, {M3: sub(r'<component ([^>]*)objectid="\d+"', r'<component \1objectid="99"')}, "s_notobj.3mf"), "FORM"),
    ("R21", r"do not match the objects in the root model"): (make(GOOD, {MSC: sub(r'<object id="\d+"', '<object id="55"')}, "s_nomatch.3mf"), "FORM"),
    ("R23", r"below the plate"): (make(GOOD, {M3: sub(r'(transform="1 0 0 0 1 0 0 0 1 128 128 )[^"]+', r"\g<1>-1")}, "s_below.3mf"), "KEEP"),
    ("R22", r"running counter"): (make(GOOD, {M3: sub(r'<object id="2"', '<object id="7"')}, "s_counter.3mf"), "FORM"),
    ("R22", r"differs from Studio's scheme"): (make(GOOD, {M3: sub(r'p:UUID="000[0-9]0000-b206', 'p:UUID="000a0000-b206')}, "s_scheme.3mf"), "FORM"),
    ("R10", r"is not a system preset of this Studio version"): (make(LISTS, {PS: cfgedit(lambda c: c.__setitem__("print_settings_id", "My own process"))}, "s_notsys.3mf"), "TOOL"),
    ("R13", r"project options missing"): (make(LISTS, {PS: drop_key("curr_bed_type")}, "s_r13.3mf"), "CHANGE"),
    ("R15", r"setting\(s\) of this Studio version are missing from the config"): (make(LISTS, {PS: drop_key("wall_loops")}, "s_r15.3mf"), "CHANGE"),
    ("R16", r"is not supported \(supported"): (make(GOOD, {PS: cfgedit(lambda c: c.__setitem__("version", "99.0.0.0"))}, "s_r16.3mf"), "SKIPPED"),
    ("R24", r"an edge is used twice"): (make(GOOD, {OBJ1: dup_triangle}, "s_edge.3mf"), "KEEP"),
    ("R24", r"the surface is not closed"): (make(GOOD, {OBJ1: lambda d: re.sub(rb"<triangle [^>]*/>\s*", b"", d, count=1)}, "s_open.3mf"), "KEEP"),
    ("R24", r"closed-surface check was skipped"): (GOOD, "TOOL"),
    ("R3", r"not in different_settings_to_system"): (make(LISTS, {PS: cfgedit(lambda c: c.__setitem__("line_width", "0.5"))}, "s_r3.3mf"), "CHANGE"),
    ("R3", r"could not rebuild the system values"): (None, "FORM"),
    ("S1", r"would not survive opening.*not listed in different_settings_to_system"): (None, "CHANGE"),
    ("S1", r"would not survive opening.*project options missing"): (None, "CHANGE"),
    ("S1", r"system values could not be rebuilt"): (None, "FORM"),
    ("S1", r"project options missing from the file"): (make(LISTS, {PS: drop_key("curr_bed_type")}, "s_s1.3mf"), "CHANGE"),
    ("R37", r"\(saved position\).*sticks out of the printable area"): (make(TL, {PS: cfgedit(lambda c: c.__setitem__("wipe_tower_x", ["250"]))}, "s_r37.3mf"), "CHANGE"),
    ("R33", r"Thumbnail\w* points to"): (make(GOOD, {M3: lambda b: b.replace(b" <resources>", b' <metadata name="Thumbnail_Middle">/Metadata/nothere.png</metadata>\n <resources>', 1)}, "s_thumb.3mf"), "FORM"),
}
# the findings no file reaches: the text is what the call site writes (simulate.py, validate.py S1, rules_config.py R3)
DIRECT = {
    ("R3", r"could not rebuild the system values"): "could not rebuild the system values to check the diff lists (printer preset 'x' is not a system preset)",
    ("S1", r"would not survive opening.*not listed in different_settings_to_system"): "2 setting(s) would not survive opening the file in the Studio app (the command-line slicer keeps them) - not listed in different_settings_to_system, so Studio resets it to the preset value: a, b",
    ("S1", r"would not survive opening.*project options missing"): "1 setting(s) would not survive opening the file in the Studio app (the command-line slicer keeps them) - project options missing from the file: Studio 2.9.1 resets them to defaults: a",
    ("S1", r"system values could not be rebuilt"): "system values could not be rebuilt (why); the diff-list check was skipped",
}
rows = [(r, rx.pattern, c) for r, rx, c in modes._COMPILED]
check("SITES has %d rows and the test has %d fixtures" % (len(rows), len(FX)), len(rows) == len(FX), [(r, p) for r, p, _c in rows if (r, p) not in FX])
rule_rows = {}
for idx, (rule, pat, cls) in enumerate(rows):
    key = (rule, pat)
    check("SITES row %d %s /%s/: has a fixture" % (idx, rule, pat[:30]), key in FX)
    if key not in FX:
        continue
    path, want = FX[key]
    check("SITES row %d %s /%s/: class %s" % (idx, rule, pat[:30], want), cls == want, cls)
    rx = re.compile(pat, re.S)
    if path is None:
        msg = DIRECT[key]
        got = modes.classify(validate.Finding(rule, "error", "x", msg))
        first = next(i for i, (r2, p2, _c2) in enumerate(rows) if r2 == rule and re.compile(p2, re.S).search(msg))
        check("SITES row %d %s: the call-site text of a finding no file reaches gets %s from this row" % (idx, rule, want), rx.search(msg) and got == want and first == idx, (got, first))
        continue
    if key == ("R24", r"closed-surface check was skipped"):
        old = rules_package.MESH_CLOSED_LIMIT
        rules_package.MESH_CLOSED_LIMIT = 1
        try:
            r = validate.validate(path, mode="strict")
        finally:
            rules_package.MESH_CLOSED_LIMIT = old
    else:
        r = validate.validate(path, mode="strict")
    hits = [f for f in r.findings + r.silent if f.rule == rule and rx.search(f.msg)]
    check("SITES row %d %s /%s/: the validator makes the finding from a fixture" % (idx, rule, pat[:30]), bool(hits), [(f.rule, f.msg[:50]) for f in r.findings + r.silent][:5])
    for f in hits[:1]:
        first = next(i for i, (r2, p2, _c2) in enumerate(rows) if r2 == rule and re.compile(p2, re.S).search(f.msg))
        check("SITES row %d %s: the finding is in class %s and this is the first row that matches" % (idx, rule, want), f.cls == want and first == idx, (f.cls, first))
        bare = validate.Finding(f.rule, f.native, f.where, f.msg)
        check("SITES row %d %s: the table alone gives %s" % (idx, rule, want), modes.classify(bare) == want, modes.classify(bare))

print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
