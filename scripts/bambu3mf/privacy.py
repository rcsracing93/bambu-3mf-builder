"""Personal-data scan for 3MF files (rule R33). Stdlib only. Used by the validator, the fixture sanitizer and CI."""
import json
import re
import zipfile

# root-model metadata that must be empty (account ids, names, MakerWorld ids). Application / 3mfVersion / dates are fine.
PERSONAL_META = {"Designer", "DesignerUserId", "DesignerCover", "DesignerRegion", "DesignModelId", "DesignProfileId",
                 "ProfileUserId", "ProfileUserName", "ProfileCover", "ProfileTitle", "ProfileDescription", "model_id",
                 "Region", "Origin", "Copyright", "CopyRight", "License", "Description"}
HOST_KEYS = ("print_host", "printhost_apikey", "printhost_user", "printhost_password", "printhost_port",
             "printhost_cafile", "print_host_webui")
PATH_RE = re.compile(r"(?<![\w.])(/(?:Users|home|root|var|mnt|private|tmp|Volumes)/[^\s\"<>;]+|[A-Za-z]:\\[^\s\"<>;]+|\\\\[\w.-]+\\[^\s\"<>;]+)")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
IP_RE = re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?![\d.])")
TEXT_EXT = (".config", ".model", ".xml", ".rels", ".json", ".txt")
# G-code in machine presets has ";===== date: ..." lines and example IPs; these keys are free-form preset text
SKIP_KEYS_FOR_PATTERNS = re.compile(r"gcode|filename_format|template|notes|description")


def shorten(s, n=40):
    return s if len(s) <= n else s[:n] + "..."


def scan_text(name, text):
    out = []
    for m in PATH_RE.finditer(text):
        out.append("%s: absolute path %s" % (name, shorten(m.group(0))))
    for m in EMAIL_RE.finditer(text):
        out.append("%s: e-mail address %s" % (name, shorten(m.group(0))))
    for m in IP_RE.finditer(text):
        v = m.group(0)
        if v.startswith(("0.", "127.")) or v == "255.255.255.255":
            continue
        if re.search(r"(?i)version\W{0,6}$", text[max(0, m.start() - 24):m.start()]):
            continue                         # a version number such as 02.08.02.61, not an address
        out.append("%s: IP address %s" % (name, v))
    return out


MAX_SCAN = 64 * 1024 ** 2   # bytes read from one text entry; a bigger text entry is reported, not read


def scan_zip(path_or_file):
    """Return a list of findings (strings). Empty list = clean."""
    out = []
    z = zipfile.ZipFile(path_or_file)

    class _Z:                   # bounded reader: never inflates more than MAX_SCAN bytes of one entry
        @staticmethod
        def read(name):
            with z.open(name) as f:
                return f.read(MAX_SCAN)
    zr = _Z()
    for info in z.infolist():
        n = info.filename
        if n.endswith(TEXT_EXT) and not n.startswith("3D/Objects/") and info.file_size > MAX_SCAN:
            out.append("%s: text entry of %.0f MiB is too large to scan for personal data" % (n, info.file_size / 1024 ** 2))
            continue
        if info.comment or info.extra:
            out.append("%s: ZIP entry carries an extra field or comment" % n)
        if n.startswith("Auxiliaries/"):
            out.append("%s: attachment (may carry cover images or documents)" % n)
        if n == "3D/3dmodel.model":
            txt = zr.read(n).decode("utf-8", "replace")
            for m in re.finditer(r' <metadata name="([^"]+)">(.*?)</metadata>', txt, flags=re.S):
                k, v = m.group(1), m.group(2)
                if (k in PERSONAL_META or k.startswith(("MakerLab", "Profile", "Designer"))) and v.strip() not in ("", "[]"):
                    out.append("3D/3dmodel.model: metadata %s is set (%s)" % (k, shorten(v)))
        elif n == "Metadata/project_settings.config":
            try:
                cfg = json.loads(zr.read(n).decode("utf-8"))
            except (ValueError, RecursionError):
                continue
            if not isinstance(cfg, dict):
                continue
            for k in HOST_KEYS:
                if cfg.get(k) not in (None, "", []):
                    out.append("%s: print host field %s is set" % (n, k))
            for k, v in cfg.items():
                if SKIP_KEYS_FOR_PATTERNS.search(k) or k == "version" or k.endswith("_version"):
                    continue
                s = v if isinstance(v, str) else json.dumps(v)
                out.extend(scan_text("%s [%s]" % (n, k), s))
        elif n == "Metadata/model_settings.config":
            txt = zr.read(n).decode("utf-8", "replace")
            for m in re.finditer(r'key="source_file" value="([^"]*)"', txt):
                if re.search(r"[\\/]", m.group(1)):
                    out.append("%s: source_file holds a path (%s)" % (n, shorten(m.group(1))))
            out.extend(scan_text(n, txt))
        elif n.endswith(TEXT_EXT) and not n.startswith("3D/Objects/"):
            out.extend(scan_text(n, zr.read(n).decode("utf-8", "replace")))
    return out
