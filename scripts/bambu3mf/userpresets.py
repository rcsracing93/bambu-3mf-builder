"""User presets of Bambu Studio, read from the local Studio data folder at run time.

Studio keeps them as <data folder>/user/<id>/{filament,process,machine}/<name>.json. A file holds only the keys that differ
from its parent (Preset.cpp get_differed_values_to_update) plus "inherits" = the parent system preset. Studio loads it as the
parent's config with the file's keys on top (PresetCollection::load_presets). A file with an empty "inherits" is a preset
made from scratch; v1.1 refuses it. This module reads exactly one file per named preset, the file the job names, and nothing
else from the folder. It is only called when a job names a preset that is not a system preset.
Nothing read here is ever stored in the repo: no test, fixture or example holds a real user preset.

Where to look, first match wins: the --user-presets option, the BAMBU_STUDIO_USER_DIR variable, the Studio data folder of this
computer (studio_detect.data_dirs). The path may be the data folder (holds user/), the user folder, or one id folder.
"""
import json
import os

from . import studio_detect

ENV_VAR = "BAMBU_STUDIO_USER_DIR"
SUBDIR = {"filament": "filament", "process": "process", "machine": "machine"}


class UserPresetError(Exception):
    pass


def source(override=None):
    """(path, where it came from)."""
    if override:
        return os.path.expanduser(override), "--user-presets"
    env = os.environ.get(ENV_VAR)
    if env:
        return os.path.expanduser(env), ENV_VAR
    for d in studio_detect.data_dirs():
        return d, "the Bambu Studio data folder of this computer"
    return None, "no Studio data folder"


def id_folders(path, how):
    """The Studio user-id folders under path (data folder, user folder or one id folder)."""
    if not path or not os.path.isdir(path):
        raise UserPresetError("the Bambu Studio user folder was not found at %s (from %s). Pass the right folder with --user-presets "
                              "or set %s" % (path, how, ENV_VAR))
    if os.path.isdir(os.path.join(path, "user")):
        base = os.path.join(path, "user")
        ids = sorted(os.path.join(base, d) for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)))
    elif any(os.path.isdir(os.path.join(path, s)) for s in SUBDIR.values()):
        ids = [path]
    else:
        ids = sorted(os.path.join(path, d) for d in os.listdir(path) if os.path.isdir(os.path.join(path, d)))
    if not ids:
        raise UserPresetError("%s (from %s) holds no Bambu Studio user folder (expected user/<id>/filament and process)" % (path, how))
    return ids


def read(kind, name, override=None):
    """The raw JSON dict of the user preset file for (kind, name). Raises UserPresetError with a plain reason."""
    if kind not in SUBDIR:
        raise UserPresetError("no user presets of kind %s" % kind)
    if not isinstance(name, str) or not name or any(c in name for c in "/\\\0") or name in (".", "..") or ".." in name:
        raise UserPresetError("%r is not a valid preset name" % (name,))
    path, how = source(override)
    found = []
    for idf in id_folders(path, how):
        f = os.path.join(idf, SUBDIR[kind], name + ".json")
        if os.path.isfile(f):
            found.append(f)
    if not found:
        raise UserPresetError("no file %s/%s.json in the Bambu Studio user folder %s (from %s)" % (SUBDIR[kind], name, path, how))
    datas = []
    for f in found:
        try:
            with open(f, encoding="utf-8") as fh:
                datas.append(json.load(fh))
        except (OSError, ValueError) as e:
            raise UserPresetError("the %s user preset file could not be read: %s" % (kind, e))
    if any(d != datas[0] for d in datas[1:]):
        raise UserPresetError("%s %r exists in %d Studio user folders with different content. Pass the one you mean with --user-presets" % (
            kind, name, len(found)))
    d = datas[0]
    if not isinstance(d, dict):
        raise UserPresetError("the %s user preset file does not hold a preset" % kind)
    return d
