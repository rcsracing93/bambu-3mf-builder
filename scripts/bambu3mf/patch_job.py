"""Patch job: reader and structural checks of a job that holds `patch = "file.3mf"`.

Nothing here needs the 3MF. load_patch_job() checks the shape of the job and returns a PatchJob. The checks that need the
file (slot numbers, preset names, setting names and values) run later, in patch.py, after the file's Studio version is known.
Patch mode never changes geometry: a mesh, a position or a copy count in the job is refused.
"""
import os

from .job import JobError, PLATE_KEY, _Check, _closest, read_raw

TOP_KEYS = {"patch", "output", "process", "plate_type", "reset_to_preset", "keep_changes", "printer", "settings", "filament",
            "object", "plate", "layer_gcode", "temperature_tower"}
NOT_IN_PATCH = {"studio": "the Studio version comes from the file", "title": "the title is kept as it is in the file",
                "purge": "the purge volumes follow Studio's own rule for the slots that change", "allow_large": "no mesh is read"}
FILAMENT_KEYS = {"slot", "preset", "color", "settings", "keep_changes", "remove"}
OBJECT_KEYS = {"name", "plate", "filament", "settings", "part"}
PART_KEYS = {"name", "filament", "settings"}
GEOMETRY_KEYS = {"mesh", "position", "copies", "instances", "units", "type"}
PLATE_JOB_KEYS = {"id", "plate_type"}
LAYER_KEYS = {"plate", "z", "temperature", "pause", "gcode"}
TOWER_KEYS = {"start", "step", "step_height", "first_z", "steps", "plate"}


class PatchRefused(JobError):
    """The patch cannot be made. Carries every reason found."""


class PatchJob:
    pass


def is_patch_job(path):
    try:
        raw = read_raw(path)
    except Exception:                                   # noqa: BLE001 - load_job reports unreadable files
        return False
    return isinstance(raw, dict) and "patch" in raw


def _int(v, where, chk, low=1):
    if isinstance(v, bool) or not isinstance(v, int) or v < low:
        chk.err(where, "must be a whole number, %d or more" % low)
        return None
    return v


def _unknown(table, allowed, where, chk, geometry=True):
    for k in table:
        if k in allowed:
            continue
        if geometry and k in GEOMETRY_KEYS:
            chk.err("%s.%s" % (where, k), "patch mode never changes geometry. Remove it (mesh, position, copies and part type are fixed by the file).")
        else:
            near = _closest(k, allowed)
            chk.err("%s.%s" % (where, k), "unknown key.%s" % ((" Closest: " + ", ".join(near)) if near else ""))


def load_patch_job(path):
    chk = _Check()
    base = os.path.dirname(os.path.abspath(path))
    try:
        raw = read_raw(path)
    except Exception as e:                              # noqa: BLE001
        raise PatchRefused(["%s could not be read: %s" % (os.path.basename(path), e)])
    if not isinstance(raw, dict):
        raise PatchRefused(["The job file must hold a table of settings"])
    for k in raw:
        if k in NOT_IN_PATCH:
            chk.err(k, "not used in patch mode (%s). Remove it." % NOT_IN_PATCH[k])
        elif k not in TOP_KEYS:
            near = _closest(k, TOP_KEYS)
            chk.err(k, "unknown job key.%s" % ((" Closest: " + ", ".join(near)) if near else ""))
    job = PatchJob()
    job.path = os.path.abspath(path)
    pf = raw.get("patch")
    job.patch = None
    if not isinstance(pf, str) or not pf.strip():
        chk.err("patch", "must be the file name of the 3MF to patch")
    else:
        full = pf if os.path.isabs(pf) else os.path.normpath(os.path.join(base, pf))
        if not os.path.isfile(full):
            chk.err("patch", "file not found: %s" % full)
        job.patch = full
    out = raw.get("output") or os.path.splitext(os.path.basename(path))[0] + ".3mf"
    job.output = str(out)
    job.printer = raw.get("printer")
    if job.printer is not None and (not isinstance(job.printer, str) or not job.printer.strip()):
        chk.err("printer", "must be the exact name of a Bambu system printer preset, e.g. \"Bambu Lab P1S 0.4 nozzle\"")
        job.printer = None
    job.process = raw.get("process")
    if job.process is not None and (not isinstance(job.process, str) or not job.process):
        chk.err("process", "must be a process preset name")
    job.plate_type = raw.get("plate_type")
    if job.plate_type is not None and not isinstance(job.plate_type, str):
        chk.err("plate_type", "must be a plate type name")
    for k in ("reset_to_preset", "keep_changes"):
        v = raw.get(k, False)
        if not isinstance(v, bool):
            chk.err(k, "must be true or false")
            v = False
        setattr(job, k.replace("reset_to_preset", "reset"), v)
    if job.keep_changes and not job.process and not job.printer:
        chk.err("keep_changes", "at the top of the job it belongs to a process change. Set process (or printer, which can change the process), or put keep_changes in the [[filament]] table of the slot.")
    job.settings = raw.get("settings", {}) or {}
    if not isinstance(job.settings, dict):
        chk.err("[settings]", "must be a table of Studio setting names")
        job.settings = {}

    # ---- filaments ---------------------------------------------------------------------------
    job.filaments = []
    fl = raw.get("filament", []) or []
    if not isinstance(fl, list):
        chk.err("filament", "must be written as [[filament]] tables")
        fl = []
    seen = set()
    for i, f in enumerate(fl, 1):
        w = "[[filament]] #%d" % i
        if not isinstance(f, dict):
            chk.err(w, "must be a table")
            continue
        _unknown(f, FILAMENT_KEYS, w, chk, geometry=False)
        slot = _int(f.get("slot"), w + ".slot", chk) if "slot" in f else None
        if "slot" not in f:
            chk.err(w, "slot is required (the filament slot number, counted from 1)")
        elif slot is not None:
            if slot in seen:
                chk.err(w, "slot %d is listed twice" % slot)
            seen.add(slot)
        col = f.get("color")
        if col is not None:
            c = str(col)
            if len(c) == 7 and c[0] == "#" and all(ch in "0123456789abcdefABCDEF" for ch in c[1:]):
                col = c.upper()
            else:
                chk.err(w + ".color", "%r is not a color. Use \"#RRGGBB\", e.g. \"#C0C0C0\"" % c)
                col = None
        preset = f.get("preset")
        if preset is not None and (not isinstance(preset, str) or not preset):
            chk.err(w + ".preset", "must be a filament preset name")
            preset = None
        keep = f.get("keep_changes", False)
        if not isinstance(keep, bool):
            chk.err(w + ".keep_changes", "must be true or false")
            keep = False
        if keep and not preset and not job.printer:
            chk.err(w + ".keep_changes", "it belongs to a preset change. Set preset (or printer, which can change the preset), or remove keep_changes.")
        st = f.get("settings", {}) or {}
        if not isinstance(st, dict):
            chk.err(w + ".settings", "must be a table of Studio setting names")
            st = {}
        rem = f.get("remove", False)
        if not isinstance(rem, bool):
            chk.err(w + ".remove", "must be true or false")
            rem = False
        if rem and (preset or col or st or keep):
            chk.err(w, "remove = true cannot be combined with preset, color, settings or keep_changes")
        if slot is not None and not (preset or col or st or rem or (keep and job.printer)):
            chk.err(w, "nothing to change for slot %d. Give preset, color or settings (or remove = true for the last slot)." % slot)
        job.filaments.append({"slot": slot, "preset": preset, "color": col, "settings": st, "keep": keep, "remove": rem, "where": w})

    # ---- objects -----------------------------------------------------------------------------
    job.objects = []
    objs = raw.get("object", []) or []
    if not isinstance(objs, list):
        chk.err("object", "must be written as [[object]] tables")
        objs = []
    for i, o in enumerate(objs, 1):
        w = "[[object]] #%d" % i
        if not isinstance(o, dict):
            chk.err(w, "must be a table")
            continue
        _unknown(o, OBJECT_KEYS, w, chk)
        name = o.get("name")
        if name is not None and (not isinstance(name, str) or not name):
            chk.err(w + ".name", "must be the object's name in the project")
            name = None
        plate = _int(o["plate"], w + ".plate", chk) if "plate" in o else None
        if name is None and plate is None:
            chk.err(w, "select the object with name, plate or both")
        filament = _int(o["filament"], w + ".filament", chk) if "filament" in o else None
        st = o.get("settings", {}) or {}
        if not isinstance(st, dict):
            chk.err(w + ".settings", "must be a table of Studio setting names")
            st = {}
        ent = {"name": name, "plate": plate, "filament": filament, "settings": st, "parts": [], "where": w}
        parts = o.get("part", []) or []
        if not isinstance(parts, list):
            chk.err(w + ".part", "must be written as [[object.part]] tables")
            parts = []
        for j, p in enumerate(parts, 1):
            pw = "%s part #%d" % (w, j)
            if not isinstance(p, dict):
                chk.err(pw, "must be a table")
                continue
            _unknown(p, PART_KEYS, pw, chk)
            pname = p.get("name")
            if not isinstance(pname, str) or not pname:
                chk.err(pw, "name is required (the part's name in the project)")
                pname = None
            pfil = _int(p["filament"], pw + ".filament", chk) if "filament" in p else None
            pst = p.get("settings", {}) or {}
            if not isinstance(pst, dict):
                chk.err(pw + ".settings", "must be a table of Studio setting names")
                pst = {}
            if pname and pfil is None and not pst:
                chk.err(pw, "nothing to change. Give filament or settings.")
            ent["parts"].append({"name": pname, "filament": pfil, "settings": pst, "where": pw})
        if name is not None or plate is not None:
            if filament is None and not st and not ent["parts"]:
                chk.err(w, "nothing to change. Give filament, settings or [[object.part]].")
        job.objects.append(ent)

    # ---- plates ------------------------------------------------------------------------------
    job.plates = []
    pls = raw.get("plate", []) or []
    if not isinstance(pls, list):
        chk.err("plate", "must be written as [[plate]] tables")
        pls = []
    pseen = set()
    for i, p in enumerate(pls, 1):
        w = "[[plate]] #%d" % i
        if not isinstance(p, dict):
            chk.err(w, "must be a table")
            continue
        _unknown(p, PLATE_JOB_KEYS, w, chk, geometry=False)
        pid = _int(p.get("id"), w + ".id", chk) if "id" in p else None
        if "id" not in p:
            chk.err(w, "id is required (the plate number, counted from 1)")
        if pid in pseen:
            chk.err(w, "plate %d is listed twice" % pid)
        pseen.add(pid)
        pt = p.get("plate_type")
        if not isinstance(pt, str):
            chk.err(w, "plate_type is required")
        job.plates.append({"id": pid, "plate_type": pt, "where": w})

    # ---- layer g-code ------------------------------------------------------------------------
    job.layer_gcode = []
    for i, g in enumerate(raw.get("layer_gcode", []) or [], 1):
        w = "[[layer_gcode]] #%d" % i
        if not isinstance(g, dict) or "z" not in g:
            chk.err(w, "needs z (mm)")
            continue
        for k in g:
            if k not in LAYER_KEYS:
                chk.err("%s.%s" % (w, k), "unknown key")
        kinds = [k for k in ("temperature", "pause", "gcode") if k in g]
        if len(kinds) != 1:
            chk.err(w, "give exactly one of temperature, pause or gcode")
            continue
        try:
            job.layer_gcode.append({"plate": int(g.get("plate", 1)), "z": float(g["z"]), "kind": kinds[0], "value": g[kinds[0]]})
        except (TypeError, ValueError):
            chk.err(w, "plate and z must be numbers")
    tw = raw.get("temperature_tower")
    if tw is not None:
        w = "[temperature_tower]"
        if not isinstance(tw, dict):
            chk.err(w, "must be a table")
        else:
            miss = [k for k in ("start", "step", "step_height", "first_z", "steps") if k not in tw]
            for k in tw:
                if k not in TOWER_KEYS:
                    chk.err("%s.%s" % (w, k), "unknown key")
            if miss:
                chk.err(w, "missing: %s" % ", ".join(miss))
            else:
                try:
                    for n in range(int(tw["steps"])):
                        job.layer_gcode.append({"plate": int(tw.get("plate", 1)),
                                                "z": round(float(tw["first_z"]) + n * float(tw["step_height"]), 4),
                                                "kind": "temperature", "value": int(tw["start"]) + n * int(tw["step"]), "tower": True})
                except (TypeError, ValueError):
                    chk.err(w, "start, step, step_height, first_z and steps must be numbers")
    seen_z = set()
    for g in job.layer_gcode:
        key = (g["plate"], round(g["z"], 4))
        if key in seen_z:
            chk.err("layer_gcode", "plate %d z=%s is given twice" % key)
        seen_z.add(key)
    if job.plate_type is not None and job.plate_type not in PLATE_KEY and not chk.errors:
        pass                                            # checked against the file's Studio version later

    changes = (job.printer or job.process or job.plate_type or job.reset or job.settings or job.filaments or job.objects or job.plates or job.layer_gcode)
    if not changes and not chk.errors:
        chk.err("job", "the job changes nothing. Give printer, process, plate_type, [settings], [[filament]], [[object]], [[plate]] or [[layer_gcode]].")
    if chk.errors:
        raise PatchRefused(chk.errors)
    return job
