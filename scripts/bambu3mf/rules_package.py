"""Validator rules for the package parts: geometry, model_settings.config, custom G-code (R21-R32, R23, R24).
Everything read from the archive is treated as untrusted (see validate.Pkg)."""
import io
import math
import re
import xml.etree.ElementTree as ET
from array import array

from . import modes, rules_config, wipetower
from . import validate as V
from .validate import NS, P_UUID, P_PATH, _floats, bed_rect, colum_count, LOGICAL_PLATE_GAP, MAX_TOTAL, instances


def _digits(s):
    """ASCII digits only (str.isdigit accepts other scripts)."""
    return isinstance(s, str) and re.fullmatch(r"[0-9]+", s) is not None

MESH_CLOSED_LIMIT = 400_000        # triangles; above this the closed-manifold check is skipped with a warning
SUBTYPES = ("normal_part", "modifier_part", "negative_part", "support_enforcer", "support_blocker")
RESERVED_PART_KEYS = {"name", "matrix", "source_file", "source_object_id", "source_volume_id", "source_offset_x", "source_offset_y",
                      "source_offset_z", "extruder", "volume_type", "text_info", "module"}
RESERVED_OBJ_KEYS = {"name", "extruder", "module"}
BAD_NAME = re.compile(r'[<>:/\\?*"|]')
SIG = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)(e[-+]?\d+)?$", re.I)


def _sig_digits(s):
    m = re.match(r"^[-+]?(\d*)\.?(\d*)(?:e[-+]?\d+)?$", s.strip(), re.I)
    if not m:
        return None
    digits = (m.group(1) + m.group(2)).lstrip("0")
    return len(digits.rstrip("0")) if digits else 1


ROOT_MODEL = "3D/3dmodel.model"


def _read_vertex(el, cur, path, rep):
    """Add one <vertex> to the object dict cur. False (after an R24 finding) if it is not readable."""
    try:
        for a in ("x", "y", "z"):
            sv = el.get(a)
            if _sig_digits(sv) is None:
                raise ValueError
            if _sig_digits(sv) > 9:
                cur["over"] += 1
            cur["v"].append(float(sv))
    except (TypeError, ValueError):
        rep.err("R24", path, "a vertex has a missing or non-numeric coordinate")
        return False
    return True


def parse_model(pkg, path, rep, root=False):
    """One streaming pass over one model file. Returns {"objects": {id: {uuid, type, v, t, over, comps, mesh}} in file order,
    "items": [attribute dicts], "meta": [(name, text)], "attrs": root attributes, "uses_p": bool}, or None if the file cannot be read.
    A component without p:path (or with p:path = the root model) refers to an object of the same model: path "".
    root=True: the file is the root model, which can hold meshes (the non-split form), so the vertex cap applies here."""
    from xml.parsers import expat
    info = pkg.index[path]
    if info.file_size > MAX_TOTAL:
        rep.err("G5", path, "entry is %.0f MiB; the limit for this file type is %.0f MiB" % (info.file_size / 1024 ** 2, MAX_TOTAL / 1024 ** 2))
        return None
    out = {"objects": {}, "list": [], "items": [], "meta": [], "attrs": {}, "uses_p": False}
    st = {"cur": None, "nv": 0, "text": None}
    stack = []
    pns = "{%s}" % NS["p"]

    class _Stop(Exception):
        pass

    def fix(attrs):
        """expat's 'ns}name' -> ElementTree's '{ns}name'."""
        return {("{" + k if "}" in k else k): v for k, v in attrs.items()}

    def start(rtag, attrs):
        tag = rtag.rsplit("}", 1)[-1]
        if tag not in ("vertex", "triangle"):
            attrs = fix(attrs)
        if not stack:
            out["attrs"] = dict(attrs)
        stack.append(tag)
        if tag not in ("vertex", "triangle") and any(k.startswith(pns) for k in attrs):
            out["uses_p"] = True
        cur = st["cur"]
        if tag == "mesh" and cur is not None:
            cur["mesh"] = True
        if tag == "object":
            cur = {"uuid": attrs.get(P_UUID), "type": attrs.get("type", "model"), "v": array("d"), "t": array("I"), "over": 0, "nv": 0,
                   "comps": [], "mesh": False, "paint": wipetower.PaintAcc()}
            cur["id"] = attrs.get("id")
            out["objects"][cur["id"]] = cur
            out["list"].append(cur)
            st["cur"] = cur
        elif tag == "metadata" and len(stack) == 2:
            st["text"] = []
        elif tag in ("vertex", "triangle", "component", "item"):
            st["attrs"] = attrs

    def chars(data):
        if st["text"] is not None:
            st["text"].append(data)

    def end(rtag):
        tag = rtag.rsplit("}", 1)[-1]
        attrs = st.get("attrs")
        stack.pop()
        cur = st["cur"]
        if tag == "vertex" and cur is not None:
            if not _read_vertex(attrs, cur, path, rep):
                raise _Stop()
            st["nv"] += 1
            if root and st["nv"] > V.MAX_VERTICES:
                rep.err("G5", path, "more than %d vertices" % V.MAX_VERTICES)
                raise _Stop()
        elif tag == "triangle" and cur is not None:
            try:
                tri = (int(attrs.get("v1")), int(attrs.get("v2")), int(attrs.get("v3")))
                if min(tri) < 0 or max(tri) >= 2 ** 32:
                    raise ValueError
                cur["t"].extend(tri)
                cur["paint"].add(attrs.get("paint_color"))
            except (TypeError, ValueError, OverflowError):
                rep.err("R24", path, "a triangle has a missing or non-integer vertex index")
                raise _Stop()
        elif tag in ("vertex", "triangle"):
            rep.err("R24", path, "a %s outside any <object>" % tag)
            raise _Stop()
        elif tag == "component" and cur is not None:
            cp = (attrs.get(P_PATH) or "").lstrip("/")
            cur["comps"].append({"path": "" if (root and cp == path) else cp, "oid": attrs.get("objectid"), "uuid": attrs.get(P_UUID), "tf": attrs.get("transform")})
        elif tag == "object":
            st["cur"] = None
        elif tag == "item":
            out["items"].append(dict(attrs))
        elif tag == "metadata" and len(stack) == 1:
            out["meta"].append((st["last_name"], "".join(st["text"] or [])))
            st["text"] = None

    def start2(rtag, attrs):
        if rtag.rsplit("}", 1)[-1] == "metadata" and len(stack) == 1:
            st["last_name"] = attrs.get("name")
        start(rtag, attrs)

    def doctype(*_a):
        rep.err("G6", path, "DOCTYPE or ENTITY in XML is not allowed")
        st["g6"] = True
        raise _Stop()

    parser = expat.ParserCreate(namespace_separator="}")
    parser.StartElementHandler = start2
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    parser.StartDoctypeDeclHandler = doctype
    parser.EntityDeclHandler = doctype
    try:
        total = 0
        with pkg.z.open(info) as f:                     # streamed in 1 MiB chunks: nothing of the entry is kept except the vertex / triangle arrays
            while True:
                chunk = f.read(1024 ** 2)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_TOTAL:
                    rep.err("G5", path, "entry is more than %.0f MiB" % (MAX_TOTAL / 1024 ** 2))
                    return out
                parser.Parse(chunk, False)
        parser.Parse(b"", True)
    except _Stop:
        return None if st.get("g6") else out
    except expat.ExpatError as e:
        rep.err("R25", path, "XML is not well-formed: %s" % e)
        return None
    return out


def parse_submodel(pkg, path, rep):
    """{object id: {uuid, type, verts(array 'd'), tris(array 'I'), digits_over}} from one sub-model file."""
    m = parse_model(pkg, path, rep)
    return {} if m is None else m["objects"]


def root_model(pkg, rep):
    """The root model, parsed once per archive (cached; the root can hold 40 MB of meshes). None if missing or unreadable."""
    if not hasattr(pkg, "_root_model"):
        pkg._root_model = parse_model(pkg, ROOT_MODEL, rep, root=True) if pkg.has(ROOT_MODEL) else None
    return pkg._root_model


def root_wrappers(rm):
    """The buildable objects of the root model: [{id, uuid, comps}]. A mesh object that a component refers to is a mesh object, not a
    wrapper (non-split form: meshes 1, 3, 5, wrappers 2, 4, 6). A mesh object nothing refers to is buildable itself."""
    refd = {c["oid"] for o in rm["list"] for c in o["comps"] if not c["path"]}
    out = []
    for o in rm["list"]:
        oid = o["id"]
        if o["mesh"] and oid in refd:
            continue
        comps = o["comps"] or ([{"path": "", "oid": oid, "uuid": None, "tf": None}] if o["mesh"] else [])
        out.append({"id": oid, "uuid": o["uuid"], "comps": comps})
    return out


def root_meshes(rm):
    """{object id: mesh dict} of the root model objects that hold a mesh: what a component without p:path resolves to."""
    return {oid: o for oid, o in rm["objects"].items() if o["mesh"]}


def check_mesh(path, oid, m, rep):
    where = "%s object %s" % (path, oid)
    nv, nt = len(m["v"]) // 3, len(m["t"]) // 3
    if m["over"]:
        rep.warn("R24", where, "%d coordinates have more than 9 significant digits (Studio writes 9)" % m["over"])
    if nt == 0 or nv == 0:
        rep.err("R24", where, "the mesh has no triangles")
        return
    if any(i >= nv for i in m["t"]):
        rep.err("R24", where, "a triangle refers to a vertex that does not exist")
        return
    if any(not math.isfinite(x) for x in m["v"]):
        rep.err("R24", where, "a coordinate is NaN or infinite")
        return
    # positive volume (signed, from the origin)
    v, t = m["v"], m["t"]
    vol = 0.0
    for i in range(0, len(t), 3):
        a, b, c = t[i] * 3, t[i + 1] * 3, t[i + 2] * 3
        vol += (v[a] * (v[b + 1] * v[c + 2] - v[b + 2] * v[c + 1]) - v[a + 1] * (v[b] * v[c + 2] - v[b + 2] * v[c]) + v[a + 2] * (v[b] * v[c + 1] - v[b + 1] * v[c])) / 6.0
    if vol <= 1e-9:
        rep.err("R24", where, "the mesh volume is %.3g mm3 (inside-out or empty). Studio flips it silently on load, which hides the error" % vol)
    if nt > MESH_CLOSED_LIMIT:
        rep.warn("R24", where, "%s triangles: the closed-surface check was skipped (limit %s)" % (format(nt, ","), format(MESH_CLOSED_LIMIT, ",")))
        return
    seen = set()
    for i in range(0, len(t), 3):
        for a, b in ((t[i], t[i + 1]), (t[i + 1], t[i + 2]), (t[i + 2], t[i])):
            if a == b:
                continue                                     # degenerate; Studio drops it
            if (a, b) in seen:
                rep.err("R24", where, "an edge is used twice in the same direction (non-manifold or mixed orientation); Studio loads and slices the mesh as it is, it does not repair it when it opens a project (bbs_3mf.cpp _generate_volumes, 2.8.2 :5550, 2.9.1 :5940)")
                return
            seen.add((a, b))
    for (a, b) in seen:
        if (b, a) not in seen:
            rep.err("R24", where, "the surface is not closed (an edge has no neighbor); Studio loads and slices the mesh as it is, it does not repair it when it opens a project (bbs_3mf.cpp _generate_volumes, 2.8.2 :5550, 2.9.1 :5940)")
            return


def parse_model_settings(pkg, rep):
    root = pkg.xml("Metadata/model_settings.config") if pkg.has("Metadata/model_settings.config") else None
    if root is None:
        if not pkg.has("Metadata/model_settings.config"):
            rep.err("R25", "Metadata/model_settings.config", "missing")
        return None
    objs, plates = [], []
    for ob in root.findall("object"):
        o = {"id": ob.get("id"), "meta": {m.get("key"): m.get("value") for m in ob.findall("metadata") if m.get("key")}, "parts": []}
        for p in ob.findall("part"):
            o["parts"].append({"id": p.get("id"), "subtype": p.get("subtype"),
                               "meta": {m.get("key"): m.get("value") for m in p.findall("metadata") if m.get("key")}})
        objs.append(o)
    for pl in root.findall("plate"):
        d = {m.get("key"): m.get("value") for m in pl.findall("metadata") if m.get("key")}
        inst = []
        for mi in pl.findall("model_instance"):
            inst.append({m.get("key"): m.get("value") for m in mi.findall("metadata") if m.get("key")})
        plates.append({"meta": d, "inst": inst})
    return {"objects": objs, "plates": plates}


def check(pkg, rep, cfg, ps, n):
    rm = root_model(pkg, rep)
    ms = parse_model_settings(pkg, rep)
    if rm is None or ms is None:
        return
    W = "3D/3dmodel.model"
    # ---- root model: wrappers, items
    wrappers = root_wrappers(rm)
    items = rm["items"]
    wid_set = {w["id"] for w in wrappers}
    if len({w["id"] for w in wrappers}) != len(wrappers):
        rep.err("R22", W, "duplicate object ids")
    # sub-models
    subs = {"": root_meshes(rm)}                             # a component without p:path refers to the root model
    for w in wrappers:
        if not w["comps"]:
            rep.err("R21", W, "object %s has no components" % w["id"])
        for c in w["comps"]:
            if c["path"] and c["path"] not in subs:
                subs[c["path"]] = parse_submodel(pkg, c["path"], rep) if pkg.has(c["path"]) else None
                if subs[c["path"]] is None:
                    rep.err("R20", W, "component refers to %s, which is not in the archive" % c["path"])
    # ---- R22 ids and UUIDs (warnings for files not made by the builder; Studio only needs uniqueness)
    counter = 1
    for w in wrappers:
        N = None
        for c in w["comps"]:
            mt = re.search(r"object_(\d+)\.model$", c["path"] or "")
            N = int(mt.group(1)) if mt else N
        for k, c in enumerate(w["comps"]):
            m = (subs.get(c["path"]) or {}).get(c["oid"])
            if m is None:
                rep.err("R21", W, "component objectid %s is not an object in %s" % (c["oid"], c["path"] or W))
                continue
            if N is not None:
                want = "%08x" % (k + (N << 16))
                if not (c["uuid"] or "").startswith(want + "-b206-40ff-9872-83e8017abed1"):
                    rep.warn("R22", W, "component UUID %s differs from Studio's scheme (%s-b206-...)" % (c["uuid"], want))
                if not (m["uuid"] or "").startswith(want + "-81cb-4c03-9d28-80fed5dfa1dc"):
                    rep.warn("R22", c["path"], "mesh UUID %s differs from Studio's scheme (%s-81cb-...)" % (m["uuid"], want))
            if c["oid"] != str(counter):
                rep.warn("R22", W, "mesh id %s: Studio's running counter would give %d" % (c["oid"], counter))
                counter = int(c["oid"]) if _digits(c["oid"] or "") else counter
            counter += 1
        if N is not None and not (w["uuid"] or "").startswith("%08x-61cb-4c03-9d28-80fed5dfa1dc" % N):
            rep.warn("R22", W, "object %s UUID %s differs from Studio's scheme" % (w["id"], w["uuid"]))
        if w["id"] != str(counter):
            rep.warn("R22", W, "wrapper id %s: Studio's running counter would give %d" % (w["id"], counter))
            counter = int(w["id"]) if _digits(w["id"] or "") else counter
        counter += 1
    # ---- R23 items
    per_obj = {}
    for it in items:
        oid = it.get("objectid")
        if oid not in wid_set:
            rep.err("R23", W, "build item refers to object %s, which does not exist" % oid)
        if _floats(it.get("transform", ""), 12) is None:
            rep.err("R23", W, "build item for object %s has no valid 12-number transform" % oid)
        if it.get("printable") not in ("0", "1"):
            rep.err("R23", W, "build item for object %s has no printable=\"0|1\"" % oid)
        per_obj[oid] = per_obj.get(oid, 0) + 1
    used_as_comp = {c["oid"] for o in rm["list"] for c in o["comps"] if not c["path"]}
    for w in wrappers:
        if per_obj.get(w["id"], 0) == 0:
            if w["id"] in used_as_comp:                      # another object builds it: only its form differs from Studio's
                rep.err("R23", W, "object %s has no build item (it would not be on any plate)" % w["id"], cls=modes.FORM)
            else:
                rep.err("R23", W, "object %s has no build item: Studio drops an object without an instance when it loads the file (bbs_3mf.cpp 2.8.2 :3761, 2.9.1 :4113)" % w["id"], cls=modes.LOAD)
    # ---- R24 meshes
    sub_types = {}
    for path, objs in subs.items():
        for oid, m in (objs or {}).items():
            check_mesh(path or W, oid, m, rep)
            sub_types[(path, oid)] = m["type"]
    # ---- model_settings: R21 pairing, R26-R31
    ms_ids = [o["id"] for o in ms["objects"]]
    if sorted(ms_ids) != sorted(wid_set):
        rep.err("R21", "Metadata/model_settings.config", "objects %s do not match the objects in the root model %s" % (sorted(ms_ids), sorted(wid_set)))
    by_id = {w["id"]: w for w in wrappers}
    for o in ms["objects"]:
        w = by_id.get(o["id"])
        where = "model_settings object %s" % o["id"]
        name = o["meta"].get("name", "")
        if BAD_NAME.search(name):
            rep.err("R31", where, "object name %r contains one of < > : / \\ ? * \" |" % name)
        ext = o["meta"].get("extruder")
        if ext is not None and not (_digits(ext) and 1 <= int(ext) <= n):
            rep.err("R27", where, "extruder %r is not a filament slot 1..%d" % (ext, n))
        for k, v in o["meta"].items():
            if k in RESERVED_OBJ_KEYS:
                continue
            _override(k, v, ps, where, rep, "R28")
        if w is None:
            continue
        comp_ids = [c["oid"] for c in w["comps"]]
        pids = [p["id"] for p in o["parts"]]
        if sorted(pids) != sorted(comp_ids):
            rep.err("R26", where, "part ids %s do not match the mesh ids of its components %s" % (pids, comp_ids))
        if len(set(pids)) != len(pids):
            rep.err("R26", where, "duplicate part ids")
        normal = 0
        for p in o["parts"]:
            pw = "%s part %s" % (where, p["id"])
            st = p["subtype"]
            if st not in SUBTYPES:
                rep.err("R26", pw, "subtype %r is not one of %s" % (st, ", ".join(SUBTYPES)))
                continue
            if BAD_NAME.search(p["meta"].get("name", "")):
                rep.err("R31", pw, "part name contains one of < > : / \\ ? * \" |")
            comp = next((c for c in w["comps"] if c["oid"] == p["id"]), None)
            if comp is not None:
                want = "model" if st == "normal_part" else "other"
                got = sub_types.get((comp["path"], comp["oid"]))
                if got is not None and got != want:
                    rep.err("R24", pw, "mesh type is %r; %s needs %r" % (got, st, want))
            if st == "normal_part":
                normal += 1
                e = p["meta"].get("extruder", o["meta"].get("extruder"))
                if e is None or not (_digits(e) and 1 <= int(e) <= n):
                    rep.err("R27", pw, "extruder %r is not a filament slot 1..%d" % (e, n))
            for k, v in p["meta"].items():
                if k in RESERVED_PART_KEYS:
                    continue
                _override(k, v, ps, pw, rep, "R28")
        if o["parts"] and normal == 0:
            rep.err("R29", where, "the object has modifier, negative or support parts but no normal part to act on")
    # ---- R30 plates
    insts_by_obj = {}
    seen_ids = set()
    for pi, pl in enumerate(ms["plates"], 1):
        pid = pl["meta"].get("plater_id")
        if pid != str(pi):
            rep.err("R30", "plate %d" % pi, "plater_id is %r; plates are numbered 1, 2, 3 in order" % pid)
        if BAD_NAME.search(pl["meta"].get("plater_name", "")):
            rep.err("R31", "plate %d" % pi, "plate name contains one of < > : / \\ ? * \" |")
        fm = pl["meta"].get("filament_maps")
        if fm is not None and len(fm.split()) != n:
            rep.err("R30", "plate %d" % pi, "filament_maps has %d entries, n=%d" % (len(fm.split()), n))
        for mi in pl["inst"]:
            for k in ("object_id", "instance_id", "identify_id"):
                if k not in mi:
                    rep.err("R30", "plate %d" % pi, "model_instance without %s" % k)
            ident = mi.get("identify_id")
            if ident is not None:
                if not _digits(ident) or int(ident) <= 0 or ident in seen_ids:
                    rep.err("R30", "plate %d" % pi, "identify_id %r must be a unique positive integer" % ident)
                seen_ids.add(ident)
            insts_by_obj.setdefault(mi.get("object_id"), []).append(mi.get("instance_id"))
    if not ms["plates"]:
        rep.err("R30", "Metadata/model_settings.config", "no <plate> element")
    for w in wrappers:
        got = sorted(insts_by_obj.get(w["id"], []))
        want = [str(i) for i in range(per_obj.get(w["id"], 0))]
        if got != sorted(want):
            rep.err("R30", "object %s" % w["id"], "plate instances %s do not match its %d build item(s)" % (got, per_obj.get(w["id"], 0)))
    # ---- R23 object inside its plate cell
    bed = bed_rect(cfg) if cfg else None
    if bed:
        inst_list, _r = instances(pkg, rep)
        plate_of = {}
        for pi, pl in enumerate(ms["plates"], 1):
            for mi in pl["inst"]:
                try:
                    plate_of[(mi.get("object_id"), int(mi.get("instance_id")))] = pi
                except (TypeError, ValueError):
                    pass
        cols = colum_count(max(len(ms["plates"]), 1))
        sx, sy = (bed[2] - bed[0]) * (1 + LOGICAL_PLATE_GAP), (bed[3] - bed[1]) * (1 + LOGICAL_PLATE_GAP)
        for it in inst_list:
            k = plate_of.get((it["wid"], it["idx"]))
            if k is None:
                continue
            ox, oy = ((k - 1) % cols) * sx, -((k - 1) // cols) * sy
            (x0, y0, z0), (x1, y1, z1) = it["bbox"]
            if x0 - ox < bed[0] - 0.01 or y0 - oy < bed[1] - 0.01 or x1 - ox > bed[2] + 0.01 or y1 - oy > bed[3] + 0.01:
                rep.err("R23", "object %s instance %d" % (it["wid"], it["idx"]), "does not lie inside plate %d (x %.1f..%.1f, y %.1f..%.1f on a %.0f x %.0f mm plate)" % (
                    k, x0 - ox, x1 - ox, y0 - oy, y1 - oy, bed[2] - bed[0], bed[3] - bed[1]))
            if z0 < -0.01:
                rep.err("R23", "object %s instance %d" % (it["wid"], it["idx"]), "sits %.2f mm below the plate; Studio keeps a sunk part in a project file (Plater.cpp ensure_on_bed with is_project_file) and does not print the part below Z 0" % -z0)
    # ---- R32 custom G-code
    if pkg.has("Metadata/custom_gcode_per_layer.xml"):
        check_gcode(pkg, rep, cfg, n, ms, instances(pkg, rep)[0] if bed else [], bed)


def _override(k, v, ps, where, rep, rule):
    if ps is None:
        return
    o = ps.options.get(k)
    if o is None:
        rep.err(rule, where, "override %r is not a Studio %s setting" % (k, ps.tag_version))
        return
    if o["preset"] != "print":
        rep.err(rule, where, "override %s is a %s setting; only process settings can be set per object or part" % (k, o["preset"]))
        return
    # Studio writes a vector option with "," between the slots ("200,nil"); only string vectors use ";" (Config.hpp serialize / deserialize)
    parts = (v or "").split(";" if o["type"] == "coStrings" else ",") if o["vector"] else [v or ""]
    for s in parts:
        why = rules_config.check_value(o, s)
        if why:
            rep.err(rule, where, "%s = %r: %s" % (k, v, why))
            return


def check_gcode(pkg, rep, cfg, n, ms, inst_list, bed):
    W = "Metadata/custom_gcode_per_layer.xml"
    root = pkg.xml(W)
    if root is None:
        return
    try:
        first = float((cfg.get("initial_layer_print_height") or ["0.2"])[0] if isinstance(cfg.get("initial_layer_print_height"), list) else cfg.get("initial_layer_print_height", "0.2"))
        lh = float((cfg.get("layer_height") or ["0.2"])[0] if isinstance(cfg.get("layer_height"), list) else cfg.get("layer_height", "0.2"))
    except (TypeError, ValueError):
        rep.err("R32", W, "layer_height / initial_layer_print_height are not numbers")
        return
    nplates = len(ms["plates"])
    plates = root.findall("plate") or [root]
    # model height per plate
    heights = {}
    plate_of = {}
    for pi, pl in enumerate(ms["plates"], 1):
        for mi in pl["inst"]:
            try:
                plate_of[(mi.get("object_id"), int(mi.get("instance_id")))] = pi
            except (TypeError, ValueError):
                pass
    for it in inst_list:
        k = plate_of.get((it["wid"], it["idx"]))
        if k:
            heights[k] = max(heights.get(k, 0.0), it["bbox"][1][2])
    for pl in plates:
        info = pl.find("plate_info")
        pid = int(info.get("id")) if info is not None and _digits(info.get("id") or "") else 1
        if pid < 1 or pid > nplates:
            rep.err("R32", W, "plate_info id %s is not a plate of this project (1..%d)" % (pid, nplates))
        for ly in pl.findall("layer"):
            t = ly.get("type")
            if t is None or not _digits(t) or int(t) > 5:
                rep.err("R32", W, "layer without a valid type (0-5); Studio would read the G-code from the old gcode attribute")
                continue
            if ly.get("extra") is None:
                rep.err("R32", W, "layer without an extra attribute (Studio throws when it is missing)")
            if int(t) == 4 and not (ly.get("extra") or "").strip():
                rep.err("R32", W, "custom G-code layer with empty extra: nothing would run")
            e = ly.get("extruder", "1")
            if not (_digits(e) and 1 <= int(e) <= max(n, 1)):
                rep.err("R32", W, "extruder %r is not a filament slot" % e)
            try:
                z = float(ly.get("top_z"))
                if not math.isfinite(z):
                    raise ValueError
            except (TypeError, ValueError):
                rep.err("R32", W, "layer without a numeric top_z")
                continue
            steps = (z - first) / lh
            if steps < -1e-6 or abs(z - (first + round(steps) * lh)) > 0.0011:
                rep.err("R32", W, "top_z %s is not the top of a layer (first layer %.3g mm, then %.3g mm each)" % (ly.get("top_z"), first, lh))
            elif pid in heights and z > heights[pid] + 0.0011:
                rep.err("R32", W, "top_z %s is above the top of the model on plate %d (%.3f mm); Studio ignores it" % (ly.get("top_z"), pid, heights[pid]))
        mode = pl.find("mode")
        if mode is not None and mode.get("value") not in ("SingleExtruder", "MultiAsSingle", "MultiExtruder"):
            rep.err("R32", W, "mode %r is not SingleExtruder, MultiAsSingle or MultiExtruder" % mode.get("value"))
