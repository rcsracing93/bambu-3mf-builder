"""Release size test. Run: python3 -I tests/test_release_size.py
Fails if the skill zip or the plugin zip is over 10 MB unpacked (the Claude app limit is not published; the API limit is 30 MB)."""
import os, subprocess, sys, tempfile, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
LIMIT = 10 * 1000 * 1000
fails = []
with tempfile.TemporaryDirectory() as out:
    r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "..", "scripts", "make_release.py"), "--out", out], capture_output=True, text=True)
    if r.returncode:
        print(r.stdout, r.stderr); sys.exit(1)
    for n in ("bambu-3mf-builder-skill.zip", "bambu-3mf-builder-plugin.zip"):
        z = zipfile.ZipFile(os.path.join(out, n))
        size = sum(i.file_size for i in z.infolist())
        ok = size <= LIMIT
        print("%s %s: %.2f MB unpacked, %.2f MB zipped (limit %.0f MB)" % ("ok  " if ok else "FAIL", n, size / 1e6, os.path.getsize(z.filename) / 1e6, LIMIT / 1e6))
        if not ok:
            fails.append(n)
# nit: --help and an unknown argument build nothing (exit 0 / exit 2)
import glob
MK = os.path.join(HERE, "..", "scripts", "make_release.py")
before = set(glob.glob(os.path.join(HERE, "..", "dist", "*")))
for args, rc in ((["--help"], 0), (["--bogus"], 2), (["--out"], 2)):
    r = subprocess.run([sys.executable, "-I", MK] + args, capture_output=True, text=True)
    ok = r.returncode == rc and "make_release.py" in (r.stdout + r.stderr) and set(glob.glob(os.path.join(HERE, "..", "dist", "*"))) == before
    print("%s make_release.py %s: exit %d (want %d), nothing built" % ("ok  " if ok else "FAIL", " ".join(args), r.returncode, rc))
    if not ok:
        fails.append(" ".join(args))
sys.exit(1 if fails else 0)
