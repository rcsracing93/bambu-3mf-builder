#!/usr/bin/env python3
"""R37 after GLCanvas3D.cpp (2.8.2 :3906, 2.9.1 :4149) and Print::has_wipe_tower(). Synthetic: every file is built from the builder's cube job.
A plate has a prime tower only with more than one used filament (parts, painted areas, support filaments), smooth timelapse or wrapping
detection; spiral mode has none. A stale saved position is moved inside by Studio (warning), a part on a plate that has a tower is an error.
Run: python3 -I tests/test_r37_tower.py"""
import json, os, re, sys, tempfile, zipfile
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import validate, package, wipetower  # noqa: E402
from bambu3mf.job import load_job  # noqa: E402

fails = []
def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)) if (extra and not cond) else ""))
    if not cond: fails.append(name)

tmp = tempfile.mkdtemp()
PS, MS, M3, OBJ = "Metadata/project_settings.config", "Metadata/model_settings.config", "3D/3dmodel.model", "3D/Objects/object_1.model"
ONE = os.path.join(tmp, "one.3mf")            # 4 slots in the project, the only part uses slot 1: no tower
TL = os.path.join(tmp, "tl.3mf")              # same with smooth timelapse: tower
package.build(load_job(os.path.join(H, "jobs", "four_color_282.toml")), ONE)
package.build(load_job(os.path.join(H, "jobs", "four_color_tower_282.toml")), TL)


def edit(src, name, edits):
    z = zipfile.ZipFile(src)
    p = os.path.join(tmp, name)
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zo:
        for i in z.infolist():
            d = z.read(i.filename)
            if i.filename in edits:
                d = edits[i.filename](d)
            zo.writestr(i.filename, d)
    return p


def cfg_edit(**kw):
    def f(d):
        c = json.loads(d)
        for k, v in kw.items():
            c[k] = v
        return json.dumps(c).encode()
    return f


def on_tower(src):
    """Move the cube onto the saved tower position."""
    c = json.loads(zipfile.ZipFile(src).read(PS))
    tx, ty = float(c["wipe_tower_x"][0]), float(c["wipe_tower_y"][0])
    return {M3: lambda d: re.sub(rb'(<item [^>]*transform="1 0 0 0 1 0 0 0 1 )[^ ]+ [^ ]+ ', rb"\g<1>%g %g " % (tx + 5, ty + 5), d)}


def stale(c):
    return cfg_edit(wipe_tower_x=["250"])


def levels(r, rule="R37"):
    return sorted((f.level, f.cls, f.msg.split("(")[0].strip()[:40]) for f in r.findings if f.rule == rule)


# ---- no tower: nothing is reported ----------------------------------------------------------------------------------------
p = edit(ONE, "one_on.3mf", dict(on_tower(ONE), **{PS: cfg_edit(wipe_tower_x=["250"])}))
r = validate.validate(p, mode="strict")
check("one used filament, part on the tower box, saved tower off the bed: no R37 (no tower is printed)", not [f for f in r.findings + r.silent if f.rule == "R37"], levels(r))
used = wipetower.plate_filaments(json.loads(zipfile.ZipFile(ONE).read(PS)), [{"meta": {"extruder": "1"}, "parts": [{"subtype": "normal_part", "meta": {"extruder": "1"}, "paint": None}]}])
check("plate_filaments: one part on slot 1 -> {1}", used == {1}, used)

# ---- tower from smooth timelapse ------------------------------------------------------------------------------------------
p = edit(TL, "tl_on.3mf", on_tower(TL))
r = validate.validate(p)
check("smooth timelapse: part on the tower is an error in default mode (class LOAD)", [(f.level, f.cls) for f in r.findings if f.rule == "R37"] == [("error", "LOAD")], levels(r))
r = validate.validate(p, mode="strict")
check("same in strict mode", any(f.rule == "R37" and f.level == "error" for f in r.findings))
p = edit(TL, "tl_stale.3mf", {PS: stale(0)})
r = validate.validate(p)
check("smooth timelapse, saved tower 250 off the bed: default mode warning, class CHANGE, names the Studio clamp",
      [(f.level, f.cls) for f in r.findings if f.rule == "R37"] == [("warning", "CHANGE")] and "moves a saved tower inside" in r.findings[0].msg and not r.errors, [str(f)[:120] for f in r.findings])
r = validate.validate(p, mode="strict")
check("same in strict mode: error", any(f.rule == "R37" and f.level == "error" for f in r.findings))

# ---- the clamp decides the overlap -----------------------------------------------------------------------------------------
# saved tower far to the right (x 250): Studio moves it to x = xmax - w - 15, so a part there overlaps the MOVED tower, not the saved box
c = json.loads(zipfile.ZipFile(TL).read(PS))
bed = validate.bed_rect(c)
src, raw, box = wipetower.tower_at(dict(c, wipe_tower_x=["250"]), 4, 20.0, bed, 1)
check("tower_at: saved x 250 is clamped inside the bed", src == "file" and raw[0] == 250 and box[2] <= bed[2] - wipetower.WIPE_TOWER_MARGIN + 1e-9, (raw, box))
cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
p = edit(TL, "tl_clamp.3mf", {PS: stale(0), M3: lambda d: re.sub(rb'(<item [^>]*transform="1 0 0 0 1 0 0 0 1 )[^ ]+ [^ ]+ ', rb"\g<1>%g %g " % (cx, cy), d)})
r = validate.validate(p)
check("part on the moved tower: overlap error that names the move; plus the clamp warning",
      sorted((f.level, f.cls) for f in r.findings if f.rule == "R37") == [("error", "LOAD"), ("warning", "CHANGE")] and any("after Studio moves it inside" in f.msg for f in r.errors),
      [str(f)[:140] for f in r.findings])

# ---- default position only without a saved entry -------------------------------------------------------------------------------
src, raw, box = wipetower.tower_at(dict(c, wipe_tower_x=[], wipe_tower_y=[]), 4, 20.0, bed, 1)
check("tower_at: no saved entry -> the Studio default position", src == "default" and raw == box, (src, raw, box))
src, raw, box = wipetower.tower_at(c, 4, 20.0, bed, 1)
check("tower_at: a saved entry is used", src == "file", src)

# ---- has_wipe_tower unit rules ----------------------------------------------------------------------------------------------
base = {"enable_prime_tower": "1", "timelapse_type": "0", "spiral_mode": "0", "enable_wrapping_detection": "0", "wrapping_exclude_area": []}
check("has_wipe_tower: two used filaments", wipetower.has_wipe_tower(base, {1, 2}))
check("has_wipe_tower: one used filament", not wipetower.has_wipe_tower(base, {1}))
check("has_wipe_tower: spiral mode, two filaments", not wipetower.has_wipe_tower(dict(base, spiral_mode="1"), {1, 2}))
check("has_wipe_tower: spiral mode, smooth timelapse", wipetower.has_wipe_tower(dict(base, spiral_mode="1", timelapse_type="1"), {1}))
check("has_wipe_tower: traditional timelapse, one filament", not wipetower.has_wipe_tower(dict(base, timelapse_type="0"), {1}))
check("has_wipe_tower: wrapping detection with an exclude area", wipetower.has_wipe_tower(dict(base, enable_wrapping_detection="1", wrapping_exclude_area=["0x0", "1x0", "1x1"]), {1}))
check("has_wipe_tower: wrapping detection without an exclude area counts as a tower (conservative, GLCanvas3D.cpp 2.8.2 :3906, 2.9.1 :4149)", wipetower.has_wipe_tower(dict(base, enable_wrapping_detection="1", wrapping_exclude_area=["0x0", "1x0"]), {1}))
check("has_wipe_tower: prime tower off", not wipetower.has_wipe_tower(dict(base, enable_prime_tower="0", timelapse_type="1"), {1, 2}))

# ---- used filaments: support, feature filaments, painting ----------------------------------------------------------------
c1 = json.loads(zipfile.ZipFile(ONE).read(PS))
part = {"subtype": "normal_part", "meta": {"extruder": "1"}, "paint": None}
def uf(om=None, pm=None, paint=None, cfg=None, tc=()):
    p0 = dict(part, meta=dict(part["meta"], **(pm or {})), paint=paint)
    return wipetower.plate_filaments(dict(c1, **(cfg or {})), [{"meta": dict({"extruder": "1"}, **(om or {})), "parts": [p0]}], tc)
check("used: support interface filament 2 with support on", uf(om={"enable_support": "1", "support_interface_filament": "2"}) == {1, 2})
check("used: support filament 2 with support off", uf(om={"enable_support": "0", "support_interface_filament": "2"}) == {1})
check("used: project-wide support filament key", uf(cfg={"enable_support": "1", "support_filament": "3"}) == {1, 3})
check("used: wall filament on the part", uf(pm={"wall_filament": "2"}) == {1, 2})
check("used: object wall filament when the part has none", uf(om={"sparse_infill_filament": "4"}) == {1, 4})
check("used: a ToolChange layer", uf(tc=[3]) == {1, 3})
check("used: negative part and support blocker are ignored", wipetower.plate_filaments(c1, [{"meta": {"extruder": "1"}, "parts": [part, {"subtype": "negative_part", "meta": {"extruder": "2"}, "paint": None}, {"subtype": "support_blocker", "meta": {"extruder": "3"}, "paint": None}]}]) == {1})
check("used: painted filament 4 with unpainted area left", uf(paint=(frozenset({4}), True)) == {1, 4})
check("used: whole part painted with 4 -> slot 1 is not used", uf(paint=(frozenset({4}), False)) == {4})
check("paint_states: filament 2 leaf", wipetower.paint_states("8") == ({2}, False))
check("paint_states: filament 4 leaf (state 4 = 3 + 1)", wipetower.paint_states("1C") == ({4}, False))
check("paint_states: state 0 leaf", wipetower.paint_states("0") == (set(), True))
check("paint_states: garbage", wipetower.paint_states("xyz") == (set(), False))

# ---- real file forms: support filament and painting are read from the archive ------------------------------------------------
def objmeta(extra):
    return lambda d: d.decode().replace('<metadata key="name"', extra + '<metadata key="name"', 1).encode()
sup = edit(ONE, "one_sup.3mf", {MS: lambda d: re.sub(r'(<object id="\d+">)', r'\1\n    <metadata key="enable_support" value="1"/>\n    <metadata key="support_interface_filament" value="2"/>', d.decode(), count=1).encode()})
r = validate.validate(sup, mode="strict")
tw = [f for f in r.findings if f.rule == "R37"]
check("file: support interface filament 2 on one object gives a tower (R37 runs: saved tower in the area, no finding on a clean file)", not tw, [str(f)[:100] for f in tw])
sup2 = edit(sup, "one_sup_on.3mf", on_tower(sup))
r = validate.validate(sup2)
check("file: support interface filament 2 + part on the tower -> overlap error", any(f.rule == "R37" and f.level == "error" for f in r.findings), [str(f)[:100] for f in r.findings])
for form, mesh in (("split", OBJ), ):
    paint = edit(ONE, "one_paint.3mf", {mesh: lambda d: d.decode().replace("<triangle ", '<triangle paint_color="1C" ', 1).encode()})
    r = validate.validate(edit(paint, "one_paint_on.3mf", on_tower(paint)))
    check("file (%s form): one triangle painted with filament 4 -> tower -> overlap error" % form, any(f.rule == "R37" and f.level == "error" for f in r.findings), [str(f)[:100] for f in r.findings])
    r = validate.validate(edit(ONE, "one_unpainted_on.3mf", on_tower(ONE)), mode="strict")
    check("file (%s form): the same file without paint: no R37" % form, not [f for f in r.findings if f.rule == "R37"])

# ---- by-object plates: no tower unless exactly one printable instance (GLCanvas3D.cpp 2.8.2 :3887, 2.9.1 :4130) -------------------------------------
BO_TOML = """studio  = "2.8.2"
output  = "bo.3mf"
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"
[settings]
print_sequence = "%s"
[[filament]]
preset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"
color = "#111111"
[[filament]]
preset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"
color = "#FFFFFF"
[[object]]
name = "A"
position = [60, 60]
[[object.part]]
mesh = "%s"
filament = 1
[[object]]
name = "B"
position = [200, 50]
[[object.part]]
mesh = "%s"
filament = 2
"""
CUBE = os.path.join(H, "data", "cube20.stl")


def two_cubes(seq, name):
    t = os.path.join(tmp, name + ".toml")
    open(t, "w").write(BO_TOML % (seq, CUBE, CUBE))
    j = load_job(t)
    out = os.path.join(tmp, name + ".3mf")
    package.build(j, out)
    return out


def tower_on_a(src, name, extra=None):
    ed = {PS: cfg_edit(wipe_tower_x=["45"], wipe_tower_y=["55"])}
    ed.update(extra or {})
    return edit(src, name, ed)


def plate_seq(v):
    return lambda d: d.decode().replace('<metadata key="plater_id" value="1"/>', '<metadata key="plater_id" value="1"/>\n    <metadata key="print_sequence" value="%s"/>' % v, 1).encode()


BO = tower_on_a(two_cubes("by object", "bo"), "bo_on.3mf")
r = validate.validate(BO)
check("by object, two printable instances, tower saved over cube A: no R37 (Studio makes no tower)", not [f for f in r.findings + r.silent if f.rule == "R37"], [str(f)[:100] for f in r.findings])
BL = tower_on_a(two_cubes("by layer", "bl"), "bl_on.3mf")
r = validate.validate(BL)
check("by layer, same layout: overlap error in default mode", any(f.rule == "R37" and f.level == "error" and "overlaps the prime tower" in f.msg for f in r.findings), [str(f)[:100] for f in r.findings])
r = validate.validate(tower_on_a(two_cubes("by layer", "bl2"), "bl2_on.3mf", {MS: plate_seq("by object")}))
check("config by layer, plate metadata by object: no R37 (the plate value wins)", not [f for f in r.findings if f.rule == "R37"], [str(f)[:100] for f in r.findings])
r = validate.validate(tower_on_a(two_cubes("by object", "bo2"), "bo2_on.3mf", {MS: plate_seq("by layer")}))
check("config by object, plate metadata by layer: overlap error (the plate value wins)", any(f.rule == "R37" and f.level == "error" for f in r.findings), [str(f)[:100] for f in r.findings])
r = validate.validate(tower_on_a(two_cubes("by object", "bo4"), "bo4_on.3mf", {M3: lambda d: re.sub(rb'(<item [^>]*objectid="4"[^>]*printable=)"1"', rb'\1"0"', d)}))
check("by object, the second instance is not printable: one printable instance, so the tower is checked (overlap error)", any(f.rule == "R37" and f.level == "error" for f in r.findings), [str(f)[:100] for f in r.findings])

print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
