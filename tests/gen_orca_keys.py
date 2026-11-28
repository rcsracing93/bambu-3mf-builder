#!/usr/bin/env python3
"""Generate scripts/bambu3mf/data/orca_keys.json for --orca-lint.

Reads (never runs) an OrcaSlicer checkout, only src/libslic3r:
  PrintConfig.cpp   the option table (every this->add / add_nullable key), the enum names an option accepts,
                    and PrintConfigDef::handle_legacy (renamed keys, value changes, keys that are dropped)
  Config.cpp        set_deserialize_nothrow (the place where a key that handle_legacy clears is dropped)

It also reads the options.json of every Bambu Studio version in this repository (studio/*/options.json) to find the
enum values that Studio accepts and Orca does not. Every record keeps the line in the Orca source that it came from.

Usage: gen_orca_keys.py <orca_checkout> <tag> [--commit SHA] [--out FILE]
Rebuild for a new Orca tag: check the tag out (sparse src/libslic3r is enough) and run this again."""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "scripts"))
import gen_bambu_options as G                                    # noqa: E402  (reuses its C++ option parser)

_cstr = G.cstr


def safe_cstr(s):
    """Orca's source holds non-ASCII text (for example the degree sign); the shared helper fails on it."""
    try:
        return _cstr(s)
    except UnicodeError:
        parts = re.findall(r'"((?:[^"\\]|\\.)*)"', s)
        return "".join(parts) if parts else None


G.cstr = safe_cstr
_PARSED = {}


def parsed(checkout, tag):
    if (checkout, tag) not in _PARSED:
        _PARSED[(checkout, tag)] = G.parse(checkout, tag)
    return _PARSED[(checkout, tag)]


DEFAULT_OUT =REPO / "scripts" / "bambu3mf" / "data" / "orca_keys.json"
SRC = "src/libslic3r/PrintConfig.cpp"
CFG = "src/libslic3r/Config.cpp"


def line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def orca_keys(raw, pc_stripped, checkout, tag):
    """Every option key of the Orca table, with the line of its definition (0 when it is generated in a loop)."""
    keys = {}
    for i, ln in enumerate(raw.splitlines(), 1):
        if ln.lstrip().startswith("//"):
            continue
        for m in re.finditer(r'this->add(?:_nullable)?\s*\(\s*"([^"]+)"', ln):
            keys.setdefault(m.group(1), i)
    # keys generated in loops (machine_max_*, filament_*_override_keys): the Studio generator expands them
    try:
        for o in parsed(checkout, tag)["options"]:
            k = o["key"]
            if k and not k.startswith("<unresolved"):
                keys.setdefault(k, 0)
    except Exception as e:                                       # noqa: BLE001
        print("note: the shared option parser failed (%s); only literal keys are listed" % e, file=sys.stderr)
    return keys


def legacy_function(raw):
    m = re.search(r"void PrintConfigDef::handle_legacy\s*\(\s*t_config_option_key\s*&\s*opt_key", raw)
    if not m:
        raise SystemExit("PrintConfigDef::handle_legacy not found")
    start = m.start()
    end = G.balanced(raw, raw.index("{", m.end()), "{", "}")
    return start, end


HEAD_RE = re.compile(r"^ {4}(?:\}\s*)?(?:else\s*)?if\s*\(", re.M)
NEST_RE = re.compile(r'if\s*\(((?:[^(){}]|\([^()]*\))*)\)\s*\{?\s*value\s*=\s*"([^"]*)"')


def values_in(cond):
    eq = re.findall(r'value\s*==\s*"([^"]*)"', cond) + re.findall(r'iequals\(\s*value\s*,\s*"([^"]*)"\s*\)', cond)
    ne = re.findall(r'value\s*!=\s*"([^"]*)"', cond)
    return eq, ne


def parse_legacy(raw):
    """Returns (renames, value_maps, erased, ignore) from handle_legacy. Lines are lines of PrintConfig.cpp."""
    start, end = legacy_function(raw)
    body = raw[start:end]
    base = line_of(raw, start) - 1
    heads = [m.start() for m in HEAD_RE.finditer(body)]
    renames, value_maps, erased = {}, [], {}
    ignore_pos = body.find("static std::set<std::string> ignore")
    for n, h in enumerate(heads):
        stop = heads[n + 1] if n + 1 < len(heads) else len(body)
        if 0 <= ignore_pos < stop and ignore_pos > h:
            stop = ignore_pos
        seg = body[h:stop]
        brace = seg.index("{") if "{" in seg else len(seg)
        # the condition ends at the first "{" that follows the balanced parentheses of the if
        p0 = seg.index("(")
        depth, j = 0, p0
        while j < len(seg):
            if seg[j] == "(":
                depth += 1
            elif seg[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        cond = seg[p0:j + 1]
        stmts = seg[j + 1:]
        keys = re.findall(r'opt_key\s*==\s*"([^"]+)"', cond)
        if not keys:
            continue
        line = base + line_of(body, h)
        head_eq, head_ne = values_in(cond)
        percent = "value.find(\"%\")" in cond
        targets = [t for t in re.findall(r'opt_key\s*=\s*"([^"]*)"\s*;', stmts)]
        new_keys = [t for t in targets if t]
        clears = "" in targets
        nested = NEST_RE.findall(stmts)
        rest = NEST_RE.sub("", stmts)
        bare = re.findall(r'(?<![\w.])value\s*=\s*"([^"]*)"\s*;', rest)
        for key in keys:
            if percent:
                erased[key] = {"line": line, "when": "the value is a percentage", "how": "handle_legacy clears the key"}
                continue
            if new_keys:
                rec = {"to": new_keys[0], "line": line}
                if len(set(new_keys)) > 1:
                    rec["also_to"] = sorted(set(new_keys[1:]) - {new_keys[0]})
                if head_eq or head_ne:
                    rec["when_value"] = {"is": head_eq, "is_not": head_ne}
                if clears:
                    rec["note"] = "some values clear the key instead"
                vm = [{"from": values_in(c)[0], "to": v} for c, v in nested]
                if vm:
                    rec["values"] = vm
                if bare and (head_eq or head_ne):
                    rec["value_to"] = bare[0]
                renames[key] = rec
                continue
            if clears and not new_keys:
                erased[key] = {"line": line, "when": "always", "how": "handle_legacy clears the key"}
                continue
            for c, v in nested:
                eq, _ = values_in(c)
                if eq:
                    value_maps.append({"key": key, "from": eq, "to": v, "line": line})
            if bare and head_eq:
                value_maps.append({"key": key, "from": head_eq, "to": bare[0], "line": line})
    ignore = {}
    if ignore_pos >= 0:
        blk_start = body.index("{", ignore_pos)
        blk_end = G.balanced(body, blk_start, "{", "}")
        for i, ln in enumerate(body[blk_start:blk_end].splitlines()):
            ln_no = base + line_of(body, blk_start) + i
            code = ln.split("//")[0]
            for k in re.findall(r'"([^"]+)"', code):
                ignore[k] = {"line": ln_no, "when": "always", "how": "listed as obsolete in handle_legacy"}
    erased.update({k: v for k, v in ignore.items() if k not in erased})
    return renames, value_maps, erased


def config_line(cfg_raw):
    """Line in Config.cpp where set_deserialize_nothrow ignores a key that handle_legacy cleared."""
    m = re.search(r"bool ConfigBase::set_deserialize_nothrow", cfg_raw)
    if not m:
        return 0
    e = re.compile(r"if\s*\(\s*opt_key\.empty\(\)\s*\)").search(cfg_raw, m.start())
    return line_of(cfg_raw, e.start()) if e else line_of(cfg_raw, m.start())


def enum_rule_line(cfg_raw):
    """Line in Config.cpp where an enum value that does not parse is replaced by the default of the option."""
    m = re.search(r"//\s*Just use the default of the option\.", cfg_raw)
    return line_of(cfg_raw, m.start()) if m else 0


def table_check_line(raw):
    """Line in PrintConfig.cpp where handle_legacy clears a key that the option table does not have."""
    start, end = legacy_function(raw)
    m = re.compile(r"if\s*\(\s*!\s*print_config_def\.has\(\s*opt_key\s*\)\s*\)").search(raw, start, end)
    return line_of(raw, m.start()) if m else 0


def orca_enums(pc_raw, pc_stripped, checkout, tag):
    """key -> (accepted values, definition line). Uses the shared parser for the accepted names."""
    data = parsed(checkout, tag)
    out = {}
    for o in data["options"]:
        acc = o.get("enum_keys") or o.get("enum_values")
        if acc and o["type"] in ("coEnum", "coEnums"):
            out[o["key"]] = sorted(set(acc))
    return out


def studio_enum_sets():
    res = {}
    for f in sorted((REPO / "studio").glob("*/options.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        for o in d.get("options", []):
            acc = o.get("enum_keys") or o.get("enum_values")
            if acc and o["type"] in ("coEnum", "coEnums"):
                res.setdefault(o["key"], set()).update(acc)
    return res


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip())
        return 2
    checkout, tag = argv[0], argv[1]
    commit = argv[argv.index("--commit") + 1] if "--commit" in argv else None
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else DEFAULT_OUT
    root = Path(checkout) / "src" / "libslic3r"
    raw = (root / "PrintConfig.cpp").read_text(encoding="utf-8", errors="replace")
    cfg = (root / "Config.cpp").read_text(encoding="utf-8", errors="replace")
    keys = orca_keys(raw, None, checkout, tag)
    renames, value_maps, erased = parse_legacy(raw)
    for r in renames.values():
        r["to_defined"] = r["to"] in keys
        if r["to_defined"]:
            r["to_line"] = keys[r["to"]]
    # an enum value Studio accepts and Orca does not (and handle_legacy does not map)
    mapped = {(v["key"], x) for v in value_maps for x in v["from"]}
    for k, r in renames.items():
        for v in r.get("values", []):
            for x in v["from"]:
                mapped.add((k, x))
    orca_e = orca_enums(raw, None, checkout, tag)
    studio_e = studio_enum_sets()
    enum_reject = {}
    for k, accepted in sorted(orca_e.items()):
        if k not in studio_e:
            continue
        bad = sorted(v for v in studio_e[k] if v not in accepted and (k, v) not in mapped)
        if bad:
            enum_reject[k] = {"line": keys.get(k, 0), "accepted": accepted, "rejected": bad}
    data = {
        "orca_tag": tag,
        "orca_commit": commit,
        "generated_from": [SRC, CFG],
        "generator": "tests/gen_orca_keys.py",
        "load_rule": {"file": CFG, "line": config_line(cfg),
                      "text": "ConfigBase::set_deserialize_nothrow calls handle_legacy; a key that is empty afterwards is ignored"},
        "enum_rule": {"file": CFG, "line": enum_rule_line(cfg),
                      "text": "ConfigBase::set_deserialize_raw replaces an enum value that does not parse with the default of the option"},
        "table_rule": {"file": SRC, "line": table_check_line(raw),
                       "text": "handle_legacy clears a key that print_config_def does not have"},
        "keys": dict(sorted(keys.items())),
        "renamed": dict(sorted(renames.items())),
        "value_maps": value_maps,
        "cleared": dict(sorted(erased.items())),
        "enum_reject": enum_reject,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print("%s: %d Orca keys, %d renamed, %d cleared, %d value maps, %d enum keys with rejected values -> %s" % (
        tag, len(keys), len(renames), len(erased), len(value_maps), len(enum_reject), out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
