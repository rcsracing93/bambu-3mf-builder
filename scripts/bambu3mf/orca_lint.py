"""--orca-lint: list the keys of a job that OrcaSlicer drops or changes when it opens the Bambu Studio 3MF.

Read-only. It loads the job, compares each key the job sets with the facts in data/orca_keys.json, prints the result and writes nothing.
The facts come from the OrcaSlicer source (see tests/gen_orca_keys.py). Orca applies PrintConfigDef::handle_legacy to every key of a 3MF
and ignores the key if the result is empty or is not an option of its table, so there are four cases:
  dropped    Orca has no such option, or handle_legacy clears the key
  renamed    handle_legacy gives the key a new name (the value is kept, or changed as the line says)
  value      handle_legacy changes the value
  rejected   the key exists in Orca, but Orca has no such enum value (Orca uses its default)
"""
import json
import os

from . import job as _job

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "orca_keys.json")
SRC = "src/libslic3r/PrintConfig.cpp"


def load_data(path=DATA):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _cite(d, line):
    return "OrcaSlicer %s %s line %d" % (d["orca_tag"], SRC, line)


def _values(v):
    return [str(x) for x in v] if isinstance(v, (list, tuple)) else [str(v)]


def check_key(d, key, value):
    """Findings for one key and its value: a list of (kind, text). An empty list means Orca keeps the key and the value."""
    out = []
    cur = key
    vals = _values(value)
    ren = d["renamed"].get(key)
    if ren:
        cond = ren.get("when_value")
        applies = True
        if cond:
            applies = all(x in cond["is"] for x in vals) if cond["is"] else True
            if cond["is_not"] and any(x in cond["is_not"] for x in vals):
                applies = False
        if applies:
            text = "Orca renames it to %s (%s)" % (ren["to"], _cite(d, ren["line"]))
            for m in ren.get("values", []):
                if any(x in m["from"] for x in vals):
                    text += "; the value %s becomes %s" % ("/".join(m["from"]), m["to"])
            if ren.get("note"):
                text += "; " + ren["note"]
            if not ren["to_defined"]:
                out.append(("dropped", "Orca renames it to %s, which Orca does not define: the key is dropped (%s)" % (
                    ren["to"], _cite(d, ren["line"]))))
            else:
                out.append(("renamed", text))
            return out
    clr = d["cleared"].get(cur)
    if clr:
        if clr["when"] == "always" or (clr["when"] == "the value is a percentage" and any("%" in x for x in vals)):
            out.append(("dropped", "Orca clears it (%s) when it loads: %s (%s)" % (
                "always" if clr["when"] == "always" else clr["when"], clr["how"], _cite(d, clr["line"]))))
            return out
    if cur not in d["keys"]:
        out.append(("dropped", "not defined in Orca PrintConfig.cpp %s; Orca drops it (%s; %s)" % (
            d["orca_tag"], _cite(d, d["table_rule"]["line"]),
            "src/libslic3r/Config.cpp line %d" % d["load_rule"]["line"])))
        return out
    mapped = set()
    for m in d["value_maps"]:
        if m["key"] == cur:
            for x in vals:
                if x in m["from"]:
                    mapped.add(x)
                    out.append(("value", "Orca changes the value %s to %s (%s)" % (x, m["to"], _cite(d, m["line"]))))
    er = d["enum_reject"].get(cur)
    if er:
        bad = [x for x in vals if x in er["rejected"] and x not in mapped]
        for x in bad:
            out.append(("rejected", "Orca has no value %s for this key and uses the default of the option (option at %s; rule at src/libslic3r/Config.cpp line %d)" % (
                x, _cite(d, er["line"]), d["enum_rule"]["line"])))
    return out


def collect(jb, raw_settings):
    """Every (where, key, value) the job sets. Builder defaults that the job did not write are left out."""
    items = []
    skip = {k for k, v in _job.DEFAULT_SETTINGS.items() if k not in raw_settings and jb.settings.get(k) == v}
    for k, v in sorted(jb.settings.items()):
        if k not in skip:
            items.append(("[settings]", k, v))
    for i, f in enumerate(jb.filaments, 1):
        for k, v in sorted(f["settings"].items()):
            items.append(("[[filament]] #%d" % i, k, v))
    for o in jb.objects:
        nm = "[[object]] %s" % o["name"]
        for k, v in sorted(o["settings"].items()):
            items.append((nm, k, v))
        for r in o.get("height_ranges") or []:
            for k, v in sorted(r["settings"].items()):
                items.append(("%s height range %s-%s mm" % (nm, r["min_z"], r["max_z"]), k, v))
        for j, p in enumerate(o["parts"], 1):
            for k, v in sorted(p["settings"].items()):
                items.append(("%s part #%d" % (nm, j), k, v))
    return items


def lint_lines(jb, raw_settings, data=None):
    """Returns (lines, measured, counts). counts maps a kind to a number."""
    d = data or load_data()
    items = collect(jb, raw_settings)
    lines, counts = [], {"dropped": 0, "renamed": 0, "value": 0, "rejected": 0}
    for where, key, value in items:
        for kind, text in check_key(d, key, value):
            counts[kind] += 1
            lines.append("%s %s = %s: %s: %s" % (where, key, ",".join(_values(value)), kind, text))
    return lines, len(items), counts


def run(path, user_presets=None, strict_version=False, out=print, err=None):
    """Lint the job at path. Returns the exit code: 0 for a lint result, 1 if the job cannot be loaded."""
    import sys
    err = err or (lambda m: print(m, file=sys.stderr))
    try:
        jb = _job.load_job(path, user_presets=user_presets, strict_version=strict_version)
        raw = _job.read_raw(path)
    except _job.JobError as e:
        err("The job cannot be linted:")
        for p in e.problems:
            err("  - " + p)
        return 1
    except Exception as e:                           # noqa: BLE001
        err("The job cannot be linted: %s" % e)
        return 1
    d = load_data()
    raw_settings = raw.get("settings", {}) if isinstance(raw.get("settings", {}), dict) else {}
    lines, measured, counts = lint_lines(jb, raw_settings, d)
    for ln in lines:
        out(ln)
    total = sum(counts.values())
    if measured == 0:
        out("orca-lint: NOT CHECKED. The job sets no keys, so nothing was measured.")
    else:
        out("orca-lint: %d key(s) measured, %d finding(s): %d dropped, %d renamed, %d value changed, %d value rejected."
            % (measured, total, counts["dropped"], counts["renamed"], counts["value"], counts["rejected"]))
    out("This list is for OrcaSlicer %s (commit %s). It is read-only: no 3MF and no other file was written." % (
        d["orca_tag"], (d.get("orca_commit") or "unknown")[:10]))
    return 0
