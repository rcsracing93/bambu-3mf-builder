"""G-code checks for [calibration] type = "flow" (called from tests/forward_check.py).
    checks(raw, f3mf, gcodes) -> [(name, ok, detail)]
raw = the job TOML as a dict, f3mf = the built 3MF, gcodes = the sliced plate G-code files.
1. Each object has its print_flow_ratio in Metadata/model_settings.config.
2. In the G-code, the extrusion per mm of OUTER WALL of each object (E per XY mm, from the second layer up) is proportional to its
   ratio: E/mm divided by the ratio is the same for all objects within 2 percent.
Studio marks an object with "; start printing object, unique label id: N" ... "; stop printing object, unique label id: N";
N is 100 plus the position of the object in model_settings.config (1-based). Every check says how many items it measured;
0 measured is NOT CHECKED, a failure."""
import math
import re
import zipfile

DEFAULT_RATIOS = [0.90, 0.95, 1.00, 1.05, 1.10]
TOLERANCE = 0.02
NC = "NOT CHECKED (0 measured)"


def _wanted(raw):
    cal = raw.get("calibration", raw)
    return [float(x) for x in cal.get("ratios", DEFAULT_RATIOS)]


def _object_ratios(f3mf):
    """[(object name, print_flow_ratio or None)] in file order."""
    with zipfile.ZipFile(f3mf) as z:
        xml = z.read("Metadata/model_settings.config").decode()
    out = []
    for blk in re.findall(r"<object id=.*?</object>", xml, re.S):
        name = re.search(r'<metadata key="name" value="([^"]*)"', blk)
        m = re.search(r'<metadata key="print_flow_ratio" value="([^"]+)"', blk)
        out.append((name.group(1) if name else "?", float(m.group(1)) if m else None))
    return out


def _outer_wall_e_per_mm(path):
    """{label id: (E total, XY mm total, segments)} for outer-wall moves from layer 2 up."""
    res = {}
    obj = feat = None
    layer = 0
    x = y = e = 0.0
    absolute_e = False
    with open(path, errors="replace") as fh:
        for line in fh:
            if line.startswith(";"):
                m = re.match(r"; (start|stop) printing object, unique label id: (\d+)", line)
                if m:
                    obj = int(m.group(2)) if m.group(1) == "start" else None
                elif line.startswith("; FEATURE:"):
                    feat = line[10:].strip()
                elif line.startswith("; Z_HEIGHT:"):        # one per layer, before its moves
                    layer += 1
                continue
            if line.startswith("M82"):
                absolute_e = True
            elif line.startswith("M83"):
                absolute_e = False
            elif line.startswith("G92"):
                m = re.search(r"\bE(-?[\d.]+)", line)
                if m:
                    e = float(m.group(1))
            if not (line.startswith("G1 ") or line.startswith("G2 ") or line.startswith("G3 ")):
                continue
            code = line.split(";")[0]
            f = {k: float(v) for k, v in re.findall(r"\b([XYEIJ])(-?\d*\.?\d+)", code)}
            nx, ny = f.get("X", x), f.get("Y", y)
            if "E" in f:
                de = (f["E"] - e) if absolute_e else f["E"]
                if absolute_e:
                    e = f["E"]
                if code.startswith("G1 "):
                    d = math.hypot(nx - x, ny - y)
                else:                                   # arc: length from the center offsets and the end point
                    cx, cy = x + f.get("I", 0.0), y + f.get("J", 0.0)
                    r = math.hypot(x - cx, y - cy)
                    a0, a1 = math.atan2(y - cy, x - cx), math.atan2(ny - cy, nx - cx)
                    da = (a1 - a0) % (2 * math.pi) if code.startswith("G3") else (a0 - a1) % (2 * math.pi)
                    d = r * (da if da else 2 * math.pi)
                if obj is not None and feat == "Outer wall" and layer >= 2 and de > 0 and d > 0:
                    t = res.setdefault(obj, [0.0, 0.0, 0])
                    t[0] += de
                    t[1] += d
                    t[2] += 1
            x, y = nx, ny
    return res


def checks(raw, f3mf, gcodes):
    out = []
    want = _wanted(raw)
    objs = _object_ratios(f3mf)
    got = [r for _n, r in objs]
    ok = len(objs) == len(want) and all(g is not None and abs(g - w) < 1e-6 for g, w in zip(got, want))
    out.append(("flow: print_flow_ratio of each object in model_settings.config (%d objects measured)" % len(objs) if objs else "flow: object ratios " + NC,
                ok and bool(objs), "want %s, got %s" % (want, got)))
    if not gcodes:
        out.append(("flow: outer-wall E per mm " + NC, False, "no G-code"))
        return out
    per = _outer_wall_e_per_mm(gcodes[0])
    rows = []
    for i, w in enumerate(want, 1):
        t = per.get(100 + i)
        rows.append((i, w, (t[0] / t[1]) if t else None, t[2] if t else 0))
    measured = [r for r in rows if r[2] is not None]
    if not measured:
        out.append(("flow: outer-wall E per mm " + NC, False, "no outer-wall extrusion found for labels 101-%d" % (100 + len(want))))
        return out
    k = [r[2] / r[1] for r in measured]
    mean = sum(k) / len(k)
    spread = (max(k) - min(k)) / mean
    detail = "; ".join("%s: %.5f E/mm (%d segments), per ratio %.5f" % ("%.3f" % r[1], r[2], r[3], r[2] / r[1]) for r in measured)
    out.append(("flow: outer-wall E/mm scales with the ratio within %d%% (%d of %d objects measured, spread %.2f%%)" % (
        TOLERANCE * 100, len(measured), len(want), spread * 100),
        len(measured) == len(want) and spread <= TOLERANCE, detail))
    return out


if __name__ == "__main__":
    import sys
    import tomllib
    with open(sys.argv[1], "rb") as fh:
        job = tomllib.load(fh)
    bad = 0
    for n, ok, d in checks(job, sys.argv[2], sys.argv[3:]):
        print(("ok   " if ok else "FAIL ") + n + "\n     " + d)
        bad += not ok
    sys.exit(1 if bad else 0)
