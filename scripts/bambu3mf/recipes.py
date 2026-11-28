"""Setting recipes: a named group of real Studio process settings, chosen with `recipe = "name"` in a job.

A recipe only fills keys. The job's own [settings] always win. Every recipe key goes through the same checks as a user setting
(job._settings_table). A recipe is refused when Studio (the job's version) lacks a key, or does not accept a value.
The keys and values are listed in references/job-format.md ("Recipes"); tests/test_recipes.py checks that the two agree.
"""
import json
import os

from .profiles import STUDIO_VERSIONS, studio_root, min_studio

RECIPES = {
    "visible-face": {
        "purpose": "The best look on visible faces: hidden seam, smooth top surfaces.",
        "settings": {
            "wall_generator": "arachne",                         # variable-width walls, no gaps in thin features
            "wall_sequence": "inner-outer-inner wall",           # the outer wall is pressed against a printed inner wall on both sides
            "override_filament_scarf_seam_setting": True,        # GCode.cpp reads seam_slope_type only when this is on
            "seam_slope_type": "external",                       # scarf seam on the outer contour ("Contour" in Studio)
            "top_surface_pattern": "monotonic",                  # one direction per region: even light on the top
            "sub_top_surface_pattern": "monotonic",              # the layer under a top surface does not print through
            "ironing_type": "top",                               # iron the top surfaces
            "ironing_pattern": "ironingarchimedeanspiral",       # spiral ironing; no straight-line scan marks
        },
    },
    "strong": {
        "purpose": "Stronger parts: more walls, denser gyroid infill, thicker bottom.",
        "settings": {
            "wall_loops": 4,
            "sparse_infill_pattern": "gyroid",
            "sparse_infill_density": "30%",
            "bottom_shell_layers": 5,
        },
    },
    "fast-draft": {
        "purpose": "A quick draft: thick layers, little infill, few shell layers.",
        "settings": {
            "layer_height": 0.28,
            "sparse_infill_density": "8%",
            "top_shell_layers": 4,
            "bottom_shell_layers": 2,
        },
    },
}


def names():
    return sorted(RECIPES)


def _allowed(opt):
    return list(opt.get("enum_values") or opt.get("enum_keys") or [])


def _needs(key, value):
    """The lowest supported Studio version ("2.9.1") that has this key and accepts this value, or None."""
    for v in STUDIO_VERSIONS:
        with open(os.path.join(studio_root(), v, "options.json")) as f:
            for o in json.load(f)["options"]:
                if o["key"] == key:
                    ok = _allowed(o)
                    if not ok or str(value) in ok:
                        return ".".join(v.split(".")[:3])
    return None


def apply(raw_value, user_settings, by, ver, chk, settings_table, table=None):
    """Check `recipe = ...` and return (names, {key: value}) of the keys to add: recipe keys the job's [settings] did not set.
    `settings_table` is job._settings_table (passed in, so this module does not import job). `table` replaces RECIPES in tests."""
    table = RECIPES if table is None else table
    if raw_value is None:
        return [], {}
    given = [raw_value] if isinstance(raw_value, str) else raw_value
    if not isinstance(given, list) or not given or not all(isinstance(x, str) for x in given):
        chk.err("recipe", "must be a recipe name or a list of names. Recipes: %s" % ", ".join(sorted(table)))
        return [], {}
    wanted, merged, owner = [], {}, {}
    for name in given:
        if name not in table:
            chk.err("recipe", "unknown recipe %r. Recipes: %s" % (name, ", ".join(sorted(table))))
            continue
        if name in wanted:
            continue
        wanted.append(name)
        for key, val in table[name]["settings"].items():
            if key in merged and str(merged[key]) != str(val):
                chk.err("recipe", "recipes %s and %s set %s to different values (%r and %r). Use one of them, or set %s in [settings]." % (
                    owner[key], name, key, merged[key], val, key))
                continue
            merged.setdefault(key, val)
            owner.setdefault(key, name)
    out = {}
    for key, val in merged.items():
        if key in user_settings:
            continue                                  # the job wins
        opt = by.get(key)
        if opt is None:
            need = min_studio(key)
            chk.err("recipe", "recipe %s sets %s, which Studio %s does not have.%s" % (
                owner[key], key, ver, (" It needs Studio %s or newer. Set studio = \"%s\" in the job, or set the other keys yourself in [settings]." % (need, need)) if need
                else " The recipe cannot be used."))
            continue
        if opt.get("type") in ("coEnum", "coEnums") and _allowed(opt) and str(val) not in _allowed(opt):
            need = _needs(key, val)
            chk.err("recipe", "recipe %s sets %s = %r, which Studio %s does not accept.%s" % (
                owner[key], key, val, ver, (" It needs Studio %s or newer. Set studio = \"%s\" in the job." % (need, need)) if need else ""))
            continue
        out[key] = val
    checked = settings_table(out, ("print",), by, ver, "recipe", chk, "a recipe (process settings only)") if out else {}
    return wanted, checked
