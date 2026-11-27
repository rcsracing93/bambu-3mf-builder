#!/usr/bin/env python3
"""Patch mode, printer change between printers with another extruder variant count (v1.1: A1 <-> P2S, same bed, 1 and 3 variants).
Every input is synthetic builder output made in this test. Never a private file.
Covers, in 2.8.2 and 2.9.1, both directions: the output equals the builder's own file for the new printer with the mapped presets; post-checks
pass; default mode 0 errors; strict: no new finding; every entry but project_settings.config is the input's byte for byte; a kept designer
change is carried over by variant name (the Standard variant), the new variants take the preset's value, a variant the new printer does not have
is dropped with a warning; the default (no keep) drops it; object settings with one value per variant are refused; post-check mutations.
Run: python3 -I tests/test_patch_printer_variants.py"""
import json, os, sys, tempfile, zipfile, hashlib
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import package, patch, patch_job, patch_verify, validate  # noqa: E402
from bambu3mf.job import load_job  # noqa: E402
from bambu3mf.patch_job import PatchRefused  # noqa: E402

failed = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)[:300]) if (extra and not cond) else ""))
    if not cond:
        failed.append(name)


tmp = tempfile.mkdtemp(prefix="pv_test_")
PS = "Metadata/project_settings.config"
MESH = os.path.join(H, "..", "examples", "data")
A1, P2S = "Bambu Lab A1 0.4 nozzle", "Bambu Lab P2S 0.4 nozzle"
SHORT = {A1: "A1", P2S: "P2S"}
FILS = {A1: [("Bambu PLA Basic @BBL A1", "#111111"), ("Bambu PETG HF @BBL A1", "#FFFFFF")],
        P2S: [("Bambu PLA Basic @BBL P2S", "#111111"), ("Bambu PETG HF @BBL P2S 0.4 nozzle", "#FFFFFF")]}
PROC = {A1: "0.20mm Standard @BBL A1", P2S: "0.20mm Standard @BBL P2S"}


def build(name, version, printer, f1_settings="", obj_settings=""):
    t = 'studio = "%s"\noutput = "%s.3mf"\nprinter = "%s"\nprocess = "%s"\nplate_type = "Textured PEI Plate"\n' % (version, name, printer, PROC[printer])
    for i, (pr, col) in enumerate(FILS[printer]):
        t += '[[filament]]\npreset = "%s"\ncolor = "%s"\n' % (pr, col)
        if i == 0 and f1_settings:
            t += "[filament.settings]\n%s\n" % f1_settings
    t += '[[object]]\nname = "Cube"\n%s[[object.part]]\nname = "Lower"\nmesh = "%s/slab1.stl"\nfilament = 1\n[[object.part]]\nname = "Upper"\nmesh = "%s/slab2.stl"\nfilament = 2\n' % (
        ("[object.settings]\n%s\n" % obj_settings) if obj_settings else "", MESH, MESH)
    p = os.path.join(tmp, name + ".toml")
    open(p, "w").write(t)
    out = os.path.join(tmp, name + ".3mf")
    package.build(load_job(p), out)
    return out


def run_job(base, body, name):
    jp = os.path.join(tmp, name + ".toml")
    out = os.path.join(tmp, name + ".3mf")
    for x in (out, out + ".part"):
        if os.path.exists(x):
            os.remove(x)
    open(jp, "w").write('patch = "%s"\noutput = "%s"\n%s' % (base, out, body))
    return patch.run(patch_job.load_patch_job(jp))


def refused(base, body, name):
    try:
        run_job(base, body, name)
    except PatchRefused as e:
        return " | ".join(e.problems)
    return None


def cfg_of(path):
    return json.loads(zipfile.ZipFile(path).read(PS))


def sha_all(path, skip=()):
    z = zipfile.ZipFile(path)
    return {n: hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist() if n not in skip}


def seg(cfg, key, slot, width):
    return cfg[key][slot * width:(slot + 1) * width]


OUT = {}
for ver in ("2.8.2", "2.9.1", "2.9.2"):
    for old, new in ((A1, P2S), (P2S, A1)):
        tag = "%s_%s_%s" % (SHORT[old], SHORT[new], ver.replace(".", ""))
        label = "[%s %s -> %s]" % (ver, SHORT[old], SHORT[new])
        try:
            base = build("in_" + tag, ver, old)
            direct = build("direct_" + tag, ver, new)
            check(label + " the input validates with 0 errors", not validate.validate(base).errors)
            r = run_job(base, 'printer = "%s"\n' % new, "pc_" + tag)
            OUT[tag] = r.path
            o, i, d = cfg_of(r.path), cfg_of(base), cfg_of(direct)
            check(label + " post-checks pass", all(c[1] for c in r.checks), [c for c in r.checks if not c[1]])
            check(label + " the variant post-checks ran", sum("extruder variants" in c[0] for c in r.checks) == 3, [c[0] for c in r.checks if "variant" in c[0]])
            check(label + " default mode: 0 errors", not validate.validate(r.path).errors, [str(f)[:120] for f in validate.validate(r.path).errors])
            diff = sorted(k for k in set(o) | set(d) if o.get(k) != d.get(k))
            # the builder's own default (Rectilinear sparse infill) is a designer change of the input's process; the process is swapped
            # without keep_changes, so the patch drops it (named in the summary) and the new process preset's pattern stands
            check(label + " project settings = the builder's own config for the new printer with the mapped presets, except the dropped infill default",
                  set(diff) <= {"sparse_infill_pattern", "different_settings_to_system", "inherits_group"} and
                  any(lbl == "process" and any(k == "sparse_infill_pattern" for k, _v in kv) for lbl, kv in r.dropped), (diff, r.dropped))
            vp = len(d["printer_extruder_variant"])
            check(label + " %d variant(s) per slot" % vp, o["filament_self_index"] == [str(s + 1) for s in range(2) for _ in range(vp)], o["filament_self_index"])
            check(label + " process and both slots mapped by alias",
                  o["print_settings_id"] == PROC[new] and o["filament_settings_id"] == [f for f, _c in FILS[new]], (o["print_settings_id"], o["filament_settings_id"]))
            check(label + " the summary names the variant change", any("extruder variants:" in c for c in r.changes), r.changes)
            a, b = sha_all(base, skip=(PS,)), sha_all(r.path, skip=(PS,))
            check(label + " every entry but project_settings.config is byte for byte the input's", a == b, [k for k in a if a[k] != b.get(k)])
            sv, sin = validate.validate(r.path, mode="strict"), validate.validate(base, mode="strict")
            newf = {(f.rule, f.where, f.cls) for f in sv.findings if f.level in ("error", "warning")} - {(f.rule, f.where, f.cls) for f in sin.findings if f.level in ("error", "warning")}
            check(label + " strict mode: no finding the input did not have", not newf, sorted(newf)[:3])
        except Exception as e:                                       # noqa: BLE001
            check(label + " runs", False, "%s: %s" % (type(e).__name__, e))

# ---- keep_changes for the process: the output is exactly the builder's own file for the new printer ---------------------------------------------
for ver in ("2.8.2", "2.9.1", "2.9.2"):
    t = ver.replace(".", "")
    for old, new in ((A1, P2S), (P2S, A1)):
        tag = "%s_%s_%s" % (SHORT[old], SHORT[new], t)
        r = run_job(os.path.join(tmp, "in_" + tag + ".3mf"), 'printer = "%s"\nkeep_changes = true\n' % new, "pk_" + tag)
        o, d = cfg_of(r.path), cfg_of(os.path.join(tmp, "direct_" + tag + ".3mf"))
        diff = sorted(k for k in set(o) | set(d) if o.get(k) != d.get(k))
        check("[%s %s -> %s] keep_changes = true: project settings = the builder's own config for the new printer; post-checks pass" % (ver, SHORT[old], SHORT[new]),
              diff == [] and all(c[1] for c in r.checks), (diff, [c for c in r.checks if not c[1]]))

# ---- designer changes: kept by variant name, or dropped --------------------------------------------------------------------------------------
for ver in ("2.8.2", "2.9.1", "2.9.2"):
    t = ver.replace(".", "")
    a1 = build("dz_a1_" + t, ver, A1, f1_settings="nozzle_temperature = 231")
    r = run_job(a1, 'printer = "%s"\n[[filament]]\nslot = 1\nkeep_changes = true\n' % P2S, "keep_a1p2s_" + t)
    o = cfg_of(r.path)
    names = seg(o, "filament_extruder_variant", 0, 3)
    temps = dict(zip(names, seg(o, "nozzle_temperature", 0, 3)))
    check("[%s] A1 -> P2S, keep_changes: the Standard variant keeps 231, the new variants take the preset's 220" % ver,
          temps.get("Direct Drive Standard") == "231" and all(v == "220" for k, v in temps.items() if k != "Direct Drive Standard"), temps)
    check("[%s]   slot 1 still lists nozzle_temperature as changed; post-checks pass; 0 errors" % ver,
          "nozzle_temperature" in o["different_settings_to_system"][1].split(";") and all(c[1] for c in r.checks) and not validate.validate(r.path).errors,
          (o["different_settings_to_system"], [c for c in r.checks if not c[1]]))
    r = run_job(a1, 'printer = "%s"\n' % P2S, "drop_a1p2s_" + t)
    o = cfg_of(r.path)
    check("[%s] A1 -> P2S, default: the change is dropped (all 220) and named in the summary" % ver,
          seg(o, "nozzle_temperature", 0, 3) == ["220"] * 3 and any(lbl == "filament slot 1" and any(k == "nozzle_temperature" for k, _v in kv) for lbl, kv in r.dropped),
          (seg(o, "nozzle_temperature", 0, 3), r.dropped))
    # P2S -> A1: a change on a High Flow variant is dropped with a warning, the Standard one is kept
    p2 = build("dz_p2s_" + t, ver, P2S, f1_settings="nozzle_temperature = 233")
    z = zipfile.ZipFile(p2)
    c = json.loads(z.read(PS))
    nm = seg(c, "filament_extruder_variant", 0, 3)
    hf = nm.index("Direct Drive High Flow")
    c["nozzle_temperature"][hf] = "238"                                      # the designer changed the High Flow value only for slot 1
    p2b = os.path.join(tmp, "dz_p2s_hf_" + t + ".3mf")
    with zipfile.ZipFile(p2b, "w", zipfile.ZIP_DEFLATED) as zo:
        for info in z.infolist():
            data = json.dumps(c, indent=4).encode() if info.filename == PS else z.read(info.filename)
            zo.writestr(info, data)
    r = run_job(p2b, 'printer = "%s"\n[[filament]]\nslot = 1\nkeep_changes = true\n' % A1, "keep_p2sa1_" + t)
    o = cfg_of(r.path)
    check("[%s] P2S -> A1, keep_changes: the Standard value 233 is kept, the High Flow value is dropped with a warning" % ver,
          seg(o, "nozzle_temperature", 0, 1) == ["233"] and any("High Flow" in w and "dropped" in w for w in r.warnings), (seg(o, "nozzle_temperature", 0, 1), r.warnings))
    check("[%s]   post-checks pass; 0 errors" % ver, all(c[1] for c in r.checks) and not validate.validate(r.path).errors, [c for c in r.checks if not c[1]])

# ---- A2L -> H3C (2.9.1 only): the H3C filament presets carry 2 variants for the printer's 3, which patch mode refuses, named --------------------
A2L, H3C = "Bambu Lab A2L 0.4 nozzle", "Bambu Lab H3C 0.4 nozzle"
SHORT.update({A2L: "A2L"})
FILS.update({A2L: [("Bambu PLA Basic @BBL A2L 0.4 nozzle", "#111111"), ("Bambu PLA Basic @BBL A2L 0.4 nozzle", "#FFFFFF")]})
PROC.update({A2L: "0.20mm Standard @BBL A2L"})
m = refused(build("in_a2l", "2.9.1", A2L), 'printer = "%s"\n' % H3C, "ref_h3c")
check("A2L -> H3C refused: the H3C filament presets carry another variant count, the slots are named", m and "carry another number" in m and "slot 1" in m, m)

# ---- refusals ---------------------------------------------------------------------------------------------------------------------------------------
a1 = build("ref_a1", "2.9.1", A1)
z = zipfile.ZipFile(a1)
ms = z.read("Metadata/model_settings.config").decode()
ms2 = ms.replace('<metadata key="name" value="Cube"/>', '<metadata key="name" value="Cube"/>\n    <metadata key="outer_wall_speed" value="200,180"/>', 1)
check("fixture: object metadata edited", ms2 != ms)
bad = os.path.join(tmp, "ref_a1_obj.3mf")
with zipfile.ZipFile(bad, "w", zipfile.ZIP_DEFLATED) as zo:
    for info in z.infolist():
        zo.writestr(info, ms2.encode() if info.filename == "Metadata/model_settings.config" else z.read(info.filename))
m = refused(bad, 'printer = "%s"\n' % P2S, "ref_obj")
check("an object setting with one value per variant is refused, named", m and "outer_wall_speed" in m and "would not fit" in m, m)
m = refused(a1, 'printer = "%s"\n[[filament]]\nslot = 3\npreset = "Bambu PLA Basic @BBL P2S"\ncolor = "#00FF00"\n' % P2S, "ref_add")
check("a variant change combined with adding a slot is refused", m and "cannot be combined" in m, m)

# ---- post-check mutations: the independent variant checks catch a wrong output --------------------------------------------------------------------
seen = {}
orig_verify = patch_verify.verify


def spy(src, out_path, plan, input_strict, ps):
    seen["a"] = (src, plan, input_strict, ps)
    return orig_verify(src, out_path, plan, input_strict, ps)


patch_verify.verify = spy
GOOD = run_job(build("mut_in", "2.9.1", A1), 'printer = "%s"\n' % P2S, "mut_good")
patch_verify.verify = orig_verify
SRC, PLAN, D_STRICT, PSX = seen["a"]
check("the honest variant change passes every post-check", all(c[1] for c in GOOD.checks), [c for c in GOOD.checks if not c[1]])


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
            zo.writestr(i, d)
    return p


for name, fn, label in (
        ("val", lambda c: c["nozzle_temperature"].__setitem__(4, "199"), "a High Flow value of slot 2 that is not the preset's"),
        ("short", lambda c: c["nozzle_temperature"].pop(), "a per-variant list one entry short"),
        ("idx", lambda c: c.__setitem__("filament_self_index", ["1", "2"]), "filament_self_index left in the old layout"),
        ("proc", lambda c: c["outer_wall_speed"].__setitem__(2, "123"), "a process value for a new variant that is not the preset's")):
    ck = patch_verify.verify(SRC, mutate(name, fn), PLAN, D_STRICT, PSX)
    bad = [nm for nm, _ok, _d in ck.failed()]
    check("mutation: %s -> post-check fails" % label, any("extruder variants" in nm or "every difference" in nm for nm in bad), bad)

print("\n%d failed" % len(failed) if failed else "\nall passed")
sys.exit(1 if failed else 0)
