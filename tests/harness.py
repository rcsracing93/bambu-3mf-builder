"""Pure grading functions for the real-slicer tests (no Studio needed to import or unit-test them).

Rule for every function here: the expectation comes from the JOB FILE and the MESH FILES, never from what the builder wrote,
so a builder bug cannot make a wrong file look right. Each function returns (ok, detail)."""
import json, math, os, re, zipfile
import xml.etree.ElementTree as ET


def list_examples(tree=None, paths=False):
    """The example jobs of a tree (default: this repo), sorted: file names (name.toml), or full paths with paths=True. Every test
    and tool lists the examples with this. Names that start with "_" are never examples."""
    d = os.path.join(tree or os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")), "examples")
    names = sorted(f for f in os.listdir(d) if f.endswith(".toml") and not f.startswith("_"))
    return [os.path.join(d, f) for f in names] if paths else names

# ---- the Studio log ----------------------------------------------------------------------------------------------
# Warnings Studio 2.8.2 and 2.9.1 print for every healthy slice at --debug 2. Each one was read in the source and is harmless.
BENIGN_LOG = [
    r"cli mode, Current BambuStudio Version",
    r"can not find system preset file: .*/(machine|process|filament)_full/",       # the CLI looks for an optional "full" copy
    r"no filament colors found in projects",                                          # BambuStudio.cpp: no colors given on the command line
    r"load_nozzle_infos_with_compatibility\]? ?:? ?building nozzle list from filament map",      # 2.9.1 prints no function prefix; same in a real save of both versions
    r"tree support default to organic support",
    r"the parent path .* is not there, create it",
    r"can not get (printer|print)_extruder_variant or (printer|print)_extruder_id from new config",   # printed by --uptodate for real Studio saves too (2.8.2 and 2.9.1)
]
FATAL_STRINGS = ["could not found extruder_type", "No valid nozzle", "Flush volumes matrix do not match", "unprintable area",
                 "invalid config", "Invalid configuration", "load geometry data only", "Segmentation fault", "std::exception"]


def scan_log(text):
    """Lines that are neither benign nor informational: every [warning]/[error]/[fatal] line outside BENIGN_LOG, and any FATAL_STRINGS hit."""
    bad = []
    for line in text.splitlines():
        low = re.search(r"\[(warning|error|fatal)\]", line)
        if any(s in line for s in FATAL_STRINGS):
            bad.append(line.strip()[-200:])
        elif low and not any(re.search(p, line) for p in BENIGN_LOG):
            bad.append(line.strip()[-200:])
    return bad


# ---- G-code config block -----------------------------------------------------------------------------------------
def config_block(gcode_path):
    cfg, on = {}, False
    with open(gcode_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("; CONFIG_BLOCK_START"):
                on = True
                continue
            if line.startswith("; CONFIG_BLOCK_END"):
                break
            if on and line.startswith("; ") and " = " in line:
                k, v = line[2:].rstrip("\n").split(" = ", 1)
                cfg[k] = v
    return cfg


def _esc(t):
    return t.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


VARIANT_LISTS = {"filament": "filament_extruder_variant", "print": "print_extruder_variant", "printer": "printer_extruder_variant"}


def active_variant(cfg):
    """Name of the variant Studio prints: the extruder type and nozzle volume type of the printer (checked on all 12 examples:
    the G-code prints printer_extruder_variant = "<extruder_type> <nozzle_volume_type>")."""
    et, nv = cfg.get("extruder_type"), cfg.get("nozzle_volume_type")
    et = et[0] if isinstance(et, list) and et else et
    nv = nv[0] if isinstance(nv, list) and nv else nv
    return "%s %s" % (et, nv) if et and nv else None


def active_forms(items, opt, n, cfg=None):
    """Every way the G-code block may print this value list. The block prints one entry per group (filament, or the whole
    list for print/printer keys) of the ACTIVE variant, so a variant list of V entries per group shows one of V choices.
    A list can never be shortened any other way: a partial list (first element only, fewer groups) is not a form."""
    items = [str(x) for x in items]
    if not (opt and opt.get("variant")):
        return [items]
    groups = n if opt.get("preset") == "filament" else 1
    stride = opt.get("variant_stride") or 1
    if not groups or len(items) % (groups * stride) or len(items) == groups * stride:
        return [items]
    per = len(items) // groups                      # entries per group = V * stride
    v = per // stride
    names = (cfg or {}).get(VARIANT_LISTS.get(opt.get("variant"), ""), [])
    act = active_variant(cfg or {})
    out = []
    for g in range(groups):                         # the active variant may sit at a different index in each group
        blk = names[g * v:(g + 1) * v] if len(names) == groups * v else names[:v]
        if act is None or act not in blk:
            return []                               # cannot tell which variant is active: no form matches (fails loudly)
        k = blk.index(act)
        out += [items[g * per + k * stride + j] for j in range(stride)]
    return [out]


def same_value(job_v, gcode_v, opt=None, n=1, cfg=None):
    items = job_v if isinstance(job_v, list) else [job_v]
    g = gcode_v.rstrip()
    for form in active_forms(items, opt, n, cfg):
        for e in (form, [_esc(x) for x in form]):
            for quote in (False, True):
                part = ['"%s"' % x for x in e] if quote else e
                for j in (",", ";", "|"):
                    c = j.join(part)
                    if g == c or gcode_v == c or (len(g) > 1 and g[0] == g[-1] == '"' and g[1:-1] == c):
                        return True
    return False


def num_equal(a, b):
    try:
        return math.isclose(float(str(a).rstrip("%")), float(str(b).rstrip("%")), rel_tol=1e-6, abs_tol=1e-9) and \
            str(a).endswith("%") == str(b).endswith("%")
    except ValueError:
        return str(a) == str(b)


# Keys the config block does not print: the set a REAL Studio 2.8.2 save is missing when sliced (checked on
# tests/data/four-color-autocalc-282.3mf, plus from/name/version which are file header keys); any other missing key is a failure.
MISSING_OK = {"extruder_printable_height",
              "filament_deretraction_speed", "filament_retract_before_wipe", "filament_retract_restart_extra",
              "filament_retract_when_changing_layer", "filament_retraction_length", "filament_retraction_minimum_travel",
              "filament_retraction_speed", "filament_wipe", "filament_wipe_distance", "filament_z_hop", "filament_z_hop_types",
              "from", "name", "version"}
# printed per active variant like a variant list, though the option table does not mark them (same in a real save: 8 entries -> 4)
VARIANT_LIKE = {"filament_self_index": {"preset": "filament", "variant": "filament", "variant_stride": 1}}
# 2.9.1 does not print these two either: same in a real 2.9.1 save (tests/data/four-color-autocalc-291.3mf)
MISSING_OK_291 = {"filament_retract_length_toolchange", "filament_retract_restart_extra_toolchange",
                  "nozzle_type"}   # legacy key: on the load-time ignore list in 2.9.1 (PrintConfig.cpp), absent from real 2.9.1 saves
# 2.9.1 prints filament_diameter_map empty (";;;") although the file holds one diameter per slot: same in a real 2.9.1 save
# (tests/data/four-color-autocalc-291.3mf holds 0.4 x4, its G-code prints ";;;"; PresetBundle.cpp leaves a single-diameter map empty)
EMPTY_IN_GCODE_291 = {"filament_diameter_map"}
# Keys whose G-code value differs from the file for a known reason. Every other written key must reach the G-code unchanged.
SKIP_KEYS = {"different_settings_to_system", "inherits_group",   # preset bookkeeping, not printed
             "filament_prime_volume",       # the CLI prints Studio's 45 for a file holding 30 (sliced: T13/R37 reserve 45)
             "enable_prime_tower",          # the slicer turns it off on a plate with one filament (multi_plate)
             "version"}


def _at_least(version, want):
    try:
        return tuple(int(x) for x in str(version).split(".")[:3]) >= want
    except ValueError:
        return False


def compare_config(written, blk, options, n, version=None):
    """The builder's config must reach the G-code. Returns (differ, missing) lists of key names."""
    differ, missing = [], []
    v291 = _at_least(version, (2, 9, 1))                 # the 2.9.1 behaviour below is also seen in 2.9.2 (checked on macOS (Apple silicon) with Studio 2.9.2.62)
    for k, v in written.items():
        if k in SKIP_KEYS or k in MISSING_OK or (v291 and k in MISSING_OK_291):
            continue
        if k not in blk:
            missing.append(k)
            continue
        if v291 and k in EMPTY_IN_GCODE_291 and not blk[k].replace(";", "").replace(",", "").strip('" '):
            continue
        if not same_value(v, blk[k], VARIANT_LIKE.get(k) or options.get(k), n, written):
            differ.append("%s: written=%s gcode=%s" % (k, ",".join(map(str, v)) if isinstance(v, list) else v, blk[k][:40]))
    return differ, missing


def slot_value(gcode_v, i, n):
    """Entry of filament slot i (0-based) from a G-code value of n entries."""
    parts = [p.strip().strip('"') for p in re.split(r"[;,]", gcode_v)]
    return parts[i] if len(parts) == n else (parts[0] if len(set(parts)) == 1 else None)


def _real_key(k, kind, raw):
    """The Studio key a job key means (friendly names, scripts/bambu3mf/job.py)."""
    import bambu3mf.job as J
    if kind == "filament" and k in ("bed_temp", "bed_temp_first_layer"):
        base = J.PLATE_KEY.get(raw.get("plate_type"))
        return base and (base if k == "bed_temp" else base + "_initial_layer")
    return (J.PRINT_ALIASES if kind == "print" else J.FILAMENT_ALIASES).get(k, k)


def job_intent(raw_job, job, blk, n):
    """What the job FILE asks for must be in the G-code: the process settings, each filament's settings, color and preset.
    Read from the raw TOML, not from the loaded job, so a loader that drops or changes a value fails here."""
    bad = []
    for k, v in (raw_job.get("settings") or {}).items():
        rk = _real_key(k, "print", raw_job)
        if rk not in blk:
            bad.append("%s: not in the G-code" % rk)
        elif not num_equal(norm(v), blk[rk].strip('"')):
            bad.append("%s: job=%s gcode=%s" % (rk, v, blk[rk][:30]))
    fils = raw_job.get("filament") or []
    if len(fils) != n:
        bad.append("job has %d filaments, the G-code %d" % (len(fils), n))
    for i, f in enumerate(fils):
        for k, v in (f.get("settings") or {}).items():
            rk = _real_key(k, "filament", raw_job)
            if not rk or rk not in blk:
                bad.append("filament %d %s: not in the G-code" % (i + 1, rk or k))
                continue
            got = slot_value(blk[rk], i, n)
            if got is None or not num_equal(norm(v), got):
                bad.append("filament %d %s: job=%s gcode=%s" % (i + 1, rk, v, blk[rk][:30]))
        col = slot_value(blk.get("filament_colour", ""), i, n)
        if col is None or col.upper() != str(f.get("color", "")).upper():
            bad.append("filament %d color: job=%s gcode=%s" % (i + 1, f.get("color"), col))
        pre = slot_value(blk.get("filament_settings_id", ""), i, n)
        if pre != f.get("preset"):
            bad.append("filament %d preset: job=%s gcode=%s" % (i + 1, f.get("preset"), pre))
    for k, key in (("printer", "printer_settings_id"), ("process", "print_settings_id")):
        if raw_job.get(k) and blk.get(key, "").strip('"') != raw_job[k]:
            bad.append("%s: job=%s gcode=%s" % (key, raw_job[k], blk.get(key, "")[:40]))
    return bad


def job_plates(raw_job):
    """Number of plates the job file asks for (highest plate number of any object)."""
    return max([int(o.get("plate", 1)) for o in raw_job.get("object", [])] or [1])


def norm(v):
    if isinstance(v, list):
        return v[0] if v else ""
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)


# ---- G-code body ------------------------------------------------------------------------------------------------
MODEL_FEATURES = ("Outer wall", "Inner wall", "Sparse infill", "Internal solid infill", "Top surface", "Bottom surface", "Bridge",
                  "Overhang wall", "Gap infill", "Solid infill", "Ironing", "Support")


def parse_body(gcode_path):
    """One pass over the G-code. Returns dict: layers {z: {tools:set, xs:[], ys:[]}}, tower (x0,y0,x1,y1)|None, temps [(z,t)], pauses [z]."""
    z, tool, feat = 0.0, 0, None
    layers, temps, pauses, txs, tys = {}, [], [], [], []
    x = y = None
    in_body = macro = False
    with open(gcode_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("; EXECUTABLE_BLOCK_START"):
                in_body = True
            if line.startswith("; Z_HEIGHT:"):
                z = round(float(line.split(":")[1]), 4)
                layers.setdefault(z, {"tools": set(), "xs": [], "ys": []})
                continue
            if line.startswith("; FEATURE:"):
                feat = line.split(":", 1)[1].strip()
                continue
            if line.startswith("; PAUSE_PRINTING"):
                pauses.append(z)
                continue
            m = re.match(r"M620 S(\d+)A", line)
            if m:
                macro = True
                if int(m.group(1)) < 255:
                    tool = int(m.group(1))
                continue
            if line.startswith("M621 "):
                macro = False
                continue
            if macro:
                continue                                   # moves inside the filament change macro leave the tower
            m = re.match(r"M104 S(\d+)", line)
            if m and in_body:
                temps.append((z, int(m.group(1))))
                continue
            if line.startswith(("G1 ", "G0 ")):
                mx, my = re.search(r" X(-?[\d.]+)", line), re.search(r" Y(-?[\d.]+)", line)
                if mx:
                    x = float(mx.group(1))
                if my:
                    y = float(my.group(1))
                me = re.search(r" E(-?[\d.]+)", line)
                if me and float(me.group(1)) > 0 and mx and my and in_body:
                    if feat == "Prime tower":
                        txs.append(x); tys.append(y)
                    elif feat in MODEL_FEATURES:
                        L = layers.setdefault(z, {"tools": set(), "xs": [], "ys": []})
                        L["tools"].add(tool)
                        L["xs"].append(x); L["ys"].append(y)
    return {"layers": layers, "temps": temps, "pauses": pauses,
            "tower": (min(txs), min(tys), max(txs), max(tys)) if txs else None}


def part_ranges(raw_job, root, read_mesh, plate):
    """[(slot, zmin, zmax)] for every normal part on a plate, from the job and the mesh files alone. Object base sits on the bed."""
    out = []
    for o in raw_job.get("object", []):
        if int(o.get("plate", 1)) != plate:
            continue
        rows = []
        for p in o.get("part", []):
            if p.get("type", "normal_part") != "normal_part":
                continue
            m = read_mesh(os.path.join(root, p["mesh"]))
            lo, hi = m.bbox()
            k = 25.4 if p.get("units", "mm") == "inch" else 1.0
            rows.append((int(p.get("filament", 1)), lo[2] * k, hi[2] * k))
        base = min((r[1] for r in rows), default=0.0)
        out += [(s, a - base, b - base) for s, a, b in rows]
    return out


def check_layer_tools(layers, ranges, eps=0.005):
    """Per layer: the tools that printed model features are exactly the slots of the parts that fill that layer.
    A part fills a layer (z0, z1] when it covers all of it; a part that only touches it may or may not show."""
    zs = sorted(layers)
    bad = []
    prev = 0.0
    for z in zs:
        z0 = prev
        prev = z
        if not layers[z]["tools"]:
            continue
        must = {s - 1 for s, a, b in ranges if a <= z0 + eps and b >= z - eps}
        may = must | {s - 1 for s, a, b in ranges if a < z - eps and b > z0 + eps}
        got = layers[z]["tools"]
        if not must <= got or not got <= may:
            bad.append("z %.2f: tools %s, expected %s%s" % (z, sorted(got), sorted(must), "" if may == must else " (may include %s)" % sorted(may - must)))
    return not bad, "%d layers, %s" % (len(zs), "; ".join(bad[:3]) if bad else "tools match the parts")


def check_filaments_result(plate, slots_job, presets_ids):
    """result.json plate entry: used slots are exactly the job's slots for this plate, each carries the preset's filament id, and each
    used slot printed model material."""
    got = {f["id"]: f for f in plate.get("filaments", [])}
    bad = []
    if set(got) != set(slots_job):
        bad.append("used %s, job %s" % (sorted(got), sorted(slots_job)))
    for s in slots_job:
        f = got.get(s)
        if f is None:
            continue
        want = presets_ids.get(s)
        if want and f.get("filament_id") != want:
            bad.append("slot %d filament_id %s, preset has %s" % (s, f.get("filament_id"), want))
        if not f.get("main_used_g", 0) > 0:
            bad.append("slot %d printed no model material" % s)
    return not bad, "; ".join(bad) if bad else "slots %s" % sorted(got)


def check_tower(plate, slots_n):
    tower_t = plate.get("feature_type_times", {}).get("Prime tower", 0)
    changes = plate.get("filament_change_times", 0)
    if slots_n > 1:
        ok = tower_t > 0 and changes >= slots_n - 1
        return ok, "slots=%d prime_tower_s=%.0f changes=%s" % (slots_n, tower_t, changes)
    return tower_t == 0 and changes == 0, "single colour, prime_tower_s=%.0f changes=%s" % (tower_t, changes)


def check_tower_clear(tower, layers, pad=0.0):
    """The real prime tower does not overlap the footprint of the printed model."""
    if not tower:
        return True, "no tower"
    xs = [x for L in layers.values() for x in L["xs"]]
    ys = [y for L in layers.values() for y in L["ys"]]
    if not xs:
        return True, "no model moves"
    mx0, my0, mx1, my1 = min(xs), min(ys), max(xs), max(ys)
    overlap = tower[0] - pad < mx1 and tower[2] + pad > mx0 and tower[1] - pad < my1 and tower[3] + pad > my0
    return not overlap, "tower %s, model x %.0f..%.0f y %.0f..%.0f" % (tuple(round(v) for v in tower), mx0, mx1, my0, my1)


def check_temps(parsed, want, tol=0.001):
    """Temperature changes above the first layer, in order, exactly as asked. Nothing else may change the nozzle temperature."""
    got = [(z, t) for z, t in parsed["temps"] if t > 0 and z > 0.35]
    want = [(round(float(a), 3), int(b)) for a, b in want]
    ok = len(got) == len(want) and all(abs(g[0] - w[0]) <= tol + 0.2 and g[1] == w[1] for g, w in zip(got, want))
    return ok, "want=%s got=%s" % (want, [(round(z, 3), t) for z, t in got])


def check_pauses(parsed, want):
    got = sorted(round(z, 3) for z in parsed["pauses"])
    want = sorted(round(float(z), 3) for z in want)
    return got == want, "want=%s got=%s" % (want, got)


# ---- Studio re-export ----------------------------------------------------------------------------------------------
def read_export(path):
    """{object name: {"settings": {...}, "parts": [{"name", "subtype", "extruder", "settings", "faces", "fixed"}]}} from a Studio-saved 3MF."""
    z = zipfile.ZipFile(path)
    root = ET.fromstring(z.read("Metadata/model_settings.config"))
    out = {}
    SKIP = {"name", "extruder", "matrix", "source_file", "source_object_id", "source_volume_id", "source_offset_x", "source_offset_y",
            "source_offset_z", "mesh_type", "volume_type"}
    for ob in root.findall("object"):
        md = {m.get("key"): m.get("value") for m in ob.findall("metadata") if m.get("key")}
        parts = []
        for p in ob.findall("part"):
            pm = {m.get("key"): m.get("value") for m in p.findall("metadata") if m.get("key")}
            ms = p.find("mesh_stat")
            parts.append({"name": pm.get("name"), "subtype": p.get("subtype"), "extruder": pm.get("extruder") or md.get("extruder"),
                          "settings": {k: v for k, v in pm.items() if k not in SKIP},
                          "faces": int(ms.get("face_count")) if ms is not None else None,
                          "fixed": sum(int(ms.get(a, 0)) for a in ("edges_fixed", "degenerate_facets", "facets_removed", "facets_reversed", "backwards_edges")) if ms is not None else None})
        out[md.get("name")] = {"settings": {k: v for k, v in md.items() if k not in SKIP and k != "face_count"}, "parts": parts}
    return out


def check_export(exp, job, tri_counts):
    """Studio's own re-save must still hold the job: objects, part names, subtypes, filament slot per part, per-object and per-part
    settings, triangle counts, and no mesh repairs. The expectation is read from the loaded job (values already coerced)."""
    bad = []
    if sorted(exp) != sorted(o["name"] for o in job.objects):
        bad.append("objects %s" % sorted(exp))
    for i, o in enumerate(job.objects, 1):
        e = exp.get(o["name"])
        if e is None:
            continue
        for k, v in (o.get("settings") or {}).items():
            if k not in e["settings"] or not num_equal(norm(v), e["settings"][k]):
                bad.append("%s: object setting %s=%r, export has %r" % (o["name"], k, v, e["settings"].get(k)))
        if len(o["parts"]) != len(e["parts"]):
            bad.append("%s: %d parts, export has %d" % (o["name"], len(o["parts"]), len(e["parts"])))
            continue
        for j, (p, ep) in enumerate(zip(o["parts"], e["parts"]), 1):
            if ep["subtype"] != p["type"]:
                bad.append("%s part %d: subtype %s, job says %s" % (o["name"], j, ep["subtype"], p["type"]))
            if p["type"] == "normal_part" and int(ep["extruder"] or 0) != int(p["filament"]):
                bad.append("%s part %d: filament %s, job says %s" % (o["name"], j, ep["extruder"], p["filament"]))
            for k, v in (p.get("settings") or {}).items():
                if k not in ep["settings"] or not num_equal(norm(v), ep["settings"][k]):
                    bad.append("%s part %d: setting %s=%r, export has %r" % (o["name"], j, k, v, ep["settings"].get(k)))
            want_faces = tri_counts.get((i, j))
            if want_faces is not None and ep["faces"] != want_faces:
                bad.append("%s part %d: %s faces, mesh has %s" % (o["name"], j, ep["faces"], want_faces))
            if ep["fixed"]:
                bad.append("%s part %d: Studio repaired the mesh (%s fixes)" % (o["name"], j, ep["fixed"]))
    return not bad, "; ".join(bad[:4]) if bad else "%d objects, parts, slots and settings intact" % len(exp)


def check_height_ranges(layers, ranges, base_h, first_h):
    """Real layer heights against the job's height ranges. layers: the Z_HEIGHT values of one object's plate. ranges: [(min_z, max_z, h)]
    from the job file. A layer fully inside a range must be that range's height; a layer at least one layer away from every range edge must
    be the base height. The first layer keeps its own height. Returns (ok, detail)."""
    zs = sorted(layers)
    bad, seen = [], {i: 0 for i in range(len(ranges))}
    plain = 0
    for a, b in zip([0.0] + zs, zs):
        h = b - a
        if a < 1e-6:
            if abs(h - first_h) > 0.006:
                bad.append("first layer %.3f, want %.3f" % (h, first_h))
            continue
        inside = [i for i, (lo, hi, _h) in enumerate(ranges) if a >= lo - 1e-3 and b <= hi + 1e-3]
        if inside:
            want = ranges[inside[0]][2]
            seen[inside[0]] += 1
            if abs(h - want) > 0.006:
                bad.append("layer %.3f-%.3f is %.3f, range wants %.3f" % (a, b, h, want))
        elif all(b <= lo - base_h - 1e-3 or a >= hi + base_h - 1e-3 for lo, hi, _h in ranges):
            plain += 1
            if abs(h - base_h) > 0.006:
                bad.append("layer %.3f-%.3f is %.3f, base is %.3f" % (a, b, h, base_h))
    empty = [i + 1 for i, c in seen.items() if c == 0]
    if empty:
        bad.append("no layer inside range(s) %s" % empty)
    if bad:
        return False, "; ".join(bad[:4])
    return True, "%s; %d plain layers at %.2f" % (", ".join("range %d: %d layers at %.2f" % (i + 1, seen[i], ranges[i][2]) for i in range(len(ranges))), plain, base_h)
