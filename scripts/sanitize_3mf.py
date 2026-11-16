#!/usr/bin/env python3
"""Remove personal data from a Bambu Studio 3MF so it can be committed as a test fixture.

Usage: sanitize_3mf.py in.3mf out.3mf [--report]

What it does (allowlist, not blocklist):
  * root model: keeps only Application and BambuStudio:3mfVersion; every other metadata entry is written empty
    (account ids, designer names, model ids, titles, descriptions); dates are set to 2000-01-01
  * drops thumbnails (plate/top/pick PNGs) and the relationships and plate entries that point to them
  * model_settings.config: source_file becomes a bare file name
  * project_settings.config: print host fields (address, user, password, API key, certificate path) are emptied
  * ZIP: fixed timestamps and order, no extra fields
It refuses (exit 2) if a value that still looks like an absolute path, e-mail address or IP address remains.
The settings themselves are not changed, so a fixture still shows what Studio really wrote."""
import json
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bambu3mf import privacy                                    # noqa: E402

KEEP_META = {"Application", "BambuStudio:3mfVersion"}
THUMB = re.compile(r"^Metadata/(plate_\d+(_small)?|plate_no_light_\d+|top_\d+|pick_\d+)\.png$")


def sanitize(src, dst):
    zin = zipfile.ZipFile(src)
    out = []
    found = []
    for info in zin.infolist():
        name = info.filename
        if THUMB.match(name):
            found.append("%s: thumbnail removed" % name)
            continue
        data = zin.read(name)
        if name == "3D/3dmodel.model":
            txt = data.decode("utf-8")
            def meta(m):
                key, val = m.group(1), m.group(2)
                if key in KEEP_META:
                    return m.group(0)
                if key in ("CreationDate", "ModificationDate"):
                    val2 = "2000-01-01"
                elif key.startswith("Thumbnail"):
                    found.append("%s: dropped" % key)
                    return ""
                else:
                    val2 = ""
                if val != val2:
                    found.append("%s: %s" % (key, "set" if val else "empty"))
                return ' <metadata name="%s">%s</metadata>\n' % (key, val2)
            txt = re.sub(r' <metadata name="([^"]+)">(.*?)</metadata>\n', meta, txt, flags=re.S)
            data = txt.encode("utf-8")
        elif name == "_rels/.rels":
            txt = data.decode("utf-8")
            kept = [l for l in txt.splitlines() if "/Metadata/" not in l]
            data = ("\n".join(kept) + "\n").encode("utf-8") if len(kept) != len(txt.splitlines()) else data
        elif name == "Metadata/model_settings.config":
            txt = data.decode("utf-8")
            txt = re.sub(r'\n[ \t]*<metadata key="(thumbnail_file|thumbnail_no_light_file|top_file|pick_file)" value="[^"]*"/>', "", txt)
            def src_file(m):
                v = m.group(1)
                b = re.split(r"[\\/]", v)[-1]
                if b != v:
                    found.append("source_file: path removed (%s)" % privacy.shorten(v))
                return '<metadata key="source_file" value="%s"/>' % b
            txt = re.sub(r'<metadata key="source_file" value="([^"]*)"/>', src_file, txt)
            data = txt.encode("utf-8")
        elif name == "Metadata/project_settings.config":
            cfg = json.loads(data.decode("utf-8"))
            for k in privacy.HOST_KEYS:
                if k in cfg and cfg[k] not in ("", [], None):
                    found.append("project_settings %s: emptied" % k)
                    cfg[k] = ""
            data = (json.dumps(cfg, indent=4, ensure_ascii=False) + "\n").encode("utf-8")
        out.append((name, data))
    tmp = dst + ".part"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zo:
        for name, data in out:
            zi = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            zo.writestr(zi, data, compresslevel=6)
    leftovers = privacy.scan_zip(tmp)
    if leftovers:
        os.remove(tmp)
        return found, leftovers
    os.replace(tmp, dst)
    return found, []


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip()); return 2
    found, left = sanitize(argv[0], argv[1])
    for f in found:
        print("  " + f)
    if left:
        print("NOT written. Personal data is still present:")
        for l in left:
            print("  " + l)
        return 2
    print("wrote " + argv[1])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
