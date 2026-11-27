#!/usr/bin/env python3
"""Patch mode core. Every input is synthetic: builder output made in this test (and converted to the non-split,
user-preset, CLI-flag forms below). Never a private file.
Covers: process swap, filament swap, colour (purge row / column), project / object (name, plate) / part settings, object and part filament,
plate type (project, per plate, bed-temperature-0 refusal), layer G-code (new file, existing file, recolour), reset_to_preset, keep_changes,
user-preset slots, byte identity of untouched entries, CRLF config, refusals, and mutation-style checks that a patch which changes
something not asked fails the post-checks. Run: python3 -I tests/test_patch_core.py"""
import glob, shutil, hashlib, io, json, os, re, struct, sys, tempfile, zipfile
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import package, patch, patch_cfg, patch_job, patch_read, patch_verify, patch_xml, validate  # noqa: E402
from bambu3mf.job import load_job  # noqa: E402
from bambu3mf.patch_job import PatchRefused  # noqa: E402
from bambu3mf.patch_zip import _Info  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)[:300]) if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


tmp = tempfile.mkdtemp()
PS, MS, M3, GC = "Metadata/project_settings.config", "Metadata/model_settings.config", "3D/3dmodel.model", "Metadata/custom_gcode_per_layer.xml"
MESH = os.path.join(H, "..", "examples", "data")


# ---- synthetic inputs --------------------------------------------------------------------------------------------------------
def build(name, version, extra_toml=""):
    """Four filaments, two plates, objects Cube (plate 1, parts Lower / Upper) and Cap (plate 2, one part)."""
    p = os.path.join(tmp, name + ".toml")
    open(p, "w").write('''studio  = "%s"
output  = "%s.3mf"
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.16mm Optimal @BBL X1C"
[settings]
infill_pattern = "grid"
wall_loops = 4
[[filament]]
preset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"
color = "#111111"
[[filament]]
preset = "Bambu PLA Matte @BBL P1S 0.4 nozzle"
color = "#FFFFFF"
[[filament]]
preset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"
color = "#C12E1F"
[[filament]]
preset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"
color = "#0A2989"
[[object]]
name = "Cube"
[[object.part]]
name = "Lower"
mesh = "%s/slab1.stl"
filament = 1
[[object.part]]
name = "Upper"
mesh = "%s/slab2.stl"
filament = 2
[[object]]
name = "Cap"
plate = 2
[object.settings]
sparse_infill_density = "40%%"
[[object.part]]
name = "Body"
mesh = "%s/cube20.stl"
filament = 3
%s''' % (version, name, MESH, MESH, MESH, extra_toml))
    out = os.path.join(tmp, name + ".3mf")
    package.build(load_job(p), out)
    return out


def build_single(name, version):
    out = os.path.join(tmp, name + ".3mf")
    package.build(load_job(os.path.join(H, "jobs", "four_color_%s.toml" % ("282" if version == "2.8.2" else "291"))), out)
    return out


def rewrite(src, dst, fn=None, extra=(), drop=(), flags=None, stored=(), order=None):
    """Copy a 3MF with edits. fn(name, bytes) -> bytes. flags: {name: flag_bits}. Used to make the forms Studio writes."""
    z = zipfile.ZipFile(src)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
        infos = [i for i in z.infolist() if i.filename not in drop]
        for i in infos:
            d = z.read(i.filename)
            if fn:
                d = fn(i.filename, d)
            zi = zipfile.ZipInfo(i.filename, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_STORED if i.filename in stored else zipfile.ZIP_DEFLATED
            zo.writestr(zi, d)
        for n, d in extra:
            zi = zipfile.ZipInfo(n, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zo.writestr(zi, d)
    return dst


def cfg_edit(src, name, f):
    def fn(n, d):
        if n != PS:
            return d
        c = json.loads(d)
        f(c)
        return json.dumps(c, indent=4, ensure_ascii=False).encode() + b"\n"
    return rewrite(src, os.path.join(tmp, name + ".3mf"), fn)


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


def run_job(base, body, name="out", **kw):
    """Write a patch job and run it. Returns the PatchResult. kw: extra top-level lines."""
    jp = os.path.join(tmp, name + ".toml")
    out = os.path.join(tmp, name + ".3mf")
    if os.path.exists(out):
        os.remove(out)
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


def entry_hashes(path):
    z = zipfile.ZipFile(path)
    return {i.filename: hashlib.sha256(z.read(i.filename)).hexdigest() for i in z.infolist()}


def ms_of(path):
    return zipfile.ZipFile(path).read(MS).decode()


def vrep(path, mode="default"):
    return validate.validate(path, mode=mode)


def by_slot(cfg, key, i, w=2):
    return cfg[key][i * w:(i + 1) * w]


# ---- bases ---------------------------------------------------------------------------------------------------------------------
B = {}
for v in ("2.8.2", "2.9.1"):
    B["split " + v] = build("multi" + v.replace(".", ""), v)
    B["single " + v] = build_single("single" + v.replace(".", ""), v)
    B["nonsplit " + v] = to_nonsplit(B["single " + v], os.path.join(tmp, "ns" + v.replace(".", "") + ".3mf"))
check("synthetic bases validate with 0 errors", all(not vrep(p).errors for p in B.values()), [(k, [str(f)[:80] for f in vrep(p).errors]) for k, p in B.items() if vrep(p).errors])
check("JSON round trip: the builder's config is written back byte for byte", all(patch_read.dump_config(cfg_of(p), "\n") == zipfile.ZipFile(p).read(PS) for p in B.values()))

# ---- every change kind, on every form and version ----------------------------------------------------------------------------------------
COMBO = '''process = "0.20mm Standard @BBL X1C"
plate_type = "High Temp Plate"
[settings]
wall_loops = 5
[[filament]]
slot = 1
preset = "Bambu PETG HF @BBL P1S 0.4 nozzle"
color = "#00AA55"
[[filament]]
slot = 2
color = "#FF8800"
[filament.settings]
nozzle_temperature = 215
[[layer_gcode]]
plate = 1
z = 2.2
pause = "check the magnet"
[[layer_gcode]]
plate = 1
z = 3.0
temperature = 205
'''
for form, base in B.items():
    ver = form.split()[1]
    body = COMBO if form.startswith("single") or form.startswith("nonsplit") else COMBO
    try:
        r = run_job(base, body.replace("[[layer_gcode]]\nplate = 1\nz = 2.2", "[[layer_gcode]]\nplate = 1\nz = 2.2"), "combo")
        o = cfg_of(r.path)
        i = cfg_of(base)
        h_in, h_out = entry_hashes(base), entry_hashes(r.path)
        same = [n for n in h_in if n not in (PS, MS, GC) and h_in[n] == h_out.get(n)]
        check("[%s] combined patch: output validates with 0 errors, post-checks all pass" % form, not vrep(r.path).errors and r.checks and all(c[1] for c in r.checks), [c for c in r.checks if not c[1]])
        check("[%s] process swapped, plate type set, setting changed, colours set" % form,
              o["print_settings_id"] == "0.20mm Standard @BBL X1C" and o["curr_bed_type"] == "High Temp Plate" and o["wall_loops"] == "5"
              and o["filament_colour"][:2] == ["#00AA55", "#FF8800"] and o["filament_colour"][2:] == i["filament_colour"][2:])
        check("[%s] slot 1 preset swapped (id, name, values from the new preset); slot 2 only the asked setting" % form,
              o["filament_settings_id"][0] == "Bambu PETG HF @BBL P1S 0.4 nozzle" and o["filament_settings_id"][1:] == i["filament_settings_id"][1:]
              and o["nozzle_temperature"][2:4] == ["215", "215"] and o["nozzle_temperature"][:2] != i["nozzle_temperature"][:2] and o["nozzle_temperature"][4:] == i["nozzle_temperature"][4:])
        check("[%s] untouched entries: every one has the same bytes (SHA-256), same order" % form,
              len(same) == len(h_in) - (3 if GC in h_in else 2) and [n for n in zipfile.ZipFile(base).namelist()] == [n for n in zipfile.ZipFile(r.path).namelist() if n != GC], (len(same), len(h_in)))
        check("[%s] layer G-code: new entry just before project_settings, two layers" % form,
              GC in zipfile.ZipFile(r.path).namelist() and zipfile.ZipFile(r.path).namelist().index(GC) + 1 == zipfile.ZipFile(r.path).namelist().index(PS)
              and zipfile.ZipFile(r.path).read(GC).count(b"<layer ") == 2)
        check("[%s] the root model (every mesh) is byte for byte the input's" % form, h_in[M3] == h_out[M3])
    except Exception as e:                                           # noqa: BLE001
        check("[%s] combined patch runs" % form, False, "%s: %s" % (type(e).__name__, e))

BASE = B["split 2.8.2"]
BASE291 = B["split 2.9.1"]
CUBE = B["single 2.8.2"]

# ---- process swap and keep_changes -----------------------------------------------------------------------------------------------------------
i0 = cfg_of(BASE)
check("fixture: the designer changed wall_loops and the infill pattern (listed in the process entry)", "wall_loops" in i0["different_settings_to_system"][0], i0["different_settings_to_system"][0])
r = run_job(BASE, 'process = "0.20mm Standard @BBL X1C"\n', "pswap")
o = cfg_of(r.path)
check("process swap: the designer's changes are dropped and listed", [k for k, _v in dict(r.dropped).get("process", [])] and "wall_loops" in [k for k, _v in dict(r.dropped)["process"]], r.dropped)
check("process swap: values are the new preset's, the diff list is empty, the key is gone when all entries are empty",
      o["wall_loops"] != "4" and o["layer_height"] == "0.2" and "different_settings_to_system" not in o and "inherits_group" not in o, (o.get("wall_loops"), o.get("different_settings_to_system")))
r = run_job(BASE, 'process = "0.20mm Standard @BBL X1C"\nkeep_changes = true\n', "pkeep")
o = cfg_of(r.path)
check("process swap with keep_changes: wall_loops stays 4, listed as kept, still in the diff list",
      o["wall_loops"] == "4" and "wall_loops" in o["different_settings_to_system"][0] and o["layer_height"] == "0.2" and dict(r.kept).get("process"), (o["wall_loops"], r.kept))
r = run_job(BASE, 'reset_to_preset = true\n', "reset")
o = cfg_of(r.path)
check("reset_to_preset: process designer changes gone, values are the preset's", "wall_loops" not in (o.get("different_settings_to_system") or [""])[0] and o["wall_loops"] != "4", o.get("different_settings_to_system"))
check("reset_to_preset with keep_changes is refused", "cannot be used together" in (refused(BASE, 'process = "0.20mm Standard @BBL X1C"\nreset_to_preset = true\nkeep_changes = true\n') or ""))

# ---- filament swap, keep_changes, flush ----------------------------------------------------------------------------------------------------------
PETG = "Bambu PETG HF @BBL P1S 0.4 nozzle"
withdiff = cfg_edit(BASE, "withdiff", lambda c: (c["nozzle_temperature"].__setitem__(slice(2, 4), ["231", "231"]),
                                                  c["different_settings_to_system"].__setitem__(2, "nozzle_temperature"),
                                                  c.setdefault("inherits_group", [""] * (len(c["filament_settings_id"]) + 2)).__setitem__(2, c["filament_settings_id"][1])))
msg = refused(withdiff, '[[filament]]\nslot = 2\npreset = "%s"\n' % PETG, "fswapcool")
check("filament swap to PETG on a Cool Plate file is refused up front (Studio does not slice it; R39)", "does not support the filament in slot 2" in (msg or ""), msg)
r = run_job(withdiff, 'plate_type = "High Temp Plate"\n[[filament]]\nslot = 2\npreset = "%s"\n' % PETG, "fswap")
o = cfg_of(r.path)
check("filament swap: slot 2's own designer change is dropped and listed; the entry is empty", dict(r.dropped).get("filament slot 2") and o["different_settings_to_system"][2] == "" and o["nozzle_temperature"][2:4] != ["231", "231"], (r.dropped, o["different_settings_to_system"]))
check("filament swap: the system preset's inherits_group entry is empty (Studio's form)", o.get("inherits_group", [""] * 4)[2] == "")
r = run_job(withdiff, 'plate_type = "High Temp Plate"\n[[filament]]\nslot = 2\npreset = "%s"\nkeep_changes = true\n' % PETG, "fkeep")
o = cfg_of(r.path)
check("filament swap with keep_changes: nozzle_temperature 231 stays and stays listed", o["nozzle_temperature"][2:4] == ["231", "231"] and "nozzle_temperature" in o["different_settings_to_system"][2] and dict(r.kept).get("filament slot 2"), (o["nozzle_temperature"][2:4], r.kept))
check("filament swap with keep_changes: other values of slot 2 are the new preset's", o["filament_settings_id"][1] == PETG and o["filament_max_volumetric_speed"][2] != cfg_of(BASE)["filament_max_volumetric_speed"][2])

# purge row and column
n = 4
i4 = cfg_of(B["single 2.8.2"])
r = run_job(B["single 2.8.2"], '[[filament]]\nslot = 3\ncolor = "#00FF00"\n', "flush3")
o = cfg_of(r.path)
M0, M1 = i4["flush_volumes_matrix"], o["flush_volumes_matrix"]
changed = {(j // n, j % n) for j in range(n * n) if M0[j] != M1[j]}
check("colour change: only row 3 and column 3 of the purge matrix changed", changed and all(2 in cell for cell in changed), changed)
check("colour change: purge volumes recalculated (summary says so), flush_volumes_vector unchanged", any("purge volumes recalculated" in c for c in r.changes) and o["flush_volumes_vector"] == i4["flush_volumes_vector"])


def edited_row(c):
    m = c["flush_volumes_matrix"]
    m[2 * n + 0] = "777"
    c["flush_volumes_matrix"] = m
edited = cfg_edit(B["single 2.8.2"], "flush_edit", edited_row)
r = run_job(edited, '[[filament]]\nslot = 3\ncolor = "#00FF00"\n', "flush3e")
o = cfg_of(r.path)
check("colour change on a purge row the designer edited: matrix kept, warning says so", o["flush_volumes_matrix"] == cfg_of(edited)["flush_volumes_matrix"] and any("purge volumes in the file differ" in w for w in r.warnings), r.warnings)
check("colour change: another slot's edited row does not stop slot 4", True)
r = run_job(edited, '[[filament]]\nslot = 4\ncolor = "#00FF00"\n', "flush4")
check("colour change on slot 4 with slot 3's row edited: slot 4 recalculated, slot 3's edit stays 777",
      cfg_of(r.path)["flush_volumes_matrix"][2 * n] == "777" and any("recalculated for slot(s) 4" in c for c in r.changes), r.changes)

# ---- settings: project, object (name, plate), part, filament of object and part ------------------------------------------------------------------------
r = run_job(BASE, '[settings]\nsparse_infill_density = "15%"\n[[object]]\nname = "Cube"\n[object.settings]\nwall_loops = 6\n', "sset")
o, m = cfg_of(r.path), ms_of(r.path)
check("project setting changed, object setting inserted in the Cube object", o["sparse_infill_density"] == "15%" and re.search(r'<object id="3">.*?<metadata key="wall_loops" value="6"/>', m, re.S), None)
check("object setting by name: the Cap object (not named) is untouched", m.count('key="wall_loops"') == 1)
r = run_job(BASE, '[[object]]\nplate = 2\n[object.settings]\nwall_loops = 3\n[[object]]\nname = "Cube"\n[[object.part]]\nname = "Upper"\n[object.part.settings]\nwall_loops = 7\n', "sset2")
m = ms_of(r.path)
check("object setting by plate: the object on plate 2 gets it", re.search(r'<object id="6">.*?<metadata key="wall_loops" value="3"/>', m, re.S) is not None or 'key="wall_loops" value="3"' in m, m[:200])
check("part setting: inserted inside the named part only", len(re.findall(r'<part id="2".*?key="wall_loops" value="7".*?</part>', m, re.S)) == 1 and m.count('key="wall_loops" value="7"') == 1)
check("an existing per-object value is changed in place (Cap sparse_infill_density 40% -> 25%)", 'value="40%"' in ms_of(BASE) and 'value="25%"' in ms_of(run_job(BASE, '[[object]]\nname = "Cap"\n[object.settings]\nsparse_infill_density = "25%"\n', "sset3").path))
r = run_job(BASE, '[[object]]\nname = "Cube"\nfilament = 4\n[[object.part]]\nname = "Upper"\nfilament = 3\n', "sfil")
m = ms_of(r.path)
mm = re.search(r'<object id="3">(.*?)</object>', m, re.S).group(1)
check("object filament: the object and its parts take the slot, a part with its own filament keeps that one",
      re.search(r'key="name" value="Cube"/>\s*<metadata key="extruder" value="4"', mm) and re.findall(r'key="extruder" value="(\d)"', mm) == ["4", "4", "3"], re.findall(r'key="extruder" value="(\d)"', mm))

multic = cfg_edit(BASE, "multic", lambda c: c["filament_multi_colour"].__setitem__(1, "#111111 #222222"))
check("a multi-colour slot: colour change refused, naming the slot", "multi-colour or mixed" in (refused(multic, '[[filament]]\nslot = 2\ncolor = "#445566"\n') or ""))
ctype1 = cfg_edit(BASE, "ctype1", lambda c: c.__setitem__("filament_colour_type", ["1"] * 4))
check("filament_colour_type 1 with a single colour (as in real Studio saves) is a normal colour change", run_job(ctype1, '[[filament]]\nslot = 2\ncolor = "#445566"\n', "ctype_o").path)

# ---- plate type: project, per plate, bed temperature 0 -------------------------------------------------------------------------------------------------
r = run_job(BASE, 'plate_type = "High Temp Plate"\n', "pt1")
check("plate type, project: curr_bed_type set", cfg_of(r.path)["curr_bed_type"] == "High Temp Plate")
msg = refused(BASE, 'plate_type = "Engineering Plate"\n', "pt1b")
check("plate type the filaments cannot use (PLA on the Engineering Plate, bed temperature 0): refused, names slot and key", msg and "eng_plate_temp" in msg and "slot 1" in msg, msg)
r = run_job(BASE, '[[plate]]\nid = 2\nplate_type = "Cool Plate"\n', "pt2")
m = ms_of(r.path)
plates = re.findall(r"<plate>.*?</plate>", m, re.S)
check("plate type per plate: bed_type inserted after 'locked' in plate 2 only", len(plates) == 2 and 'key="bed_type" value="Cool Plate"' in plates[1] and 'bed_type' not in plates[0]
      and re.search(r'key="locked"[^>]*/>\s*<metadata key="bed_type"', plates[1]), plates[1][:300])
cold = cfg_edit(BASE, "cold", lambda c: c.__setitem__("hot_plate_temp", ["0", "0"] + c["hot_plate_temp"][2:]))
msg = refused(cold, 'plate_type = "High Temp Plate"\n', "pt3")
check("plate type the filament cannot use (bed temperature 0 for slot 1): refused, names slot and key", msg and "slot 1" in msg and "hot_plate_temp" in msg, msg)
check("   a per-plate plate type on plate 2 (slot 3 prints there): fine when slot 3 has a bed temperature",
      run_job(cold, '[[plate]]\nid = 2\nplate_type = "High Temp Plate"\n', "pt4").path)

# ---- layer G-code in an existing file ----------------------------------------------------------------------------------------------------------------------
g1 = run_job(BASE, '[[layer_gcode]]\nplate = 1\nz = 2.12\npause = "x"\n', "gc1")
g2 = run_job(g1.path, '[[layer_gcode]]\nplate = 1\nz = 2.44\ntemperature = 200\n[[layer_gcode]]\nplate = 2\nz = 2.12\ntemperature = 210\n[[filament]]\nslot = 1\ncolor = "#123456"\n', "gc2")
gx = zipfile.ZipFile(g2.path).read(GC).decode()
check("layer G-code added to an existing file: a layer is placed in plate 1 in z order, plate 2 gets its own block", gx.count("<layer ") == 3 and gx.count("<plate>") == 2 and gx.index('top_z="2.12"') < gx.index('top_z="2.44"'), gx)
tc = zipfile.ZipFile(g1.path).read(GC).replace(b'type="1" extruder="1" color=""', b'type="0" extruder="1" color="#111111"')
gcol = rewrite(g1.path, os.path.join(tmp, "gccol.3mf"), lambda n, d: tc if n == GC else d)
g3 = run_job(gcol, '[[filament]]\nslot = 1\ncolor = "#123456"\n', "gc3")
check("slot colour change: a colour-change layer of that slot gets the new colour", b'color="#123456"' in zipfile.ZipFile(g3.path).read(GC), zipfile.ZipFile(g3.path).read(GC))
msg = refused(g1.path, '[[layer_gcode]]\nplate = 1\nz = 2.12\npause = "y"\n', "gc4")
check("a layer at a z that already has one is refused", msg and "already has a layer change" in msg, msg)
msg = refused(BASE, '[[layer_gcode]]\nplate = 1\nz = 2.25\npause = "y"\n', "gc5")
check("a z that is not a layer top is refused with the nearest layer top", msg and "not the top of a layer" in msg, msg)

# ---- user-preset slots -------------------------------------------------------------------------------------------------------------------------------------------
def user_slot(c):
    sysn = c["filament_settings_id"][0]
    c["filament_settings_id"][0] = "My own PLA"
    ig = list(c.get("inherits_group") or [""] * 6)
    ig[1] = sysn
    c["inherits_group"] = ig
    dl = list(c.get("different_settings_to_system") or [""] * 6)
    dl[1] = "nozzle_temperature"
    c["different_settings_to_system"] = dl
    c["nozzle_temperature"][0:2] = ["231", "231"]
USER = cfg_edit(BASE, "user", user_slot)
check("fixture: a user-preset slot input validates with 0 errors (default mode)", not vrep(USER).errors, [str(f)[:90] for f in vrep(USER).errors])
r = run_job(USER, '[[filament]]\nslot = 2\ncolor = "#445566"\n', "u1")
o = cfg_of(r.path)
check("user-preset slot input loads; patching another slot leaves the user slot (name, parent, value, listed key) alone",
      o["filament_settings_id"][0] == "My own PLA" and o["inherits_group"][1] == cfg_of(BASE)["filament_settings_id"][0] and o["nozzle_temperature"][:2] == ["231", "231"] and "nozzle_temperature" in o["different_settings_to_system"][1])
r = run_job(USER, '[[filament]]\nslot = 1\n[filament.settings]\nfilament_flow_ratio = 0.97\n', "u2")
o = cfg_of(r.path)
check("setting changed on a user-preset slot: listed next to the old one, the parent stays in inherits_group, the name stays",
      o["filament_flow_ratio"][:2] == ["0.97", "0.97"] and sorted(o["different_settings_to_system"][1].split(";")) == ["filament_flow_ratio", "nozzle_temperature"]
      and o["inherits_group"][1] == cfg_of(BASE)["filament_settings_id"][0] and o["filament_settings_id"][0] == "My own PLA")
parent = cfg_of(BASE)["nozzle_temperature"][:2]
r = run_job(USER, '[[filament]]\nslot = 1\n[filament.settings]\nnozzle_temperature = %s\n' % parent[0], "u3")
o = cfg_of(r.path)
check("setting set back to the parent's value: its key leaves the diff list", "nozzle_temperature" not in o["different_settings_to_system"][1], o["different_settings_to_system"][1])
r = run_job(USER, 'plate_type = "High Temp Plate"\n[[filament]]\nslot = 1\npreset = "%s"\n' % PETG, "u4")
o = cfg_of(r.path)
check("user-preset slot swapped for a system preset: the user preset's change is dropped and listed, inherits entry empty",
      o["filament_settings_id"][0] == PETG and o.get("inherits_group", [""] * 4)[1] == "" and ("filament slot 1", [("nozzle_temperature", "231")]) in [(a, b) for a, b in r.dropped], r.dropped)
orphan = cfg_edit(BASE, "orphan", lambda c: (c["filament_settings_id"].__setitem__(0, "Made from scratch"), c.setdefault("inherits_group", [""] * (len(c["filament_settings_id"]) + 2)).__setitem__(1, "")))
r = run_job(orphan, '[[filament]]\nslot = 2\ncolor = "#445566"\n', "u5")
check("a user preset with no parent: another slot can still be patched, the file validates", not vrep(r.path).errors)

# ---- CRLF config and the Studio CLI zip form (bit 11 on every name, stored PNG, a non-ASCII name) ----------------------------------------------------
crlf = rewrite(BASE, os.path.join(tmp, "crlf.3mf"), lambda n, d: d.replace(b"\n", b"\r\n") if n == PS else d)
check("fixture: CRLF project_settings round-trips (the writer keeps the input's newline)", patch_read.dump_config(cfg_of(crlf), "\r\n") == zipfile.ZipFile(crlf).read(PS))
r = run_job(crlf, '[settings]\nwall_loops = 3\n', "crlf_o")
raw = zipfile.ZipFile(r.path).read(PS)
check("CRLF input: output config keeps CRLF, no bare LF", b"\r\n" in raw and raw.count(b"\n") == raw.count(b"\r\n"))
png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
cli = os.path.join(tmp, "cli.3mf")
with zipfile.ZipFile(BASE) as zi_, zipfile.ZipFile(cli, "w", zipfile.ZIP_DEFLATED) as zo:
    def put(name, data, ctype=zipfile.ZIP_DEFLATED):
        zi = _Info(name, date_time=(1980, 1, 1, 0, 0, 0))
        zi.compress_type = ctype
        zi.force_utf8 = True
        zo.writestr(zi, data)
    for i in zi_.infolist():
        put(i.filename, zi_.read(i.filename))
    put("Metadata/plate_1.png", png, zipfile.ZIP_STORED)
    put("Auxiliaries/Models/Modèle été.txt", b"note")
check("fixture: the CLI form validates (stored PNG, bit 11 on every name, non-ASCII name)", not vrep(cli).errors, [str(f)[:90] for f in vrep(cli).errors])
r = run_job(cli, '[[filament]]\nslot = 2\ncolor = "#445566"\n', "cli_o")
zin, zout = zipfile.ZipFile(cli), zipfile.ZipFile(r.path)
check("CLI form: names, order, bit 11 on every entry, stored PNG still stored, non-ASCII name kept",
      zin.namelist() == zout.namelist() and all(i.flag_bits == 0x800 for i in zout.infolist()) and zout.getinfo("Metadata/plate_1.png").compress_type == zipfile.ZIP_STORED
      and zout.read("Auxiliaries/Models/Modèle été.txt") == b"note" and zout.read("Metadata/plate_1.png") == png)
zip64 = os.path.join(tmp, "zip64.3mf")
with zipfile.ZipFile(BASE) as zi_, zipfile.ZipFile(zip64, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zo:
    for i in zi_.infolist():
        zi = zipfile.ZipInfo(i.filename, date_time=(1980, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_DEFLATED
        with zo.open(zi, "w", force_zip64=True) as f:
            f.write(zi_.read(i.filename))
r = run_job(zip64, '[settings]\nwall_loops = 3\n', "zip64_o")
check("input written with Zip64 headers: the output reads back, untouched bytes equal", all(zipfile.ZipFile(zip64).read(n) == zipfile.ZipFile(r.path).read(n) for n in zipfile.ZipFile(zip64).namelist() if n not in (PS,)))

# ---- refusals ------------------------------------------------------------------------------------------------------------------------------------------------------
def head_edit(src, name, f):
    return rewrite(src, os.path.join(tmp, name + ".3mf"), lambda n, d: f(d) if n == M3 else d)
ok_body = '[settings]\nwall_loops = 3\n'
cases = [
    ("slot beyond n + 1", BASE, '[[filament]]\nslot = 9\ncolor = "#000000"\n', "a new slot must be the next number"),
    ("printer change to another bed", BASE, 'printer = "Bambu Lab A1 0.4 nozzle"\n' + ok_body, "printable_height differs"),
    ("mesh in an object", BASE, '[[object]]\nname = "Cube"\nmesh = "x.stl"\n', "never changes geometry"),
    ("position of an object", BASE, '[[object]]\nname = "Cube"\nposition = [10, 10]\n', "never changes geometry"),
    ("studio key", BASE, 'studio = "2.9.1"\n' + ok_body, "not used in patch mode"),
    ("output = input", BASE, ok_body, None),
    ("unknown preset: closest names", BASE, '[[filament]]\nslot = 1\npreset = "Bambu PETG HF @BBL P1S"\n', "Closest"),
    ("process made for another printer", BASE, 'process = "0.20mm Standard @BBL A2L"\n', "not made for"),
    ("unknown setting", BASE, '[settings]\nwal_loops = 3\n', "Closest"),
    ("object that does not exist", BASE, '[[object]]\nname = "Nope"\n[object.settings]\nwall_loops = 3\n', "no object matches"),
    ("part that does not exist", BASE, '[[object]]\nname = "Cube"\n[[object.part]]\nname = "Nope"\nfilament = 2\n', "no part named"),
    ("job that changes nothing", BASE, 'reset_to_preset = false\n', "changes nothing"),
    ("support filament above n", BASE, '[settings]\nsupport_filament = 7\n', "filament slots are 0"),
]
for label, base, body, want in cases:
    if label == "output = input":
        jp = os.path.join(tmp, "same.toml")
        open(jp, "w").write('patch = "%s"\noutput = "%s"\n%s' % (base, base, ok_body))
        try:
            patch.run(patch_job.load_patch_job(jp)); msg = None
        except PatchRefused as e:
            msg = " | ".join(e.problems)
        check("refused: output = input, the input is not touched", msg and "same file" in msg and zipfile.ZipFile(base).testzip() is None, msg)
        continue
    msg = None
    try:
        jp = os.path.join(tmp, "ref.toml")
        open(jp, "w").write('patch = "%s"\noutput = "%s"\n%s' % (base, os.path.join(tmp, "ref.3mf"), body))
        patch.run(patch_job.load_patch_job(jp))
    except PatchRefused as e:
        msg = " | ".join(e.problems)
    check("refused: %s" % label, msg and want in msg, msg)
    check("   no output file left behind (%s)" % label, not os.path.exists(os.path.join(tmp, "ref.3mf")) and not os.path.exists(os.path.join(tmp, "ref.3mf.part")))

# the input files
orca = head_edit(BASE, "orca", lambda d: d.replace(b"BambuStudio-02.08.02.61", b"OrcaSlicer-02.01.00.00"))
check("refused: Orca Slicer file", "not by Bambu Studio" in (refused(orca, ok_body) or "") and "save it in Bambu Studio 2.9.1 first" in (refused(orca, ok_body) or ""))
v27 = rewrite(BASE, os.path.join(tmp, "v27.3mf"), lambda n, d: d.replace(b"BambuStudio-02.08.02.61", b"BambuStudio-02.07.00.50") if n == M3 else (d.replace(b'"version": "02.08.02.61"', b'"version": "02.07.00.50"') if n == PS else d))
check("refused: Studio 2.7", "2.7.0.50. Patch mode accepts 2.8.2" in (refused(v27, ok_body) or ""), refused(v27, ok_body))
mism = cfg_edit(BASE, "vmism", lambda c: c.__setitem__("version", "02.09.01.70"))
check("refused: Application and project_settings version disagree", "project settings are version" in (refused(mism, ok_body) or ""))
multi = cfg_edit(BASE, "twonoz", lambda c: c.__setitem__("nozzle_diameter", ["0.4", "0.4"]))
check("refused: two nozzles", "nozzles" in (refused(multi, ok_body) or ""), refused(multi, ok_body))
open(os.path.join(tmp, "nz.3mf"), "wb").write(b"not a zip at all")
check("refused: a file that is not a ZIP", "not a readable ZIP" in (refused(os.path.join(tmp, "nz.3mf"), ok_body) or ""), refused(os.path.join(tmp, "nz.3mf"), ok_body))
dup = rewrite(BASE, os.path.join(tmp, "dup.3mf"), extra=[("metadata/PROJECT_SETTINGS.config", b"{}")])
check("refused: duplicate entry names (G3)", refused(dup, ok_body) is not None)
emb = rewrite(BASE, os.path.join(tmp, "emb.3mf"), extra=[("Metadata/filament_settings_1.config", b"{}")])
msg = refused(emb, '[[filament]]\nslot = 1\npreset = "%s"\n' % PETG)
check("refused: a filament preset swap when the file stores a filament preset (the copy would go stale)", msg and "stores filament preset" in msg, msg)
check("   a colour change is still fine with an embedded preset", run_job(emb, '[[filament]]\nslot = 2\ncolor = "#445566"\n', "emb2").path)
broken = cfg_edit(BASE, "broken", lambda c: c.pop("printer_settings_id"))
check("refused: an input that does not validate (default mode errors)", "does not validate" in (refused(broken, ok_body) or ""))
twice = build("twice", "2.8.2")
dupname = rewrite(twice, os.path.join(tmp, "dupname.3mf"), lambda n, d: d.replace(b'key="name" value="Cap"', b'key="name" value="Cube"') if n == MS else d)
check("ambiguous object name: refused, says to add plate", "matches 2 objects" in (refused(dupname, '[[object]]\nname = "Cube"\n[object.settings]\nwall_loops = 3\n') or "") and "plate = N" in (refused(dupname, '[[object]]\nname = "Cube"\n[object.settings]\nwall_loops = 3\n') or ""))
check("   the same name with plate = 2 picks one", run_job(dupname, '[[object]]\nname = "Cube"\nplate = 2\n[object.settings]\nwall_loops = 3\n', "dn").path)

doctype = rewrite(BASE, os.path.join(tmp, "doctype.3mf"), lambda n, d: d.replace(b"<config>", b'<!DOCTYPE c [<!ENTITY a "b">]><config>', 1) if n == MS else d)
check("refused: DOCTYPE / ENTITY in model_settings.config (G6)", "DOCTYPE" in (refused(doctype, ok_body) or ""), refused(doctype, ok_body))
bomb = rewrite(BASE, os.path.join(tmp, "bomb.3mf"), extra=[("Auxiliaries/zeros.bin", b"\0" * (100 * 1024 ** 2))])
check("refused: a 100 MiB entry that compresses 1000:1 (G4), before anything is read", "G4" in (refused(bomb, ok_body) or ""), refused(bomb, ok_body))
crlf_ms = rewrite(BASE, os.path.join(tmp, "crlfms.3mf"), lambda n, d: d.replace(b"\n", b"\r\n") if n == MS else d)
r = run_job(crlf_ms, '[[object]]\nname = "Cube"\n[object.settings]\nwall_loops = 6\n[[plate]]\nid = 1\nplate_type = "Cool Plate"\n', "crlfms_o")
raw = zipfile.ZipFile(r.path).read(MS)
check("CRLF model_settings.config: inserted lines use CRLF, no bare LF", b'key="wall_loops" value="6"' in raw and b'key="bed_type"' in raw and raw.count(b"\n") == raw.count(b"\r\n"))

# ---- the job reader ---------------------------------------------------------------------------------------------------------------------------------------------------
check("a job with patch is a patch job", patch_job.is_patch_job(os.path.join(tmp, "pswap.toml")) and not patch_job.is_patch_job(os.path.join(H, "jobs", "tower.toml")))

# ---- mutation style: a patch that changes something not asked must fail the post-checks ----------------------------------------------------------
GOOD = run_job(BASE, '[[filament]]\nslot = 2\ncolor = "#445566"\n[settings]\nwall_loops = 3\n', "good")
SRC = patch_read.open_source(BASE)
d_def, d_strict = patch_verify.validate_both(BASE)
ps_ = patch.ProfileSet(SRC.tag)
req, edits, layers, warns, _pt = patch.build_request(patch_job.load_patch_job(os.path.join(tmp, "good.toml")), SRC, ps_)
plan = patch.Plan()
plan.n, plan.req, plan.flush_slots, plan.ms_edits, plan.layers, plan.recolored, plan.added_gcode, plan.edited = 4, req, [1], edits, [], 0, False, [PS]
base_ck = patch_verify.verify(SRC, GOOD.path, plan, d_strict, ps_)
check("the honest output passes every post-check (and the plan matches it)", base_ck.ok, base_ck.failed())


def mutate(name, fn_entry=None, order=None, flags=None):
    p = os.path.join(tmp, "mut_%s.3mf" % name)
    z = zipfile.ZipFile(GOOD.path)
    names = [i.filename for i in z.infolist()]
    if order:
        names = order(names)
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zo:
        for n_ in names:
            d = z.read(n_)
            if fn_entry:
                d = fn_entry(n_, d)
            zi = _Info(n_, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.force_utf8 = bool((flags or {}).get(n_, 0))
            zo.writestr(zi, d)
    return p


def fails_check(p, text):
    ck = patch_verify.verify(SRC, p, plan, d_strict, ps_)
    return any(text in nm for nm, ok, _d in ck.failed()), [nm for nm, _ok, _d in ck.failed()]


def extra_key(n_, d):
    if n_ != PS:
        return d
    c = json.loads(d)
    c["layer_height"] = "0.3"
    return json.dumps(c, indent=4, ensure_ascii=False).encode() + b"\n"
ok_, got = fails_check(mutate("cfgkey", extra_key), "every difference is one the job asked for")
check("mutation: an extra config key changed -> post-check fails", ok_, got)


def other_slot(n_, d):
    if n_ != PS:
        return d
    c = json.loads(d)
    c["filament_colour"][3] = "#000001"
    return json.dumps(c, indent=4, ensure_ascii=False).encode() + b"\n"
ok_, got = fails_check(mutate("slotcol", other_slot), "every difference is one the job asked for")
check("mutation: the colour of a slot that was not asked for changed -> post-check fails", ok_, got)
ok_, got = fails_check(mutate("flipbyte", lambda n_, d: d[:-1] + bytes([d[-1] ^ 1]) if n_ == "3D/Objects/object_1.model" else d), "untouched entries")
check("mutation: one byte flipped in an untouched entry -> post-check fails", ok_, got)
ok_, got = fails_check(mutate("order", order=lambda ns: ns[:2][::-1] + ns[2:]), "entry list and order")
check("mutation: entry order swapped -> post-check fails", ok_, got)
base_bit = fails_check(mutate("bit11b", flags={n_: 0x800 for n_ in zipfile.ZipFile(GOOD.path).namelist()}), "bit 11")
check("mutation: bit 11 added to an entry that the input has without it -> post-check fails", base_bit[0], base_bit[1])
# the same with an input that HAS bit 11 on every name (the Studio CLI form): the output must keep it, a copy that drops it fails
CLI_R = run_job(cli, '[[filament]]\nslot = 2\ncolor = "#445566"\n', "cli_o2")
CLI_SRC = patch_read.open_source(cli)
cli_plan = patch.Plan()
cli_plan.n, cli_plan.flush_slots, cli_plan.ms_edits, cli_plan.layers, cli_plan.recolored, cli_plan.added_gcode, cli_plan.edited = 4, [1], [], [], 0, False, [PS]
cli_plan.req = patch.patch_cfg.Request()
cli_plan.req.slots = {1: {"preset": None, "color": "#445566", "settings": {}, "keep": False}}
cli_ck = patch_verify.verify(CLI_SRC, CLI_R.path, cli_plan, patch_verify.validate_both(cli)[1], ps_)
check("the CLI-form output passes the post-checks", cli_ck.ok, cli_ck.failed())
nobit = rewrite(CLI_R.path, os.path.join(tmp, "nobit.3mf"))
ck = patch_verify.verify(CLI_SRC, nobit, cli_plan, patch_verify.validate_both(cli)[1], ps_)
check("mutation: bit 11 dropped from every entry (a plain re-zip) -> post-check fails", any("bit 11" in nm for nm, _ok, _d in ck.failed()), [nm for nm, _o, _d in ck.failed()])
ok_, got = fails_check(mutate("roottail", lambda n_, d: d.replace(b"</model>", b" </model>") if n_ == M3 else d), "untouched entries")
check("mutation: the root model changed -> post-check fails", ok_, got)
ok_, got = fails_check(mutate("msattr", lambda n_, d: d.replace(b'<config>', b'<config>\n  <metadata key="x" value="1"/>', 1) if n_ == MS else d), "model_settings")
check("mutation: an extra metadata line in model_settings -> post-check fails", ok_, got)
ok_, got = fails_check(mutate("msval", lambda n_, d: d.replace(b'key="extruder" value="2"', b'key="extruder" value="3"', 1) if n_ == MS else d), "model_settings")
check("mutation: an extruder value changed in model_settings -> post-check fails", ok_, got)

# the whole run refuses and leaves nothing when the editor changes something not asked
real_apply = patch_cfg.apply_config


def bad_apply(src, ps, req):
    r_ = real_apply(src, ps, req)
    r_.cfg["layer_height"] = "0.28"
    return r_
patch_cfg.apply_config = bad_apply
try:
    msg = refused(BASE, '[[filament]]\nslot = 2\ncolor = "#445566"\n', "mutrun")
finally:
    patch_cfg.apply_config = real_apply
check("mutation, whole run: an editor that changes layer_height unasked -> PatchRefused, no output, no .part", msg and "failed its own checks" in msg and not os.path.exists(os.path.join(tmp, "mutrun.3mf")) and not os.path.exists(os.path.join(tmp, "mutrun.3mf.part")), msg)

real_set = patch_xml.MsDoc.set


def bad_set(self, scope, key, value):
    changed = real_set(self, scope, key, value)
    real_set(self, ("object", "3"), "wall_loops", "9")
    return changed
patch_xml.MsDoc.set = bad_set
try:
    msg = refused(BASE, '[[object]]\nname = "Cap"\n[object.settings]\nsparse_infill_density = "25%"\n', "mutrun2")
finally:
    patch_xml.MsDoc.set = real_set
check("mutation, whole run: a model_settings edit that also touches another object -> PatchRefused", msg and "failed its own checks" in msg, msg)

# ---- D1: an input named '<output>.part' is never touched ------------------------------------------------------------------------
vic = os.path.join(tmp, "victim.3mf.part")
shutil.copy(BASE, vic)
h0 = hashlib.sha256(open(vic, "rb").read()).hexdigest()
for f in glob.glob(os.path.join(tmp, "victim.3mf*")):
    if f != vic:
        os.remove(f)
open(os.path.join(tmp, "victim.toml"), "w").write('patch = "%s"\noutput = "victim.3mf"\n[settings]\nwall_loops = 3\n' % vic)
try:
    rr = patch.run(patch_job.load_patch_job(os.path.join(tmp, "victim.toml")))
except Exception as e:
    rr = e
check("D1: input named <output>.part: the input is still there and byte-identical", os.path.exists(vic) and hashlib.sha256(open(vic, "rb").read()).hexdigest() == h0)
check("D1: ... and the patch itself worked (output is a valid zip)", not isinstance(rr, Exception) and zipfile.ZipFile(os.path.join(tmp, "victim.3mf")).testzip() is None, rr)
check("D1: no temp file left behind", not [f for f in os.listdir(tmp) if f.startswith(".victim.3mf.")])
# a failed run (refused after the temp file was made) removes only its own temp file
os.remove(os.path.join(tmp, "victim.3mf"))
open(os.path.join(tmp, "victim2.toml"), "w").write('patch = "%s"\noutput = "victim.3mf"\n[settings]\nwal_loops = 3\n' % vic)
msg = None
try:
    patch.run(patch_job.load_patch_job(os.path.join(tmp, "victim2.toml")))
except PatchRefused as e:
    msg = str(e)
check("D1: a refused job leaves the input alone", msg and os.path.exists(vic) and hashlib.sha256(open(vic, "rb").read()).hexdigest() == h0, msg)
# the temp-file guard
try:
    patch._make_temp(vic[:-5], vic[:-5])
    guard = False
except Exception:
    guard = True
check("D1: output the same file as the input is refused by the temp-file guard", guard)
for f in [f for f in os.listdir(tmp) if f.startswith(".victim")]:
    os.remove(os.path.join(tmp, f))

# ---- D8: two inputs refused late before ---------------------------------------------------------------------------------------------------
# (a) a non-ASCII entry name without bit 11: refused in open_source with the reason
raw = open(BASE, "rb").read()
zz = zipfile.ZipFile(BASE)
victim = next(n for n in zz.namelist() if n.endswith(".json"))             # same length, two bytes become cp437 "e-acute" bytes in both headers
bad_name = raw.replace(victim.encode(), victim.encode()[:-2] + b"\x82\x82")
open(os.path.join(tmp, "cp437.3mf"), "wb").write(bad_name)
check("D8a setup: the crafted file reads as a ZIP and the odd name has no bit 11", any((not i.filename.isascii()) and not i.flag_bits & 0x800 for i in zipfile.ZipFile(os.path.join(tmp, "cp437.3mf")).infolist()))
m = refused(os.path.join(tmp, "cp437.3mf"), '[settings]\nwall_loops = 3\n', "d8a")
check("D8a: a non-ASCII entry name without bit 11 is refused at open, with a clear reason", m is not None and "bit 11" in m and "non-ASCII" in m and "failed its own checks" not in m, m)
# (b) a newline or tab in an object string setting is written as a character reference and reads back as it was
r = run_job(BASE, '[[object]]\nname = "Cap"\n[object.settings]\nprocess_notes = "a\\nb\\tc"\n', "d8b")
msx = zipfile.ZipFile(r.path).read(MS).decode()
import xml.etree.ElementTree as _ET
vals = [m_.get("value") for m_ in _ET.fromstring(msx).iter("metadata") if m_.get("key") == "process_notes"]
check("D8b: newline and tab in an object setting: written as &#xA; / &#x9;, reads back as it was, post-checks pass",
      "&#xA;" in msx and "&#x9;" in msx and vals == ["a\nb\tc"] and all(c[1] for c in r.checks), (vals, [c for c in r.checks if not c[1]]))

# ---- D9: extruder "0" means inherit, not slot -1 ----------------------------------------------------------------------------------------
srcD9 = patch_read.open_source(BASE)
oid = next(o for o in srcD9.ms["objects"] if o["meta"].get("name") == "Cap")
pid = oid["parts"][0]["id"]
u0 = patch._used_slots(srcD9, [(("part", oid["id"], pid), "extruder", "0")], 2)
u1 = patch._used_slots(srcD9, [], 2)
check("D9: a part whose extruder is set to 0 names no slot -1 (it inherits the object's filament)", -1 not in u0 and min(u0, default=0) >= 0, u0)
uo = patch._used_slots(srcD9, [(("object", oid["id"]), "extruder", "0")], 2)
check("D9: an object extruder of 0 names no slot -1 either", -1 not in uo, uo)
check("D9: without the edits the used slots are unchanged (Cap uses slot 3)", u1 == {2}, u1)

# ---- nit: two objects with the same name on the same plate -------------------------------------------------------------------------------
dup = os.path.join(tmp, "dup.toml")
open(dup, "w").write(
                 'studio = "2.8.2"\noutput = "dup.3mf"\nprinter = "Bambu Lab P1S 0.4 nozzle"\nprocess = "0.16mm Optimal @BBL X1C"\n[[filament]]\npreset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"\ncolor = "#111111"\n'
                     '[[object]]\nname = "Twin"\n[[object.part]]\nname = "A"\nmesh = "%s/cube20.stl"\nfilament = 1\n[[object]]\nname = "Twin"\n[[object.part]]\nname = "A"\nmesh = "%s/cube20.stl"\nfilament = 1\n' % (MESH, MESH))
package.build(load_job(dup), os.path.join(tmp, "dup.3mf"))
m = refused(os.path.join(tmp, "dup.3mf"), '[[object]]\nname = "Twin"\nplate = 1\n[object.settings]\nsparse_infill_density = "40%"\n', "dupref")
check("nit: same name on the same plate: the message says the job cannot tell them apart (not 'add plate')", m is not None and "cannot tell them apart" in m and "Add plate" not in m, m)

# ---- the command line ------------------------------------------------------------------------------------------------------------------------------------------------------------
import subprocess
jp = os.path.join(tmp, "cli.toml")
open(jp, "w").write('patch = "%s"\noutput = "cliout.3mf"\n[settings]\nwall_loops = 3\n' % BASE)
rc = subprocess.run([sys.executable, "-I", os.path.join(H, "..", "scripts", "build_bambu_3mf.py"), jp], capture_output=True, text=True)
check("command line: exit 0, summary on stdout, summary file next to the output, source named in it",
      rc.returncode == 0 and "Patched" in rc.stdout and os.path.exists(os.path.join(tmp, "cliout.summary.md")) and os.path.basename(BASE) in open(os.path.join(tmp, "cliout.summary.md")).read(), (rc.stdout[-300:], rc.stderr[-300:]))
open(jp, "w").write('patch = "%s"\noutput = "cliout2.3mf"\n[settings]\nwal_loops = 3\n' % BASE)
rc = subprocess.run([sys.executable, "-I", os.path.join(H, "..", "scripts", "build_bambu_3mf.py"), jp], capture_output=True, text=True)
check("command line: a refused patch exits 1 and says why", rc.returncode == 1 and "patch cannot be made" in rc.stderr and "Closest" in rc.stderr, rc.stderr[-300:])

print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
