#!/usr/bin/env python3
"""Navi check (needs a real Bambu Studio; not run by run_all). For examples/colors_4.toml and examples/multi_plate.toml:
build with --slice-check, slice the same 3MF again in a new empty folder, read that result.json, and compare every time,
grams and metres value in the console and in the summary file with it (same rounding: 2 decimals; time as 1h 5m 10s).
Metres are not in result.json, so they are compared with the G-code header "total filament length [mm]" of the second slice.
Usage: python3 -I tests/grams_check.py [--studio-path PATH] [--report FILE]"""
import glob, json, os, re, subprocess, sys, tempfile
H = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(H)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from bambu3mf import studio_detect

CLI = os.path.join(ROOT, "scripts", "build_bambu_3mf.py")
EXAMPLES = ["colors_4.toml", "multi_plate.toml"]


def fmt_time(sec):
    s = int(round(sec)); d, s = divmod(s, 86400); h, s = divmod(s, 3600); m, s = divmod(s, 60)
    return " ".join(x for x in [("%dd" % d) if d else "", ("%dh" % h) if (d or h) else "", ("%dm" % m) if (d or h or m) else "", "%ds" % s] if x)


def main(argv):
    studio = report = None
    a = list(argv)
    while a:
        k = a.pop(0)
        if k == "--studio-path" and a: studio = a.pop(0)
        elif k == "--report" and a: report = a.pop(0)
        else:
            print(__doc__); return 2
    exe, how = studio_detect.find_cli(studio)
    out = []
    def log(s):
        print(s); out.append(s)
    if not exe:
        log("NOT CHECKED: %s" % how)
        if report: open(report, "w").write("\n".join(out) + "\n")
        return 2
    measured = 0
    fails = []
    def check(name, cond, extra=""):
        nonlocal measured
        measured += 1
        log(("PASS " if cond else "FAIL ") + name + ((" - " + str(extra)[:300]) if (extra and not cond) else ""))
        if not cond: fails.append(name)
    for ex in EXAMPLES:
        with tempfile.TemporaryDirectory() as tmp:
            f3 = os.path.join(tmp, "job.3mf")
            r = subprocess.run([sys.executable, "-I", CLI, os.path.join(ROOT, "examples", ex), "--out", f3, "--slice-check",
                                "--studio-path", exe], capture_output=True, text=True, cwd=tmp)
            console = r.stdout
            md = open(os.path.join(tmp, "job.summary.md"), encoding="utf-8").read() if os.path.isfile(os.path.join(tmp, "job.summary.md")) else ""
            check("%s: build and slice check exit 0" % ex, r.returncode == 0, r.stdout[-300:] + r.stderr[-300:])
            second = os.path.join(tmp, "second"); os.makedirs(second)
            subprocess.run([exe, "--slice", "0", "--outputdir", second, f3], capture_output=True, cwd=tmp)
            try:
                res = json.load(open(os.path.join(second, "result.json")))
            except (OSError, ValueError):
                check("%s: second slice gave result.json" % ex, False); continue
            plates = res.get("sliced_plates") or []
            check("%s: second slice has plates" % ex, len(plates) > 0)
            tot_s = tot_g = tot_m = 0.0
            for p in plates:
                pid = p["id"]
                u = studio_detect.read_gcode_usage(os.path.join(second, "plate_%d.gcode" % pid))
                t = fmt_time(p["total_predication"])
                gs = [f["total_used_g"] for f in sorted(p["filaments"], key=lambda f: f["id"])]
                check("%s plate %d: console time" % (ex, pid), ("plate %d: print time %s," % (pid, t)) in console, t)
                check("%s plate %d: summary time" % (ex, pid), ("plate %d: print time %s," % (pid, t)) in md, t)
                check("%s plate %d: console plate grams" % (ex, pid), ("plate %d: print time %s, filament %.2f g" % (pid, t, sum(gs))) in console)
                check("%s plate %d: summary plate grams" % (ex, pid), ("plate %d: print time %s, filament %.2f g" % (pid, t, sum(gs))) in md)
                tot_s += p["total_predication"]; tot_g += sum(gs)
                for f in sorted(p["filaments"], key=lambda f: f["id"]):
                    k = f["id"]
                    mm = u["mm"][k - 1] if k <= len(u["mm"]) else None
                    m_txt = ("%.2f" % (mm / 1000.0)) if mm is not None else None
                    tot_m += (mm or 0) / 1000.0
                    gtxt = "%.2f" % f["total_used_g"]
                    check("%s plate %d slot %d: console grams %s and metres %s" % (ex, pid, k, gtxt, m_txt),
                          m_txt is not None and re.search(r"filament %d[^:\n]*: %s g, %s m" % (k, re.escape(gtxt), re.escape(m_txt)), console) is not None)
                    check("%s plate %d slot %d: summary row" % (ex, pid, k),
                          m_txt is not None and re.search(r"\| %d \| %s \| %d \| [^|]* \| %s \| %s \|" % (pid, re.escape(t), k, re.escape(gtxt), re.escape(m_txt)), md) is not None)
            if len(plates) > 1:
                tl = "total: print time %s, filament %.2f g, %.2f m" % (fmt_time(tot_s), tot_g, tot_m)
                check("%s: console total (%s)" % (ex, tl), tl in console)
                check("%s: summary total row" % ex, ("| total | %s | | | %.2f | %.2f |" % (fmt_time(tot_s), tot_g, tot_m)) in md)
    log("measured %d checks, %d failed" % (measured, len(fails)))
    if measured == 0: log("NOT CHECKED")
    log("RESULT: " + ("PASS" if measured and not fails else ("NOT CHECKED" if not measured else "FAIL")))
    if report:
        with open(report, "w", encoding="utf-8") as fh: fh.write("\n".join(out) + "\n")
    return 0 if measured and not fails else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
