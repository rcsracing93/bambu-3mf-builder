"""Calibration ledger: measured values for each spool, applied to a [[filament]] with spool = "name".

The ledger is a TOML file with one [[spool]] table per spool (see references/job-format.md, "Calibration ledger").
Ledger values become filament settings in the job, so they are written and listed as changed settings exactly like
[filament.settings]. A value in [filament.settings] wins over the ledger value for the same key.
"""
import datetime
import difflib
import os

try:
    import tomllib
except ImportError:                      # Python 3.8-3.10: vendored tomli
    from ._vendor import tomli as tomllib

ENV_VAR = "BAMBU_CALIBRATION_LEDGER"
LEDGER_FILE = "calibration.toml"
SPOOL_KEYS = ("name", "preset", "flow", "pa", "max_volumetric_speed", "nozzle_temp", "nozzle_temp_first_layer",
              "bed_temp", "bed_temp_first_layer", "date", "source")
NUMBER_KEYS = ("flow", "pa", "max_volumetric_speed", "nozzle_temp", "nozzle_temp_first_layer", "bed_temp", "bed_temp_first_layer")
# ledger field -> Studio filament key. bed_temp and bed_temp_first_layer depend on the job's plate_type.
FIELD_KEY = {"flow": "filament_flow_ratio", "pa": "pressure_advance", "max_volumetric_speed": "filament_max_volumetric_speed",
             "nozzle_temp": "nozzle_temperature", "nozzle_temp_first_layer": "nozzle_temperature_initial_layer"}


class LedgerError(Exception):
    pass


def _close(word, words):
    return difflib.get_close_matches(str(word), sorted(words), n=4, cutoff=0.4)


def find_ledger(job_dir, key_value):
    """Return (path, how) or (None, tried). Order: the job key `ledger`, calibration.toml next to the job, the environment variable."""
    tried = []
    if key_value is not None:
        if not isinstance(key_value, str) or not key_value.strip():
            raise LedgerError("ledger must be a file name or path (a string)")
        p = key_value if os.path.isabs(key_value) else os.path.join(job_dir, key_value)
        if not os.path.isfile(p):
            raise LedgerError("the ledger file %s (job key ledger) does not exist" % os.path.basename(p))
        return os.path.abspath(p), "job key ledger"
    tried.append("a job key ledger = \"path\"")
    p = os.path.join(job_dir, LEDGER_FILE)
    if os.path.isfile(p):
        return os.path.abspath(p), "next to the job file"
    tried.append("%s next to the job file" % LEDGER_FILE)
    env = os.environ.get(ENV_VAR)
    if env:
        if not os.path.isfile(env):
            raise LedgerError("the ledger file %s (environment variable %s) does not exist" % (os.path.basename(env), ENV_VAR))
        return os.path.abspath(env), "environment variable " + ENV_VAR
    tried.append("the environment variable " + ENV_VAR)
    return None, tried


def load_ledger(path):
    """Read and check the ledger file. Returns {name: spool dict}. Raises LedgerError naming the file."""
    fname = os.path.basename(path)
    try:
        with open(path, "rb") as fh:
            raw = tomllib.loads(fh.read().decode("utf-8"))
    except Exception as e:
        raise LedgerError("%s could not be read: %s" % (fname, e))
    probs = []
    for k in raw:
        if k != "spool":
            probs.append("%s: unknown key %r. The file holds [[spool]] tables only." % (fname, k))
    spools = raw.get("spool", [])
    if not isinstance(spools, list) or not all(isinstance(s, dict) for s in spools):
        raise LedgerError("%s: write each spool as a [[spool]] table" % fname)
    out = {}
    for i, s in enumerate(spools, 1):
        name = s.get("name")
        w = "%s: [[spool]] #%d" % (fname, i)
        if not isinstance(name, str) or not name.strip():
            probs.append("%s: name is required (a unique text)" % w)
            continue
        w = "%s: spool %r" % (fname, name)
        if name in out:
            probs.append("%s: the name is used twice. Each spool needs a unique name." % w)
            continue
        for k in s:
            if k not in SPOOL_KEYS:
                near = _close(k, SPOOL_KEYS)
                probs.append("%s: unknown key %r.%s" % (w, k, (" Closest: " + ", ".join(near)) if near else ""))
        if not isinstance(s.get("preset"), str) or not s["preset"].strip():
            probs.append("%s: preset is required (the Studio filament preset the values were measured with)" % w)
        for k in NUMBER_KEYS:
            v = s.get(k)
            if k in s and (isinstance(v, bool) or not isinstance(v, (int, float))):
                probs.append("%s: %s = %r is not a number" % (w, k, v))
        if "date" in s:
            d = s["date"]
            if isinstance(d, datetime.datetime):
                d = d.date()
            try:
                if isinstance(d, str):
                    if len(d) != 10:
                        raise ValueError
                    d = datetime.date.fromisoformat(d)
                if not isinstance(d, datetime.date):
                    raise ValueError
                s["date"] = d.isoformat()
            except ValueError:
                probs.append("%s: date = %r is not a date. Use YYYY-MM-DD." % (w, s["date"]))
        if "source" in s and not isinstance(s["source"], str):
            probs.append("%s: source must be text" % w)
        out[name] = s
    if probs:
        raise LedgerError("\n".join(probs))
    return out


class Ledger:
    def __init__(self, path, how, spools):
        self.path, self.how, self.spools = path, how, spools
        self.name = os.path.basename(path)


def open_for_job(raw, job_dir, filaments, chk):
    """The ledger of a job, or None when no [[filament]] uses `spool`. Errors go to chk."""
    if not isinstance(filaments, list):
        return None
    uses = [f for f in filaments if isinstance(f, dict) and "spool" in f]
    if not uses:
        return None
    try:
        path, how = find_ledger(job_dir, raw.get("ledger"))
        if path is None:
            chk.err("[[filament]].spool", "no calibration ledger found. Looked for: %s. Add ledger = \"calibration.toml\" to the job, put %s next to the job file, or set %s." % (
                "; ".join(how), LEDGER_FILE, ENV_VAR))
            return None
        return Ledger(path, how, load_ledger(path))
    except LedgerError as e:
        for line in str(e).split("\n"):
            chk.err("ledger", line)
        return None


def pick(led, f, w, chk):
    """The spool a [[filament]] names, or None (error reported). Also checks that the presets agree."""
    name = f.get("spool")
    if name is None:
        return None
    if not isinstance(name, str):
        chk.err(w + ".spool", "spool must be a text, the name of a spool in the ledger")
        return None
    if led is None:
        return None                                   # the missing ledger is reported already
    sp = led.spools.get(name)
    if sp is None:
        near = _close(name, led.spools)
        chk.err(w + ".spool", "%s has no spool %r.%s" % (led.name, name, (" Closest names: " + ", ".join(near)) if near else
                (" Spools in the file: " + (", ".join(sorted(led.spools)) or "none"))))
        return None
    if f.get("preset") and f["preset"] != sp["preset"]:
        chk.err(w + ".preset", "the slot uses %r, but spool %r in %s was measured with %r. Use the same preset, or remove preset from the slot." % (
            f["preset"], name, led.name, sp["preset"]))
        return None
    return sp


def apply(led, sp, entry, by, ver, plate_type, w, chk):
    """Add the ledger values of spool sp to entry["settings"] (canonical text, like [filament.settings]). Job settings win."""
    if sp is None:
        return
    from .job import coerce, PLATE_KEY
    job_set = dict(entry["settings"])
    todo = []                                         # (field, key, value)
    for field in ("flow", "pa", "max_volumetric_speed", "nozzle_temp", "nozzle_temp_first_layer", "bed_temp", "bed_temp_first_layer"):
        if field not in sp:
            continue
        if field in ("bed_temp", "bed_temp_first_layer"):
            if plate_type not in PLATE_KEY:
                chk.err(w + ".spool", "spool %r in %s has %s. That needs plate_type in the job (it is the bed temperature of that plate). Set plate_type, or remove %s from the spool." % (
                    sp["name"], led.name, field, field))
                continue
            key = PLATE_KEY[plate_type] + ("_initial_layer" if field.endswith("first_layer") else "")
        else:
            key = FIELD_KEY[field]
        todo.append((field, key, sp[field]))
    if "pa" in sp:
        todo.append(("pa", "enable_pressure_advance", True))
    applied = []
    for field, key, val in todo:
        where = "spool %r in %s: %s" % (sp["name"], led.name, field)
        if key in job_set:
            chk.warn(w + ".settings." + key, "%s is set in the job and in spool %r of %s. The job value %s is used." % (
                key, sp["name"], led.name, job_set[key]))
            continue
        opt = by.get(key)
        if opt is None:
            chk.err(where, "Studio %s has no setting %r" % (ver, key))
            continue
        c = coerce(opt, val, where, chk)
        if c is None:
            continue
        entry["settings"][key] = c
        applied.append({"field": field, "key": key, "value": c})
    entry["spool"] = {"name": sp["name"], "ledger": led.name, "date": sp.get("date"), "source": sp.get("source"), "applied": applied}


def summary_lines(f):
    """Markdown lines for a filament slot that uses a spool (empty when it does not)."""
    sp = f.get("spool")
    if not sp:
        return []
    L = ["    - spool `%s` from ledger `%s`%s%s" % (sp["name"], sp["ledger"], (", measured %s" % sp["date"]) if sp.get("date") else "",
                                                (", source: %s" % sp["source"]) if sp.get("source") else "")]
    for a in sp["applied"]:
        L.append("        - ledger value `%s` = `%s`" % (a["key"], a["value"]))
    return L
