"""Profile resolver for the bundled Bambu system presets.

Follows PresetBundle::load_vendor_configs_from_json (both supported tags):
  1. start from the parent preset (`inherits`), fully resolved; no parent = nothing (defaults are
     filled in later by the config builder from options.json),
  2. apply each `include` template, only the keys whose value differs from the default,
  3. apply the preset's own keys (whole-value replace; a "nil" slot stays "nil").
Keys that are not Studio options are dropped (Preset::remove_invalid_keys).
"""
import difflib
import json
import os
import unicodedata

from . import userpresets

STUDIO_VERSIONS = ("2.8.2.61", "2.9.1.70", "2.9.2.62")
KINDS = ("machine", "process", "filament")
# Studio 2.9.2 Tab.cpp TabPrintLayer: the height-range tab shows layer_height and PrintRegionConfig less cross_part_color_penetration.
RANGE_EXCLUDED = {"cross_part_color_penetration"}
# Studio 2.9.2 Tab.cpp TabPrintPart::toggle_options: shown only when every selected volume is a model part (not a modifier).
MODEL_PART_ONLY = {"cross_part_color_penetration"}
# Studio 2.9.2 PrintConfig.hpp (PrintObjectConfig, not PrintRegionConfig): Studio reads these per object. A part or a height range cannot set them.
OBJECT_ONLY = {"seam_embedded_headroom"}
_KEYS_OF = {}


def min_studio(key, root=None):
    """The lowest supported Studio version ("2.9.2") whose option table has this key, or None. A job for an older version that
    sets the key gets "needs Studio X or newer" (for example seam_embedded_headroom and cross_part_color_penetration, new in 2.9.2)."""
    for v in STUDIO_VERSIONS:
        if v not in _KEYS_OF:
            with open(os.path.join(root or studio_root(), v, "options.json")) as f:
                _KEYS_OF[v] = {o["key"] for o in json.load(f)["options"]}
        if key in _KEYS_OF[v]:
            return ".".join(v.split(".")[:3])
    return None
META_KEYS = {"type", "name", "inherits", "include", "from", "instantiation", "setting_id",
             "filament_id", "description", "version", "alias", "renamed_from"}


class ProfileError(Exception):
    pass


def studio_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "studio"))


class ProfileSet:
    def __init__(self, version, root=None):
        if version not in STUDIO_VERSIONS:
            raise ProfileError("Studio version %r is not supported. Supported: %s" % (version, ", ".join(STUDIO_VERSIONS)))
        self.version = version
        base = os.path.join(root or studio_root(), version)
        self.dir = os.path.join(base, "profiles")
        with open(os.path.join(base, "options.json")) as f:
            opts = json.load(f)
        self.options = {o["key"]: o for o in opts["options"]}
        self.project_options = set(opts["project_options"])
        self.region_options = set(opts.get("region_options") or [])   # PrintRegionConfig: what a part or a height range may set
        self.range_options = self.region_options - RANGE_EXCLUDED      # a height range: PrintRegionConfig less the excluded keys
        self.tag_version = opts["studio_tag"].lstrip("v")
        self.defaults = {o["key"]: o["default"] for o in opts["options"]}
        self.valid_keys = set(self.defaults)
        self._index = {k: {} for k in KINDS}      # kind -> name -> relative path
        with open(os.path.join(self.dir, "BBL.json")) as f:
            root_json = json.load(f)
        for kind in KINDS:
            for item in root_json.get("%s_list" % kind, []):
                self._index[kind][item["name"]] = item["sub_path"]
        self._file_cache = {}
        self._cache = {}
        self.user_enabled = False        # off unless the builder turns it on: the validator never reads the Studio data folder
        self.user_override = None

    # ---- lookup -------------------------------------------------------------------------
    def names(self, kind, instantiated_only=True):
        out = []
        for name in self._index[kind]:
            if not instantiated_only or self._load_file(kind, name).get("instantiation") == "true":
                out.append(name)
        return sorted(out)

    @property
    def bed_types(self):
        o = self.options["curr_bed_type"]
        return list(o.get("enum_values") or o.get("enum_keys") or [])

    def compatible(self, kind, printer, like=None):
        """Names of selectable presets made for this printer (optionally close to a given name)."""
        out = []
        for n in self.names(kind):
            cfg, _m = self.resolve(kind, n)
            cp = cfg.get("compatible_printers")
            if cp and printer in cp:
                out.append(n)
        if like:
            near = difflib.get_close_matches(like, out, n=8, cutoff=0.4)
            return near or out
        return out

    def user_compat_note(self, kind, name, printer):
        """Extra text for a "not made for" error on a user preset: Studio judges a user preset by its parent's list
        (Preset.cpp is_compatible_with_printer; the file stores compatible_printers only if the user changed it)."""
        try:
            _cfg, meta = self.resolve(kind, name)
        except ProfileError:
            return ""
        if not meta.get("user"):
            return ""
        parent = meta.get("parent", "")
        pcfg, _m = self.resolve(kind, parent)
        made = [x for x in self.compatible(kind, printer) if x != parent]
        same = [x for x in made if x.split(" @")[0] == parent.split(" @")[0]]
        return (" %r is your user preset. Its parent %r is made for %s in Studio %s, so Studio also hides it when %r is selected. "
                "A preset saved in an older Studio can have a parent that no longer covers your printer. "
                "Fix: in Studio select %r, pick %s, set your values, and Save As a new preset name." % (
                    name, parent, ", ".join(pcfg.get("compatible_printers") or []) or "no printer", self.version, printer,
                    printer, repr(same[0]) if same else "a system preset made for it"))

    @staticmethod
    def _squash(name):
        """Name with case, spaces and invisible characters removed. Used only to explain a miss, never to match."""
        s = unicodedata.normalize("NFKC", name)
        return "".join(c for c in s.casefold() if not c.isspace() and unicodedata.category(c) != "Cf")

    def suggest(self, kind, name, n=3):
        """The n closest selectable preset names. A name that differs only in case or spaces comes first."""
        if not isinstance(name, str):
            return []
        names = self.names(kind)
        sq = self._squash(name)
        same = [x for x in names if self._squash(x) == sq]
        rest = difflib.get_close_matches(name, [x for x in names if x not in same], n=n, cutoff=0.5)
        return (same + rest)[:n]

    def _miss_note(self, kind, name, hint):
        if hint and self._squash(hint[0]) == self._squash(name):
            why = "only case, spaces or hidden characters differ" if name.isascii() else "only case, spaces or non-ASCII characters differ"
            return " Names are exact, including case and spaces (%s from %r)." % (why, hint[0])
        return " Names must match exactly, including case and spaces." if hint else ""

    def _load_file(self, kind, name):
        key = (kind, name)
        if key not in self._file_cache:
            sub = self._index[kind].get(name)
            if sub is None:
                hint = self.suggest(kind, name) if kind in self._index else []
                raise ProfileError("No %s preset named %r in Studio %s.%s%s" % (
                    kind, name, self.version, self._miss_note(kind, name, hint),
                    (" Closest: " + "; ".join(hint)) if hint else ""))
            with open(self._preset_path(sub)) as f:
                data = json.load(f)
            self._file_cache[key] = self._aux_fan_fallback(data)
        return self._file_cache[key]

    def _preset_path(self, sub):
        """The preset file. The repo has every file of every version. The release zip stores a preset file once: a file that is
        identical (after the release trim) to the same file of the previous version is left out, and profiles/BASE names that
        version (scripts/make_release.py). Only a file that BBL.json of this version lists is read, so the chain never finds a
        preset that this version does not have."""
        d = self.dir
        for _ in range(len(STUDIO_VERSIONS)):
            p = os.path.join(d, "BBL", sub)
            if os.path.exists(p):
                return p
            try:
                with open(os.path.join(d, "BASE")) as f:
                    base = f.read().strip()
            except OSError:
                break
            if base not in STUDIO_VERSIONS:
                break
            d = os.path.join(os.path.dirname(os.path.dirname(d)), base, "profiles")
        return os.path.join(self.dir, "BBL", sub)            # the open fails with the path of this version

    # Studio 2.9.1 (Config.cpp, s_aux_fan_standard_to_optional_keys, applied to every config level as it loads):
    # the optional auxiliary fan has no entry in any profile, so a level that sets the standard fan key and not the
    # optional one gets the optional key with the same value. 2.8.2 has no optional keys.
    AUX_FAN_KEYS = (("additional_cooling_fan_speed", "optional_cooling_fan_speed"),
                    ("close_additional_fan_first_x_layers", "close_optional_fan_first_x_layers"),
                    ("first_x_layer_fan_speed", "first_x_layer_optional_fan_speed"),
                    ("additional_fan_full_speed_layer", "optional_fan_full_speed_layer"))

    def _aux_fan_fallback(self, data):
        for std, opt in self.AUX_FAN_KEYS:
            if opt in self.defaults and std in data and opt not in data:
                data = dict(data)
                data[opt] = data[std]
        return data

    # ---- user presets ----------------------------------------------------------
    def enable_user_presets(self, override=None):
        """Let resolve() fall back to the user's own Studio preset file when a name is not a system preset."""
        self.user_enabled = True
        self.user_override = override

    USER_KINDS = {"filament": "filament", "process": "print"}      # kind -> option owner

    def _resolve_user(self, kind, name, sys_err):
        """A user preset: its parent system preset fully resolved, the file's own keys on top (PresetCollection::load_presets).
        meta gets user = True and parent = the system preset named by the file's "inherits"."""
        def fail(why):
            raise ProfileError("%s As a user preset it cannot be used: %s" % (sys_err, why))
        if kind not in self.USER_KINDS:
            fail("user printer presets are not supported in this version. Use the system printer preset.")
        try:
            raw = userpresets.read(kind, name, self.user_override)
        except userpresets.UserPresetError as e:
            fail(str(e))
        parent = raw.get("inherits") if isinstance(raw.get("inherits"), str) else ""
        if not parent:
            fail("the preset has no parent (\"inherits\" is empty, it was made from scratch). Only user presets made from a system preset "
                 "are supported in this version. Save the preset in Studio from a Bambu system preset first.")
        if parent not in self._index[kind]:
            fail("its parent %r is not a Bambu system preset of Studio %s" % (parent, self.version))
        cfg, pmeta = self._resolve(kind, parent, ())
        cfg = dict(cfg)
        owner = self.USER_KINDS[kind]
        dropped = []
        for k, v in self._aux_fan_fallback(raw).items():
            if k in META_KEYS:
                continue
            o = self.options.get(k)
            if o is None or o["preset"] != owner:
                dropped.append(k)
                continue
            cfg[k] = v
        meta = {"name": name, "kind": kind, "setting_id": "", "filament_id": pmeta.get("filament_id", ""),
                "chain": [name] + pmeta["chain"], "instantiation": "true", "from": "User", "dropped": sorted(dropped),
                "user": True, "parent": parent}
        return cfg, meta

    # ---- resolve ------------------------------------------------------------------------
    def resolve(self, kind, name):
        """Return (config, meta). config: key -> str | list[str]. meta: name, setting_id, filament_id,
        chain (list of names child first), instantiation, dropped (keys removed)."""
        if not isinstance(name, str):
            raise ProfileError("%s preset name must be a string, found %r" % (kind, name))
        key = (kind, name)
        if key in self._cache:
            cfg, meta = self._cache[key]
            return dict(cfg), dict(meta)
        if self.user_enabled and name not in self._index.get(kind, {}):
            try:
                cfg, meta = self._resolve(kind, name, ())
            except ProfileError as e:
                cfg, meta = self._resolve_user(kind, name, str(e))
        else:
            cfg, meta = self._resolve(kind, name, ())
        self._cache[key] = (cfg, meta)
        return dict(cfg), dict(meta)

    def _resolve(self, kind, name, stack):
        if name in stack:
            raise ProfileError("Preset loop: %s" % " -> ".join(stack + (name,)))
        raw = self._load_file(kind, name)
        parent = raw.get("inherits") or ""
        if parent:
            cfg, pmeta = self._resolve(kind, parent, stack + (name,))
            cfg = dict(cfg)
            filament_id = pmeta.get("filament_id", "")
            chain = [name] + pmeta["chain"]
        else:
            cfg, filament_id, chain = {}, "", [name]
        includes = raw.get("include") or []
        if isinstance(includes, str):
            includes = [includes]
        for inc in includes:
            inc_raw = self._load_file(kind, inc)
            for k, v in inc_raw.items():
                if k in META_KEYS:
                    continue
                if k in self.defaults and self.defaults[k] == v:
                    continue
                cfg[k] = v
        for k, v in raw.items():
            if k in META_KEYS:
                continue
            cfg[k] = v
        if raw.get("filament_id"):
            filament_id = raw["filament_id"]
        dropped = sorted(k for k in cfg if k not in self.valid_keys)
        for k in dropped:
            del cfg[k]
        meta = {"name": name, "kind": kind, "setting_id": raw.get("setting_id", ""), "filament_id": filament_id,
                "chain": chain, "instantiation": raw.get("instantiation", ""), "from": raw.get("from", ""),
                "dropped": dropped}
        return cfg, meta
