"""G-code check for [calibration] type = "mvs". Called by tests/forward_check.py:  checks(raw, f3mf, gcodes) -> [(name, ok, detail)].

raw = the job TOML dict, f3mf = the built 3MF, gcodes = the sliced plate G-code paths.
For each band: the feed rate F (mm/min) of the outer-wall moves (`; FEATURE: Outer wall`, G1 ... F<n>) in the layers of the band
(by `; Z_HEIGHT:`) must equal 60 * flow / area, where area = layer * (width - layer * (1 - pi/4)) is Studio's line cross-section.
The expected value comes from the job file and the preset, never from the builder's output.
Tolerance: 0.5 mm/min. The builder writes the speed with 2 decimals (0.005 mm/s of rounding, 0.3 mm/min after the factor of 60) and
Studio writes F with one decimal (0.05 mm/min); the largest difference seen on the example is 0.3 mm/min. Every check prints how many moves it measured;
0 measured is reported as NOT CHECKED (ok = False).
Run alone: python3 -I tests/calib_check_mvs.py job.toml file.3mf plate_1.gcode
"""
import math, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
TOL = 0.5   # mm/min

_F = re.compile(r"\bF([0-9.]+)")
_Z = re.compile(r"^; Z_HEIGHT: *([0-9.]+)")


def _plan(raw, ctx_values):
    """[(flow, band index)] with the expected outer-wall F per band, from the job dict and the layer height / width."""
    cal = raw["calibration"] if "calibration" in raw else raw
    if "flows" in cal:
        flows = [float(x) for x in cal["flows"]]
    else:
        a, b, s = float(cal.get("start", 10)), float(cal.get("end", 30)), float(cal.get("step", 2))
        flows = [round(a + i * s, 6) for i in range(int((b - a) / s + 1e-9) + 1)]
    bh = float(cal.get("band_height", 5))
    lh, w = ctx_values
    area = lh * (w - lh * (1 - math.pi / 4))
    return flows, bh, [60.0 * f / area for f in flows], lh


def _values(raw):
    """(layer height, outer wall width) from the job's settings, else the process preset of the job."""
    from bambu3mf import profiles
    st = dict(raw.get("settings", {}))
    ver = {"2.8.2": "2.8.2.61", "2.9.1": "2.9.1.70", "2.9.2": "2.9.2.62"}.get(str(raw.get("studio", "2.9.2")), "2.9.2.62")
    ps = profiles.ProfileSet(ver, None)
    p = ps.resolve("process", raw["process"])[0]
    g = lambda k, d: float(str(st.get(k, (p.get(k) if not isinstance(p.get(k), list) else p[k][0]) if k in p else d)).rstrip("%"))
    lh, w = g("layer_height", 0.2), g("outer_wall_line_width", 0.42)
    if w == 0:
        w = 1.125 * 0.4
    return lh, w


def measure(gcode_path):
    """{z top of layer: [F of every outer-wall G1 move with an F or inheriting the last F]} from one G-code file."""
    out, z, feature, cur_f = {}, 0.0, "", None
    with open(gcode_path, errors="replace") as fh:
        for line in fh:
            m = _Z.match(line)
            if m:
                z = round(float(m.group(1)), 4)
                continue
            if line.startswith("; FEATURE:"):
                feature = line[10:].strip()
                continue
            if line.startswith("G1 ") or line.startswith("G0 "):
                m = _F.search(line)
                if m:
                    cur_f = float(m.group(1))
                if feature == "Outer wall" and cur_f is not None and " E" in line and " X" in line:
                    out.setdefault(z, []).append(cur_f)
    return out


def checks(raw, f3mf, gcodes):
    res = []
    if not gcodes:
        return [("mvs: a sliced G-code file exists", False, "NOT CHECKED: no G-code file given (measured 0)")]
    cal = raw.get("calibration", {})
    lh, w = _values(raw)
    flows, bh, want, lh = _plan(raw, (lh, w))
    layers = measure(gcodes[0])
    res.append(("mvs: outer-wall moves found in the G-code (%d layers)" % len(layers), len(layers) > 0, "NOT CHECKED: measured 0 layers" if not layers else ""))
    for i, (f, exp) in enumerate(zip(flows, want)):
        lo, hi = i * bh, (i + 1) * bh
        zs = [z for z in sorted(layers) if lo + 1e-6 < z <= hi + 1e-6 and z > lh + 1e-6]       # the first layer prints at initial_layer_speed
        moves = [x for z in zs for x in layers[z]]
        name = "mvs: band %d (%g mm3/s, z %g-%g mm) outer-wall F = %.1f mm/min (%.2f mm/s)" % (i + 1, f, lo, hi, exp, exp / 60.0)
        if not moves:
            res.append((name, False, "NOT CHECKED: measured 0 moves"))
            continue
        # the moves of a band must all carry the expected F (a layer starts at the band speed; tabs print at the same speed)
        bad = [x for x in moves if abs(x - exp) > TOL]
        res.append((name + " (%d moves in %d layers)" % (len(moves), len(zs)), not bad,
                    "" if not bad else "%d of %d moves differ, first F%s" % (len(bad), len(moves), bad[0])))
    return res


if __name__ == "__main__":
    from bambu3mf.job import tomllib                  # tomllib, or the vendored tomli on Python 3.8-3.10
    with open(sys.argv[1], "rb") as fh:
        raw = tomllib.load(fh)
    bad = 0
    for name, ok, detail in checks(raw, sys.argv[2], sys.argv[3:]):
        print(("ok   " if ok else "FAIL ") + name + ((" - " + detail) if detail else ""))
        bad += (not ok)
    sys.exit(1 if bad else 0)
