"""Job file reader and checker (TOML, JSON also accepted).

load_job(path, profiles_factory) returns a Job or raises JobError carrying every problem found, each
naming its place in the file and a fix. Nothing is written before all checks pass.
"""
import difflib
import json
import os

from .profiles import ProfileError, ProfileSet, STUDIO_VERSIONS, MODEL_PART_ONLY
from .serialize import normalize
from . import studio_detect
from . import calibration
from . import versions

try:
    import tomllib
except ImportError:                      # Python 3.8-3.10: vendored tomli 2.0.1 (MIT, scripts/bambu3mf/_vendor/tomli)
    from ._vendor import tomli as tomllib

STUDIO_ALIASES = {"2.8.2": "2.8.2.61", "2.8.2.61": "2.8.2.61", "2.9.1": "2.9.1.70", "2.9.1.70": "2.9.1.70",
                  "2.9.2": "2.9.2.62", "2.9.2.62": "2.9.2.62"}
TOP_KEYS = {"studio", "output", "title", "printer", "process", "plate_type", "settings", "filament", "object",
            "layer_gcode", "temperature_tower", "purge", "allow_large", "strict_version"}
BAD_NAME_CHARS = '<>:/\\?*"|'          # Studio drops such names (validator rule R31)
FILAMENT_KEYS = {"preset", "color", "settings"}
OBJECT_KEYS = {"name", "plate", "position", "settings", "part", "copies", "height_range"}
RANGE_KEYS = {"min_z", "max_z", "layer_height", "filament", "settings"}
PART_KEYS = {"name", "mesh", "filament", "type", "settings", "units"}
PART_TYPES = ("normal_part", "modifier_part", "negative_part", "support_blocker", "support_enforcer")
LAYER_KEYS = {"plate", "z", "temperature", "pause", "gcode"}
TOWER_KEYS = {"shape", "start", "step", "step_height", "first_z", "steps", "plate"}
CALIB_KEYS = {"shape", "center", "plus_minus", "start", "end", "step", "plate"}
TOWER_SHAPES = ("custom", "calibration")
RESERVED = {"filament_colour", "filament_ids", "filament_settings_id", "print_settings_id", "printer_settings_id",
            "inherits_group", "different_settings_to_system", "filament_self_index", "flush_volumes_matrix",
            "flush_volumes_vector", "flush_multiplier", "flush_multiplier_fast", "filament_map", "filament_map_2",
            "filament_volume_map", "filament_nozzle_map", "print_compatible_printers", "compatible_printers",
            "compatible_printers_condition", "inherits", "extruder"}


class JobError(Exception):
    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("\n".join("- " + p for p in self.problems))


def _closest(word, words, n=4):
    return difflib.get_close_matches(str(word), sorted(words), n=n, cutoff=0.55)


class Job:
    pass


class _Check:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def err(self, where, msg):
        self.errors.append("%s: %s" % (where, msg))

    def warn(self, where, msg):
        self.warnings.append("%s: %s" % (where, msg))


def read_raw(path):
    with open(path, "rb") as f:
        data = f.read()
    if path.lower().endswith(".json"):
        return json.loads(data.decode("utf-8"))
    return tomllib.loads(data.decode("utf-8"))


def coerce(option, value, where, chk):
    """One user value -> the canonical Studio text for a scalar slot. Appends to chk on error. None on error."""
    t = option["type"]
    key = option["key"]
    if isinstance(value, (list, dict)):
        chk.err(where, "%s takes one value, not a list. The builder expands it to every extruder variant." % key)
        return None
    if isinstance(value, bool):
        s = "1" if value else "0"
    else:
        s = str(value)
    if s == "nil":
        if option["nullable"]:
            return "nil"
        chk.err(where, "%s cannot be nil" % key)
        return None
    base = t.rstrip("s") if t.endswith("s") and t not in ("coBools",) else t
    try:
        if t in ("coBool", "coBools"):
            if s.strip().lower() not in ("0", "1", "true", "false"):
                raise ValueError("expected true or false")
        elif t in ("coInt", "coInts"):
            f = float(s)
            if f != int(f):
                raise ValueError("expected a whole number")
        elif t in ("coFloat", "coFloats", "coPercent", "coPercents", "coFloatOrPercent", "coFloatsOrPercents"):
            f = float(s.strip().rstrip("%"))
            if t in ("coPercent", "coPercents") or s.strip().endswith("%"):
                pass
        elif t in ("coEnum", "coEnums"):
            ek = option.get("enum_keys") or option.get("enum_values") or []
            if s not in ek:
                near = _closest(s, ek)
                raise ValueError("not one of the allowed values%s" % ((". Closest: " + ", ".join(near)) if near else ""))
        elif t in ("coString", "coStrings"):
            pass
        else:
            raise ValueError("this setting type (%s) cannot be set from a job file" % t)
    except ValueError as e:
        chk.err(where, "%s = %r: %s" % (key, value, e))
        return None
    if t in ("coInt", "coInts", "coFloat", "coFloats", "coPercent", "coPercents", "coFloatOrPercent", "coFloatsOrPercents"):
        num = float(s.strip().rstrip("%"))
        if not (t in ("coFloatOrPercent", "coFloatsOrPercents") and s.strip().endswith("%")):
            lo, hi = option.get("min"), option.get("max")
            if lo is not None and num < lo or hi is not None and num > hi:
                chk.err(where, "%s = %s is outside the allowed range %s to %s%s" % (
                    key, value, lo, hi, (" " + option["unit"]) if option.get("unit") else ""))
                return None
    single = dict(option, vector=False, type=t.rstrip("s") if t.endswith("s") and t != "coBools" else t)
    if t == "coBools":
        single["type"] = "coBool"
    if t == "coEnums":
        single["type"] = "coEnum"
    if t == "coFloatsOrPercents":
        single["type"] = "coFloatOrPercent"
    return normalize(single, s)


# Friendly names for the settings people ask for most. Each one is only a second spelling of a real Studio key and is listed in
# references/job-format.md. This hand table wins over the generated one (scripts/bambu3mf/data/aliases.json, built from Studio's
# option labels by tests/gen_aliases.py, listed in references/aliases.md). Any other unknown name gets the closest real names.
PRINT_ALIASES = {"walls": "wall_loops", "infill": "sparse_infill_density", "infill_pattern": "sparse_infill_pattern",
                 "top_layers": "top_shell_layers", "bottom_layers": "bottom_shell_layers", "supports": "enable_support",
                 "brim_size": "brim_width", "color_penetration_cross_parts": "cross_part_color_penetration"}
NEEDS_STUDIO_292 = {"color_penetration_cross_parts", "cross_part_color_penetration"}     # settings that exist from Studio 2.9.2 on
FILAMENT_ALIASES = {"nozzle_temp": "nozzle_temperature", "nozzle_temp_first_layer": "nozzle_temperature_initial_layer",
                    "flow": "filament_flow_ratio"}
PLATE_KEY = {"Cool Plate": "cool_plate_temp", "Engineering Plate": "eng_plate_temp", "High Temp Plate": "hot_plate_temp",
             "Textured PEI Plate": "textured_plate_temp", "Supertack Plate": "supertack_plate_temp",
             "3D Effect Plate": "effect_plate_temp"}                  # Studio 2.9.2 and newer (curr_bed_type enum)
PLATE_BED_ALIASES = ("bed_temp", "bed_temp_first_layer")      # resolved from the job's plate_type, see _settings_table
ALIASES = {"print": PRINT_ALIASES, "filament": FILAMENT_ALIASES}
_GENERATED = None


def generated_aliases(kind, ver):
    """alias -> real key for one setting kind ("print" or "filament") and one Studio version, from the generated data file."""
    global _GENERATED
    if _GENERATED is None:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "aliases.json"), encoding="utf-8") as f:
            _GENERATED = json.load(f)["aliases"]
    out = {}
    for alias, ents in _GENERATED.get(kind, {}).items():
        for key, versions in ents:
            if ver in versions:
                out[alias] = key
    return out
# Infill patterns by the name Studio shows. Studio stores "Rectilinear" as zig-zag.
PATTERN_KEYS = {"sparse_infill_pattern", "top_surface_pattern", "bottom_surface_pattern", "internal_solid_infill_pattern"}
PATTERN_NAMES = {"rectilinear": "zig-zag", "aligned_rectilinear": "alignedrectilinear", "aligned rectilinear": "alignedrectilinear"}
# The builder's one default that differs from Bambu's presets: sparse infill is Rectilinear, not Grid. Grid crosses itself on
# every layer, so the nozzle drags over infill it just printed. A job that sets sparse_infill_pattern (or infill_pattern) keeps its own.
DEFAULT_SETTINGS = {"sparse_infill_pattern": "zig-zag"}


def _height_ranges(raw, by, ver, w, chk, ps, nfil):
    """[[object.height_range]]: min_z, max_z (mm from the object's bottom), layer_height, and optional [object.height_range.settings]
    with the settings a part may have (Studio's PrintRegionConfig). See ranges.py for the source."""
    out = []
    if raw is None:
        return out
    if not isinstance(raw, list):
        chk.err(w + ".height_range", "use [[object.height_range]] tables")
        return out
    region = getattr(ps, "range_options", None) or set()
    for i, r in enumerate(raw, 1):
        rw = "%s height_range #%d" % (w, i)
        if not isinstance(r, dict):
            chk.err(rw, "must be a table"); continue
        for k in r:
            if k not in RANGE_KEYS:
                chk.err("%s.%s" % (rw, k), "unknown key.%s" % ((" Closest: " + ", ".join(_closest(k, RANGE_KEYS))) if _closest(k, RANGE_KEYS) else ""))
        miss = [k for k in ("min_z", "max_z", "layer_height") if k not in r]
        if miss:
            chk.err(rw, "needs %s" % ", ".join(miss)); continue
        try:
            lo, hi, lh = float(r["min_z"]), float(r["max_z"]), float(r["layer_height"])
        except (TypeError, ValueError):
            chk.err(rw, "min_z, max_z and layer_height must be numbers (mm)"); continue
        st = _settings_table(r.get("settings", {}), ("print",), by, ver, rw + ".settings", chk, "[object.height_range.settings] (process settings only)")
        if "layer_height" in st:
            chk.err(rw + ".settings.layer_height", "set layer_height on the range itself, not in its settings")
            st.pop("layer_height")
        bad = sorted(k for k in st if region and k not in region)
        if bad:
            chk.err(rw + ".settings", "%s cannot be set in a height range. A range takes layer_height and the settings a part can have "
                    "(walls, infill, speeds, ...); set the others on the object" % ", ".join(bad))
            for k in bad:
                st.pop(k)
        fil = 0
        if "filament" in r:
            try:
                fil = int(r["filament"])
            except (TypeError, ValueError):
                fil = -1
            if not (1 <= fil <= max(nfil, 1)):
                chk.err(rw + ".filament", "slot %s does not exist; the job defines %d filament slot(s)" % (r["filament"], nfil))
                fil = 0
        out.append({"min_z": lo, "max_z": hi, "layer_height": lh, "filament": fil, "settings": st})
    return out


def _settings_table(tbl, owners, by, ver, where, chk, label, plate_type=None):
    out = {}
    if not isinstance(tbl, dict):
        chk.err(where, "must be a table of Studio setting names")
        return out
    aliases = {}
    for o in owners:
        aliases.update(generated_aliases(o, ver))
    for o in owners:                               # the hand table wins
        aliases.update(ALIASES.get(o, {}))
    if "filament" in owners:                       # bed_temp means the bed temperature of the plate the job prints on
        if plate_type in PLATE_KEY:
            aliases["bed_temp"] = PLATE_KEY[plate_type]
            aliases["bed_temp_first_layer"] = PLATE_KEY[plate_type] + "_initial_layer"
        else:
            for a in ("bed_temp", "bed_temp_first_layer"):
                if a in tbl:
                    chk.err("%s.%s" % (where, a), "%s needs plate_type in the job (it is the bed temperature of that plate). Set plate_type, or use the real key, e.g. textured_plate_temp." % a)
    for key, val in list(tbl.items()):
        w = "%s.%s" % (where, key)
        if aliases.get(key, key) in PATTERN_KEYS and isinstance(val, str) and val.lower() in PATTERN_NAMES:
            val = PATTERN_NAMES[val.lower()]           # Studio's own names for these two are zig-zag / alignedrectilinear
        if key in aliases and key not in by:
            real = aliases[key]
            if real in tbl:
                chk.err(w, "%s is another spelling of %s, which is also set. Use one." % (key, real))
                continue
            key = real
        elif key in ("bed_temp", "bed_temp_first_layer") and "filament" in owners:
            continue                                  # reported above
        opt = by.get(key)
        if key in RESERVED:
            chk.err(w, "%s is set by the builder (from presets, colors and the build). Remove it." % key)
            continue
        if opt is None:
            near = _closest(key, by.keys())
            other = None
            if key in NEEDS_STUDIO_292:
                chk.err(w, "%s needs Studio 2.9.2 or newer. Studio %s has no setting %r. Set studio = \"2.9.2\" in the job, or remove it." % (key, ver, "cross_part_color_penetration"))
                continue
            chk.err(w, "Studio %s has no setting %r.%s" % (ver, key, (" Closest: " + ", ".join(near)) if near else ""))
            continue
        if opt["preset"] not in owners:
            chk.err(w, "%s is a %s setting; it does not belong in %s" % (key, opt["preset"], label))
            continue
        c = coerce(opt, val, w, chk)
        if c is not None:
            out[key] = c
    return out


def _num(v):
    if isinstance(v, (list, tuple)):
        v = v[0]
    return float(str(v).strip().rstrip("%"))


def _whole(tbl, key, where, chk, default=None, minimum=None):
    if key not in tbl:
        return default
    v = tbl[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v):
        chk.err("%s.%s" % (where, key), "%r is not a whole number" % (v,))
        return default
    v = int(v)
    if minimum is not None and v < minimum:
        chk.err("%s.%s" % (where, key), "%d is below the minimum %d" % (v, minimum))
        return default
    return v


def _calibration_tower(tw, job, ps, by, chk, nfil):
    """[temperature_tower] shape = "calibration": the temperatures, the generated object, the layer G-code at each band."""
    w = "[temperature_tower]"
    for k in tw:
        if k not in CALIB_KEYS:
            hint = " (it belongs to shape = \"custom\")" if k in TOWER_KEYS else ""
            chk.err("%s.%s" % (w, k), "unknown key for the calibration tower%s.%s" % (hint, (" Closest: " + ", ".join(_closest(k, CALIB_KEYS))) if _closest(k, CALIB_KEYS) else ""))
    if int(tw.get("plate", 1) if isinstance(tw.get("plate", 1), int) else 0) != 1:
        chk.err(w + ".plate", "the calibration tower is built on plate 1")
    if not job.filaments or "meta" not in job.filaments[0]:
        return                                              # the filament errors are reported already
    if nfil > 1:
        chk.err("filament", "the calibration tower prints in one filament; the job has %d [[filament]] tables" % nfil)
        return
    f0 = job.filaments[0]
    by_range = "center" in tw or "plus_minus" in tw
    by_ends = "start" in tw or "end" in tw
    if by_range and by_ends:
        chk.err(w, "give either center / plus_minus or start / end, not both")
        return
    step = _whole(tw, "step", w, chk, 5, 1)
    try:
        if by_ends:
            if "start" not in tw or "end" not in tw:
                chk.err(w, "start and end go together")
                return
            a, b = _whole(tw, "start", w, chk), _whole(tw, "end", w, chk)
            if a is None or b is None:
                return
            temps = calibration.temperatures(start=a, end=b, step=step)
        else:
            center = _whole(tw, "center", w, chk)
            if center is None and "center" not in tw:
                cur = f0["settings"].get("nozzle_temperature")
                if cur is None:
                    cur = ps.resolve("filament", f0["preset"])[0]["nozzle_temperature"]
                center = int(round(_num(cur)))
            pm = _whole(tw, "plus_minus", w, chk, 10, 0)
            if center is None or pm is None:
                return
            temps = calibration.temperatures(center=center, plus_minus=pm, step=step)
        body, labels = calibration.build_tower(temps)
    except calibration.CalibrationError as e:
        chk.err(w, str(e))
        return
    # The first band's temperature is also the first-layer temperature.
    for key in ("nozzle_temperature", "nozzle_temperature_initial_layer"):
        old = f0["settings"].get(key)
        if old is not None and int(round(_num(old))) != temps[0]:
            chk.warn("[[filament]] #1.settings.%s" % key, "%s is replaced by %d, the temperature of the first band (the first layer prints at the first band's temperature)" % (key, temps[0]))
        f0["settings"][key] = str(temps[0])
    try:
        pcfg = ps.resolve("process", job.process)[0]
    except ProfileError:
        return                                              # the process error is reported already
    first = _num(job.settings.get("initial_layer_print_height", pcfg["initial_layer_print_height"]))
    lh = _num(job.settings.get("layer_height", pcfg["layer_height"]))
    job.calibration = {"temps": temps, "band_bottoms": [calibration.band_bottom(k) for k in range(1, len(temps) + 1)]}
    for k in range(2, len(temps) + 1):                     # a change at layer top z is printed from that layer on: the first layer of the band
        zb = calibration.band_bottom(k)
        top = first + (int((zb - first) / lh + 1e-6) + 1) * lh
        job.layer_gcode.append({"plate": 1, "z": round(top, 4), "kind": "temperature", "value": temps[k - 1], "tower": True})
    parts = [{"name": "Tower body", "mesh": "calibration_tower", "mesh_obj": body, "filament": 1, "type": "normal_part", "units": "mm", "settings": {}}]
    for t, m in labels:
        parts.append({"name": m.name, "mesh": "calibration_tower_%d" % t, "mesh_obj": m, "filament": 1, "type": "negative_part", "units": "mm", "settings": {}})
    job.objects.append({"name": "Calibration tower", "plate": 1, "position": None, "copies": 1, "parts": parts, "settings": {}})


def _user_warning(chk, where, name, meta, cfg, ps):
    kind = meta["kind"]
    parent = meta["parent"]
    base, _m = ps.resolve(kind, parent)
    changed = sorted(k for k in set(cfg) | set(base) if cfg.get(k) != base.get(k) and k in ps.options)
    chk.warn(where, "%s is a user preset of this computer (parent: %s, %d setting(s) changed from its parent). The 3MF holds its name and its "
             "changed values, not the preset itself. On a computer without a user preset of that name, Studio opens the slot as the parent "
             "system preset with those values applied as edits (a project preset). The 3MF also carries the name of your preset." % (
                 repr(name), repr(parent), len(changed)))
    # D7: free text the user typed into the preset (notes, filament_notes ...) is written into the 3MF as it is. Name each key whose value looks
    # like a local path or an e-mail address; the value itself is never printed.
    from .privacy import PATH_RE, EMAIL_RE
    for k in changed:
        v = cfg.get(k)
        txt = v if isinstance(v, str) else " ".join(str(x) for x in v) if isinstance(v, list) else ""
        if PATH_RE.search(txt) or EMAIL_RE.search(txt):
            chk.warn(where, "user preset setting %s holds what looks like a local path or an e-mail address. It goes into the 3MF as it is. Edit it in Studio "
                     "(or in the file) before you share the 3MF." % k)
    if meta.get("dropped"):
        chk.warn(where, "%d key(s) of the user preset file are not %s settings of Studio %s and were ignored (as Studio does)" % (
            len(meta["dropped"]), kind, ps.version))


def load_job(path, root=None, user_presets=None, strict_version=False):
    chk = _Check()
    base = os.path.dirname(os.path.abspath(path))
    try:
        raw = read_raw(path)
    except JobError:
        raise
    except Exception as e:
        raise JobError(["%s could not be read: %s" % (os.path.basename(path), e)])
    if not isinstance(raw, dict):
        raise JobError(["The job file must hold a table of settings"])
    for k in raw:
        if k not in TOP_KEYS:
            near = _closest(k, TOP_KEYS)
            chk.err(k, "unknown job key.%s" % ((" Closest: " + ", ".join(near)) if near else ""))

    job = Job()
    job.path = os.path.abspath(path)
    # ---- studio version -------------------------------------------------------------------
    studio = str(raw.get("studio", "auto"))
    job.studio_note = None
    job.studio_warning = None
    job.auto_slice_check = False
    sv = raw.get("strict_version", False)
    if not isinstance(sv, bool):
        raise JobError(["strict_version: must be true or false"])
    job.strict_version = bool(strict_version or sv)
    if studio == "auto":
        requested = "auto"
    elif studio in STUDIO_ALIASES:
        requested = STUDIO_ALIASES[studio]
    else:
        raise JobError(["studio = %r is not supported. Use %s or \"auto\"." % (
            studio, ", ".join('"%s"' % k for k in sorted(STUDIO_ALIASES) if k.count(".") == 2))])
    try:
        choice = versions.choose(requested, studio_detect.detect() if requested == "auto" else None, STUDIO_VERSIONS, job.strict_version)
    except versions.VersionError as e:
        raise JobError([str(e)])
    ver = choice.version
    job.studio_note = choice.note
    job.studio_warning = choice.warning
    job.studio_mode = choice.mode
    job.auto_slice_check = choice.auto_slice_check
    if choice.warning:
        chk.warn("studio", choice.warning)
    job.studio = ver
    try:
        ps = ProfileSet(ver, root)
    except ProfileError as e:
        raise JobError([str(e)])
    ps.enable_user_presets(user_presets)             # a user preset file is read only if the job names a preset that is not a system preset
    job.profiles = ps
    by = ps.options
    job.warnings = chk.warnings

    # ---- plain fields ---------------------------------------------------------------------
    job.allow_large = raw.get("allow_large", False)
    if not isinstance(job.allow_large, bool):
        chk.err("allow_large", "must be true or false")
        job.allow_large = False
    job.purge_manual, job.purge_table = None, None
    if isinstance(raw.get("purge"), dict):
        job.purge, job.purge_table = "manual", raw["purge"]
    else:
        job.purge = str(raw.get("purge", "auto"))
    if job.purge not in ("auto", "default") and not isinstance(raw.get("purge"), dict):
        chk.err("purge", "%r. Use \"auto\" (Studio's color-based purge volumes), \"default\" (140 + 140 mm3 for every pair) or a table such as { \"1>2\" = 450 }" % job.purge)
    job.output = str(raw.get("output") or os.path.splitext(os.path.basename(path))[0] + ".3mf")
    job.title = str(raw.get("title") or os.path.splitext(job.output)[0])
    job.printer = raw.get("printer")
    job.process = raw.get("process")
    printer_ok = False
    for kind, field in (("machine", "printer"), ("process", "process")):
        name = raw.get(field)
        if not name:
            chk.err(field, "required. Give the Studio preset name%s" % (", e.g. \"Bambu Lab P1S 0.4 nozzle\"" if field == "printer" else ""))
            continue
        try:
            _cfg, meta = ps.resolve(kind, name)
            if meta["instantiation"] != "true":
                chk.err(field, "%r is a base template, not a selectable preset" % name)
            elif field == "printer":
                printer_ok = True
            if meta.get("user"):
                _user_warning(chk, field, name, meta, _cfg, ps)
        except ProfileError as e:
            chk.err(field, str(e))
    if printer_ok and job.process and not [e for e in chk.errors if e.startswith("process")]:
        pcfg, _ = ps.resolve("process", job.process)
        cp = pcfg.get("compatible_printers")
        if cp and job.printer not in cp:
            chk.err("process", "%r is not made for %r. Presets for this printer: %s.%s" % (
                job.process, job.printer, "; ".join(ps.compatible("process", job.printer)[:8]) or "none found",
                ps.user_compat_note("process", job.process, job.printer)))

    # ---- plate type -----------------------------------------------------------------------
    bed_opt = by["curr_bed_type"]
    job.plate_type = None
    if raw.get("plate_type") is not None:
        pt = str(raw["plate_type"])
        names = ps.bed_types
        if pt not in names and pt in PLATE_KEY:
            chk.err("plate_type", "%s needs Studio 2.9.2 or newer; this job builds for Studio %s. Set studio = \"2.9.2\", or choose one of: %s" % (
                pt, ver, ", ".join(sorted(names))))
        elif pt not in names:
            chk.err("plate_type", "%r is not a plate type. Choose one of: %s" % (pt, ", ".join(sorted(names))))
        else:
            job.plate_type = pt

    # ---- process settings -----------------------------------------------------------------
    job.settings = _settings_table(raw.get("settings", {}), ("print",), by, ver, "[settings]", chk,
                                   "[settings] (process settings only; filament settings go under [filament.settings])")
    for k, v in DEFAULT_SETTINGS.items():
        if k in by and k not in job.settings:
            job.settings[k] = v

    # ---- filaments ------------------------------------------------------------------------
    job.filaments = []
    fl = raw.get("filament")
    if fl is None:
        chk.err("filament", "at least one [[filament]] is required")
        fl = []
    if not isinstance(fl, list):
        chk.err("filament", "must be written as [[filament]] tables")
        fl = []
    if len(fl) > 16:
        chk.err("filament", "%d filaments. Studio supports up to 16." % len(fl))
    for i, f in enumerate(fl, 1):
        w = "[[filament]] #%d" % i
        if not isinstance(f, dict):
            chk.err(w, "must be a table"); continue
        for k in f:
            if k not in FILAMENT_KEYS:
                chk.err("%s.%s" % (w, k), "unknown key.%s" % ((" Closest: " + ", ".join(_closest(k, FILAMENT_KEYS))) if _closest(k, FILAMENT_KEYS) else ""))
        preset = f.get("preset")
        entry = {"preset": preset, "color": None, "settings": {}}
        if not preset:
            chk.err(w, "preset is required")
        else:
            try:
                fcfg, meta = ps.resolve("filament", preset)
                if meta["instantiation"] != "true":
                    chk.err(w + ".preset", "%r is a base template, not a selectable preset" % preset)
                cp = fcfg.get("compatible_printers")
                if cp and printer_ok and job.printer not in cp:
                    chk.err(w + ".preset", "%r is not made for %r. Presets for this printer: %s.%s" % (
                        preset, job.printer, "; ".join(ps.compatible("filament", job.printer, like=preset)[:8]) or "none found",
                        ps.user_compat_note("filament", preset, job.printer)))
                entry["meta"] = meta
                if meta.get("user"):
                    _user_warning(chk, w + ".preset", preset, meta, fcfg, ps)
            except ProfileError as e:
                chk.err(w + ".preset", str(e))
        col = f.get("color")
        if col is None:
            chk.err(w, "color is required, as \"#RRGGBB\"")
        else:
            c = str(col)
            if len(c) == 7 and c[0] == "#" and all(ch in "0123456789abcdefABCDEF" for ch in c[1:]):
                entry["color"] = c.upper()
            else:
                chk.err(w + ".color", "%r is not a color. Use \"#RRGGBB\", e.g. \"#C0C0C0\"" % c)
        entry["settings"] = _settings_table(f.get("settings", {}), ("filament",), by, ver, w + ".settings", chk,
                                            "[filament.settings] (filament settings only)", job.plate_type)
        job.filaments.append(entry)

    if job.purge_table is not None:
        from . import flush as _fl
        job.purge_manual, _errs = _fl.parse_manual(job.purge_table, len(job.filaments), job.studio)
        for _e in _errs:
            chk.err("purge", _e)

    # bed temperature for the chosen plate must be non-zero for every filament
    plate_key = PLATE_KEY
    if job.plate_type and job.plate_type in plate_key:
        k = plate_key[job.plate_type]
        for i, f in enumerate(job.filaments, 1):
            if "meta" not in f:
                continue
            cfg, _m = ps.resolve("filament", f["preset"])
            vals = f["settings"].get(k)
            vals = [vals] if vals is not None else cfg.get(k, [])
            if vals and all(str(v) == "0" for v in vals):
                chk.err("[[filament]] #%d" % i, "%s does not support %s (%s = 0). Pick another plate or filament." % (f["preset"], job.plate_type, k))

    # ---- objects --------------------------------------------------------------------------
    job.objects = []
    objs = raw.get("object")
    tw_raw = raw.get("temperature_tower")
    calib = isinstance(tw_raw, dict) and tw_raw.get("shape") == "calibration"
    if calib:
        if objs:
            chk.err("object", "[temperature_tower] shape = \"calibration\" makes its own object. Remove the [[object]] tables.")
        objs = []
    elif not objs or not isinstance(objs, list):
        chk.err("object", "at least one [[object]] with [[object.part]] is required")
        objs = []
    nfil = len(job.filaments)
    used = set()
    for i, o in enumerate(objs, 1):
        w = "[[object]] #%d" % i
        if not isinstance(o, dict):
            chk.err(w, "must be a table"); continue
        for k in o:
            if k not in OBJECT_KEYS:
                chk.err("%s.%s" % (w, k), "unknown key.%s" % ((" Closest: " + ", ".join(_closest(k, OBJECT_KEYS))) if _closest(k, OBJECT_KEYS) else ""))
        if any(c in str(o.get("name") or "") for c in BAD_NAME_CHARS):
            chk.err(w + ".name", "%r contains one of %s, which Studio drops. Use letters, digits, spaces, - or _" % (o.get("name"), " ".join(BAD_NAME_CHARS)))
        ent = {"name": str(o.get("name") or "Object %d" % i), "plate": int(o.get("plate", 1)), "position": None,
               "copies": int(o.get("copies", 1)), "parts": []}
        if ent["plate"] < 1:
            chk.err(w + ".plate", "plates count from 1")
        if ent["copies"] < 1:
            chk.err(w + ".copies", "must be 1 or more")
        pos = o.get("position")
        if pos is not None:
            if isinstance(pos, list) and len(pos) == 2 and all(isinstance(v, (int, float)) for v in pos):
                ent["position"] = (float(pos[0]), float(pos[1]))
            else:
                chk.err(w + ".position", "must be [x, y] in mm")
        ent["settings"] = _settings_table(o.get("settings", {}), ("print",), by, ver, w + ".settings", chk,
                                          "[object.settings] (process settings only)")
        ent["height_ranges"] = _height_ranges(o.get("height_range"), by, ver, w, chk, ps, nfil)
        parts = o.get("part")
        if not parts or not isinstance(parts, list):
            chk.err(w, "needs at least one [[object.part]]")
            parts = []
        for j, p in enumerate(parts, 1):
            pw = "%s part #%d" % (w, j)
            if not isinstance(p, dict):
                chk.err(pw, "must be a table"); continue
            for k in p:
                if k not in PART_KEYS:
                    chk.err("%s.%s" % (pw, k), "unknown key.%s" % ((" Closest: " + ", ".join(_closest(k, PART_KEYS))) if _closest(k, PART_KEYS) else ""))
            if any(c in str(p.get("name") or "") for c in BAD_NAME_CHARS):
                chk.err(pw + ".name", "%r contains one of %s, which Studio drops. Use letters, digits, spaces, - or _" % (p.get("name"), " ".join(BAD_NAME_CHARS)))
            pe = {"name": str(p.get("name") or "Part %d" % j), "mesh": None, "filament": int(p.get("filament", 1)),
                  "type": p.get("type", "normal_part"), "units": str(p.get("units", "mm"))}
            if pe["type"] not in PART_TYPES:
                chk.err(pw + ".type", "%r. Choose one of: %s" % (pe["type"], ", ".join(PART_TYPES)))
            if pe["units"] not in ("mm", "inch"):
                chk.err(pw + ".units", "must be \"mm\" or \"inch\"")
            if not (1 <= pe["filament"] <= max(nfil, 1)):
                chk.err(pw + ".filament", "slot %d does not exist; the job defines %d filament slot(s)" % (pe["filament"], nfil))
            else:
                used.add(pe["filament"])
            mp = p.get("mesh")
            if not mp:
                chk.err(pw, "mesh is required (path to an .stl, .obj or .3mf, relative to the job file)")
            else:
                full = mp if os.path.isabs(mp) else os.path.normpath(os.path.join(base, mp))
                if not os.path.isfile(full):
                    chk.err(pw + ".mesh", "file not found: %s" % full)
                elif not full.lower().endswith((".stl", ".obj", ".3mf")):
                    chk.err(pw + ".mesh", "unsupported format. Use .stl, .obj or .3mf (STEP is not supported; export STL from your CAD tool)")
                pe["mesh"] = full
            pe["settings"] = _settings_table(p.get("settings", {}), ("print",), by, ver, pw + ".settings", chk,
                                             "[object.part.settings] (process settings only)")
            if pe["type"] != "normal_part":
                for k in sorted(MODEL_PART_ONLY & set(pe["settings"])):
                    chk.err("%s.settings.%s" % (pw, k), "%s applies to a normal part only, not to a %s (Studio hides it for modifiers)" % (k, pe["type"]))
                    pe["settings"].pop(k)
            ent["parts"].append(pe)
        job.objects.append(ent)
    if nfil:
        for s in range(1, nfil + 1):
            if s not in used and job.objects:
                chk.warn("[[filament]] #%d" % s, "no part uses this slot. It stays in the project (purge volume is still counted).")

    # ---- layer g-code ---------------------------------------------------------------------
    job.layer_gcode = []
    for i, g in enumerate(raw.get("layer_gcode", []) or [], 1):
        w = "[[layer_gcode]] #%d" % i
        if not isinstance(g, dict) or "z" not in g:
            chk.err(w, "needs z (mm)"); continue
        for k in g:
            if k not in LAYER_KEYS:
                chk.err("%s.%s" % (w, k), "unknown key")
        kinds = [k for k in ("temperature", "pause", "gcode") if k in g]
        if len(kinds) != 1:
            chk.err(w, "give exactly one of temperature, pause or gcode"); continue
        job.layer_gcode.append({"plate": int(g.get("plate", 1)), "z": float(g["z"]), "kind": kinds[0], "value": g[kinds[0]]})
    tw = raw.get("temperature_tower")
    job.calibration = None
    if tw is not None:
        w = "[temperature_tower]"
        if not isinstance(tw, dict):
            chk.err(w, "must be a table")
        elif tw.get("shape", "custom") not in TOWER_SHAPES:
            chk.err(w + ".shape", "%r. Use \"calibration\" (the builder makes the tower) or \"custom\" (your mesh; the default)" % (tw.get("shape"),))
        elif calib:
            _calibration_tower(tw, job, ps, by, chk, nfil)
        else:
            miss = [k for k in ("start", "step", "step_height", "first_z", "steps") if k not in tw]
            for k in tw:
                if k not in TOWER_KEYS:
                    chk.err("%s.%s" % (w, k), "unknown key")
            if miss:
                chk.err(w, "missing: %s" % ", ".join(miss))
            else:
                for n in range(int(tw["steps"])):
                    job.layer_gcode.append({"plate": int(tw.get("plate", 1)),
                                            "z": round(float(tw["first_z"]) + n * float(tw["step_height"]), 4),
                                            "kind": "temperature", "value": int(tw["start"]) + n * int(tw["step"]), "tower": True})
    for g in job.layer_gcode:
        if g["kind"] == "temperature":
            nt = by["nozzle_temperature"]
            lo, hi = nt.get("min"), nt.get("max")
            v = g["value"]
            if not isinstance(v, (int, float)) or (lo is not None and v < lo) or (hi is not None and v > hi):
                chk.err("layer_gcode z=%s" % g["z"], "temperature %r is outside %s to %s" % (v, lo, hi))

    if chk.errors:
        raise JobError(chk.errors)
    return job
