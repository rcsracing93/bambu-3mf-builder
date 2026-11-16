#!/usr/bin/env python3
"""Patch mode: printer change (same bed, Studio's alias mapping) and the print-host privacy keys. Every input is synthetic: builder output
made in this test (and converted to the non-split form). Never a private file.
Covers: X1C -> P1S and P1S -> X1C in 2.8.2 and 2.9.1, split and non-split (the output equals the builder's own file for the new printer with the
mapped presets, the post-checks pass, default mode 0 errors, strict findings no new ones, untouched entries byte for byte); the same-bed groups
derived from the profiles; refusals (other bed, multi-nozzle either way, nozzle diameter, same printer, user preset slot, no / two alias
candidates, explicit preset incompatible); an explicit preset that resolves a refusal; keep_changes (Studio's Transfer) and the default (Discard);
designer printer settings dropped and listed; R23 / R37 re-check refusals; post-check mutations; print-host keys blanked and absolute paths warned.
Run: python3 -I tests/test_patch_printer.py"""
import json, os, re, sys, tempfile, zipfile
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import package, patch, patch_cfg, patch_job, patch_printer, patch_read, patch_verify, privacy, validate  # noqa: E402
from bambu3mf.job import load_job  # noqa: E402
from bambu3mf.patch_job import PatchRefused  # noqa: E402
from bambu3mf.patch_zip import _Info  # noqa: E402
from bambu3mf.profiles import ProfileSet  # noqa: E402

failed = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)[:300]) if (extra and not cond) else ""))
    if not cond:
        failed.append(name)


tmp = tempfile.mkdtemp()
PS, MS, M3 = "Metadata/project_settings.config", "Metadata/model_settings.config", "3D/3dmodel.model"
MESH = os.path.join(H, "..", "examples", "data")
X1C, P1S, A1 = "Bambu Lab X1 Carbon 0.4 nozzle", "Bambu Lab P1S 0.4 nozzle", "Bambu Lab A1 0.4 nozzle"
FILS = {"X1C": [("Bambu PLA Basic @BBL X1C", "#111111"), ("Bambu PETG HF @BBL X1C", "#FFFFFF")],
        "P1S": [("Bambu PLA Basic @BBL P1S 0.4 nozzle", "#111111"), ("Bambu PETG HF @BBL P1S 0.4 nozzle", "#FFFFFF")]}
NAME = {X1C: "X1C", P1S: "P1S"}


def build(name, version, printer, fils=None, one_object=False, f1_settings="", process="0.16mm Optimal @BBL X1C"):
    """Two filaments on one plate; one object (two parts) so that the non-split form can be made from it."""
    fils = fils or FILS[NAME[printer]]
    t = 'studio = "%s"\noutput = "%s.3mf"\nprinter = "%s"\nprocess = "%s"\nplate_type = "High Temp Plate"\n[settings]\nwall_loops = 4\n' % (version, name, printer, process)
    for i, (pr, col) in enumerate(fils):
        t += '[[filament]]\npreset = "%s"\ncolor = "%s"\n' % (pr, col)
        if i == 0 and f1_settings:
            t += "[filament.settings]\n%s\n" % f1_settings
    t += '[[object]]\nname = "Cube"\n[[object.part]]\nname = "Lower"\nmesh = "%s/slab1.stl"\nfilament = 1\n[[object.part]]\nname = "Upper"\nmesh = "%s/slab2.stl"\nfilament = 2\n' % (MESH, MESH)
    p = os.path.join(tmp, name + ".toml")
    open(p, "w").write(t)
    out = os.path.join(tmp, name + ".3mf")
    package.build(load_job(p), out)
    return out


def to_nonsplit(src, dst):
    z = zipfile.ZipFile(src)
    root = z.read(M3).decode()
    mesh = re.search(r"<object .*?</object>", z.read("3D/Objects/object_1.model").decode(), re.S).group(0)
    mesh = re.sub(r' p:UUID="[^"]*"', "", mesh)
    root = root.replace(' xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"', "").replace(' requiredextensions="p"', "")
    root = re.sub(r' p:UUID="[^"]*"', "", root).replace(' p:path="/3D/Objects/object_1.model"', "")
    root = root.replace("<resources>\n", "<resources>\n  " + mesh + "\n")
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
        for i in z.infolist():
            if i.filename in ("3D/Objects/object_1.model", "3D/_rels/3dmodel.model.rels"):
                continue
            zo.writestr(i.filename, root.encode() if i.filename == M3 else z.read(i.filename))
    return dst


def cfg_edit(src, name, f):
    dst = os.path.join(tmp, name + ".3mf")
    z = zipfile.ZipFile(src)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
        for i in z.infolist():
            d = z.read(i.filename)
            if i.filename == PS:
                c = json.loads(d)
                f(c)
                d = json.dumps(c, indent=4, ensure_ascii=False).encode() + b"\n"
            zi = zipfile.ZipInfo(i.filename, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zo.writestr(zi, d)
    return dst


def run_job(base, body, name="out"):
    jp = os.path.join(tmp, name + ".toml")
    out = os.path.join(tmp, name + ".3mf")
    for x in (out, out + ".part"):
        if os.path.exists(x):
            os.remove(x)
    open(jp, "w").write('patch = "%s"\noutput = "%s"\n%s' % (base, out, body))
    return patch.run(patch_job.load_patch_job(jp))


def refused(base, body, name="ref"):
    try:
        run_job(base, body, name)
    except PatchRefused as e:
        return " | ".join(e.problems)
    return None


def cfg_of(path):
    return json.loads(zipfile.ZipFile(path).read(PS))


def sha_all(path, skip=()):
    z = zipfile.ZipFile(path)
    import hashlib
    return {n: hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist() if n not in skip}


def vrep(path, mode="default"):
    return validate.validate(path, mode=mode)


# ---- same-bed groups, derived from the vendored profiles ---------------------------------------------------------------------------------
G1 = sorted(["Bambu Lab P1P 0.4 nozzle", "Bambu Lab P1S 0.4 nozzle", "Bambu Lab X1 0.4 nozzle", "Bambu Lab X1 Carbon 0.4 nozzle", "Bambu Lab X1E 0.4 nozzle"])
G2 = sorted(["Bambu Lab A1 0.4 nozzle", "Bambu Lab P2S 0.4 nozzle"])
for tag in ("2.8.2.61", "2.9.1.70"):
    ps = ProfileSet(tag)
    check("[%s] same-bed group of the P1S: X1C, X1, X1E, P1S, P1P" % tag, patch_printer.same_bed_printers(ps, P1S) == G1, patch_printer.same_bed_printers(ps, P1S))
    check("[%s] same-bed group of the A1: A1, P2S (another height than the P1S)" % tag, patch_printer.same_bed_printers(ps, A1) == G2, patch_printer.same_bed_printers(ps, A1))
    a2l = patch_printer.same_bed_printers(ps, "Bambu Lab A2L 0.4 nozzle")
    check("[%s] same-bed group of the A2L: %s" % (tag, "A2L, H3C" if tag.startswith("2.9") else "A2L alone"),
          a2l == (["Bambu Lab A2L 0.4 nozzle", "Bambu Lab H3C 0.4 nozzle"] if tag.startswith("2.9") else ["Bambu Lab A2L 0.4 nozzle"]), a2l)
    check("[%s] A1 mini and H2S are alone; no multi-nozzle printer is in any group" % tag,
          patch_printer.same_bed_printers(ps, "Bambu Lab A1 mini 0.4 nozzle") == ["Bambu Lab A1 mini 0.4 nozzle"] and patch_printer.same_bed_printers(ps, "Bambu Lab H2S 0.4 nozzle") == ["Bambu Lab H2S 0.4 nozzle"]
          and all("H2D" not in n and "H2C" not in n and "X2D" not in n for g in (G1, G2) for n in g))
check("alias rule: the name up to the first @, right-trimmed; no @ = the whole name",
      patch_printer.alias_of("Bambu PLA Basic @BBL X1C") == "Bambu PLA Basic" and patch_printer.alias_of("Generic PLA") == "Generic PLA" and patch_printer.alias_of("@x") == "@x")

# ---- the printer change, every form -------------------------------------------------------------------------------------------------------------
DIRECT = {}
for ver in ("2.8.2", "2.9.1"):
    t = ver.replace(".", "")
    for old, new in ((X1C, P1S), (P1S, X1C)):
        tag = "%s_%s_%s" % (NAME[old], NAME[new], t)
        split = build("in_" + tag, ver, old)
        nonsplit = to_nonsplit(build("one_" + tag, ver, old), os.path.join(tmp, "ns_" + tag + ".3mf"))
        direct = build("direct_" + tag, ver, new)                            # the builder's own file for the new printer, with the mapped presets
        for form, base in (("split", split), ("non-split", nonsplit)):
            label = "[%s %s -> %s %s]" % (ver, NAME[old], NAME[new], form)
            try:
                check(label + " the input validates with 0 errors", not vrep(base).errors, [str(f)[:90] for f in vrep(base).errors])
                r = run_job(base, 'printer = "%s"\n' % new, "pc_" + form.replace("-", "") + "_" + tag)
                o, i = cfg_of(r.path), cfg_of(base)
                d = cfg_of(direct)
                check(label + " post-checks pass, default mode 0 errors, strict: nothing new", all(c[1] for c in r.checks) and not vrep(r.path).errors,
                      ([c for c in r.checks if not c[1]], [str(f)[:90] for f in vrep(r.path).errors]))
                check(label + " project settings = the builder's own config for the new printer with the mapped presets",
                      sorted(k for k in set(o) | set(d) if o.get(k) != d.get(k)) == [], sorted(k for k in set(o) | set(d) if o.get(k) != d.get(k)))
                check(label + " printer, slots mapped by alias, process kept (still compatible)",
                      o["printer_settings_id"] == new and o["filament_settings_id"] == d["filament_settings_id"] and o["print_settings_id"] == i["print_settings_id"], o["filament_settings_id"])
                check(label + " the summary names the printer change and each mapped slot",
                      any(c.startswith("printer: ") for c in r.changes) and sum("preset match for the new printer, slot" in c for c in r.changes) == 2, r.changes)
                a, b = sha_all(base, skip=(PS,)), sha_all(r.path, skip=(PS,))
                check(label + " every entry but project_settings.config is byte for byte the input's", a == b, [k for k in a if a[k] != b.get(k)])
                check(label + " bed, nozzle and variants are the input's", all(o[k] == i[k] for k in ("printable_area", "printable_height", "bed_exclude_area", "nozzle_diameter", "printer_extruder_variant")))
                check(label + " printer entry of the diff lists empty, designer's other entries as they were",
                      o.get("inherits_group", [""])[-1] == "" and o["different_settings_to_system"][-1] == "" and o["different_settings_to_system"][0] == i["different_settings_to_system"][0])
                sv = vrep(r.path, "strict")
                sin = vrep(base, "strict")
                new_keys = {(f.rule, f.where, f.cls) for f in sv.findings if f.level in ("error", "warning")} - {(f.rule, f.where, f.cls) for f in sin.findings if f.level in ("error", "warning")}
                check(label + " strict mode: no finding the input did not have", not new_keys, sorted(new_keys)[:3])
            except Exception as e:                                           # noqa: BLE001
                check(label + " runs", False, "%s: %s" % (type(e).__name__, e))
        DIRECT[tag] = (split, direct)

ps291 = ProfileSet("2.9.1.70")
# ---- refusals --------------------------------------------------------------------------------------------------------------------------------------------
BASE = build("base_p1s", "2.9.1", P1S)
BASE282 = build("base_p1s282", "2.8.2", P1S)
for lbl, base in (("2.9.1", BASE), ("2.8.2", BASE282)):
    m = refused(base, 'printer = "%s"\n' % A1, "r_a1")
    check("[%s] P1S -> A1 refused: another printable_height, groups named" % lbl, m and "printable_height differs" in m and "Printers this patch can change" in m and "X1 Carbon" in m, m)
A1F = build("base_a1", "2.9.1", A1, fils=[("Bambu PLA Basic @BBL A1", "#111111"), ("Bambu PETG HF @BBL A1", "#FFFFFF")], process="0.20mm Standard @BBL A1")
check("fixture: an A1 project validates", not vrep(A1F).errors, [str(f)[:90] for f in vrep(A1F).errors])
m = refused(A1F, 'printer = "Bambu Lab P2S 0.4 nozzle"\n', "r_p2s")
check("A1 -> P2S is allowed (same bed; 1 and 3 extruder variants are cut again, tests/test_patch_printer_variants.py)", m is None, m)
check("the change targets of the A1 are the A1 itself; of the P1S the five printers of the group", patch_printer.change_targets(ps291, A1) == [A1] and patch_printer.change_targets(ps291, P1S) == G1, patch_printer.change_targets(ps291, A1))
m = refused(BASE, 'printer = "Bambu Lab A1 mini 0.4 nozzle"\n', "r_mini")
check("P1S -> A1 mini refused: bed differs", m and "printable_area differs" in m, m)
m = refused(BASE, 'printer = "Bambu Lab H2D 0.4 nozzle"\n', "r_h2d")
check("P1S -> a multi-nozzle printer (H2D) refused", m and "has 2 nozzles" in m, m)
m = refused(BASE, 'printer = "Bambu Lab X2D 0.4 nozzle"\n', "r_x2d")
check("P1S -> X2D refused", m and "nozzles" in m, m)
two = cfg_edit(BASE, "two_nozzles", lambda c: c.__setitem__("nozzle_diameter", ["0.4", "0.4"]))
m = refused(two, 'printer = "%s"\n' % X1C, "r_from_multi")
check("a file with a multi-nozzle printer -> any printer refused", m and "2 nozzles" in m, m)
h2d, _ = ps291.resolve("machine", "Bambu Lab H2D 0.4 nozzle")
p1s_cfg = cfg_of(BASE)
why = patch_printer._printer_reasons(ps291, "Bambu Lab H2D 0.4 nozzle", h2d, P1S, ps291.resolve("machine", P1S)[0], p1s_cfg)
check("from a multi-nozzle printer preset: the reason names the nozzle count", why and "has 2 nozzles" in why[0], why)
m = refused(BASE, 'printer = "Bambu Lab P1S 0.6 nozzle"\n', "r_nozzle")
check("P1S 0.4 -> P1S 0.6 refused: nozzle diameter", m and "nozzle diameter differs" in m, m)
m = refused(BASE, 'printer = "%s"\n' % P1S, "r_same")
check("the file's own printer refused", m and "already uses" in m, m)
m = refused(BASE, 'printer = "Bambu Lab P1S 0.4 nozzel"\n', "r_typo")
check("unknown printer: closest names", m and "Closest" in m, m)
m = refused(BASE, 'printer = "%s"\n[[filament]]\nslot = 3\npreset = "Bambu PLA Basic @BBL X1C"\ncolor = "#000000"\n' % X1C, "r_add")
check("a printer change together with a new slot refused", m and "own patch" in m, m)
m = refused(BASE, 'printer = 5\n', "r_type")
check("printer must be a name", m and "exact name" in m, m)

# a user-preset-like slot (no system name, no parent) cannot be matched
user_slot = cfg_edit(BASE, "user_slot", lambda c: c["filament_settings_id"].__setitem__(0, "My own PLA"))
m = refused(user_slot, 'printer = "%s"\n' % X1C, "r_user")
check("a slot that holds a non-system preset: refused, names the slot, asks for a preset", m and "slot 1" in m and "not a Bambu system preset" in m, m)

# alias: no candidate and two candidates (the profiles have none of either for these printers, so the candidate list is replaced)
orig_names = patch_printer.compatible_names
patch_printer.compatible_names = lambda ps_, kind, printer: [n for n in orig_names(ps_, kind, printer) if patch_printer.alias_of(n) != "Bambu PLA Basic"] if kind == "filament" else orig_names(ps_, kind, printer)
m = refused(BASE, 'printer = "%s"\n' % X1C, "r_none")
check("no preset with the alias: refused, names the slot, the alias and presets that are close", m and "slot 1" in m and "no preset with the alias 'Bambu PLA Basic'" in m and "close" in m, m)
r = run_job(BASE, 'printer = "%s"\n[[filament]]\nslot = 1\npreset = "Bambu PLA Basic @BBL X1C"\n' % X1C, "r_none_ok")
check("   an explicit preset for the slot resolves it; the other slot is still matched by alias", cfg_of(r.path)["filament_settings_id"] == ["Bambu PLA Basic @BBL X1C", "Bambu PETG HF @BBL X1C"] and all(c[1] for c in r.checks),
      cfg_of(r.path)["filament_settings_id"])
patch_printer.compatible_names = lambda ps_, kind, printer: orig_names(ps_, kind, printer) + (["Bambu PLA Basic @BBL X1C copy", "Bambu PLA Basic @Other"] if kind == "filament" else [])
m = refused(BASE, 'printer = "%s"\n' % X1C, "r_two")
check("two presets with the alias: refused, both named, not guessed", m and "slot 1" in m and "Bambu PLA Basic @BBL X1C copy" in m and "will not guess" in m, m)
r = run_job(BASE, 'printer = "%s"\n[[filament]]\nslot = 1\npreset = "Bambu PLA Basic @BBL X1C"\n' % X1C, "r_two_ok")
check("   an explicit preset resolves it", cfg_of(r.path)["filament_settings_id"][0] == "Bambu PLA Basic @BBL X1C" and all(c[1] for c in r.checks))
patch_printer.compatible_names = orig_names
# an explicit process or slot preset that does not fit the new printer
m = refused(BASE, 'printer = "%s"\nprocess = "0.20mm Standard @BBL A2L"\n' % X1C, "r_proc")
check("an explicit process made for another printer: refused against the NEW printer", m and "not made for" in m and X1C in m, m)
m = refused(BASE, 'printer = "%s"\n[[filament]]\nslot = 2\npreset = "Bambu PETG HF @BBL P1S 0.4 nozzle"\n' % X1C, "r_slot")
check("an explicit slot preset made for the OLD printer: refused", m and "not made for" in m, m)
# an explicit process that fits both
r = run_job(BASE, 'printer = "%s"\nprocess = "0.20mm Standard @BBL X1C"\n' % X1C, "r_proc_ok")
check("an explicit process for the new printer is used (the process swap rule applies)", cfg_of(r.path)["print_settings_id"] == "0.20mm Standard @BBL X1C" and all(c[1] for c in r.checks) and not vrep(r.path).errors)
# a process that Studio would replace: P1S file with a process made for the P1S only

# ---- keep_changes (Studio's Transfer) and the default (Discard) -------------------------------------------------------------------------------------
KEEP = build("keep_p1s", "2.9.1", P1S, f1_settings="nozzle_temperature = 225")
check("fixture: slot 1 carries a designer change in the diff list", "nozzle_temperature" in cfg_of(KEEP)["different_settings_to_system"][1], cfg_of(KEEP)["different_settings_to_system"])
r = run_job(KEEP, 'printer = "%s"\n' % X1C, "keep_drop")
o = cfg_of(r.path)
check("default: the designer's slot change is dropped (Studio's Discard) and listed in the summary",
      o["nozzle_temperature"][0] != "225" and "nozzle_temperature" not in o.get("different_settings_to_system", ["", "", "", ""])[1]
      and any(lbl == "filament slot 1" and any(k == "nozzle_temperature" for k, _v in keys) for lbl, keys in r.dropped), (r.dropped, o["nozzle_temperature"]))
r = run_job(KEEP, 'printer = "%s"\n[[filament]]\nslot = 1\nkeep_changes = true\n' % X1C, "keep_keep")
o = cfg_of(r.path)
check("keep_changes = true on the slot: the change is transferred to the mapped preset, listed as kept, post-checks pass",
      o["nozzle_temperature"][0] == "225" and "nozzle_temperature" in o["different_settings_to_system"][1] and o["filament_settings_id"][0] == "Bambu PLA Basic @BBL X1C"
      and any(lbl == "filament slot 1" for lbl, _k in r.kept) and all(c[1] for c in r.checks) and not vrep(r.path).errors, (r.kept, [c for c in r.checks if not c[1]]))
m = refused(KEEP, 'printer = "%s"\nkeep_changes = true\n[[filament]]\nslot = 1\ncolor = "#ABCDEF"\n' % P1S, "keep_same")
check("top-level keep_changes with a printer is accepted by the reader (nothing to keep when the process stays)", m is not None and "already uses" in m, m)

# the designer's own printer settings are replaced by the new preset's and listed
edit_printer = cfg_edit(BASE, "edit_printer", lambda c: (c.__setitem__("machine_end_gcode", "M104 S0 ; my own end"), c["different_settings_to_system"].__setitem__(-1, "machine_end_gcode"), c.setdefault("inherits_group", [""] * len(c["different_settings_to_system"])).__setitem__(-1, P1S)))
r = run_job(edit_printer, 'printer = "%s"\n' % X1C, "edit_printer_out")
o = cfg_of(r.path)
check("fixture: the printer carries a designer change", not vrep(edit_printer).errors, [str(f)[:90] for f in vrep(edit_printer).errors])
check("designer's printer setting replaced by the new preset's, listed, diff entry empty, checks pass",
      "my own end" not in o["machine_end_gcode"] and any(lbl == "printer" and any(k == "machine_end_gcode" for k, _v in keys) for lbl, keys in r.dropped) and o["different_settings_to_system"][-1] == "" and all(c[1] for c in r.checks), r.dropped)

# ---- plates and tower re-check -------------------------------------------------------------------------------------------------------------------------
orig_apply = patch_cfg.apply_config


def with_cfg(fn):
    def wrapped(src, ps, req):
        res = orig_apply(src, ps, req)
        fn(res.cfg)
        return res
    return wrapped


patch_cfg.apply_config = with_cfg(lambda c: c.__setitem__("wipe_tower_x", ["400"] * len(c["wipe_tower_x"])))
m = refused(BASE, 'printer = "%s"\n' % X1C, "r_r37")
check("R37 re-check: a tower that no longer fits the new printer refuses the patch, no output", m and "prime tower (R37)" in m and not os.path.exists(os.path.join(tmp, "r_r37.3mf.part")), m)
patch_cfg.apply_config = with_cfg(lambda c: c.__setitem__("printable_area", ["0x0", "60x0", "60x60", "0x60"]))
m = refused(BASE, 'printer = "%s"\n' % X1C, "r_r23")
check("R23 re-check: parts that no longer lie on the plate refuse the patch", m and "plates (R23)" in m, m)
patch_cfg.apply_config = orig_apply

# ---- post-check mutations on a good printer change ---------------------------------------------------------------------------------------------
seen = {}
orig_verify = patch_verify.verify


def spy(src, out_path, plan, input_strict, ps):
    seen["a"] = (src, plan, input_strict, ps)
    return orig_verify(src, out_path, plan, input_strict, ps)


patch_verify.verify = spy
GOOD = run_job(BASE, 'printer = "%s"\n' % X1C, "good")
patch_verify.verify = orig_verify
SRC, PLAN, D_STRICT, PSX = seen["a"]
check("the honest printer change passes every post-check", all(c[1] for c in GOOD.checks) and any("printer setting is the value" in c[0] for c in GOOD.checks), GOOD.checks)


def mutate(name, fn):
    p = os.path.join(tmp, "mut_%s.3mf" % name)
    z = zipfile.ZipFile(GOOD.path)
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zo:
        for i in z.infolist():
            d = z.read(i.filename)
            if i.filename == PS:
                c = json.loads(d)
                fn(c)
                d = json.dumps(c, indent=4, ensure_ascii=False).encode() + b"\n"
            zi = _Info(i.filename, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.force_utf8 = bool(i.flag_bits & 0x800)
            zo.writestr(zi, d)
    return p


def fails(name, fn, text):
    ck = patch_verify.verify(SRC, mutate(name, fn), PLAN, D_STRICT, PSX)
    return any(text in nm for nm, _ok, _d in ck.failed()), [nm for nm, _ok, _d in ck.failed()]


ok_, got = fails("pk", lambda c: c.__setitem__("machine_start_gcode", c["machine_start_gcode"] + "\nG4 S1"), "printer setting is the value")
check("mutation: a printer setting that is not the new preset's -> post-check fails", ok_, got)
ok_, got = fails("pid", lambda c: c.__setitem__("printer_settings_id", P1S), "printer setting is the value")
check("mutation: the old printer name left in printer_settings_id -> post-check fails", ok_, got)
ok_, got = fails("bed", lambda c: c.__setitem__("printable_area", ["0x0", "255x0", "255x256", "0x256"]), "bed and nozzle")
check("mutation: the bed changed -> post-check fails", ok_, got)
ok_, got = fails("diffentry", lambda c: c["different_settings_to_system"].__setitem__(-1, "machine_end_gcode"), "printer entry of the diff lists")
check("mutation: the printer diff entry not empty -> post-check fails", ok_, got)
ok_, got = fails("lh", lambda c: c.__setitem__("layer_height", "0.3"), "every difference is one the job asked for")
check("mutation: a setting that was not asked for changed -> post-check fails", ok_, got)
ok_, got = fails("tower", lambda c: c.__setitem__("wipe_tower_x", ["400"] * len(c["wipe_tower_x"])), "prime tower (R37)")
check("mutation: the tower pushed off the plate -> the R37 re-check fails", ok_, got)


# ---- print-host keys (rule 5) and absolute paths ---------------------------------------------------------------------------------------------------------
def set_host(c):
    c["print_host"] = "192.168.7.20"
    c["printhost_apikey"] = "SECRETKEY-12345"
    c["printhost_user"] = "owner"
    c["printhost_port"] = "8080"
    c["print_host_webui"] = "http://192.168.7.20:7125"
    c["bed_custom_texture"] = "C:\\Users\\someone\\Pictures\\bed.png"


hosted = cfg_edit(BASE, "hosted", set_host)
check("fixture: host keys and a path are set; the input has no error", not vrep(hosted).errors, [str(f)[:90] for f in vrep(hosted).errors])
r = run_job(hosted, '[settings]\nwall_loops = 3\n', "host_out")
o = cfg_of(r.path)
check("print-host keys that were set are blank in the output", all(o[k] == "" for k in ("print_host", "printhost_apikey", "printhost_user", "printhost_port", "print_host_webui")), {k: o.get(k) for k in privacy.HOST_KEYS})
check("   the summary lists them (names only, no values)", any(c.startswith("privacy: print-host") and "printhost_apikey" in c for c in r.changes) and "SECRETKEY" not in " ".join(r.changes + r.warnings) and "192.168" not in " ".join(r.changes + r.warnings), r.changes)
check("   the value is nowhere in the output file", b"SECRETKEY" not in zipfile.ZipFile(r.path).read(PS) and all(c[1] for c in r.checks) and not vrep(r.path).errors, [c for c in r.checks if not c[1]])
check("   an absolute path in a setting is a warning and stays (listed by key, not shown)",
      any("bed_custom_texture" in w and "absolute path" in w for w in r.warnings) and o.get("bed_custom_texture", "").startswith("C:") and not any("someone" in w for w in r.warnings), r.warnings)
check("   the other keys follow the job: wall_loops changed, nothing else", [k for k in set(o) | set(cfg_of(hosted)) if o.get(k) != cfg_of(hosted).get(k)].__len__() == 1 + 5 + (1 if o.get("different_settings_to_system") != cfg_of(hosted).get("different_settings_to_system") else 0),
      sorted(k for k in set(o) | set(cfg_of(hosted)) if o.get(k) != cfg_of(hosted).get(k)))
r = run_job(BASE, '[settings]\nwall_loops = 3\n', "nohost_out")
check("no host key set: nothing blanked, no privacy line", not any(c.startswith("privacy: print-host") for c in r.changes) and not any("absolute path" in w for w in r.warnings), r.changes)
r = run_job(hosted, 'printer = "%s"\n' % X1C, "host_pc")
check("host keys are blanked in a printer change too, post-checks pass", cfg_of(r.path)["print_host"] == "" and all(c[1] for c in r.checks) and not vrep(r.path).errors, [c for c in r.checks if not c[1]])
leftover = [k for k in privacy.HOST_KEYS if cfg_of(r.path).get(k) not in (None, "")]
check("   privacy scan of the output: no print-host finding", not any("print host field" in f for f in privacy.scan_zip(r.path)) and not leftover, leftover)
# mutation: a host key left set
ck_src = patch_read.open_source(hosted)
seen2 = {}


def spy2(src, out_path, plan, input_strict, ps):
    seen2["a"] = (src, plan, input_strict, ps)
    return orig_verify(src, out_path, plan, input_strict, ps)


patch_verify.verify = spy2
HG = run_job(hosted, '[settings]\nwall_loops = 3\n', "host_good")
patch_verify.verify = orig_verify
SRC, PLAN, D_STRICT, PSX = seen2["a"]


def mutate_h(name, fn):
    p = os.path.join(tmp, "muth_%s.3mf" % name)
    z = zipfile.ZipFile(HG.path)
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zo:
        for i in z.infolist():
            d = z.read(i.filename)
            if i.filename == PS:
                c = json.loads(d)
                fn(c)
                d = json.dumps(c, indent=4, ensure_ascii=False).encode() + b"\n"
            zi = _Info(i.filename, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.force_utf8 = bool(i.flag_bits & 0x800)
            zo.writestr(zi, d)
    return p


ck = patch_verify.verify(SRC, mutate_h("left", lambda c: c.__setitem__("printhost_apikey", "SECRETKEY-12345")), PLAN, D_STRICT, PSX)
check("mutation: a print-host key left set -> post-check fails", any("print-host keys" in nm for nm, _o, _d in ck.failed()), [nm for nm, _o, _d in ck.failed()])
ck = patch_verify.verify(SRC, mutate_h("extra", lambda c: c.__setitem__("printhost_cafile", "x")), PLAN, D_STRICT, PSX)
check("mutation: another host key set that was not set before -> post-check fails", any("every difference" in nm for nm, _o, _d in ck.failed()), [nm for nm, _o, _d in ck.failed()])

print("\n%d failed" % len(failed))
sys.exit(1 if failed else 0)
