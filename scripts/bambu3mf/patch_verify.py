"""Checks after writing a patched 3MF. Every check reads the OUTPUT FILE again; none trusts what the
editor says it wrote. The expectation comes from the request (patch_cfg.allowed_changes and the planned model_settings edits).

1. validator, default mode, on the output: 0 errors
2. validator, strict mode: the output's findings (rule, where, class) are a subset of the input's
3. entries: same list and order (plus planned additions); an untouched entry has the same name, bit 11, size, CRC-32 and SHA-256
4. project_settings.config: the differing (key, slot) pairs are all allowed by the request, and every requested value is there
5. model_settings.config: the differences are exactly the planned edits
6. custom_gcode_per_layer.xml: the differences are exactly the planned layers and colours
"""
import copy
import hashlib
import json
import re
import xml.etree.ElementTree as ET
import zipfile

from . import modes, patch_cfg, patch_slots, patch_xml, validate
from .package import gs

CHUNK = 1024 ** 2


def validate_both(path):
    """One validator run, both modes: (default findings, strict findings). The findings are the same objects' copies, classified twice."""
    rep = validate._validate(path, False)
    out = []
    for mode in ("default", "strict"):
        shown, _silent = modes.apply(copy.deepcopy(rep.findings), mode)
        out.append(shown)
    return out[0], out[1]


def _key(f):
    return (f.rule, f.where, f.cls)


def new_findings(old, new):
    """The findings of `new` that `old` did not have. A finding that names setting keys (R3, S1, R5 ...) is compared key by key, so a new
    key is found even when the input already had a finding of the same rule; the others are compared by (rule, where, class)."""
    old_keys = {(f.rule, f.cls, k) for f in old for k in (getattr(f, "keys", None) or ())}
    old_plain = {_key(f) for f in old}
    out = []
    for f in new:
        ks = getattr(f, "keys", None)
        if ks:
            if any((f.rule, f.cls, k) not in old_keys for k in ks):
                out.append(f)
        elif _key(f) not in old_plain:
            out.append(f)
    return out


def _sha(zf, info):
    h = hashlib.sha256()
    with zf.open(info) as fh:
        while True:
            c = fh.read(CHUNK)
            if not c:
                break
            h.update(c)
    return h.hexdigest()


def flat_ms(root):
    """model_settings.config as {(path, kind, name): value}. Positions count per tag, so an inserted metadata line moves nothing."""
    out = {}

    def walk(el, path):
        counts = {}
        for ch in el:
            if ch.tag == "metadata":
                if ch.get("key") is not None:
                    out[(path, "meta", ch.get("key"))] = ch.get("value")
                else:
                    for a, v in ch.attrib.items():
                        out[(path, "attr", a)] = v
                continue
            c = counts.get(ch.tag, 0)
            counts[ch.tag] = c + 1
            ident = (ch.tag, ch.get("id") if ch.tag in ("object", "part") and ch.get("id") is not None else c)
            sub = path + (ident,)
            for a, v in ch.attrib.items():
                out[(sub, "attr", a)] = v
            walk(ch, sub)
    walk(root, ())
    return out


def ms_path(scope, plate_order):
    if scope[0] == "object":
        return (("object", str(scope[1])),)
    if scope[0] == "part":
        return (("object", str(scope[1])), ("part", str(scope[2])))
    return (("plate", plate_order.index(scope[1])),)


def layer_set(root):
    out = set()
    for pl in (root.findall("plate") or [root]):
        info = pl.find("plate_info")
        pid = info.get("id") if info is not None else "1"
        for ly in pl.findall("layer"):
            out.add((pid, ly.get("top_z"), ly.get("type"), ly.get("extruder"), ly.get("color"), ly.get("extra"), ly.get("gcode")))
    return out


class Check:
    def __init__(self):
        self.rows = []

    def add(self, name, ok, detail=""):
        self.rows.append((name, bool(ok), detail))

    @property
    def ok(self):
        return all(r[1] for r in self.rows)

    def failed(self):
        return [r for r in self.rows if not r[1]]


def verify(src, out_path, plan, input_strict, ps):
    """plan: patch.Plan. input_strict: the strict findings of the input. Returns a Check."""
    ck = Check()
    zi, zo = src.pkg.z, None
    try:
        zo = zipfile.ZipFile(out_path)
    except (zipfile.BadZipFile, OSError) as e:
        ck.add("output is a readable ZIP", False, str(e))
        return ck
    try:
        _entries(ck, src, zi, zo, plan)
        _config(ck, src, zo, plan, ps)
        _model_settings(ck, src, zo, plan)
        _gcode(ck, src, zo, plan)
        _root_model(ck, src, zi, zo, plan)
        _small(ck, src, zo, plan)
        _slots(ck, src, zo, plan, ps)
        _printer(ck, src, zo, plan, ps)
    finally:
        zo.close()
    d_def, d_strict = validate_both(out_path)
    errs = [f for f in d_def if f.level == "error"]
    ck.add("validator, default mode: 0 errors", not errs, "; ".join("%s %s" % (f.rule, f.msg[:100]) for f in errs[:3]))
    new = new_findings(input_strict, d_strict)
    ck.add("validator, strict mode: no finding that the input did not have", not new, "; ".join("%s [%s] %s" % (f.rule, f.where, f.msg[:90]) for f in new[:3]))
    if getattr(plan.req, "printer", None):
        old_fit = {(f.rule, f.where) for f in getattr(plan, "input_default", ()) if f.rule in ("R23", "R37")}
        fit = [f for f in d_def if f.rule in ("R23", "R37") and (f.rule, f.where) not in old_fit]
        ck.add("printer change: the plates (R23) and the prime tower (R37) fit the new printer, no finding that the input did not have", not fit,
               "; ".join("%s [%s] %s" % (f.rule, f.where, f.msg[:110]) for f in fit[:3]))
    return ck


def _entries(ck, src, zi, zo, plan):
    ins = zi.infolist()
    outs = zo.infolist()
    removed = set(getattr(plan, "removed", ()))
    expect = [i.filename for i in ins if i.filename not in removed]
    if plan.added_gcode:
        at = expect.index("Metadata/project_settings.config") if "Metadata/project_settings.config" in expect else len(expect)
        expect.insert(at, "Metadata/custom_gcode_per_layer.xml")
    got = [i.filename for i in outs]
    ck.add("entry list and order: the input's, plus the planned additions", got == expect, "" if got == expect else "expected %d entries, found %d" % (len(expect), len(got)))
    oi = {i.filename: i for i in outs}
    ii = {i.filename: i for i in ins}
    bad_flags = [i.filename for i in outs if i.flag_bits & ~0x800]
    ck.add("entry flags: only bit 11 is ever set", not bad_flags, ", ".join(bad_flags[:3]))
    edited = set(plan.edited)
    mism = []
    bit = []
    for name, i in ii.items():
        o = oi.get(name)
        if name in removed:
            if o is not None:
                mism.append(name + " (should be removed)")
            continue
        if o is None:
            mism.append(name + " (missing)")
            continue
        if (o.flag_bits & 0x800) != (i.flag_bits & 0x800):
            bit.append(name)
        if name in edited:
            continue
        if o.file_size != i.file_size or o.CRC != i.CRC or o.compress_type != i.compress_type:
            mism.append(name)
            continue
        if _sha(zi, i) != _sha(zo, o):
            mism.append(name + " (SHA-256)")
    ck.add("untouched entries: same bytes (size, CRC-32, SHA-256), same compress method", not mism, ", ".join(mism[:4]) + (" ..." if len(mism) > 4 else ""))
    ck.add("name-encoding flag (bit 11) kept on every entry", not bit, ", ".join(bit[:4]))
    if "3D/3dmodel.model" not in edited and "3D/3dmodel.model" in oi and "3D/3dmodel.model" in ii:
        ck.add("root model: not edited, same bytes", "3D/3dmodel.model" not in mism and not any(m.startswith("3D/3dmodel.model") for m in mism))


# ---- independent helpers for slot add / remove (nothing here calls patch_slots) ---------------------------------------------------
def _per_slot_key(by, k):
    """Does the key hold one segment per filament slot? From the option table and the config constants only."""
    from . import config as cfgmod
    o = by.get(k)
    if o is not None and o["preset"] == "filament" and o["vector"]:
        return True
    return k in cfgmod.PER_FILAMENT_PROJECT or k in ("filament_diameter_map", "filament_settings_id", "filament_ids", "filament_colour", "filament_multi_colour",
                                                     "filament_self_index")


def cut_to(big, ref, n_big, n_small, by):
    """`big` (a config with n_big slots) cut down to the shape of `ref` (the same config with n_small slots), by plain truncation: every
    per-slot list to ref's length, the purge matrix to its top-left square, the purge vector to 2 * n_small, the two diff lists with the
    entries of the slots in between taken out. Returns None if a list does not fit (then the check fails and says so)."""
    out = dict(big)
    for k, v in big.items():
        if k in ("flush_volumes_matrix", "flush_volumes_vector", "different_settings_to_system", "inherits_group"):
            continue
        r = ref.get(k)
        if isinstance(v, list) and isinstance(r, list) and len(v) != len(r) and _per_slot_key(by, k):
            if len(v) < len(r):
                return None
            out[k] = v[:len(r)]
    m = big.get("flush_volumes_matrix")
    if isinstance(m, list):
        if len(m) != n_big * n_big:
            return None
        out["flush_volumes_matrix"] = [m[r * n_big + c] for r in range(n_small) for c in range(n_small)]
    vec = big.get("flush_volumes_vector")
    if isinstance(vec, list):
        out["flush_volumes_vector"] = vec[:2 * n_small]
    for k in ("different_settings_to_system", "inherits_group"):
        if isinstance(big.get(k), list):
            if len(big[k]) != n_big + 2:
                return None
            lst = big[k][:n_small + 1] + big[k][n_big + 1:]
            if k in ref:
                out[k] = lst
            else:
                out.pop(k, None)
    return out


def expected_new_segments(ps, preset, vf):
    """{key: list} the values a new slot holds for its preset: ps.resolve("filament", preset) + serialize.normalize, the way
    patch_printer.expected_value does it for printers (a key the preset does not set takes the option's default). Only keys that are
    lists with one segment per slot."""
    from . import config as cfgmod
    from .serialize import normalize
    fc, _m = ps.resolve("filament", preset)
    out = {}
    for k, o in ps.options.items():
        if o["preset"] != "filament" or not o["vector"]:
            continue
        raw = fc.get(k)
        if raw is None:
            raw = o["default"]
        if raw is None:
            continue
        fv = normalize(o, raw)
        fv = fv if isinstance(fv, list) else [fv]
        if not fv:
            continue
        if k in cfgmod.FILAMENT_DEV_OPTIONS:
            out[k] = list(fv)
        elif o["variant"] == "filament":
            seg = list(fv)
            while len(seg) < vf:
                seg.append(seg[0])
            out[k] = seg[:vf]
        else:
            out[k] = [fv[0]]
    return out


def _req_old(req, n_old):
    """The request restricted to the slots the input already had (a new slot is checked on its own)."""
    r = patch_cfg.Request()
    r.__dict__.update(req.__dict__)
    r.slots = {i: s for i, s in req.slots.items() if i < n_old}
    return r


def _config(ck, src, zo, plan, ps):
    name = "Metadata/project_settings.config"
    try:
        new = json.loads(zo.read(name).decode("utf-8"))
    except (KeyError, ValueError) as e:
        ck.add("project_settings.config reads back as JSON", False, str(e))
        return
    n, req = plan.n, plan.req
    n_old = getattr(plan, "n_old", n)
    old, view, cmp_n, creq = src.cfg, new, n, req
    if n > n_old:                                                   # slots were added: compare the output without them to the input
        view = cut_to(new, old, n, n_old, ps.options)               # an independent cutter (plain truncation), not the editor's
        if view is None:
            ck.add("project_settings.config: the added slot(s) can be cut out again", False, "a per-slot list does not have the expected length")
            return
        cmp_n, creq = n_old, _req_old(req, n_old)
    elif n < n_old:
        old = cut_to(old, new, n_old, n, ps.options)
        if old is None:
            ck.add("project_settings.config: the input without its last slot can be built", False, "a per-slot list does not have the expected length")
            return
        cmp_n = n
    from .patch_cfg import Layout
    lay = Layout(old, cmp_n)
    diffs = patch_cfg.diff_config(old, view, cmp_n, ps.options, lay)
    allowed = patch_cfg.allowed_changes(creq, cmp_n, ps.options, plan.flush_slots)
    bad = patch_cfg.check_allowed(diffs, allowed, creq)
    ck.add("project_settings.config: every difference is one the job asked for", not bad,
           "; ".join("%s%s" % (k, "" if w is None else " [%s]" % (w,)) for k, w in bad[:6]))
    miss = []
    lay_new = Layout(new, n)
    for k, v in req.settings.items():
        o = ps.options[k]
        if not (new.get(k) == ([v] * len(new[k]) if o["vector"] else v)):
            miss.append(k)
    for i, s in req.slots.items():
        for k, v in s["settings"].items():
            o = ps.options[k]
            if o["variant"] == "filament" and len(new[k]) == lay_new.total:
                seg = new[k][lay_new.starts[i]:lay_new.starts[i] + lay_new.widths[i]]
            else:
                seg = new[k][i:i + 1]
            if seg != [v] * len(seg):
                miss.append("slot %d %s" % (i + 1, k))
        if s.get("color") and new["filament_colour"][i] != s["color"]:
            miss.append("slot %d colour" % (i + 1))
        if s.get("preset") and (new["filament_settings_id"][i] != s["preset"]):
            miss.append("slot %d preset" % (i + 1))
    for a in req.adds:
        i = a["slot"] - 1
        if len(new["filament_settings_id"]) <= i or new["filament_settings_id"][i] != a["preset"]:
            miss.append("slot %d preset" % a["slot"])
        if len(new["filament_colour"]) <= i or new["filament_colour"][i] != a["color"]:
            miss.append("slot %d colour" % a["slot"])
    if req.process and new.get("print_settings_id") != req.process:
        miss.append("process")
    if req.plate_type and new.get("curr_bed_type") != req.plate_type:
        miss.append("plate type")
    ck.add("project_settings.config: every requested value is in the output", not miss, ", ".join(miss[:6]))
    keyd = set(old) ^ set(view)
    ck.add("project_settings.config: no key added or removed except the diff lists", keyd <= {"different_settings_to_system", "inherits_group"}, ", ".join(sorted(keyd)[:5]))
    ck.add("project_settings.config: the slot count is the input's plus the added slots minus the removed one", len(new["filament_colour"]) == n, "%d, expected %d" % (len(new["filament_colour"]), n))


def _slots(ck, src, zo, plan, ps):
    """Slot add / remove: the new slot's values are the preset's (checked against a second build), nothing names a slot above the count."""
    n = plan.n
    n_old = getattr(plan, "n_old", n)
    try:
        new = json.loads(zo.read("Metadata/project_settings.config").decode("utf-8"))
        ms = zo.read("Metadata/model_settings.config").decode("utf-8", "replace")
    except (KeyError, ValueError):
        return                                                      # reported by the checks above
    over = sorted({int(v) for v in re.findall(r'key="extruder" value="(\d+)"', ms) if int(v) > n})
    ck.add("model_settings.config: no filament above the slot count (%d)" % n, not over, ", ".join(str(v) for v in over[:4]))
    if n == n_old:
        return
    if n > n_old:
        by = ps.options
        old = src.cfg
        vp = len(old.get("printer_extruder_variant") or [0]) or 1
        bad, skip = [], set()
        for a in plan.req.adds:
            skip |= set(plan.req.slots.get(a["slot"] - 1, {}).get("settings", {}))
        # the expected tail of every per-slot list: the added slots' segments, built from the presets (a second path, not extend_config)
        segs = []
        for a in plan.req.adds:
            try:
                segs.append(expected_new_segments(ps, a["preset"], vp))
            except Exception as e:                                  # noqa: BLE001
                ck.add("added slot(s): the preset's values can be rebuilt", False, str(e)[:200])
                return
        compared = 0
        for k, o in by.items():
            if k in skip or o["preset"] != "filament" or not o["vector"] or k not in old or k not in new:
                continue
            if len(new[k]) == len(old[k]):                          # a list the editor left alone (the warning names it)
                continue
            want = []
            for sg in segs:
                if k not in sg:
                    want = None
                    break
                want += sg[k]
            if want is None:
                continue
            compared += 1
            if new[k][len(old[k]):] != want or new[k][:len(old[k])] != old[k]:
                bad.append(k)
        for a_i, a in enumerate(plan.req.adds):
            i = a["slot"] - 1
            for k, want in (("filament_settings_id", a["preset"]), ("filament_colour", a["color"])):
                if len(new.get(k, [])) <= i or new[k][i] != want:
                    bad.append(k)
        ck.add("added slot(s): every list holds the preset's values for the new slot (rebuilt from the preset, %d lists)" % compared, not bad and compared > 0, ", ".join(sorted(set(bad))[:5]))
        n_cells = [(r, c) for r in range(n) for c in range(n) if r >= n_old or c >= n_old]
        m, m0 = new.get("flush_volumes_matrix"), old.get("flush_volumes_matrix")
        if isinstance(m0, list) and len(m0) == n_old * n_old:
            keep = isinstance(m, list) and len(m) == n * n and all(m[r * n + c] == m0[r * n_old + c] for r in range(n_old) for c in range(n_old))
            auto, why = None, ""
            try:                                                    # Studio's automatic values, worked out from the OUTPUT's own colours and presets
                import os
                from . import flush as fl
                from .profiles import studio_root
                tables = fl.Tables(os.path.join(studio_root(), ps.version, "flush"))
                ds = int(new.get("nozzle_flush_dataset", ["0"])[0]) if new.get("nozzle_flush_dataset") else 0
                auto = fl.build_matrix(new, tables, mode="auto", nozzle_flush_dataset=ds)["matrix"]
            except Exception as e:                                  # noqa: BLE001 - the warning of the run says so; the cells must still be numbers
                why = type(e).__name__
            if auto is not None and len(auto) == n * n:
                cells_ok = all(m[r * n + c] == auto[r * n + c] for r, c in n_cells) if keep else False
            else:
                cells_ok = keep and all(re.fullmatch(r"\d+(\.\d+)?", str(m[r * n + c])) for r, c in n_cells)
            ck.add("added slot(s): the purge matrix is (n x n), the old cells are the input's, the new cells are Studio's automatic values", keep and cells_ok, why)
        dl = new.get("different_settings_to_system")
        if isinstance(dl, list):
            ck.add("added slot(s): different_settings_to_system has one entry per preset (n + 2)", len(dl) == n + 2 and len(new.get("inherits_group", dl)) == n + 2)


def _printer(ck, src, zo, plan, ps):
    """Printer change: every printer-owned key is the new preset's value (a second read of the preset), the bed and the nozzle are the input's,
    the printer entry of the diff lists is empty. Print-host keys that were set are blank."""
    req = plan.req
    try:
        new = json.loads(zo.read("Metadata/project_settings.config").decode("utf-8"))
    except (KeyError, ValueError):
        return                                                      # reported by the checks above
    old = src.cfg
    if req.blank_hosts:
        left = [k for k in req.blank_hosts if new.get(k) not in ("", [""], [])]
        ck.add("privacy: the print-host keys that were set are blank", not left, ", ".join(left))
    if not req.printer:
        return
    from . import config as cfgmod
    from . import patch_printer
    by = ps.options
    mcfg, _m = ps.resolve("machine", req.printer)
    bad = []
    for k, o in by.items():
        if o["preset"] != "printer" or k in cfgmod.BUILDER_KEYS or k not in new or k not in old:
            continue
        if k in cfgmod.EXCLUDE:
            continue
        if new[k] != patch_printer.expected_value(by, mcfg, k):
            bad.append(k)
    ck.add("printer change: every printer setting is the value of %s" % req.printer, new.get("printer_settings_id") == req.printer and not bad, ", ".join(bad[:5]))
    recut = bool(getattr(req, "recut", False))
    same = [k for k in ("printable_area", "printable_height", "bed_exclude_area", "nozzle_diameter") + (() if recut else ("printer_extruder_variant",))
            if new.get(k) != old.get(k)]
    ck.add("printer change: bed and nozzle%s are the input's" % ("" if recut else " and extruder variants"), not same, ", ".join(same))
    if recut:
        _recut_check(ck, new, plan, ps, mcfg)
    n = plan.n
    dl, ig = new.get("different_settings_to_system"), new.get("inherits_group")
    ck.add("printer change: the printer entry of the diff lists is empty", (not isinstance(dl, list) or dl[n + 1] == "") and (not isinstance(ig, list) or ig[n + 1] == ""))


def _recut_check(ck, new, plan, ps, mcfg):
    """Another extruder variant count: a second path to the values. The layout comes from the new printer preset; a slot or the process
    whose preset was swapped without keep_changes holds the preset's own values (ps.resolve + normalize, not the editor's sysc)."""
    from .serialize import normalize
    req, by, n = plan.req, ps.options, plan.n
    vp = len(mcfg.get("printer_extruder_variant") or []) or 1
    si = new.get("filament_self_index") or []
    ck.add("printer change, extruder variants: %d per slot, filament_self_index fits" % vp,
           list(new.get("printer_extruder_variant") or []) == list(mcfg.get("printer_extruder_variant") or []) and
           si == [str(i + 1) for i in range(n) for _ in range(vp)], "%d entries" % len(si))
    bad = []
    for k, o in by.items():
        if o["variant"] == "filament" and k in new and isinstance(new[k], list) and o["vector"] and len(new[k]) != n * vp:
            bad.append(k)
        if o["variant"] == "print" and k in new and isinstance(new[k], list) and o["vector"] and len(new[k]) != vp:
            bad.append(k)
    ck.add("printer change, extruder variants: every per-variant list has one value per variant", not bad, ", ".join(sorted(bad)[:6]))
    bad, compared = [], 0
    expect = bool(req.process and not req.keep_process) or any(sl.get("preset") and not sl.get("keep") for sl in req.slots.values())
    if req.process and not req.keep_process:
        pc, _m = ps.resolve("process", req.process)
        for k, o in by.items():
            if o["variant"] != "print" or not o["vector"] or k not in new or k in req.settings:
                continue
            raw = pc.get(k, o["default"])
            if raw is None:
                continue
            want = normalize(o, raw)
            want = want if isinstance(want, list) else [want]
            while len(want) < vp:
                want.append(want[0])
            compared += 1
            if new[k] != want[:vp]:
                bad.append("process %s" % k)
    for i, sl in req.slots.items():
        if not sl.get("preset") or sl.get("keep"):
            continue
        segs = expected_new_segments(ps, sl["preset"], vp)
        for k, want in segs.items():
            o = by[k]
            if o["variant"] != "filament" or k in sl["settings"] or k not in new:
                continue
            compared += 1
            if new[k][i * vp:(i + 1) * vp] != want:
                bad.append("slot %d %s" % (i + 1, k))
    ck.add("printer change, extruder variants: swapped presets hold their own values for the new variants (rebuilt from the presets, %d lists)" % compared,
           not bad and (compared > 0 or not expect), ", ".join(bad[:6]))


def _model_settings(ck, src, zo, plan):
    name = "Metadata/model_settings.config"
    try:
        new_root = ET.fromstring(zo.read(name))
        old_root = ET.fromstring(src.ms_raw)
    except (KeyError, ET.ParseError) as e:
        ck.add("model_settings.config reads back as XML", False, str(e))
        return
    plate_order = [int(p["meta"].get("plater_id", i + 1)) for i, p in enumerate(src.ms["plates"])]
    flat_old, flat_new = flat_ms(old_root), flat_ms(new_root)
    expected = dict(flat_old)
    for scope, key, value in plan.ms_edits:
        expected[(ms_path(scope, plate_order), "meta", key)] = value
    for scope, key in getattr(plan, "ms_deletes", ()):
        expected.pop((ms_path(scope, plate_order), "meta", key), None)
    ok = flat_new == expected
    detail = ""
    if not ok:
        extra = [k for k in flat_new if flat_new.get(k) != expected.get(k)]
        gone = [k for k in expected if k not in flat_new]
        detail = "%d value(s) differ from the plan%s: %s" % (len(extra) + len(gone), "", "; ".join("%s" % (k[2],) for k in (extra + gone)[:5]))
    ck.add("model_settings.config: the differences are exactly the planned edits (%d)" % (len(plan.ms_edits) + len(getattr(plan, "ms_deletes", ()))), ok, detail)


def _gcode(ck, src, zo, plan):
    name = "Metadata/custom_gcode_per_layer.xml"
    has_out = name in zo.namelist()
    if not (plan.added_gcode or name in plan.edited):
        return
    if not has_out:
        ck.add("custom_gcode_per_layer.xml is in the output", False)
        return
    try:
        new = layer_set(ET.fromstring(zo.read(name)))
    except ET.ParseError as e:
        ck.add("custom_gcode_per_layer.xml reads back as XML", False, str(e))
        return
    old = layer_set(ET.fromstring(src.pkg.raw(name))) if src.pkg.has(name) else set()
    added, removed = new - old, old - new
    want = {(str(g["plate"]), gs(g["z"])) for g in plan.layers}
    n_new = len([a for a in added if (a[0], a[1]) in want])
    recolor_n = plan.recolored
    ok = n_new == len(plan.layers) and len(added) - n_new == recolor_n and len(removed) == recolor_n
    ck.add("custom_gcode_per_layer.xml: the new layers are the planned ones and nothing else changed", ok,
           "" if ok else "planned %d new layer(s) and %d recolored; found %d added, %d removed" % (len(plan.layers), recolor_n, len(added), len(removed)))


def _root_model(ck, src, zi, zo, plan):
    """Rule 5: the output's metadata block is the input's with exactly the planned elements removed; every byte from <resources on is the input's."""
    name = "3D/3dmodel.model"
    removed = list(getattr(plan, "head_removed", ()))
    if name not in zo.namelist():
        return
    from .patch_read import read_head
    from .package import gs as _gs                                 # noqa: F401
    class _P:
        pass
    p = _P()
    p.z = zo
    p.index = {i.filename: i for i in zo.infolist()}
    out_head = read_head(p, name)
    want = patch_slots.head_without(src.head, removed)
    ck.add("root model: the metadata block is the input's minus the planned privacy removals (%d)" % len(removed), out_head is not None and want is not None and out_head == want,
           "" if out_head == want else "the metadata block differs from the plan")
    i_in, i_out = zi.getinfo(name), zo.getinfo(name)
    ck.add("root model: every byte from <resources on is the input's (size, SHA-256)",
           i_out.file_size - len(out_head or b"") == i_in.file_size - len(src.head) and _sha_from(zi, i_in, len(src.head)) == _sha_from(zo, i_out, len(out_head or b"")))


def _sha_from(zf, info, skip):
    h = hashlib.sha256()
    with zf.open(info) as fh:
        left = skip
        while left > 0:
            got = fh.read(min(left, CHUNK))
            if not got:
                return None
            left -= len(got)
        while True:
            c = fh.read(CHUNK)
            if not c:
                break
            h.update(c)
    return h.hexdigest()


def _canon(el):
    return (el.tag, tuple(sorted(el.attrib.items())), (el.text or "").strip(), tuple(_canon(c) for c in el))


def _expected_sequence(raw, sliced, n_old, adds):
    """filament_sequence.json as it should read after the patch, worked out here (not the editor's bytes): a sliced input has its sequence
    lists emptied; a slot add extends a per-filament optimal_assignment of n_old entries with zeros."""
    j = json.loads(raw.decode("utf-8"))
    if isinstance(j, dict):
        for pv in j.values():
            if not isinstance(pv, dict):
                continue
            for lk, lst in list(pv.items()):
                if not isinstance(lst, list) or not lst:
                    continue
                if sliced and lk in ("sequence", "filament_sequence", "nozzle_sequence", "optimal_assignment"):
                    pv[lk] = []
                elif not sliced and adds and lk == "optimal_assignment" and len(lst) == n_old:
                    pv[lk] = lst + [0] * adds
    return j


def _small(ck, src, zo, plan):
    """slice_info.config (no plate entries) and filament_sequence.json (empty lists) of a sliced input. Both are compared with what the
    INPUT says, worked out here, not with the editor's own bytes."""
    names = zo.namelist()
    sname, qname = "Metadata/slice_info.config", "Metadata/filament_sequence.json"
    sliced = getattr(src, "sliced", False)
    if sliced and src.pkg.has(sname) and sname in names:
        try:
            r_old, r_new = ET.fromstring(src.pkg.raw(sname)), ET.fromstring(zo.read(sname))
            keep_old = [_canon(c) for c in r_old if c.tag != "plate"]
            keep_new = [_canon(c) for c in r_new]
            ok = r_new.tag == r_old.tag and r_new.attrib == r_old.attrib and keep_new == keep_old and not r_new.findall(".//plate")
        except ET.ParseError:
            ok = False
        ck.add("slice_info.config: no <plate> entry left, every other element is the input's", ok)
    adds = len(getattr(plan.req, "adds", ()) or ())
    if (sliced or adds) and src.pkg.has(qname) and qname in names:
        try:
            want = _expected_sequence(src.pkg.raw(qname), sliced, getattr(plan, "n_old", plan.n), adds)
            got = json.loads(zo.read(qname).decode("utf-8"))
            ok = got == want
        except (ValueError, TypeError):
            ok = False
        ck.add("filament_sequence.json: the input's, worked out again (lists emptied / new slot appended)", ok)
    if getattr(src, "sliced", False):
        left = [n for n in names if re.match(r"^Metadata/plate_\d+\.gcode(\.md5)?$", n) or n == "Metadata/_rels/model_settings.config.rels"]
        ck.add("sliced input: no G-code, G-code checksum or G-code link entry left", not left, ", ".join(left[:3]))
        ms = zo.read("Metadata/model_settings.config")
        ck.add("sliced input: no gcode_file key left in model_settings.config", b'key="gcode_file"' not in ms)
