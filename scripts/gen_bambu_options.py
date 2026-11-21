#!/usr/bin/env python3
"""Generate the Bambu Studio options table for one Studio source tag.

Reads (never runs) a Bambu Studio checkout:
  src/libslic3r/PrintConfig.cpp   option definitions, variant sets
  src/libslic3r/Preset.cpp        which preset owns each option
  src/libslic3r/PresetBundle.cpp  project options
  src/libslic3r/PrintConfig.hpp   enum class names (for enum defaults) and the
                                  PrintRegionConfig keys (settings a part or a
                                  height range may carry)

Writes bambu_options_<tag>.json. Every option record:
  key, type (coInt, coFloats, ...), vector (bool), nullable, preset
  (print|filament|printer|project|other), variant (null|print|filament|
  printer_1|printer_2), variant_stride, default (list of strings for vectors,
  string for scalars, serialized the way project_settings.config stores it;
  null if the source expression could not be evaluated), default_raw (C++
  text), min, max, unit, label, full_label, category, mode, enum_values,
  readonly.

Usage: gen_bambu_options.py <studio_checkout> <tag> <out.json>
"""
import json
import re
import sys
from pathlib import Path

VECTOR_TYPES = {"coFloats", "coInts", "coStrings", "coPercents", "coFloatsOrPercents",
                "coPoints", "coBools", "coEnums", "coPointsGroups", "coIntsGroups"}


def strip_comments(src):
    # Remove /* */ and // comments, but keep string literals intact.
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c == '"':
            j = i + 1
            while j < n and src[j] != '"':
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1]); i = j + 1
        elif c == "'" :
            j = i + 1
            while j < n and src[j] != "'":
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1]); i = j + 1
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(c); i += 1
    return "".join(out)


def cstr(s):
    """Concatenate adjacent C string literals inside s, unescape."""
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', s)
    if not parts:
        return None
    raw = "".join(parts)
    return bytes(raw, "utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8")


def balanced(text, start, open_c="(", close_c=")"):
    """Return index just after the bracket matching text[start] == open_c."""
    depth, i, n = 0, start, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unbalanced")


def split_top(s, sep=","):
    out, depth, cur, i = [], 0, [], 0
    while i < len(s):
        c = s[i]
        if c == '"':
            j = i + 1
            while j < len(s) and s[j] != '"':
                j += 2 if s[j] == "\\" else 1
            cur.append(s[i:j + 1]); i = j + 1; continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == sep and depth == 0:
            out.append("".join(cur).strip()); cur = []
        else:
            cur.append(c)
        i += 1
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


def string_list(src, name_regex):
    """Parse `<decl> NAME = { "a", "b" };` or `NAME { ... };` into a list."""
    m = re.search(name_regex + r"\s*=?\s*\{", src)
    if not m:
        return None
    start = m.end() - 1
    end = balanced(src, start, "{", "}")
    body = src[start + 1:end - 1]
    return re.findall(r'"((?:[^"\\]|\\.)*)"', body)


class Evaluator:
    """Evaluates the small C++ expressions used in option defaults."""

    def __init__(self, consts, enum_maps, enum_classes):
        self.consts = consts
        self.enum_maps = enum_maps        # map name -> {key_string: enumerator}
        self.enum_classes = enum_classes  # enumerator -> int

    def num(self, expr):
        e = expr.strip()
        e = re.sub(r"(?<=[0-9.])f\b", "", e)          # 1.5f -> 1.5
        e = re.sub(r"\b(int|double|float|size_t)\s*\(", "(", e)
        for k, v in self.consts.items():
            e = re.sub(r"\b%s\b" % re.escape(k), str(v), e)
        if not re.fullmatch(r"[0-9eE.+\-*/() ]+", e):
            raise ValueError(expr)
        val = eval(e, {"__builtins__": {}})
        return val

    @staticmethod
    def fmt(v, kind):
        if kind in ("int", "bool"):
            return str(int(v))
        # Studio serializes floats with %g-like shortest form.
        s = repr(float(v))
        if s.endswith(".0"):
            s = s[:-2]
        return s


def parse_default(arg_text, otype, ev, enum_map_name):
    """Return default as Studio would serialize it in project_settings.config."""
    m = re.match(r"new\s+(ConfigOption\w+)(?:<\s*([\w:]+)\s*>)?\s*([({])", arg_text.strip())
    if not m:
        return None
    cls, tmpl = m.group(1), m.group(2)
    start = m.end() - 1
    close = ")" if m.group(3) == "(" else "}"
    end = balanced(arg_text, start, m.group(3), close)
    inner = arg_text[start + 1:end - 1].strip()

    def enum_name(tok):
        tok = re.sub(r"\(\s*(int|size_t)\s*\)", "", tok).strip().split("::")[-1]
        names = []
        if tmpl:
            names.append("s_keys_map_" + tmpl.split("::")[-1])
        if enum_map_name:
            names.append(enum_map_name)
        for name in names:
            inv = {v: k for k, v in ev.enum_maps.get(name, {}).items()}
            if tok in inv:
                return inv[tok]
        # last resort: unique enumerator across all maps
        hits = {k for m in ev.enum_maps.values() for k, v in m.items() if v == tok}
        return hits.pop() if len(hits) == 1 else None

    def scalar(tok):
        tok = tok.strip()
        if cls.startswith("ConfigOptionEnum") or tmpl:
            return enum_name(tok)
        if cls in ("ConfigOptionString", "ConfigOptionStrings") or tok.startswith('"'):
            v = cstr(tok)
            return "" if v is None and tok in ('""', "") else v
        if cls.startswith("ConfigOptionBool"):
            return {"true": "1", "false": "0", "1": "1", "0": "0"}.get(tok)
        if cls.startswith("ConfigOptionPoint"):
            pm = re.match(r"Vec2d\s*\(\s*(.+?)\s*,\s*(.+?)\s*\)", tok)
            if pm:
                return "%sx%s" % (Evaluator.fmt(ev.num(pm.group(1)), "f"), Evaluator.fmt(ev.num(pm.group(2)), "f"))
            return None
        if cls.startswith("ConfigOptionPercent"):
            return Evaluator.fmt(ev.num(tok), "f") + "%"
        if cls.startswith("ConfigOptionFloatOrPercent") or cls.startswith("ConfigOptionFloatsOrPercents"):
            fm = re.match(r"FloatOrPercent\s*\{?\(?\s*(.+?)\s*,\s*(true|false)\s*\)?\}?$", tok)
            if fm:
                return Evaluator.fmt(ev.num(fm.group(1)), "f") + ("%" if fm.group(2) == "true" else "")
            return None
        if cls.startswith("ConfigOptionInt"):
            return Evaluator.fmt(ev.num(tok), "int")
        if "nil" in tok:
            return "nil"
        return Evaluator.fmt(ev.num(tok), "f")

    try:
        vector = otype in VECTOR_TYPES
        if not vector:
            if cls == "ConfigOptionFloatOrPercent":
                parts = split_top(inner)
                return Evaluator.fmt(ev.num(parts[0]), "f") + ("%" if parts[1].strip() == "true" else "")
            if cls == "ConfigOptionString" and inner == "":
                return ""
            return scalar(inner)
        # vectors
        if inner == "":
            return []
        body = inner
        if body.startswith("{") and body.endswith("}"):
            body = body[1:-1].strip()
        items = split_top(body)
        # ConfigOptionFloats(3, 0.) style: count, value
        if len(items) == 2 and m.group(3) == "(" and re.fullmatch(r"\d+", items[0]):
            return [scalar(items[1])] * int(items[0])
        # std::vector<...>{...} or axis.member: not evaluable here
        if len(items) == 1 and (items[0].startswith("std::") or "." in items[0] and not re.match(r"^[-+0-9.]", items[0]) and not items[0].startswith('"')):
            return None
        vals = [scalar(t) for t in items]
        if any(v is None for v in vals):
            return None
        return vals
    except (ValueError, SyntaxError, ZeroDivisionError):
        return None


def parse(checkout, tag):
    root = Path(checkout) / "src" / "libslic3r"
    pc = strip_comments((root / "PrintConfig.cpp").read_text(encoding="utf-8", errors="replace"))
    pr = strip_comments((root / "Preset.cpp").read_text(encoding="utf-8", errors="replace"))
    pb = strip_comments((root / "PresetBundle.cpp").read_text(encoding="utf-8", errors="replace"))

    # enum key maps: static const t_config_enum_values s_keys_map_X = { {"name", enumerator}, ... };
    enum_maps = {}
    for m in re.finditer(r"t_config_enum_values\s+(s_keys_map_\w+)\s*=?\s*\{", pc):
        end = balanced(pc, m.end() - 1, "{", "}")
        body = pc[m.end():end - 1]
        enum_maps[m.group(1)] = {k: v.split("::")[-1] for k, v in
                                  re.findall(r'\{\s*"([^"]*)"\s*,\s*(?:int\s*\(\s*)?([\w:]+)\s*\)?\s*\}', body)}

    # restrict to FFF + common definitions
    def fn_body(name):
        m = re.search(r"void PrintConfigDef::%s\s*\(\s*\)\s*\{" % name, pc)
        end = balanced(pc, m.end() - 1, "{", "}")
        return pc[m.end():end - 1]

    body = fn_body("init_common_params") + "\n" + fn_body("init_fff_params")

    consts = {}
    for m in re.finditer(r"const\s+(?:int|double|float)\s+(\w+)\s*=\s*([0-9.eE+\-]+)\s*;", body):
        consts[m.group(1)] = float(m.group(2)) if "." in m.group(2) else int(m.group(2))
    ev = Evaluator(consts, enum_maps, {})

    opts = {}
    order = []
    add_re = re.compile(r"def\s*=\s*this->add(_nullable)?\s*\(")
    positions = [m for m in add_re.finditer(body)]
    for idx, m in enumerate(positions):
        args_end = balanced(body, m.end() - 1)
        args = split_top(body[m.end():args_end - 1])
        block_end = positions[idx + 1].start() if idx + 1 < len(positions) else len(body)
        block = body[args_end:block_end]
        key_expr, otype = args[0], args[1].strip()
        rec = {"type": otype, "nullable": bool(m.group(1)), "_block": block, "_key_expr": key_expr}
        if re.fullmatch(r'\s*"(?:[^"\\]|\\.)*"\s*', key_expr):
            rec["key"] = cstr(key_expr)
        else:
            rec["key"] = None  # expanded below
        order.append(rec)

    def props(rec):
        b = rec["_block"]
        def grab(name):
            mm = re.search(r"def->%s\s*=\s*(.+?);" % name, b, re.S)
            return mm.group(1).strip() if mm else None
        label = grab("label"); full = grab("full_label"); cat = grab("category")
        side = grab("sidetext"); mode = grab("mode"); mn = grab("min"); mx = grab("max")
        nul = grab("nullable"); ro = grab("readonly"); ekm = grab("enum_keys_map")
        rec["label"] = cstr(label) if label and '"' in label else None
        rec["full_label"] = cstr(full) if full and '"' in full else None
        rec["category"] = cstr(cat) if cat and '"' in cat else None
        rec["unit"] = cstr(side) if side and '"' in side else None
        rec["mode"] = mode.replace("com", "").lower() if mode else None
        for k, v in (("min", mn), ("max", mx)):
            try:
                rec[k] = ev.num(v) if v is not None else None
            except (ValueError, SyntaxError):
                rec[k] = None
                rec[k + "_raw"] = v
        if nul is not None:
            rec["nullable"] = nul.strip() == "true"
        rec["readonly"] = (ro or "").strip() == "true"
        rec["enum_values"] = [cstr(x) for x in re.findall(r'def->enum_values\.(?:emplace_back|push_back)\s*\(\s*("(?:[^"\\]|\\.)*")\s*\)', b)] or None
        if not rec["enum_values"]:
            ev_list = re.search(r"def->enum_values\s*=\s*\{(.+?)\}\s*;", b, re.S)
            if ev_list:
                rec["enum_values"] = re.findall(r'"((?:[^"\\]|\\.)*)"', ev_list.group(1))
        emap_name = None
        if ekm:
            mm = re.search(r"(s_keys_map_\w+)", ekm) or None
            if mm:
                emap_name = mm.group(1)
            else:
                mm = re.search(r"ConfigOptionEnum<\s*([\w:]+)\s*>", ekm)
                if mm:
                    emap_name = "s_keys_map_" + mm.group(1).split("::")[-1]
        rec["_enum_map"] = emap_name
        dm = re.search(r"def->set_default_value\s*\(", b)
        if dm:
            dend = balanced(b, dm.end() - 1)
            raw = b[dm.end():dend - 1].strip()
            rec["default_raw"] = " ".join(raw.split())
            rec["default"] = parse_default(raw, rec["type"], ev, rec["_enum_map"])
        else:
            rec["default_raw"] = None
            rec["default"] = None

    for rec in order:
        props(rec)

    # Expand machine_max_* axis loop
    expanded = []
    axes_m = re.search(r"std::vector<AxisDefault>\s+axes\s*\{(.+?)\};", body, re.S)
    axes = []
    if axes_m:
        for am in re.finditer(r'\{\s*"(\w)"\s*,\s*\{([^}]*)\}\s*,\s*\{([^}]*)\}\s*,\s*\{([^}]*)\}\s*\}', axes_m.group(1)):
            axes.append((am.group(1), [x.strip() for x in am.group(2).split(",")],
                         [x.strip() for x in am.group(3).split(",")], [x.strip() for x in am.group(4).split(",")]))
    member = {"max_feedrate": 1, "max_acceleration": 2, "max_jerk": 3}
    for rec in order:
        if rec["key"] is not None:
            expanded.append(rec); continue
        km = re.match(r'"(\w+)"\s*\+\s*axis\.name', rec["_key_expr"])
        if km and axes:
            mem = re.search(r"axis\.(\w+)", rec.get("default_raw") or "")
            for ax in axes:
                r2 = dict(rec); r2["key"] = km.group(1) + ax[0]
                if mem and mem.group(1) in member:
                    vals = ax[member[mem.group(1)]]
                    r2["default"] = [Evaluator.fmt(ev.num(v), "f") for v in vals]
                expanded.append(r2)
            continue
        if rec["_key_expr"].strip() == "opt_key":
            continue  # filament_extruder_override_keys, handled below
        rec["key"] = "<unresolved:%s>" % rec["_key_expr"]
        expanded.append(rec)

    by_key = {r["key"]: r for r in expanded}

    # Generated options: filament_extruder_override_keys and filament_overhang_override_keys.
    # Each is add_nullable(key, base.type) copying label/min/max/enum from the option named
    # by the key without its "filament_" prefix; the default is a copy of the base default
    # (PrintConfig.cpp, the two `for (auto& opt_key : filament_*_override_keys)` loops).
    for list_name in ("filament_extruder_override_keys", "filament_overhang_override_keys"):
        keys = string_list(pc, r"const\s+std::vector<std::string>\s+%s" % list_name)
        if keys is None:
            continue
        for k in keys:
            base_key = k[k.find("filament_") + len("filament_"):]
            base = by_key.get(base_key)
            if base is None or k in by_key:
                continue
            r2 = {kk: vv for kk, vv in base.items()}
            r2["key"] = k
            r2["nullable"] = True
            r2["default_raw"] = "copy of %s default (%s)" % (base_key, list_name)
            r2["default"] = base.get("default")
            r2["_generated_from"] = base_key
            if list_name == "filament_extruder_override_keys":
                r2["category"] = None
            by_key[k] = r2

    # Preset membership
    print_opts = set(string_list(pr, r"static std::vector<std::string>\s+s_Preset_print_options") or [])
    fil_opts = set(string_list(pr, r"static std::vector<std::string>\s+s_Preset_filament_options") or [])
    prn_opts = set(string_list(pr, r"static std::vector<std::string>\s+s_Preset_printer_options") or [])
    prn_opts |= set(string_list(pr, r"static std::vector<std::string>\s+s_Preset_machine_limits_options") or [])
    ext_keys = re.search(r"m_extruder_option_keys\s*=\s*\{(.+?)\};", pc, re.S)
    prn_opts |= set(re.findall(r'"(\w+)"', ext_keys.group(1))) if ext_keys else set()
    proj_opts = string_list(pb, r"static std::vector<std::string>\s+s_project_options")

    variant_sets = {}
    for name, label in (("print_options_with_variant", "print"), ("filament_options_with_variant", "filament"),
                        ("printer_options_with_variant_1", "printer_1"), ("printer_options_with_variant_2", "printer_2")):
        variant_sets[label] = set(string_list(pc, r"std::set<std::string>\s+%s" % name) or [])

    dev_opts = set(string_list(pc, r"std::set<std::string>\s+filament_dev_options") or [])
    out = []
    for k in sorted(by_key):
        r = by_key[k]
        preset = ("print" if k in print_opts else "filament" if k in fil_opts else
                  "printer" if k in prn_opts else "project" if k in proj_opts else "other")
        variant = None; stride = None
        for label, s in variant_sets.items():
            if k in s:
                variant = label.split("_")[0]; stride = 2 if label == "printer_2" else 1
        rec = {
            "key": k, "type": r["type"], "vector": r["type"] in VECTOR_TYPES,
            "nullable": r["nullable"], "preset": preset,
            "also_in": [p for p, s in (("print", print_opts), ("filament", fil_opts), ("printer", prn_opts), ("project", set(proj_opts)))
                        if k in s and p != preset] or None,
            "variant": variant, "variant_stride": stride,
            "default": r.get("default"), "default_raw": r.get("default_raw"),
            "min": r.get("min"), "max": r.get("max"), "unit": r.get("unit"),
            "label": r.get("label"), "full_label": r.get("full_label"), "category": r.get("category"),
            "mode": r.get("mode"), "enum_values": r.get("enum_values"), "readonly": r.get("readonly", False),
        }
        if k in dev_opts:
            rec["dev_option"] = True
        if r.get("_generated_from"):
            rec["generated_from"] = r["_generated_from"]
        if r.get("_enum_map"):
            rec["enum_keys"] = sorted(enum_maps.get(r["_enum_map"], {}).keys()) or None
        out.append(rec)

    hpp = strip_comments((root / "PrintConfig.hpp").read_text(encoding="utf-8", errors="replace"))
    m = re.search(r"PRINT_CONFIG_CLASS_DEFINE\(\s*PrintRegionConfig\s*,", hpp)
    region = []
    if m:
        st = hpp.index("(", m.start())
        body = hpp[st:balanced(hpp, st)]
        region = sorted(set(re.findall(r"\(\(\s*ConfigOption\w+\s*,\s*(\w+)\s*\)\)", body)))

    return {
        "studio_tag": tag,
        "generated_from": ["src/libslic3r/PrintConfig.cpp", "src/libslic3r/Preset.cpp", "src/libslic3r/PresetBundle.cpp"],
        "license_note": "Derived from Bambu Studio source (AGPL-3.0). Distributed under AGPL-3.0.",
        "project_options": proj_opts,
        "region_options": region,
        "variant_sets": {k: sorted(v) for k, v in variant_sets.items()},
        "preset_lists": {"print": sorted(print_opts), "filament": sorted(fil_opts), "printer": sorted(prn_opts)},
        "option_count": len(out),
        "options": out,
    }


if __name__ == "__main__":
    checkout, tag, dest = sys.argv[1], sys.argv[2], sys.argv[3]
    data = parse(checkout, tag)
    Path(dest).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    unresolved = [o["key"] for o in data["options"] if o["key"].startswith("<unresolved")]
    nodef = [o["key"] for o in data["options"] if o["default"] is None]
    print("%s: %d options, %d unresolved keys, %d without evaluated default" % (tag, data["option_count"], len(unresolved), len(nodef)))
