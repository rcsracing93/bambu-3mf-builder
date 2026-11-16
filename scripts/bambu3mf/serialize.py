"""Value normalisation: raw preset text -> the text Studio writes into project_settings.config.

Studio reads every value into a typed ConfigOption and writes it back out. Floats go through
`ostream << double` (6 significant digits, %g), percents get a trailing %, bools become 0/1, points
become "x,y". A value written in the other form (1.0, 100, 0.5x0.5) loads the same but is not what
Studio saves. The builder writes the saved form so its file diffs clean against a real save.
"""
import re
import math

NIL = "nil"


def _g(x):
    if isinstance(x, float) and x == int(x) and abs(x) < 1e15:
        return "%d" % int(x)
    s = "%g" % x
    return s


def _num(s):
    return float(str(s).strip().rstrip("%"))


def _single(t, s):
    if not isinstance(s, str):
        s = str(s)
    if s == NIL:
        return NIL
    if t in ("coFloat", "coFloats"):
        return _g(_num(s))
    if t in ("coPercent", "coPercents"):
        return _g(_num(s)) + "%"
    if t in ("coFloatOrPercent", "coFloatsOrPercents"):
        return _g(_num(s)) + ("%" if s.strip().endswith("%") else "")
    if t in ("coInt", "coInts"):
        return "%d" % int(round(_num(s)))
    if t in ("coBool", "coBools"):
        v = s.strip().lower()
        return "1" if v in ("1", "true") else "0"
    if t in ("coPoint", "coPoints"):
        return s  # handled by caller
    return s


def _point(s):
    s = s.strip()
    sep = "x" if "x" in s else ","
    a, b = s.split(sep)
    return "%s,%s" % (_g(float(a)), _g(float(b)))


def normalize(option, value):
    """option: an options.json entry; value: str or list[str] as in a preset file or job."""
    t = option["type"]
    if t == "coPointsGroups":
        return value
    if t == "coPoint":
        return _point(value)
    if t == "coPoints":
        # Points keep the "AxB" form inside vector settings (checked against a real save)
        return [_pointx(v) for v in value]
    if option["vector"]:
        if not isinstance(value, list):
            value = [value]
        return [_single(t, v) for v in value]
    return _single(t, value)


def _lead_num(t):
    """The number at the start of t, as std::istringstream >> double reads it (0 when there is none)."""
    m = re.match(r"\s*[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?", t)
    return float(m.group(0)) if m else 0.0


def _pointx(s):
    s = s.strip()
    if "x" not in s and "," not in s:
        # Studio's ConfigOptionPoints::deserialize (Config.hpp) splits on a lower-case 'x' only and reads each part with >> double.
        # The P2S profile has extruder_offset "0X0": that reads as 0 then stops, so Studio holds (0, 0) and saves "0x0".
        return "%sx%s" % (_g(_lead_num(s)), _g(0.0))
    sep = "x" if "x" in s else ","
    a, b = s.split(sep)
    return "%sx%s" % (_g(float(a)), _g(float(b)))
