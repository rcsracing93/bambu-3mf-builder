"""Purge (flush) volumes between filaments, ported from Studio's FlushVolCalc.cpp, FlushVolPredictor.cpp and
Plater.cpp (auto_calc_flushing_volumes_internal, get_min_flush_volumes). Same in 2.8.2 and 2.9.1.

Studio recomputes the matrix with this when filament colors change (app setting auto_calculate_flush, default "all").
The builder writes the same numbers so a fresh file matches what the GUI would show after picking the colors.
Float32 steps are emulated where the C++ uses float. Compared with real 2.8.2 and 2.9.1 saves (tests/data/four-color-autocalc-*.3mf).
"""
import json
import math
import os
import struct

G_MAX = 900
G_MIN_FROM_SUPPORT = 700
G_TO_SUPPORT = 230
DATASET_FILES = {0: "flush_data_standard.txt", 1: "flush_data_dual_standard.txt", 2: "flush_data_dual_highflow.txt"}


def f32(x):
    return struct.unpack("f", struct.pack("f", x))[0]


def _rgb2hsv(r, g, b):
    cmax, cmin = max(r, g, b), min(r, g, b)
    d = f32(cmax - cmin)
    if abs(d) < 0.001:
        h = 0.0
    elif cmax == r:
        h = f32(60.0 * math.fmod(f32((g - b) / d), 6.0))
    elif cmax == g:
        h = f32(60.0 * (f32((b - r) / d) + 2))
    else:
        h = f32(60.0 * (f32((r - g) / d) + 4))
    s = 0.0 if abs(cmax) < 0.001 else f32(d / cmax)
    return h, s, cmax


def _lumi(r, g, b):
    return f32(r * 0.3 + g * 0.59 + b * 0.11)


def _rad(deg):
    return f32(deg / 180.0 * math.pi)


def _delta_hs(h1, s1, v1, h2, s2, v2):
    a, b = _rad(h1), _rad(h2)
    dx = f32(math.cos(a) * s1 * v1 - math.cos(b) * s2 * v2)
    dy = f32(math.sin(a) * s1 * v1 - math.sin(b) * s2 * v2)
    return min(1.2, f32(math.sqrt(dx * dx + dy * dy)))


def calc_flush_vol_rgb(src, dst):
    sr, sg, sb = (f32(c / 255.0) for c in src)
    dr, dg, db = (f32(c / 255.0) for c in dst)
    fh, fs, fv = _rgb2hsv(sr, sg, sb)
    th, ts, tv = _rgb2hsv(dr, dg, db)
    hs_dist = _delta_hs(fh, fs, fv, th, ts, tv)
    from_l, to_l = _lumi(sr, sg, sb), _lumi(dr, dg, db)
    if to_l >= from_l:
        lumi_flush = f32(math.pow(f32(to_l - from_l), 0.7) * 560.0)
    else:
        lumi_flush = f32((from_l - to_l) * 80.0)
        inter = f32(0.67 * tv + 0.33 * fv)
        hs_dist = min(inter, hs_dist)
    hs_flush = f32(230.0 * hs_dist)
    v = f32(math.sqrt(hs_flush * hs_flush + lumi_flush * lumi_flush - 2 * hs_flush * lumi_flush * math.cos(_rad(120.0))))
    return int(max(v, 60.0))


# ---- ΔE2000 and dataset lookup (FlushVolPredictor.cpp) -------------------------------------------------
def _rgb2lab(c):
    def gamma(x):
        return ((x + 0.055) / 1.055) ** 2.4 if x > 0.04045 else x / 12.92
    R, G, B = (gamma(v / 255.0) * 100 for v in c)
    x = 0.412453 * R + 0.357580 * G + 0.180423 * B
    y = 0.212671 * R + 0.715160 * G + 0.072169 * B
    z = 0.019334 * R + 0.119193 * G + 0.950227 * B

    def f(t):
        return t ** (1.0 / 3.0) if t > f32(0.008856) else 7.787 * t + 0.137931
    xn, yn, zn = f(x / 95.0489), f(y / 100.0), f(z / 108.8840)
    return 116.0 * yn - 16.0, 500.0 * (xn - yn), 200.0 * (yn - zn)


def delta_e2000(c1, c2):
    l1, a1, b1 = _rgb2lab(c1)
    l2, a2, b2 = _rgb2lab(c2)
    p25 = 25.0 ** 7
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2)
    cm7 = ((C1 + C2) / 2.0) ** 7
    G = 0.5 * (1 - math.sqrt(cm7 / (cm7 + p25)))
    pa1, pa2 = (1.0 + G) * a1, (1.0 + G) * a2
    pc1, pc2 = math.hypot(pa1, b1), math.hypot(pa2, b2)

    def hue(a, b):
        if a == 0 and b == 0:
            return 0.0
        h = math.atan2(b, a)
        return h + 2 * math.pi if h < 0 else h
    h1, h2 = hue(pa1, b1), hue(pa2, b2)
    dL, dC = l2 - l1, pc2 - pc1
    pm = pc1 * pc2
    if pm == 0:
        dH = 0.0
    else:
        dH = h2 - h1
        if dH < -math.pi:
            dH += 2 * math.pi
        elif dH > math.pi:
            dH -= 2 * math.pi
        dH = 2 * math.sqrt(pm) * math.sin(dH / 2.0)
    Lm, Cm = (l1 + l2) / 2.0, (pc1 + pc2) / 2.0
    hsum = h1 + h2
    if pm == 0:
        Hm = hsum
    elif abs(h1 - h2) <= math.pi:
        Hm = hsum / 2
    else:
        Hm = (hsum + 2 * math.pi) / 2.0 if hsum < 2 * math.pi else (hsum - 2 * math.pi) / 2.0
    d2r = math.radians
    T = 1 - 0.17 * math.cos(Hm - d2r(30)) + 0.24 * math.cos(2 * Hm) + 0.32 * math.cos(3 * Hm + d2r(6)) - 0.2 * math.cos(4 * Hm - d2r(63))
    dtheta = d2r(30) * math.exp(-(((Hm - d2r(275)) / d2r(25)) ** 2))
    cm7b = Cm ** 7
    RC = 2 * math.sqrt(cm7b / (cm7b + p25))
    lm2 = (Lm - 50) ** 2
    SL = 1 + (0.015 * lm2) / math.sqrt(20 + lm2)
    SC = 1 + 0.045 * Cm
    SH = 1 + 0.015 * Cm * T
    RT = -math.sin(2 * dtheta) * RC
    t1, t2, t3 = dL / SL, dC / SC, dH / SH
    return f32(math.sqrt(t1 * t1 + t2 * t2 + t3 * t3 + RT * t2 * t3))


def _hex(s):
    return int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16)


class Tables:
    def __init__(self, flush_dir):
        self.dir = flush_dir
        self._sets = {}
        pair = os.path.join(flush_dir, "flush_data_material_pair.json")
        self.pairs = {}
        if os.path.isfile(pair):
            with open(pair) as f:
                for it in json.load(f).get("pairs", []):
                    if it.get("from") and it.get("to") and "purge" in it:
                        self.pairs[(it["from"], it["to"])] = float(it["purge"])

    def dataset(self, n):
        if n not in self._sets:
            colors, table = [], {}
            path = os.path.join(self.dir, DATASET_FILES.get(n, ""))
            if os.path.isfile(path):
                with open(path) as f:
                    lines = f.read().split("\n")
                colors = [_hex(t) for t in lines[1].split()]
                for ln in lines[3:]:
                    p = ln.split()
                    if len(p) == 3:
                        table[(_hex(p[0]), _hex(p[1]))] = float(p[2])
            self._sets[n] = (colors, table)
        return self._sets[n]

    def predict(self, dataset, src, dst):
        colors, table = self.dataset(dataset)
        sf = next((c for c in colors if delta_e2000(c, src) <= 5.0), None)
        st = next((c for c in colors if delta_e2000(c, dst) <= 5.0), None)
        if sf is None or st is None:
            return None
        return table.get((sf, st))


def calc_flush_vol(tables, src_id, dst_id, src, dst, min_vol, dataset, max_vol=G_MAX):
    """src/dst: (a, r, g, b)."""
    sa, sr, sg, sb = src
    da, dr, dg, db = dst
    if sa == 0:
        sr = sg = sb = 255
    if da == 0:
        dr = dg = db = 255
    s, d = (sr, sg, sb), (dr, dg, db)
    hit = tables.predict(dataset, s, d)
    if dataset == 0:
        # calc_flush_vol_rgb returns the table value directly for dataset 0; calc_flush_vol then adds the minimum
        vol = hit if hit is not None else calc_flush_vol_rgb(s, d)
        vol += min_vol
    elif hit is not None:
        vol = hit
    else:
        vol = calc_flush_vol_rgb(s, d)
        # Studio compares 0-255 channel values with 0-1 thresholds here (get_luminance on unsigned char)
        if _lumi(sr, sg, sb) > 180.0 / 255.0 and _lumi(dr, dg, db) < 75.0 / 255.0:
            vol *= 1.3
        vol += min_vol
    predicted = min(int(vol), max_vol)
    measured = tables.pairs.get((src_id, dst_id))
    if measured is not None:
        return min(max(predicted, int(measured)), max_vol)
    return predicted


def _first(v, default):
    if isinstance(v, list):
        return v[0] if v else default
    return v if v not in (None, "") else default


def min_flush_volumes(cfg, nozzle_id=0):
    """get_min_flush_volumes (Plater.cpp). cfg: the built project config (strings)."""
    n = len(cfg["filament_colour"])
    # Studio reads these from its full config, where every per-variant list is already cut to the active variant of the extruder
    # (PresetBundle::full_config, update_values_to_printer_extruders: extruder_type + nozzle_volume_type, e.g. "Direct Drive Standard").
    # A nil entry (nullable option, the preset does not set it) is the option's nil value: 255 for a bool, NaN for a float.
    active = "%s %s" % (_first(cfg.get("extruder_type"), "Direct Drive"), _first(cfg.get("nozzle_volume_type"), "Standard"))
    pv = cfg.get("printer_extruder_variant") or []
    pv = pv if isinstance(pv, list) else [pv]
    pidx = pv.index(active) if active in pv else nozzle_id
    fev = cfg.get("filament_extruder_variant") or []

    def per_filament(key, default):
        vals = cfg.get(key)
        if not isinstance(vals, list) or not vals:
            return [default] * n
        if len(vals) == n:
            return list(vals)
        if n and len(vals) % n == 0 and len(fev) == len(vals):
            w = len(vals) // n
            out = []
            for i in range(n):
                blk = fev[i * w:(i + 1) * w]
                out.append(vals[i * w + (blk.index(active) if active in blk else 0)])
            return out
        return [vals[i] if i < len(vals) else default for i in range(n)]

    def at(key, default):
        vals = cfg.get(key) or [default]
        vals = vals if isinstance(vals, list) else [vals]
        return vals[pidx] if pidx < len(vals) else vals[0]

    nozzle_volume = int(float(at("nozzle_volume", "0")))
    level = int(cfg.get("enable_long_retraction_when_cut", "0"))
    machine_on = at("long_retractions_when_cut", "0") == "1"
    pd = at("retraction_distances_when_cut", "18")
    pdist = {nozzle_id: float(pd) if pd != "nil" else 18.0}
    fon = [255 if x == "nil" else int(x) for x in per_filament("filament_long_retractions_when_cut", "0")]
    fdist = per_filament("filament_retraction_distances_when_cut", "18")
    out = []
    for i in range(n):
        extra = nozzle_volume
        rl = pdist[nozzle_id] if level and machine_on else 0
        if fon[i] == 0:
            rl = 0
        elif fon[i] == 1 and level == 2:
            rl = int(float(fdist[i])) if fdist[i] != "nil" else pdist[nozzle_id]
        extra = int(extra - math.pi * 1.75 * 1.75 / 4 * rl)
        out.append(extra)
    return out


def build_matrix(cfg, tables, mode="auto", nozzle_flush_dataset=0):
    """Return {'matrix': [...strings], 'vector': [...], 'multiplier', 'multiplier_fast'} for one nozzle.
    mode 'auto': Studio's auto-calculation; 'default': the unmodified new-project matrix (140 + 140)."""
    n = len(cfg["filament_colour"])
    ids = cfg["filament_ids"]
    vec = ["140"] * (2 * n)
    mat = [[0] * n for _ in range(n)]
    if mode == "default":
        for i in range(n):
            for j in range(n):
                mat[i][j] = 0 if i == j else 280
    else:
        mins = min_flush_volumes(cfg)
        sup = [x == "1" for x in cfg.get("filament_is_support", ["0"] * n)[:n]]
        rgb = []
        for c in cfg["filament_colour"]:
            r, g, b = _hex(c)
            rgb.append((255, r, g, b))
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                if sup[j]:
                    v = G_TO_SUPPORT
                else:
                    v = calc_flush_vol(tables, ids[i], ids[j], rgb[i], rgb[j], mins[i], nozzle_flush_dataset)
                    if sup[i]:
                        v = max(v, G_MIN_FROM_SUPPORT)
                mat[i][j] = v
    flat = ["%d" % mat[i][j] for i in range(n) for j in range(n)]
    return {"matrix": flat, "vector": vec}
