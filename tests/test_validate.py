#!/usr/bin/env python3
"""Validator tests: R37 wipe tower, untrusted-input guards, clean files pass.
Run: python3 -I tests/test_validate.py"""
import io, os, re, shutil, sys, tempfile, zipfile
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import validate, package
from bambu3mf.job import load_job

fails = []
def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)) if (extra and not cond) else ""))
    if not cond: fails.append(name)

tmp = tempfile.mkdtemp()
try:
    two = os.path.join(tmp, "two.3mf"); package.build(load_job(os.path.join(H, "jobs", "four_color_282.toml")), two)

    def tamper(src, fn, name):
        zin = zipfile.ZipFile(src); p = os.path.join(tmp, name)
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zo:
            for i in zin.infolist():
                d = fn(i.filename, zin.read(i.filename))
                if d is not None: zo.writestr(i.filename, d)
        return p

    twot = os.path.join(tmp, "twot.3mf"); package.build(load_job(os.path.join(H, "jobs", "four_color_tower_282.toml")), twot)
    check("built file passes", validate.validate(two, mode="strict", privacy=True).ok, [str(x) for x in validate.validate(two, mode="strict", privacy=True).errors])
    # R37: move the cube onto the tower (tower sits near x 165..206, y 199..240)
    p = tamper(twot, lambda n, d: re.sub(rb'(<item [^>]*transform="1 0 0 0 1 0 0 0 1 )[^ ]+ [^ ]+ ', rb'\g<1>180 220 ', d) if n == "3D/3dmodel.model" else d, "over.3mf")
    r = validate.validate(p, mode="strict", privacy=True)
    check("R37 part on tower is caught", "R37" in r.rules_failed(), [str(x) for x in r.findings])
    # R37: tower pushed off the bed
    def tower_off(n, d):
        if n != "Metadata/project_settings.config": return d
        return re.sub(rb'("wipe_tower_x": \[\s*")[^"]+', rb'\g<1>250', d)
    p = tamper(twot, tower_off, "off.3mf")
    check("R37 tower off the bed is caught", "R37" in validate.validate(p, mode="strict", privacy=True).rules_failed())
    # R37 does not fire for one filament (no tower)
    one = os.path.join(tmp, "one.3mf"); package.build(load_job(os.path.join(H, "jobs", "tower.toml")), one)
    check("single filament: no R37", "R37" not in [f.rule for f in validate.validate(one, mode="strict", privacy=True).findings])
    # R37 with a big part that cannot avoid the tower: builder must refuse
    big = os.path.join(tmp, "big.toml")
    src = open(os.path.join(H, "jobs", "four_color_282.toml")).read().replace('mesh = "../data/cube20.stl"', 'mesh = "%s"' % os.path.join(H, "data", "cube20.stl"))
    src = src.replace('name = "Cube"', 'name = "Cube"\nposition = [185.0, 225.0]')
    open(big, "w").write(src)
    try:
        package.build(load_job(big), os.path.join(tmp, "big.3mf")); ok = False
    except Exception as e:
        ok = "R37" in str(getattr(e, "problems", e))
    check("builder refuses a part on the tower (R37)", ok)

    # guards
    check("not a zip", "G1" in validate.validate(io.BytesIO(b"hello"), mode="strict", privacy=True).rules_failed())
    p = tamper(two, lambda n, d: b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><config>&a;</config>' if n == "Metadata/model_settings.config" else d, "xxe.3mf")
    check("DOCTYPE refused (G6)", "G6" in validate.validate(p, mode="strict", privacy=True).rules_failed())
    bomb = os.path.join(tmp, "bomb.3mf")
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zo:
        zo.writestr("Metadata/project_settings.config", b"0" * (200 * 1024 ** 2))
    r = validate.validate(bomb, mode="strict", privacy=True)
    check("compression ratio / size guard (G4/G5)", {"G4", "G5"} & set(r.rules_failed()), r.rules_failed())
    trav = os.path.join(tmp, "trav.3mf")
    with zipfile.ZipFile(trav, "w") as zo:
        zo.writestr("../evil.txt", b"x"); zo.writestr("/abs.txt", b"x"); zo.writestr("a\\b.txt", b"x")
    check("path tricks refused (G3)", "G3" in validate.validate(trav, mode="strict", privacy=True).rules_failed())
    # Studio's real save: only a warning for the stale tower position
    real = validate.validate(os.path.join(H, "data", "four-color-autocalc-282.3mf"), mode="strict", privacy=True)
    check("real Studio save: no errors", real.ok, [str(x) for x in real.errors])

    # ---- teeth: project_settings rules (each mutation must trip exactly its rule) ----
    import json
    src = os.path.join(H, "data", "four-color-autocalc-282.3mf")
    def mut(fn, name):
        def f(n, d):
            if n != "Metadata/project_settings.config": return d
            c = json.loads(d); fn(c); return (json.dumps(c, indent=4) + "\n").encode()
        return tamper(src, f, name + ".3mf")
    teeth = {
        "R5": lambda c: c["filament_diameter"].pop(),
        "R6": lambda c: c["nozzle_temperature"].pop(),
        "R35": lambda c: c.__setitem__("nozzle_temperature", ["220"]),
        "R16": lambda c: c.__setitem__("sparse_infill_pattern", "blob"),
        "R3": lambda c: c.__setitem__("wall_loops", "5"),
        "R9": lambda c: c.__setitem__("printer_model", "Foo"),
        "R17": lambda c: c.__setitem__("wipe_tower_x", ["1", "2"]),
        "R14": lambda c: c["flush_volumes_matrix"].pop(),
        "R13": lambda c: c.pop("flush_multiplier_fast"),
        "R12": lambda c: c.__setitem__("filament_ids", ["GFA00"]),
        "R7": lambda c: c["filament_self_index"].reverse(),
        "R11": lambda c: c.__setitem__("inherits_group", [""] * 5),
        "R4": lambda c: c["filament_colour"].__setitem__(0, "red"),
        "R15": lambda c: c.__setitem__("name", "x"),
    }
    for rule, fn in teeth.items():
        got = validate.validate(mut(fn, "t_" + rule), mode="strict", privacy=True).rules_failed()
        check("teeth %s caught" % rule, rule in got, got)
    check("builder output has no findings at all", validate.validate(two, mode="strict", privacy=True).findings == [], [str(x) for x in validate.validate(two, mode="strict", privacy=True).findings])

    # ---- teeth: package rules. Built files are the base; each mutation must trip its rule ----
    base = os.path.join(tmp, "base.3mf"); package.build(load_job(os.path.join(H, "jobs", "four_color_282.toml")), base)
    towerf = os.path.join(tmp, "tower.3mf"); package.build(load_job(os.path.join(H, "jobs", "tower.toml")), towerf)
    check("base files have no findings", validate.validate(base, mode="strict", privacy=True).findings == [] and validate.validate(towerf, mode="strict", privacy=True).findings == [],
          [str(x) for x in validate.validate(base, mode="strict", privacy=True).findings + validate.validate(towerf, mode="strict", privacy=True).findings])
    def ed(entry, old, new, count=1, regex=False):
        def f(n, d):
            if n != entry: return d
            if regex: return re.sub(old, new, d, count=count)
            assert old in d, (entry, old)
            return d.replace(old, new, count)
        return f
    pk = {
        "R1": (base, ed("3D/3dmodel.model", b"BambuStudio-02", b"Foo-02")),
        "R2": (base, ed("3D/3dmodel.model", b'3mfVersion">1<', b'3mfVersion">2<')),
        "R18": (base, ed("[Content_Types].xml", b'Extension="png"', b'Extension="jpg"')),
        "R19": (base, ed("_rels/.rels", b'Target="/3D/3dmodel.model"', b'Target="/3D/missing.model"')),
        "R20": (base, ed("3D/_rels/3dmodel.model.rels", b"object_1.model", b"object_9.model")),
        "R21": (base, ed("3D/3dmodel.model", b'objectid="1" p:UUID', b'objectid="7" p:UUID')),
        "R23": (base, ed("3D/3dmodel.model", rb'(<item [^>]*transform="1 0 0 0 1 0 0 0 1 )[^ ]+ [^ ]+ ', rb"\g<1>900 900 ", regex=True)),
        "R24": (base, ed("3D/Objects/object_1.model", b'v2="1" v3="2"', b'v2="2" v3="1"')),
        "R25": (base, ed("Metadata/model_settings.config", b"</config>", b"</conf>")),
        "R26": (base, ed("Metadata/model_settings.config", b'<part id="1"', b'<part id="5"')),
        "R27": (base, ed("Metadata/model_settings.config", rb'(key="extruder" value=")1("/>\s*<mesh_stat)', rb"\g<1>9\g<2>", regex=True)),
        "R28": (base, ed("Metadata/model_settings.config", b'<metadata key="extruder" value="1"/>', b'<metadata key="extruder" value="1"/>\n    <metadata key="wall_loops" value="abc"/>')),
        "R29": (base, ed("Metadata/model_settings.config", b'subtype="normal_part"', b'subtype="modifier_part"')),
        "R30": (base, ed("Metadata/model_settings.config", b'<metadata key="instance_id" value="0"/>', b'<metadata key="instance_id" value="3"/>')),
        "R31": (base, ed("Metadata/model_settings.config", b'<metadata key="name" value="Cube"/>', b'<metadata key="name" value="a/b"/>')),
        "R32": (towerf, ed("Metadata/custom_gcode_per_layer.xml", b'top_z="4"', b'top_z="4.05"')),
        "R32 above": (towerf, ed("Metadata/custom_gcode_per_layer.xml", b'top_z="16"', b'top_z="40"')),
    }
    for rule, (f0, fn) in pk.items():
        got = validate.validate(tamper(f0, fn, "p_" + rule.replace(" ", "_") + ".3mf"), mode="strict", privacy=True).rules_failed()
        check("teeth %s caught" % rule, rule.split()[0] in got, got)
    # R34: archive order
    zin = zipfile.ZipFile(base); order = zin.namelist(); order.remove("Metadata/filament_sequence.json"); order.insert(order.index("Metadata/model_settings.config"), "Metadata/filament_sequence.json")
    p34 = os.path.join(tmp, "p34.3mf")
    with zipfile.ZipFile(p34, "w") as zo:
        for nme in order: zo.writestr(nme, zin.read(nme))
    check("teeth R34 caught", "R34" in validate.validate(p34, mode="strict", privacy=True).rules_failed())
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
