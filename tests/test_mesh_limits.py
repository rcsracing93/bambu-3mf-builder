#!/usr/bin/env python3
"""Large-mesh limits: warn above 2M triangles, strong warning above 5M, stop above 10M unless allow_large = true; the same limits on
the sum over the whole project. A real mesh stands in; only its triangle count is raised. Run: python3 -I tests/test_mesh_limits.py"""
import os, sys, tempfile
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import mesh as meshmod, package  # noqa: E402
from bambu3mf.job import load_job  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)) if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


CUBE = os.path.join(H, "data", "cube20.stl")


REAL_READ = meshmod.read_mesh


def big(n):
    class Big(meshmod.Mesh):
        @property
        def n_triangles(self):
            return n
    m = REAL_READ(CUBE)
    b = Big()
    b.v, b.t = m.v, m.t
    return b


def run(n, **kw):
    return meshmod.check_mesh(big(n), "part", **kw)


e, w = run(2_000_000)
check("2,000,000 triangles: no message", not e and not any("triangles" in x for x in w), (e, w))
e, w = run(2_000_001)
check("2,000,001: a warning, no error", not e and any("is large" in x for x in w), (e, w))
e, w = run(5_000_000)
check("5,000,000: still the plain warning", not e and any("is large" in x for x in w) and not any("very large" in x for x in w), (e, w))
e, w = run(5_000_001)
check("5,000,001: the strong warning", not e and any("very large" in x for x in w), (e, w))
e, w = run(10_000_000)
check("10,000,000: strong warning, no stop", not e and any("very large" in x for x in w), (e, w))
e, w = run(10_000_001)
check("10,000,001: the build stops and names allow_large", len(e) == 1 and "allow_large = true" in e[0] and "10,000,001" in e[0], e)
e, w = run(10_000_001, allow_large=True)
check("10,000,001 with allow_large: no stop, strong warning", not e and any("very large" in x for x in w), (e, w))

# project total: two parts of 6M each are each below the stop limit, but 12M together are not
tmp = tempfile.mkdtemp()
job_text = '''studio = "2.8.2"
output = "%s/o.3mf"
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"
%s
[[filament]]
preset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"
color = "#111111"
[[object]]
name = "A"
[[object.part]]
name = "P1"
mesh = "%s"
filament = 1
[[object.part]]
name = "P2"
mesh = "%s"
filament = 1
'''


def build(per_part, allow):
    p = os.path.join(tmp, "j.toml")
    open(p, "w").write(job_text % (tmp, "allow_large = true" if allow else "", CUBE, CUBE))
    saved = meshmod.read_mesh
    meshmod.read_mesh = lambda path: big(per_part)
    try:
        job = load_job(p)
        return package.build(job, os.path.join(tmp, "o.3mf")), None
    except Exception as ex:                       # JobError or a build problem: the text is what the user sees
        return None, str(getattr(ex, "problems", ex))
    finally:
        meshmod.read_mesh = saved


s, err = build(6_000_000, False)
check("project total above 10M stops, naming the total and allow_large", s is None and "12,000,000 triangles in all parts" in (err or "") and "allow_large" in err, err)
s, err = build(6_000_000, True)
check("project total above 10M with allow_large builds", s is not None, err)
s, err = build(3_000_000, False)
check("project total 6M builds with a warning only", s is not None, err)
print("\n%d failed" % len(fails))
sys.exit(1 if fails else 0)
