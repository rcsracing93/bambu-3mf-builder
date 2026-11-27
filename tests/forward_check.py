#!/usr/bin/env python3
"""Forward-compatible mode with the REAL installed Studio (run on a computer with Bambu Studio installed).
Every example is built with studio = "auto" and --slice-check. Graded per example:
  - build exit 0, the 3MF exists,
  - when the installed Studio is newer than the newest supported version: the forward warning is printed and is in the summary file,
  - the slice check passed (Studio sliced the file).
Usage: python3 tests/forward_check.py [--studio-path PATH] [--report FILE] [--only name,name]
Exit 0 when every example passed and at least one was checked."""
import os, subprocess, sys, tempfile, time

H = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(H, ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, H)
from bambu3mf import studio_detect, versions  # noqa: E402
from bambu3mf.profiles import STUDIO_VERSIONS  # noqa: E402
from compare_release import job_copy  # noqa: E402


def main(argv):
    report = argv[argv.index("--report") + 1] if "--report" in argv else None
    spath = argv[argv.index("--studio-path") + 1] if "--studio-path" in argv else None
    only = set(argv[argv.index("--only") + 1].split(",")) if "--only" in argv else None
    det = studio_detect.detect()
    choice = versions.choose("auto", det, STUDIO_VERSIONS)
    expect_warn = choice.mode in ("newer", "newer-major", "between")
    tmp = tempfile.mkdtemp()
    lines = ["Installed Studio: %s. Builder supports: %s. Mode: %s (builds for %s)." % (det, ", ".join(STUDIO_VERSIONS), choice.mode, choice.version), ""]
    examples = sorted(f for f in os.listdir(os.path.join(ROOT, "examples")) if f.endswith(".toml") and not f.startswith("_"))
    checked, bad = 0, []
    for ex in examples:
        if only and ex[:-5] not in only:
            continue
        j = job_copy(ROOT, ex, None)
        out = os.path.join(tmp, ex[:-5] + ".3mf")
        cmd = [sys.executable, "-I", os.path.join(ROOT, "scripts", "build_bambu_3mf.py"), j, "--out", out, "--slice-check"]
        if spath:
            cmd += ["--studio-path", spath]
        t0 = time.time()
        r = subprocess.run(cmd, capture_output=True, text=True)
        os.remove(j)
        dt = time.time() - t0
        checked += 1
        md = out[:-4] + ".summary.md"
        mdtext = open(md).read() if os.path.exists(md) else ""
        warn_ok = (not expect_warn) or (choice.warning in r.stdout and choice.warning in mdtext)
        slice_line = next((l for l in r.stdout.splitlines() if l.startswith("slice check")), "no slice check line")
        ok = r.returncode == 0 and os.path.exists(out) and warn_ok and "slice check" in r.stdout
        grams = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith("plate ") and "print time" in l]
        lines.append("%s %-24s %5.1f s  %s%s" % ("PASS" if ok else "FAIL", ex[:-5], dt, slice_line, ("  | " + "; ".join(grams)) if grams else ""))
        if not ok:
            bad.append(ex)
            lines.append("     rc %d, warning shown: %s; stderr: %s" % (r.returncode, warn_ok, r.stderr.strip()[-400:]))
    lines += ["", "%d examples checked, %d failed" % (checked, len(bad))]
    if checked == 0:
        lines.append("NOT CHECKED")
    text = "\n".join(lines) + "\n"
    print(text, end="")
    if report:
        open(report, "w").write(text)
    return 0 if checked and not bad else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
