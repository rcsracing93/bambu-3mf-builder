"""Validator modes. One table maps (finding class, mode) to a level.

Classes
  LOAD     Studio rejects the file or misreads it.
  CHANGE   Studio accepts the file and changes a value when it loads it.
  KEEP     Studio uses the data as it is, but the print may differ from what the reader expects (sunk part, open mesh).
  FORM     Not what Studio writes, but it loads the same.
  PRIVACY  Personal data in the file. Runs only with --privacy; never changes the exit code.
  SKIPPED  A rule group was not run (unsupported Studio version, multi-nozzle printer). A warning in both modes; never fails.
  TOOL     A limit of this tool. A note; never fails.

A finding keeps the level its call site gave it (error or warning). The mode table gives the highest level the
class may have in this mode; the finding gets the lower of the two. So strict mode (every class error) keeps
today's levels exactly, and default mode only lowers or hides findings.
"""
import re

LOAD, CHANGE, KEEP, FORM, PRIVACY, SKIPPED, TOOL = "LOAD", "CHANGE", "KEEP", "FORM", "PRIVACY", "SKIPPED", "TOOL"
CLASSES = (LOAD, CHANGE, KEEP, FORM, PRIVACY, SKIPPED, TOOL)
MODES = ("default", "strict")
ORDER = {"silent": 0, "note": 1, "warning": 2, "error": 3}

TABLE = {
    "default": {LOAD: "error", CHANGE: "warning", KEEP: "warning", FORM: "silent", PRIVACY: "warning", SKIPPED: "warning", TOOL: "note"},
    "strict": {LOAD: "error", CHANGE: "error", KEEP: "error", FORM: "error", PRIVACY: "warning", SKIPPED: "warning", TOOL: "note"},
}

# (rule, message regex, class). First match wins. A finding that matches nothing is LOAD (today's behaviour).
# A call site can also pass cls= when the class depends on the file (for example the nozzle count).
SITES = [
    ("R21", r"requiredextensions", FORM),
    ("R21", r"has no components", FORM),
    ("R21", r"is not an object in", FORM),
    ("R21", r"do not match the objects in the root model", FORM),
    ("R23", r"below the plate", KEEP),
    ("R22", r"running counter", FORM),
    ("R22", r"differs from Studio's scheme", FORM),
    ("R10", r"is not a system preset of this Studio version", TOOL),
    ("R13", r"project options missing", CHANGE),
    ("R15", r"setting\(s\) of this Studio version are missing from the config", CHANGE),
    ("R16", r"is not supported \(supported", SKIPPED),
    ("R24", r"an edge is used twice", KEEP),
    ("R24", r"the surface is not closed", KEEP),
    ("R24", r"closed-surface check was skipped", TOOL),
    ("R3", r"not in different_settings_to_system", CHANGE),
    ("R3", r"could not rebuild the system values", FORM),
    ("S1", r"would not survive opening.*not listed in different_settings_to_system", CHANGE),
    ("S1", r"would not survive opening.*project options missing", CHANGE),
    ("S1", r"system values could not be rebuilt", FORM),
    ("S1", r"project options missing from the file", CHANGE),
    ("R37", r"\(saved position\).*sticks out of the printable area", CHANGE),
    ("R33", r"Thumbnail\w* points to", FORM),
]
_COMPILED = [(r, re.compile(p, re.S), c) for r, p, c in SITES]


def classify(f):
    """The class of one finding: the one its call site gave, else the first matching row of SITES, else LOAD."""
    if getattr(f, "cls", None):
        return f.cls
    for rule, rx, cls in _COMPILED:
        if f.rule == rule and rx.search(f.msg):
            return cls
    return LOAD


def level_for(cls, mode, native):
    """The level of a finding of this class in this mode: the lower of the call site's level and the table's."""
    top = TABLE[mode][cls]
    return top if ORDER[top] < ORDER[native] else native


def apply(findings, mode):
    """Set .cls and .level on every finding for this mode. Returns (shown, silent)."""
    if mode not in MODES:
        raise ValueError("mode must be one of %s, not %r" % (", ".join(MODES), mode))
    shown, silent = [], []
    for f in findings:
        f.cls = classify(f)
        f.level = level_for(f.cls, mode, f.native)
        (silent if f.level == "silent" else shown).append(f)
    return shown, silent
