#!/usr/bin/env python3
"""--slice-check and --studio-path through a real Studio: a good job prints "sliced" and exits 0, a broken file makes it exit 1.
Usage: python3 tests/studio_cli.py --studio-dir DIR"""
import argparse, os, shutil, subprocess, sys, tempfile
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, H)
import mutations as M  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--studio-dir", required=True)
a = ap.parse_args()
cli = os.path.join(H, "..", "scripts", "build_bambu_3mf.py")
tmp = tempfile.mkdtemp()
out = os.path.join(tmp, "good.3mf")
r = subprocess.run([sys.executable, cli, os.path.join(H, "data", "mutation_base.toml"), "--out", out, "--slice-check", "--studio-path", a.studio_dir],
                   capture_output=True, text=True)
ok1 = r.returncode == 0 and "Studio sliced it" in r.stdout
print("%s good file: exit %d, %s" % ("ok  " if ok1 else "FAIL", r.returncode, r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-200:]))
bad = os.path.join(tmp, "bad.3mf")
shutil.copy(out, bad)
M.tower_on_part(bad)
sys.path.insert(0, os.path.join(H, "..", "scripts"))
from bambu3mf import studio_detect as D  # noqa: E402
exe, how = D.find_cli(a.studio_dir)
ok, msg = D.slice_check(exe, bad)
ok2 = not ok and "could not slice" in msg
print("%s broken file (tower on a part): %s" % ("ok  " if ok2 else "FAIL", msg[:110]))
r = subprocess.run([sys.executable, cli, os.path.join(H, "data", "mutation_base.toml"), "--out", os.path.join(tmp, "x.3mf"), "--slice-check", "--studio-path", "/nonexistent"],
                   capture_output=True, text=True)
ok3 = r.returncode == 1 and "/nonexistent" in r.stderr
print("%s wrong --studio-path: exit %d, %s" % ("ok  " if ok3 else "FAIL", r.returncode, r.stderr.strip()[:90]))
sys.exit(0 if ok1 and ok2 and ok3 else 1)
