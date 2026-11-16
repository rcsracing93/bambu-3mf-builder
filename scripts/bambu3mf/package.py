"""Package writer: layout, split-model 3MF, model_settings.config, custom G-code, deterministic ZIP.

Formats follow what Bambu Studio writes (checked against real 2.8.2 and 2.9.1 saves).
Output is byte-identical for the same job (fixed ZIP order and timestamps, hash-derived UUIDs).
Stdlib only.
"""
import array
import hashlib
import math
import os
import time
import zipfile
from xml.sax.saxutils import escape

from . import config as cfgmod
from . import mesh as meshmod
from . import lint as lintmod
from . import wipetower
from .job import JobError
from .serialize import normalize

GAP = 5.0                 # mm between objects (shelf packing)
LOGICAL_PLATE_GAP = 0.2   # PartPlateList: stride = plate size * (1 + gap)
CHUNK = 20000
XMLNS = ('xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
         'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" '
         'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" requiredextensions="p"')
MODEL_OPEN = '<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" xml:lang="en-US" %s>\n' % XMLNS
REL_MODEL = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
BUILD_UUID = "2c7c17d8-22b5-4d84-8835-1976022ea369"
SUBTYPE = {"normal_part": "normal_part", "modifier": "modifier_part", "modifier_part": "modifier_part",
           "negative_part": "negative_part", "support_enforcer": "support_enforcer", "support_blocker": "support_blocker"}


def g9(x):
    return "%.9g" % x


def gs(x):
    s = "%.6f" % x
    s = s.rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def colum_count(count):
    value = math.sqrt(count)
    r = math.floor(value + 0.5)
    return int(r + 1) if value > r else int(r)


def fake_uuid(*parts):
    h = hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()
    return "%s-%s-4%s-%s%s-%s" % (h[0:8], h[8:12], h[13:16], "89ab"[int(h[16], 16) % 4], h[17:20], h[20:32])


# ---- bed ---------------------------------------------------------------------------------------
def bed_rect(machine_cfg):
    pts = []
    for s in machine_cfg.get("printable_area", []):
        a, b = str(s).replace(",", "x").split("x")
        pts.append((float(a), float(b)))
    if not pts:
        raise JobError(["The printer preset has no printable_area"])
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


# ---- model assembly ---------------------------------------------------------------------------
class Part:
    pass


class Obj:
    pass


def load_geometry(job, bed, height):
    """Read and check every mesh. Returns (objects, errors, warnings). Each Obj has parts with .mesh."""
    errs, warns = [], list(job.warnings)
    cache = {}
    objs = []
    for oi, o in enumerate(job.objects, 1):
        ob = Obj()
        ob.src = o
        ob.index = oi
        ob.name = o["name"]
        ob.parts = []
        for pj, p in enumerate(o["parts"], 1):
            where = "[[object]] #%d part #%d (%s)" % (oi, pj, os.path.basename(p["mesh"]))
            key = (p["mesh"], p["units"])
            if key not in cache:
                try:
                    m = p["mesh_obj"] if p.get("mesh_obj") is not None else meshmod.read_mesh(p["mesh"])
                except meshmod.MeshError as e:
                    errs.append("%s: %s" % (where, e)); continue
                e2, w2 = meshmod.check_mesh(m, where, units=p["units"], bed=None if p["type"] != "normal_part" else (bed[2] - bed[0], bed[3] - bed[1], height),
                                            allow_large=getattr(job, "allow_large", False))
                errs.extend(e2); warns.extend(w2)
                cache[key] = (m, bool(e2))
            m, bad = cache[key]
            if bad:
                continue
            pt = Part()
            pt.src = p
            pt.mesh = m
            pt.name = p["name"]
            pt.filament = p["filament"]
            pt.subtype = SUBTYPE.get(p["type"], "normal_part")
            pt.id = 0
            ob.parts.append(pt)
        objs.append(ob)
    total = sum(pt.mesh.n_triangles for ob in objs for pt in ob.parts)
    if total > meshmod.STOP_TRI and not getattr(job, "allow_large", False):
        errs.append("the project has %s triangles in all parts together. Above %s the build stops; decimate the meshes or set allow_large = true in the job" % (
            format(total, ","), format(meshmod.STOP_TRI, ",")))
    elif total > meshmod.WARN_TRI and not any("triangles" in w for w in warns):
        warns.append("the project has %s triangles in all parts together; load and slice will be slow" % format(total, ","))
    if not errs:
        for ob in objs:
            normal = [p for p in ob.parts if p.subtype == "normal_part"]
            if not normal:
                errs.append("[[object]] #%d (%s) has no normal part; modifiers and negative parts need a body" % (ob.index, ob.name))
                continue
            los = []; his = []
            for p in normal:
                lo, hi = p.mesh.bbox()
                los.append(lo); his.append(hi)
            lo = [min(l[i] for l in los) for i in range(3)]
            hi = [max(h[i] for h in his) for i in range(3)]
            ob.lo, ob.hi = lo, hi
            ob.w, ob.d, ob.h = hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]
            ob.shift = ((lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0, (lo[2] + hi[2]) / 2.0)
            # Studio's own form (bbs_3mf.cpp, center_geometry_after_creation): every part mesh is centered on its own
            # bbox, the component transform carries the part's offset inside the object, and the part "matrix" holds
            # the same offset. csrc = part center in the source mesh's coordinates (also written as source_offset).
            for p in ob.parts:
                plo, phi = p.mesh.bbox()
                p.csrc = tuple((plo[i] + phi[i]) / 2.0 for i in range(3))
                p.cobj = tuple(p.csrc[i] - ob.shift[i] for i in range(3))
    return objs, errs, warns


def layout(objs, bed, errs):
    """Assign (plate, x, y) per instance. Plate-local coordinates (bed origin = rect min). Returns plates count."""
    nplates = 0
    for ob in objs:
        nplates = max(nplates, ob.src["plate"])
    used = {ob.src["plate"] for ob in objs}
    for k in range(1, nplates + 1):
        if k not in used:
            errs.append("Plate %d has no objects. Plates must be numbered 1, 2, 3 without gaps." % k)
    cx, cy = (bed[0] + bed[2]) / 2.0, (bed[1] + bed[3]) / 2.0
    for k in range(1, nplates + 1):
        items = []
        for ob in objs:
            if ob.src["plate"] != k:
                continue
            ob.inst = []
            if ob.src["position"] is not None:
                if ob.src["copies"] != 1:
                    errs.append("[[object]] #%d: position can only be used with copies = 1" % ob.index)
                    continue
                ob.inst.append([ob.src["position"][0], ob.src["position"][1]])
                continue
            for c in range(ob.src["copies"]):
                slot = [0.0, 0.0]
                ob.inst.append(slot)
                items.append((ob, slot))
        if not items:
            continue
        # shelf packing in job order, then center the group on the bed
        maxw = bed[2] - bed[0]
        x = y = rowh = 0.0
        place = []
        for ob, slot in items:
            if x > 0 and x + ob.w > maxw:
                x = 0.0; y += rowh + GAP; rowh = 0.0
            place.append((slot, x, y, ob))
            x += ob.w + GAP
            rowh = max(rowh, ob.d)
        tw = max(px + ob.w for _s, px, _y, ob in place)
        th = max(py + ob.d for _s, _x, py, ob in place)
        for slot, px, py, ob in place:
            slot[0] = cx - tw / 2.0 + px + ob.w / 2.0
            slot[1] = cy - th / 2.0 + py + ob.d / 2.0
    # bounds check
    for ob in objs:
        for n, (x, y) in enumerate(getattr(ob, "inst", []), 1):
            if x - ob.w / 2 < bed[0] - 1e-6 or x + ob.w / 2 > bed[2] + 1e-6 or y - ob.d / 2 < bed[1] - 1e-6 or y + ob.d / 2 > bed[3] + 1e-6:
                errs.append("[[object]] #%d (%s) instance %d does not fit on plate %d (%.1f x %.1f mm on a %.0f x %.0f mm bed). Use fewer copies, another plate, or a smaller model." % (
                    ob.index, ob.name, n, ob.src["plate"], ob.w, ob.d, bed[2] - bed[0], bed[3] - bed[1]))
    for k in range(1, nplates + 1):
        boxes = [(ob, i, x, y) for ob in objs if ob.src["plate"] == k for i, (x, y) in enumerate(getattr(ob, "inst", []))]
        for a in range(len(boxes)):
            for b in range(a + 1, len(boxes)):
                A, B = boxes[a], boxes[b]
                if abs(A[2] - B[2]) < (A[0].w + B[0].w) / 2 and abs(A[3] - B[3]) < (A[0].d + B[0].d) / 2:
                    errs.append("Plate %d: [[object]] #%d and #%d overlap. Change position or use automatic placement." % (k, A[0].index, B[0].index))
    return nplates


def tower_collisions(objs, cfg, n, max_height, bed, nplates, errs, extruders=1):
    """R37: the prime tower must lie inside the printable area and clear of every object, at the position in the file
    and at the position Studio derives itself (it may recompute it on load)."""
    for k in range(1, nplates + 1):
        boxes = wipetower.tower_boxes(cfg, n, max_height, bed, k)
        for (x0, y0, x1, y1, _src) in boxes:
            if x0 < bed[0] - 1e-6 or y0 < bed[1] - 1e-6 or x1 > bed[2] + 1e-6 or y1 > bed[3] + 1e-6:
                errs.append("R37 plate %d: the prime tower (x %.0f..%.0f, y %.0f..%.0f) is not inside the printable area. Use a smaller prime_tower_width or fewer colors." % (k, x0, x1, y0, y1))
            for ob in objs:
                if ob.src["plate"] != k:
                    continue
                for (x, y) in ob.inst:
                    if x + ob.w / 2 > x0 and x - ob.w / 2 < x1 and y + ob.d / 2 > y0 and y - ob.d / 2 < y1:
                        errs.append("R37 plate %d: [[object]] #%d (%s) overlaps the prime tower (x %.0f..%.0f, y %.0f..%.0f). Move it with position = [x, y], or put it on another plate." % (
                            k, ob.index, ob.name, x0, x1, y0, y1))
                        break
        # one message per problem is enough
    errs[:] = list(dict.fromkeys(errs))


# ---- XML text -----------------------------------------------------------------------------------
def root_model(job, objs, items, date):
    app = "BambuStudio-%s" % job.profiles.tag_version
    meta = {"Application": app, "BambuStudio:3mfVersion": "1", "Copyright": "", "CreationDate": date, "Description": "",
            "Designer": "", "DesignerCover": "", "DesignerUserId": "", "License": "", "ModificationDate": date,
            "Origin": "", "ProfileCover": "", "ProfileDescription": "", "ProfileTitle": "", "Title": job.title}
    out = [MODEL_OPEN]
    for k in sorted(meta):
        out.append(' <metadata name="%s">%s</metadata>\n' % (k, escape(meta[k])))
    out.append(" <resources>\n")
    ident = " ".join(["1 0 0 0 1 0 0 0 1 0 0 0"][:1])
    for ob in objs:
        out.append('  <object id="%d" p:UUID="%08x-61cb-4c03-9d28-80fed5dfa1dc" type="model">\n   <components>\n' % (ob.wid, ob.index))
        for p in ob.parts:
            out.append('    <component p:path="/3D/Objects/object_%d.model" objectid="%d" p:UUID="%08x-b206-40ff-9872-83e8017abed1" transform="%s"/>\n' % (
                ob.index, p.id, (p.pidx - 1) + (ob.index << 16),
                "1 0 0 0 1 0 0 0 1 %s %s %s" % tuple(g9(x) for x in p.cobj)))
        out.append("   </components>\n  </object>\n")
    out.append(" </resources>\n")
    out.append(' <build p:UUID="%s">\n' % BUILD_UUID)
    for (ob, inst, tx, ty, tz) in items:
        out.append('  <item objectid="%d" p:UUID="%08x-b1ec-4553-aec9-835e5b724bb4" transform="1 0 0 0 1 0 0 0 1 %s %s %s" printable="1"/>\n' % (
            ob.wid, ob.wid + (inst << 16), g9(tx), g9(ty), g9(tz)))
    out.append(" </build>\n</model>\n")
    return "".join(out)


def mesh_stream(ob):
    """Yield text chunks of one sub-model file."""
    yield MODEL_OPEN + ' <metadata name="BambuStudio:3mfVersion">1</metadata>\n <resources>\n'
    for p in ob.parts:
        sx, sy, sz = p.csrc
        yield '  <object id="%d" p:UUID="%08x-81cb-4c03-9d28-80fed5dfa1dc" type="%s">\n   <mesh>\n    <vertices>\n' % (
            p.id, (p.pidx - 1) + (ob.index << 16), "model" if p.subtype == "normal_part" else "other")
        v = array.array("f", p.mesh.v)           # float32, as Studio stores
        nv = len(v) // 3
        for s in range(0, nv, CHUNK):
            e = min(nv, s + CHUNK)
            lines = []
            for i in range(s, e):
                lines.append('     <vertex x="%s" y="%s" z="%s"/>\n' % (
                    g9(v[3 * i] - sx), g9(v[3 * i + 1] - sy), g9(v[3 * i + 2] - sz)))
            yield "".join(lines)
        yield "    </vertices>\n    <triangles>\n"
        t = p.mesh.t
        nt = len(t) // 3
        for s in range(0, nt, CHUNK):
            e = min(nt, s + CHUNK)
            yield "".join('     <triangle v1="%d" v2="%d" v3="%d"/>\n' % (t[3 * i], t[3 * i + 1], t[3 * i + 2]) for i in range(s, e))
        yield "    </triangles>\n   </mesh>\n  </object>\n"
    yield " </resources>\n <build/>\n</model>\n"


def _meta(key, value, ind):
    return '%s<metadata key="%s" value="%s"/>\n' % (ind, key, escape(str(value), {'"': "&quot;"}))


def model_settings(job, objs, nplates, by, n):
    out = ['<?xml version="1.0" encoding="UTF-8"?>\n<config>\n']
    for ob in objs:
        normal = [p for p in ob.parts if p.subtype == "normal_part"]
        ob_ext = normal[0].filament
        out.append('  <object id="%d">\n' % ob.wid)
        out.append(_meta("name", ob.name, "    "))
        out.append(_meta("extruder", ob_ext, "    "))
        for k in sorted(ob.src["settings"]):
            out.append(_meta(k, _value(by, k, ob.src["settings"][k]), "    "))
        out.append('    <metadata face_count="%d"/>\n' % sum(p.mesh.n_triangles for p in ob.parts))
        for p in ob.parts:
            out.append('    <part id="%d" subtype="%s" uuid="%s">\n' % (p.id, p.subtype, fake_uuid(job.title, ob.index, p.pidx, p.name)))
            out.append(_meta("name", p.name, "      "))
            out.append(_meta("matrix", "1 0 0 %s 0 1 0 %s 0 0 1 %s 0 0 0 1" % tuple(g9(x) for x in p.cobj), "      "))
            out.append(_meta("source_file", os.path.basename(p.src["mesh"]), "      "))
            out.append(_meta("source_object_id", 0, "      "))
            out.append(_meta("source_volume_id", 0, "      "))
            out.append(_meta("source_offset_x", g9(p.csrc[0]), "      "))
            out.append(_meta("source_offset_y", g9(p.csrc[1]), "      "))
            out.append(_meta("source_offset_z", g9(p.csrc[2]), "      "))
            if p.subtype == "normal_part":
                out.append(_meta("extruder", p.filament, "      "))
            for k in sorted(p.src["settings"]):
                out.append(_meta(k, _value(by, k, p.src["settings"][k]), "      "))
            out.append('      <mesh_stat face_count="%d" edges_fixed="0" degenerate_facets="0" facets_removed="0" facets_reversed="0" backwards_edges="0"/>\n' % p.mesh.n_triangles)
            out.append("    </part>\n")
        out.append("  </object>\n")
    ident = 100
    for k in range(1, nplates + 1):
        out.append("  <plate>\n")
        out.append(_meta("plater_id", k, "    "))
        out.append(_meta("plater_name", "", "    "))
        out.append(_meta("locked", "false", "    "))
        out.append(_meta("filament_map_mode", "Auto For Flush", "    "))
        out.append(_meta("filament_maps", " ".join(["1"] * n), "    "))
        out.append(_meta("filament_volume_maps", " ".join(["0"] * n), "    "))
        for ob in objs:
            if ob.src["plate"] != k:
                continue
            for i in range(len(ob.inst)):
                ident += 1
                out.append("    <model_instance>\n")
                out.append(_meta("object_id", ob.wid, "      "))
                out.append(_meta("instance_id", i, "      "))
                out.append(_meta("identify_id", ident, "      "))
                out.append("    </model_instance>\n")
        out.append("  </plate>\n")
    out.append("</config>\n")
    return "".join(out)


def _value(by, key, v):
    nv = normalize(by[key], v)
    if isinstance(nv, list):
        return ";".join(str(x) for x in nv)
    return nv


def custom_gcode_xml(job, cfg, n, plate_heights, nplates):
    """Returns (xml text or None). Raises JobError on bad z."""
    if not job.layer_gcode:
        return None
    first = float(cfg["initial_layer_print_height"]) if not isinstance(cfg["initial_layer_print_height"], list) else float(cfg["initial_layer_print_height"][0])
    lh = float(cfg["layer_height"]) if not isinstance(cfg["layer_height"], list) else float(cfg["layer_height"][0])
    errs = []
    by_plate = {}
    for g in job.layer_gcode:
        k = g["plate"]
        if k < 1 or k > nplates:
            errs.append("[[layer_gcode]] z=%s: plate %d does not exist (the job has %d)" % (g["z"], k, nplates)); continue
        z = g["z"]
        steps = (z - first) / lh
        nearest = first + round(steps) * lh
        if steps < -1e-6 or abs(z - nearest) > 0.0011:
            lo = max(first, nearest)
            errs.append("[[layer_gcode]] z=%s is not the top of a layer (first layer %.3g, then every %.3g mm). Nearest layer top: %s" % (z, first, lh, gs(lo)))
            continue
        if z > plate_heights[k] + 0.0011:
            errs.append("[[layer_gcode]] z=%s is above the top of the model on plate %d (%s mm)" % (z, k, gs(plate_heights[k])))
            continue
        by_plate.setdefault(k, []).append(g)
    if errs:
        raise JobError(errs)
    pause = cfg.get("machine_pause_gcode", "")
    mode = "SingleExtruder" if n == 1 else "MultiAsSingle"
    out = ['<?xml version="1.0" encoding="utf-8"?>\n<custom_gcodes_per_layer>\n']
    for k in sorted(by_plate):
        out.append('<plate>\n<plate_info id="%d"/>\n' % k)
        for g in sorted(by_plate[k], key=lambda x: x["z"]):
            if g["kind"] == "temperature":
                typ, extra, gc = 4, "M104 S%d" % int(g["value"]), "M104 S%d" % int(g["value"])
            elif g["kind"] == "pause":
                typ = 1
                extra = g["value"] if isinstance(g["value"], str) else ""
                gc = pause if isinstance(pause, str) else ""
            else:
                typ, extra, gc = 4, str(g["value"]), str(g["value"])
            q = {'"': "&quot;", "\n": "&#10;"}
            out.append('<layer top_z="%s" type="%d" extruder="1" color="" extra="%s" gcode="%s"/>\n' % (gs(g["z"]), typ, escape(extra, q), escape(gc, q)))
        out.append('<mode value="%s"/>\n</plate>\n' % mode)
    out.append("</custom_gcodes_per_layer>\n")
    return "".join(out)


# ---- ZIP ------------------------------------------------------------------------------------------
def _info(name):
    zi = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.external_attr = 0o644 << 16
    zi.create_system = 3
    return zi


class Writer:
    def __init__(self, path):
        self.z = zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True)

    def text(self, name, s):
        self.z.writestr(_info(name), s.encode("utf-8"), compresslevel=6)

    def stream(self, name, chunks):
        with self.z.open(_info(name), "w", force_zip64=True) as f:
            for c in chunks:
                f.write(c.encode("utf-8"))

    def close(self):
        self.z.close()


def build(job, out_path=None):
    """Build the 3MF for a loaded job. Returns a Summary-like object with .path .config .objects .warnings."""
    ps = job.profiles
    mcfg, _m = ps.resolve("machine", job.printer)
    bed = bed_rect(mcfg)
    height = float(mcfg.get("printable_height", ["250"])[0] if isinstance(mcfg.get("printable_height"), list) else mcfg.get("printable_height", 250))
    objs, errs, warns = load_geometry(job, bed, height)
    nplates = 0
    if not errs:
        nplates = layout(objs, bed, errs)
    if errs:
        raise JobError(errs)
    plate_heights = {k: max(ob.h for ob in objs if ob.src["plate"] == k) for k in range(1, nplates + 1)}
    max_h = max(plate_heights.values())
    job.warnings = warns
    res = cfgmod.build_config(job, plates=nplates, max_height=max_h)
    cfg = res.config
    n = res.n
    warns.extend(lintmod.lint(job, cfg, objs, res))          # settings lint: warnings only, never blocks
    tower_collisions(objs, cfg, n, max_h, bed, nplates, errs)
    xml_gc = None
    try:
        xml_gc = custom_gcode_xml(job, cfg, n, plate_heights, nplates)
    except JobError as e:
        errs.extend(e.problems)
    if errs:
        raise JobError(errs)

    # ids (spec 3.3): one counter for parts and wrappers
    counter = 1
    for ob in objs:
        for j, p in enumerate(ob.parts, 1):
            p.id = counter; p.pidx = j; counter += 1
        ob.wid = counter; counter += 1

    # build items: object origin = center of the normal-part bbox; z = half height
    cols = colum_count(nplates)
    sx = (bed[2] - bed[0]) * (1.0 + LOGICAL_PLATE_GAP)
    sy = (bed[3] - bed[1]) * (1.0 + LOGICAL_PLATE_GAP)
    items = []
    for ob in objs:
        k = ob.src["plate"] - 1
        ox, oy = (k % cols) * sx, -(k // cols) * sy
        for i, (x, y) in enumerate(ob.inst):
            items.append((ob, i, ox + x, oy + y, ob.h / 2.0))
    items.sort(key=lambda t: (t[0].index, t[1]))

    date = time.strftime("%Y-%m-%d", time.gmtime(int(os.environ.get("SOURCE_DATE_EPOCH", time.time()))))
    if out_path:
        path = os.path.abspath(out_path)               # --out is relative to where the command runs
    else:
        path = job.output                              # "output" in the job is relative to the job file
        if not os.path.isabs(path):
            path = os.path.join(os.path.dirname(job.path), path)
    tmp = path + ".part"
    w = Writer(tmp)
    try:
        w.text("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
               ' <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
               ' <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
               ' <Default Extension="png" ContentType="image/png"/>\n <Default Extension="gcode" ContentType="text/x.gcode"/>\n</Types>')
        w.text("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
               ' <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="%s"/>\n</Relationships>' % REL_MODEL)
        w.text("3D/3dmodel.model", root_model(job, objs, items, date))
        rels = ['<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n']
        for i, ob in enumerate(objs, 1):
            rels.append(' <Relationship Target="/3D/Objects/object_%d.model" Id="rel-%d" Type="%s"/>\n' % (ob.index, i, REL_MODEL))
        rels.append("</Relationships>")
        w.text("3D/_rels/3dmodel.model.rels", "".join(rels))
        for ob in objs:
            w.stream("3D/Objects/object_%d.model" % ob.index, mesh_stream(ob))
        w.text("Metadata/project_settings.config", cfgmod.dumps(cfg))
        w.text("Metadata/model_settings.config", model_settings(job, objs, nplates, ps.options, n))
        if xml_gc:
            w.text("Metadata/custom_gcode_per_layer.xml", xml_gc)
        w.text("Metadata/slice_info.config", '<?xml version="1.0" encoding="UTF-8"?>\n<config>\n  <header>\n    <header_item key="X-BBL-Client-Type" value="slicer"/>\n'
               '    <header_item key="X-BBL-Client-Version" value="%s"/>\n  </header>\n</config>\n' % ps.tag_version)
        seq = ",".join('"plate_%d":{"nozzle_sequence":[],"optimal_assignment":[],"sequence":[]}' % k for k in sorted(range(1, nplates + 1), key=str))
        w.text("Metadata/filament_sequence.json", "{%s}" % seq)
    finally:
        w.close()
    os.replace(tmp, path)

    class Summary:
        pass
    s = Summary()
    s.path = path
    s.result = res
    s.config = cfg
    s.objects = objs
    s.plates = nplates
    s.items = items
    s.warnings = warns
    s.max_height = max_h
    return s
