"""Static validator for Bambu Studio project 3MF files. Works on any 3MF, ours or Studio's.

Every finding carries a rule id from references/rules.md (R1-R40) or a guard id (G1-G6, untrusted-input limits).
Stdlib only. The file is treated as untrusted: size, entry-count and ratio limits, no DOCTYPE/ENTITY in any XML,
no path tricks, streaming mesh parse with a vertex cap. Nothing in the file is ever executed or opened by path.
"""
import io
import json
import math
import re
import xml.etree.ElementTree as ET
import zipfile

from . import modes, privacy, wipetower

MAX_ENTRIES = 5000
MAX_TOTAL = 6 * 1024 ** 3          # uncompressed bytes in the whole archive
MAX_XML = 64 * 1024 ** 2           # one non-mesh XML/JSON entry
MAX_RATIO = 1000                   # uncompressed / compressed, for entries above 1 MiB
MAX_RATIO_MESH = 100               # mesh models are read whole and parsed: real mesh XML compresses about 5-10:1
MAX_VERTICES = 60_000_000
NS = {"c": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02",
      "p": "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"}
P_UUID = "{%s}UUID" % NS["p"]
P_PATH = "{%s}path" % NS["p"]
CT_FIXED = ('<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
            ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
            ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
            ' <Default Extension="png" ContentType="image/png"/>\n <Default Extension="gcode" ContentType="text/x.gcode"/>\n</Types>')
REL_MODEL = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
LOGICAL_PLATE_GAP = 0.2


class Finding:
    """level is what the call site says (strict mode). validate() sets cls and level for the chosen mode afterwards;
    native keeps the call site's level."""

    def __init__(self, rule, level, where, msg, cls=None, keys=None):
        self.rule, self.level, self.where, self.msg, self.cls = rule, level, where, msg, cls
        self.native = level
        self.keys = frozenset(keys) if keys else None       # the setting keys a finding is about (the patch post-check compares them one by one)

    def __str__(self):
        return "%s %s [%s] %s" % (self.level.upper(), self.rule, self.where, self.msg)

    def as_dict(self):
        return {"rule": self.rule, "class": self.cls or modes.classify(self), "level": self.level, "where": self.where, "message": self.msg}


class Report:
    def __init__(self):
        self.findings = []
        self.silent = []                     # findings the mode hides (class FORM in default mode)
        self.checked = set()
        self.mode = "strict"                 # levels are strict while the rules run; validate() applies the mode at the end
        self.studio_version = None
        self.tables_applied = False

    def err(self, rule, where, msg, cls=None, keys=None):
        self.findings.append(Finding(rule, "error", where, msg, cls, keys))

    def warn(self, rule, where, msg, cls=None, keys=None):
        self.findings.append(Finding(rule, "warning", where, msg, cls, keys))

    @property
    def errors(self):
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self):
        return [f for f in self.findings if f.level == "warning"]

    @property
    def notes(self):
        return [f for f in self.findings if f.level == "note"]

    def as_dict(self):
        return {"mode": self.mode, "studio_version": self.studio_version, "tables_applied": self.tables_applied,
                "findings": [f.as_dict() for f in self.findings]}

    @property
    def ok(self):
        return not self.errors

    def errors_for(self, *rules):
        return [f for f in self.errors if f.rule in rules]

    def rules_failed(self):
        return sorted({f.rule for f in self.errors}, key=lambda r: (r[0], int(re.sub(r"\D", "", r) or 0)))


# ---- guarded access ---------------------------------------------------------------------------------
class Pkg:
    """Read-only, limit-checked view of the archive."""

    def __init__(self, source, rep):
        self.rep = rep
        self.source = source
        self.z = None
        self.names = []
        self.cfg = None
        try:
            self.z = zipfile.ZipFile(source)
        except (zipfile.BadZipFile, OSError, ValueError) as e:
            rep.err("G1", "archive", "not a readable ZIP file: %s" % e)
            return
        infos = self.z.infolist()
        if len(infos) > MAX_ENTRIES:
            rep.err("G2", "archive", "%d entries; the limit is %d" % (len(infos), MAX_ENTRIES))
            self.z = None
            return
        total = 0
        seen = {}
        for i in infos:
            n = i.filename
            total += i.file_size
            if n.startswith("/") or "\\" in n or ".." in n.split("/") or re.match(r"^[A-Za-z]:", n):
                rep.err("G3", n, "unsafe entry name (absolute path, backslash or ..)")
            if n.lower() in seen:
                rep.err("G3", n, "duplicate entry name (also %s)" % seen[n.lower()])
            seen[n.lower()] = n
            cap = MAX_RATIO_MESH if n.lower().endswith(".model") else MAX_RATIO
            if i.file_size > 1024 ** 2 and i.compress_size and i.file_size / i.compress_size > cap:
                rep.err("G4", n, "compression ratio above %d:1" % cap)
            if i.flag_bits & 0x1:
                rep.err("G1", n, "encrypted entry")
        if total > MAX_TOTAL:
            rep.err("G4", "archive", "uncompressed size %.1f GiB is above the limit" % (total / 1024 ** 3))
            self.z = None
            return
        self.names = [i.filename for i in infos]
        self.index = {i.filename: i for i in infos}
        if rep.errors_for("G1", "G3", "G4"):
            self.z = None                    # an unsafe archive is not read any further

    def has(self, name):
        return self.z is not None and name in self.index

    def raw(self, name, limit=MAX_XML):
        i = self.index[name]
        if i.file_size > limit:
            self.rep.err("G5", name, "entry is %.0f MiB; the limit for this file type is %.0f MiB" % (i.file_size / 1024 ** 2, limit / 1024 ** 2))
            return None
        with self.z.open(i) as f:
            data = f.read(limit + 1)
        return data

    def text(self, name):
        d = self.raw(name)
        return None if d is None else d.decode("utf-8", "replace")

    def xml(self, name):
        d = self.raw(name)
        if d is None:
            return None
        if re.search(rb"<!\s*(DOCTYPE|ENTITY)", d, re.I):
            self.rep.err("G6", name, "DOCTYPE or ENTITY in XML is not allowed (entity expansion)")
            return None
        try:
            return ET.fromstring(d)
        except ET.ParseError as e:
            self.rep.err("R25", name, "XML is not well-formed: %s" % e)
            return None

    def json(self, name):
        d = self.raw(name)
        if d is None:
            return None
        try:
            return json.loads(d.decode("utf-8"))
        except (ValueError, RecursionError) as e:
            self.rep.err("R15", name, "not valid JSON: %s" % e)
            return None


# ---- geometry ------------------------------------------------------------------------------------------
def _xf(t, x, y, z):
    return (t[0] * x + t[3] * y + t[6] * z + t[9], t[1] * x + t[4] * y + t[7] * z + t[10], t[2] * x + t[5] * y + t[8] * z + t[11])


def _floats(s, n, default=None):
    try:
        v = [float(a) for a in s.split()]
        return v if len(v) == n else default
    except (ValueError, AttributeError):
        return default


def _corners(bb):
    (x0, y0, z0), (x1, y1, z1) = bb
    return [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]


def _union(a, b):
    if a is None:
        return b
    return (tuple(min(a[0][i], b[0][i]) for i in range(3)), tuple(max(a[1][i], b[1][i]) for i in range(3)))


class _Stop(Exception):
    """Ends the mesh parse early (a finding was reported)."""


def mesh_bboxes(pkg, name, rep, paint=None):
    """{object id: (type, bbox or None)} from one sub-model file, streamed: the entry is read in 1 MiB chunks straight from the ZIP and fed to an
    expat parser, nothing of the mesh is kept. A DOCTYPE anywhere (also after a long comment) raises at once (G6). paint (a dict) gets
    {(file, object id): (painted states, has unpainted area)}."""
    from xml.parsers import expat
    out = {}
    info = pkg.index[name]
    if info.file_size > MAX_TOTAL:
        rep.err("G5", name, "entry is %.0f MiB; the limit for this file type is %.0f MiB" % (info.file_size / 1024 ** 2, MAX_TOTAL / 1024 ** 2))
        return out
    st = {"cur": None, "typ": None, "lo": None, "hi": None, "nv": 0, "acc": None}

    def start(tag, attrs):
        tag = tag.rsplit("}", 1)[-1]
        if tag == "object":
            st["cur"], st["typ"] = attrs.get("id"), attrs.get("type", "model")
            st["lo"] = [math.inf] * 3
            st["hi"] = [-math.inf] * 3
            st["acc"] = wipetower.PaintAcc()
        elif tag == "vertex" and st["lo"] is not None:
            try:
                x, y, z = float(attrs.get("x")), float(attrs.get("y")), float(attrs.get("z"))
            except (TypeError, ValueError):
                rep.err("R24", name, "vertex without numeric x, y, z")
                raise _Stop()
            if not all(math.isfinite(v) for v in (x, y, z)):
                rep.err("R24", name, "a coordinate is NaN or infinite")
                raise _Stop()
            st["nv"] += 1
            if st["nv"] > MAX_VERTICES:
                rep.err("G5", name, "more than %d vertices" % MAX_VERTICES)
                raise _Stop()
            lo, hi = st["lo"], st["hi"]
            for i, v in enumerate((x, y, z)):
                if v < lo[i]: lo[i] = v
                if v > hi[i]: hi[i] = v
        elif tag == "triangle" and st["acc"] is not None:
            st["acc"].add(attrs.get("paint_color"))

    def end(tag):
        if tag.rsplit("}", 1)[-1] == "object":
            lo, hi = st["lo"], st["hi"]
            out[st["cur"]] = (st["typ"], (tuple(lo), tuple(hi)) if lo[0] != math.inf else None)
            if paint is not None and st["acc"] is not None:
                paint[(name, st["cur"])] = st["acc"].result()
            st["cur"] = None

    def doctype(*_a):
        st["g6"] = True
        rep.err("G6", name, "DOCTYPE or ENTITY in XML is not allowed")
        raise _Stop()

    parser = expat.ParserCreate(namespace_separator="}")
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.StartDoctypeDeclHandler = doctype
    parser.EntityDeclHandler = doctype
    try:
        total = 0
        with pkg.z.open(info) as f:
            while True:
                chunk = f.read(1024 ** 2)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_TOTAL:
                    rep.err("G5", name, "entry is more than %.0f MiB" % (MAX_TOTAL / 1024 ** 2))
                    return out
                parser.Parse(chunk, False)
        parser.Parse(b"", True)
    except _Stop:
        return {} if st.get("g6") else out
    except expat.ExpatError as e:
        rep.err("R25", name, "XML is not well-formed: %s" % e)
    return out


def colum_count(count):
    value = math.sqrt(count)
    r = math.floor(value + 0.5)
    return int(r + 1) if value > r else int(r)


def bed_rect(cfg):
    pts = []
    for s in cfg.get("printable_area", []) or []:
        try:
            a, b = str(s).replace(",", "x").split("x")
            pts.append((float(a), float(b)))
        except ValueError:
            return None
    if not pts:
        return None
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _bbox_of(v):
    """AABB of a flat x,y,z array, or None if empty or not finite."""
    if not len(v):
        return None
    lo = (min(v[0::3]), min(v[1::3]), min(v[2::3]))
    hi = (max(v[0::3]), max(v[1::3]), max(v[2::3]))
    return (lo, hi) if all(math.isfinite(x) for x in lo + hi) else None


def instances(pkg, rep):
    """List of dicts {wid, idx, bbox} in world coordinates (AABB of printable parts), plus the parsed root model.
    A component without p:path is resolved in the root model itself (the non-split form)."""
    from . import rules_package as rp
    if hasattr(pkg, "_instances"):
        return pkg._instances
    pkg._instances = _instances(pkg, rep, rp)
    return pkg._instances


def _instances(pkg, rep, rp):
    rm = rp.root_model(pkg, rep)
    pkg._paint = {}                                          # {(model file or "", mesh object id): (painted filaments, has unpainted area)}
    if rm is None:
        return [], None
    for oid, o in rp.root_meshes(rm).items():
        pkg._paint[("", oid)] = o["paint"].result()
    subs = {}
    local = {}
    for w in rp.root_wrappers(rm):
        bb = None
        for c in w["comps"]:
            path, oid = c["path"], c["oid"]
            if path not in subs:
                if path == "":
                    subs[path] = {i: (o["type"], _bbox_of(o["v"])) for i, o in rp.root_meshes(rm).items()}
                else:
                    subs[path] = mesh_bboxes(pkg, path, rep, pkg._paint) if pkg.has(path) else {}
            m = subs.get(path, {}).get(oid)
            if not m or m[0] != "model" or m[1] is None:
                continue
            t = _floats(c["tf"] or "1 0 0 0 1 0 0 0 1 0 0 0", 12, [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0])
            pts = [_xf(t, *p) for p in _corners(m[1])]
            bb = _union(bb, (tuple(min(p[i] for p in pts) for i in range(3)), tuple(max(p[i] for p in pts) for i in range(3))))
        local[w["id"]] = bb
    out = []
    count = {}
    for it in rm["items"]:
        wid = it.get("objectid")
        t = _floats(it.get("transform", "1 0 0 0 1 0 0 0 1 0 0 0"), 12)
        idx = count.get(wid, 0)
        count[wid] = idx + 1
        bb = local.get(wid)
        if t is None or bb is None:
            continue
        pts = [_xf(t, *p) for p in _corners(bb)]
        out.append({"wid": wid, "idx": idx, "printable": it.get("printable") != "0",
                    "bbox": (tuple(min(p[i] for p in pts) for i in range(3)), tuple(max(p[i] for p in pts) for i in range(3)))})
    return out, rm


def plate_of_instances(pkg):
    """{(object id, instance id): plate number} from model_settings.config."""
    res = {}
    n = 0
    if not pkg.has("Metadata/model_settings.config"):
        return res, 0
    root = pkg.xml("Metadata/model_settings.config")
    if root is None:
        return res, 0
    for pl in root.findall("plate"):
        pid = None
        for m in pl.findall("metadata"):
            if m.get("key") == "plater_id":
                try:
                    pid = int(m.get("value"))
                except (TypeError, ValueError):
                    pass
        n = max(n, pid or 0)
        for mi in pl.findall("model_instance"):
            d = {m.get("key"): m.get("value") for m in mi.findall("metadata")}
            try:
                res[(d["object_id"], int(d["instance_id"]))] = pid
            except (KeyError, ValueError):
                pass
    return res, n


def plate_print_sequences(pkg):
    """{plate number: print_sequence value} from the plate metadata of model_settings.config; a plate without the key has none
    (Studio: PrintSequence::ByDefault, the config value counts). An unknown value reads as "by layer" (bbs_3mf.cpp, from_string keeps the default)."""
    res = {}
    if not pkg.has("Metadata/model_settings.config"):
        return res
    root = pkg.xml("Metadata/model_settings.config")
    if root is None:
        return res
    for pl in root.findall("plate"):
        d = {m.get("key"): m.get("value") for m in pl.findall("metadata")}
        try:
            pid = int(d.get("plater_id"))
        except (TypeError, ValueError):
            continue
        if "print_sequence" in d:
            res[pid] = "by object" if d["print_sequence"] == "by object" else "by layer"
    return res


# ---- rules -------------------------------------------------------------------------------------------
def check_package(pkg, rep):
    if not pkg.has("[Content_Types].xml"):
        rep.err("R18", "[Content_Types].xml", "missing")
    else:
        t = pkg.text("[Content_Types].xml")
        if t is not None and t.strip() != CT_FIXED.strip():
            rep.err("R18", "[Content_Types].xml", "content differs from the fixed Studio content")
    if not pkg.has("_rels/.rels"):
        rep.err("R19", "_rels/.rels", "missing")
        return
    root = pkg.xml("_rels/.rels")
    if root is None:
        return
    rels = root.findall("{http://schemas.openxmlformats.org/package/2006/relationships}Relationship")
    models = [r for r in rels if r.get("Type", "").startswith("http://schemas.microsoft.com/3dmanufacturing/") and r.get("Type", "").endswith("3dmodel")]
    if not models or models[0].get("Target") != "/3D/3dmodel.model":
        rep.err("R19", "_rels/.rels", "the first 3dmodel relationship must point to /3D/3dmodel.model")
    if len(models) > 1:
        rep.err("R20", "_rels/.rels", "sub-models belong in 3D/_rels/3dmodel.model.rels, not here")
    ids = [r.get("Id") for r in rels]
    if len(ids) != len(set(ids)):
        rep.err("R19", "_rels/.rels", "duplicate relationship ids")
    for r in rels:
        tgt = (r.get("Target") or "").lstrip("/")
        if tgt not in pkg.index:
            rep.err("R19", "_rels/.rels", "relationship %s points to %s, which is not in the archive" % (r.get("Id"), r.get("Target")))
    sub = pkg.xml("3D/_rels/3dmodel.model.rels") if pkg.has("3D/_rels/3dmodel.model.rels") else None
    listed = set()
    if sub is not None:
        for r in sub.findall("{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"):
            tgt = (r.get("Target") or "").lstrip("/")
            listed.add(tgt)
            if tgt not in pkg.index:
                rep.err("R19", "3D/_rels/3dmodel.model.rels", "%s is not in the archive" % r.get("Target"))
    for n in pkg.names:
        if n.startswith("3D/Objects/") and n.endswith(".model") and n not in listed:
            rep.err("R20", n, "sub-model is not listed in 3D/_rels/3dmodel.model.rels; Studio would never load it")
    order = {n: i for i, n in enumerate(pkg.names)}
    if "Metadata/filament_sequence.json" in order and "Metadata/model_settings.config" in order:
        if order["Metadata/filament_sequence.json"] < order["Metadata/model_settings.config"]:
            rep.err("R34", "Metadata/filament_sequence.json", "comes before model_settings.config in the archive; Studio 2.8.2 reads it in archive order")


def check_root_model(pkg, rep):
    from . import rules_package as rp
    rm = rp.root_model(pkg, rep)
    if rm is None:
        if not pkg.has("3D/3dmodel.model"):
            rep.err("R21", "3D/3dmodel.model", "missing")
        return
    meta = dict(rm["meta"])
    app = meta.get("Application", "")
    if not re.fullmatch(r"BambuStudio-\d+(\.\d+)+", app):
        rep.err("R1", "3D/3dmodel.model", "Application is %r; it must be BambuStudio-<version> or Studio drops every setting" % app)
    if meta.get("BambuStudio:3mfVersion") != "1":
        rep.err("R2", "3D/3dmodel.model", "BambuStudio:3mfVersion must be 1")
    # the production extension is declared (and needed) only when p: elements or 3D/Objects/ are used; Export Generic 3MF writes neither
    uses_p = rm["uses_p"] or any(n.startswith("3D/Objects/") for n in pkg.names)
    if uses_p and rm["attrs"].get("requiredextensions") != "p":
        rep.err("R21", "3D/3dmodel.model", "requiredextensions=\"p\" is missing")
    return app


def check_privacy(pkg, rep, scan=True):
    """R33: no account ids, no absolute paths, nothing that is not in the archive. The personal-data scan (class PRIVACY)
    runs only when scan is true (--privacy); the dangling-reference checks (class FORM) always run."""
    if pkg.z is None:
        return
    src = pkg.source
    if hasattr(src, "seek"):
        src.seek(0)
    for f in (privacy.scan_zip(src) if scan else []):
        rep.err("R33", "privacy", f, cls=modes.PRIVACY)
    # references to files that are not there
    from . import rules_package as rp
    rm = rp.root_model(pkg, rep)
    if rm is not None:
        for name, text in rm["meta"]:
            if (name or "").startswith("Thumbnail") and (text or "").lstrip("/") not in pkg.index:
                rep.err("R33", "3D/3dmodel.model", "%s points to %s, which is not in the archive" % (name, text))
    if pkg.has("Metadata/model_settings.config"):
        t = pkg.text("Metadata/model_settings.config") or ""
        for m in re.finditer(r'key="(?:thumbnail_file|thumbnail_no_light_file|top_file|pick_file)" value="([^"]+)"', t):
            if m.group(1) not in pkg.index:
                rep.err("R33", "Metadata/model_settings.config", "%s is not in the archive" % m.group(1))


def _tool_changes(pkg, plate_count):
    """{plate: [filament numbers]} of the ToolChange layers (type 2) in custom_gcode_per_layer.xml. These count as used filaments
    (Print::extruders(true))."""
    out = {}
    if not pkg.has("Metadata/custom_gcode_per_layer.xml"):
        return out
    root = pkg.xml("Metadata/custom_gcode_per_layer.xml")
    if root is None:
        return out
    for pl in (root.findall("plate") or [root]):
        info = pl.find("plate_info")
        try:
            pid = int(info.get("id")) if info is not None else 1
        except (TypeError, ValueError):
            continue
        for ly in pl.findall("layer"):
            if ly.get("type") == "2":
                try:
                    out.setdefault(pid, []).append(int(ly.get("extruder", "1")))
                except ValueError:
                    pass
    return out


BED_TEMP_KEY = {"Cool Plate": "cool_plate_temp", "Engineering Plate": "eng_plate_temp", "High Temp Plate": "hot_plate_temp",
                "Textured PEI Plate": "textured_plate_temp", "Supertack Plate": "supertack_plate_temp"}


def _check_bed_temp(cfg, rep, plate, used):
    """R39, after Print::validate (Print.cpp, both tags): a filament the plate prints with must have a bed temperature above 0 for the plate's
    bed type, else Studio refuses to slice: "Plate %d: %s does not support filament %s"."""
    key = BED_TEMP_KEY.get(cfg.get("curr_bed_type"))
    vals = cfg.get(key) if key else None
    if not isinstance(vals, list):
        return
    for f in sorted(used):
        if 1 <= f <= len(vals) and str(vals[f - 1]).strip() in ("0", "0.0"):
            rep.err("R39", "plate %d" % plate, "%s is 0 for filament %d: Studio does not slice a plate with %s on it (Print::validate: \"Plate %d: %s does not support filament %d\")" % (
                key, f, cfg.get("curr_bed_type"), plate, cfg.get("curr_bed_type"), f))


def check_wipe_tower(pkg, rep, cfg):
    """R37, after the per-plate rule in GLCanvas3D.cpp (2.8.2 :3906, 2.9.1 :4149; the tower is only made when the plate prints with more than one
    filament (parts, painted areas, support filaments, tool changes), or smooth timelapse, or wrapping detection) and Print::has_wipe_tower()
    (spiral mode has none). No tower on a "by object" plate (plate metadata print_sequence, else the config value) that does not have exactly
    one printable instance (GLCanvas3D.cpp 2.8.2 :3887, 2.9.1 :4130). On a plate with a tower:
    - a saved position that sticks out of the printable area is moved inside by Studio on load (class CHANGE, PartPlate.cpp
      set_default_wipe_tower_pos_for_plate); the overlap test then uses the moved tower;
    - a part that overlaps the tower is an error (the tower is printed over it);
    - the position Studio derives itself is used only when the file has no entry for the plate."""
    if cfg is None:
        return
    from . import rules_package as rp
    fil = cfg.get("filament_colour") or []
    n = len(fil)
    bed = bed_rect(cfg)
    if bed is None:
        rep.err("R17", "Metadata/project_settings.config", "printable_area is missing or not a list of x,y points")
        return
    insts, rm = instances(pkg, rep)
    pkg._tower_plates = set()
    plate_map, nplates = plate_of_instances(pkg)
    nplates = max(nplates, 1)
    ms = rp.parse_model_settings(pkg, rep) if pkg.has("Metadata/model_settings.config") else None
    wrappers = {w["id"]: w for w in rp.root_wrappers(rm)} if rm is not None else {}
    msobj = {o["id"]: o for o in ms["objects"]} if ms else {}
    changes = _tool_changes(pkg, nplates)
    cols = colum_count(nplates)
    sx = (bed[2] - bed[0]) * (1 + LOGICAL_PLATE_GAP)
    sy = (bed[3] - bed[1]) * (1 + LOGICAL_PLATE_GAP)
    by_plate, objs_on, n_print = {}, {}, {}
    plate_seq = plate_print_sequences(pkg)
    for it in insts:
        k = plate_map.get((it["wid"], it["idx"]), 1)
        ox, oy = ((k - 1) % cols) * sx, -((k - 1) // cols) * sy
        (x0, y0, z0), (x1, y1, z1) = it["bbox"]
        by_plate.setdefault(k, []).append(((x0 - ox, y0 - oy, x1 - ox, y1 - oy), z1))
        n_print[k] = n_print.get(k, 0) + (1 if it.get("printable", True) else 0)
    for (wid, _idx), k in plate_map.items():
        o, w = msobj.get(wid), wrappers.get(wid)
        if o is None:
            continue
        parts = []
        for p in o["parts"]:
            comp = next((c for c in (w["comps"] if w else []) if c["oid"] == p["id"]), None)
            parts.append({"subtype": p["subtype"], "meta": p["meta"], "paint": getattr(pkg, "_paint", {}).get((comp["path"], comp["oid"])) if comp else None})
        objs_on.setdefault(k, []).append({"meta": o["meta"], "parts": parts})
    range_fil = _range_filaments(pkg, rm, plate_map, n)    # a height range with its own filament prints with it on that plate
    for k in range(1, nplates + 1):
        items = by_plate.get(k, [])
        if not items:
            continue                                         # nothing is printed on this plate
        used = wipetower.plate_filaments(cfg, objs_on.get(k, []), changes.get(k, ())) | range_fil.get(k, set())
        _check_bed_temp(cfg, rep, k, used)
        if not wipetower.has_wipe_tower(cfg, used):
            continue
        if plate_seq.get(k, str(cfg.get("print_sequence", "by layer"))) == "by object" and n_print.get(k, 0) != 1:
            continue                                         # GLCanvas3D.cpp (2.8.2 :3887, 2.9.1 :4130): no tower on a by-object plate with other than one printable instance
        pkg._tower_plates.add(k)
        max_h = max([z1 for _b, z1 in items], default=0.0)
        try:
            src, raw, box = wipetower.tower_at(cfg, max(len(used), 1), max(max_h, 1.0), bed, k)
        except (KeyError, ValueError, TypeError, ZeroDivisionError) as e:
            rep.err("R37", "Metadata/project_settings.config", "cannot size the prime tower from the settings (%s: %s)" % (type(e).__name__, e))
            return
        tx0, ty0, tx1, ty1 = raw
        over = max(bed[0] - tx0, bed[1] - ty0, tx1 - bed[2], ty1 - bed[3])
        if over > 1e-6:
            msg = ("prime tower (%s position) x %.0f..%.0f, y %.0f..%.0f sticks out of the printable area (%.0f..%.0f, %.0f..%.0f) by %.2f mm; "
                   "Studio moves a saved tower inside the plate when it loads the file (PartPlate.cpp set_default_wipe_tower_pos_for_plate)" % (
                       "saved" if src == "file" else "Studio default", tx0, tx1, ty0, ty1, bed[0], bed[2], bed[1], bed[3], over))
            # Studio saves a stale position (sized for two colors), so a small overrun of the saved value is only a warning in strict mode
            if src == "file" and over <= 1.0:
                rep.warn("R37", "plate %d" % k, msg, cls=modes.CHANGE)
            else:
                rep.err("R37", "plate %d" % k, msg, cls=modes.CHANGE if src == "file" else None)
        bx0, by0, bx1, by1 = box
        for (b, _z) in items:
            if b[2] > bx0 and b[0] < bx1 and b[3] > by0 and b[1] < by1:
                rep.err("R37", "plate %d" % k, "a part (x %.0f..%.0f, y %.0f..%.0f) overlaps the prime tower (%s position x %.0f..%.0f, y %.0f..%.0f); the plate prints with %d filament(s) and has a tower" % (
                    b[0], b[2], b[1], b[3], "saved, after Studio moves it inside the plate" if src == "file" else "Studio default", bx0, bx1, by0, by1, len(used)))
                break


def _range_filaments(pkg, rm, plate_map, n):
    """{plate: filaments the height ranges of its objects print with}. Bad files give nothing here; R40 reports them."""
    from . import ranges as rg
    if rm is None or not pkg.has(rg.FILE):
        return {}
    try:
        by_obj, _p = rg.parse(pkg.text(rg.FILE))
    except Exception:                                        # noqa: BLE001 - untrusted input, R40 reports it
        return {}
    order = []
    for it in rm["items"]:
        if it.get("objectid") not in order:
            order.append(it.get("objectid"))
    out = {}
    for idx, rs in by_obj.items():
        if 1 <= idx <= len(order):
            wid = order[idx - 1]
            fil = {e for e in rg.extruders(rs) if e <= n}
            for (w, _i), k in plate_map.items():
                if w == wid and fil:
                    out.setdefault(k, set()).update(fil)
    return out


def check_height_ranges(pkg, rep, cfg, ps):
    """R40, Metadata/layer_config_ranges.xml (height ranges). See ranges.py for the Studio source behind each check."""
    from . import ranges as rg
    from . import rules_package as rp
    W = rg.FILE
    if not pkg.has(W):
        return
    by_obj, probs = rg.parse(pkg.text(W))
    for pr in probs:
        rep.err("R40", W, pr)
    if not by_obj:
        return
    insts, rm = instances(pkg, rep)
    order = []                                               # model object order = first build item of each object (bbs_3mf.cpp)
    for it in (rm["items"] if rm else []):
        if it.get("objectid") not in order:
            order.append(it.get("objectid"))
    height = {}
    for it in insts:
        if it["idx"] == 0:
            height[it["wid"]] = it["bbox"][1][2] - it["bbox"][0][2]
    ms = rp.parse_model_settings(pkg, rep) if pkg.has("Metadata/model_settings.config") else None
    msobj = {o["id"]: o for o in ms["objects"]} if ms else {}
    names = {wid: (msobj.get(wid, {}).get("meta") or {}).get("name") or ("object %s" % wid) for wid in order}
    limits = rg.layer_limits(cfg)
    region = ps.region_options if ps is not None and getattr(ps, "region_options", None) else None
    sig = {}
    for idx, rs in sorted(by_obj.items()):
        if idx > len(order):
            rep.err("R40", W, "object id %d: the model has %d object(s); Studio drops these ranges" % (idx, len(order)))
            continue
        wid = order[idx - 1]
        where = "object %d (%s)" % (idx, names.get(wid))
        errs, warns = rg.check(rs, where, limits, None, height.get(wid))
        for e in errs:
            cls = modes.CHANGE if ("overlaps" in e or "outside the nozzle" in e) else None
            rep.err("R40", W, e, cls=cls)
        for w_ in warns:
            rep.warn("R40", W, w_, cls=modes.KEEP)
        if region is not None:
            for lo, hi, opts in rs:
                bad = sorted(k for k in opts if k not in ("layer_height", "extruder") and k not in region)
                if bad:
                    rep.err("R40", W, "%s range %s-%s mm: %s cannot be set in a height range (Studio uses only layer_height and the settings a part can have)" % (
                        where, rg._g(lo), rg._g(hi), ", ".join(bad)), cls=modes.KEEP)
                for k, v in opts.items():
                    if k in region:
                        rp._override(k, v, ps, "%s range %s-%s mm" % (where, rg._g(lo), rg._g(hi)), rep, "R40")
        nf = len(cfg.get("filament_colour") or []) or 1
        for lo, hi, opts in rs:
            if "extruder" in opts:
                try:
                    e = int(opts["extruder"])
                except ValueError:
                    e = -1
                if not (0 <= e <= nf):
                    rep.err("R40", W, "%s range %s-%s mm: extruder %r is not 0 (the object's filament) or a filament slot 1..%d" % (
                        where, rg._g(lo), rg._g(hi), opts["extruder"], nf))
        om = (msobj.get(wid) or {}).get("meta") or {}
        def eff(key, default):
            return str(om.get(key, cfg.get(key, default)))
        if eff("enable_support", "0") in ("1", "true") and eff("support_type", "").startswith("tree") and eff("support_style", "default") == "tree_organic":
            rep.err("R40", W, "%s has height ranges and organic tree supports; Studio does not slice that (Print.cpp: \"Variable layer height is not supported with Organic supports\")" % where)
        sig[wid] = rg.signature(rs)
    # a plate with a prime tower needs one layer z series for all its objects (Print.cpp, "The prime tower requires that all objects are sliced with the same layer heights")
    plate_map, _n = plate_of_instances(pkg)
    on_plate = {}
    for (wid, _i), k in plate_map.items():
        on_plate.setdefault(k, set()).add(wid)
    for k in sorted(getattr(pkg, "_tower_plates", set())):
        wids = sorted(on_plate.get(k, ()))
        if len(wids) > 1 and any(w in sig for w in wids) and len({sig.get(w, ()) for w in wids}) > 1:
            rep.err("R40", "plate %d" % k, "the plate has a prime tower and objects with different height ranges (%s). Studio needs one layer height "
                    "series for every object on a plate with a prime tower: give them the same ranges, or put them on separate plates" % ", ".join(
                        names.get(w, w) for w in wids))


def _guarded(rep, rule, fn, *a):
    """Run one rule group. A malformed file must give a finding, never a traceback."""
    try:
        return fn(*a)
    except (MemoryError, KeyboardInterrupt):
        raise
    except Exception as e:                                   # noqa: BLE001 - untrusted input
        rep.err(rule, "validator", "the file is malformed in a way this check cannot read (%s: %s)" % (type(e).__name__, str(e)[:120]))
        return None


def validate(source, mode="default", privacy=False):
    """source: path or file object. Returns a Report. mode: "default" or "strict" (see modes.py). privacy: also run the
    personal-data scan (class PRIVACY; reported, never an error)."""
    if mode not in modes.MODES:
        raise ValueError("mode must be one of %s, not %r" % (", ".join(modes.MODES), mode))
    rep = _validate(source, privacy)
    rep.mode = mode
    rep.findings, rep.silent = modes.apply(rep.findings, mode)
    return rep


def _validate(source, privacy_scan):
    rep = Report()
    try:
        pkg = Pkg(source, rep)
    except Exception as e:                                   # noqa: BLE001
        rep.err("G1", "archive", "cannot read the archive (%s: %s)" % (type(e).__name__, str(e)[:120]))
        return rep
    if pkg.z is None:
        return rep
    _guarded(rep, "R19", check_package, pkg, rep)
    check_root_model_app = _guarded(rep, "R1", check_root_model, pkg, rep)
    _validate_rest(pkg, rep, check_root_model_app, privacy_scan)
    return rep


def _validate_rest(pkg, rep, check_root_model_app, privacy_scan=False):
    cfg = pkg.json("Metadata/project_settings.config") if pkg.has("Metadata/project_settings.config") else None
    if cfg is None and not pkg.has("Metadata/project_settings.config"):
        rep.err("R15", "Metadata/project_settings.config", "missing")
    if cfg is None and pkg.has("Metadata/project_settings.config") and not rep.errors_for("R15", "G5"):
        rep.err("R15", "Metadata/project_settings.config", "must be a JSON object, found null")
    if cfg is not None and not isinstance(cfg, dict):
        rep.err("R15", "Metadata/project_settings.config", "must be a JSON object, found %s" % type(cfg).__name__)
        cfg = None
    table_ok = True
    if isinstance(cfg, dict):
        rep.studio_version = cfg["version"] if isinstance(cfg.get("version"), str) else None
        bad = [k for k, v in cfg.items() if not (isinstance(v, str) or (isinstance(v, list) and all(isinstance(x, str) for x in v)))]
        if bad:
            rep.err("R15", "Metadata/project_settings.config", "values must be strings or lists of strings: %s" % ", ".join(bad[:5]))
            table_ok = False                 # the table rules assume strings; checking them would only repeat this error
    else:
        cfg = None
    if cfg is not None and table_ok:
        from . import rules_config
        app = check_root_model_app
        ps = _guarded(rep, "R10", rules_config.load_profiles, cfg, app, rep)
        rep.tables_applied = ps is not None
        _pm, nplates = _guarded(rep, "R30", plate_of_instances, pkg) or ({}, 0)
        n_fil = len(cfg.get("filament_colour") or []) or 1
        if ps is not None:
            _guarded(rep, "R15", rules_config.check, cfg, rep, ps, nplates)
        from . import rules_package
        _guarded(rep, "R25", rules_package.check, pkg, rep, cfg, ps, n_fil)
        multi = len(cfg.get("nozzle_diameter") or []) > 1 if isinstance(cfg.get("nozzle_diameter"), list) else False
        sim = _guarded(rep, "S1", simulate_load, cfg, check_root_model_app, ps, nplates) if ps is not None and not multi else None
        if sim is not None:
            eff, lost, notes = sim
            # each key is reported once: R3 already named the unlisted keys, R13 the missing project options
            r3 = getattr(rep, "r3_keys", set())
            lost = [l for l in lost if not (l.reason.startswith("not listed") and l.key in r3) and not l.reason.startswith("project options missing")]
            if eff is None or lost:
                grouped = {}
                for l in lost:
                    grouped.setdefault(l.reason, []).append(l)
                for reason, ls in grouped.items():
                    rep.err("S1", "load simulator", "%d setting(s) would not survive opening the file in the Studio app (the command-line slicer keeps them; Preset.cpp load_external_preset, 2.8.2 :2263, 2.9.1 :2331) - %s: %s%s" % (
                        len(ls), reason, ", ".join(l.key for l in ls[:8]), " ..." if len(ls) > 8 else ""), keys=[l.key for l in ls])
            for nt in notes:
                rep.warn("S1", "load simulator", nt)
    _guarded(rep, "R33", check_privacy, pkg, rep, privacy_scan)
    if cfg is not None and table_ok:
        _guarded(rep, "R37", check_wipe_tower, pkg, rep, cfg)
        _guarded(rep, "R40", check_height_ranges, pkg, rep, cfg, ps)


def simulate_load(cfg, app, ps, nplates):
    from . import simulate
    return simulate.simulate(cfg, app, ps, nplates)
