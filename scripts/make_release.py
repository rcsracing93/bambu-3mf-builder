#!/usr/bin/env python3
"""Build the release zips.  Usage: python3 scripts/make_release.py [--out DIR]
  bambu-3mf-builder-skill.zip   folder bambu-3mf-builder/ with SKILL.md at its top
  bambu-3mf-builder-plugin.zip  .claude-plugin/plugin.json and skills/bambu-3mf-builder/ (same content)
Fixed timestamps and sorted entries, so the same source gives the same zip."""
import json, os, sys, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INCLUDE = ["SKILL.md", "README.md", "LICENSE", "NOTICE", "CHANGELOG.md", "scripts", "references", "examples", "docs", "studio"]
SKIP_DIRS = {"__pycache__", ".git"}
STAMP = (2000, 1, 1, 0, 0, 0)


# Build-time trim of studio/<version>/. The repo keeps the Studio files unchanged. The release keeps only what
# scripts/bambu3mf/profiles.py reads: BBL.json lists (name, sub_path), per preset file the Studio option keys plus the
# keys the resolver uses, options.json and flush/. cli_config.json is read by nothing. JSON is written compact.
KEEP_META = {"name", "inherits", "include", "from", "instantiation", "setting_id", "filament_id"}
_valid = {}


def _valid_keys(version):
    if version not in _valid:
        with open(os.path.join(ROOT, "studio", version, "options.json")) as f:
            _valid[version] = {o["key"] for o in json.load(f)["options"]}
    return _valid[version]


def _compact(obj):
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def read_bytes(rel):
    """File content for the release, trimmed for studio/<version>/profiles."""
    raw = open(os.path.join(ROOT, rel), "rb").read()
    parts = rel.replace(os.sep, "/").split("/")
    if len(parts) >= 4 and parts[0] == "studio" and parts[2] == "profiles" and rel.endswith(".json"):
        data = json.loads(raw.decode("utf-8"))
        if len(parts) == 4:                                    # BBL.json
            data = {"name": data.get("name", "")} | {k: [{"name": i["name"], "sub_path": i["sub_path"]} for i in data.get(k, [])]
                                                      for k in ("machine_list", "process_list", "filament_list")}
        else:
            keep = _valid_keys(parts[1]) | KEEP_META
            data = {k: v for k, v in data.items() if k in keep}
        return _compact(data)
    return raw


def files():
    out = []
    for item in INCLUDE:
        p = os.path.join(ROOT, item)
        if os.path.isfile(p):
            out.append(item)
            continue
        for d, dirs, names in os.walk(p):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
            for n in sorted(names):
                if not n.endswith(".pyc") and n != "cli_config.json":
                    out.append(os.path.relpath(os.path.join(d, n), ROOT))
    return out


def write(zpath, prefix, extra=None):
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in files():
            i = zipfile.ZipInfo(prefix + rel.replace(os.sep, "/"), STAMP)
            i.compress_type = zipfile.ZIP_DEFLATED
            i.external_attr = (0o755 if rel.endswith(".py") and rel.startswith("scripts" + os.sep) else 0o644) << 16
            z.writestr(i, read_bytes(rel))
        for name, path in (extra or []):
            i = zipfile.ZipInfo(name, STAMP)
            i.external_attr = 0o644 << 16
            z.writestr(i, open(path, "rb").read())


def main(argv):
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    rest = list(argv)
    if "--out" in rest:
        i = rest.index("--out")
        if i + 1 >= len(rest) or rest[i + 1].startswith("-"):
            print("make_release.py: --out needs a folder\n" + __doc__.split("\n")[0], file=sys.stderr)
            return 2
        del rest[i:i + 2]
    if rest:
        print("make_release.py: unknown argument %s\n%s" % (" ".join(rest), __doc__.split("\n")[0]), file=sys.stderr)
        return 2
    out = argv[argv.index("--out") + 1] if "--out" in argv else os.path.join(ROOT, "dist")
    os.makedirs(out, exist_ok=True)
    write(os.path.join(out, "bambu-3mf-builder-skill.zip"), "bambu-3mf-builder/")
    write(os.path.join(out, "bambu-3mf-builder-plugin.zip"), "skills/bambu-3mf-builder/",
          [(".claude-plugin/plugin.json", os.path.join(ROOT, ".claude-plugin", "plugin.json"))])
    for n in ("bambu-3mf-builder-skill.zip", "bambu-3mf-builder-plugin.zip"):
        print("%s  %.1f MB" % (os.path.join(out, n), os.path.getsize(os.path.join(out, n)) / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
