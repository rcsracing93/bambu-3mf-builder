#!/usr/bin/env python3
"""Self-test for the options table: grade a real Studio project_settings.config
against bambu_options_<tag>.json. Prints PASS/FAIL lines and a summary.

Usage: check_options_vs_project.py <options.json> <project.3mf> [label]
"""
import json
import sys
import zipfile

HEADER = {"version", "name", "from"}


def main(opt_path, mf_path, label=""):
    table = json.load(open(opt_path))
    opts = {o["key"]: o for o in table["options"]}
    with zipfile.ZipFile(mf_path) as z:
        cfg = json.loads(z.read("Metadata/project_settings.config"))
    n = len(cfg["filament_colour"])
    v_f = len(cfg.get("filament_extruder_variant", [])) // max(n, 1)
    v_p = len(cfg.get("printer_extruder_variant", ["x"]))
    v_pr = len(cfg.get("print_extruder_variant", ["x"]))
    fails, warns = [], []

    unknown = sorted(k for k in cfg if k not in HEADER and k not in opts)
    for k in unknown:
        fails.append("unknown key in project: %s" % k)

    for k, val in cfg.items():
        o = opts.get(k)
        if not o:
            continue
        if o["vector"] != isinstance(val, list):
            fails.append("%s: table says vector=%s, project has %s" % (k, o["vector"], type(val).__name__))
            continue
        if isinstance(val, list):
            ln = len(val)
            if o["variant"] == "filament":
                if ln != n * v_f:
                    fails.append("%s: filament-variant length %d, expected n*V=%d" % (k, ln, n * v_f))
            elif o["variant"] == "print":
                if ln != v_pr:
                    fails.append("%s: print-variant length %d, expected %d" % (k, ln, v_pr))
            elif o["variant"] == "printer":
                exp = v_p * (o["variant_stride"] or 1)
                if ln != exp and not (o["variant_stride"] == 2 and ln == 2 * v_p):
                    # machine limits with stride 2 carry normal+silent per variant
                    if ln != exp:
                        warns.append("%s: printer-variant length %d, expected %d" % (k, ln, exp))
            elif o["preset"] == "filament" and not o.get("dev_option") and k not in ("compatible_prints", "compatible_printers"):
                if ln != n:
                    warns.append("%s: per-filament length %d, n=%d" % (k, ln, n))
        # enum check
        if o.get("enum_keys") and isinstance(val, str) and val not in o["enum_keys"]:
            fails.append("%s: value %r not in enum %s" % (k, val, o["enum_keys"]))
        # range check (scalars and vectors, numeric only)
        vals = val if isinstance(val, list) else [val]
        for x in vals:
            if not isinstance(x, str) or x == "nil":
                continue
            try:
                num = float(x.rstrip("%"))
            except ValueError:
                continue
            if o.get("min") is not None and num < o["min"] and not (o["type"].startswith("coFloatOrPercent") or o["type"].startswith("coFloatsOrPercents")):
                warns.append("%s: %s below min %s" % (k, x, o["min"]))
            if o.get("max") is not None and num > o["max"]:
                warns.append("%s: %s above max %s" % (k, x, o["max"]))

    # diff lists name keys of the right preset
    diff = cfg.get("different_settings_to_system", [])
    owners = ["print"] + ["filament"] * n + ["printer"]
    for i, entry in enumerate(diff):
        for k in [x for x in entry.split(";") if x]:
            o = opts.get(k)
            if o is None:
                fails.append("different_settings_to_system[%d] names unknown key %s" % (i, k))
            elif o["preset"] != owners[i] and owners[i] not in (o.get("also_in") or []):
                fails.append("different_settings_to_system[%d] (%s) lists %s, table owner %s" % (i, owners[i], k, o["preset"]))

    print("== %s  (%s, n=%d, V_f=%d, V_print=%d, keys=%d)" % (label or mf_path, table["studio_tag"], n, v_f, v_pr, len(cfg)))
    for f in fails:
        print("FAIL", f)
    for w in warns:
        print("WARN", w)
    print("RESULT %s: %d fail, %d warn" % ("PASS" if not fails else "FAIL", len(fails), len(warns)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
