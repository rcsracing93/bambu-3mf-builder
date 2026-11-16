"""Validator rules for Metadata/project_settings.config (R3-R17, R35, R36). Table-driven from studio/<version>/options.json."""
import math
import re

from .profiles import ProfileSet, ProfileError, STUDIO_VERSIONS
from . import config as cfgmod


def _digits(s):
    """ASCII digits only (str.isdigit accepts other scripts)."""
    return isinstance(s, str) and re.fullmatch(r"[0-9]+", s) is not None


HEADER = ("version", "name", "from")
MAX_REPORT = 25


def version_key(v):
    try:
        return ".".join(str(int(x)) for x in str(v).split("."))
    except ValueError:
        return None


def load_profiles(cfg, app, rep):
    where = "Metadata/project_settings.config"
    # checks that need no option table, so they run for every version
    for k in ("printer_settings_id", "print_settings_id"):
        if not isinstance(cfg.get(k), str) or not cfg.get(k):
            rep.err("R10", where, "%s is missing or not a preset name; Studio cannot match the project to a printer or process" % k)
    if not isinstance(cfg.get("filament_settings_id"), list) or not cfg.get("filament_settings_id"):
        rep.err("R10", where, "filament_settings_id is missing or empty")
    if not str(cfg.get("printer_model", "")).startswith("Bambu Lab"):
        rep.err("R9", where, "printer_model %r is not a Bambu printer; Studio drops the project config" % cfg.get("printer_model"))
    if rep.errors_for("R10"):
        return None
    v = version_key(cfg.get("version", ""))
    build_other = None
    if v not in STUDIO_VERSIONS:
        # the same major.minor.patch with another build (2.8.2.60): the tables of the supported build apply, with a warning
        near = [sv for sv in STUDIO_VERSIONS if v is not None and sv.split(".")[:3] == v.split(".")[:3]]
        if not near:
            rep.warn("R16", "project_settings version", "Studio version %r is not supported (supported: %s); the table rules were skipped" % (cfg.get("version"), ", ".join(STUDIO_VERSIONS)))
            return None
        build_other, v = v, near[0]
    try:
        ps = ProfileSet(v)
    except ProfileError as e:
        rep.warn("R16", "profiles", str(e), cls="SKIPPED")
        return None
    ps.build_other = build_other
    if build_other:
        rep.warn("R16", "project_settings version", "Studio build %s is not a supported build (supported: %s); the tables of %s are applied (the system presets of the other build can differ, and Studio resets unlisted values to its own), so a key that build does not know is a warning, not an error (%s)" % (
            build_other, ", ".join(STUDIO_VERSIONS), ps.tag_version, LOAD_EXTERNAL_PRESET), cls="CHANGE")
    stamp = str(cfg.get("version")) if build_other else ps.tag_version
    if app and app != "BambuStudio-" + stamp:
        rep.err("R15", "project_settings version", "Application is %s but project_settings says version %s" % (app, cfg.get("version")))
    return ps


def _lst(v):
    return v if isinstance(v, list) else [v]


def _is_system(ps, kind, name):
    try:
        ps.resolve(kind, name)
        return True
    except ProfileError:
        return False


def slot_bases(cfg, ps, n):
    """The preset behind every slot of the project: slot 0 process, 1..n filaments, n + 1 printer. Returns [(kind, name in the file, base)].
    base is the preset itself if it is a system preset; else the parent that inherits_group names for the slot (a user preset: Studio
    resets every value that is not in its different_settings_to_system entry to the values of its parent, Preset.cpp load_external_preset,
    both tags); else None (a preset made from scratch: its values cannot be compared)."""
    ig = cfg.get("inherits_group") if isinstance(cfg.get("inherits_group"), list) else []
    fils = cfg.get("filament_settings_id") if isinstance(cfg.get("filament_settings_id"), list) else []
    names = [cfg.get("print_settings_id")] + [fils[i] if i < len(fils) else None for i in range(n)] + [cfg.get("printer_settings_id")]
    kinds = ["process"] + ["filament"] * n + ["machine"]
    out = []
    for i, (kind, nm) in enumerate(zip(kinds, names)):
        base = nm if isinstance(nm, str) and _is_system(ps, kind, nm) else None
        if base is None and i < len(ig) and isinstance(ig[i], str) and ig[i] and _is_system(ps, kind, ig[i]):
            base = ig[i]
        out.append((kind, nm, base))
    return out


def system_config(cfg, ps, n, nplates):
    """The values Studio resets the project to when it opens the file: (config or None, skipped slots, why not). A slot whose preset is not a
    system preset and has no parent in inherits_group is skipped (its keys are not compared); only a printer without a base stops everything.
    Built with the same resolver the builder uses (checked against real saves in tests)."""
    bases = slot_bases(cfg, ps, n)
    printer = bases[n + 1][2]
    if printer is None:
        return None, set(range(n + 2)), "printer preset %r is not a system preset and inherits_group has no parent for it" % bases[n + 1][1]
    skipped = {i for i, (_k, _n, b) in enumerate(bases) if b is None}
    try:
        proc = bases[0][2] or ps.compatible("process", printer)[0]
        have = [b for (_k, _n, b) in bases[1:n + 1] if b]
        spare = have[0] if have else ps.compatible("filament", printer)[0]
    except (IndexError, ProfileError) as e:
        return None, skipped, "no stand-in preset for a skipped slot (%s)" % e

    class J:
        pass
    j = J()
    j.profiles, j.process, j.printer = ps, proc, printer
    j.plate_type = cfg.get("curr_bed_type")
    j.settings = {}
    j.filaments = [{"preset": bases[1 + i][2] or spare, "color": c, "settings": {}} for i, c in enumerate(cfg["filament_colour"])]
    j.purge = "default"
    j.studio = ps.version
    try:
        return cfgmod.build_config(j, plates=max(nplates, 1), max_height=0.0).config, skipped, ""
    except Exception as e:                                   # noqa: BLE001 - a preset the builder cannot assemble: skip, say so
        return None, skipped, str(e)


def check_value(opt, s):
    """None if the text is valid for one slot of this option, else a reason."""
    t = opt["type"]
    if s == "nil":
        return None if opt.get("nullable") else "nil is not allowed here"
    try:
        if t in ("coBool", "coBools"):
            if s not in ("0", "1"):
                return "expected 0 or 1"
        elif t in ("coInt", "coInts"):
            f = float(s)
            if f != int(f):
                return "expected a whole number"
        elif t in ("coFloat", "coFloats"):
            float(s)
        elif t in ("coPercent", "coPercents"):
            float(s.rstrip("%"))
        elif t in ("coFloatOrPercent", "coFloatsOrPercents"):
            float(s.rstrip("%"))
        elif t in ("coEnum", "coEnums"):
            ek = opt.get("enum_keys") or opt.get("enum_values") or []
            if ek and s not in ek:
                return "%r is not one of the allowed values" % s
            return None
        else:
            return None
    except (ValueError, OverflowError):
        return "not a number"
    if t not in ("coBool", "coBools", "coEnum", "coEnums") and not math.isfinite(float(s.rstrip("%"))):
        return "not a finite number"
    if t in ("coInt", "coInts", "coFloat", "coFloats", "coPercent", "coPercents", "coFloatOrPercent", "coFloatsOrPercents") and not s.endswith("%") or t in ("coPercent", "coPercents"):
        num = float(s.rstrip("%"))
        lo, hi = opt.get("min"), opt.get("max")
        if (lo is not None and num < lo) or (hi is not None and num > hi):
            return "%s is outside the range %s to %s" % (s, lo, hi)
    return None


# Settings that a real Studio 2.8.2 and 2.9.1 save leaves out of project_settings.config (preset links, host settings and keys that
# only exist in the preset editor). Taken from tests/data/four-color-autocalc-*.3mf.
ABSENT_IN_REAL_SAVES = {
    "compatible_machine_expression_group", "compatible_printers", "compatible_printers_condition", "compatible_prints",
    "compatible_prints_condition", "compatible_process_expression_group", "different_settings_to_system", "extruder",
    "extruder_nozzle_count", "extruder_nozzle_volume_type", "filament_map_2", "filament_retract_lift_above", "filament_retract_lift_below",
    "first_layer_sequence_choice", "inherits", "inherits_group", "other_layers_sequence_choice", "preset_name", "preset_names",
    "print_host", "print_host_webui", "printhost_apikey", "printhost_cafile", "printhost_password", "printhost_port", "printhost_user"}


def _studio_default_entry(val, opt):
    """True for a list of one entry that is the option's own default (or nil for a nullable option): what Studio holds for a key it filled in."""
    if not isinstance(val, list) or len(val) != 1 or not opt:
        return False
    d = opt.get("default")
    return (isinstance(d, list) and len(d) == 1 and str(val[0]) == str(d[0])) or (bool(opt.get("nullable")) and str(val[0]) == "nil")


LOAD_EXTERNAL_PRESET = "Preset.cpp load_external_preset (2.8.2 :2263, 2.9.1 :2331)"


_SHIPPED = {}


def shipped_values(ps, key):
    """Every value the vendored system presets of this version hold for key. Studio checks min/max only in the settings editor; a system
    preset may ship a value outside them (2.9.1 Bambu PETG HF @BBL P2S 0.4 nozzle: filament_retraction_distances_when_cut 0, range 10-18)."""
    ck = (ps.version, key)
    if ck not in _SHIPPED:
        o = ps.options.get(key) or {}
        kind = {"print": "process", "filament": "filament", "printer": "machine"}.get(o.get("preset"))
        vals = set()
        if kind:
            for nm in ps.names(kind, instantiated_only=False):
                try:
                    c, _m = ps.resolve(kind, nm)
                except Exception:                            # noqa: BLE001 - a template that does not resolve adds nothing
                    continue
                v = c.get(key)
                vals |= set(v) if isinstance(v, list) else ({v} if v is not None else set())
        _SHIPPED[ck] = vals
    return _SHIPPED[ck]


def _range_ok(ps, key, s, why):
    return "outside the range" in why and s in shipped_values(ps, key)


def check(cfg, rep, ps, nplates):
    where = "Metadata/project_settings.config"
    by = ps.options
    # the list sizes (n filaments x V variants), the preset difference and the load simulation assume one nozzle; a printer with several nozzles
    # keeps its own sizes (Studio wrote and sliced them), so those rules are skipped with one note
    multi = len(_lst(cfg.get("nozzle_diameter", ["0.4"]))) > 1
    if multi:
        rep.warn("R16", where, "multi-nozzle printer, table rules skipped (list sizes R5 R6 R8 R14 R35, difference to the system presets R3 R38, load simulation S1)", cls="SKIPPED")
        rep.tables_applied = False
    # ---- R15 header
    if cfg.get("name") != "project_settings":
        rep.err("R15", where, "name must be project_settings, found %r" % cfg.get("name"))
    if cfg.get("from") != "project":
        rep.err("R15", where, "from must be project, found %r" % cfg.get("from"))
    # a flattened config holds every setting. The Studio app starts a missing key from the default preset and then resets it to the value of the
    # slot's system preset (Preset.cpp load_external_preset); only the command-line slicer keeps the built-in default
    gone = sorted(k for k, o in by.items() if k not in cfg and k not in ABSENT_IN_REAL_SAVES and o.get("preset") in ("print", "filament", "printer"))
    if gone:
        rep.err("R15", where, "%d setting(s) of this Studio version are missing from the config (the Studio app takes the system preset value for each of them, Preset.cpp load_external_preset; the command-line slicer keeps its built-in default): %s%s" % (
            len(gone), ", ".join(gone[:6]), " ..." if len(gone) > 6 else ""))
    # ---- R4 filament count
    fc = cfg.get("filament_colour")
    if not isinstance(fc, list) or not fc:
        rep.err("R4", where, "filament_colour must be a list with at least one color")
        return
    n = len(fc)
    for c in fc:
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?", c):
            rep.err("R4", where, "filament_colour entry %r is not #RRGGBB" % c)
    pv = _lst(cfg.get("printer_extruder_variant", ["Direct Drive Standard"]))
    vp = len(pv) or 1
    fev = cfg.get("filament_extruder_variant", [])
    vf = len(fev) // n if n and len(fev) % n == 0 and fev else None
    if vf is None:
        rep.err("R7", where, "filament_extruder_variant has %d entries; it must have n x V (n=%d)" % (len(fev), n))
        vf = vp
    elif vf != vp:
        rep.err("R7", where, "filaments carry %d extruder variants, the printer has %d" % (vf, vp))
    # R7 agreement
    si = cfg.get("filament_self_index", [])
    if len(si) != n * vf:
        rep.err("R7", where, "filament_self_index has %d entries; it must have n x V = %d" % (len(si), n * vf))
    else:
        for i in range(n):
            blk_i = si[i * vf:(i + 1) * vf]
            blk_v = fev[i * vf:(i + 1) * vf]
            if any(x != str(i + 1) for x in blk_i) or (blk_v != pv and vf == vp):
                rep.err("R7", where, "filament %d: filament_self_index / filament_extruder_variant do not agree (self index %s, variants %s)" % (i + 1, blk_i, blk_v))
                break
    # ---- R16 / R5 / R6 / R8 / R35 per key
    bad_report = 0
    other = getattr(ps, "build_other", None)
    dl0 = cfg.get("different_settings_to_system")
    listed_any = set()
    for ent in (dl0 if isinstance(dl0, list) else []):
        listed_any |= set(filter(None, (ent or "").split(";")))

    def report(rule, msg, warn=False, key=None, val=None, opt=None):
        nonlocal bad_report
        if multi and rule in ("R5", "R6", "R8", "R35"):
            return                                           # list sizes of a multi-nozzle printer are not modelled (one note above)
        cls = None
        if key is not None and rule in ("R5", "R6", "R8", "R35") and key not in listed_any and _studio_default_entry(val, opt):
            # one entry holding the option's own default: Studio fills a key the file did not have with that value and writes it back as it is
            # (a CLI re-export of a 2.8.2 file into 2.9.1 does this for the keys that version added; a native 2.9.1 save has full lists)
            cls = "FORM"
            if rule == "R35":
                msg = re.sub(r"; Studio \S+ misreads it", "", msg)
        elif key is not None and rule in ("R5", "R6", "R8", "R35") and key not in listed_any:
            # a list of the wrong size in a key that is not in different_settings_to_system: the Studio app replaces the whole key with the
            # system preset value on load (Preset.cpp load_external_preset), so the wrong size changes nothing it prints
            cls = "CHANGE"
            if rule == "R35":
                msg = re.sub(r"; Studio \S+ misreads it", "", msg)          # the app replaces the key, so nothing is misread
            msg += "; the key is not in different_settings_to_system, so the Studio app replaces it with the system preset value when it opens the file (%s)" % LOAD_EXTERNAL_PRESET
        bad_report += 1
        if bad_report <= MAX_REPORT:
            (rep.warn if warn else rep.err)(rule, where, msg, cls=cls, keys=[key if key is not None else k])
    for k, v in cfg.items():
        if k in HEADER:
            continue
        o = by.get(k)
        if o is None:
            report("R16", "%s is not a Studio %s setting%s" % (k, ps.tag_version, " (the file is from build %s)" % other if other else ""), warn=bool(other))
            continue
        if not o["vector"]:
            if isinstance(v, list):
                report("R16", "%s is a single value but the file holds a list" % k)
            else:
                why = check_value(o, v)
                if why and not _range_ok(ps, k, v, why):
                    report("R16", "%s = %r: %s" % (k, v, why))
            continue
        if not isinstance(v, list):
            report("R16", "%s is a list setting but the file holds a single value" % k)
            continue
        want, rule = None, None
        if o["preset"] == "filament" and o.get("variant") == "filament":
            want, rule = n * vp, "R6"
        elif o["preset"] == "filament" and k not in cfgmod.FILAMENT_DEV_OPTIONS and o.get("variant") is None:
            want, rule = n, "R5"
        elif o["preset"] in ("print", "printer") and o.get("variant") in ("print", "printer"):
            want, rule = vp * (o.get("variant_stride") or 1), "R8"
        elif k in cfgmod.PER_FILAMENT_PROJECT:
            want, rule = n, "R5"
        if k in cfgmod.LAZY_PROJECT_LISTS and want is not None and 1 <= len(v) <= want:
            want = None   # Studio grows these on demand; a short list is what it writes itself
        if want is not None and len(v) != want:
            if rule == "R6" and len(v) == 1 and n * vp > 1:
                report("R35", "%s is a variant list written short (1 entry, needs %d); Studio %s misreads it" % (k, want, version_key(ps.tag_version)), key=k, val=v, opt=o)
            else:
                report(rule, "%s has %d entries, needs %d (n=%d, extruder variants=%d)" % (k, len(v), want, n, vp), key=k, val=v, opt=o)
        for s in v[:2000]:
            why = check_value(o, s)
            if why and not _range_ok(ps, k, s, why):
                report("R16", "%s has the value %r: %s" % (k, s, why))
                break
    if bad_report > MAX_REPORT:
        rep.err("R16", where, "... and %d more problems of the same kind" % (bad_report - MAX_REPORT))
    # ---- R13 project options
    miss = sorted(k for k in ps.project_options if k not in cfg)
    if miss:
        rep.err("R13", where, "project options missing: %s%s. Studio 2.9.1 resets a missing project option to its default value when it opens the file; Studio 2.8.2 keeps the value it had before (PresetBundle.cpp project_config.apply_only, 2.8.2 :4020, 2.9.1 :5574)" % (
            ", ".join(miss[:8]), " ..." if len(miss) > 8 else ""))
    # ---- R9 / R10 / R12 presets
    printer = cfg.get("printer_settings_id")
    proc = cfg.get("print_settings_id")
    fils = cfg.get("filament_settings_id", [])
    bases = slot_bases(cfg, ps, n)

    def not_system(what, name, base):
        tail = ("its base for the comparison is the parent %r named in inherits_group" % base) if base else "it has no parent in inherits_group, so its values are not compared"
        rep.warn("R10", where, "%s %r is not a system preset of this Studio version; %s" % (what, name, tail))
    system_ok = bases[n + 1][2] is not None
    pbase = bases[n + 1][2]
    if pbase is None:
        not_system("printer_settings_id", printer, None)
    else:
        mcfg, mmeta = ps.resolve("machine", pbase)
        if pbase != printer:
            not_system("printer_settings_id", printer, pbase)
        elif mmeta["instantiation"] != "true":
            rep.err("R10", where, "printer_settings_id %r is a base template" % printer)
        if cfg.get("printer_model") != mcfg.get("printer_model"):
            rep.err("R9", where, "printer_model is %r but %r is a %r. Studio drops the project config unless printer_model is a Bambu printer" % (
                cfg.get("printer_model"), pbase, mcfg.get("printer_model")))
    if bases[0][2] == proc:
        pc, pm = ps.resolve("process", proc)
        cp = pc.get("compatible_printers")
        if cp and printer not in cp:
            rep.err("R10", where, "print_settings_id %r is not made for %r" % (proc, printer))
    else:
        not_system("print_settings_id", proc, bases[0][2])
    if len(fils) != n:
        rep.err("R5", where, "filament_settings_id has %d entries, n=%d" % (len(fils), n))
    ids = cfg.get("filament_ids", [])
    if len(ids) != n:
        rep.err("R12", where, "filament_ids has %d entries, n=%d" % (len(ids), n))
    for i, f in enumerate(fils):
        base = bases[1 + i][2] if 1 + i <= n else None
        if base == f:
            fcfg, fm = ps.resolve("filament", f)
            cp = fcfg.get("compatible_printers")
            if cp and printer not in cp:
                rep.err("R10", where, "filament %d (%s) is not made for %r" % (i + 1, f, printer))
            if i < len(ids) and fm.get("filament_id") and ids[i] != fm["filament_id"]:
                rep.err("R12", where, "filament_ids[%d] is %r; the preset %s has %r" % (i, ids[i], f, fm["filament_id"]))
        else:
            not_system("filament_settings_id[%d]" % i, f, base)
    # ---- R11 diff lists
    dl, ig = cfg.get("different_settings_to_system"), cfg.get("inherits_group")
    # Studio writes each list only if one of its entries is not empty (PresetBundle.cpp add_if_some_non_empty), so either may be absent;
    # absent means all entries are empty. The length is checked only for a list that is there.
    for name, lst in (("different_settings_to_system", dl), ("inherits_group", ig)):
        if lst is not None and len(lst) != n + 2:
            rep.err("R11", where, "%s has %d entries, needs n + 2 = %d (process, %d filaments, printer)" % (name, len(lst), n + 2, n))
    # ---- R3 every changed key is listed
    if system_ok and not multi and not rep.errors_for("R4", "R5", "R6", "R7", "R16") and (dl is None or len(dl) == n + 2):
        check_diff(cfg, rep, ps, n, nplates, dl, where)
    # ---- R14 flush
    nozzles = len(_lst(cfg.get("nozzle_diameter", ["0.4"]))) or 1
    m = cfg.get("flush_volumes_matrix", [])
    if multi:
        pass
    elif len(m) != nozzles * n * n:
        rep.err("R14", where, "flush_volumes_matrix has %d entries, needs nozzles x n x n = %d" % (len(m), nozzles * n * n))
    else:
        for z in range(nozzles):
            for i in range(n):
                if m[z * n * n + i * n + i] not in ("0", "0.0"):
                    rep.err("R14", where, "flush_volumes_matrix diagonal must be 0 (filament %d to itself)" % (i + 1))
                    break
    if not multi and len(cfg.get("flush_volumes_vector", [])) != 2 * n * (1 if nozzles == 1 else nozzles):
        rep.err("R14", where, "flush_volumes_vector has %d entries, needs %d" % (len(cfg.get("flush_volumes_vector", [])), 2 * n * nozzles))
    for k in ("flush_multiplier", "flush_multiplier_fast"):
        if k in cfg and len(cfg[k]) != nozzles and not multi:
            rep.err("R14", where, "%s needs %d entries (one per nozzle), has %d" % (k, nozzles, len(cfg[k])))
    # ---- R17 bed type, wipe tower per plate
    bt = cfg.get("curr_bed_type")
    if bt not in ps.bed_types:
        rep.err("R17", where, "curr_bed_type %r is not a bed type of this version (%s)" % (bt, ", ".join(sorted(ps.bed_types))))
    for k in ("wipe_tower_x", "wipe_tower_y"):
        if len(cfg.get(k, [])) != max(nplates, 1):
            rep.err("R17", where, "%s has %d entries; one per plate (%d) is needed" % (k, len(cfg.get(k, [])), max(nplates, 1)))
    # ---- R36 multi-extruder
    if nozzles > 1:
        if len(cfg.get("extruder_type", [])) != nozzles:
            rep.err("R36", where, "extruder_type needs one entry per nozzle (%d)" % nozzles)
        if any(not _digits(x) or not (1 <= int(x) <= nozzles) for x in cfg.get("filament_map", [])):
            rep.err("R36", where, "filament_map entries must be 1..%d" % nozzles)
        if len(cfg.get("extruder_nozzle_stats", [])) != nozzles:
            rep.err("R36", where, "extruder_nozzle_stats needs one entry per nozzle (%d)" % nozzles)
    return n


def check_diff(cfg, rep, ps, n, nplates, dl, where):
    """R3. The system values come from the same resolver the builder uses (checked against real saves in tests). A slot with a user preset is
    compared with its parent from inherits_group; a slot without one is skipped (the R10 note says so), never the whole file."""
    sysc, skipped, why = system_config(cfg, ps, n, nplates)
    if sysc is None:
        rep.warn("R3", where, "could not rebuild the system values to check the diff lists (%s)" % why)
        return
    # R38: Studio keeps a listed variant override only in slots whose variant name and id match the system preset
    for k in ("print_extruder_variant", "print_extruder_id", "printer_extruder_variant", "printer_extruder_id", "filament_extruder_variant"):
        slot_skipped = (0 in skipped) if k.startswith("print_") else ((n + 1 in skipped) if k.startswith("printer_") else any(1 <= i <= n for i in skipped))
        if k in cfg and k in sysc and cfg[k] != sysc[k] and not slot_skipped:
            rep.err("R38", where, "%s is %s but the system presets give %s; Studio would reset the listed per-extruder overrides that sit in slots whose variant or id differs" % (
                k, cfg[k], sysc[k]))
    listed = [set(filter(None, (s or "").split(";"))) for s in (dl or [""] * (n + 2))]
    skip = cfgmod.BUILDER_KEYS | {"version", "name", "from", "printer_model", "printer_variant", "printer_extruder_variant"}
    unlisted = []
    for k, v in cfg.items():
        o = ps.options.get(k)
        if o is None or k in skip or k not in sysc:
            continue
        if o["preset"] not in ("print", "filament", "printer"):
            continue
        if v == sysc[k]:
            continue
        idx = 0 if o["preset"] == "print" else (n + 1 if o["preset"] == "printer" else None)
        if idx is not None:
            if idx not in skipped and k not in listed[idx]:
                unlisted.append(k)
            continue
        # filament key: each slot that differs must be listed in that slot's own entry (spec R3; Studio restores per slot)
        a, b = _lst(v), _lst(sysc[k])
        per = max(len(a) // n, 1) if len(a) % max(n, 1) == 0 else 1
        slots = sorted({i // per for i in range(max(len(a), len(b))) if (a[i] if i < len(a) else None) != (b[i] if i < len(b) else None)})
        for s in slots:
            if s < n and 1 + s not in skipped and k not in listed[1 + s]:
                unlisted.append("%s (filament %d)" % (k, s + 1))
    rep.r3_keys = {u.split(" (")[0] for u in unlisted}       # the load simulator (S1) does not report these keys a second time
    if unlisted:
        rep.err("R3", where, "%d setting(s) differ from the system preset but are not in different_settings_to_system. The Studio app resets each of them to its own system preset value when it opens the file "
                "(Preset.cpp load_external_preset; the command-line slicer keeps them). If the file was saved with a newer system preset pack than the one this tool has, "
                "the values are the pack's own and the reset changes nothing you set: %s%s" % (
            len(unlisted), ", ".join(sorted(unlisted)[:8]), " ..." if len(unlisted) > 8 else ""), keys=unlisted)
