"""Wipe (prime) tower position, after PartPlateList::set_default_wipe_tower_pos_for_plate and
PartPlate::estimate_wipe_tower_size (Studio 2.8.2 / 2.9.1, PartPlate.cpp). Used for multi-color jobs;
with one filament no tower is printed and the value only has to be inside the bed.
Unverified against a real multi-color save until the 3-color fixture exists."""
import math
import re

WIPE_TOWER_MARGIN = 15.0            # libslic3r.h
DEFAULT_X, DEFAULT_Y = 165.0, 250.0  # PartPlate.cpp
MIN_DEPTH_PER_HEIGHT = [(5.0, 5.0), (100.0, 20.0), (250.0, 40.0), (350.0, 60.0)]   # WipeTower.cpp


def limit_depth_by_height(h):
    t = MIN_DEPTH_PER_HEIGHT
    for i, (hh, dd) in enumerate(t):
        if hh >= h:
            return dd
        if i == len(t) - 1:
            return dd
        nh, nd = t[i + 1]
        if nh > h:
            return dd + (h - hh) / (nh - hh) * (nd - dd)
    return t[-1][1]


def estimate_size(cfg, n, max_height, extruders=1, conservative=False):
    """(width, depth) of the prime tower. cfg: normalized project config (strings).
    conservative: size for the 45 mm3 default prime volume too. A real 2.8.2 slice of a file holding 30 printed a tower
    as big as the 45 estimate (G-code carries filament_prime_volume = 45; tests/studio_slice.py T13), so the overlap
    check must reserve that much."""
    lh = float(cfg["layer_height"])
    gap = float(cfg["prime_tower_infill_gap"].rstrip("%")) / 100.0
    rib_wall = cfg.get("prime_tower_rib_wall", "1") == "1"
    rib_w = float(cfg["prime_tower_rib_width"])
    extra_rib = float(cfg.get("prime_tower_extra_rib_length", "0"))
    width = float(cfg["prime_tower_width"])
    need = cfg.get("timelapse_type") == "1" or cfg.get("enable_wrapping_detection") == "1"
    size = max(n, 2)                          # a new plate is sized for two filaments (init_pos)
    vol_each = max(float(v) for v in cfg["filament_prime_volume"])
    if conservative and cfg.get("prime_volume_mode") != "Saving":
        vol_each = max(vol_each, 45.0)
    if cfg.get("prime_volume_mode") == "Saving":
        vol_each = 15.0
    volume = vol_each * (size if extruders == 2 else size - 1)
    if rib_wall:
        depth = math.sqrt(volume / lh * gap)
        if need or size > 1:
            vdepth = depth
            depth = max(limit_depth_by_height(max_height), depth)
            rib_w = min(rib_w, depth / 2)
            depth = rib_w / math.sqrt(2) + max(depth + extra_rib, vdepth)
            return depth, depth
        return 0.0, 0.0
    depth = volume / (lh * width) * gap
    if need or depth > 1e-4:
        depth = max(limit_depth_by_height(max_height), depth)
    return width, depth


def position(cfg, n, max_height, bed):
    """bed = (xmin, ymin, xmax, ymax) of the printable area. Returns (x, y) in plate coordinates."""
    w, d = estimate_size(cfg, n, max_height)
    x, y = DEFAULT_X, DEFAULT_Y
    xmin, _ymin, xmax, ymax = bed
    if x + WIPE_TOWER_MARGIN + w > xmax:
        x = xmax - w - WIPE_TOWER_MARGIN
    elif x < WIPE_TOWER_MARGIN + xmin:
        x = WIPE_TOWER_MARGIN + xmin
    if y + WIPE_TOWER_MARGIN + d > ymax:
        y = ymax - d - WIPE_TOWER_MARGIN
    elif y < WIPE_TOWER_MARGIN:
        y = WIPE_TOWER_MARGIN
    return round(x, 3), round(y, 3)


def tower_boxes(cfg, n, max_height, bed, plate):
    """Footprints (x0, y0, x1, y1) in plate coordinates the prime tower can occupy on a plate, or [] when there is no tower.
    Each box is (x0, y0, x1, y1, source), source "file" or "derived". Two candidates: the position written in the file (wipe_tower_x/y, index plate-1) and the position Studio derives itself
    (set_default_wipe_tower_pos_for_plate), because Studio may recompute it on load. Sizes use the conservative estimate."""
    enabled = str(cfg.get("enable_prime_tower", "1")).lower() in ("1", "true")
    if n < 2 or not enabled:
        return []
    w, d = estimate_size(cfg, n, max_height, conservative=True)
    cands = []
    try:
        fx, fy = float(cfg["wipe_tower_x"][plate - 1]), float(cfg["wipe_tower_y"][plate - 1])
    except (KeyError, IndexError, ValueError, TypeError):
        fx = fy = None
    if fx is not None:
        if not (math.isfinite(fx) and math.isfinite(fy)):
            raise ValueError("wipe tower position is not a finite number")
        cands.append((fx, fy))
    cands.append(position(cfg, n, max_height, bed))
    return [(x, y, x + w, y + d, src) for (x, y), src in zip(cands, ["file", "derived"][-len(cands):])]


# ---- which plates have a prime tower, and where Studio puts it ----------------------------------------------------
def _on(v):
    return str(v).lower() in ("1", "true")


def _int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def paint_states(s, limit=1 << 22):
    """The extruder states of one `paint_color` attribute (TriangleSelector::serialize, Model.cpp FacetsAnnotation::get_triangle_as_string).
    The text is hex, the first character holds the last 4 bits; a node is 2 bits split sides, then either 2 bits special side and the
    children, or a leaf of 2 bits (state 0-2) or 11 followed by 4-bit groups (state 3 + the groups, 1111 = +15 and go on).
    Returns (set of states, True if a leaf of state 0 is in it)."""
    try:
        bits = []
        for ch in reversed(s.strip()):
            v = int(ch, 16)
            bits.extend(((v >> i) & 1) for i in range(4))
            if len(bits) > limit:
                break
    except ValueError:
        return set(), False
    pos, states, zero, todo = 0, set(), False, 1
    try:
        while todo > 0:
            todo -= 1
            split = bits[pos] | (bits[pos + 1] << 1)
            pos += 2
            if split:
                pos += 2
                todo += split + 1
                continue
            n = bits[pos] | (bits[pos + 1] << 1)
            pos += 2
            if n == 3:
                while True:
                    v = bits[pos] | (bits[pos + 1] << 1) | (bits[pos + 2] << 2) | (bits[pos + 3] << 3)
                    pos += 4
                    n += v
                    if v != 15:
                        break
            if n == 0:
                zero = True
            else:
                states.add(n)
    except IndexError:
        pass
    return states, zero


class PaintAcc:
    """The painted filaments of one mesh: add(paint_color attribute or None) once per triangle."""
    def __init__(self):
        self.states, self.zero, self._seen = set(), False, {}

    def add(self, attr):
        if not attr:
            self.zero = True
            return
        r = self._seen.get(attr)
        if r is None:
            r = self._seen[attr] = paint_states(attr)
            self.states |= r[0]
            self.zero = self.zero or r[1]

    def result(self):
        return (frozenset(self.states), self.zero)


def plate_filaments(cfg, objects, tool_changes=()):
    """The filaments (1-based slot numbers) a plate prints with, after ModelVolume::get_extruders, Print::support_material_extruders and
    Print::extruders(true) (Model.cpp, Print.cpp, both tags). objects: one dict per instance on the plate,
    {"meta": object metadata, "parts": [{"subtype", "meta", "paint": (states, has_unpainted) or None}]}. tool_changes: filament numbers of
    the ToolChange layers in custom_gcode_per_layer.xml. Painted areas, wall / infill filament keys, support filaments are all counted."""
    n = len(cfg.get("filament_colour") or []) or 1
    ver = [_int(x) for x in str(cfg.get("version", "")).split(".")[:2]]
    glob = ver >= [2, 9]                                    # the project-wide wall / infill filament counts only from 2.9 (Model.cpp)
    used = set()
    for ob in objects:
        om = ob["meta"]
        obj_ext = set()
        for p in ob["parts"] or [{"subtype": "normal_part", "meta": {}, "paint": None}]:
            if p["subtype"] in ("negative_part", "support_enforcer", "support_blocker"):
                continue
            pm = p["meta"]
            ext = _int(pm.get("extruder"))
            if ext == 0:
                ext = _int(om.get("extruder"), 1) or 1
            painted, zero = p.get("paint") or (frozenset(), True)
            obj_ext |= set(painted)
            if (p["subtype"] != "normal_part" or zero) and ext > 0:
                obj_ext.add(ext)
            for key in ("wall_filament", "solid_infill_filament", "sparse_infill_filament"):
                v = pm.get(key)
                if v is None:
                    v = om.get(key)
                if v is None and glob:
                    g = _int(cfg.get(key))
                    v = g if 0 < g <= n else 0
                if _int(v) > 0:
                    obj_ext.add(_int(v))
        used |= obj_ext
        has = _on(om.get("enable_support", cfg.get("enable_support", "0"))) or _int(om.get("enforce_support_layers", cfg.get("enforce_support_layers", "0"))) > 0 \
            or _int(om.get("raft_layers", cfg.get("raft_layers", "0"))) > 0
        if has:
            for key in ("support_filament", "support_interface_filament"):
                f = _int(om.get(key, cfg.get(key, "0")))
                if f > 0:
                    used.add(f if f <= n else 1)             # an id above the filament count falls back to filament 1
    used |= {t for t in tool_changes if 0 < t <= n}
    return used


def has_wipe_tower(cfg, used):
    """Whether a plate gets a prime tower: GLCanvas3D.cpp (2.8.2 :3906, 2.9.1 :4149) needs enable_prime_tower and (wrapping detection, or smooth
    timelapse, or more than one used filament on the plate); Print::has_wipe_tower() (Print.cpp) adds that spiral mode has none. Wrapping
    detection counts as a tower even with an empty exclude area (the GUI turns the tower on for the switch alone; conservative)."""
    if not _on(cfg.get("enable_prime_tower", "1")):
        return False
    if _on(cfg.get("enable_wrapping_detection", "0")):
        return True
    if str(cfg.get("timelapse_type", "0")) == "1":
        return True
    return not _on(cfg.get("spiral_mode", "0")) and len(used) > 1


def tower_at(cfg, n, max_height, bed, plate):
    """The prime tower of one plate as Studio places it when it loads the file. n: the number of filaments the plate prints with (Studio sizes the
    tower for the plate's used filaments when it clamps, PartPlate.cpp estimate_wipe_tower_size with plate_extruder_size 0).
    Returns (source, raw box, box after Studio's clamp), boxes (x0, y0, x1, y1) in plate coordinates. source is "file" when the file has a
    wipe_tower_x/y entry for the plate (Studio keeps it and clamps it into the plate: set_default_wipe_tower_pos_for_plate, keep_existing),
    else "default" (the position Studio derives itself). The clamp and the raw box use Studio's own size estimate; the box after the clamp
    keeps the corner the clamp gives and is as big as the conservative estimate (see estimate_size). ValueError for a position that is not finite."""
    w, d = estimate_size(cfg, n, max_height, conservative=True)
    w0, d0 = estimate_size(cfg, n, max_height)
    xmin, ymin, xmax, ymax = bed
    try:
        fx, fy = float(cfg["wipe_tower_x"][plate - 1]), float(cfg["wipe_tower_y"][plate - 1])
    except (KeyError, IndexError, TypeError):
        fx = fy = None
    if fx is None:
        x, y = position(cfg, n, max_height, bed)
        return "default", (x, y, x + w, y + d), (x, y, x + w, y + d)
    if not (math.isfinite(fx) and math.isfinite(fy)):
        raise ValueError("wipe tower position is not a finite number")
    x, y = fx, fy
    if x + WIPE_TOWER_MARGIN + w0 > xmax:
        x = xmax - w0 - WIPE_TOWER_MARGIN
    elif x < WIPE_TOWER_MARGIN + xmin:
        x = WIPE_TOWER_MARGIN + xmin
    if y + WIPE_TOWER_MARGIN + d0 > ymax:
        y = ymax - d0 - WIPE_TOWER_MARGIN
    elif y < WIPE_TOWER_MARGIN + ymin:
        y = WIPE_TOWER_MARGIN + ymin
    return "file", (fx, fy, fx + w0, fy + d0), (x, y, x + w, y + d)
