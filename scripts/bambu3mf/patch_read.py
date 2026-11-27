"""Patch input: open the 3MF to patch, apply the guards, read only the small entries.

Order : G1-G6 limits before any read; Application and version guards; nozzle count; entries the patch
needs; input validation in default mode (any error refuses). Mesh models and pictures are never parsed here.
"""
import json
import re

from . import rules_package, validate
from .patch_job import PatchRefused

ACCEPT = {(2, 8, 2): "2.8.2.61", (2, 9, 1): "2.9.1.70", (2, 9, 2): "2.9.2.62"}
SUPPORTED_BUILDS = ("02.08.02.61", "02.09.01.70", "02.09.02.62")
HEAD_CAP = 1024 ** 2
OPEN_IT = "Open it and save it in Bambu Studio 2.9.2 first."
EMBEDDED_RE = re.compile(r"^Metadata/(filament|process|machine)_settings_\d+\.config$")
GCODE_RE = re.compile(r"^Metadata/plate_\d+\.gcode$")
SLICED_ENTRY_RE = re.compile(r"^(Metadata/plate_\d+\.gcode(\.md5)?|Metadata/_rels/model_settings\.config\.rels)$")


class Source:
    """What the patcher knows about the input. No mesh, no picture."""


def _vtuple(text):
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)\.(\d+)$", str(text or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def read_head(pkg, name="3D/3dmodel.model", cap=HEAD_CAP):
    """The bytes of the root model before the first '<resources', or None if they are not within cap bytes."""
    buf = b""
    with pkg.z.open(pkg.index[name]) as f:
        while len(buf) < cap:
            chunk = f.read(65536)
            if not chunk:
                break
            buf += chunk
            i = buf.find(b"<resources")
            if i >= 0:
                return buf[:i]
    return None


def newline_of(data):
    return "\r\n" if b"\r\n" in data[:4096] else "\n"


def dump_config(cfg, nl="\n"):
    text = json.dumps(cfg, indent=4, ensure_ascii=False) + "\n"
    return (text.replace("\n", nl) if nl != "\n" else text).encode("utf-8")


def open_source(path):
    rep = validate.Report()
    try:
        pkg = validate.Pkg(path, rep)
    except Exception as e:                                          # noqa: BLE001 - untrusted file
        raise PatchRefused(["%s cannot be read as a ZIP file (%s: %s)" % (_base(path), type(e).__name__, str(e)[:100])])
    if pkg.z is None or rep.errors:
        raise PatchRefused(["%s: %s" % (f.rule, f.msg) for f in rep.errors] or ["the file is not a readable 3MF"])
    problems = []
    for need in ("Metadata/project_settings.config", "Metadata/model_settings.config", "3D/3dmodel.model"):
        if not pkg.has(need):
            problems.append("%s is missing. Patch mode needs a project saved by Bambu Studio. %s" % (need, OPEN_IT))
    if problems:
        raise PatchRefused(problems)
    odd = [i.filename for i in pkg.z.infolist() if not i.filename.isascii() and not i.flag_bits & 0x800]
    if odd:
        raise PatchRefused(["The ZIP has %d entry name(s) with non-ASCII bytes that are not marked as UTF-8 (ZIP flag bit 11), for example %s. "
                            "The name would be read in the old DOS code page and could not be copied as it is, so the file is not patched. %s" % (
                                len(odd), ascii(odd[0]), OPEN_IT)])
    s = Source()
    s.path, s.pkg, s.warnings = path, pkg, []
    head = read_head(pkg)
    if head is None:
        raise PatchRefused(["3D/3dmodel.model has no <resources within its first %d KiB. This is not a file Studio saved. %s" % (HEAD_CAP // 1024, OPEN_IT)])
    s.head = head
    m = re.search(rb'<metadata\s+name="Application"\s*>([^<]*)<', head)
    s.app = m.group(1).decode("utf-8", "replace").strip() if m else ""
    am = re.match(r"^BambuStudio-(\d+\.\d+\.\d+\.\d+)$", s.app)
    if not am:
        raise PatchRefused(["This file was saved by %r, not by Bambu Studio. Patch mode only accepts files saved by Bambu Studio 2.8.2, 2.9.1, 2.9.2 or a newer 2.x (Orca Slicer, PrusaSlicer and other programs are not supported). %s" % (s.app or "an unknown program", OPEN_IT)])
    raw_cfg = pkg.raw("Metadata/project_settings.config")
    if raw_cfg is None:
        raise PatchRefused(["project_settings.config is too large to read"])
    try:
        cfg = json.loads(raw_cfg.decode("utf-8"))
    except (ValueError, RecursionError) as e:
        raise PatchRefused(["project_settings.config is not valid JSON (%s). %s" % (e, OPEN_IT)])
    if not isinstance(cfg, dict) or not isinstance(cfg.get("version"), str):
        raise PatchRefused(["project_settings.config has no version. %s" % OPEN_IT])
    app_v = _vtuple(am.group(1))
    cfg_v = _vtuple(cfg["version"])
    if app_v is None or cfg_v is None or app_v[:3] != cfg_v[:3]:
        raise PatchRefused(["The file says it was saved by %s but its project settings are version %s. %s" % (s.app, cfg["version"], OPEN_IT)])
    s.build = am.group(1)
    s.newer = False
    if app_v[:3] in ACCEPT:
        s.tag = ACCEPT[app_v[:3]]
    else:
        newest = max(ACCEPT)
        if app_v[0] == newest[0] and app_v[:3] > newest:
            # 1.3.0 forward-compatible mode: a file from a newer 2.x is patched with the tables of the newest supported version
            s.tag, s.newer = ACCEPT[newest], True
            s.warnings.append("The file was saved by Studio %s, newer than the newest supported version. It is patched with the rules of %s. "
                              "Settings that %s does not know are kept as they are and reported as warnings. A builder update may follow." % (
                                  ".".join(str(x) for x in app_v), s.tag, s.tag))
        else:
            raise PatchRefused(["This file was saved by Bambu Studio %s. Patch mode accepts %s and newer 2.x. %s" % (
                ".".join(str(x) for x in app_v), ", ".join("%d.%d.%d" % k for k in sorted(ACCEPT)), OPEN_IT)])
    if s.build not in SUPPORTED_BUILDS and not s.newer:
        s.warnings.append("The file was saved by Studio build %s; the tables of build %s are used. Studio may treat a setting that the other build does not know differently." % (s.build, s.tag))
    nz = cfg.get("nozzle_diameter")
    if not isinstance(nz, list) or len(nz) != 1:
        raise PatchRefused(["The printer in this file has %s nozzles. Patch mode works on single-nozzle printers only (X2D and H2D files are refused)." % (len(nz) if isinstance(nz, list) else "an unknown number of")])
    fc = cfg.get("filament_colour")
    if not isinstance(fc, list) or not fc:
        raise PatchRefused(["project_settings.config has no filament_colour list. %s" % OPEN_IT])
    s.gcode = [n for n in pkg.names if GCODE_RE.match(n)]
    s.sliced = bool(s.gcode)
    s.sliced_drop = [n for n in pkg.names if SLICED_ENTRY_RE.match(n)] if s.sliced else []
    s.embedded = [n for n in pkg.names if EMBEDDED_RE.match(n)]
    s.cfg, s.cfg_raw = cfg, raw_cfg
    s.nl = newline_of(raw_cfg)
    s.cfg_roundtrip = dump_config(cfg, s.nl) == raw_cfg
    if not s.cfg_roundtrip:
        s.warnings.append("project_settings.config is not in Studio's own JSON layout; it is written in that layout in the output (the values are the same).")
    s.ms_raw = pkg.raw("Metadata/model_settings.config")
    if s.ms_raw is None:
        raise PatchRefused(["model_settings.config is too large to read"])
    scratch = validate.Report()
    ms = rules_package.parse_model_settings(pkg, scratch)
    why = [f.msg for f in scratch.errors + rep.errors]
    if ms is None or why:
        raise PatchRefused(["model_settings.config cannot be read: %s" % ("; ".join(why) or "it is not valid XML")])
    s.ms = ms
    s.nplates = max(len(ms["plates"]), 1)
    s.n = len(fc)
    return s


def _base(path):
    import os
    return os.path.basename(path)
