"""Settings lint: warnings only, never blocks a build.

Every rule uses a preset or option field that exists in the Studio source (v02.08.02.61 and v02.09.01.70). The comment on a rule names the
field and where Studio uses it. No field, no rule. Rules that were considered and dropped:
  - bed temperature outside a range: no filament preset field gives a bed range (only the per-plate temperatures themselves). A bed
    temperature of 0 on the selected plate is not a lint: Studio refuses to slice ("Plate %d: %s does not support filament %s",
    Print.cpp Print::validate). The builder stops on it (job.py) and the validator reports it as an error (R39).
  - abrasive filament on a soft nozzle: the filament side exists (required_nozzle_HRC, PrintConfig.cpp), but the nozzle is not in any
    printer preset. Studio takes it from the connected printer (SelectMachine.cpp is_nozzle_hrc_matched, Print::get_hrc_by_nozzle_type with
    resources/info/nozzle_info.json); "nozzle_type" and "nozzle_hrc" are in the ignore list of PrintConfig.cpp. Nothing to compare against.
  - enclosure needed on an open printer: Studio's own warning (SelectMachine.cpp, "high temperature slicing") reads the connected printer's
    chamber and door, not a preset. The preset-side form is rule 4 below.
"""


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _slot(cfg, key, i, n, vf):
    """The value of a per-filament list for slot i (0-based). Lists have one entry per slot or one per slot and nozzle variant."""
    lst = cfg.get(key)
    if not isinstance(lst, list) or not lst:
        return None
    if len(lst) == sum(vf):
        return lst[sum(vf[:i])]
    if len(lst) == n:
        return lst[i]
    return lst[0]


def lint(job, cfg, objs, res):
    """Returns a list of warning texts. job: loaded job; cfg: built project config; objs: objects with parts and meshes; res: build_config result."""
    out = []
    n, vf = res.n, res.vf

    # Rule 1. Nozzle temperature outside the filament's recommended range.
    # Fields: nozzle_temperature, nozzle_temperature_initial_layer against nozzle_temperature_range_low / _high. Studio shows the same
    # warning ("Nozzle may be blocked when the temperature is out of recommended range", ConfigManipulation.cpp
    # check_nozzle_temperature_range and check_nozzle_temperature_initial_layer_range).
    for i in range(n):
        lo = _num(_slot(cfg, "nozzle_temperature_range_low", i, n, vf))
        hi = _num(_slot(cfg, "nozzle_temperature_range_high", i, n, vf))
        if lo is None or hi is None:
            continue
        for key, label in (("nozzle_temperature", "nozzle temperature"), ("nozzle_temperature_initial_layer", "first layer nozzle temperature")):
            t = _num(_slot(cfg, key, i, n, vf))
            if t is not None and (t < lo or t > hi):
                out.append("lint: filament %d (%s): %s %g C is outside the recommended range %g to %g C of this filament "
                           "(nozzle_temperature_range_low / _high). Studio warns that the nozzle may block." % (
                               i + 1, job.filaments[i]["preset"], label, t, lo, hi))

    # Rule 4. Chamber temperature asked of a printer that cannot control the chamber.
    # Fields: filament chamber_temperatures (Studio treats 40 C and above as "high temperature slicing", SelectMachine.cpp) and printer
    # support_chamber_temp_control (PrintConfig.cpp; Tab.cpp shows chamber_temperatures only when it is 1).
    if str(cfg.get("support_chamber_temp_control", "0")) in ("0", "false"):
        for i in range(n):
            c = _num(_slot(cfg, "chamber_temperatures", i, n, vf))
            if c is not None and c >= 40:
                out.append("lint: filament %d (%s): chamber temperature %g C is asked, but the printer preset has no chamber temperature control "
                           "(support_chamber_temp_control = 0). The material needs a warm enclosed chamber; the printer will not heat it." % (
                               i + 1, job.filaments[i]["preset"], c))

    # Rule 5. Inlay thinner than one layer. Geometry and layer_height only (no preset field needed).
    # A normal part of another filament than the tallest normal part of its object, lower than layer_height (object or part override,
    # else the process value), cannot be printed as its own layer. (Two layer G-code steps closer than one layer cannot occur: the
    # builder refuses a z that is not a layer top.)
    base_lh = _num(cfg.get("layer_height"))
    for ob in objs:
        lh = _num(ob.src["settings"].get("layer_height")) or base_lh
        normal = []
        for p in ob.parts:
            if p.subtype == "normal_part":
                lo, hi = p.mesh.bbox()
                normal.append((hi[2] - lo[2], p))
        if len(normal) < 2:
            continue
        base = max(normal, key=lambda t: t[0])[1]
        for h, p in normal:
            plh = _num(p.src["settings"].get("layer_height")) or lh
            if p.filament != base.filament and plh and h < plh - 1e-9:
                out.append("lint: object %s, part %s (filament %d) is %.3g mm high, less than one layer (%g mm). It will not print as its own "
                           "colour. Make it at least one layer high." % (ob.name, p.name, p.filament, h, plh))
    return out
