"""Height ranges (Studio's "height range modifier"): settings, above all the layer height, for a z band of one object.

Studio keeps them in Metadata/layer_config_ranges.xml (bbs_3mf.cpp, same in 2.8.2 and 2.9.1):

    <objects>
     <object id="1">                      1-based position of the object in the model (build order), not the 3MF object id
      <range min_z="0" max_z="2.4">       mm from the bottom of the object
       <option opt_key="layer_height">0.12</option>
       <option opt_key="wall_loops">4</option>
      </range>
     </object>
    </objects>

What the source fixes (2.9.1 line numbers; 2.8.2 is the same code):
- Every range carries layer_height. Slicing reads it without a check (Slicing.cpp layer_height_profile_from_ranges, :257), so a range
  without it is not usable.
- A range may also carry the settings a part may carry: PrintRegionConfig (GUI_ObjectList.cpp :1568 uses get_options(true)), and
  "extruder": the filament of the band, 0 = the object's own. The GUI writes layer_height and extruder (0) in every range it makes
  (GUI_ObjectList.cpp :3772-3777); a range filament also prints in the band (PrintObject.cpp region_config_from_model_volume).
- Ranges are trimmed one by the other in z order (PrintApply.cpp LayerRanges::assign); the GUI never makes overlapping ranges.
- A layer height outside the nozzle's limits makes Studio drop the whole profile (PrintObject::update_layer_height_profile, the
  "nozzle_range_reset" path; limits from Slicing.cpp min/max_layer_height_from_nozzle).
- Organic tree supports refuse variable layer heights (Print.cpp :1532). A prime tower needs every object on the plate to have the same
  layer z series (Print.cpp :1609 / :1657).
"""
import xml.etree.ElementTree as ET

FILE = "Metadata/layer_config_ranges.xml"
EPS = 1e-6
MIN_LAYER = 0.01          # Slicing.cpp MIN_LAYER_HEIGHT
MIN_LAYER_DEFAULT = 0.07  # Slicing.cpp MIN_LAYER_HEIGHT_DEFAULT (min_layer_height 0)


def _g(x):
    s = ("%.6f" % float(x)).rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def to_xml(by_obj):
    """by_obj: {object index (1-based): [(min_z, max_z, [(key, serialized value), ...]), ...]}. Studio's own layout (beautified)."""
    if not by_obj:
        return None
    out = ['<?xml version="1.0" encoding="utf-8"?>\n<objects>\n']
    for idx in sorted(by_obj):
        out.append(' <object id="%d">\n' % idx)
        for lo, hi, opts in sorted(by_obj[idx], key=lambda r: (r[0], r[1])):
            out.append('  <range min_z="%s" max_z="%s">\n' % (_g(lo), _g(hi)))
            for k, v in sorted(opts):                     # Studio writes a range's keys in key order (DynamicConfig is a sorted map)
                out.append('   <option opt_key="%s">%s</option>\n' % (k, _esc(str(v))))
            out.append("  </range>\n")
        out.append(" </object>\n")
    out.append("</objects>\n")
    return "".join(out)


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def parse(text):
    """Returns ({object index: [(min_z, max_z, {key: value})]}, [problem, ...]). Follows the reader in bbs_3mf.cpp."""
    probs = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        return {}, ["not well-formed XML (%s)" % e]
    if root.tag != "objects":
        return {}, ["root element is <%s>, Studio reads <objects>" % root.tag]
    out = {}
    for ob in root:
        if ob.tag != "object":
            continue
        try:
            idx = int(ob.get("id", "-1"))
        except ValueError:
            idx = -1
        if idx <= 0:
            probs.append("object id %r is not a positive integer (Studio: \"Found invalid object id\")" % ob.get("id"))
            continue
        if idx in out:
            probs.append("object %d is listed twice (Studio keeps the first: \"Found duplicated layer config range\")" % idx)
            continue
        rs = []
        for r in ob:
            if r.tag != "range":
                continue
            try:
                lo, hi = float(r.get("min_z")), float(r.get("max_z"))
            except (TypeError, ValueError):
                probs.append("object %d: a range has no numeric min_z / max_z" % idx)
                continue
            opts = {}
            for o in r:
                if o.tag == "option" and o.get("opt_key"):
                    opts[o.get("opt_key")] = (o.text or "").strip()
            rs.append((lo, hi, opts))
        out[idx] = rs
    return out, probs


def layer_limits(cfg, extruder=1):
    """(min, max) layer height Studio allows for the nozzle (Slicing.cpp min/max_layer_height_from_nozzle)."""
    def at(key, default):
        v = cfg.get(key, default)
        if isinstance(v, list):
            v = v[min(max(extruder, 1), len(v)) - 1] if v else default
        try:
            return float(v)
        except (TypeError, ValueError):
            return float(default)
    lo = at("min_layer_height", 0)
    lo = MIN_LAYER_DEFAULT if lo == 0 else max(MIN_LAYER, lo)
    hi = at("max_layer_height", 0)
    hi = max(lo, 0.75 * at("nozzle_diameter", 0.4) if hi == 0 else hi)
    return lo, hi


def check(ranges, where, limits, region_keys=None, height=None):
    """Checks one object's ranges [(min_z, max_z, {key: value})]. Returns (errors, warnings) as strings."""
    errs, warns = [], []
    lo_lim, hi_lim = limits
    prev = None
    for lo, hi, opts in sorted(ranges, key=lambda r: (r[0], r[1])):
        w = "%s range %s-%s mm" % (where, _g(lo), _g(hi))
        if not (lo >= 0 and hi > lo + EPS):
            errs.append("%s: needs 0 <= min_z < max_z" % w)
            continue
        if prev is not None and lo < prev - EPS:
            errs.append("%s overlaps the range below it (ends at %s mm). Studio trims overlapping ranges; make them meet or leave a gap" % (w, _g(prev)))
        prev = max(prev or 0, hi)
        if height is not None and lo >= height - EPS:
            errs.append("%s starts at or above the top of the object (%s mm); Studio ignores it" % (w, _g(height)))
        elif height is not None and hi > height + 0.001:
            warns.append("%s ends above the top of the object (%s mm); Studio stops it at the top" % (w, _g(height)))
        lh = opts.get("layer_height")
        if lh in (None, ""):
            errs.append("%s has no layer_height. Studio needs one in every range" % w)
        else:
            try:
                h = float(lh)
            except ValueError:
                errs.append("%s: layer_height %r is not a number" % (w, lh))
            else:
                if not (lo_lim - EPS <= h <= hi_lim + EPS):
                    errs.append("%s: layer_height %s mm is outside the nozzle's limits %s-%s mm. Studio then drops every height range of the object" % (
                        w, _g(h), _g(lo_lim), _g(hi_lim)))
                elif hi - lo < h - EPS:
                    warns.append("%s is thinner than one layer (%s mm); it may not change any layer" % (w, _g(h)))
        if region_keys is not None:
            bad = sorted(k for k in opts if k not in ("layer_height", "extruder") and k not in region_keys)
            if bad:
                errs.append("%s: %s cannot be set in a height range (only layer_height and the settings a part can have)" % (w, ", ".join(bad)))
    return errs, warns


def signature(ranges):
    """What decides the layer z series of an object: its ranges and their layer heights."""
    return tuple(sorted((round(lo, 4), round(hi, 4), str(o.get("layer_height"))) for lo, hi, o in ranges))


def extruders(ranges):
    """The filaments (1-based) the ranges print with (extruder > 0)."""
    out = set()
    for _lo, _hi, o in ranges:
        try:
            e = int(str(o.get("extruder", "0")).strip() or 0)
        except ValueError:
            continue
        if e > 0:
            out.add(e)
    return out
