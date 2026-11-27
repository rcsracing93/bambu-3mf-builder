"""Patch mode orchestrator: read -> plan -> edit -> write <out>.part -> verify -> rename.

Step 8c adds the printer change (patch_printer.py) and the print-host privacy edit (patch_cfg._privacy).
Scope of 8a: process preset, filament preset and colour of existing slots, settings (project, object, part), plate type, layer G-code
(pause, temperature, custom). Step 8b adds: a new slot at the end, removal of the last slot (only if nothing uses it), the root-model
privacy edit (rule 5) and sliced input (G-code, its md5 and the plate entries of slice_info.config are dropped). The mesh models are copied, never parsed here; the input validator and the post-write validator read them as for any file.
"""
import os
import tempfile

from . import patch_cfg, patch_printer, patch_slots, patch_verify, patch_xml, patch_zip
from .job import PLATE_KEY, _Check, _settings_table
from .package import _value, gs
from .patch_job import PatchRefused
from .patch_read import dump_config, open_source
from .profiles import ProfileSet

SLICED_NOTE = ("The input was sliced. Its G-code (%d plate(s)), the G-code checksums, the plate entries of slice_info.config and the G-code links in "
               "model_settings.config are removed, so the output is an unsliced project. Slice it again in Studio.")
PICTURE_NOTE = ("Plate pictures, thumbnails and plate JSON are copied as they were. They may show the old colours, presets or plate type until "
                "Studio saves the project again (Studio does not read them back).")


class Plan:
    """What was decided. patch_verify checks the output against this and the request, not against the editor's own bookkeeping."""
    pass


class PatchResult:
    pass


def _same_file(a, b):
    try:
        if os.path.exists(b):
            return os.path.samefile(a, b)
    except OSError:
        pass
    return os.path.realpath(a) == os.path.realpath(b)


def _bad_plate(pt, ps):
    names = ", ".join(sorted(ps.bed_types))
    if pt in PLATE_KEY:                      # a plate of a newer Studio (3D Effect Plate needs 2.9.2)
        return "%s needs Studio 2.9.2 or newer; this file is from Studio %s. Choose one of: %s" % (pt, ps.version, names)
    return "%r is not a plate type. Choose one of: %s" % (pt, names)


# ---- the request, checked against the file -----------------------------------------------------------------------
def build_request(job, src, ps):
    """Coerce setting values and check every slot, object, plate and layer of the job against the file. Returns
    (Request, ms_edits [(scope, key, value)], layers, warnings). Raises PatchRefused with every problem found."""
    chk = _Check()
    by, n = ps.options, len(src.cfg["filament_colour"])
    req = patch_cfg.Request()
    req.process, req.keep_process, req.reset = job.process, job.keep_changes, job.reset
    if job.plate_type is not None:
        if job.plate_type not in ps.bed_types:
            chk.err("plate_type", _bad_plate(job.plate_type, ps))
        else:
            req.plate_type = job.plate_type
    ptype = req.plate_type or src.cfg.get("curr_bed_type")
    req.settings = _settings_table(job.settings, ("print",), by, ps.version, "[settings]", chk,
                                   "[settings] (process settings only; filament settings go under [[filament]] settings)")
    n0 = n
    adds, removal = [], None
    for f in sorted((f for f in job.filaments if f["slot"] is not None), key=lambda f: f["slot"]):
        slot, w = f["slot"], f["where"]
        if f["remove"]:
            if slot != n0:
                chk.err(w + ".slot", ("only the last slot can be removed (slot %d). Slots are never renumbered, so a slot in the middle stays." % n0) if slot < n0
                        else "the file has %d filament slot(s)" % n0)
            elif n0 == 1:
                chk.err(w + ".slot", "the file has one filament slot; it cannot be removed")
            else:
                removal = slot
        elif slot > n0:
            want = n0 + len(adds) + 1
            if slot != want:
                chk.err(w + ".slot", "a new slot must be the next number (%d); the file has %d slot(s)%s" % (want, n0, (" and this job adds %d" % len(adds)) if adds else ""))
            elif not f["preset"] or not f["color"]:
                chk.err(w, "slot %d is a new slot: give both preset and color" % slot)
            else:
                adds.append({"slot": slot, "preset": f["preset"], "color": f["color"], "where": w})
    if adds and removal:
        chk.err("filament", "a job can add slots or remove the last one, not both")
        removal = None
    req.adds = adds
    if removal:
        why = patch_slots.slot_use(src, removal, ignore_sequence=src.sliced)
        if why:
            chk.err("[[filament]] slot %d" % removal, "slot %d is in use, so it cannot be removed (slots are never renumbered): %s. Give those parts and settings another slot first, in a separate patch." % (
                removal, "; ".join(why[:6]) + (" ..." if len(why) > 6 else "")))
        else:
            req.remove = removal
    n = n0 + len(adds) - (1 if removal else 0)          # the slot count after the patch; every later range check uses it
    for k in patch_cfg.SLOT_RANGE_KEYS:
        if k in req.settings and not 0 <= int(req.settings[k]) <= n:
            chk.err("[settings].%s" % k, "filament slots are 0 (auto) to %d" % n)
    added = {a["slot"] for a in adds}
    for f in job.filaments:
        slot, w = f["slot"], f["where"]
        if slot is None or f["remove"] or (slot > n0 and slot not in added):
            continue
        st = _settings_table(f["settings"], ("filament",), by, ps.version, w + ".settings", chk,
                             "[filament.settings] (filament settings only)", ptype) if f["settings"] else {}
        if slot in added:
            if st:
                req.slots[slot - 1] = {"preset": None, "color": None, "settings": st, "keep": False}
        else:
            req.slots[slot - 1] = {"preset": f["preset"], "color": f["color"], "settings": st, "keep": f["keep"]}
    if job.printer:
        if adds or removal:
            chk.err("printer", "a printer change cannot be combined with adding or removing a slot. Make the printer change in its own patch.")
        else:
            try:
                patch_printer.plan(src, ps, req, job.printer)
            except PatchRefused as e:
                chk.errors.extend(e.problems)
    # ---- model_settings: objects, parts, plates --------------------------------------------------
    ms = src.ms
    objs = {o["id"]: o for o in ms["objects"]}
    on_plate = {}
    for pi, pl in enumerate(ms["plates"], 1):
        for inst in pl["inst"]:
            on_plate.setdefault(inst.get("object_id"), set()).add(pi)
    nplates = len(ms["plates"])
    edits, warns = [], []

    def add(scope, key, value):
        edits.append((scope, key, value))

    for ent in job.objects:
        w = ent["where"]
        cand = [o for o in ms["objects"] if (ent["name"] is None or o["meta"].get("name") == ent["name"])
                and (ent["plate"] is None or ent["plate"] in on_plate.get(o["id"], ()))]
        if ent["plate"] is not None and ent["plate"] > nplates:
            chk.err(w + ".plate", "the file has %d plate(s)" % nplates)
            continue
        if not cand:
            names = ", ".join(sorted("%r (plate %s)" % (o["meta"].get("name"), "/".join(str(p) for p in sorted(on_plate.get(o["id"], ())))) for o in ms["objects"])[:8])
            chk.err(w, "no object matches %s. Objects in the file: %s" % (
                ("name %r" % ent["name"]) + ((" on plate %d" % ent["plate"]) if ent["plate"] else "") if ent["name"] else "plate %d" % ent["plate"], names))
            continue
        if ent["name"] is not None and len(cand) > 1:
            where_ = ", ".join("plate %s" % "/".join(str(p) for p in sorted(on_plate.get(o["id"], ()))) for o in cand)
            if len({tuple(sorted(on_plate.get(o["id"], ()))) for o in cand}) == 1:
                chk.err(w, "name %r matches %d objects (%s), all on the same plate. The job cannot tell them apart. Rename one of them in Studio, save the project, and patch that file." % (
                    ent["name"], len(cand), where_))
            else:
                chk.err(w, "name %r matches %d objects (%s). Add plate = N to pick one." % (ent["name"], len(cand), where_))
            continue
        ost = _settings_table(ent["settings"], ("print",), by, ps.version, w + ".settings", chk, "[object.settings] (process settings only)")
        for k in patch_cfg.SLOT_RANGE_KEYS:
            if k in ost and not 0 <= int(ost[k]) <= n:
                chk.err(w + ".settings.%s" % k, "filament slots are 0 (auto) to %d" % n)
        fil = ent["filament"]
        if fil is not None and not 1 <= fil <= n:
            chk.err(w + ".filament", "slot %d does not exist; the file has %d slot(s)" % (fil, n))
            fil = None
        own = {p["name"] for p in ent["parts"] if p["filament"] is not None}
        for o in cand:
            others = sorted(on_plate.get(o["id"], ()) - ({ent["plate"]} if ent["plate"] else set()))
            if ent["plate"] is not None and others or (ent["plate"] is None and len(on_plate.get(o["id"], ())) > 1):
                warns.append("%s: object %r has instances on plates %s; the change applies to every instance (settings belong to the object)." % (
                    w, o["meta"].get("name"), "/".join(str(p) for p in sorted(on_plate.get(o["id"], ())))))
            for k in sorted(ost):
                add(("object", o["id"]), k, _value(by, k, ost[k]))
            if fil is not None:
                add(("object", o["id"]), "extruder", str(fil))
                for p in o["parts"]:
                    if p["meta"].get("extruder") is not None and p["meta"].get("name") not in own:
                        add(("part", o["id"], p["id"]), "extruder", str(fil))
            for pe in ent["parts"]:
                pm = [p for p in o["parts"] if p["meta"].get("name") == pe["name"]]
                if not pm:
                    chk.err(pe["where"], "object %r has no part named %r. Parts: %s" % (o["meta"].get("name"), pe["name"], ", ".join(repr(p["meta"].get("name")) for p in o["parts"])))
                    continue
                if len(pm) > 1:
                    chk.err(pe["where"], "object %r has %d parts named %r; patch mode cannot tell them apart." % (o["meta"].get("name"), len(pm), pe["name"]))
                    continue
                p = pm[0]
                pst = _settings_table(pe["settings"], ("print",), by, ps.version, pe["where"] + ".settings", chk, "[object.part.settings] (process settings only)")
                for k in sorted(pst):
                    add(("part", o["id"], p["id"]), k, _value(by, k, pst[k]))
                if pe["filament"] is not None:
                    if not 1 <= pe["filament"] <= n:
                        chk.err(pe["where"] + ".filament", "slot %d does not exist; the file has %d slot(s)" % (pe["filament"], n))
                    elif p["meta"].get("extruder") is None:
                        chk.err(pe["where"] + ".filament", "part %r (%s) has no filament in the file, so it cannot be given one" % (pe["name"], p["subtype"]))
                    else:
                        add(("part", o["id"], p["id"]), "extruder", str(pe["filament"]))
    plate_types = {}
    for pj in job.plates:
        if pj["id"] is None:
            continue
        if pj["id"] > nplates:
            chk.err(pj["where"] + ".id", "the file has %d plate(s)" % nplates)
        elif pj["plate_type"] not in ps.bed_types:
            chk.err(pj["where"] + ".plate_type", _bad_plate(pj["plate_type"], ps))
        else:
            plate_types[pj["id"]] = pj["plate_type"]
            add(("plate", int(ms["plates"][pj["id"] - 1]["meta"].get("plater_id", pj["id"]))), "bed_type", pj["plate_type"])
    if req.plate_type:
        for pi, pl in enumerate(ms["plates"], 1):
            if "bed_type" in pl["meta"] and pi not in plate_types:
                add(("plate", int(pl["meta"].get("plater_id", pi))), "bed_type", req.plate_type)
    # ---- layer g-code ---------------------------------------------------------------------------
    layers = []
    nt = by["nozzle_temperature"]
    for g in job.layer_gcode:
        if not 1 <= g["plate"] <= nplates:
            chk.err("layer_gcode z=%s" % g["z"], "plate %d does not exist (the file has %d)" % (g["plate"], nplates))
            continue
        if g["kind"] == "temperature":
            lo, hi, v = nt.get("min"), nt.get("max"), g["value"]
            if not isinstance(v, (int, float)) or isinstance(v, bool) or (lo is not None and v < lo) or (hi is not None and v > hi):
                chk.err("layer_gcode z=%s" % g["z"], "temperature %r is outside %s to %s" % (v, lo, hi))
                continue
        layers.append(g)
    if chk.errors:
        raise PatchRefused(chk.errors)
    # a later edit of the same key wins
    last = {}
    for e in edits:
        last[(e[0], e[1])] = e
    order = list(dict.fromkeys((e[0], e[1]) for e in edits))
    edits = [last[k] for k in order]
    return req, edits, layers, warns, plate_types


def _first(v, default):
    if isinstance(v, list):
        v = v[0] if v else default
    return float(v if v is not None else default)


def _check_layers(layers, cfg):
    errs = []
    first = _first(cfg.get("initial_layer_print_height"), 0.2)
    lh = _first(cfg.get("layer_height"), 0.2)
    for g in layers:
        steps = (g["z"] - first) / lh
        if steps < -1e-6 or abs(g["z"] - (first + round(steps) * lh)) > 0.0011:
            near = max(first, first + round(steps) * lh)
            errs.append("[[layer_gcode]] z=%s is not the top of a layer (first layer %.3g, then every %.3g mm). Nearest layer top: %s" % (g["z"], first, lh, gs(near)))
    return errs


def _used_slots(src, edits, plate):
    """Slots named by the objects and parts on one plate, after the planned edits (extruder of the object and of its parts)."""
    def slot_of(v):
        """The slot number a value names; None for "0" (inherit: the object's filament, or the default) and for anything that is not a number."""
        v = str(v).strip() if v is not None else ""
        return v if v.isdigit() and int(v) > 0 else None
    ext = {}
    for o in src.ms["objects"]:
        ext[("object", o["id"])] = slot_of(o["meta"].get("extruder"))
        for p in o["parts"]:
            if slot_of(p["meta"].get("extruder")) is not None and p["subtype"] in ("normal_part", "modifier_part"):
                ext[("part", o["id"], p["id"])] = slot_of(p["meta"].get("extruder"))
    for scope, key, value in edits:
        if key == "extruder":
            if slot_of(value) is not None:
                ext[scope] = slot_of(value)
            elif scope[0] == "part":
                ext.pop(scope, None)                    # a part with 0 inherits the object's filament
            else:
                ext[scope] = None
    pl = src.ms["plates"][plate - 1]
    used = set()
    for inst in pl["inst"]:
        oid = inst.get("object_id")
        for scope, v in ext.items():
            if scope[1] == oid and v is not None and str(v).isdigit():
                if scope[0] == "part" or not any(s[0] == "part" and s[1] == oid for s in ext):
                    used.add(int(v) - 1)
    return used


def run(job, out_path=None, log=None):
    """Make the patched 3MF. Returns a PatchResult. Raises PatchRefused (a JobError) when it cannot, naming every reason."""
    say = log or (lambda m: None)
    if out_path:
        out = os.path.abspath(out_path)
    else:
        out = job.output if os.path.isabs(job.output) else os.path.join(os.path.dirname(job.path), job.output)
    if _same_file(job.patch, out):
        raise PatchRefused(["output is the same file as the input. Patch mode always writes a new file: give another name in output (or --out)."])
    say("reading %s" % os.path.basename(job.patch))
    src = open_source(job.patch)
    say("validating the input (default and strict) ...")
    d_def, d_strict = patch_verify.validate_both(src.path)
    errs = [f for f in d_def if f.level == "error"]
    if errs:
        raise PatchRefused(["The input does not validate (%d error(s)): %s. Open it in Studio and save it first." % (
            len(errs), "; ".join("%s %s" % (f.rule, f.msg[:90]) for f in errs[:3]))])
    ps = ProfileSet(src.tag)
    req, ms_edits, layers, warns, plate_types = build_request(job, src, ps)
    n_old = len(src.cfg["filament_colour"])
    n = n_old + len(req.adds) - (1 if req.remove else 0)
    say("editing project settings")
    ext_changes, ext_warns = [], []
    srcx = src
    if req.adds:
        cfg_x, ext_changes, ext_warns = patch_slots.extend_config(src, ps, req.adds, plate_type=req.plate_type)
        srcx = patch_slots.Shim(src, cfg_x)
    elif req.remove:
        srcx = patch_slots.Shim(src, patch_slots.drop_last_slot(src.cfg, ps, n_old))
        ext_changes = ["filament slot %d removed (nothing used it; no other slot was renumbered)" % n_old]
    if n != n_old:
        pe, pw = patch_slots.plate_map_edits(src, n_old, n)
        ms_edits = [e for e in ms_edits if not (e[0][0] == "plate" and e[1] in ("filament_maps", "filament_volume_maps"))] + pe
        ext_warns += pw
    res = patch_cfg.apply_config(srcx, ps, req)
    cfg = res.cfg
    lay = patch_cfg.Layout(cfg, n)
    errs = _check_layers(layers, cfg)
    # bed temperature of the plate type, for the slots in use
    ptypes = {}
    for pi in range(1, len(src.ms["plates"]) + 1):
        ptypes[pi] = plate_types.get(pi) or req.plate_type or src.ms["plates"][pi - 1]["meta"].get("bed_type") or cfg.get("curr_bed_type")
    # checked on every plate: a plate type change and a filament swap or bed setting change can each make a used filament unprintable
    for pi, pt in ptypes.items():
        if True:
                for slot, key in patch_cfg.bed_temp_problem(cfg, n, lay, ps.options, _used_slots(src, ms_edits, pi), pt):
                    errs.append("plate %d: %s does not support the filament in slot %d (%s = 0 for it). Pick another plate type or filament." % (pi, pt, slot, key))
    if errs:
        raise PatchRefused(errs)

    # ---- rule 5: the root model's metadata block, and source_file paths ------------------------------------------------------
    new_head, head_removed, credit = patch_slots.privacy_head(src.head)
    priv = []
    for k, why, _t in head_removed:
        priv.append("privacy: root-model metadata %s removed (%s)" % (k, why))
    path_warns = ["root-model metadata %s holds what looks like an absolute path. It is the designer's credit, so it was left as it is. Check it before you share the file." % k
                  for k in patch_slots.kept_with_path(src.head)]
    sf = [e for e in patch_slots.source_file_paths(src) if not any(x[0] == e[0] and x[1] == e[1] for x in ms_edits)]
    if sf:
        ms_edits = ms_edits + sf
        priv.append("privacy: source_file of %d part(s) reduced to the file name (it held a path)" % len(sf))
    # ---- sliced input -----------------------------------------------------------------------------------------------------------
    deletes = []
    if src.sliced:
        for pi, pl in enumerate(src.ms["plates"], 1):
            if "gcode_file" in pl["meta"]:
                deletes.append((("plate", int(pl["meta"].get("plater_id", pi))), "gcode_file"))

    # ---- model_settings.config --------------------------------------------------------------------------
    doc = patch_xml.MsDoc(src.ms_raw)
    try:
        for scope, key, value in ms_edits:
            doc.set(scope, key, value)
        for scope, key in deletes:
            doc.delete(scope, key)
    except patch_xml.XmlEditError as e:
        raise PatchRefused(["model_settings.config: %s" % e])
    # ---- custom G-code ------------------------------------------------------------------------------------
    gname = "Metadata/custom_gcode_per_layer.xml"
    by_plate = {}
    for g in layers:
        by_plate.setdefault(g["plate"], []).append(g)
    pause = cfg.get("machine_pause_gcode", "")
    gdata, recolored = None, 0
    had_g = src.pkg.has(gname)
    try:
        if had_g:
            gdata = src.pkg.raw(gname)
            if gdata is None:
                raise PatchRefused(["%s is too large to read" % gname])
            if by_plate:
                gdata = patch_xml.layer_xml_add(gdata, by_plate, pause, n)
            cols = {i + 1: s["color"] for i, s in req.slots.items() if s.get("color")}
            if cols:
                gdata, recolored = patch_xml.layer_recolor(gdata, cols)
            gdata = patch_xml.set_gcode_mode(gdata, n_old, n)
        elif by_plate:
            gdata = patch_xml.layer_xml_new(by_plate, pause, n)
    except patch_xml.XmlEditError as e:
        raise PatchRefused([str(e)])
    gchanged = gdata is not None and (not had_g or gdata != src.pkg.raw(gname))
    # ---- slice_info.config and filament_sequence.json -----------------------------------------------------------
    sname, qname = "Metadata/slice_info.config", "Metadata/filament_sequence.json"
    sdata = qdata = None
    if src.sliced and src.pkg.has(sname):
        raw_s = src.pkg.raw(sname)
        if raw_s is None:
            raise PatchRefused(["%s is too large to read" % sname])
        sdata, _cnt = patch_xml.strip_slice_plates(raw_s)
        if sdata == raw_s:
            sdata = None
    if src.pkg.has(qname) and (src.sliced or req.adds):
        raw_q = src.pkg.raw(qname)
        if raw_q is not None:
            qdata = patch_slots.sequence_edit(raw_q, n_old, add=len(req.adds), clear=src.sliced)

    # ---- what is written -------------------------------------------------------------------------------------
    new_cfg_bytes = dump_config(cfg, src.nl)
    cfg_changed = cfg != src.cfg
    plan = Plan()
    plan.n, plan.n_old, plan.req, plan.flush_slots = n, n_old, req, res.flush_slots
    plan.input_default = d_def
    plan.ms_edits = [(s, k, v) for s, k, v in ms_edits]
    plan.ms_deletes = list(deletes)
    plan.layers, plan.recolored = layers, recolored
    plan.added_gcode = gchanged and not had_g
    plan.removed = list(src.sliced_drop)
    plan.head_removed = [t for _k, _w, t in head_removed]
    plan.slice_info = sdata
    plan.sequence = qdata
    plan.edited = []
    if cfg_changed:
        plan.edited.append("Metadata/project_settings.config")
    if doc.d != src.ms_raw:
        plan.edited.append("Metadata/model_settings.config")
    if gchanged and had_g:
        plan.edited.append(gname)
    if sdata is not None:
        plan.edited.append(sname)
    if qdata is not None:
        plan.edited.append(qname)
    if new_head != src.head:
        plan.edited.append("3D/3dmodel.model")
    if not plan.edited and not plan.added_gcode and not plan.removed:
        raise PatchRefused(["The job asks for nothing that differs from the file (every value is already as requested). No file written."])

    say("writing %s" % os.path.basename(out))
    zin = src.pkg.z
    tmp = _make_temp(out, job.patch)
    try:
        w = patch_zip.PatchWriter(tmp)
    except Exception:
        _rm(tmp)
        raise
    try:
        for info in zin.infolist():
            name = info.filename
            utf8 = info.flag_bits & patch_zip.UTF8_FLAG
            if name in plan.removed:
                continue
            if name == "Metadata/project_settings.config" and plan.added_gcode:
                w.put(gname, gdata, utf8)
            if name == "Metadata/project_settings.config" and cfg_changed:
                w.put(name, new_cfg_bytes, utf8, info)
            elif name == "Metadata/model_settings.config" and doc.d != src.ms_raw:
                w.put(name, doc.d, utf8, info)
            elif name == gname and gchanged:
                w.put(name, gdata, utf8, info)
            elif name == sname and sdata is not None:
                w.put(name, sdata, utf8, info)
            elif name == qname and qdata is not None:
                w.put(name, qdata, utf8, info)
            elif name == "3D/3dmodel.model" and new_head != src.head:
                w.put_head(name, new_head, zin, info, len(src.head))
            else:
                w.copy(zin, info)
    except Exception:
        w.close()
        _rm(tmp)
        raise
    w.close()
    say("checking the output (validator runs on the whole file) ...")
    try:
        ck = patch_verify.verify(src, tmp, plan, d_strict, ps)
    except Exception:
        _rm(tmp)
        raise
    if not ck.ok:
        _rm(tmp)
        raise PatchRefused(["The patched file failed its own checks, so nothing was written. Failed: " + " | ".join(
            "%s%s" % (n_, (" (" + d + ")") if d else "") for n_, _ok, d in ck.failed())])
    os.replace(tmp, out)
    r = PatchResult()
    r.path, r.source, r.tag, r.build = out, os.path.basename(job.patch), src.tag, src.build
    r.cfg, r.old_cfg = cfg, src.cfg
    r.changes = list(ext_changes) + list(res.changes)
    for scope, key, value in ms_edits:
        r.changes.append(_ms_line(src, scope, key, value))
    r.changes += priv
    if src.sliced:
        r.changes.append("sliced input: %d G-code file(s), their checksums, the plate entries of slice_info.config and the G-code links removed" % len(src.gcode))
    for g in sorted(layers, key=lambda x: (x["plate"], x["z"])):
        r.changes.append("layer G-code: plate %d, z %s mm: %s" % (g["plate"], gs(g["z"]), {"pause": "pause", "temperature": "nozzle temperature %s C" % g["value"], "gcode": "custom G-code"}[g["kind"]]))
    if recolored:
        r.changes.append("custom G-code: %d colour/tool-change layer(s) given the new slot colour" % recolored)
    r.dropped, r.kept = res.dropped, res.kept
    r.warnings = list(src.warnings) + path_warns + list(ext_warns) + list(res.warnings) + warns + ([SLICED_NOTE % len(src.gcode)] if src.sliced else []) + [PICTURE_NOTE]
    r.credit = credit
    r.checks = ck.rows
    r.printer = cfg.get("printer_settings_id")
    r.process = cfg.get("print_settings_id")
    r.slots = n
    r.n_old = n_old
    return r


def _ms_line(src, scope, key, value):
    if scope[0] == "plate":
        return "plate %s: %s = %s" % (scope[1], key, value)
    o = next(o for o in src.ms["objects"] if o["id"] == scope[1])
    if scope[0] == "object":
        return "object %r: %s = %s" % (o["meta"].get("name"), key, value)
    p = next(p for p in o["parts"] if p["id"] == scope[2])
    return "object %r part %r: %s = %s" % (o["meta"].get("name"), p["meta"].get("name"), key, value)


def _make_temp(out, src_path):
    """A new, empty temp file in the output's folder. mkstemp creates it with O_EXCL, so it is a file this run made and never an existing one
    (an input named '<output>.part' stays untouched). Refuses if it is, or the output is, the same file as the input."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(out) or ".", prefix="." + os.path.basename(out) + ".", suffix=".part")
    os.close(fd)
    um = os.umask(0)
    os.umask(um)
    try:
        os.chmod(tmp, 0o666 & ~um)          # mkstemp makes 0600; a normal output gets the umask mode
    except OSError:
        pass
    if _same_file(src_path, tmp) or _same_file(src_path, out):
        _rm(tmp)
        raise PatchRefused(["the temporary file or the output is the same file as the input. Nothing was written."])
    return tmp


def _rm(path):
    try:
        os.remove(path)
    except OSError:
        pass


# ---- summary ---------------------------------------------------------------------------------------------------------
def format_summary(r):
    L = ["Patched %s" % os.path.abspath(r.path), "  from %s | Bambu Studio %s (build %s) | %s | %s" % (r.source, r.tag, r.build, r.printer, r.process), ""]
    L.append("Changes (%d)" % len(r.changes))
    L += ["  - " + c for c in r.changes] or ["  none"]
    for title, lst in (("Designer's changes dropped (the preset was swapped; keep_changes = true keeps them)", r.dropped),
                       ("Designer's changes kept", r.kept)):
        if lst:
            L.append("")
            L.append(title)
            for label, keys in lst:
                L.append("  %s: %s" % (label, ", ".join("%s (was %s)" % (k, v) for k, v in keys)))
    if getattr(r, "credit", None):
        L.append("")
        L.append("Designer credit kept as it was: %s" % ", ".join(r.credit))
    L.append("")
    L.append("Checks after writing: %d of %d passed" % (sum(1 for c in r.checks if c[1]), len(r.checks)))
    for w in r.warnings:
        L.append("")
        L.append("warning: " + w)
    return "\n".join(L) + "\n"


def format_summary_md(r, slice_info=None):
    L = ["# Patch summary: %s" % os.path.basename(r.path), "",
         "- Source file: %s" % r.source, "- Bambu Studio: %s (build %s)" % (r.tag, r.build),
         "- Printer: %s" % r.printer, "- Process: %s" % r.process, "", "## Changes", ""]
    L += ["- " + c for c in r.changes] or ["None."]
    L += ["", "## Designer's changes dropped (a preset was swapped)", ""]
    if r.dropped:
        for label, keys in r.dropped:
            L.append("- %s: %s" % (label, ", ".join("`%s` (was %s)" % (k, v) for k, v in keys)))
    else:
        L.append("None.")
    L += ["", "## Designer's changes kept", ""]
    if r.kept:
        for label, keys in r.kept:
            L.append("- %s: %s" % (label, ", ".join("`%s` = %s" % (k, v) for k, v in keys)))
    else:
        L.append("None.")
    L += ["", "## Designer credit kept", ""]
    L += ["- " + ", ".join(r.credit)] if getattr(r, "credit", None) else ["None."]
    L += ["", "## Checks after writing", ""]
    L += ["- %s: %s%s" % ("pass" if ok else "FAIL", name, (" (%s)" % d) if d and not ok else "") for name, ok, d in r.checks]
    L += ["", "## Warnings", ""]
    L += ["- " + w for w in r.warnings] if r.warnings else ["None."]
    if slice_info is not None:
        ok, msg, plates = slice_info
        L += ["", "## Studio slice check", "", "- Result: %s" % msg]
        for pl in plates or []:
            L.append("- plate %d: print time %s, filament %s g" % (pl["plate"], pl.get("time") or "n/a", ("%.2f" % pl["grams"]) if pl.get("grams") is not None else "n/a"))
        from .studio_detect import usage_table_md
        L += usage_table_md(plates or [])
    return "\n".join(L) + "\n"


def write_summary_file(r, slice_info=None):
    from .summary import summary_path
    p = summary_path(r.path)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(format_summary_md(r, slice_info))
    return p
