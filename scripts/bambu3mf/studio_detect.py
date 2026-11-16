"""Find the installed Bambu Studio version (best effort). Returns "2.9.1.70" style text or None.

Mac: Info.plist of the app bundle. Windows / Linux / any: the "version" in the app config
(BambuStudio.conf, JSON) in the Studio data folder.
find_cli() finds the Studio command-line program (an explicit --studio-path, the BAMBU_STUDIO_PATH environment variable, then the
usual install places); slice_check() runs it on a built file. The path rules are tested with fake installs; the slice itself is tested
with a real Studio 2.8.2 and 2.9.1 on Linux and Studio 2.9.1 on macOS (tests/test_detect.py, tests/studio_slice.py, tests/machine_check.py).
Not yet run on a Windows install.
"""
import glob
import shutil
import subprocess
import tempfile
import json
import os
import plistlib
import re
import sys

_VER = re.compile(r"(\d{2})\.(\d{2})\.(\d{2})\.(\d{2})")


def _dotted(text):
    m = _VER.search(text or "")
    if not m:
        return None
    return ".".join(str(int(g)) for g in m.groups())


def from_plist(path):
    try:
        with open(path, "rb") as f:
            d = plistlib.load(f)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None
    for key in ("CFBundleShortVersionString", "CFBundleVersion"):
        v = _dotted(str(d.get(key, "")))
        if v:
            return v
    return None


def from_conf(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    app = d.get("app", {}) if isinstance(d, dict) else {}
    return _dotted(str(app.get("version", "")))


def data_dirs():
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        yield os.path.join(home, "Library", "Application Support", "BambuStudio")
    elif sys.platform.startswith("win"):
        yield os.path.join(os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming")), "BambuStudio")
    else:
        yield os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.join(home, ".config")), "BambuStudio")


def detect():
    if sys.platform == "darwin":
        for app in ("/Applications/BambuStudio.app", os.path.expanduser("~/Applications/BambuStudio.app"),
                    "/Applications/Bambu Studio.app"):
            v = from_plist(os.path.join(app, "Contents", "Info.plist"))
            if v:
                return v
    for d in data_dirs():
        v = from_conf(os.path.join(d, "BambuStudio.conf"))
        if v:
            return v
    return None


ENV_VAR = "BAMBU_STUDIO_PATH"


def _exe_in(path):
    """The program inside a path given as program, app bundle or install folder (extracted AppImage); None if there is none."""
    if not path:
        return None
    path = os.path.expanduser(path)
    cands = [path]
    if os.path.isdir(path):
        cands = [os.path.join(path, "Contents", "MacOS", "BambuStudio"), os.path.join(path, "bin", "bambu-studio"),
                 os.path.join(path, "bambu-studio.exe"), os.path.join(path, "bambu-studio"), os.path.join(path, "BambuStudio")]
    for c in cands:
        if os.path.isfile(c) and os.access(c, os.X_OK) or (os.path.isfile(c) and c.lower().endswith(".exe")):
            return c
    return None


def default_places():
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return ["/Applications/BambuStudio.app", os.path.join(home, "Applications", "BambuStudio.app"), "/Applications/Bambu Studio.app"]
    if sys.platform.startswith("win"):
        pf = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
        return [os.path.join(p, "Bambu Studio") for p in pf]
    places = [os.path.join(home, "Applications"), "/opt/BambuStudio", "/opt/bambu-studio", os.path.join(home, "BambuStudio")]
    found = []
    for d in places:
        found += glob.glob(os.path.join(d, "*.AppImage")) + glob.glob(os.path.join(d, "squashfs-root"))
        found.append(d)
    return found


def find_cli(override=None):
    """(program path, where it came from) or (None, reason). An explicit path that does not work is an error, never silently replaced."""
    if override:
        exe = _exe_in(override)
        return (exe, "--studio-path") if exe else (None, "--studio-path %s is not a Bambu Studio program, app or folder" % override)
    env = os.environ.get(ENV_VAR)
    if env:
        exe = _exe_in(env)
        return (exe, ENV_VAR) if exe else (None, "%s=%s is not a Bambu Studio program, app or folder" % (ENV_VAR, env))
    for pth in default_places():
        exe = _exe_in(pth)
        if exe:
            return exe, "install folder"
    w = shutil.which("bambu-studio") or shutil.which("BambuStudio")
    if w:
        return w, "PATH"
    return None, "no Bambu Studio found; pass --studio-path or set %s" % ENV_VAR


_TIME = re.compile(r"total estimated time:\s*([0-9dhms ]+?)\s*(?:;|$)")
_GRAMS = re.compile(r"total filament weight \[g\]\s*:\s*([0-9.]+)")


def read_gcode_header(path):
    """(print time text, grams) from a Bambu G-code file header; None for what is not there."""
    t = g = None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i > 400:
                    break
                m = _TIME.search(line)
                if m and t is None:
                    t = m.group(1).strip()
                m = _GRAMS.search(line)
                if m and g is None:
                    g = float(m.group(1))
                if t is not None and g is not None:
                    break
    except OSError:
        pass
    return t, g


def slice_check(exe, f3mf, timeout=900):
    """Slice a built file with the real Studio. Returns (ok, message). Needs a display on Linux (xvfb-run is used when there is none)."""
    ok, msg, _plates = slice_details(exe, f3mf, timeout)
    return ok, msg


def slice_details(exe, f3mf, timeout=900):
    """Like slice_check, and a third value: a list of {"plate": n, "time": text or None, "grams": number or None}, read from the G-code header."""
    out = tempfile.mkdtemp(prefix="bambu3mf_slice_")
    try:
        cmd = [exe, "--slice", "0", "--outputdir", out, os.path.abspath(f3mf)]
        env = dict(os.environ)
        if sys.platform.startswith("linux"):
            env["LD_LIBRARY_PATH"] = os.path.normpath(os.path.dirname(exe)) + os.pathsep + env.get("LD_LIBRARY_PATH", "")
            if not env.get("DISPLAY") and shutil.which("xvfb-run"):
                cmd = ["xvfb-run", "-a"] + cmd
        try:
            r = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, "Studio could not be run: %s" % e, []
        try:
            with open(os.path.join(out, "result.json")) as fh:
                res = json.load(fh)
        except (OSError, ValueError):
            res = None
        gcodes = sorted(glob.glob(os.path.join(out, "plate_*.gcode")))
        plates = []
        for gpath in gcodes:
            m = re.search(r"plate_(\d+)\.gcode$", gpath)
            t, g = read_gcode_header(gpath)
            plates.append({"plate": int(m.group(1)) if m else len(plates) + 1, "time": t, "grams": g})
        plates.sort(key=lambda p: p["plate"])
        if r.returncode == 0 and res and res.get("return_code") == 0 and gcodes:
            return True, "Studio sliced it: %d plate(s), no error" % len(gcodes), plates
        why = (res or {}).get("error_string") or r.stdout.decode("utf-8", "replace").strip().splitlines()[-1:] or "no result"
        return False, "Studio could not slice it (exit %s): %s" % (r.returncode, why), plates
    finally:
        shutil.rmtree(out, ignore_errors=True)
