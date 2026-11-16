"""Patch: every edit of Metadata/project_settings.config.

apply_config() takes the parsed input config and a Request and returns a new dict. Nothing else is touched.
The request says what the job asks for; allowed_changes() derives from the request alone which (key, slot) pairs may
differ between input and output. patch_verify uses that to check what was written.
"""
import copy
import json
import os

from . import config as cfgmod
from . import flush as fl
from . import rules_config
from .config import BUILDER_KEYS, FILAMENT_DEV_OPTIONS
from .job import PLATE_KEY
from .patch_job import PatchRefused
from .privacy import HOST_KEYS, PATH_RE, SKIP_KEYS_FOR_PATTERNS
from .profiles import ProfileError, studio_root
from .serialize import normalize

PER_SLOT_LISTS = ("filament_settings_id", "filament_ids", "filament_colour", "filament_multi_colour")
SLOT_RANGE_KEYS = ("wall_filament", "sparse_infill_filament", "solid_infill_filament", "support_filament", "support_interface_filament")


class Request:
    """What the job asks for, after the checks that need the file. slots: {slot index from 0: {"preset","color","settings","keep"}}."""
    process = None
    keep_process = False
    reset = False
    settings = None
    slots = None
    plate_type = None
    adds = None            # [{"slot", "preset", "color"}] slots appended at the end (8b)
    remove = None          # slot number of the last slot to remove (8b)
    printer = None         # the new printer preset (8c); patch_printer.plan fills process / slot presets that Studio's alias match picks
    old_printer = None
    mapped = None          # summary lines of the presets that the alias match picked
    blank_hosts = None     # print-host keys that held a value and were blanked (rule 5, 8c); set by apply_config

    def __init__(self):
        self.settings, self.slots, self.adds, self.mapped, self.blank_hosts = {}, {}, [], [], []

    def swapped(self):
        return {i for i, s in self.slots.items() if s.get("preset")}


class Layout:
    def __init__(self, cfg, n):
        si = cfg.get("filament_self_index")
        if not isinstance(si, list) or not si:
            raise PatchRefused(["project_settings.config has no filament_self_index. Open the file in Studio and save it first."])
        self.widths = [si.count(str(i + 1)) for i in range(n)]
        if sum(self.widths) != len(si) or any(w < 1 for w in self.widths):
            raise PatchRefused(["filament_self_index does not match the %d filament slots. Open the file in Studio and save it first." % n])
        self.starts = [sum(self.widths[:i]) for i in range(n)]
        self.total = len(si)
        self.n = n


def _dev_layout(k, bases, by, defaults, ps, vals):
    """Segments (start, end) per slot of a filament_dev_* list, from the preset values of the slots. None if they do not add up."""
    out, at = [], 0
    for b in bases:
        if b is None:
            return None
        try:
            fc, _m = ps.resolve("filament", b)
        except ProfileError:
            return None
        raw = fc.get(k)
        fv = normalize(by[k], raw) if raw is not None else defaults.get(k)
        if fv is None:
            return None
        fv = fv if isinstance(fv, list) else [fv]
        out.append((at, at + len(fv)))
        at += len(fv)
    return out if at == len(vals) else None


class Ctx:
    """Everything the editor reads about the input: option table, layout, bases, diff lists."""

    def __init__(self, src, ps):
        self.src, self.ps, self.by = src, ps, ps.options
        cfg = src.cfg
        self.n = len(cfg["filament_colour"])
        self.lay = Layout(cfg, self.n)
        self.defaults = {}
        for k, o in self.by.items():
            d = o["default"]
            if d is not None and k in FILAMENT_DEV_OPTIONS:
                self.defaults[k] = normalize(o, d)
        dl = cfg.get("different_settings_to_system")
        ig = cfg.get("inherits_group")
        if dl is not None and not (isinstance(dl, list) and len(dl) == self.n + 2):
            raise PatchRefused(["different_settings_to_system has %s entries; this file has %d slots, so it needs %d. Open the file in Studio and save it first." % (
                len(dl) if isinstance(dl, list) else "no list of", self.n, self.n + 2)])
        if ig is not None and not (isinstance(ig, list) and len(ig) == self.n + 2):
            raise PatchRefused(["inherits_group does not have %d entries. Open the file in Studio and save it first." % (self.n + 2)])
        self.old_diff = [set(filter(None, (s or "").split(";"))) for s in (dl or [""] * (self.n + 2))]
        self.inh = list(ig) if ig else [""] * (self.n + 2)
        self.bases = rules_config.slot_bases(cfg, ps, self.n)       # [(kind, name in file, base or None)]

    def entry_label(self, idx):
        return "process" if idx == 0 else ("printer" if idx == self.n + 1 else "filament slot %d" % idx)

    def seg(self, k, i, vals, lay, dev):
        """(start, end) of slot i in the list vals of key k (a filament option), or None if the list has no clean layout."""
        o = self.by.get(k)
        if o is None or o["preset"] != "filament" or not o["vector"]:
            return None
        if k in FILAMENT_DEV_OPTIONS:
            d = dev.get(k)
            return d[i] if d else None
        if o["variant"] == "filament":
            return (lay.starts[i], lay.starts[i] + lay.widths[i]) if len(vals) == lay.total else None
        return (i, i + 1) if len(vals) == lay.n else None


def _compat_check(ctx, kind, name, label):
    """The new preset must exist (exact name), be selectable and made for the file's printer."""
    ps = ctx.ps
    try:
        cfg, meta = ps.resolve(kind, name)
    except ProfileError as e:
        raise PatchRefused(["%s: %s" % (label, e)])
    if meta["instantiation"] != "true":
        raise PatchRefused(["%s: %r is a base template, not a selectable preset" % (label, name)])
    printer = getattr(ctx, "printer", None) or ctx.bases[ctx.n + 1][2]
    if printer is None:
        raise PatchRefused(["%s: the printer preset of this file is a user preset with no parent in inherits_group, so the new preset cannot be checked against it. Open the file in Studio and save it first." % label])
    cp = cfg.get("compatible_printers")
    if cp and printer not in cp:
        raise PatchRefused(["%s: %r is not made for %r. Presets for this printer: %s.%s" % (
            label, name, printer, "; ".join(ps.compatible(kind, printer, like=name)[:8]) or "none found",
            ps.user_compat_note(kind, name, printer))])
    return meta


def _set_seg(cfg, k, rng, vals):
    cfg[k] = list(cfg[k][:rng[0]]) + list(vals) + list(cfg[k][rng[1]:])


def _val_text(v):
    if isinstance(v, list):
        v = v[0] if len(set(v)) == 1 else ",".join(str(x) for x in v)
    s = str(v)
    return s if len(s) <= 40 else s[:37] + "..."


class CfgResult:
    pass


def apply_config(src, ps, req):
    """Return a CfgResult: .cfg (new dict), .changes (plain-text lines), .dropped ([(label, [(key, old value)])]), .kept (same shape),
    .warnings, .touched (entry indexes whose diff list was rewritten), .flush_slots (slots whose purge cells were recalculated)."""
    ctx = Ctx(src, ps)
    n, by, lay = ctx.n, ctx.by, ctx.lay
    cfg_in = src.cfg
    cfg = copy.deepcopy(cfg_in)
    res = CfgResult()
    res.changes, res.dropped, res.kept, res.warnings = [], [], [], []
    res.touched, res.flush_slots = set(), []
    swapped = req.swapped()
    if req.reset and ((req.process and req.keep_process) or any(s.get("keep") for s in req.slots.values())):
        raise PatchRefused(["reset_to_preset and keep_changes cannot be used together (one drops the designer's changes, the other keeps them)."])
    if src.embedded and (req.process or swapped or req.settings or any(s["settings"] for s in req.slots.values())):
        kinds = sorted({os.path.basename(e).split("_settings")[0] for e in src.embedded})
        hit = (req.process and "process" in kinds) or (swapped and "filament" in kinds) or (req.settings and "process" in kinds) or \
              (any(s["settings"] for s in req.slots.values()) and "filament" in kinds) or (req.reset and ("process" in kinds or "filament" in kinds))
        if hit:
            raise PatchRefused(["This file stores %s preset(s) inside it (%s). A change to a preset or to settings of that kind would leave the stored copy out of date. Patch mode does not handle that in this version." % (
                "/".join(kinds), ", ".join(src.embedded))])

    # ---- names of the new state, and the system values of every slot in the new state ----------
    nm = dict(cfg)
    if req.process:
        nm["print_settings_id"] = req.process
    fsid = list(cfg["filament_settings_id"])
    ig_nm = list(ctx.inh)
    if req.process:
        ig_nm[0] = ""
    for i, s in req.slots.items():
        if s.get("preset"):
            fsid[i] = s["preset"]
            ig_nm[1 + i] = ""
    if req.printer:
        nm["printer_settings_id"] = req.printer
        ig_nm[n + 1] = ""
        ctx.printer = req.printer
    nm["filament_settings_id"], nm["inherits_group"] = fsid, ig_nm
    if req.process:
        _compat_check(ctx, "process", req.process, "process")
    for i, s in sorted(req.slots.items()):
        if s.get("preset"):
            _compat_check(ctx, "filament", s["preset"], "[[filament]] slot %d preset" % (i + 1))
    sysc, skipped, why = rules_config.system_config(nm, ps, n, src.nplates)
    if sysc is None:
        raise PatchRefused(["The system values of this file's presets cannot be rebuilt (%s), so the patch cannot be checked. Open the file in Studio and save it first." % why])
    new_bases = rules_config.slot_bases(nm, ps, n)
    lay_s = Layout(sysc, n)
    dev_old, dev_new = {}, {}
    for k in FILAMENT_DEV_OPTIONS:
        if k in cfg and k in sysc:
            dev_old[k] = _dev_layout(k, [b[2] for b in ctx.bases[1:n + 1]], by, ctx.defaults, ps, cfg[k])
            dev_new[k] = _dev_layout(k, [b[2] for b in new_bases[1:n + 1]], by, ctx.defaults, ps, sysc[k])

    def base_equal(k, idx):
        """True if the value of key k in entry idx is the base preset's value. False if it cannot be told."""
        if idx in skipped:
            return False
        o = by.get(k)
        if o is None or k not in sysc or k not in cfg:
            return False
        if idx == 0 or idx == n + 1:
            return cfg[k] == sysc[k]
        i = idx - 1
        if o["preset"] != "filament":
            return False
        a = ctx.seg(k, i, cfg[k], lay, dev_old)
        b = ctx.seg(k, i, sysc[k], lay_s, dev_new)
        if a is None or b is None:
            return False
        return cfg[k][a[0]:a[1]] == sysc[k][b[0]:b[1]]

    def restore(k, idx, source):
        """Set key k of entry idx to the value in `source` (cfg_in or sysc). False if the key has no clean layout."""
        if idx == 0:
            cfg[k] = copy.deepcopy(source[k])
            return True
        i = idx - 1
        lay_src = lay if source is cfg_in else lay_s
        dev_src = dev_old if source is cfg_in else dev_new
        a = ctx.seg(k, i, cfg[k], lay, dev_old)
        b = ctx.seg(k, i, source[k], lay_src, dev_src)
        if a is None or b is None:
            return False
        _set_seg(cfg, k, a, source[k][b[0]:b[1]])
        return True

    # ---- reset_to_preset (project level: process and filament slots) --------------------------
    if req.reset:
        for idx in [0] + [1 + i for i in range(n)]:
            if idx == 0 and req.process:
                continue
            if idx > 0 and (idx - 1) in swapped:
                continue
            gone = []
            for k in sorted(ctx.old_diff[idx]):
                if k in sysc and k in cfg and not base_equal(k, idx):
                    old = cfg[k]
                    if restore(k, idx, sysc):
                        gone.append((k, _val_text(_slot_val(ctx, k, idx, old, lay, dev_old))))
            if ctx.old_diff[idx] or gone:
                res.touched.add(idx)
            if gone:
                res.dropped.append((ctx.entry_label(idx), gone))
                res.changes.append("reset to preset: %s, %d setting(s)" % (ctx.entry_label(idx), len(gone)))

    # ---- process swap -----------------------------------------------------------------------------
    if req.process:
        old_name = cfg["print_settings_id"]
        gone, kept, count = [], [], 0
        for k, o in by.items():
            if o["preset"] != "print" or k in BUILDER_KEYS or k not in cfg or k not in sysc:
                continue
            if cfg[k] != sysc[k]:
                count += 1
            if k in ctx.old_diff[0]:
                if req.keep_process:
                    kept.append((k, _val_text(cfg[k])))
                    continue
                if cfg[k] != sysc[k]:
                    gone.append((k, _val_text(cfg[k])))
            cfg[k] = copy.deepcopy(sysc[k])
        cfg["print_settings_id"] = req.process
        if "print_compatible_printers" in cfg and "print_compatible_printers" in sysc:
            cfg["print_compatible_printers"] = copy.deepcopy(sysc["print_compatible_printers"])
        res.touched.add(0)
        res.changes.append("process preset: %s -> %s (%d setting values replaced by the new preset's)" % (old_name, req.process, count))
        if gone:
            res.dropped.append(("process", gone))
        if kept:
            res.kept.append(("process", kept))

    # ---- printer change (8c): the printer-owned keys are the new preset's -------------------------------------------------------------
    if req.printer:
        from . import patch_printer
        keys = patch_printer.printer_keys(by, cfg, sysc)
        gone, count = [], 0
        for k in keys:
            if k in ctx.old_diff[n + 1] and cfg[k] != sysc[k]:
                gone.append((k, _val_text(cfg[k])))
            if cfg[k] != sysc[k]:
                count += 1
            cfg[k] = copy.deepcopy(sysc[k])
        old_pn = cfg["printer_settings_id"]
        cfg["printer_settings_id"] = req.printer
        res.touched.add(n + 1)
        res.changes.append("printer: %s -> %s (%d printer setting values replaced by the new preset's)" % (old_pn, req.printer, count))
        for line in req.mapped:
            res.changes.append("preset match for the new printer, " + line)
        if gone:
            res.dropped.append(("printer", gone))

    # ---- printer change with another extruder variant count (A1 <-> P2S): cut every per-variant list again ------------------------
    recut = bool(req.printer and getattr(req, "recut", False))
    if recut:
        _recut_variants(ctx, cfg_in, cfg, sysc, lay, lay_s, req, res, swapped)
        lay = lay_s                                                 # from here on every list has the new printer's layout

    # ---- filament slots: preset swap ------------------------------------------------------------------
    for i in sorted(swapped):
        s = req.slots[i]
        idx = 1 + i
        old_name = cfg["filament_settings_id"][i]
        gone, kept, count = [], [], 0
        skipped_keys = []
        for k, o in by.items():
            if o["preset"] != "filament" or not o["vector"] or k in BUILDER_KEYS or k not in cfg or k not in sysc:
                continue
            if recut and o["variant"] == "filament":                  # already cut by _recut_variants; only report it here
                a = ctx.seg(k, i, cfg_in[k], ctx.lay, dev_old)
                b = ctx.seg(k, i, sysc[k], lay_s, dev_new)
                if a is None or b is None:
                    continue
                old_seg, new_seg = cfg_in[k][a[0]:a[1]], sysc[k][b[0]:b[1]]
                if k in ctx.old_diff[idx]:
                    if s.get("keep"):
                        kept.append((k, _val_text(old_seg)))
                        continue
                    if old_seg != new_seg:
                        gone.append((k, _val_text(old_seg)))
                if old_seg != new_seg:
                    count += 1
                continue
            a = ctx.seg(k, i, cfg[k], lay, dev_old)
            b = ctx.seg(k, i, sysc[k], lay_s, dev_new)
            if a is None or b is None:
                skipped_keys.append(k)
                continue
            old_seg = cfg[k][a[0]:a[1]]
            new_seg = sysc[k][b[0]:b[1]]
            if len(old_seg) != len(new_seg) and k not in FILAMENT_DEV_OPTIONS:
                raise PatchRefused(["slot %d preset %s: %s has %d value(s) in the file but %d in the new preset (another extruder variant count). Pick a preset made for this printer." % (
                    i + 1, s["preset"], k, len(old_seg), len(new_seg))])
            if k in ctx.old_diff[idx]:
                if s.get("keep"):
                    kept.append((k, _val_text(old_seg)))
                    continue
                if old_seg != new_seg:
                    gone.append((k, _val_text(old_seg)))
            if old_seg != new_seg:
                count += 1
            _set_seg(cfg, k, a, new_seg)
            if k in FILAMENT_DEV_OPTIONS:
                dev_old[k] = _recut(dev_old[k], i, len(new_seg))
        if skipped_keys:
            res.warnings.append("slot %d: %s left as they were (the list layout in the file is not the one the preset gives)" % (i + 1, ", ".join(sorted(skipped_keys))))
        cfg["filament_settings_id"][i] = s["preset"]
        cfg["filament_ids"][i] = sysc["filament_ids"][i]
        res.touched.add(idx)
        if ctx.bases[idx][2] is None:
            res.warnings.append("slot %d held a user preset with no parent preset; all of its values are replaced by %s" % (i + 1, s["preset"]))
        res.changes.append("filament slot %d preset: %s -> %s (%d setting values replaced by the new preset's)" % (i + 1, old_name, s["preset"], count))
        if gone:
            res.dropped.append(("filament slot %d" % (i + 1), gone))
        if kept:
            res.kept.append(("filament slot %d" % (i + 1), kept))

    # ---- settings: project ------------------------------------------------------------------------------
    for k in sorted(req.settings):
        v = req.settings[k]
        o = by[k]
        if k not in cfg:
            raise PatchRefused(["[settings].%s is not in this file's project settings. Patch mode changes settings that the file already holds." % k])
        old = cfg[k]
        if o["vector"]:
            cfg[k] = [v] * (len(old) if o["variant"] == "print" and isinstance(old, list) and old else 1)
        else:
            cfg[k] = v
        if k in SLOT_RANGE_KEYS and not 0 <= int(v) <= n:
            raise PatchRefused(["[settings].%s = %s: filament slots are 0 (auto) to %d" % (k, v, n)])
        res.touched.add(0)
        res.changes.append("setting %s: %s -> %s" % (k, _val_text(old), _val_text(cfg[k])))

    # ---- settings: filament slot ---------------------------------------------------------------------------
    for i in sorted(req.slots):
        for k in sorted(req.slots[i]["settings"]):
            v = req.slots[i]["settings"][k]
            o = by[k]
            if k not in cfg:
                raise PatchRefused(["[[filament]] slot %d settings.%s is not in this file's project settings." % (i + 1, k)])
            if not o["vector"]:
                raise PatchRefused(["[[filament]] slot %d settings.%s is not a per-slot setting; patch mode cannot place it." % (i + 1, k)])
            a = ctx.seg(k, i, cfg[k], lay, dev_old)
            if a is None:
                raise PatchRefused(["[[filament]] slot %d settings.%s: the list in the file has an unexpected length (%d). Open the file in Studio and save it first." % (i + 1, k, len(cfg[k]))])
            old = cfg[k][a[0]:a[1]]
            _set_seg(cfg, k, a, [v] * (a[1] - a[0]))
            res.touched.add(1 + i)
            res.changes.append("filament slot %d setting %s: %s -> %s" % (i + 1, k, _val_text(old), v))

    # ---- colour ---------------------------------------------------------------------------------------------
    colour_slots = []
    for i in sorted(req.slots):
        c = req.slots[i].get("color")
        if not c:
            continue
        old = cfg["filament_colour"][i]
        mc = cfg.get("filament_multi_colour")
        mixed = cfg.get("filament_is_mixed")
        if (isinstance(mc, list) and len(mc) == n and mc[i] not in (old, "")) or (isinstance(mixed, list) and len(mixed) == n and mixed[i] not in ("0", "")):
            raise PatchRefused(["[[filament]] slot %d: this slot holds a multi-colour or mixed filament; patch mode changes single colours only." % (i + 1)])
        if old == c:
            continue
        if isinstance(mc, list) and len(mc) == n:
            mc[i] = c
        cfg["filament_colour"][i] = c
        colour_slots.append(i)
        res.changes.append("filament slot %d colour: %s -> %s" % (i + 1, old, c))

    # ---- plate type -----------------------------------------------------------------------------------------
    if req.plate_type:
        old = cfg.get("curr_bed_type")
        if old != req.plate_type:
            cfg["curr_bed_type"] = req.plate_type
            res.changes.append("plate type: %s -> %s" % (old, req.plate_type))

    # ---- purge volumes ----------------------------------------------------------------------------------------
    flush_trigger = sorted(set(colour_slots) | swapped)
    if req.printer:
        flush_trigger = list(range(n))                              # the printer's own values (nozzle volume, retraction) enter the automatic matrix
    if flush_trigger and n >= 2:
        _flush(src, ps, cfg_in, cfg, n, flush_trigger, res, quiet=bool(req.printer))

    # ---- diff lists and inherits_group -----------------------------------------------------------------------------
    _diff_lists(ctx, cfg, req, res, swapped, base_equal)
    _privacy(cfg, req, res)
    res.cfg = cfg
    res.sysc = sysc
    return res


def _by_name(new_vals, new_names, old_vals, old_names):
    """new_vals with the value of every variant that old_names also has taken from old_vals. None if old_vals does not fit old_names."""
    if len(old_vals) == 1 and len(old_names) > 1:
        old_vals = list(old_vals) * len(old_names)
    if len(old_vals) != len(old_names) or len(new_vals) != len(new_names):
        return None
    out = list(new_vals)
    for j, nm in enumerate(new_names):
        if nm in old_names:
            out[j] = old_vals[old_names.index(nm)]
    return out


def _recut_variants(ctx, cfg_in, cfg, sysc, lay, lay_s, req, res, swapped):
    """Printer change between printers with another extruder variant count (patch_printer.plan sets req.recut). Every list with one value per
    variant takes the new presets' values (sysc, built like the builder builds the new state); a designer change that stays (the process or a
    slot that was not swapped, or keep_changes) is carried over by variant name. The printer's own lists were set by the printer block."""
    n, by = ctx.n, ctx.by
    vkeys = [k for k, o in by.items() if o["variant"] in ("print", "filament") and k not in BUILDER_KEYS]
    stray = sorted(k for k in vkeys if k in cfg_in and k not in sysc)
    if stray:
        raise PatchRefused(["printer change: %s hold one value per extruder variant but the new presets do not set them, so they cannot be cut for the new printer. Open the file in Studio, save it, and patch again." % ", ".join(stray[:6])])
    p_old = list(cfg_in.get("print_extruder_variant") or [])
    p_new = list(sysc.get("print_extruder_variant") or [])
    f_old = cfg_in.get("filament_extruder_variant") or []
    f_new = sysc.get("filament_extruder_variant") or []
    if len(f_old) != lay.total or len(f_new) != lay_s.total:
        raise PatchRefused(["printer change: filament_extruder_variant does not match filament_self_index, so the per-variant lists cannot be cut again. Open the file in Studio and save it first."])
    keep_proc = (not req.process) or bool(req.keep_process)
    dropped_var, unfit = set(), []
    for k in vkeys:
        if k not in cfg_in or k not in sysc:
            continue
        o = by[k]
        if o["variant"] == "print":
            new = copy.deepcopy(sysc[k]) if isinstance(sysc[k], list) else sysc[k]
            if keep_proc and k in ctx.old_diff[0] and isinstance(new, list) and isinstance(cfg_in[k], list):
                got = _by_name(new, p_new, cfg_in[k], p_old)
                if got is None:
                    unfit.append(k)
                else:
                    new = got
                    dropped_var |= {v for v in p_old if v not in p_new}
            cfg[k] = new
            continue
        if not o["vector"] or not isinstance(sysc[k], list) or len(sysc[k]) != lay_s.total or not isinstance(cfg_in[k], list) or len(cfg_in[k]) != lay.total:
            if isinstance(sysc[k], list) and len(sysc[k]) == lay_s.total:
                cfg[k] = copy.deepcopy(sysc[k])                     # the file's list had no clean layout: the presets' values
                res.warnings.append("printer change: %s had no clean per-slot layout in the file; it now holds the presets' values" % k)
                continue
            unfit.append(k)
            continue
        out = []
        for i in range(n):
            a0, a1 = lay.starts[i], lay.starts[i] + lay.widths[i]
            b0, b1 = lay_s.starts[i], lay_s.starts[i] + lay_s.widths[i]
            seg = list(sysc[k][b0:b1])
            keep_slot = (i not in swapped) or bool(req.slots.get(i, {}).get("keep"))
            if keep_slot and k in ctx.old_diff[1 + i]:
                on, nn = list(f_old[a0:a1]), list(f_new[b0:b1])
                got = _by_name(seg, nn, cfg_in[k][a0:a1], on)
                if got is None:
                    unfit.append("%s (slot %d)" % (k, i + 1))
                else:
                    seg = got
                    dropped_var |= {v for v in on if v not in nn}
            out += seg
        cfg[k] = out
    if unfit:
        raise PatchRefused(["printer change: these kept settings do not hold one value per extruder variant, so they cannot be carried to the new printer by variant name: %s. Use keep_changes = false or reset them in Studio first." % ", ".join(unfit[:6])])
    cfg["filament_self_index"] = copy.deepcopy(sysc["filament_self_index"])
    res.changes.append("extruder variants: %s -> %s; every list with one value per variant was cut again for the new printer" % (
        ", ".join(p_old) or "none", ", ".join(p_new) or "none"))
    if dropped_var:
        res.warnings.append("printer change: kept settings had values for %s, which %s does not have; those values are dropped" % (
            ", ".join(sorted(dropped_var)), req.printer))


def _privacy(cfg, req, res):
    """Rule 5 (remove only local data): print-host keys that hold a value are blanked. A setting value that holds an absolute path is only reported."""
    for k in HOST_KEYS:
        v = cfg.get(k)
        if v in (None, "", [], [""]):
            continue
        cfg[k] = [""] * len(v) if isinstance(v, list) else ""
        req.blank_hosts.append(k)
    if req.blank_hosts:
        res.changes.append("privacy: print-host setting(s) blanked (they are local to the designer's printer): %s" % ", ".join(req.blank_hosts))
    paths = []
    for k, v in cfg.items():
        if SKIP_KEYS_FOR_PATTERNS.search(k) or k == "version" or k.endswith("_version"):
            continue
        if PATH_RE.search(v if isinstance(v, str) else json.dumps(v)):
            paths.append(k)
    if paths:
        res.warnings.append("privacy: setting(s) %s hold an absolute path of the designer's computer. Patch mode does not change them. Clear them in Studio before you share the file." % ", ".join(paths))


def _recut(layout_list, i, new_len):
    """Segments of a dev list after slot i changed to a segment of new_len entries."""
    if not layout_list:
        return layout_list
    out, at = [], 0
    for j, (s, e) in enumerate(layout_list):
        ln = new_len if j == i else e - s
        out.append((at, at + ln))
        at += ln
    return out


def _slot_val(ctx, k, idx, vals, lay, dev):
    if idx == 0 or not isinstance(vals, list):
        return vals
    r = ctx.seg(k, idx - 1, vals, lay, dev)
    return vals[r[0]:r[1]] if r else vals


def _flush(src, ps, cfg_in, cfg, n, trigger, res, quiet=False):
    """Studio recomputes the purge row and column of a slot whose colour or preset changed (Sidebar::auto_calc_flushing_volumes) unless the
    designer edited them. Compare the file's cells with the automatic values of the OLD state: equal = not edited = recompute."""
    key = "flush_volumes_matrix"
    m = cfg.get(key)
    if not isinstance(m, list) or len(m) != n * n:
        res.warnings.append("the purge matrix in the file has %s entries, not %d; it is left as it is" % (len(m) if isinstance(m, list) else "no", n * n))
        return
    try:
        tables = fl.Tables(os.path.join(studio_root(), ps.version, "flush"))
        ds = int(cfg.get("nozzle_flush_dataset", ["0"])[0]) if cfg.get("nozzle_flush_dataset") else 0
        old_auto = fl.build_matrix(cfg_in, tables, mode="auto", nozzle_flush_dataset=ds)["matrix"]
        new_auto = fl.build_matrix(cfg, tables, mode="auto", nozzle_flush_dataset=ds)["matrix"]
    except Exception as e:                                          # noqa: BLE001 - a file the purge code cannot read
        res.warnings.append("purge volumes were not recalculated (%s: %s); the matrix in the file is left as it is" % (type(e).__name__, str(e)[:80]))
        return
    mine = cfg_in[key]

    def cell(arr, r, c):
        return float(arr[r * n + c])
    ok = []
    for i in trigger:
        if quiet and all(cell(old_auto, i, j) == cell(new_auto, i, j) and cell(old_auto, j, i) == cell(new_auto, j, i) for j in range(n) if j != i):
            continue                                                # printer change: this row and column do not change, nothing to say
        same = all(cell(mine, i, j) == cell(old_auto, i, j) and cell(mine, j, i) == cell(old_auto, j, i) for j in range(n) if j != i)
        if same:
            ok.append(i)
        else:
            res.warnings.append("slot %d: the purge volumes in the file differ from Studio's automatic values (set by the designer, or never calculated); they are kept as they are" % (i + 1))
    cells = 0
    for r in range(n):
        for c in range(n):
            if r == c:
                continue
            ch = [x for x in (r, c) if x in trigger]
            if ch and all(x in ok for x in ch) and cell(mine, r, c) != cell(new_auto, r, c):
                m[r * n + c] = new_auto[r * n + c]
                cells += 1
    res.flush_slots = ok
    if ok:
        res.changes.append("purge volumes recalculated for slot(s) %s (%d cell(s) changed)" % (", ".join(str(i + 1) for i in ok), cells))


def _diff_lists(ctx, cfg, req, res, swapped, base_equal):
    n, by = ctx.n, ctx.by
    job_keys = {0: set(req.settings)}
    for i, s in req.slots.items():
        job_keys[1 + i] = set(s["settings"])
    entries = []
    new_lists = [set(x) for x in ctx.old_diff]
    new_inh = list(ctx.inh)
    for idx in sorted(res.touched):
        if idx == n + 1:                                            # printer change: every value is the new preset's, so nothing differs
            new_lists[idx] = set()
            entries.append(idx)
            new_inh[idx] = ""
            continue
        is_swap = idx > 0 and (idx - 1) in swapped or (idx == 0 and bool(req.process))
        is_reset = req.reset and idx <= n and not is_swap
        if is_reset or (is_swap and not _keeps(req, idx)):
            keys = set()
        else:
            keys = set(ctx.old_diff[idx])
        keys |= job_keys.get(idx, set())
        check = set(keys) if is_swap else set(job_keys.get(idx, set()))
        keys = {k for k in keys if not (k in check and base_equal(k, idx))}
        if keys != ctx.old_diff[idx] or is_swap or is_reset:
            new_lists[idx] = keys
            entries.append(idx)
            kind, nm, _b = ctx.bases[idx]
            name_now = cfg["print_settings_id"] if idx == 0 else cfg["filament_settings_id"][idx - 1]
            if _is_system(ctx.ps, "process" if idx == 0 else "filament", name_now):
                new_inh[idx] = ""
    res.diff_entries = entries
    if not entries:
        return
    out = [";".join(sorted(s)) for s in new_lists]
    # keep an entry's string as it was if its set did not change (the designer's own order)
    old_dl = cfg.get("different_settings_to_system")
    if old_dl:
        out = [old_dl[i] if set(filter(None, old_dl[i].split(";"))) == new_lists[i] else out[i] for i in range(n + 2)]
    # Studio writes each list only if one of its entries is not empty (PresetBundle.cpp add_if_some_non_empty)
    for key, vals in (("different_settings_to_system", out), ("inherits_group", new_inh)):
        if any(vals):
            _put_sorted(cfg, key, vals)
        else:
            cfg.pop(key, None)


def _keeps(req, idx):
    if idx == 0:
        return bool(req.keep_process)
    return bool(req.slots.get(idx - 1, {}).get("keep"))


def _is_system(ps, kind, name):
    try:
        ps.resolve(kind, name)
        return True
    except ProfileError:
        return False


def _put_sorted(cfg, key, value):
    """Set cfg[key]; a new key goes in the sorted place (Studio writes its keys sorted)."""
    if key in cfg:
        cfg[key] = value
        return
    items = list(cfg.items())
    cfg.clear()
    placed = False
    for k, v in items:
        if not placed and k > key:
            cfg[key] = value
            placed = True
        cfg[k] = v
    if not placed:
        cfg[key] = value


# ---- what may differ: derived from the request alone ---------------------------------------------------------------
def allowed_changes(req, n, by, flush_slots):
    """The set of (key, where) the output config may differ in. where: None (whole key), a slot index, ("cell", r, c), ("entry", idx), "key" (added or removed)."""
    ok = set()
    swapped = req.swapped()
    print_keys = [k for k, o in by.items() if o["preset"] == "print"]
    fil_keys = [k for k, o in by.items() if o["preset"] == "filament" and o["vector"]]
    if req.process or req.reset:
        for k in print_keys:
            ok.add((k, None))
    if req.process:
        ok |= {("print_settings_id", None), ("print_compatible_printers", None)}
    for k in req.settings:
        ok.add((k, None))
    for i in req.slots:
        s = req.slots[i]
        if s.get("preset") or req.reset:
            for k in fil_keys:
                ok.add((k, i))
        if s.get("preset"):
            ok |= {("filament_settings_id", i), ("filament_ids", i)}
        for k in s["settings"]:
            ok.add((k, i))
        if s.get("color"):
            ok |= {("filament_colour", i), ("filament_multi_colour", i)}
    if req.reset:
        for i in range(n):
            for k in fil_keys:
                ok.add((k, i))
    if req.plate_type:
        ok.add(("curr_bed_type", None))
    if req.printer:
        ok |= {(k, None) for k, o in by.items() if o["preset"] == "printer"}
        ok.add(("printer_settings_id", None))
        if getattr(req, "recut", False):                            # every per-variant list is cut again (another variant count)
            ok |= {(k, None) for k, o in by.items() if o["variant"]}
            ok.add(("filament_self_index", None))
    for k in req.blank_hosts:
        ok.add((k, None))
    trig = set(flush_slots)
    for r in range(n):
        for c in range(n):
            if r != c and (r in trig or c in trig):
                ok.add(("flush_volumes_matrix", ("cell", r, c)))
    for idx in range(n + 2):
        ok.add(("different_settings_to_system", ("entry", idx)))
        ok.add(("inherits_group", ("entry", idx)))
    ok |= {("different_settings_to_system", "key"), ("inherits_group", "key")}
    return ok


def diff_config(old, new, n, by, lay_old):
    """The set of (key, where) in which two configs differ (the same vocabulary as allowed_changes)."""
    out = set()
    for k in set(old) | set(new):
        a, b = old.get(k), new.get(k)
        if a == b:
            continue
        if k not in old or k not in new:
            out.add((k, "key"))
            if k in ("different_settings_to_system", "inherits_group"):
                other = new.get(k) if k in new else old.get(k)
                for idx, v in enumerate(other):
                    if v:
                        out.add((k, ("entry", idx)))
            continue
        if k in ("different_settings_to_system", "inherits_group") and isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            for idx in range(len(a)):
                if a[idx] != b[idx]:
                    out.add((k, ("entry", idx)))
            continue
        if k == "flush_volumes_matrix" and isinstance(a, list) and isinstance(b, list) and len(a) == len(b) == n * n:
            for j in range(n * n):
                if a[j] != b[j]:
                    out.add((k, ("cell", j // n, j % n)))
            continue
        o = by.get(k)
        if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            if k in PER_SLOT_LISTS and len(a) == n:
                for i in range(n):
                    if a[i] != b[i]:
                        out.add((k, i))
                continue
            if o and o["preset"] == "filament" and o["vector"] and k not in FILAMENT_DEV_OPTIONS and o["variant"] == "filament" and len(a) == lay_old.total:
                for i in range(n):
                    s, w = lay_old.starts[i], lay_old.widths[i]
                    if a[s:s + w] != b[s:s + w]:
                        out.add((k, i))
                continue
            if o and o["preset"] == "filament" and o["vector"] and k not in FILAMENT_DEV_OPTIONS and o["variant"] != "filament" and len(a) == n:
                for i in range(n):
                    if a[i] != b[i]:
                        out.add((k, i))
                continue
        out.add((k, None))
    return out


def dev_allowed(diffs, req):
    """filament_dev_* lists cannot be cut per slot from the file; they may differ when a slot preset was swapped or reset."""
    return bool(req.swapped()) or req.reset


def check_allowed(diffs, allowed, req):
    """Differences that the request does not allow."""
    bad = []
    for key, where in sorted(diffs, key=lambda t: (t[0], str(t[1]))):
        if (key, where) in allowed:
            continue
        if key in FILAMENT_DEV_OPTIONS and where is None and dev_allowed(diffs, req):
            continue
        if key in ("different_settings_to_system", "inherits_group") and where == "key":
            continue
        bad.append((key, where))
    return bad


def bed_temp_problem(cfg, n, lay, by, slots, plate_type):
    """Names of the used slots whose bed temperature for this plate type is 0 (Print::validate: the filament does not support the plate)."""
    key = PLATE_KEY.get(plate_type)
    if not key or key not in cfg or key not in by:
        return []
    vals = cfg[key]
    if not isinstance(vals, list):
        return []
    o = by[key]
    out = []
    for i in sorted(slots):
        if o["variant"] == "filament" and len(vals) == lay.total:
            seg = vals[lay.starts[i]:lay.starts[i] + lay.widths[i]]
        elif len(vals) == n:
            seg = [vals[i]]
        else:
            continue
        if seg and all(str(v) == "0" for v in seg):
            out.append((i + 1, key))
    return out
