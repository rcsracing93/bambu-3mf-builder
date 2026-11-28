#!/usr/bin/env python3
"""G-code check for [calibration] type = "pa", used by tests/forward_check.py.
    checks(raw, f3mf, gcodes) -> [(name, ok, detail)]
raw = the job TOML as a dict, f3mf = the built 3MF, gcodes = the sliced plate G-code paths.
The expected values come from the job file (start / end / step or values, band_height), never from the builder's output.
A check that measured nothing is a failure: its detail starts with "NOT CHECKED".
Run on a file: python3 tests/calib_check_pa.py job.toml file.3mf plate_1.gcode"""
import os, re, sys, zipfile

DEFAULTS = {"start": 0.0, "end": 0.06, "step": 0.01, "band_height": 5.0}     # the documented defaults, written out on purpose


def expected_values(cal):
    if "values" in cal:
        return [round(float(v), 4) for v in cal["values"]]
    start, end, step = (float(cal.get(k, DEFAULTS[k])) for k in ("start", "end", "step"))
    if end < start:
        start, end = end, start
    n = int(round((end - start) / step, 6)) + 1
    return [round(start + i * step, 4) for i in range(n)]


def read_gcode(path):
    """(layers, m900): layers = [z top of each layer in order], m900 = [(line number, layer index, K, text)]. A layer starts at its
    '; Z_HEIGHT: z' line (Studio writes it after '; CHANGE_LAYER')."""
    layers, m900 = [], []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh, 1):
            m = re.match(r"; Z_HEIGHT: ([0-9.]+)", line)
            if m:
                layers.append(float(m.group(1)))
                continue
            m = re.match(r"\s*M900\b(.*)", line)
            if m:
                k = re.search(r"\bK(-?[0-9.]+)", m.group(1))
                m900.append((n, len(layers) - 1, float(k.group(1)) if k else None, line.strip()))
    return layers, m900


def checks(raw, f3mf, gcodes):
    out = []
    cal = raw.get("calibration", raw)
    vals = expected_values(cal)
    band = float(cal.get("band_height", DEFAULTS["band_height"]))
    # --- the 3MF: one M900 K step per value, in order ---------------------------------------------------------------
    with zipfile.ZipFile(f3mf) as z:
        xml = z.read("Metadata/custom_gcode_per_layer.xml").decode("utf-8", "replace") if "Metadata/custom_gcode_per_layer.xml" in z.namelist() else ""
    in_file = [float(x) for x in re.findall(r' gcode="M900 K(-?[0-9.]+)"', xml)]
    if not vals:
        out.append(("pa: values asked for in the job", False, "NOT CHECKED: the job asks for 0 values"))
    else:
        out.append(("pa: 3MF has one M900 K layer G-code step per band, in order (%d measured)" % len(in_file),
                    in_file == vals, "file %s, job %s" % (in_file, vals)))
    if not gcodes:
        out.append(("pa: sliced G-code", False, "NOT CHECKED: no G-code files"))
        return out
    layers, m900 = read_gcode(gcodes[0])
    if not layers:
        out.append(("pa: layers in the G-code", False, "NOT CHECKED: 0 '; Z_HEIGHT:' lines"))
        return out
    top = layers[-1]
    # --- one M900 K per band, at the first layer of the band, with the right value ---------------------------------------
    # band i starts at z = i * band: its first layer is the first one whose bottom (the previous layer top) is at or above that z.
    first = []
    for i in range(len(vals)):
        z0 = i * band
        idx = next((j for j in range(len(layers)) if (layers[j - 1] if j else 0.0) >= z0 - 1e-6), None)
        first.append(idx)
    ok_where, bad = 0, []
    for i, (v, idx) in enumerate(zip(vals, first)):
        hit = [m for m in m900 if m[1] == idx and m[2] is not None and abs(m[2] - v) < 1e-9]
        if idx is not None and len(hit) == 1:
            ok_where += 1
        else:
            bad.append("band %d K%g: layer %s has %d matching lines" % (i + 1, v, "none" if idx is None else "%d (z %g)" % (idx + 1, layers[idx]), len(hit)))
    out.append(("pa: M900 K<value> at the first layer of each band (%d of %d bands measured)" % (len(vals), len(vals)), ok_where == len(vals) and len(vals) > 0,
                "; ".join(bad) or "ok"))
    # --- the values asked for, in the order printed ------------------------------------------------------------------------
    got = [m[2] for m in m900]
    out.append(("pa: M900 values in the G-code are the values asked for, in order (%d M900 lines measured)" % len(m900),
                len(m900) > 0 and got == vals, "G-code %s, job %s" % (got, vals)))
    # --- no other M900 changes K inside a band -------------------------------------------------------------------------------
    extra = [m for m in m900 if m[1] not in first]
    out.append(("pa: no other M900 inside a band (%d M900 lines measured, %d layers)" % (len(m900), len(layers)),
                len(m900) > 0 and not extra, "extra: %s" % [(m[0], m[3]) for m in extra][:5] if extra else "ok"))
    # --- the tower reaches the last band ---------------------------------------------------------------------------------------
    need = len(vals) * band
    out.append(("pa: the print is as tall as the bands (%g mm measured, needs %g mm)" % (top, need), top >= need - 0.25 and len(vals) > 0, "top z %g" % top))
    return out


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
    from bambu3mf.job import tomllib
    with open(sys.argv[1], "rb") as fh:
        job = tomllib.load(fh)
    res = checks(job, sys.argv[2], sys.argv[3:])
    for n, ok, d in res:
        print(("ok   " if ok else "FAIL ") + n + ("" if ok else " - " + d))
    sys.exit(0 if res and all(o for _n, o, _d in res) else 1)
