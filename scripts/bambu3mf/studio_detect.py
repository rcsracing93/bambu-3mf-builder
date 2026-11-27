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
_GRAMS = re.compile(r"total filament weight \[g\]\s*:\s*([0-9.,]+)")
_LENGTH = re.compile(r"total filament length \[mm\]\s*:\s*([0-9.,]+)")
_COLOURS = re.compile(r"^; filament_colour = (.*)$")


def _numbers(text):
    try:
        return [float(x) for x in text.split(",") if x != ""]
    except ValueError:
        return []


def read_gcode_usage(path):
    """Read a Bambu G-code file header. Returns {"time": text or None, "grams": [per filament], "mm": [per filament],
    "colours": [per filament]}. A list is empty for what is not there."""
    out = {"time": None, "grams": [], "mm": [], "colours": []}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i > 400:
                    break
                m = _TIME.search(line)
                if m and out["time"] is None:
                    out["time"] = m.group(1).strip()
                m = _GRAMS.search(line)
                if m and not out["grams"]:
                    out["grams"] = _numbers(m.group(1))
                m = _LENGTH.search(line)
                if m and not out["mm"]:
                    out["mm"] = _numbers(m.group(1))
                m = _COLOURS.match(line.rstrip("\n"))
                if m and not out["colours"]:
                    out["colours"] = [c.strip() for c in m.group(1).split(";")]
    except OSError:
        pass
    return out


def read_gcode_header(path):
    """(print time text, grams) from a Bambu G-code file header; None for what is not there. Grams is the sum over all filaments."""
    u = read_gcode_usage(path)
    return u["time"], (sum(u["grams"]) if u["grams"] else None)


def format_time(seconds):
    """Studio's style: 17m 53s, 1h 5m 10s, 1d 2h 3m 4s."""
    s = int(round(seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    parts = [("%dd" % d) if d else "", ("%dh" % h) if (d or h) else "", ("%dm" % m) if (d or h or m) else "", "%ds" % s]
    return " ".join(p for p in parts if p)


def merge_plate_usage(plate_id, res_plate, gcode_path):
    """One plate dict. res_plate is a sliced_plates entry of result.json (or None), gcode_path the plate's G-code file.
    Time and grams come from result.json (total_predication in seconds, filaments[].total_used_g). Metres and colours are not
    in result.json: they come from the G-code header, as do time and grams when result.json lacks them."""
    gc = read_gcode_usage(gcode_path)
    rp = res_plate or {}
    secs = rp.get("total_predication")
    secs = float(secs) if isinstance(secs, (int, float)) else None
    rf = [f for f in (rp.get("filaments") or []) if isinstance(f, dict) and isinstance(f.get("total_used_g"), (int, float))]
    fils = []
    if rf:
        for f in sorted(rf, key=lambda f: f.get("id", 0)):
            k = int(f.get("id", len(fils) + 1))
            mm = gc["mm"][k - 1] if 0 < k <= len(gc["mm"]) else None
            col = gc["colours"][k - 1] if 0 < k <= len(gc["colours"]) else None
            fils.append({"slot": k, "grams": float(f["total_used_g"]), "metres": (mm / 1000.0) if mm is not None else None, "colour": col})
    else:
        for k, g in enumerate(gc["grams"], 1):
            mm = gc["mm"][k - 1] if k <= len(gc["mm"]) else None
            fils.append({"slot": k, "grams": g, "metres": (mm / 1000.0) if mm is not None else None,
                         "colour": gc["colours"][k - 1] if k <= len(gc["colours"]) else None})
    from_json = []
    if secs is not None: from_json.append("time")
    if rf: from_json.append("grams")
    return {"plate": plate_id, "time": format_time(secs) if secs is not None else gc["time"], "seconds": secs,
            "grams": sum(f["grams"] for f in fils) if fils else None, "filaments": fils,
            "source": "result.json" if from_json == ["time", "grams"] else "G-code header"}


def _g(x):
    return ("%.2f" % x) if x is not None else "n/a"


def usage_lines(plates):
    """Console lines for a list of plate dicts (indented two spaces)."""
    L = []
    for pl in plates:
        L.append("  plate %d: print time %s, filament %s g" % (pl["plate"], pl["time"] or "n/a", _g(pl["grams"])))
        for f in pl.get("filaments") or []:
            L.append("    filament %d%s: %s g, %s m" % (f["slot"], (" (%s)" % f["colour"]) if f.get("colour") else "", _g(f["grams"]), _g(f.get("metres"))))
        if pl.get("filaments") and pl.get("source") == "G-code header":
            L.append("    (Studio's result.json had no usage values; read from the G-code header)")
    if len(plates) > 1:
        secs = [p.get("seconds") for p in plates]
        tt = format_time(sum(secs)) if all(s is not None for s in secs) else "n/a"
        gs = [p["grams"] for p in plates]
        ms = [f.get("metres") for p in plates for f in p.get("filaments") or []]
        L.append("  total: print time %s, filament %s g, %s m" % (tt, _g(sum(gs)) if all(g is not None for g in gs) else "n/a",
                 _g(sum(ms)) if ms and all(m is not None for m in ms) else "n/a"))
    return L


def usage_table_md(plates):
    """Markdown lines: one table row per filament slot of each plate."""
    rows = [(p, f) for p in plates for f in p.get("filaments") or []]
    if not rows:
        return []
    L = ["", "| Plate | Print time | Slot | Colour | Grams | Metres |", "| --- | --- | --- | --- | --- | --- |"]
    for p, f in rows:
        L.append("| %d | %s | %d | %s | %s | %s |" % (p["plate"], p["time"] or "n/a", f["slot"], f.get("colour") or "n/a", _g(f["grams"]), _g(f.get("metres"))))
    if len(plates) > 1:
        L.append("| total | %s | | | %s | %s |" % (
            format_time(sum(p["seconds"] for p in plates)) if all(p.get("seconds") is not None for p in plates) else "n/a",
            _g(sum(p["grams"] for p in plates if p["grams"] is not None)),
            _g(sum(f["metres"] for _p, f in rows if f.get("metres") is not None))))
    if any(p.get("source") == "G-code header" for p in plates):
        L += ["", "Studio's result.json had no usage values for some plates. Those values come from the G-code header."]
    return L


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
            pid = int(m.group(1)) if m else len(plates) + 1
            rps = [p for p in ((res or {}).get("sliced_plates") or []) if isinstance(p, dict) and p.get("id") == pid]
            plates.append(merge_plate_usage(pid, rps[0] if rps else None, gpath))
        plates.sort(key=lambda p: p["plate"])
        if r.returncode == 0 and res and res.get("return_code") == 0 and gcodes:
            return True, "Studio sliced it: %d plate(s), no error" % len(gcodes), plates
        why = (res or {}).get("error_string") or r.stdout.decode("utf-8", "replace").strip().splitlines()[-1:] or "no result"
        return False, "Studio could not slice it (exit %s): %s" % (r.returncode, why), plates
    finally:
        shutil.rmtree(out, ignore_errors=True)
