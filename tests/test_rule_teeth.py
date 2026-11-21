#!/usr/bin/env python3
"""A broken file for every validator rule and guard the design review found weak. Each case breaks ONE thing in a
known-good file and the named rule must fire; if a rule (or a finer part of it) is disabled, its case fails.
The last part disables each rule in turn and checks that some case then fails. Run: python3 -I tests/test_rule_teeth.py"""
import io, json, os, re, sys, tempfile, zipfile
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import validate, package, wipetower  # noqa: E402
from bambu3mf.job import load_job  # noqa: E402

tmp = tempfile.mkdtemp()
GOOD = os.path.join(tmp, "good.3mf")
package.build(load_job(os.path.join(H, "jobs", "four_color_282.toml")), GOOD)
GOODT = os.path.join(tmp, "good_tower.3mf")      # same, with smooth timelapse: the plate has a prime tower (Print::has_wipe_tower)
package.build(load_job(os.path.join(H, "jobs", "four_color_tower_282.toml")), GOODT)
LISTS = os.path.join(tmp, "lists.3mf")           # a file with different_settings_to_system / inherits_group (job settings present)
package.build(load_job(os.path.join(H, "data", "mutation_base.toml")), LISTS)
sys.path.insert(0, H)
from fixture_lists import add_inherits_group  # noqa: E402
add_inherits_group(LISTS)
PAUSE = os.path.join(tmp, "pause.3mf")           # a file with custom_gcode_per_layer.xml
package.build(load_job(os.path.join(H, "..", "examples", "pause.toml")), PAUSE)

PS, MS, M3, OBJ, GC = ("Metadata/project_settings.config", "Metadata/model_settings.config", "3D/3dmodel.model",
                       "3D/Objects/object_1.model", "Metadata/custom_gcode_per_layer.xml")


def make(src, edits=None, extra=(), drop=(), name="t.3mf", infos=None):
    zin = zipfile.ZipFile(src)
    p = os.path.join(tmp, name)
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zo:
        for i in zin.infolist():
            if i.filename in drop:
                continue
            d = zin.read(i.filename)
            if edits and i.filename in edits:
                d = edits[i.filename](d)
            zo.writestr(i.filename, d)
        for n, d in extra:
            zo.writestr(n, d)
    return p


def cfg(fn):
    def go(b):
        c = json.loads(b)
        fn(c)
        return json.dumps(c, indent=4).encode()
    return go


def sub(pat, rep, count=1, flags=0):
    return lambda b: re.sub(pat, rep, b.decode(), count=count, flags=flags).encode()


def rewrite_str(old, new):
    def go(b):
        t = b.decode()
        assert old in t, "test needs %r" % old
        return t.replace(old, new, 1).encode()
    return go


def setk(k, v):
    return cfg(lambda c: c.__setitem__(k, v))


def short(k):
    return cfg(lambda c: c.__setitem__(k, c[k][:-1]))


fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)) if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


# ---- the cases: (label, rule, level, builder of the broken file) ---------------------------------------------------------
CASES = []


def case(label, rule, level="error"):
    def deco(fn):
        CASES.append((label, rule, level, fn))
        return fn
    return deco


def c_(label, rule, src, edits=None, level="error", **kw):
    CASES.append((label, rule, level, lambda s=src, e=edits, k=kw: make(s, e, **k)))


# R1 Application string
c_("R1 Application with trailing text", "R1", GOOD, {M3: rewrite_str("BambuStudio-02.08.02.61<", "BambuStudio-02.08.02.61 x<")})
c_("R1 Application without a version", "R1", GOOD, {M3: rewrite_str("BambuStudio-02.08.02.61<", "BambuStudio-<")})
c_("R1 Application with a prefix", "R1", GOOD, {M3: rewrite_str(">BambuStudio-02", ">Xx BambuStudio-02")})
# R3 diff lists: filament and printer paths
c_("R3 filament override not listed in any slot", "R3", LISTS, {PS: cfg(lambda c: c["filament_max_volumetric_speed"].__setitem__(0, "3"))})
c_("R3 filament override listed only in the other slot", "R3", LISTS, {PS: cfg(lambda c: (c["filament_max_volumetric_speed"].__setitem__(0, "3"),
    c["different_settings_to_system"].__setitem__(2, "filament_max_volumetric_speed")))})
c_("R3 printer override not listed", "R3", LISTS, {PS: cfg(lambda c: c.__setitem__("retract_length_toolchange", ["9"]))})
c_("R3 process override not listed", "R3", LISTS, {PS: cfg(lambda c: c["different_settings_to_system"].__setitem__(0, "wall_loops"))})
# R5 / R6 / R8 list lengths
c_("R5 filament_settings_id one entry short", "R5", GOOD, {PS: short("filament_settings_id")})
c_("R5 per-filament list one entry short", "R5", GOOD, {PS: short("filament_type")})
c_("R6 variant list one entry short", "R6", GOOD, {PS: short("nozzle_temperature")})
c_("R8 print/printer variant list too short", "R8", GOOD, {PS: setk("bridge_speed", ["50", "50", "50"])})
c_("R8 print/printer variant list too long", "R8", GOOD, {PS: setk("bridge_speed", ["50"] * 5)})
# R9 printer identity
c_("R9 printer_model not a Bambu printer", "R9", GOOD, {PS: setk("printer_model", "Prusa MK4")})
c_("R9 printer_model of another Studio version table", "R9", GOOD, {PS: cfg(lambda c: (c.__setitem__("printer_model", "Prusa MK4"), c.__setitem__("version", "99.0.0.0")))})
c_("R9 printer_model differs from the printer preset", "R9", GOOD, {PS: setk("printer_model", "Bambu Lab A1")})
# R10 presets made for another printer
c_("R10 filament made for another printer", "R10", GOOD, {PS: cfg(lambda c: c["filament_settings_id"].__setitem__(0, "Bambu PLA Basic @BBL X1C"))})
c_("R10 process made for another printer", "R10", GOOD, {PS: setk("print_settings_id", "0.20mm Standard @BBL A1")})
c_("R10 printer settings id not a printer preset", "R10", GOOD, {PS: setk("printer_settings_id", "Not a printer")}, level="any")
# R11 diff list lengths
c_("R11 inherits_group length not n + 2", "R11", LISTS, {PS: short("inherits_group")})
c_("R11 list length not n + 2", "R11", LISTS, {PS: short("different_settings_to_system")})
# R12 filament ids
c_("R12 filament_ids one entry short", "R12", GOOD, {PS: short("filament_ids")})
c_("R12 filament id differs from the preset", "R12", GOOD, {PS: cfg(lambda c: c["filament_ids"].__setitem__(0, "GFZ99"))})
# R14 flush
c_("R14 matrix of the wrong size", "R14", GOOD, {PS: short("flush_volumes_matrix")})
c_("R14 matrix diagonal not zero", "R14", GOOD, {PS: cfg(lambda c: c["flush_volumes_matrix"].__setitem__(0, "5"))})
c_("R14 flush_volumes_vector of the wrong size", "R14", GOOD, {PS: short("flush_volumes_vector")})
c_("R14 flush_multiplier of the wrong size", "R14", GOOD, {PS: setk("flush_multiplier", ["1", "1"])})
# R15 full config
c_("R15 config is not an object", "R15", GOOD, {PS: lambda b: b"[1, 2, 3]"})
c_("R15 a setting missing from the flattened config", "R15", GOOD, {PS: cfg(lambda c: c.pop("wall_loops"))})
# R16 values
c_("R16 value out of range", "R16", GOOD, {PS: setk("sparse_infill_density", "250%")})
c_("R16 enum value not allowed", "R16", GOOD, {PS: setk("sparse_infill_pattern", "not_a_pattern")})
c_("R16 unknown setting", "R16", GOOD, {PS: setk("no_such_setting", "1")})
c_("R16 a bad entry past the first slot of a list", "R16", GOOD, {PS: cfg(lambda c: c.__setitem__("filament_flow_ratio", ["0.98", "0.98", "0.98", "x"]))})
# R17 bed type and wipe tower positions
c_("R17 bed type not of this version", "R17", GOOD, {PS: setk("curr_bed_type", "Gold Plate")})
c_("R17 wipe_tower_x not one per plate", "R17", GOOD, {PS: setk("wipe_tower_x", ["165", "165"])})
c_("R17 wipe_tower_y not one per plate", "R17", GOOD, {PS: setk("wipe_tower_y", [])})
# R22 UUID scheme (warnings)
c_("R22 object UUID not Studio's scheme", "R22", GOOD, {M3: sub(r'(<object id="2" p:UUID=")[^"]+', r"\g<1>11111111-2222-3333-4444-555555555555")}, level="warning")
c_("R22 component UUID not Studio's scheme", "R22", GOOD, {M3: sub(r'(<component [^>]*p:UUID=")[^"]+', r"\g<1>11111111-2222-3333-4444-555555555555")}, level="warning")
c_("R22 mesh UUID not Studio's scheme", "R22", GOOD, {OBJ: sub(r'(<object id="1" p:UUID=")[^"]+', r"\g<1>11111111-2222-3333-4444-555555555555")}, level="warning")
c_("R22 object ids repeated", "R22", GOOD, {M3: sub(r"(  <object id=\"2\".*?</object>\n)", r"\1\1", flags=re.S)})
# R23 build items
c_("R23 object partly outside the plate", "R23", GOOD, {M3: sub(r'(transform="1 0 0 0 1 0 0 0 1 )[^ ]+ ', r"\g<1>500 ", flags=0)})
c_("R23 object below the plate", "R23", GOOD, {M3: sub(r'(transform="1 0 0 0 1 0 0 0 1 128 128 )[^"]+', r"\g<1>5")})
c_("R23 build item without printable", "R23", GOOD, {M3: rewrite_str(' printable="1"', "")})
c_("R23 build item of a missing object", "R23", GOOD, {M3: rewrite_str('<item objectid="2"', '<item objectid="9"')})
# R24 meshes
c_("R24 mesh without triangles", "R24", GOOD, {OBJ: sub(r"<triangles>.*</triangles>", "<triangles></triangles>", flags=re.S)})
c_("R24 triangle index past the vertex list", "R24", GOOD, {OBJ: sub(r'v1="\d+"', 'v1="9999"')})
c_("R24 inside-out mesh", "R24", GOOD, {OBJ: sub(r'(<triangle v1=")(\d+)(" v2=")(\d+)(")', r"\g<1>\4\3\2\5", count=0)})
c_("R24 mesh type does not fit the part subtype", "R24", GOOD, {OBJ: rewrite_str('type="model"', 'type="other"')})
c_("R24 open surface (one triangle dropped)", "R24", GOOD, {OBJ: sub(r"<triangle [^>]*/>\s*", "", count=1)})
c_("R24 NaN coordinate", "R24", GOOD, {OBJ: sub(r'x="-10"', 'x="nan"')})
# R27 extruder slots
c_("R27 object extruder not a slot", "R27", GOOD, {MS: sub(r'(<object id="2">\s*<metadata key="name" value="Cube"/>\s*<metadata key="extruder" value=")1', r"\g<1>9")})
c_("R27 part extruder not a slot", "R27", GOOD, {MS: sub(r'(<metadata key="extruder" value=")1("/>\s*<mesh_stat)', r"\g<1>9\2")})
c_("R27 part extruder with a Unicode digit", "R27", GOOD, {MS: sub(r'(<metadata key="extruder" value=")1("/>\s*<mesh_stat)', "\\g<1>\u0661\\2")})
# R28 per-object / per-part overrides
OBJ_OVR = lambda k, v: {MS: rewrite_str('<metadata face_count="12"/>', '<metadata key="%s" value="%s"/>\n    <metadata face_count="12"/>' % (k, v))}
c_("R28 override that is not a Studio setting", "R28", GOOD, OBJ_OVR("no_such_setting", "1"))
c_("R28 override that is not a process setting", "R28", GOOD, OBJ_OVR("nozzle_temperature", "200"))
c_("R28 override with a bad value", "R28", GOOD, OBJ_OVR("wall_loops", "abc"))
# R30 plates
c_("R30 plater_id not 1", "R30", GOOD, {MS: rewrite_str('key="plater_id" value="1"', 'key="plater_id" value="2"')})
TWO = os.path.join(tmp, "two_objects.3mf")      # two objects, two instances
package.build(load_job(os.path.join(H, "..", "examples", "per_object_settings.toml")), TWO)
c_("R30 identify_id repeated", "R30", TWO, {MS: sub(r'key="identify_id" value="\d+"', 'key="identify_id" value="101"', count=0)})
c_("R30 identify_id zero", "R30", GOOD, {MS: rewrite_str('key="identify_id" value="101"', 'key="identify_id" value="0"')})
c_("R30 identify_id with a Unicode digit", "R30", GOOD, {MS: rewrite_str('key="identify_id" value="101"', 'key="identify_id" value="\u0661\u0660\u0661"')})
c_("R30 filament_maps of the wrong length", "R30", GOOD, {MS: rewrite_str('key="filament_maps" value="1 1 1 1"', 'key="filament_maps" value="1 1"')})
c_("R30 instance missing", "R30", GOOD, {MS: sub(r"<model_instance>.*?</model_instance>", "", flags=re.S)})
# R32 custom G-code per layer
c_("R32 layer type missing", "R32", PAUSE, {GC: sub(r' type="\d+"', "")})
c_("R32 layer top_z not a number", "R32", PAUSE, {GC: sub(r'top_z="[^"]+"', 'top_z="nan"')})
c_("R32 layer top_z not on a layer boundary", "R32", PAUSE, {GC: sub(r'top_z="[^"]+"', 'top_z="3.17"')})
c_("R32 layer top_z above the model", "R32", PAUSE, {GC: sub(r'top_z="[^"]+"', 'top_z="900"')})
c_("R32 plate_info id not a plate", "R32", PAUSE, {GC: sub(r'(<plate_info id=")\d+', r"\g<1>7")})
c_("R32 mode not allowed", "R32", PAUSE, {GC: sub(r'(<mode value=")[^"]+', r"\g<1>Duplex")})
# R33 personal data
c_("R33 absolute path", "R33", GOOD, extra=[("Metadata/note.txt", b"saved from /Users/someone/Documents/model.3mf")], level="warning")
c_("R33 Windows path", "R33", GOOD, extra=[("Metadata/note.txt", b"C:\\Users\\someone\\model.3mf")], level="warning")
c_("R33 e-mail address", "R33", GOOD, extra=[("Metadata/note.txt", b"by someone@example.com")], level="warning")
c_("R33 IP address", "R33", GOOD, extra=[("Metadata/note.txt", b"printer at 192.168.1.50")], level="warning")
c_("R33 printer host key set", "R33", GOOD, {PS: setk("print_host", "printer.local")}, level="warning")
c_("R33 account id in the root model", "R33", GOOD, {M3: rewrite_str('name="DesignerUserId"></metadata>', 'name="DesignerUserId">123456</metadata>')}, level="warning")
# R38 variant keys
c_("R38 variant id differs from the system preset", "R38", LISTS, {PS: setk("printer_extruder_id", ["2"])})


def zip_with(entries, name, flag=0, extra_field=b"", comment=b""):
    p = os.path.join(tmp, name)
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zo:
        for n, d in entries:
            zi = zipfile.ZipInfo(n, (2024, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.flag_bits |= flag
            zi.extra = extra_field
            zi.comment = comment
            zo.writestr(zi, d)
    return p


def good_entries(skip=()):
    z = zipfile.ZipFile(GOOD)
    return [(i.filename, z.read(i.filename)) for i in z.infolist() if i.filename not in skip]


# R33 ZIP extra field
CASES.append(("R33 ZIP entry with an extra field", "R33", "warning", lambda: zip_with(good_entries(), "extra.3mf", extra_field=b"\x99\x99\x04\x00abcd")))
# G1
CASES.append(("G1 not a ZIP file", "G1", "error", lambda: _bytes(b"hello", "nz.3mf")))
def _encrypted():
    p = zip_with(good_entries(), "enc.3mf")
    b = bytearray(open(p, "rb").read())
    for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):          # general purpose bit 0 = encrypted, local header and central directory
        i = bytes(b).find(sig)
        b[i + off] |= 1
    open(p, "wb").write(bytes(b))
    return p
CASES.append(("G1 encrypted entry", "G1", "error", _encrypted))
# G2
CASES.append(("G2 more entries than the limit", "G2", "error", lambda: zip_with(good_entries() + [("Metadata/f%d.txt" % i, b"") for i in range(validate.MAX_ENTRIES + 1)], "many.3mf")))
# G2 boundary: exactly the limit is allowed
_n = len(good_entries())
_p = zip_with(good_entries() + [("Metadata/f%d.txt" % i, b"") for i in range(validate.MAX_ENTRIES - _n)], "limit.3mf")
check("G2 exactly %d entries is not refused" % validate.MAX_ENTRIES, "G2" not in [f.rule for f in validate.validate(_p, mode="strict", privacy=True).findings])
# G3
CASES.append(("G3 entry name with ..", "G3", "error", lambda: zip_with(good_entries() + [("a/../b.txt", b"x")], "dots.3mf")))
CASES.append(("G3 absolute entry name", "G3", "error", lambda: zip_with(good_entries() + [("/etc/x.txt", b"x")], "abs.3mf")))
CASES.append(("G3 backslash in the entry name", "G3", "error", lambda: zip_with(good_entries() + [("a\\b.txt", b"x")], "bs.3mf")))
CASES.append(("G3 duplicate entry name in other case", "G3", "error", lambda: zip_with(good_entries() + [("metadata/PROJECT_SETTINGS.CONFIG", b"x")], "dup.3mf")))
# G4 ratio only (the file is small, so no other guard fires)
CASES.append(("G4 compression ratio above the limit", "G4", "error", lambda: zip_with(good_entries() + [("Metadata/pad.txt", b" " * (8 * 1024 ** 2))], "ratio.3mf")))
# G6
CASES.append(("G6 DOCTYPE in a sub-model after a long comment", "G6", "error",
              lambda: make(GOOD, {OBJ: lambda b: b.replace(b"<model", b"<!-- " + b"x" * 6000 + b" --><!DOCTYPE m [<!ENTITY a 'b'>]><model", 1)})))
CASES.append(("G6 ENTITY in model_settings.config", "G6", "error",
              lambda: make(GOOD, {MS: lambda b: b"<?xml version='1.0'?><!DOCTYPE c [<!ENTITY a 'aaaa'>]>" + b.split(b"?>", 1)[1]})))


def _bytes(b, name):
    p = os.path.join(tmp, name)
    open(p, "wb").write(b)
    return p


def fired(path, rule, level):
    r = validate.validate(path, mode="strict", privacy=True)
    hit = [f for f in r.findings if f.rule == rule and (level == "any" or f.level == level)]
    return bool(hit), r



# ---- review 2 re-check: finer mutants that survived, and non-finite values -------------------------------------------------
def _two(c):
    c["nozzle_diameter"] = ["0.4", "0.4"]


def _long(k):
    return cfg(lambda c: c.__setitem__(k, c[k] + [c[k][-1]]))
c_("R4 filament_colour entry too short (#12345)", "R4", GOOD, {PS: cfg(lambda c: c["filament_colour"].__setitem__(0, "#12345"))})
c_("R5 filament_settings_id one entry too long", "R5", GOOD, {PS: _long("filament_settings_id")})
c_("R5 per-filament list one entry too long", "R5", GOOD, {PS: _long("filament_type")})
c_("R12 filament_ids one entry too long", "R12", GOOD, {PS: _long("filament_ids")})
c_("R14 flush_volumes_vector one entry too long", "R14", GOOD, {PS: _long("flush_volumes_vector")})
c_("R16 value below the minimum", "R16", GOOD, {PS: setk("wall_loops", "-1")})
c_("R16 NaN value", "R16", GOOD, {PS: setk("layer_height", "nan")})
c_("R16 infinite value", "R16", GOOD, {PS: setk("layer_height", "inf")})
c_("R16 NaN percent", "R16", GOOD, {PS: setk("sparse_infill_density", "nan%")})
c_("R16 NaN wipe tower position", "R16", GOOD, {PS: setk("wipe_tower_x", ["nan"])})
c_("R37 NaN wipe tower position", "R37", GOODT, {PS: setk("wipe_tower_x", ["nan"])})
c_("R32 pause on a filament slot above n", "R32", PAUSE, {GC: sub(r'extruder="\d+"', 'extruder="9"')})
c_("R32 custom G-code layer with empty extra", "R32", PAUSE, {GC: lambda b: re.sub(r'type="\d+"', 'type="4"', re.sub(r'extra="[^"]*"', 'extra=" "', b.decode()), count=1).encode()})
c_("R33 path in a plain project setting", "R33", GOOD, {PS: setk("bed_custom_texture", "/Users/someone/texture.png")}, level="warning")
c_("R36 filament_map entry 0", "R36", GOOD, {PS: cfg(lambda c: (_two(c), c.__setitem__("extruder_type", ["0", "0"]), c.__setitem__("filament_map", ["0", "1", "1", "1"])))})
_c = json.loads(zipfile.ZipFile(GOODT).read(PS))
_tb = wipetower.tower_boxes(_c, len(_c["filament_colour"]), 20.0, (0, 0, 256, 256), 1)[0]
_x = _tb[0] + 0.5 - 10.0                        # cube20 right edge 0.5 mm into the saved tower box
_y = (_tb[1] + _tb[3]) / 2
c_("R37 part 0.5 mm into the tower", "R37", GOODT, {M3: sub(r'(<item [^>]*transform="1 0 0 0 1 0 0 0 1 )[^ ]+ [^ ]+ ', r"\g<1>%.3f %.3f " % (_x, _y))})
_w0 = wipetower.estimate_size(_c, 1, 20.0)[0]                  # the plate prints with one filament; Studio sizes the tower for that
c_("R37 saved tower 2 mm off the bed", "R37", GOODT, {PS: setk("wipe_tower_x", ["%.3f" % (256 + 2 - _w0)])})
# R39 bed temperature 0 for the plate type of the file (Cool Plate in GOOD), on a used filament
c_("R39 bed temperature 0 on the selected plate", "R39", GOOD, {PS: setk("cool_plate_temp", ["0", "0", "0", "0"])})

# R40 height ranges (Metadata/layer_config_ranges.xml)
_RG = "Metadata/layer_config_ranges.xml"
def _rg(body):
    return ('<?xml version="1.0" encoding="utf-8"?>\n<objects>\n <object id="1">\n%s </object>\n</objects>\n' % body).encode()
c_("R40 range without layer_height", "R40", GOOD, extra=[(_RG, _rg('  <range min_z="0" max_z="2">\n   <option opt_key="extruder">0</option>\n  </range>\n'))])
c_("R40 layer height above the nozzle limit", "R40", GOOD, extra=[(_RG, _rg('  <range min_z="0" max_z="2">\n   <option opt_key="layer_height">0.5</option>\n  </range>\n'))])
c_("R40 overlapping ranges", "R40", GOOD, extra=[(_RG, _rg('  <range min_z="0" max_z="3">\n   <option opt_key="layer_height">0.12</option>\n  </range>\n'
                                                       '  <range min_z="2" max_z="4">\n   <option opt_key="layer_height">0.16</option>\n  </range>\n'))])
c_("R40 object id past the model", "R40", GOOD, extra=[(_RG, _rg('  <range min_z="0" max_z="2">\n   <option opt_key="layer_height">0.12</option>\n  </range>\n').replace(b'id="1"', b'id="9"'))])


for label, rule, level, build in CASES:
    p = build()
    ok, r = fired(p, rule, level)
    check("%s -> %s" % (label, rule), ok, [str(f)[:90] for f in r.findings][:3])

# guards whose limits are too big to build a real file for: the module constants are lowered for the call
saved = (validate.MAX_TOTAL, validate.MAX_VERTICES)
try:
    validate.MAX_TOTAL = 50 * 1024
    ok, r = fired(GOOD, "G4", "error")
    check("G4 total uncompressed size above the limit -> G4", ok)
    check("G4 total size: nothing is read after the limit (no other rule runs)", [f.rule for f in r.findings] == ["G4"], [str(f)[:80] for f in r.findings][:3])
    validate.MAX_TOTAL = saved[0]
    validate.MAX_VERTICES = 5
    ok, r = fired(GOOD, "G5", "error")
    check("G5 more vertices than the limit -> G5", ok)
    check("G5 vertex cap does not trip G4", "G4" not in [f.rule for f in r.findings])
finally:
    validate.MAX_TOTAL, validate.MAX_VERTICES = saved
# G5 entry size: a text entry above MAX_XML (64 MiB) that is not a bomb (ratio stays below 1000)
import base64  # noqa: E402
block = base64.b64encode(os.urandom(3000))
body = b'{"x": "' + block * (66 * 1024 ** 2 // len(block)) + b'"}'
p = zip_with(good_entries(skip=(PS,)) + [(PS, body)], "g5.3mf")
r = validate.validate(p, mode="strict", privacy=True)
rules = [f.rule for f in r.errors]
check("G5 entry above the size limit -> G5 (and not G4)", "G5" in rules and "G4" not in rules, rules)

# R36 multi-nozzle basics
def two_nozzles(c):
    c["nozzle_diameter"] = ["0.4", "0.4"]
c_("R36 two nozzles but one extruder_type", "R36", GOOD, {PS: cfg(two_nozzles)})
c_("R36 filament_map entry outside 1..nozzles", "R36", GOOD, {PS: cfg(lambda c: (two_nozzles(c), c.__setitem__("extruder_type", ["0", "0"]), c.__setitem__("filament_map", ["1", "1", "1", "3"])))})
c_("R36 extruder_nozzle_stats not one per nozzle", "R36", GOOD, {PS: cfg(lambda c: (two_nozzles(c), c.__setitem__("extruder_type", ["0", "0"]), c.__setitem__("filament_map", ["1", "1", "1", "1"]), c.__setitem__("extruder_nozzle_stats", ["Standard#1"])))})
CASES_36 = CASES[-3:]
for label, rule, level, build in CASES_36:
    ok, r = fired(build(), rule, level)
    check("%s -> %s" % (label, rule), ok, [str(f)[:90] for f in r.findings][:3])

# R37 sizing: the reserved box is the conservative one (never smaller than the estimate for the real prime volume)
cfgd = json.loads(zipfile.ZipFile(GOOD).read(PS))
n = len(cfgd["filament_colour"])
w0, d0 = wipetower.estimate_size(cfgd, n, 20.0, conservative=False)
w1, d1 = wipetower.estimate_size(cfgd, n, 20.0, conservative=True)
bx = wipetower.tower_boxes(cfgd, n, 20.0, (0, 0, 256, 256), 1)[0]
check("R37 reserved box is at least the estimate for the file's own prime volume", w1 >= w0 and d1 >= d0)
check("R37 reserved box has the conservative size", abs((bx[2] - bx[0]) - w1) < 1e-6 and abs((bx[3] - bx[1]) - d1) < 1e-6, (bx, w1, d1))

# ---- disabling a rule makes its cases fail --------------------------------------------------------------------------------
RULES = sorted({c[1] for c in CASES} | {"G4", "G5", "R36", "R37"}, key=lambda r: (r[0], int(r[1:])))
orig_err, orig_warn = validate.Report.err, validate.Report.warn
for rule in RULES:
    mine = [c for c in CASES if c[1] == rule]
    if not mine:
        continue

    def off(self, r, where, msg, *a, _o=orig_err, **kw):
        if r != rule:
            _o(self, r, where, msg, *a, **kw)

    def offw(self, r, where, msg, *a, _o=orig_warn, **kw):
        if r != rule:
            _o(self, r, where, msg, *a, **kw)
    validate.Report.err, validate.Report.warn = off, offw
    try:
        still = [c[0] for c in mine if fired(c[3](), rule, c[2])[0]]
    finally:
        validate.Report.err, validate.Report.warn = orig_err, orig_warn
    check("with %s disabled, its %d case(s) fail" % (rule, len(mine)), not still, still)

# every rule the rule index promises has a case
doc = open(os.path.join(H, "..", "references", "rules.md")).read()
promised = {m for m in re.findall(r"\b(R\d+|G\d)\b", doc)}
missing = sorted(promised - {c[1] for c in CASES} - {"G4", "G5", "R36", "R37"} - {"R2", "R4", "R6", "R7", "R13", "R18", "R19", "R20", "R21", "R25", "R26", "R29", "R31", "R34", "R35", "R37"},
                 key=lambda r: (r[0], int(r[1:])))
check("every rule in references/rules.md has a case here or in test_validate/test_teeth/test_hostile", not missing, missing)

print("\n%d failed" % len(fails))
sys.exit(1 if fails else 0)
