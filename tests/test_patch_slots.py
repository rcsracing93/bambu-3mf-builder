#!/usr/bin/env python3
"""Patch mode: slot add, last-slot removal, rule 5 (root-model metadata), sliced input. Every input is synthetic: builder output made in
this test (and converted to the non-split, painted, sliced forms below). Never a private file.
Covers: slot add on split and non-split inputs, Studio 2.8.2 and 2.9.1 (every per-slot list has the right length - the validator in strict mode
finds no list-size finding; the result equals the builder's own 5-filament config), one slot to two (custom G-code mode), plate maps,
filament_sequence.json, removal (allowed, and refused for each kind of use: part, painted area, per-object filament key, project key, tool
change, height range, sequence, middle slot), add then remove gives the input back, rule 5 (designer kept / local id removed / path removed /
unknown key kept / rest of the root model byte-identical), sliced input, and mutation-style checks for every new edit path.
Run: python3 -I tests/test_patch_slots.py"""
import hashlib, json, os, re, sys, tempfile, zipfile
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import package, patch, patch_job, patch_read, patch_slots, patch_verify, patch_xml, validate, wipetower  # noqa: E402
from bambu3mf.job import load_job  # noqa: E402
from bambu3mf.patch_job import PatchRefused  # noqa: E402

failed = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)[:300]) if (extra and not cond) else ""))
    if not cond:
        failed.append(name)


tmp = tempfile.mkdtemp()
PS, MS, M3, GC = "Metadata/project_settings.config", "Metadata/model_settings.config", "3D/3dmodel.model", "Metadata/custom_gcode_per_layer.xml"
SI, SQ = "Metadata/slice_info.config", "Metadata/filament_sequence.json"
MESH = os.path.join(H, "..", "examples", "data")
PETG = "Bambu PETG HF @BBL P1S 0.4 nozzle"
PLA = "Bambu PLA Basic @BBL P1S 0.4 nozzle"
FIL = [(PLA, "#111111"), ("Bambu PLA Matte @BBL P1S 0.4 nozzle", "#FFFFFF"), (PLA, "#C12E1F"), (PLA, "#0A2989"), (PETG, "#FF8800"), (PLA, "#00AA55")]


# ---- synthetic inputs ---------------------------------------------------------------------------------------------------------------
def build(name, version, nfil=4, layers="", parts=True):
    """nfil filaments, two plates: object Cube (plate 1, parts Lower slot 1 / Upper slot 2), object Cap (plate 2, slot 3 when nfil >= 3)."""
    p = os.path.join(tmp, name + ".toml")
    t = 'studio = "%s"\noutput = "%s.3mf"\nprinter = "Bambu Lab P1S 0.4 nozzle"\nprocess = "0.16mm Optimal @BBL X1C"\nplate_type = "High Temp Plate"\n[settings]\ninfill_pattern = "grid"\nwall_loops = 4\n' % (version, name)
    for pr, col in FIL[:nfil]:
        t += '[[filament]]\npreset = "%s"\ncolor = "%s"\n' % (pr, col)
    if nfil == 1:
        t += '[[object]]\nname = "Cube"\n[[object.part]]\nname = "Lower"\nmesh = "%s/slab1.stl"\nfilament = 1\n' % MESH
        t += '[[object]]\nname = "Cap"\nplate = 2\n[[object.part]]\nname = "Body"\nmesh = "%s/cube20.stl"\nfilament = 1\n' % MESH
    else:
        t += '[[object]]\nname = "Cube"\n[[object.part]]\nname = "Lower"\nmesh = "%s/slab1.stl"\nfilament = 1\n[[object.part]]\nname = "Upper"\nmesh = "%s/slab2.stl"\nfilament = 2\n' % (MESH, MESH)
        t += '[[object]]\nname = "Cap"\nplate = 2\n[object.settings]\nsparse_infill_density = "40%%"\n[[object.part]]\nname = "Body"\nmesh = "%s/cube20.stl"\nfilament = %d\n' % (MESH, min(3, nfil))
    t += layers
    open(p, "w").write(t)
    out = os.path.join(tmp, name + ".3mf")
    package.build(load_job(p), out)
    return out


def rewrite(src, dst, fn=None, extra=(), drop=()):
    z = zipfile.ZipFile(src)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
        for i in z.infolist():
            if i.filename in drop:
                continue
            d = z.read(i.filename)
            if fn:
                d = fn(i.filename, d)
            zi = zipfile.ZipInfo(i.filename, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zo.writestr(zi, d)
        for n, d in extra:
            zi = zipfile.ZipInfo(n, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zo.writestr(zi, d)
    return dst


def to_nonsplit(src, dst):
    """Put the mesh of object_1 into the root model (the form Studio writes for a project with one object)."""
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


def ms_of(path):
    return zipfile.ZipFile(path).read(MS).decode()


def vrep(path, mode="default"):
    return validate.validate(path, mode=mode)


def sha(path, name):
    return hashlib.sha256(zipfile.ZipFile(path).read(name)).hexdigest()


ADD5 = '[[filament]]\nslot = 5\npreset = "%s"\ncolor = "#FF8800"\n' % PETG
LISTRULES = ("R5", "R6", "R7", "R8", "R11", "R14", "R35")

# ---- bases ---------------------------------------------------------------------------------------------------------------------------------
B = {}
for v in ("2.8.2", "2.9.1"):
    t = v.replace(".", "")
    B["split " + v] = build("multi" + t, v)
    B["nonsplit " + v] = to_nonsplit(build("one" + t, v, nfil=4), os.path.join(tmp, "ns" + t + ".3mf")) if False else None
# the non-split form needs a one-object file
for v in ("2.8.2", "2.9.1"):
    t = v.replace(".", "")
    jp = os.path.join(tmp, "oneobj" + t + ".toml")
    open(jp, "w").write(open(os.path.join(H, "jobs", "four_color_%s.toml" % ("282" if v == "2.8.2" else "291"))).read().replace('output  = "f4out.3mf"', 'output = "oneobj%s.3mf"' % t).replace("../data/", MESH + "/"))
    package.build(load_job(jp), os.path.join(tmp, "oneobj" + t + ".3mf"))
    B["single " + v] = os.path.join(tmp, "oneobj" + t + ".3mf")
    B["nonsplit " + v] = to_nonsplit(B["single " + v], os.path.join(tmp, "ns" + t + ".3mf"))
check("synthetic bases validate with 0 errors", all(not vrep(p).errors for p in B.values()), [(k, [str(f)[:80] for f in vrep(p).errors]) for k, p in B.items() if vrep(p).errors])

# ---- slot add: every form, both versions -----------------------------------------------------------------------------------------------------
for form, base in B.items():
    ver = form.split()[1]
    try:
        r = run_job(base, ADD5 + '[[filament]]\nslot = 6\npreset = "%s"\ncolor = "#00AA55"\n' % PLA, "add_" + form.replace(" ", "_"))
        o, i = cfg_of(r.path), cfg_of(base)
        check("[%s] add two slots: 6 slots, post-checks pass, default mode 0 errors" % form, len(o["filament_colour"]) == 6 and r.checks and all(c[1] for c in r.checks) and not vrep(r.path).errors,
              ([c for c in r.checks if not c[1]], [str(f)[:90] for f in vrep(r.path).errors]))
        sv = vrep(r.path, "strict")
        bad = [f for f in sv.findings if f.rule in LISTRULES and f.level in ("error", "warning")]
        check("[%s] strict validator: no list-size finding (R5 R6 R7 R8 R11 R14 R35)" % form, not bad, [str(f)[:90] for f in bad[:3]])
        check("[%s] the new slots hold the preset and the colour; the old four are unchanged" % form,
              o["filament_settings_id"] == i["filament_settings_id"] + [PETG, PLA] and o["filament_colour"] == i["filament_colour"] + ["#FF8800", "#00AA55"]
              and o["filament_multi_colour"][:4] == i["filament_multi_colour"] and o["nozzle_temperature"][:len(i["nozzle_temperature"])] == i["nozzle_temperature"])
        n = 6
        M0, M1 = i["flush_volumes_matrix"], o["flush_volumes_matrix"]
        check("[%s] purge matrix 6 x 6: old cells kept, diagonal 0, new rows and columns filled" % form,
              len(M1) == 36 and all(M1[r_ * n + c] == M0[r_ * 4 + c] for r_ in range(4) for c in range(4)) and all(M1[k * n + k] == "0" for k in range(n))
              and all(M1[r_ * n + c] not in ("", None) for r_ in range(n) for c in range(n) if r_ >= 4 or c >= 4) and len(o["flush_volumes_vector"]) == 12)
        plates = re.findall(r"<plate>.*?</plate>", ms_of(r.path), re.S)
        check("[%s] every plate's filament_maps and filament_volume_maps got two more entries" % form,
              all(re.search(r'key="filament_maps" value="1 1 1 1 1 1"', pl) and re.search(r'key="filament_volume_maps" value="0 0 0 0 0 0"', pl) for pl in plates) and len(plates) >= 1, plates[:1])
        check("[%s] untouched entries: the root model and every mesh keep their bytes" % form, sha(base, M3) == sha(r.path, M3) and all(sha(base, n_) == sha(r.path, n_) for n_ in zipfile.ZipFile(base).namelist() if n_.startswith("3D/")))
    except Exception as e:                                           # noqa: BLE001
        check("[%s] slot add runs" % form, False, "%s: %s" % (type(e).__name__, e))

# the result equals what the builder makes for five filaments (same job, one more filament)
for ver in ("2.8.2", "2.9.1"):
    t = ver.replace(".", "")
    base4, base5 = build("b4_" + t, ver, 4), build("b5_" + t, ver, 5)
    r = run_job(base4, ADD5, "cmp_" + t)
    a, b = cfg_of(r.path), cfg_of(base5)
    diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
    check("[split %s] added slot = the builder's own 5-filament config (only the wipe tower position may differ)" % ver, set(diff) <= {"wipe_tower_x", "wipe_tower_y"}, diff)

# one slot becomes two: the custom G-code mode follows
LAY = "[[layer_gcode]]\nplate = 1\nz = 2.12\npause = \"x\"\n"
one = build("one1", "2.8.2", nfil=1, layers=LAY)
check("fixture: one slot, custom G-code in SingleExtruder mode", b"SingleExtruder" in zipfile.ZipFile(one).read(GC) and not vrep(one).errors, [str(f)[:80] for f in vrep(one).errors])
r = run_job(one, '[[filament]]\nslot = 2\npreset = "%s"\ncolor = "#445566"\n' % PETG, "one_to_two")
check("1 -> 2 slots: MultiAsSingle mode in the custom G-code, 0 errors, post-checks pass, matrix 2 x 2",
      b"MultiAsSingle" in zipfile.ZipFile(r.path).read(GC) and b"SingleExtruder" not in zipfile.ZipFile(r.path).read(GC) and not vrep(r.path).errors and all(c[1] for c in r.checks)
      and len(cfg_of(r.path)["flush_volumes_matrix"]) == 4 and cfg_of(r.path)["flush_volumes_matrix"][0] == "0", ([c for c in r.checks if not c[1]], [str(f)[:90] for f in vrep(r.path).errors]))
r2 = run_job(r.path, '[[filament]]\nslot = 2\nremove = true\n', "two_to_one")
check("2 -> 1 slot again: SingleExtruder mode back, the input's config and custom G-code come back byte for byte",
      zipfile.ZipFile(r2.path).read(GC) == zipfile.ZipFile(one).read(GC) and zipfile.ZipFile(r2.path).read(PS) == zipfile.ZipFile(one).read(PS) and zipfile.ZipFile(r2.path).read(MS) == zipfile.ZipFile(one).read(MS))

# a new slot given a part, a setting, and a purge recalculation not triggered
r = run_job(B["split 2.8.2"], ADD5 + '[filament.settings]\nnozzle_temperature = 225\n[[object]]\nname = "Cap"\n[[object.part]]\nname = "Body"\nfilament = 5\n', "add_use")
o = cfg_of(r.path)
check("new slot with a setting and a part on it: validates, the setting is in the new slot only, the part has extruder 5",
      not vrep(r.path).errors and all(c[1] for c in r.checks) and o["nozzle_temperature"][-2:] == ["225", "225"] and o["nozzle_temperature"][:-2] == cfg_of(B["split 2.8.2"])["nozzle_temperature"]
      and "nozzle_temperature" in o["different_settings_to_system"][5], ([c for c in r.checks if not c[1]], o.get("different_settings_to_system")))
check("   the diff list has one entry per preset (7), the new entry before the printer entry",
      len(o["different_settings_to_system"]) == 7 and "nozzle_temperature" in o["different_settings_to_system"][5] and len(o.get("inherits_group", [""] * 7)) == 7, o["different_settings_to_system"])
check("   the Cap body part has extruder 5 and the tower is sized for it", re.search(r'key="name" value="Body"/>.*?key="extruder" value="5"', ms_of(r.path), re.S) is not None)

# plate maps absent stay absent; filament_sequence.json extended
nomaps = rewrite(B["split 2.8.2"], os.path.join(tmp, "nomaps.3mf"), lambda n, d: re.sub(rb'\s*<metadata key="filament_(volume_)?maps" value="[^"]*"/>', b"", d) if n == MS else d)
r = run_job(nomaps, ADD5, "add_nomaps")
check("a file without filament_maps keys: they stay absent", b"filament_maps" not in zipfile.ZipFile(r.path).read(MS) and not vrep(r.path).errors)
seq = rewrite(B["split 2.8.2"], os.path.join(tmp, "seq.3mf"), lambda n, d: d.replace(b'"optimal_assignment":[]', b'"optimal_assignment":[0,0,0,0]', 1) if n == SQ else d)
r = run_job(seq, ADD5, "add_seq")
check("filament_sequence.json with a per-filament optimal_assignment: extended by one entry", b'"optimal_assignment":[0,0,0,0,0]' in zipfile.ZipFile(r.path).read(SQ), zipfile.ZipFile(r.path).read(SQ)[:120])

# ---- refusals of a slot add ----------------------------------------------------------------------------------------------------------------------
BASE = B["split 2.8.2"]
for label, body, want in (
        ("a new slot without a preset", '[[filament]]\nslot = 5\ncolor = "#000000"\n', "give both preset and color"),
        ("a new slot without a colour", '[[filament]]\nslot = 5\npreset = "%s"\n' % PETG, "give both preset and color"),
        ("a gap (slot 6 with 4 slots)", '[[filament]]\nslot = 6\npreset = "%s"\ncolor = "#000000"\n' % PETG, "must be the next number (5)"),
        ("a preset made for another printer", '[[filament]]\nslot = 5\npreset = "Bambu PETG HF @BBL A1"\ncolor = "#000000"\n', "not made for"),
        ("an unknown preset", '[[filament]]\nslot = 5\npreset = "Bambu PETG HF @BBL P1S"\ncolor = "#000000"\n', "Closest"),
        ("add and remove in one job", ADD5 + '[[filament]]\nslot = 4\nremove = true\n', "not both"),
        ("an object on a slot that does not exist", '[[object]]\nname = "Cap"\nfilament = 6\n', "does not exist"),
        ("support_filament above the new count", ADD5 + '[settings]\nsupport_filament = 7\n', "filament slots are 0")):
    msg = refused(BASE, body)
    check("refused: %s" % label, msg and want in msg, msg)
    check("   no output file left behind (%s)" % label, not os.path.exists(os.path.join(tmp, "ref.3mf")) and not os.path.exists(os.path.join(tmp, "ref.3mf.part")))
check("an object may take the new slot in the same job (support_filament = 5 after the add)", run_job(BASE, ADD5 + '[settings]\nsupport_filament = 5\n', "add_sf").path is not None)

# ---- slot removal --------------------------------------------------------------------------------------------------------------------------------------------
for form, base in B.items():
    ver = form.split()[1]
    r = run_job(base, '[[filament]]\nslot = 4\nremove = true\n', "rm_" + form.replace(" ", "_"))
    o, i = cfg_of(r.path), cfg_of(base)
    sv = vrep(r.path, "strict")
    bad = [f for f in sv.findings if f.rule in LISTRULES and f.level in ("error", "warning")]
    check("[%s] remove the last slot (unused): 3 slots, every list cut, default 0 errors, strict has no list-size finding, post-checks pass" % form,
          len(o["filament_colour"]) == 3 and o["filament_colour"] == i["filament_colour"][:3] and len(o["flush_volumes_matrix"]) == 9 and not vrep(r.path).errors and not bad and all(c[1] for c in r.checks),
          ([c for c in r.checks if not c[1]], [str(f)[:90] for f in bad[:3]]))
    r5 = run_job(base, ADD5, "rt_add_" + form.replace(" ", "_"))
    r4 = run_job(r5.path, '[[filament]]\nslot = 5\nremove = true\n', "rt_rm_" + form.replace(" ", "_"))
    check("[%s] add a slot, then remove it: the config and model_settings come back byte for byte" % form,
          zipfile.ZipFile(r4.path).read(PS) == zipfile.ZipFile(base).read(PS) and zipfile.ZipFile(r4.path).read(MS) == zipfile.ZipFile(base).read(MS))

# in use, each kind (slot 4 of the 4-slot file)
mesh1 = "3D/Objects/object_1.model"


def ms_edit(old, new):
    return lambda n, d: d.replace(old, new, 1) if n == MS else d


def paint_edit(n, d):
    if n != mesh1:
        return d
    s = d.decode()
    i = s.index("<triangle ")
    j = s.index("/>", i)
    return (s[:j] + ' paint_color="1C"' + s[j:]).encode()


GCODE_TOOL = b'<?xml version="1.0" encoding="utf-8"?>\n<custom_gcodes_per_layer>\n<plate>\n<plate_info id="1"/>\n<layer top_z="2.12" type="2" extruder="4" color="#0A2989" extra="" gcode="T3"/>\n<mode value="MultiAsSingle"/>\n</plate>\n</custom_gcodes_per_layer>\n'
RANGES = b'<?xml version="1.0" encoding="utf-8"?>\n<objects>\n<object id="1">\n<range min_z="0" max_z="1">\n<option opt_key="extruder">4</option>\n<option opt_key="layer_height">0.2</option>\n</range>\n</object>\n</objects>\n'
cases = [
    ("a part on it", rewrite(BASE, os.path.join(tmp, "u_part.3mf"), ms_edit(b'<metadata key="name" value="Body"/>', b'<metadata key="name" value="Body"/>\n    <metadata key="extruder" value="4"/>\n    <metadata key="x" value="1"/>') if False else
                              (lambda n, d: d.replace(b'key="extruder" value="3"', b'key="extruder" value="4"') if n == MS else d)), "has this filament"),
    ("a painted area", rewrite(BASE, os.path.join(tmp, "u_paint.3mf"), paint_edit), "painted"),
    ("support_interface_filament on an object", rewrite(BASE, os.path.join(tmp, "u_objkey.3mf"), lambda n, d: d.replace(b'<metadata face_count="24"/>', b'<metadata key="support_interface_filament" value="4"/>\n    <metadata face_count="24"/>', 1) if n == MS else d), "support_interface_filament = 4"),
    ("wall_filament on a part", rewrite(BASE, os.path.join(tmp, "u_partkey.3mf"), lambda n, d: d.replace(b'<mesh_stat face_count="12"', b'<metadata key="wall_filament" value="4"/>\n      <mesh_stat face_count="12"', 1) if n == MS else d), "wall_filament = 4"),
    ("a project filament key", None, "project setting support_filament = 4"),
    ("a tool-change layer", rewrite(BASE, os.path.join(tmp, "u_tool.3mf"), extra=[(GC, GCODE_TOOL)]), "tool change"),
    ("a height range", rewrite(BASE, os.path.join(tmp, "u_range.3mf"), extra=[("Metadata/layer_config_ranges.xml", RANGES)]), "height range"),
    ("filament_sequence.json", rewrite(BASE, os.path.join(tmp, "u_seq.3mf"), lambda n, d: d.replace(b'"sequence":[]', b'"sequence":[1,4]', 1) if n == SQ else d), "filament_sequence.json"),
]
c = json.loads(zipfile.ZipFile(BASE).read(PS))
c["support_filament"] = "4"
cases[4] = (cases[4][0], rewrite(BASE, os.path.join(tmp, "u_proj.3mf"), lambda n, d: json.dumps(c, indent=4, ensure_ascii=False).encode() + b"\n" if n == PS else d), cases[4][2])
for label, base, want in cases:
    msg = refused(base, '[[filament]]\nslot = 4\nremove = true\n', "rmref")
    check("removal refused, slot in use by %s" % label, msg and "is in use" in msg and want in msg, msg)
    check("   no output file left behind (%s)" % label, not os.path.exists(os.path.join(tmp, "rmref.3mf")) and not os.path.exists(os.path.join(tmp, "rmref.3mf.part")))
msg = refused(BASE, '[[filament]]\nslot = 2\nremove = true\n', "rmmid")
check("removal refused: a slot in the middle (no reorder)", msg and "only the last slot" in msg, msg)
msg = refused(BASE, '[[filament]]\nslot = 9\nremove = true\n', "rmbig")
check("removal refused: a slot the file does not have", msg and "filament slot" in msg, msg)
msg = refused(build("oneslot", "2.8.2", nfil=1), '[[filament]]\nslot = 1\nremove = true\n', "rm1")
check("removal refused: the only slot", msg and "cannot be removed" in msg, msg)
msg = refused(BASE, '[[filament]]\nslot = 4\nremove = true\ncolor = "#000000"\n', "rmcol")
check("job refused: remove = true with a colour", msg and "cannot be combined" in msg, msg)
# the painted-area scan reads across chunk boundaries and in a non-split file
real_chunk = patch_slots.CHUNK
patch_slots.CHUNK = 7
try:
    src_ = patch_read.open_source(cases[1][1])
    check("painted area found when the attribute is cut by a read boundary (7-byte reads)", patch_slots.scan_paint(src_.pkg, 4) is True and patch_slots.scan_paint(src_.pkg, 3) is False)
finally:
    patch_slots.CHUNK = real_chunk
check("paint_states('1C') is state 4 (the encoding used by the painted fixture)", wipetower.paint_states("1C")[0] == {4}, wipetower.paint_states("1C"))
nsp = rewrite(B["nonsplit 2.8.2"], os.path.join(tmp, "u_ns_paint.3mf"), lambda n, d: (lambda s: (s[:s.index("/>", s.index("<triangle "))] + ' paint_color="1C"' + s[s.index("/>", s.index("<triangle ")):]).encode())(d.decode()) if n == M3 else d)
msg = refused(nsp, '[[filament]]\nslot = 4\nremove = true\n', "rmref_ns")
check("removal refused: painted area inside the root model of a non-split file", msg and "painted" in msg, msg)

# ---- rule 5: root-model metadata ---------------------------------------------------------------------------------------------------------------------------
def head_with(path, name, fn):
    def f(n, d):
        if n != M3:
            return d
        i = d.index(b"<resources")
        return fn(d[:i]) + d[i:]
    return rewrite(path, os.path.join(tmp, name + ".3mf"), f)


def setmeta(h, key, val):
    pat = re.compile(rb'(<metadata name="%s">)[^<]*(</metadata>)' % key.encode())
    if pat.search(h):
        return pat.sub(lambda m: m.group(1) + val.encode() + m.group(2), h, count=1)
    return h.replace(b' <metadata name="Title">', (' <metadata name="%s">%s</metadata>\n' % (key, val)).encode() + b' <metadata name="Title">', 1)


def head_of(path):
    d = zipfile.ZipFile(path).read(M3)
    return d[:d.index(b"<resources")]


def tail_of(path):
    d = zipfile.ZipFile(path).read(M3)
    return d[d.index(b"<resources"):]


STUDIO_FILLED = head_with(BASE, "r5_local", lambda h: setmeta(setmeta(setmeta(setmeta(h, "DesignerUserId", "1234567890"), "Title", "Bracket"), "License", "CC-BY-4.0"), "Region", "US"))
r = run_job(STUDIO_FILLED, '[settings]\nwall_loops = 3\n', "r5a")
h = head_of(r.path)
check("rule 5: Designer empty, DesignerUserId filled in by Studio -> DesignerUserId and Region removed; Title and License kept",
      b"DesignerUserId" not in h and b'name="Region"' not in h and b">Bracket<" in h and b">CC-BY-4.0<" in h and b'name="Designer">' in h, h)
check("   the rest of the root model (meshes) is byte for byte the input's; the output validates; the summary names the removal and the source file",
      tail_of(r.path) == tail_of(STUDIO_FILLED) and not vrep(r.path).errors and all(c[1] for c in r.checks) and any("DesignerUserId" in c for c in r.changes) and r.source == os.path.basename(STUDIO_FILLED), [c for c in r.checks if not c[1]])
check("   only the two removed lines differ in the head", len(head_of(STUDIO_FILLED)) - len(h) == len(b' <metadata name="DesignerUserId">1234567890</metadata>\n') + len(b' <metadata name="Region">US</metadata>\n'))
DESIGNER = head_with(BASE, "r5_designer", lambda h: setmeta(setmeta(setmeta(setmeta(h, "Designer", "A. Maker"), "DesignerUserId", "99887766"), "Origin", "original"), "ProfileUserId", "5551234"))
r = run_job(DESIGNER, '[settings]\nwall_loops = 3\n', "r5b")
check("rule 5: a designer is named -> DesignerUserId, Designer, Origin, ProfileUserId kept (the head is unchanged, same bytes)",
      head_of(r.path) == head_of(DESIGNER) and tail_of(r.path) == tail_of(DESIGNER) and sha(r.path, M3) == sha(DESIGNER, M3) and "Designer" in r.credit and "DesignerUserId" in r.credit, r.credit)
PATHS = head_with(BASE, "r5_path", lambda h: setmeta(setmeta(h, "LocalNoteA", "C:\\Users\\someone\\Desktop\\x.3mf"), "LocalNoteB", "made at /Users/someone/models/x.stl"))
r = run_job(PATHS, '[settings]\nwall_loops = 3\n', "r5c")
h = head_of(r.path)
check("rule 5: a metadata value with an absolute path (Windows or Unix) is removed", b"LocalNoteA" not in h and b"LocalNoteB" not in h and b"Users" not in h and b"Title" in h, h)
# D4: a path inside the designer's credit: the key stays, a warning names it
CREDPATH = head_with(BASE, "r5_credpath", lambda h: setmeta(setmeta(setmeta(h, "Designer", "A. Maker"), "Description", "made on C:\\Program Files\\Tool\\x"), "ProfileTitle", "see /home/someone/x.stl"))
r = run_job(CREDPATH, '[settings]\nwall_loops = 3\n', "r5c2")
h = head_of(r.path)
check("D4: a path inside a kept key (Description, ProfileTitle): the credit stays, byte for byte", h == head_of(CREDPATH) and sha(r.path, M3) == sha(CREDPATH, M3), h)
check("D4: ... and a warning names each key", any("Description" in w and "path" in w for w in r.warnings) and any("ProfileTitle" in w for w in r.warnings) and not any("removed" in c and "Description" in c for c in r.changes), r.warnings[:3])
u, _rm, _k = patch_slots.privacy_head(head_of(CREDPATH))
check("D4: privacy_head removes no kept key", not [x for x in _rm if x[0] in patch_slots.KEEP_KEYS] and u == head_of(CREDPATH))
UNKNOWN = head_with(BASE, "r5_unknown", lambda h: setmeta(h, "customXMLNS0:PTC_onshape_metadata", '{"a":1}'))
r = run_job(UNKNOWN, '[settings]\nwall_loops = 3\n', "r5d")
check("rule 5: a key the builder does not know (CAD exporter) is kept", b"customXMLNS0:PTC_onshape_metadata" in head_of(r.path) and sha(r.path, M3) == sha(UNKNOWN, M3))
check("rule 5: a clean head gives the same root-model bytes (no edit)", sha(run_job(BASE, '[settings]\nwall_loops = 3\n', "r5e").path, M3) == sha(BASE, M3))
SRC_PATH = rewrite(BASE, os.path.join(tmp, "r5_srcfile.3mf"), lambda n, d: d.replace(b'key="source_file" value="slab1.stl"', b'key="source_file" value="C:\\Users\\someone\\parts\\slab1.stl"') if n == MS else d)
r = run_job(SRC_PATH, '[settings]\nwall_loops = 3\n', "r5f")
check("rule 5: a source_file with a path is cut to the file name", 'key="source_file" value="slab1.stl"' in ms_of(r.path) and "Users" not in ms_of(r.path) and all(c[1] for c in r.checks), [c for c in r.checks if not c[1]])
u, _rm, kept = patch_slots.privacy_head(head_of(DESIGNER))
check("privacy_head: nothing removed, the kept list names the credit", not _rm and u == head_of(DESIGNER) and {"Designer", "Origin", "ProfileUserId"} <= set(kept), kept)
CRLF = head_with(BASE, "r5_crlf", lambda h: setmeta(h, "DesignerUserId", "42").replace(b"\n", b"\r\n"))
r = run_job(CRLF, '[settings]\nwall_loops = 3\n', "r5g")
check("rule 5 with CRLF line ends: the removed line takes its own CRLF", b"DesignerUserId" not in head_of(r.path) and head_of(r.path).count(b"\n") == head_of(r.path).count(b"\r\n") and all(c[1] for c in r.checks))

# ---- sliced input ---------------------------------------------------------------------------------------------------------------------------------------
GCODE = b"; fake gcode\nG1 X1 Y1\n"
SLICE_PLATE = b'  <plate>\n    <metadata key="index" value="1"/>\n    <metadata key="prediction" value="100"/>\n    <filament id="1" type="PLA" color="#111111" used_m="1" used_g="3"/>\n  </plate>\n'


def slice_it(src, dst):
    def fn(n, d):
        if n == MS:
            return d.replace(b'<metadata key="locked" value="false"/>', b'<metadata key="locked" value="false"/>\n    <metadata key="gcode_file" value="Metadata/plate_1.gcode"/>', 1)
        if n == SI:
            return d.replace(b"</config>", SLICE_PLATE + b"</config>")
        if n == SQ:
            return d.replace(b'"sequence":[]', b'"sequence":[1,2]', 1)
        return d
    return rewrite(src, dst, fn, extra=[("Metadata/plate_1.gcode", GCODE), ("Metadata/plate_1.gcode.md5", b"d41d8cd98f00b204e9800998ecf8427e"),
                                        ("Metadata/_rels/model_settings.config.rels", b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')])


SL = slice_it(BASE, os.path.join(tmp, "sliced.3mf"))
check("fixture: the sliced input validates with 0 errors (default mode)", not vrep(SL).errors, [str(f)[:100] for f in vrep(SL).errors])
r = run_job(SL, '[settings]\nwall_loops = 3\n', "sl1")
z = zipfile.ZipFile(r.path)
names = z.namelist()
check("sliced input: G-code, md5 and rels entries gone; the plate entry gone from slice_info.config; gcode_file key gone; sequence lists empty",
      not [n for n in names if "gcode" in n.lower() and n != GC] and "Metadata/_rels/model_settings.config.rels" not in names and b"<plate" not in z.read(SI) and b"gcode_file" not in z.read(MS)
      and b'"sequence":[1,2]' not in z.read(SQ) and b'"sequence":[]' in z.read(SQ), names)
check("   a warning says the file was sliced and must be sliced again; 0 errors; post-checks pass; the other entries keep their bytes",
      any("input was sliced" in w for w in r.warnings) and not vrep(r.path).errors and all(c[1] for c in r.checks) and sha(r.path, M3) == sha(SL, M3) and any("sliced input" in c for c in r.changes), ([c for c in r.checks if not c[1]], r.warnings[:2]))
check("   the output has the same entries as an unsliced build of the same project, in the input's order",
      [n for n in names] == [n for n in zipfile.ZipFile(SL).namelist() if n not in ("Metadata/plate_1.gcode", "Metadata/plate_1.gcode.md5", "Metadata/_rels/model_settings.config.rels")])
r = run_job(SL, ADD5, "sl2")
check("sliced input + slot add: works, 0 errors, post-checks pass", len(cfg_of(r.path)["filament_colour"]) == 5 and not vrep(r.path).errors and all(c[1] for c in r.checks), [c for c in r.checks if not c[1]])
r = run_job(SL, '[[filament]]\nslot = 4\nremove = true\n', "sl3")
check("sliced input + removal of an unused last slot: works (the old sequence is not counted, the G-code is gone)", len(cfg_of(r.path)["filament_colour"]) == 3 and not vrep(r.path).errors and all(c[1] for c in r.checks), [c for c in r.checks if not c[1]])
SL4 = slice_it(rewrite(BASE, os.path.join(tmp, "sl_use4.3mf"), lambda n, d: d.replace(b'"sequence":[]', b'"sequence":[1,4]', 1) if n == SQ else d), os.path.join(tmp, "sliced4.3mf"))
check("sliced input: a sequence naming the slot does not block the removal (it is cleared with the G-code)", refused(SL4, '[[filament]]\nslot = 4\nremove = true\n', "sl4") is None)

# ---- mutation style: each new edit path must fail the post-checks when it does something not asked ---------------------------------------------
CAP = {}
real_verify = patch_verify.verify


def spy(src, out_path, plan, input_strict, ps):
    CAP.update(src=src, plan=plan, strict=input_strict, ps=ps, path=out_path)
    return real_verify(src, out_path, plan, input_strict, ps)


def edit_zip(path, name, fn=None, drop=(), extra=()):
    p = os.path.join(tmp, "mut_%s.3mf" % name)
    return rewrite(path, p, fn, extra=extra, drop=drop)


def captured(base, body, name):
    patch_verify.verify = spy
    try:
        r = run_job(base, body, name)
    finally:
        patch_verify.verify = real_verify
    return r, dict(CAP)


def fails(mutant, cap, text):
    ck = real_verify(cap["src"], mutant, cap["plan"], cap["strict"], cap["ps"])
    return any(text in nm for nm, ok, _d in ck.failed()), [nm for nm, _o, _d in ck.failed()]


def jedit(f):
    def fn(n, d):
        if n != PS:
            return d
        c = json.loads(d)
        f(c)
        return json.dumps(c, indent=4, ensure_ascii=False).encode() + b"\n"
    return fn


rA, capA = captured(BASE, ADD5, "mA")
okA = real_verify(capA["src"], rA.path, capA["plan"], capA["strict"], capA["ps"])
check("slot add: the honest output passes every post-check", okA.ok, okA.failed())
ok_, got = fails(edit_zip(rA.path, "newval", jedit(lambda c: c["nozzle_temperature"].__setitem__(-1, "999"))), capA, "added slot(s)")
check("mutation (add): one value of the new slot is not the preset's -> post-check fails", ok_, got)
ok_, got = fails(edit_zip(rA.path, "oldval", jedit(lambda c: c["nozzle_temperature"].__setitem__(0, "999"))), capA, "every difference")
check("mutation (add): a value of an OLD slot changed -> post-check fails", ok_, got)
ok_, got = fails(edit_zip(rA.path, "flushold", jedit(lambda c: c["flush_volumes_matrix"].__setitem__(1, "1"))), capA, "every difference")
check("mutation (add): an old purge cell changed -> post-check fails", ok_, got)
ok_, got = fails(edit_zip(rA.path, "flushnew", jedit(lambda c: c["flush_volumes_matrix"].__setitem__(4, "1"))), capA, "added slot(s)")
check("mutation (add): a new purge cell is not Studio's automatic value -> post-check fails", ok_, got)
ok_, got = fails(edit_zip(rA.path, "nolist", jedit(lambda c: c["filament_type"].pop())), capA, "")
check("mutation (add): one list left one entry short -> post-checks fail (list size, validator)", bool(got), got)
ok_, got = fails(edit_zip(rA.path, "mapsshort", lambda n, d: d.replace(b'value="1 1 1 1 1"', b'value="1 1 1 1"', 1) if n == MS else d), capA, "model_settings")
check("mutation (add): a plate's filament_maps not extended -> post-check fails", ok_, got)
ok_, got = fails(edit_zip(rA.path, "extr", lambda n, d: d.replace(b'key="extruder" value="2"', b'key="extruder" value="5"', 1) if n == MS else d), capA, "model_settings")
check("mutation (add): an extruder value changed in model_settings -> post-check fails", ok_, got)
ok_, got = fails(edit_zip(rA.path, "diffshort", jedit(lambda c: c.__setitem__("inherits_group", c["inherits_group"][:-1]) if "inherits_group" in c else c.__setitem__("different_settings_to_system", c["different_settings_to_system"][:-1]))), capA, "")
check("mutation (add): a diff list one entry short -> post-checks fail", bool(got), got)
ok_, got = fails(edit_zip(rA.path, "colour", jedit(lambda c: c["filament_colour"].__setitem__(4, "#000001"))), capA, "requested value")
check("mutation (add): the new slot's colour is not the asked one -> post-check fails", ok_, got)

rR, capR = captured(BASE, '[[filament]]\nslot = 4\nremove = true\n', "mR")
ok_, got = fails(edit_zip(rR.path, "rm_extra", jedit(lambda c: c["nozzle_temperature"].__setitem__(0, "999"))), capR, "every difference")
check("mutation (remove): an old slot's value changed -> post-check fails", ok_, got)
ok_, got = fails(edit_zip(rR.path, "rm_uncut", jedit(lambda c: c["filament_type"].append("PLA"))), capR, "")
check("mutation (remove): one list not cut -> post-checks fail", bool(got), got)
ok_, got = fails(edit_zip(rR.path, "rm_ext", lambda n, d: d.replace(b'key="extruder" value="3"', b'key="extruder" value="4"', 1) if n == MS else d), capR, "model_settings")
check("mutation (remove): a part left on the removed slot -> post-check fails", ok_, got)

rP, capP = captured(STUDIO_FILLED, '[settings]\nwall_loops = 3\n', "mP")
check("rule 5: the honest output passes every post-check", real_verify(capP["src"], rP.path, capP["plan"], capP["strict"], capP["ps"]).ok)
ok_, got = fails(edit_zip(rP.path, "p_extra", lambda n, d: d.replace(b' <metadata name="Title">', b' <metadata name="Title">'.replace(b"Title", b"TitleX"), 1) if False else (d.replace(b'<metadata name="License">CC-BY-4.0</metadata>\n', b"") if n == M3 else d)), capP, "root model")
check("mutation (rule 5): an extra element removed from the head (the License) -> post-check fails", ok_, got)
ok_, got = fails(edit_zip(rP.path, "p_none", lambda n, d: d.replace(b"</resources>", b" </resources>") if n == M3 else d), capP, "root model")
check("mutation (rule 5): one byte changed after <resources -> post-check fails", ok_, got)
ok_, got = fails(edit_zip(rP.path, "p_back", lambda n, d: d.replace(b' <metadata name="Title">', b' <metadata name="DesignerUserId">1234567890</metadata>\n <metadata name="Title">', 1) if n == M3 else d), capP, "root model")
check("mutation (rule 5): the planned removal not done -> post-check fails", ok_, got)

rS, capS = captured(SL, '[settings]\nwall_loops = 3\n', "mS")
check("sliced: the honest output passes every post-check", real_verify(capS["src"], rS.path, capS["plan"], capS["strict"], capS["ps"]).ok, real_verify(capS["src"], rS.path, capS["plan"], capS["strict"], capS["ps"]).failed())
ok_, got = fails(edit_zip(rS.path, "s_gcode", extra=[("Metadata/plate_1.gcode", GCODE)]), capS, "")
check("mutation (sliced): a G-code entry left in the output -> post-checks fail", bool(got), got)
ok_, got = fails(edit_zip(rS.path, "s_si", lambda n, d: d.replace(b"</config>", SLICE_PLATE + b"</config>") if n == SI else d), capS, "slice_info")
check("mutation (sliced): a plate entry left in slice_info.config -> post-check fails", ok_, got)
ok_, got = fails(edit_zip(rS.path, "s_gf", lambda n, d: d.replace(b'<metadata key="locked" value="false"/>', b'<metadata key="locked" value="false"/>\n    <metadata key="gcode_file" value="x"/>', 1) if n == MS else d), capS, "model_settings")
check("mutation (sliced): a gcode_file key left in model_settings.config -> post-check fails", ok_, got)
ok_, got = fails(edit_zip(rS.path, "s_seq", lambda n, d: d.replace(b'"sequence":[]', b'"sequence":[1]', 1) if n == SQ else d), capS, "filament_sequence")
check("mutation (sliced): the sequence lists not emptied -> post-check fails", ok_, got)
ok_, got = fails(edit_zip(rS.path, "s_dropped", drop=(PS,)), capS, "entry list")
check("mutation (sliced): an entry that should stay is gone -> post-check fails", ok_, got)

# the whole run refuses and leaves nothing when the editor builds a wrong new slot
real_ext = patch_slots.extend_config


def bad_ext(src, ps, specs, plate_type=None):
    cfg, ch, wn = real_ext(src, ps, specs, plate_type)
    cfg["nozzle_temperature"][-1] = "777"
    return cfg, ch, wn
patch_slots.extend_config = bad_ext
try:
    msg = refused(BASE, ADD5, "mutrun")
finally:
    patch_slots.extend_config = real_ext
check("mutation, whole run: an editor that builds a wrong value for the new slot -> PatchRefused, no output, no .part",
      msg is not None and not os.path.exists(os.path.join(tmp, "mutrun.3mf")) and not os.path.exists(os.path.join(tmp, "mutrun.3mf.part")), msg)
# ---- D2: post-checks do not use the editor's own helpers ---------------------------------------------------------------------------
# an input that already has an R3 (an edited flow ratio with no diff-list entry), then a slot add whose new slot has a wrong flow ratio
r3in = edit_zip(BASE, "r3in", jedit(lambda c: c["filament_flow_ratio"].__setitem__(0, "0.93") if c["filament_flow_ratio"][0] != "0.93" else c["filament_flow_ratio"].__setitem__(0, "0.94")))
rep3 = validate._validate(r3in, False)
check("D2 setup: the input has an R3 finding and still validates (no error in default mode)",
      any(f.rule == "R3" for f in rep3.findings) and not [f for f in patch_verify.validate_both(r3in)[0] if f.level == "error"], [str(f)[:80] for f in rep3.findings][:3])
real_ext2 = patch_slots.extend_config


def flow_ext(src, ps, specs, plate_type=None):
    cfg, ch, wn = real_ext2(src, ps, specs, plate_type)
    cfg["filament_flow_ratio"][-1] = "0.5"                         # the reviewer's mutation (mut_extend.py): a wrong value in the new slot
    return cfg, ch, wn
patch_slots.extend_config = flow_ext
try:
    msg = refused(r3in, ADD5, "d2mut")
finally:
    patch_slots.extend_config = real_ext2
check("D2: new slot with a wrong flow ratio on an input that already has an R3 -> refused by the post-checks", msg is not None and "failed its own checks" in msg and not os.path.exists(os.path.join(tmp, "d2mut.3mf")), msg)
check("D2: the same job without the mutation is written", refused(r3in, ADD5, "d2ok") is None)
# the same mutation, written into an honest output: the new-slot check itself finds it (not only the strict delta)
rD, capD = captured(r3in, ADD5, "d2cap")
ok_, got = fails(edit_zip(rD.path, "d2flow", jedit(lambda c: c["filament_flow_ratio"].__setitem__(-1, "0.5"))), capD, "added slot(s): every list holds")
check("D2: the new-slot check rebuilt from the preset finds a wrong value of the new slot", ok_, got)
# keyed strict delta: same rule, same where, one more key
F = validate.Finding
a = F("R3", "warning", "project_settings.config", "x", "CHANGE", keys=["filament_flow_ratio (filament 1)"])
b = F("R3", "warning", "project_settings.config", "x", "CHANGE", keys=["filament_flow_ratio (filament 1)", "filament_flow_ratio (filament 5)"])
c = F("R3", "warning", "project_settings.config", "x", "CHANGE", keys=["filament_flow_ratio (filament 1)"])
check("D2: strict delta is keyed by setting key (a new key under an existing R3 is new; the same key is not)", len(patch_verify.new_findings([a], [b])) == 1 and not patch_verify.new_findings([a], [c]))
# slice_info and the sequence are compared with the input, worked out again
real_strip = patch_xml.strip_slice_plates


def bad_strip(data):
    out, n = real_strip(data)
    return out.replace(b"</config>", b"  <extra_header key=\"x\" value=\"1\"/>\n</config>"), n          # the editor's bytes and the output agree; the input does not
patch_xml.strip_slice_plates = bad_strip
try:
    msg = refused(SL, '[settings]\nwall_loops = 3\n', "d2si")
finally:
    patch_xml.strip_slice_plates = real_strip
check("D2: slice_info.config is compared with the input, not with the editor's bytes (an extra element passes the old check, fails now)", msg is not None and "slice_info" in msg, msg)
real_seq = patch_slots.sequence_edit


def bad_seq(raw, n, add=0, clear=False):
    return b'{"x":{"sequence":[]}}'
patch_slots.sequence_edit = bad_seq
try:
    msg = refused(SL, '[settings]\nwall_loops = 3\n', "d2sq")
finally:
    patch_slots.sequence_edit = real_seq
check("D2: filament_sequence.json is compared with the input worked out again, not with the editor's bytes", msg is not None and "filament_sequence" in msg, msg)
# ---- D6: the plate print-order lists follow Studio's rule (PartPlate.cpp, both tags) --------------------------------------------------------
def with_plate_lists(src, name, first, other, nums):
    def fn(n, d):
        if n != MS:
            return d
        t = d.decode()
        add = '    <metadata key="first_layer_print_sequence" value="%s"/>\n    <metadata key="other_layers_print_sequence" value="%s"/>\n    <metadata key="other_layers_print_sequence_nums" value="%s"/>\n' % (first, other, nums)
        return t.replace("  </plate>", add + "  </plate>").encode()
    return rewrite(src, os.path.join(tmp, name + ".3mf"), fn)


def plate_vals(path, key):
    return re.findall(r'<metadata key="%s" value="([^"]*)"' % key, ms_of(path))


for tag in ("2.8.2", "2.9.1"):
    PSQ = with_plate_lists(B["split " + tag], "d6_" + tag.replace(".", ""), "2 1 3 4", "2 4 1 2 3 4 5 9 4 3 2 1", "2")
    check("D6 [%s] fixture validates (0 errors)" % tag, not vrep(PSQ).errors, [str(f)[:90] for f in vrep(PSQ).errors][:3])
    r = run_job(PSQ, ADD5, "d6add")
    check("D6 [%s] slot add: first_layer_print_sequence gets the new slot, every group of other_layers_print_sequence too, _nums stays" % tag,
          set(plate_vals(r.path, "first_layer_print_sequence")) == {"2 1 3 4 5"} and set(plate_vals(r.path, "other_layers_print_sequence")) == {"2 4 1 2 3 4 5 5 9 4 3 2 1 5"}
          and set(plate_vals(r.path, "other_layers_print_sequence_nums")) == {"2"} and all(c[1] for c in r.checks), (plate_vals(r.path, "first_layer_print_sequence"), plate_vals(r.path, "other_layers_print_sequence"), [c for c in r.checks if not c[1]]))
    r = run_job(PSQ, '[[filament]]\nslot = 4\nremove = true\n', "d6rm")
    check("D6 [%s] last-slot removal: the removed slot leaves every list" % tag,
          set(plate_vals(r.path, "first_layer_print_sequence")) == {"2 1 3"} and set(plate_vals(r.path, "other_layers_print_sequence")) == {"2 4 1 2 3 5 9 3 2 1"} and all(c[1] for c in r.checks),
          (plate_vals(r.path, "first_layer_print_sequence"), plate_vals(r.path, "other_layers_print_sequence"), [c for c in r.checks if not c[1]]))
PSQ0 = with_plate_lists(B["split 2.8.2"], "d6_zero", "0", "2 4 1 2 3 4", "1")
r = run_job(PSQ0, ADD5, "d6zero")
check("D6: a first_layer_print_sequence of 0 (automatic) stays 0", set(plate_vals(r.path, "first_layer_print_sequence")) == {"0"}, plate_vals(r.path, "first_layer_print_sequence"))
PSQB = with_plate_lists(B["split 2.8.2"], "d6_bad", "2 1 3 4", "2 4 1 2 3", "2")
r = run_job(PSQB, ADD5, "d6bad")
check("D6: an other_layers_print_sequence that is not in Studio's form is left and a warning says so", set(plate_vals(r.path, "other_layers_print_sequence")) == {"2 4 1 2 3"} and any("other_layers_print_sequence" in w for w in r.warnings), r.warnings[:3])
rD6, capD6 = captured(PSQ, ADD5, "d6cap")
ok_, got = fails(edit_zip(rD6.path, "d6mut", lambda n, d: d.replace(b'value="2 1 3 4 5"', b'value="2 1 3 4"') if n == MS else d), capD6, "model_settings.config")
check("D6: mutation: the first-layer list not extended -> post-check fails", ok_, got)

real_priv = patch_slots.privacy_head


def bad_priv(head):
    h2, rm, kept = real_priv(head)
    return h2.replace(b' <metadata name="Title">', b' <metadata name="Titl">', 1), rm, kept
patch_slots.privacy_head = bad_priv
try:
    msg = refused(STUDIO_FILLED, '[settings]\nwall_loops = 3\n', "mutrun2")
finally:
    patch_slots.privacy_head = real_priv
check("mutation, whole run: a privacy edit that changes another line -> PatchRefused", msg and "failed its own checks" in msg, msg)

# ---- the command line --------------------------------------------------------------------------------------------------------------------------------------------
import subprocess
jp = os.path.join(tmp, "cli.toml")
open(jp, "w").write('patch = "%s"\noutput = "cliout.3mf"\n%s' % (STUDIO_FILLED, ADD5))
rc = subprocess.run([sys.executable, "-I", os.path.join(H, "..", "scripts", "build_bambu_3mf.py"), jp], capture_output=True, text=True)
sm = open(os.path.join(tmp, "cliout.summary.md")).read() if os.path.exists(os.path.join(tmp, "cliout.summary.md")) else ""
check("command line: slot add + rule 5 in one job; the summary file names the source file, the slot and the removed id; no id value is printed",
      rc.returncode == 0 and os.path.basename(STUDIO_FILLED) in sm and "slot 5 added" in sm and "DesignerUserId" in sm and "1234567890" not in sm and "1234567890" not in rc.stdout, (rc.stdout[-300:], rc.stderr[-300:]))

print("\n%d failed" % len(failed) if failed else "\nall passed")
sys.exit(1 if failed else 0)
