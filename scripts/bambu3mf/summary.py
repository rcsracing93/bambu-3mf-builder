"""Build summary (3f): what was built, in plain text, with the purge table in grams."""
import os


def _f(x):
    return ("%.1f" % x).rstrip("0").rstrip(".") if x != int(x) else "%d" % x


def purge_table(cfg, n):
    m = [float(v) for v in cfg.get("flush_volumes_matrix", [])]
    if n < 2 or len(m) != n * n:
        return None
    mult = float((cfg.get("flush_multiplier") or ["1"])[0])
    dens = [float(d) for d in cfg.get("filament_density", ["1.24"] * n)]
    grams = [[m[i * n + j] * mult * dens[j] / 1000.0 for j in range(n)] for i in range(n)]
    return grams


def format_summary(job, s):
    cfg = s.config
    n = s.result.n
    L = []
    L.append("Built %s" % os.path.abspath(s.path))
    L.append("  Bambu Studio %s | %s | %s" % (job.studio, job.printer, job.process))
    L.append("  Plate type: %s" % (cfg.get("curr_bed_type") or "default"))
    L.append("  Walls %s, infill %s %s, layer %s mm" % (cfg.get("wall_loops"), cfg.get("sparse_infill_density"), {"zig-zag": "rectilinear", "alignedrectilinear": "aligned rectilinear"}.get(cfg.get("sparse_infill_pattern"), cfg.get("sparse_infill_pattern")), cfg.get("layer_height")))
    L.append("")
    L.append("Filaments")
    for i, f in enumerate(job.filaments):
        nt = cfg["nozzle_temperature"]
        vf = s.result.vf
        start = sum(vf[:i])
        L.append("  %d  %s  %s  nozzle %s C  flow %s" % (i + 1, f["color"], f["preset"], nt[start], cfg["filament_flow_ratio"][start]))
    L.append("")
    L.append("Plates")
    for k in range(1, s.plates + 1):
        for ob in s.objects:
            if ob.src["plate"] == k:
                size = "%s x %s x %s mm" % (_f(round(ob.w, 2)), _f(round(ob.d, 2)), _f(round(ob.h, 2)))
                parts = ", ".join("%s (filament %d)" % (p.name, p.filament) for p in ob.parts)
                L.append("  plate %d: %s x%d, %s; %s" % (k, ob.name, len(ob.inst), size, parts))
    if getattr(job, "calibration", None):
        t = job.calibration["temps"]
        L.append("")
        L.append("Calibration tower: %d bands of 8 mm, %d C at the bottom down to %d C at the top" % (len(t), t[0], t[-1]))
    if job.layer_gcode:
        L.append("")
        L.append("Layer G-code (%d steps)" % len(job.layer_gcode))
        for g in sorted(job.layer_gcode, key=lambda x: (x["plate"], x["z"])):
            L.append("  plate %d  z %s  %s %s" % (g["plate"], _f(g["z"]), g["kind"], g["value"]))
    t = purge_table(cfg, n)
    if t:
        L.append("")
        L.append("Purge per color change, grams (from filament in the row to filament in the column; approximate)")
        head = "      " + "".join("%8d" % (j + 1) for j in range(n))
        L.append(head)
        for i in range(n):
            L.append("  %3d " % (i + 1) + "".join(("%8s" % ("-" if i == j else "%.2f" % t[i][j])) for j in range(n)))
        L.append("  Wipe tower: x %s, y %s (plate 1)" % (cfg["wipe_tower_x"][0], cfg["wipe_tower_y"][0]))
        mx = max(t[i][j] for i in range(n) for j in range(n) if i != j)
        L.append("  Largest single purge %.2f g. Mode: %s" % (mx, job.purge))
    for w in s.warnings:
        L.append("")
        L.append("warning: " + w)
    return "\n".join(L) + "\n"


# ---- summary file (<name>.summary.md) ---------------------------------------------------------------------------------
def _studio_text(v):
    """2.8.2.61 -> "2.8.2 (build 61)": four numbers in a row look like an IP address to the privacy scan."""
    p = str(v).split(".")
    return "%s (build %s)" % (".".join(p[:3]), p[3]) if len(p) == 4 else str(v)


def summary_path(path_3mf):
    """<output name without .3mf>.summary.md next to the 3MF."""
    base = path_3mf[:-4] if path_3mf.lower().endswith(".3mf") else path_3mf
    return base + ".summary.md"


def _val(v):
    return ("true" if v else "false") if isinstance(v, bool) else v if isinstance(v, str) else repr(v)


def format_summary_md(job, s, slice_info=None):
    """Plain Markdown record of a build. Only file names, never full paths. slice_info: None, or (ok, message, plates)."""
    from .job import PLATE_KEY
    cfg = s.config
    n = s.result.n
    vf = s.result.vf
    out = os.path.basename(s.path)
    bed_key = PLATE_KEY.get(cfg.get("curr_bed_type") or "")
    L = ["# Build summary: %s" % out, ""]
    L += ["- Printer: %s" % job.printer, "- Process: %s" % job.process,
          "- Plate type: %s" % (cfg.get("curr_bed_type") or "default"), "- Bambu Studio: %s" % _studio_text(job.studio),
          "- Job file: %s" % os.path.basename(job.path), ""]
    if job.settings:
        L += ["## Job settings (overrides of the process preset)", ""]
        L += ["- `%s` = `%s`" % (k, _val(job.settings[k])) for k in sorted(job.settings)]
        L.append("")
    L += ["## Filament slots", ""]
    for i, f in enumerate(job.filaments):
        start = sum(vf[:i])
        nz = cfg["nozzle_temperature"][start]
        bed = (cfg.get(bed_key) or [None] * (i + 1))[i] if bed_key else None
        pre = cfg["filament_settings_id"][i] if cfg.get("filament_settings_id") else f["preset"]
        col = cfg["filament_colour"][i] if cfg.get("filament_colour") else f["color"]
        L.append("%d. %s, color %s, nozzle %s C, bed %s" % (i + 1, pre, col, nz, ("%s C" % bed) if bed is not None else "n/a"))
        for k in sorted(f["settings"]):
            L.append("    - override `%s` = `%s`" % (k, _val(f["settings"][k])))
    L += ["", "## Objects and parts", ""]
    for ob in s.objects:
        src = ob.src
        L.append("- %s: plate %d, %d cop%s, %s x %s x %s mm" % (ob.name, src["plate"], len(ob.inst), "y" if len(ob.inst) == 1 else "ies",
                 _f(round(ob.w, 2)), _f(round(ob.d, 2)), _f(round(ob.h, 2))))
        for k in sorted(src["settings"]):
            L.append("    - object override `%s` = `%s`" % (k, _val(src["settings"][k])))
        for p in ob.parts:
            L.append("    - part %s: filament %d, type %s" % (p.name, p.filament, p.src["type"]))
            for k in sorted(p.src["settings"]):
                L.append("        - override `%s` = `%s`" % (k, _val(p.src["settings"][k])))
    L.append("")
    if getattr(job, "calibration", None):
        t = job.calibration["temps"]
        L += ["## Calibration tower", "", "- %d bands of 8 mm: %s C, bottom to top (the first band's temperature is also the first-layer temperature)" % (len(t), ", ".join(str(x) for x in t)), ""]
    L += ["## Layer G-code", ""]
    if job.layer_gcode:
        for g in sorted(job.layer_gcode, key=lambda x: (x["plate"], x["z"])):
            what = {"pause": "pause", "temperature": "nozzle temperature %s C" % g["value"], "gcode": "custom G-code: %s" % repr(g["value"])}[g["kind"]]
            L.append("- plate %d, z %s mm: %s" % (g["plate"], _f(g["z"]), what))
    else:
        L.append("None.")
    L += ["", "## Warnings", ""]
    L += ["- " + w for w in s.warnings] if s.warnings else ["None."]
    if slice_info is not None:
        ok, msg, plates = slice_info
        L += ["", "## Studio slice check", "", "- Result: %s" % msg]
        for pl in plates or []:
            L.append("- plate %d: print time %s, filament %s g" % (pl["plate"], pl.get("time") or "n/a",
                     ("%.2f" % pl["grams"]) if pl.get("grams") is not None else "n/a"))
    return "\n".join(L) + "\n"


def write_summary_file(job, s, slice_info=None):
    p = summary_path(s.path)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(format_summary_md(job, s, slice_info))
    return p
