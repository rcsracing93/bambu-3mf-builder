"""Patch mode: add a filament slot, remove the last one, find out whether a slot is in use, root-model privacy edit (rule 5),
sliced-input clean-up. Design note sections 3.1-3.5 and 4. Nothing here renumbers a slot: a slot is added at the end or removed from
the end, and only if nothing names it.

All config work is on parsed dicts; the byte-level edits of the small XML entries are in patch_xml.
"""
import copy
import json
import os
import re

from . import config as cfgmod
from . import flush as fl
from .config import FILAMENT_DEV_OPTIONS, LAZY_PROJECT_LISTS, PER_FILAMENT_PROJECT
from .patch_job import PatchRefused
from .privacy import PATH_RE
from .profiles import studio_root
from .rules_config import slot_bases, _is_system
from .wipetower import paint_states

SLOT_KEYS = ("wall_filament", "sparse_infill_filament", "solid_infill_filament", "support_filament", "support_interface_filament")
PER_SLOT_PROJECT_EXTRA = {"filament_diameter_map"}          # 2.9.1: nozzle diameter per filament (config.py)
CHUNK = 1024 ** 2
PAINT_RE = re.compile(rb'paint_color="([^"]*)"')
PAINT_SCAN_CAP = 2 * 1024 ** 3                              # bytes of mesh text read for one removal check

# ---- the keys that hold one segment per slot ---------------------------------------------------------------------------------


def slot_keys(cfg, by):
    """Keys of the config that carry one segment per filament slot, with their kind: 'variant' (n x V), 'dev' (own length per slot), 'one' (n)."""
    out = {}
    for k, v in cfg.items():
        if not isinstance(v, list):
            continue
        o = by.get(k)
        if o is not None and o["preset"] == "filament" and o["vector"]:
            if k in FILAMENT_DEV_OPTIONS:
                out[k] = "dev"
            elif o["variant"] == "filament":
                out[k] = "variant"
            else:
                out[k] = "one"
        elif k in PER_FILAMENT_PROJECT or k in PER_SLOT_PROJECT_EXTRA:
            out[k] = "one"
    return out


class Shim:
    """A Source stand-in: the same attributes, another config. patch_cfg reads cfg, embedded and nplates from it."""

    def __init__(self, src, cfg):
        self.__dict__.update(src.__dict__)
        self.cfg = cfg
        self.n = len(cfg["filament_colour"])


# ---- one slot, built by the builder's own code ----------------------------------------------------------------------------------
def one_slot_config(ps, bases, preset, color, plate_type, nplates):
    """The builder's project config for a project with this one filament: its segments are the new slot's segments (every segment in
    config.py depends on the slot's own preset only)."""
    from . import config as cfgmod_

    class J:
        pass
    j = J()
    printer = bases[-1][2]
    j.profiles, j.printer, j.studio = ps, printer, ps.version
    j.process = bases[0][2] or ps.compatible("process", printer)[0]
    j.plate_type, j.settings, j.purge = plate_type, {}, "default"
    j.filaments = [{"preset": preset, "color": color, "settings": {}}]
    return cfgmod_.build_config(j, plates=max(nplates, 1), max_height=0.0).config


def extend_config(src, ps, specs, plate_type=None):
    """Add slots at the end. specs: [{"preset", "color"}]. Returns (new cfg, changes, warnings). The input dict is not changed."""
    from .patch_cfg import Ctx, Layout, _compat_check
    cfg = copy.deepcopy(src.cfg)
    changes, warns = [], []
    for sp in specs:
        shim = Shim(src, cfg)
        ctx = Ctx(shim, ps)
        n, by = ctx.n, ctx.by
        _compat_check(ctx, "filament", sp["preset"], "[[filament]] slot %d preset" % (n + 1))
        if ctx.bases[n + 1][2] is None:
            raise PatchRefused(["slot %d: the printer preset of this file is a user preset with no parent, so the new slot's preset cannot be built for it. Open the file in Studio and save it first." % (n + 1)])
        try:
            one = one_slot_config(ps, ctx.bases, sp["preset"], sp["color"], plate_type or cfg.get("curr_bed_type"), src.nplates)
        except Exception as e:                                          # noqa: BLE001 - a preset the builder cannot assemble
            raise PatchRefused(["slot %d: preset %r cannot be built for this printer (%s: %s)" % (n + 1, sp["preset"], type(e).__name__, str(e)[:120])])
        vp = len(cfg.get("printer_extruder_variant") or [0]) or 1
        vf = len(one["filament_self_index"])
        if vf != vp:
            raise PatchRefused(["slot %d: preset %r has %d extruder variant(s), the printer has %d. Pick a preset made for this printer." % (n + 1, sp["preset"], vf, vp)])
        lay = Layout(cfg, n)
        skipped = []
        for k, kind in slot_keys(cfg, by).items():
            if k not in one:
                skipped.append(k)
                continue
            v = cfg[k]
            seg = one[k]
            if kind == "variant":
                if len(v) != lay.total:
                    skipped.append(k)
                    continue
                cfg[k] = v + list(seg)
            elif kind == "dev":
                cfg[k] = v + list(seg)
            else:
                if len(v) != n:
                    if not (k in LAZY_PROJECT_LISTS and len(v) <= n):
                        skipped.append(k)
                    continue
                cfg[k] = v + list(seg[:1])
        cfg["filament_settings_id"] = list(cfg["filament_settings_id"]) + [sp["preset"]]
        cfg["filament_ids"] = list(cfg["filament_ids"]) + list(one["filament_ids"])
        cfg["filament_colour"] = list(cfg["filament_colour"]) + [sp["color"]]
        if isinstance(cfg.get("filament_multi_colour"), list) and len(cfg["filament_multi_colour"]) == n:
            cfg["filament_multi_colour"] = list(cfg["filament_multi_colour"]) + [sp["color"]]
        cfg["filament_self_index"] = list(cfg["filament_self_index"]) + [str(n + 1)] * vf
        if skipped:
            warns.append("new slot %d: %s left without a value for the new slot (the list in the file has a layout the preset does not give)" % (n + 1, ", ".join(sorted(skipped))))
        # diff lists: the new entry goes before the printer entry
        for key in ("different_settings_to_system", "inherits_group"):
            if isinstance(cfg.get(key), list):
                cfg[key] = cfg[key][:n + 1] + [""] + cfg[key][n + 1:]
        _grow_flush(src, ps, cfg, n, warns)
        changes.append("filament slot %d added: %s, colour %s" % (n + 1, sp["preset"], sp["color"]))
    return cfg, changes, warns


def _grow_flush(src, ps, cfg, n, warns):
    """flush_volumes_matrix: (n+1)^2 cells, the old ones kept, the new row and column Studio's automatic values (auto_calc_flushing_volumes for
    the changed slot only). flush_volumes_vector: two more entries (the multipliers' per-slot pair)."""
    m = cfg.get("flush_volumes_matrix")
    vec = cfg.get("flush_volumes_vector")
    n1 = n + 1
    if not isinstance(m, list) or len(m) != n * n:
        warns.append("the purge matrix in the file has %s entries, not %d; it is left as it is" % (len(m) if isinstance(m, list) else "no", n * n))
        return
    try:
        tables = fl.Tables(os.path.join(studio_root(), ps.version, "flush"))
        ds = int(cfg.get("nozzle_flush_dataset", ["0"])[0]) if cfg.get("nozzle_flush_dataset") else 0
        auto = fl.build_matrix(cfg, tables, mode="auto", nozzle_flush_dataset=ds)["matrix"] if n1 > 1 else ["0"]
    except Exception as e:                                          # noqa: BLE001
        warns.append("purge volumes of the new slot were not calculated (%s: %s); the new row and column hold 280 (Studio's default)" % (type(e).__name__, str(e)[:80]))
        auto = [("0" if r == c else "280") for r in range(n1) for c in range(n1)]
    new = []
    for r in range(n1):
        for c in range(n1):
            new.append(m[r * n + c] if r < n and c < n else auto[r * n1 + c])
    cfg["flush_volumes_matrix"] = new
    if isinstance(vec, list) and len(vec) == 2 * n:
        cfg["flush_volumes_vector"] = list(vec) + ["140", "140"]
    else:
        warns.append("flush_volumes_vector has %s entries, not %d; it is left as it is" % (len(vec) if isinstance(vec, list) else "no", 2 * n))


# ---- remove the last slot (also the verifier's view of an output without its last slot) -----------------------------------------
def drop_last_slot(cfg, ps, n):
    """The config as it would be with n - 1 slots: the last segment of every per-slot list cut, the last purge row and column removed.
    Raises PatchRefused when a list has no layout that can be cut."""
    from .patch_cfg import Layout
    from .patch_cfg import _dev_layout
    by = ps.options
    out = copy.deepcopy(cfg)
    lay = Layout(cfg, n)
    bases = slot_bases(cfg, ps, n)
    defaults = {}
    from .serialize import normalize
    for k, o in by.items():
        d = o["default"]
        if d is not None and k in FILAMENT_DEV_OPTIONS:
            defaults[k] = normalize(o, d)
    problems = []
    for k, kind in slot_keys(cfg, by).items():
        v = cfg[k]
        if kind == "variant":
            if len(v) != lay.total:
                problems.append(k)
                continue
            out[k] = v[:lay.starts[n - 1]]
        elif kind == "dev":
            segs = _dev_layout(k, [b[2] for b in bases[1:n + 1]], by, defaults, ps, v)
            if segs is None:
                problems.append(k)
                continue
            out[k] = v[:segs[n - 1][0]]
        else:
            if len(v) == n:
                out[k] = v[:n - 1]
            elif not (k in LAZY_PROJECT_LISTS and len(v) < n):
                problems.append(k)
    if problems:
        raise PatchRefused(["the list(s) %s do not have the layout of %d slots, so the last slot cannot be cut out. Open the file in Studio and save it first." % (", ".join(sorted(problems)[:6]), n)])
    for k in ("filament_settings_id", "filament_ids", "filament_colour", "filament_multi_colour"):
        if isinstance(cfg.get(k), list) and len(cfg[k]) == n:
            out[k] = cfg[k][:n - 1]
    out["filament_self_index"] = cfg["filament_self_index"][:lay.starts[n - 1]]
    for key in ("different_settings_to_system", "inherits_group"):
        if isinstance(cfg.get(key), list) and len(cfg[key]) == n + 2:
            lst = cfg[key][:n] + cfg[key][n + 1:]
            if any(lst):
                out[key] = lst
            else:
                out.pop(key, None)
    m = cfg.get("flush_volumes_matrix")
    if isinstance(m, list) and len(m) == n * n:
        out["flush_volumes_matrix"] = [m[r * n + c] for r in range(n - 1) for c in range(n - 1)]
    vec = cfg.get("flush_volumes_vector")
    if isinstance(vec, list) and len(vec) == 2 * n:
        out["flush_volumes_vector"] = vec[:2 * (n - 1)]
    return out


# ---- plate lists in model_settings.config ---------------------------------------------------------------------------------------
PLATE_LISTS = (("filament_maps", "1"), ("filament_volume_maps", "0"))


def _ints(v):
    """Studio reads a plate list with `stream >> int` until it fails (bbs_3mf.cpp get_vector_from_string). None if a token is not an integer."""
    try:
        return [int(t) for t in v.split()]
    except ValueError:
        return None


def first_layer_sequence(vals, n_old, n_new):
    """first_layer_print_sequence of one plate after the slot count goes n_old -> n_new. Studio's rule, both tags:
    add: PartPlate::update_first_layer_print_sequence (2.8.2.61 PartPlate.cpp L3805-3846, tail L3828-3846; 2.9.1.70 L4121-4162, tail L4144-4162), called from
    Plater.cpp L24310 (2.8.2) / L28861 (2.9.1): nothing when the list is empty or starts with 0; more entries than slots -> entries above the count
    erased; fewer -> the missing slot numbers appended.
    remove: PartPlate::update_first_layer_print_sequence_when_delete_filament (2.8.2 L3848-3875, tail L3865-3875; 2.9.1 L4164-4196, tail L4181-4196), called from
    PartPlate::on_filament_deleted (2.8.2 L4119, 2.9.1 L4444): the entry for the removed slot erased, higher numbers lowered by one."""
    if not vals or vals[0] == 0:
        return vals
    out = list(vals)
    if n_new > n_old:
        if len(out) > n_new:
            out = [x for x in out if x <= n_new]
        elif len(out) < n_new:
            out += list(range(len(out) + 1, n_new + 1))
    else:
        out = [x - 1 if x > n_new else x for x in out if x != n_old]       # the removed slot is the last one (n_old = n_new + 1): nothing above it
    return out


def other_layers_sequence(nums, vals, n_old, n_new):
    """other_layers_print_sequence of one plate: groups of [first layer, last layer, order ...] (ParameterUtils.cpp get_other_layers_print_sequence,
    both tags identical). The same add / remove rule as for the first layer, applied to each group's order (same functions, same lines). Returns the new
    ints, or None when the list is not in Studio's form."""
    if nums == 0 or not vals:
        return vals
    if nums < 0 or len(vals) % nums:
        return None
    k = len(vals) // nums
    if k <= 2:
        return None
    out = []
    for g in range(nums):
        item = vals[g * k:(g + 1) * k]
        orders = item[2:]
        if n_new > n_old:
            if len(orders) > n_new:
                orders = [x for x in orders if x <= n_new]
            if len(orders) < n_new:
                orders = orders + list(range(len(orders) + 1, n_new + 1))
        else:
            orders = [x - 1 if x > n_new else x for x in orders if x != n_old]
        out += item[:2] + orders
    return out


def plate_map_edits(src, n_old, n_new):
    """Edits [(("plate", plater_id), key, value)] that give every plate's filament_maps / filament_volume_maps n_new entries and apply Studio's rule to
    first_layer_print_sequence / other_layers_print_sequence (+ _nums). Absent keys stay absent. A list that does not have n_old entries (maps) or is not in
    Studio's form (print sequences) is left and reported."""
    edits, warns = [], []
    for pi, pl in enumerate(src.ms["plates"], 1):
        pid = int(pl["meta"].get("plater_id", pi))
        for key, fill in PLATE_LISTS:
            v = pl["meta"].get(key)
            if v is None:
                continue
            toks = v.split()
            if len(toks) != n_old:
                warns.append("plate %d: %s holds %d entries, not %d; left as it is" % (pi, key, len(toks), n_old))
                continue
            toks = toks + [fill] * (n_new - n_old) if n_new > n_old else toks[:n_new]
            edits.append((("plate", pid), key, " ".join(toks)))
        v = pl["meta"].get("first_layer_print_sequence")
        if v is not None:
            vals = _ints(v)
            if vals is None:
                warns.append("plate %d: first_layer_print_sequence is not a list of numbers; left as it is" % pi)
            else:
                new = first_layer_sequence(vals, n_old, n_new)
                if new != vals:
                    edits.append((("plate", pid), "first_layer_print_sequence", " ".join(str(x) for x in new)))
        v = pl["meta"].get("other_layers_print_sequence")
        if v is not None:
            vals = _ints(v)
            nums = _ints(pl["meta"].get("other_layers_print_sequence_nums", "0"))
            new = other_layers_sequence(nums[0] if nums else 0, vals, n_old, n_new) if vals is not None and nums is not None and len(nums) == 1 else None
            if new is None:
                warns.append("plate %d: other_layers_print_sequence is not in Studio's form; left as it is" % pi)
            elif new != vals:
                edits.append((("plate", pid), "other_layers_print_sequence", " ".join(str(x) for x in new)))


    return edits, warns


# ---- is a slot in use? -----------------------------------------------------------------------------------------------------------
def scan_paint(pkg, slot, cap=PAINT_SCAN_CAP):
    """True if any triangle of any mesh entry is painted with this slot (a byte scan for paint_color="..."; no XML parse, nothing kept)."""
    seen, read = set(), 0
    for name in pkg.names:
        if not name.endswith(".model") or not (name.startswith("3D/") or name == "3D/3dmodel.model"):
            continue
        carry = b""
        with pkg.z.open(pkg.index[name]) as f:
            while True:
                chunk = f.read(CHUNK)
                if not chunk:
                    break
                read += len(chunk)
                if read > cap:
                    raise PatchRefused(["The meshes are too large to scan for painted areas (more than %d GiB of text). The last slot cannot be checked, so it is not removed." % (cap // 1024 ** 3)])
                buf = carry + chunk
                end = 0
                for m in PAINT_RE.finditer(buf):
                    end = m.end()
                    val = m.group(1)
                    if val in seen:
                        continue
                    seen.add(val)
                    states, _zero = paint_states(val.decode("ascii", "replace"))
                    if slot in states:
                        return True
                cut = buf.rfind(b"paint_color=", end)
                carry = buf[cut:] if cut >= 0 else buf[-16:]
    return False


def _num(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def slot_use(src, slot, scan=True, ignore_sequence=False):
    """Reasons why slot (1-based) is in use. Empty list = free. Each reason names the kind of use, never a value."""
    why = []
    cfg = src.cfg
    for o in src.ms["objects"]:
        nm = o["meta"].get("name", "?")
        if _num(o["meta"].get("extruder")) == slot:
            why.append("object %r has this filament" % nm)
        for k in SLOT_KEYS:
            if _num(o["meta"].get(k)) == slot:
                why.append("object %r: %s = %d" % (nm, k, slot))
        for p in o["parts"]:
            if p["subtype"] in ("negative_part", "support_enforcer", "support_blocker"):
                continue
            if _num(p["meta"].get("extruder")) == slot:
                why.append("part %r of object %r has this filament" % (p["meta"].get("name", "?"), nm))
            for k in SLOT_KEYS:
                if _num(p["meta"].get(k)) == slot:
                    why.append("part %r of object %r: %s = %d" % (p["meta"].get("name", "?"), nm, k, slot))
    for k in SLOT_KEYS:
        if _num(cfg.get(k)) == slot:
            why.append("project setting %s = %d" % (k, slot))
    pkg = src.pkg
    gname = "Metadata/custom_gcode_per_layer.xml"
    if pkg.has(gname):
        d = pkg.raw(gname) or b""
        for m in re.finditer(rb"<layer\b[^>]*/>", d):
            t = re.search(rb'\btype="(\d+)"', m.group(0))
            e = re.search(rb'\bextruder="(\d+)"', m.group(0))
            if t and e and int(t.group(1)) in (0, 2) and int(e.group(1)) == slot:
                why.append("a custom G-code %s at a layer uses this filament" % ("colour change" if t.group(1) == b"0" else "tool change"))
                break
    rname = "Metadata/layer_config_ranges.xml"
    if pkg.has(rname):
        d = pkg.raw(rname) or b""
        keys = b"extruder|" + b"|".join(k.encode() for k in SLOT_KEYS)
        for m in re.finditer(rb'<option\s+opt_key="(' + keys + rb')"\s*>\s*(\d+)\s*<', d):
            if int(m.group(2)) == slot:
                why.append("a height range sets %s = %d" % (m.group(1).decode(), slot))
                break
    sname = "Metadata/filament_sequence.json"
    if pkg.has(sname) and not ignore_sequence:
        try:
            j = json.loads((pkg.raw(sname) or b"{}").decode("utf-8"))
            hit = [pk for pk, pv in j.items() if isinstance(pv, dict) and any(
                slot in [x for x in (pv.get(lk) or []) if isinstance(x, int)] for lk in ("sequence", "filament_sequence"))]
            if hit:
                why.append("filament_sequence.json (%s) lists this filament" % hit[0])
        except (ValueError, AttributeError, TypeError):
            why.append("filament_sequence.json cannot be read, so its use of this filament is unknown")
    if scan and not why and scan_paint(pkg, slot):
        why.append("a part is painted with this filament")
    return why


def sequence_edit(raw, n, add=0, clear=False):
    """filament_sequence.json: clear = empty every list (the unsliced form); add = extend a per-filament optimal_assignment of n entries by 0.
    Returns new bytes or None when nothing changes. Studio writes this entry as compact JSON with sorted keys; the file's own order is kept."""
    try:
        j = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None
    if not isinstance(j, dict):
        return None
    changed = False
    for pk, pv in j.items():
        if not isinstance(pv, dict):
            continue
        for lk in ("sequence", "filament_sequence", "nozzle_sequence", "optimal_assignment"):
            lst = pv.get(lk)
            if not isinstance(lst, list) or not lst:
                continue
            if clear:
                pv[lk] = []
                changed = True
            elif lk == "optimal_assignment" and add and len(lst) == n:
                pv[lk] = lst + [0] * add
                changed = True
    if not changed:
        return None
    return json.dumps(j, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# ---- rule 5: the root model's metadata block ----------------------------------------------------------------------------------------
META_RE = re.compile(rb'(?P<pre>[ \t]*)<metadata\s+name="(?P<k>[^"]*)"\s*(?:/>|>(?P<v>.*?)</metadata>)(?P<post>[ \t]*(?:\r?\n)?)', re.S)
LOCAL_KEYS = {"Region"}                      # the local app's country code (GUI/Plater.cpp)
KEEP_KEYS = ("Title", "Designer", "DesignerCover", "Description", "License", "Origin", "Copyright", "CopyRight", "ProfileTitle", "ProfileCover",
              "ProfileDescription", "ProfileUserId", "ProfileUserName", "DesignModelId", "DesignProfileId", "DesignRegion", "DesignerUserId")


def _text(v):
    if v is None:
        return ""
    from xml.sax.saxutils import unescape
    return unescape(v.decode("utf-8", "replace"), {"&quot;": '"', "&apos;": "'"}).strip()


def privacy_head(head):
    """Rule 5 on the bytes before <resources. Returns (new head, removed [(key, why, exact bytes)], kept [key]). Each <metadata> element is kept
    whole or removed whole; every other byte stays. Removed: a value that holds an absolute path; DesignerUserId when Designer is empty (Studio
    filled in the local user's id: GUI/Plater.cpp); Region. Kept: the designer's credit and licence, the profile author's public ids."""
    items = list(META_RE.finditer(head))
    vals = {m.group("k").decode("utf-8", "replace"): _text(m.group("v")) for m in items}
    removed, drop = [], []
    for m in items:
        k = m.group("k").decode("utf-8", "replace")
        v = _text(m.group("v"))
        why = None
        if k in LOCAL_KEYS and v:
            why = "the local app's country code"
        elif k == "DesignerUserId" and v and not vals.get("Designer"):
            why = "the local user's id (Studio filled it in; no designer is named)"
        elif v and PATH_RE.search(v) and k not in KEEP_KEYS:          # the designer's credit is never removed (D4); patch.py warns about a path in it
            why = "holds an absolute path"
        if why:
            drop.append(m)
            removed.append((k, why, m.group(0)))
    out, at = [], 0
    for m in drop:
        out.append(head[at:m.start()])
        at = m.end()
    out.append(head[at:])
    new = b"".join(out)
    kept = [k for k in KEEP_KEYS if vals.get(k) and k not in {r[0] for r in removed}]
    return new, removed, kept


def kept_with_path(head):
    """Kept (credit) keys whose value holds an absolute path: they stay as they are, the run warns and names the key."""
    out = []
    for m in META_RE.finditer(head):
        k = m.group("k").decode("utf-8", "replace")
        v = _text(m.group("v"))
        if k in KEEP_KEYS and v and PATH_RE.search(v):
            out.append(k)
    return out


def head_without(head, removed_lines):
    """The head with exactly these removed element texts taken out (the verifier recomputes the expected head from the plan with this)."""
    for text in removed_lines:
        i = head.find(text)
        if i < 0:
            return None
        head = head[:i] + head[i + len(text):]
    return head


def source_file_paths(src):
    """Edits [(("part", object id, part id), "source_file", base name)] for every part whose source_file holds a path."""
    edits = []
    for o in src.ms["objects"]:
        for p in o["parts"]:
            v = p["meta"].get("source_file")
            if v and (PATH_RE.search(v) or "/" in v or "\\" in v):
                base = re.split(r"[\\/]", v.rstrip("\\/"))[-1]
                edits.append((("part", o["id"], p["id"]), "source_file", base))
    return edits
