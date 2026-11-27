"""Studio version policy: which supported version a job builds for, and what to tell the user (1.3.0 forward-compatible mode).

A new Studio release must never stop a user's build before the builder is updated. Studio opens a project saved by an older 2.x
release with its full config; only a file with a HIGHER MAJOR version than the app loads as geometry only (Plater.cpp, load_files,
Studio 2.9.2: "file_version.maj() > app_version.maj()"). So a file built for the newest supported 2.x opens in any newer 2.x with
every setting.

choose(requested, detected, supported, strict) -> Choice
  requested  "auto" or a supported tag (already resolved from the aliases)
  detected   installed Studio version text ("2.9.2.62") or None
  supported  the supported tags, oldest first
  strict     True keeps the 1.2.0 hard stop for any installed version that is not supported exactly
"""

NEWER_2X = ("Built for Studio {built}. Your Studio is {installed}. Studio opens this as an older project and keeps all settings. "
            "A builder update may follow.")
NEWER_MAJOR = ("Built for Studio {built}. Your Studio is {installed}, a newer MAJOR version. Studio {major}.x may open a {built_major}.x "
               "project as geometry only and drop its settings. Check the settings after you open the file. A builder update is needed "
               "for full support.")
BETWEEN = ("Built for Studio {built}, the nearest supported version below your Studio {installed}. Studio opens this as an older "
           "project and keeps all settings.")
UNREADABLE = "Could not read the installed Studio version ({text}). Built for Studio {built}."


class VersionError(Exception):
    pass


class Choice:
    def __init__(self, version, note=None, warning=None, auto_slice_check=False, mode="exact"):
        self.version = version                    # the supported tag the build uses
        self.note = note                          # plain information ("no Studio found")
        self.warning = warning                    # shown in the console and the summary file
        self.auto_slice_check = auto_slice_check  # newer major: run --slice-check when Studio is found
        self.mode = mode                          # exact | none | newer | newer-major | between | pinned | unreadable


def vtuple(text):
    """'2.9.2.62' -> (2, 9, 2, 62); None for anything that is not 4 dotted numbers."""
    try:
        parts = tuple(int(x) for x in str(text).strip().split("."))
    except (ValueError, AttributeError):
        return None
    return parts if len(parts) == 4 else None


def _strict_stop(detected, supported):
    return VersionError("Installed Bambu Studio is %s. Supported: %s. Update or set studio = \"%s\" in the job." % (
        detected, ", ".join(supported), "\" or \"".join(".".join(s.split(".")[:3]) for s in supported)))


def choose(requested, detected, supported, strict=False):
    supported = tuple(supported)
    newest = supported[-1]
    if requested != "auto":
        # a pinned version always builds; the installed Studio does not matter
        return Choice(requested, mode="pinned")
    if not detected:
        return Choice(newest, note="No Bambu Studio found on this computer; building for the newest supported version (%s)." % newest,
                      mode="none")
    if detected in supported:
        return Choice(detected)
    d = vtuple(detected)
    if strict:
        raise _strict_stop(detected, supported)
    if d is None:
        return Choice(newest, warning=UNREADABLE.format(text=detected, built=newest), mode="unreadable")
    tups = [vtuple(s) for s in supported]
    if d < tups[0]:
        raise VersionError("Installed Bambu Studio is %s, older than the oldest supported version (%s). Update Studio, or pin a "
                           "supported version in the job (studio = \"%s\") if you open the file on another computer. Supported: %s." % (
                               detected, supported[0], ".".join(supported[0].split(".")[:3]), ", ".join(supported)))
    if d[0] > tups[-1][0]:
        return Choice(newest, warning=NEWER_MAJOR.format(built=newest, installed=detected, major=d[0], built_major=tups[-1][0]),
                      auto_slice_check=True, mode="newer-major")
    if d > tups[-1]:
        return Choice(newest, warning=NEWER_2X.format(built=newest, installed=detected), mode="newer")
    below = [s for s, t in zip(supported, tups) if t <= d][-1]
    return Choice(below, warning=BETWEEN.format(built=below, installed=detected), mode="between")


def file_choice(file_version, supported):
    """Validator and patch mode: the supported tag whose tables apply to a file saved by Studio file_version.
    Returns (tag, kind) with kind exact | build (same x.y.z, other build) | newer (newer 2.x) | None when no tables apply."""
    supported = tuple(supported)
    if file_version in supported:
        return file_version, "exact"
    v = vtuple(file_version)
    if v is None:
        return None, None
    same = [s for s in supported if vtuple(s)[:3] == v[:3]]
    if same:
        return same[0], "build"
    newest = vtuple(supported[-1])
    if v[0] == newest[0] and v > newest:
        return supported[-1], "newer"
    return None, None
