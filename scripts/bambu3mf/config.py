"""Config builder: job -> the full project_settings.config dictionary.

Layers (PresetBundle::full_fff_config): option defaults, process preset, filament presets (n), printer
preset, project options. Job overrides go on top of the resolved system presets. Every key whose final
value differs from the system preset is listed in `different_settings_to_system`, computed by diff.
"""
import json
import math

from .serialize import normalize

EXCLUDE = {
    "compatible_printers", "compatible_printers_condition", "compatible_prints", "compatible_prints_condition",
    "compatible_machine_expression_group", "compatible_process_expression_group", "inherits",
    "preset_name", "preset_names", "extruder", "filament_map_2", "filament_retract_lift_above",
    "filament_retract_lift_below", "first_layer_sequence_choice", "other_layers_sequence_choice",
    "print_host", "print_host_webui", "printhost_apikey", "printhost_cafile", "printhost_password",
    "printhost_port", "printhost_user",
}
# keys set by the builder (not copied from a preset); never listed in the diff lists
BUILDER_KEYS = {"print_settings_id", "filament_settings_id", "printer_settings_id", "inherits_group",
                "different_settings_to_system", "filament_ids", "filament_colour", "filament_multi_colour",
                "filament_self_index", "print_compatible_printers", "extruder_ams_count",
                "extruder_nozzle_stats", "extruder_nozzle_stats_new", "curr_bed_type",
                "wipe_tower_x", "wipe_tower_y", "flush_volumes_matrix", "flush_volumes_vector",
                "flush_multiplier", "flush_multiplier_fast"}
# project options that hold one entry per filament (default element repeated n times)
PER_FILAMENT_PROJECT = {"filament_colour_type", "filament_is_mixed", "filament_mixed_components",
                        "filament_mixed_sublayer_ratios", "filament_mixed_gradient", "filament_mixed_gradient_range",
                        "filament_mixed_gradient_curve", "filament_mixed_gradient_per_part", "filament_map",
                        "filament_nozzle_map", "filament_volume_map"}
# filament options that keep their own length per filament (Preset::normalize skips them; PrintConfig.cpp filament_dev_options, both tags)
FILAMENT_DEV_OPTIONS = {"filament_dev_ams_drying_ams_limitations", "filament_dev_ams_drying_temperature",
                        "filament_dev_ams_drying_time", "filament_dev_ams_drying_heat_distortion_temperature",
                        "filament_dev_chamber_drying_bed_temperature", "filament_dev_chamber_drying_time",
                        "filament_dev_drying_softening_temperature", "filament_dev_drying_cooling_temperature"}
# Project string lists Studio grows lazily: default is one "" entry, and every reader pads on demand
# (Plater.cpp "while (values.size() <= idx) push_back"). Unlike filament_is_mixed, filament_mixed_gradient,
# filament_mixed_gradient_curve and filament_mixed_gradient_per_part, PresetBundle does not resize them on load, so a
# real 2.8.2.61 save of a 2-filament project holds 1 entry. Both tags (v02.08.02.61, v02.09.01.70). Any length 1..n is valid.
LAZY_PROJECT_LISTS = {"filament_mixed_components", "filament_mixed_sublayer_ratios", "filament_mixed_gradient_range"}
AMS_NONE = "1#0|4#0"


class ConfigError(Exception):
    pass


def written_keys(options, project_options):
    keys = []
    for o in options:
        k = o["key"]
        if k in EXCLUDE or (o["preset"] == "other" and k.startswith("extruder_nozzle_")):
            continue
        if o["preset"] == "project" and k not in project_options:
            continue
        keys.append(k)
    return keys


def _listify(v):
    return list(v) if isinstance(v, list) else [v]


def _fit(vals, n):
    """Pad (repeat the first value) or cut to n."""
    vals = list(vals)
    if not vals:
        return vals
    while len(vals) < n:
        vals.append(vals[0])
    return vals[:n]


class Result:
    pass


def build_config(job, plates=1, max_height=0.0, wipe_xy=None, flush=None):
    """Return a Result with .config (sorted dict), .diff (lists per preset) and .notes."""
    ps = job.profiles
    by = ps.options
    n = len(job.filaments)
    keys = written_keys(by.values(), ps.project_options)
    pcfg, pmeta = ps.resolve("process", job.process)
    mcfg, mmeta = ps.resolve("machine", job.printer)
    fres = [ps.resolve("filament", f["preset"]) for f in job.filaments]
    # A user preset slot is compared with its parent system preset (Studio: cfg.diff(parent->config)), a system preset with itself.
    pbase = ps.resolve("process", pmeta["parent"])[0] if pmeta.get("parent") else pcfg
    fbase = [ps.resolve("filament", m["parent"])[0] if m.get("parent") else c for c, m in fres]

    def norm(k, v):
        return normalize(by[k], v)

    def default(k):
        d = by[k]["default"]
        return norm(k, d) if d is not None else None

    vp = len(mcfg.get("printer_extruder_variant", [])) or 1       # printer variants (P1S: 2)
    vf = []
    for i, (fc, _m) in enumerate(fres):
        vf.append(len(fc.get("filament_extruder_variant", [])) or vp)
        if vf[-1] != vp:
            raise ConfigError("filament %d (%s) has %d extruder variants, the printer has %d. Pick a filament made for this printer." % (
                i + 1, job.filaments[i]["preset"], vf[-1], vp))

    cfg = {}
    system = {}          # key -> system (preset) value, same shape as cfg[key]
    sys_fil = [dict() for _ in range(n)]   # per filament: key -> slice

    for k in keys:
        o = by[k]
        owner = o["preset"]
        if owner == "print":
            raw = pcfg.get(k)
            v = norm(k, raw) if raw is not None else default(k)
            if v is not None and o["vector"] and o["variant"] == "print":
                v = _fit(v, vp)
            if pbase is pcfg:
                sv = v
            else:
                sraw = pbase.get(k)
                sv = norm(k, sraw) if sraw is not None else default(k)
                if sv is not None and o["vector"] and o["variant"] == "print":
                    sv = _fit(sv, vp)
            system[k] = sv
            if k in job.settings:
                ov = job.settings[k]
                if o["vector"]:
                    v = [ov] * vp if o["variant"] == "print" else [ov]
                else:
                    v = ov
            cfg[k] = v
        elif owner == "printer":
            raw = mcfg.get(k)
            v = norm(k, raw) if raw is not None else default(k)
            system[k] = v
            cfg[k] = v
        elif owner == "filament":
            parts = []
            for i, (fc, _m) in enumerate(fres):
                raw = fc.get(k)
                fv = norm(k, raw) if raw is not None else default(k)
                braw = fbase[i].get(k)
                bv = norm(k, braw) if braw is not None else default(k)
                if o["vector"]:
                    if o["variant"] == "filament":
                        slot, bslot = _fit(fv, vf[i]), _fit(bv, vf[i])
                    elif k in FILAMENT_DEV_OPTIONS:
                        slot, bslot = list(fv), list(bv)
                    else:
                        slot = [fv[0]] if fv else [""]
                        bslot = [bv[0]] if bv else [""]
                    sys_fil[i][k] = list(bslot)
                    ov = job.filaments[i]["settings"].get(k)
                    if ov is not None:
                        if isinstance(ov, list):
                            slot = list(ov)
                        else:
                            slot = [ov] * vf[i] if o["variant"] == "filament" else [ov]
                    parts.extend(slot)
                else:
                    sys_fil[i][k] = bv
                    ov = job.filaments[i]["settings"].get(k)
                    parts = ov if ov is not None else (fv if i == 0 else parts)
            cfg[k] = parts
        else:
            cfg[k] = default(k)

    # ---- builder-set keys ----------------------------------------------------------------------
    cfg["print_settings_id"] = job.process
    cfg["printer_settings_id"] = job.printer
    cfg["filament_settings_id"] = [f["preset"] for f in job.filaments]
    cfg["filament_ids"] = [m["filament_id"] for _c, m in fres]
    cfg["filament_colour"] = [f["color"] for f in job.filaments]
    cfg["filament_multi_colour"] = [f["color"] for f in job.filaments]
    cfg["filament_self_index"] = [str(i + 1) for i in range(n) for _ in range(vf[i])]
    cfg["print_compatible_printers"] = norm("print_compatible_printers", pcfg.get("compatible_printers", [])) \
        if "print_compatible_printers" in by else pcfg.get("compatible_printers", [])
    cfg["extruder_ams_count"] = [AMS_NONE] * 2
    nvt = _listify(cfg.get("nozzle_volume_type") or ["Standard"])
    stats = ["%s#1" % t for t in nvt]
    cfg["extruder_nozzle_stats"] = stats
    cfg["extruder_nozzle_stats_new"] = list(stats)
    # 2.9.1 only (real 2.9.1.70 save, 4-color fixture): nozzle diameter per filament and physical slot per extruder
    if "filament_diameter_map" in keys:
        nd = _listify(cfg.get("nozzle_diameter") or ["0.4"])
        cfg["filament_diameter_map"] = [str(nd[0])] * n
    if "extruder_nozzle_slot" in keys:
        cfg["extruder_nozzle_slot"] = ["0"] * len(stats)
    cfg["curr_bed_type"] = job.plate_type or cfg.get("curr_bed_type") or default("curr_bed_type")
    for k in PER_FILAMENT_PROJECT:
        if k in cfg and k in keys:
            d = default(k)
            cfg[k] = [d[0] if d else ""] * n
    cfg["filament_map"] = ["1"] * n
    cfg["filament_nozzle_map"] = ["0"] * n          # fresh Studio 2.8.2 save (4-color fixture): 0, not 1
    cfg["filament_colour_type"] = ["0"] * n
    cfg["filament_volume_map"] = ["0"] * n
    # purge volumes (one matrix per nozzle; stage A printers have one)
    if flush is None:
        flush = _flush(job, cfg, by, fres)
    cfg["flush_volumes_matrix"] = flush["matrix"]
    cfg["flush_volumes_vector"] = flush["vector"]
    # wipe tower position
    if wipe_xy is None:
        from . import wipetower
        xs = [float(p.split("x")[0]) for p in cfg["printable_area"]]
        ys = [float(p.split("x")[1]) for p in cfg["printable_area"]]
        wipe_xy = wipetower.position(
            dict(cfg, layer_height=cfg["layer_height"], filament_prime_volume=cfg["filament_prime_volume"]),
            n, max_height, (min(xs), min(ys), max(xs), max(ys)))
    cfg["wipe_tower_x"] = [("%g" % wipe_xy[0])] * plates
    cfg["wipe_tower_y"] = [("%g" % wipe_xy[1])] * plates

    # ---- checks that need n ------------------------------------------------------------------------
    for k in ("support_filament", "support_interface_filament"):
        if k in job.settings and not 0 <= int(job.settings[k]) <= n:
            raise ConfigError("%s = %s: filament slots are 0 (auto) to %d" % (k, job.settings[k], n))

    # ---- diff lists ------------------------------------------------------------------------------
    diff_print = sorted(k for k in keys if by[k]["preset"] == "print" and k not in BUILDER_KEYS
                        and cfg[k] != system[k])
    diff_fil = []
    for i in range(n):
        lst = []
        for k, slot in sys_fil[i].items():
            if k in BUILDER_KEYS:
                continue
            o = by[k]
            if o["vector"]:
                if k in FILAMENT_DEV_OPTIONS:
                    cur = slot
                else:
                    start = sum(vf[:i]) if o["variant"] == "filament" else i
                    width = vf[i] if o["variant"] == "filament" else 1
                    cur = cfg[k][start:start + width]
            else:
                cur = cfg[k]
            if cur != slot:
                lst.append(k)
        diff_fil.append(sorted(lst))
    diff_printer = sorted(k for k in keys if by[k]["preset"] == "printer" and k not in BUILDER_KEYS
                          and cfg[k] != system[k])
    diffs = [";".join(diff_print)] + [";".join(d) for d in diff_fil] + [";".join(diff_printer)]
    cfg["different_settings_to_system"] = diffs
    # Studio writes Preset::inherits() of each edited preset (PresetBundle.cpp 2.8.2 :3531-3545, 2.9.1 :5043-5057): a user preset names its
    # parent system preset; a system preset has no "inherits" once loaded, so its entry is empty even when something differs (real saves with
    # a diff list and no inherits_group; patch mode writes it the same way).
    cfg["inherits_group"] = [pmeta["parent"] if pmeta.get("parent") else ""] + \
                            [fres[i][1]["parent"] if fres[i][1].get("parent") else "" for i in range(n)] + [""]

    # Studio writes each list only if one entry is not empty; a real save with unmodified presets omits both (4-color fixture)
    if not any(diffs):
        del cfg["different_settings_to_system"]
    if not any(cfg["inherits_group"]):
        del cfg["inherits_group"]
    cfg["version"] = job.profiles.tag_version
    cfg["name"] = "project_settings"
    cfg["from"] = "project"
    res = Result()
    res.config = {k: cfg[k] for k in sorted(cfg)}
    res.diffs = diffs
    res.n = n
    res.vp = vp
    res.vf = vf
    return res


def _flush(job, cfg, by, fres):
    import os
    from . import flush as fl
    from .profiles import studio_root
    n = len(job.filaments)
    mode = getattr(job, "purge", "auto")
    if n == 1:
        return {"matrix": ["0"], "vector": ["140", "140"]}
    tables = fl.Tables(os.path.join(studio_root(), job.studio, "flush"))
    # filament_is_support and the retraction keys are per filament; pass the slices the matrix code reads
    view = dict(cfg)
    ds = int(cfg.get("nozzle_flush_dataset", ["0"])[0]) if cfg.get("nozzle_flush_dataset") else 0
    res = fl.build_matrix(view, tables, mode="auto" if mode == "manual" else mode, nozzle_flush_dataset=ds)
    if mode == "manual":
        fl.apply_manual(res, getattr(job, "purge_manual", None) or {}, n)
    return res


def dumps(config):
    return json.dumps(config, indent=4, ensure_ascii=False) + "\n"
