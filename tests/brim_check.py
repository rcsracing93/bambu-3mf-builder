"""Brim lint checks for a real slice (called by the lead's real-slice run).
    checks(raw, f3mf, gcodes) -> [(name, ok, detail)]
raw = the job TOML as a dict, f3mf = the built 3MF, gcodes = the sliced plate G-code files.
1. The summary file next to the 3MF (<3MF without .3mf>.summary.md) holds the brim lint text exactly when the job expects it:
   a PETG filament (preset name contains "PETG"), an explicit brim type (outer_only, inner_only, outer_and_inner) with brim_width above 0,
   and brim_object_gap below 0.2 mm in the job [settings]. The check reads process settings of the job only; an object override is
   covered by tests/test_brim_lint.py.
2. brim_type and brim_object_gap in the G-code config block (tests/harness.py config_block) equal the job values.
Every check says how many items it measured; 0 measured is NOT CHECKED, a failure."""
import os

from harness import config_block

TEXT = "PETG fuses to a tight brim; set brim_object_gap = 0.25"
EXPLICIT = ("outer_only", "inner_only", "outer_and_inner")
NC = "NOT CHECKED (0 measured)"


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _expects(raw):
    st = raw.get("settings", {})
    petg = any("PETG" in str(f.get("preset", "")).upper() for f in raw.get("filament", []))
    gap = _num(st.get("brim_object_gap", 0.1))
    width = _num(st.get("brim_width", st.get("brim_size", 5)))
    return petg and st.get("brim_type") in EXPLICIT and bool(width and width > 0) and gap is not None and gap < 0.2 - 1e-9


def checks(raw, f3mf, gcodes):
    out = []
    summary = (f3mf[:-4] if f3mf.endswith(".3mf") else f3mf) + ".summary.md"
    if os.path.isfile(summary):
        with open(summary, encoding="utf-8") as fh:
            has = TEXT in fh.read()
        want = _expects(raw)
        out.append(("brim lint text in summary file %s (1 measured)" % ("expected" if want else "not expected"), has == want,
                    "expected %s, found %s" % (want, has)))
    else:
        out.append(("brim lint summary file", False, "%s: %s" % (NC, summary)))
    st = raw.get("settings", {})
    for key, default in (("brim_type", "auto_brim"), ("brim_object_gap", 0.1)):
        want = st.get(key, default)
        vals = [config_block(g).get(key) for g in gcodes]
        vals = [v for v in vals if v is not None]
        if not vals:
            out.append(("G-code %s equals the job (0 measured)" % key, False, NC))
            continue
        if key == "brim_object_gap":
            ok = all(_num(v) == _num(want) for v in vals)
        else:
            ok = all(v == str(want) for v in vals)
        out.append(("G-code %s equals the job: %s (%d measured)" % (key, want, len(vals)), ok, "found %s" % sorted(set(vals))))
    return out
