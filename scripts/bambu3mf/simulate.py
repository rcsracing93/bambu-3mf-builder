"""Load simulator. Applies Studio's GUI load rules (Preset.cpp load_external_preset and the PresetBundle load path) to a project_settings.config and reports
every value that would not survive the load. Python only; no Studio needed.

Rules applied:
  1.1  Application must be BambuStudio-<version>, else the whole project config is ignored.
  1.4  printer_model must be a Bambu printer, else the GUI drops the config.
  1.2  A key owned by a preset keeps its file value only if it is listed in that preset's different_settings_to_system
       entry; every other key is replaced by the system preset value. No lists at all = everything is the system value.
  1.3  The filament count is len(filament_colour); per-filament vectors are cut or padded to it.
  9    Project options missing from the file: 2.9.1 resets them to defaults, 2.8.2 keeps the previous value.
"""
from . import config as cfgmod
from . import rules_config


class Lost:
    def __init__(self, key, reason, intended, effective):
        self.key, self.reason, self.intended, self.effective = key, reason, intended, effective

    def __str__(self):
        return "%s: %s (file %s -> after load %s)" % (self.key, self.reason, _short(self.intended), _short(self.effective))


def _short(v):
    s = str(v)
    return s if len(s) <= 40 else s[:37] + "..."


def simulate(cfg, application, ps, nplates=1):
    """Returns (effective config or None, [Lost], [notes])."""
    notes = []
    if not (application or "").startswith("BambuStudio-"):
        return None, [Lost("*", "Application is %r, not BambuStudio-<version>: Studio ignores every setting in the file" % application, "", "")], notes
    if not str(cfg.get("printer_model", "")).startswith("Bambu Lab"):
        return None, [Lost("*", "printer_model %r is not a Bambu printer: the GUI drops the project config" % cfg.get("printer_model"), "", "")], notes
    fc = cfg.get("filament_colour")
    if not isinstance(fc, list) or not fc:
        return None, [Lost("*", "no filament_colour: the filament count is unknown", "", "")], notes
    n = len(fc)
    sysc, skipped, why = rules_config.system_config(cfg, ps, n, nplates)
    if sysc is None:
        notes.append("system values could not be rebuilt (%s); the diff-list check was skipped" % why)
        return dict(cfg), [], notes
    vp = len(cfg.get("printer_extruder_variant", [])) or 1
    dl = cfg.get("different_settings_to_system")
    listed = [set(filter(None, (s or "").split(";"))) for s in (dl if dl is not None and len(dl) == n + 2 else [""] * (n + 2))]
    skip = cfgmod.BUILDER_KEYS | {"version", "name", "from", "printer_model", "printer_variant", "printer_extruder_variant"}
    eff, lost = {}, []
    for k, v in cfg.items():
        o = ps.options.get(k)
        if o is None or k in skip or k not in sysc or o["preset"] not in ("print", "filament", "printer"):
            eff[k] = v
            continue
        sv = sysc[k]
        if (o["preset"] == "print" and 0 in skipped) or (o["preset"] == "printer" and n + 1 in skipped):
            eff[k] = v                                       # a preset with no parent: its values are not compared
            continue
        if o["preset"] == "print":
            keep = k in listed[0]
            eff[k] = v if keep else sv
        elif o["preset"] == "printer":
            keep = k in listed[n + 1]
            eff[k] = v if keep else sv
        else:
            if not isinstance(v, list) or not isinstance(sv, list) or k in cfgmod.FILAMENT_DEV_OPTIONS:
                eff[k] = v if any(k in listed[i] for i in range(1, n + 1)) or any(1 <= i <= n for i in skipped) else sv
            else:
                width = vp if o.get("variant") == "filament" else 1
                out = []
                for i in range(n):
                    a, b = i * width, (i + 1) * width
                    out.extend(v[a:b] if (k in listed[i + 1] or 1 + i in skipped) else sv[a:b])
                eff[k] = out
        if eff[k] != v:
            lost.append(Lost(k, "not listed in different_settings_to_system, so Studio resets it to the preset value", v, eff[k]))
    missing = [k for k in ps.project_options if k not in cfg]
    if missing:
        if ps.version.startswith("2.9"):
            lost.append(Lost(",".join(missing[:4]) + (" ..." if len(missing) > 4 else ""), "project options missing from the file: Studio 2.9.1 resets them to defaults (PresetBundle.cpp, 2.9.1 :5574)", "(absent)", "(default)"))
        else:
            notes.append("project options missing from the file (%s): Studio 2.8.2 keeps its previous values (PresetBundle.cpp project_config.apply_only, 2.8.2 :4020)" % ", ".join(missing[:4]))
    return eff, lost, notes
