#!/usr/bin/env python3
"""D5: the validator reads a mesh entry as a stream. A synthetic 3MF (builder output with its mesh entry replaced by a ~60 MB one) is validated in a
subprocess; its peak RSS is measured with resource.getrusage and must stay far below the unpacked size of the entry. The base process (python +
profiles, a small file) is measured the same way and subtracted. A late DOCTYPE in the same kind of entry is still refused (G6).
Run: python3 -I tests/test_validate_memory.py"""
import os, random, subprocess, sys, tempfile, zipfile, json
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import package  # noqa: E402
from bambu3mf.job import load_job  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)[:300]) if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


tmp = tempfile.mkdtemp()
MESH = os.path.join(H, "..", "examples", "data")
jp = os.path.join(tmp, "m.toml")
open(jp, "w").write('studio = "2.8.2"\noutput = "m.3mf"\nprinter = "Bambu Lab P1S 0.4 nozzle"\nprocess = "0.16mm Optimal @BBL X1C"\n'
                    '[[filament]]\npreset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"\ncolor = "#111111"\n'
                    '[[object]]\nname = "Cube"\n[[object.part]]\nname = "Body"\nmesh = "%s/cube20.stl"\nfilament = 1\n' % MESH)
SMALL = os.path.join(tmp, "m.3mf")
package.build(load_job(jp), SMALL)
MODEL = [n for n in zipfile.ZipFile(SMALL).namelist() if n.startswith("3D/Objects/") and n.endswith(".model")]


def with_big_mesh(dst, n_vertices, doctype_late=False):
    """Copy SMALL; the first object model is replaced by a streamed one: n_vertices vertices (a 20 mm cube's corners, repeated) and 12 triangles."""
    zin = zipfile.ZipFile(SMALL)
    unpacked = 0
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
        for i in zin.infolist():
            if i.filename != MODEL[0]:
                zo.writestr(i.filename, zin.read(i.filename))
                continue
            zi = zipfile.ZipInfo(i.filename, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            old = zin.read(i.filename).decode()
            head = old[:old.index("<vertices>")]
            tail = old[old.index("</vertices>") + len("</vertices>"):]
            with zo.open(zi, "w", force_zip64=True) as w:
                def put(b):
                    nonlocal unpacked
                    unpacked += len(b)
                    w.write(b)
                if doctype_late:
                    head = head.replace("?>", "?>\n<!--" + "x" * 6000 + "-->\n<!DOCTYPE lolz [<!ENTITY lol \"lol\">]>", 1)
                put(head.encode() + b"<vertices>\n")
                rnd = random.Random(7)                              # distinct numbers, so the compression ratio stays under the 100:1 limit (G4)
                for _ in range(n_vertices // 1000):
                    put("".join(' <vertex x="%.5f" y="%.5f" z="%.5f"/>\n' % (rnd.uniform(0, 20), rnd.uniform(0, 20), rnd.uniform(0, 20)) for j in range(1000)).encode())
                put(b"</vertices>" + tail.encode())
    return dst, unpacked


CHILD = r'''
import os, resource, sys
sys.path.insert(0, %r)
from bambu3mf import validate
rep = validate._validate(sys.argv[1], False)
print("RSS_KB", resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // (1024 if sys.platform == "darwin" else 1))   # macOS gives bytes, Linux KiB
print("RULES", sorted({f.rule for f in rep.findings if f.level == "error" or f.rule == "G6"}), [str(f)[:150] for f in rep.findings if f.level == "error"][:3])
''' % os.path.join(H, "..", "scripts")


def run_child(path):
    r = subprocess.run([sys.executable, "-I", "-c", CHILD, path], capture_output=True, text=True)
    rss = int(next(l for l in r.stdout.splitlines() if l.startswith("RSS_KB")).split()[1]) * 1024 if "RSS_KB" in r.stdout else None
    rules = next((l for l in r.stdout.splitlines() if l.startswith("RULES")), "")
    return rss, rules, r.stderr[-300:]


base_rss, _r, err = run_child(SMALL)
check("base run (a small file) measured", base_rss is not None, err)
BIGP, unpacked = with_big_mesh(os.path.join(tmp, "big.3mf"), 1_260_000)
# Peak RSS is noisy on macOS (the allocator; 39 to 52 MB for the same run on navi). Noise only adds memory, so the lowest of three
# runs is the measurement. The limit is not changed.
runs = [run_child(BIGP) for _ in range(3)]
rss, rules, err = min(runs, key=lambda r: r[0] if r[0] is not None else float("inf"))
delta = (rss or 0) - (base_rss or 0)
print("unpacked mesh entry %.1f MB, peak RSS %.1f MB (lowest of %s MB), base %.1f MB, delta %.1f MB" % (
    unpacked / 1e6, (rss or 0) / 1e6, ", ".join("%.1f" % ((r[0] or 0) / 1e6) for r in runs), (base_rss or 0) / 1e6, delta / 1e6))
check("synthetic mesh entry is about 60 MB unpacked", unpacked > 50e6, unpacked)
check("the validator read the whole entry (no G4 / G5 finding)", not any(r in rules for r in ("G4", "G5")), rules)
# what stays is the vertex array the geometry checks use (8 bytes x 3 per vertex, about half the text size); before the fix the whole entry plus
# its element tree were held too (about 5 times the unpacked size)
check("peak RSS of the validation is below the unpacked size (extra < 75 percent of it; was about 500 percent)", rss is not None and delta < unpacked * 0.75, delta)
BADP, _u = with_big_mesh(os.path.join(tmp, "late.3mf"), 5000, doctype_late=True)
rss, rules, err = run_child(BADP)
check("a DOCTYPE after a long comment in a mesh entry is still refused (G6)", "G6" in rules, rules + err)
print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
