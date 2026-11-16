"""Patch mode: change the printer of a project.

Allowed only between single-nozzle printers with the same bed (printable_area, printable_height, bed_exclude_area) and the same nozzle
diameter. Anything else is refused with the reason. Parts are never moved.

Extruder variants (A1 <-> P2S): when the two printers have a different number of extruder variants (printer_extruder_variant, A1 1,
P2S 3), every list with one value per variant is cut again for the new printer (req.recut, patch_cfg._recut_variants): the values are the
new presets', and a designer change that is kept is carried over by variant name (a variant the new printer does not have is dropped and
named in the summary). Object, part and plate settings that hold one value per variant are refused, because they would not fit.

Preset mapping follows what the Studio app does when the printer changes (PresetBundle::update_compatible, PresetBundle.cpp 2.8.2 :5620,
2.9.1 :7340):
- a process or filament preset that is still compatible with the new printer stays (2.8.2 :5736-5777, 2.9.1 :7380-7432);
- an incompatible one is replaced by the compatible preset with the same alias: "Matching an alias, always take this preset with
  priority" (PreferedProfileMatch 2.8.2 :5625-5648 (:5638), 2.9.1 :7185-7208 (:7198); PreferedFilamentsProfileMatch 2.8.2 :5705-5730
  (:5720), 2.9.1 :7346-7371 (:7361)); first_compatible_idx returns the first preset with the highest quality (Preset.hpp 2.8.2 :641-665,
  2.9.1 :661-685);
- alias = the name up to the first "@", right-trimmed; a name without "@" is its own alias (load_vendor_configs_from_json, PresetBundle.cpp
  2.8.2 :5012-5027, 2.9.1 :6571-6586). The BBL profiles carry no "alias" key.
Studio takes the first alias match silently. Here exactly one candidate is required, else the patch is refused and names the slot and
the candidates. A job may give an explicit preset for the process or for a slot; that one is used and only checked for the new printer.
"""
from . import config as cfgmod
from .patch_job import PatchRefused
from .profiles import ProfileError
from .rules_config import slot_bases

_CACHE = {}


def alias_of(name):
    """Studio's alias of a preset name (the BBL profiles have no explicit alias)."""
    if "@" in name:
        a = name[:name.index("@")].rstrip()
        if a:
            return a
    return name


def _nums(v):
    out = []
    for p in (v if isinstance(v, list) else [v]):
        for q in str(p).replace(",", "x").split("x"):
            q = q.strip()
            if q:
                out.append(float(q))
    return out


def _bed(cfg):
    """(printable_area, printable_height, bed_exclude_area) as numbers. A missing key is the empty list."""
    try:
        return (_nums(cfg.get("printable_area", [])), _nums(cfg.get("printable_height", "0")), _nums(cfg.get("bed_exclude_area", [])))
    except ValueError:
        return None


def compatible_names(ps, kind, printer):
    """Selectable presets of this kind made for this printer (compatible_printers holds the printer; Preset.cpp is_compatible_with_printer)."""
    key = (ps.version, kind, printer)
    if key not in _CACHE:
        out = []
        for n in ps.names(kind):
            cfg, _m = ps.resolve(kind, n)
            cp = cfg.get("compatible_printers")
            if not cp or printer in cp:
                out.append(n)
        _CACHE[key] = out
    return _CACHE[key]


def single_nozzle_printers(ps):
    return [n for n in ps.names("machine") if len(ps.resolve("machine", n)[0].get("nozzle_diameter", [])) == 1]


def same_bed_printers(ps, printer):
    """The single-nozzle printers with the bed (area, height, exclude area) and the nozzle diameter of this printer. Derived from the profiles."""
    cfg, _m = ps.resolve("machine", printer)
    sig = _signature(cfg, False)
    return sorted(n for n in single_nozzle_printers(ps) if _signature(ps.resolve("machine", n)[0], False) == sig)


def change_targets(ps, printer):
    """The printers a patch can change this one to: the same bed and nozzle, and the same number of extruder variants (the lists of the file
    have one segment per variant; the CLI exits on a mismatch, BambuStudio.cpp 2.9.1 :4355)."""
    cfg, _m = ps.resolve("machine", printer)
    sig = _signature(cfg, True)
    return sorted(n for n in single_nozzle_printers(ps) if _signature(ps.resolve("machine", n)[0], True) == sig)


def _signature(cfg, variants):
    b = _bed(cfg)
    nz = cfg.get("nozzle_diameter", [])
    if not b:
        return None
    return (tuple(b[0]), tuple(b[1]), tuple(b[2]), tuple(_nums(nz))) + ((len(cfg.get("printer_extruder_variant", []) or []),) if variants else ())


def _printer_reasons(ps, old_name, old_cfg, new_name, new_cfg, file_cfg):
    why = []
    for lbl, c in (("the printer in the file (%s)" % old_name, old_cfg), ("the new printer (%s)" % new_name, new_cfg)):
        if len(c.get("nozzle_diameter", [])) != 1:
            why.append("%s has %d nozzles. Patch mode changes the printer between single-nozzle printers only." % (lbl, len(c.get("nozzle_diameter", []))))
    if why:
        return why
    ob, nb = _bed(old_cfg), _bed(new_cfg)
    fb = _bed(file_cfg)
    if ob is None or nb is None or fb is None:
        return ["the bed of the printer cannot be read from the settings"]
    names = ("printable_area", "printable_height", "bed_exclude_area")
    for i, nm in enumerate(names):
        if ob[i] != nb[i]:
            why.append("%s differs between %s and %s. Another bed moves every plate cell in the project (plate origin = column x bed width x 1.2), and parts are never moved." % (nm, old_name, new_name))
        elif fb[i] != nb[i]:
            why.append("%s in the file is not the value of the preset %s, so the plates would move when the printer preset is applied." % (nm, old_name))
    ond, nnd, fnd = _nums(old_cfg["nozzle_diameter"]), _nums(new_cfg["nozzle_diameter"]), _nums(file_cfg.get("nozzle_diameter", []))
    if ond != nnd or fnd != nnd:
        why.append("the nozzle diameter differs (%s in the file, %s for %s). Choose the printer preset with the same nozzle." % (
            "/".join("%g" % x for x in fnd), "/".join("%g" % x for x in nnd), new_name))
    if why:
        same = [n for n in same_bed_printers(ps, old_name) if n != old_name]
        why.append("Printers this patch can change %s to (same bed, same nozzle): %s." % (old_name, ", ".join(same) if same else "none"))
    return why


def variant_names(cfg):
    v = cfg.get("printer_extruder_variant") or []
    return list(v) if isinstance(v, list) else [v]


def variant_keys(by):
    """Options with one value per extruder variant (print, filament and printer presets)."""
    return {k for k, o in by.items() if o["variant"]}


def _ms_variant_values(src, by):
    """Object, part and plate settings in model_settings.config that hold one value per extruder variant (more than one value)."""
    keys = variant_keys(by)
    hits = []
    for o in src.ms["objects"]:
        scopes = [("object %r" % o["meta"].get("name"), o["meta"])] + [("part %r of %r" % (p["meta"].get("name"), o["meta"].get("name")), p["meta"]) for p in o["parts"]]
        for lbl, meta in scopes:
            for k, v in meta.items():
                if k in keys and len([x for x in str(v).replace(";", ",").split(",") if x != ""]) > 1:
                    hits.append("%s: %s" % (lbl, k))
    for pi, pl in enumerate(src.ms["plates"], 1):
        for k, v in pl["meta"].items():
            if k in keys and len([x for x in str(v).replace(";", ",").split(",") if x != ""]) > 1:
                hits.append("plate %d: %s" % (pi, k))
    return hits


def plan(src, ps, req, new_printer):
    """Check the printer change and fill the request: req.printer, req.process and req.slots[i]["preset"] (where the job gave none and the
    old preset is not compatible with the new printer), req.mapped (summary lines). Raises PatchRefused with every reason."""
    errs = []
    cfg = src.cfg
    n = len(cfg["filament_colour"])
    old_name = cfg.get("printer_settings_id")
    bases = slot_bases(cfg, ps, n)
    if src.embedded:
        mach = [e for e in src.embedded if "machine" in e.rsplit("/", 1)[-1]]
        if mach:
            raise PatchRefused(["This file stores the printer preset inside it (%s). Patch mode does not change the printer of such a file." % ", ".join(mach)])
    try:
        new_cfg, new_meta = ps.resolve("machine", new_printer)
    except ProfileError as e:
        raise PatchRefused(["printer: %s" % e])
    if new_meta["instantiation"] != "true":
        raise PatchRefused(["printer: %r is a base template, not a selectable printer preset" % new_printer])
    if new_printer == old_name:
        raise PatchRefused(["printer: the file already uses %r. Remove printer from the job." % new_printer])
    old_base = bases[n + 1][2]
    if old_base is None:
        raise PatchRefused(["The printer preset of this file (%r) is not a Bambu system preset, so the printer cannot be changed. Open the file in Studio, choose a system printer preset and save it first." % old_name])
    old_cfg, _om = ps.resolve("machine", old_base)
    why = _printer_reasons(ps, old_base, old_cfg, new_printer, new_cfg, cfg)
    if why:
        raise PatchRefused(["printer %s -> %s refused: %s" % (old_name, new_printer, " ".join(why))])
    req.printer = new_printer
    req.old_printer = old_name
    req.mapped = []
    ov, nv = variant_names(old_cfg), variant_names(new_cfg)
    req.recut = len(ov) != len(nv)
    if req.recut:
        if variant_names(cfg) != ov:
            raise PatchRefused(["printer %s -> %s refused: the extruder variants in the file (%s) are not those of %s (%s). Open the file in Studio and save it first." % (
                old_name, new_printer, ", ".join(variant_names(cfg)) or "none", old_base, ", ".join(ov))])
        hits = _ms_variant_values(src, ps.options)
        if hits:
            raise PatchRefused(["printer %s -> %s refused: %s has %d extruder variant(s) and %s has %d, and these object, part or plate settings hold one value per variant, which would not fit: %s. Remove them in Studio first, or patch to a printer with the same variants." % (
                old_name, new_printer, old_base, len(ov), new_printer, len(nv), "; ".join(hits[:6]))])
        gone = [v for v in ov if v not in nv]
        req.mapped.append("extruder variants: %s -> %s; lists with one value per variant are cut again for %s%s" % (
            ", ".join(ov), ", ".join(nv), new_printer, (" (values for %s are dropped)" % ", ".join(gone)) if gone else ""))

    def map_one(kind, label, current, base, given):
        """Return the preset for the new printer, or None to keep the current one. Append to errs when it cannot be decided."""
        if given:
            return None                                              # the job names it; apply_config checks it against the new printer
        if base is None:
            errs.append("%s: the preset %r is not a Bambu system preset and has no parent in the file, so it cannot be matched. Give %s in the job." % (
                label, current, "process" if kind == "process" else "preset for this slot"))
            return None
        cfgp, _m = ps.resolve(kind, base)
        cp = cfgp.get("compatible_printers")
        if not cp or new_printer in cp:
            return None                                              # still compatible: Studio keeps it
        al = alias_of(base)
        cand = [c for c in compatible_names(ps, kind, new_printer) if alias_of(c) == al]
        if len(cand) == 1:
            req.mapped.append("%s: alias %r, the only preset with it made for %s" % (label, al, new_printer))
            return cand[0]
        errs.append("%s: %r is not made for %s and %s. Give %s in the job.%s" % (
            label, current, new_printer,
            ("no preset with the alias %r is" % al) if not cand else ("%d presets have the alias %r (%s). Studio would take the first; patch mode will not guess" % (len(cand), al, "; ".join(cand))),
            "process = \"...\"" if kind == "process" else "preset = \"...\" for this slot",
            (" Presets for %s that are close: %s." % (new_printer, "; ".join(ps.compatible(kind, new_printer, like=current)[:5]) or "none")) if not cand else ""))
        return None

    got = map_one("process", "process", cfg.get("print_settings_id"), bases[0][2], req.process)
    if got:
        req.process = got
    for i in range(n):
        slot = req.slots.get(i)
        given = slot.get("preset") if slot else None
        got = map_one("filament", "slot %d" % (i + 1), cfg["filament_settings_id"][i], bases[1 + i][2], given)
        if got:
            if slot is None:
                req.slots[i] = {"preset": got, "color": None, "settings": {}, "keep": False}
            else:
                slot["preset"] = got
    if errs:
        raise PatchRefused(errs)
    if req.recut:                                                    # every slot must carry one value per variant of the new printer
        wide = []
        for i in range(n):
            pre = (req.slots.get(i) or {}).get("preset") or bases[1 + i][2]
            try:
                fc, _m = ps.resolve("filament", pre)
            except ProfileError:
                continue                                             # apply_config reports it
            w = len(fc.get("filament_extruder_variant") or []) or len(nv)
            if w != len(nv):
                wide.append("slot %d (%s: %d)" % (i + 1, pre, w))
        if wide:
            raise PatchRefused(["printer %s -> %s refused: %s has %d extruder variants, but these filament presets carry another number: %s. Studio maps such presets by variant name; patch mode does not do that in this version." % (
                old_name, new_printer, new_printer, len(nv), "; ".join(wide))])
    return req


# ---- values -----------------------------------------------------------------------------------------------------------------------
def printer_keys(by, cfg, sysc):
    """The printer-owned keys of the project config that the new printer preset sets (the builder writes them: config.py written_keys)."""
    return [k for k, o in by.items() if o["preset"] == "printer" and k in cfg and k in sysc and k not in cfgmod.BUILDER_KEYS]


def expected_value(by, mcfg, k):
    """What the printer preset holds for key k, built without the editor's own code path (for the check after writing)."""
    from .serialize import normalize
    o = by[k]
    raw = mcfg.get(k)
    if raw is None:
        d = o["default"]
        return normalize(o, d) if d is not None else None
    return normalize(o, raw)
