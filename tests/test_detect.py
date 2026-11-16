#!/usr/bin/env python3
"""Studio program detection with fake installs (no Studio needed): override order, bad overrides are errors, app bundle and install folder forms."""
import os, stat, sys, tempfile
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
from bambu3mf import studio_detect as D  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)) if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


def fake(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write("#!/bin/sh\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


tmp = tempfile.mkdtemp()
mac = fake(os.path.join(tmp, "BambuStudio.app", "Contents", "MacOS", "BambuStudio"))
lin = fake(os.path.join(tmp, "squashfs-root", "bin", "bambu-studio"))
plain = fake(os.path.join(tmp, "somewhere", "bs"))
saved = os.environ.pop(D.ENV_VAR, None)
try:
    check("app bundle path gives the program inside", D.find_cli(os.path.join(tmp, "BambuStudio.app")) == (mac, "--studio-path"))
    check("install folder gives bin/bambu-studio", D.find_cli(os.path.join(tmp, "squashfs-root")) == (lin, "--studio-path"))
    check("a program path is used as given", D.find_cli(plain) == (plain, "--studio-path"))
    exe, why = D.find_cli(os.path.join(tmp, "nothing"))
    check("a wrong --studio-path is an error and names the path", exe is None and "nothing" in why, why)
    os.environ[D.ENV_VAR] = lin
    check("the environment variable is used", D.find_cli() == (lin, D.ENV_VAR))
    check("--studio-path wins over the environment variable", D.find_cli(plain) == (plain, "--studio-path"))
    os.environ[D.ENV_VAR] = os.path.join(tmp, "nothing")
    exe, why = D.find_cli()
    check("a wrong environment variable is an error, not a silent fallback", exe is None and D.ENV_VAR in why, why)
    del os.environ[D.ENV_VAR]
    exe, why = D.find_cli()
    check("without any install the message says what to do", exe is not None or "--studio-path" in why, why)
    ok, msg = D.slice_check(plain, os.path.join(tmp, "x.3mf"), timeout=20)
    check("slice_check of a program that does nothing fails with a message", not ok and "could not slice" in msg, msg)
finally:
    if saved is not None:
        os.environ[D.ENV_VAR] = saved
print("\n%d failed" % len(fails))
sys.exit(1 if fails else 0)
