#!/usr/bin/env python3
"""Copy the Bambu vendor profiles of one Studio tag into studio/<version>/profiles/.

Usage: vendor_profiles.py <studio-checkout> <tag> <out-folder>
Copies resources/profiles/BBL.json, the model files (BBL/*.json) and BBL/{machine,process,filament}/*.json
unchanged. Images are skipped. Writes <out-folder>/../SOURCE with the tag and commit.
"""
import os, shutil, subprocess, sys

def main(argv):
    if len(argv) != 4:
        print(__doc__); return 2
    src, tag, out = argv[1], argv[2], argv[3]
    prof = os.path.join(src, "resources", "profiles")
    dst = os.path.join(out, "BBL")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(dst)
    shutil.copy2(os.path.join(prof, "BBL.json"), os.path.join(out, "BBL.json"))
    n = 0
    for name in sorted(os.listdir(os.path.join(prof, "BBL"))):
        p = os.path.join(prof, "BBL", name)
        if os.path.isfile(p) and name.endswith(".json"):
            shutil.copy2(p, os.path.join(dst, name)); n += 1
    for sub in ("machine", "process", "filament"):
        top = os.path.join(prof, "BBL", sub)
        for dirpath, _dirs, files in sorted(os.walk(top)):
            rel = os.path.relpath(dirpath, top)
            os.makedirs(os.path.join(dst, sub, rel) if rel != "." else os.path.join(dst, sub), exist_ok=True)
            for name in sorted(files):
                if name.endswith(".json"):
                    shutil.copy2(os.path.join(dirpath, name), os.path.join(dst, sub, rel, name) if rel != "." else os.path.join(dst, sub, name)); n += 1
    try:
        commit = subprocess.run(["git", "-C", src, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        commit = "unknown"
    with open(os.path.join(os.path.dirname(os.path.abspath(out)), "SOURCE"), "w") as f:
        f.write("repository: https://github.com/bambulab/BambuStudio\ntag: %s\ncommit: %s\nfiles: resources/profiles/BBL.json, BBL/*.json, BBL/{machine,process,filament}/*.json (copied unchanged, %d files)\nlicense: AGPL-3.0 (BambuStudio)\n" % (tag, commit, n + 1))
    print("copied", n + 1, "files to", out)
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))
