"""Manual purge matrix (purge = { "1>2" = 450 }). Run: python3 -I tests/test_purge_matrix.py"""
import os, shutil, sys, tempfile, zipfile, json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf.job import load_job, JobError      # noqa: E402
from bambu3mf import package, validate           # noqa: E402

fails = []
measured = [0]


def check(name, cond, extra=""):
    measured[0] += 1
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)) if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


SLAB1 = os.path.join(ROOT, "examples", "data", "slab1.stl")
SLAB2 = os.path.join(ROOT, "examples", "data", "slab2.stl")
tmp = tempfile.mkdtemp()


def job_text(studio, purge, nfil=2):
    t = 'studio = "%s"\noutput = "o.3mf"\nprinter = "Bambu Lab P1S 0.4 nozzle"\nprocess = "0.20mm Standard @BBL X1C"\n%s\n' % (studio, purge)
    cols = ["#111111", "#FFFFFF", "#CC2020"]
    for i in range(nfil):
        t += '[[filament]]\npreset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"\ncolor = "%s"\n' % cols[i]
    t += '[[object]]\nname = "M"\n'
    for k in range(nfil):
        t += '[[object.part]]\nmesh = "%s"\nfilament = %d\n' % ([SLAB1, SLAB2][k % 2], k + 1)
    return t


def build(text, name="t"):
    p = os.path.join(tmp, name + ".toml")
    open(p, "w").write(text)
    return package.build(load_job(p), os.path.join(tmp, name + ".3mf"))


def matrix(s):
    z = zipfile.ZipFile(s.path)
    cfg = json.loads(z.read("Metadata/project_settings.config").decode())
    return cfg["flush_volumes_matrix"]


def refused(text, needles, name):
    try:
        build(text, "bad")
    except JobError as e:
        msg = " | ".join(e.problems)
        check(name, all(n in msg for n in needles), msg)
        return
    check(name, False, "built without an error")


try:
    # 1. values land in the right cells, other pairs equal the auto build (3 slots, so the cells differ)
    auto = matrix(build(job_text("2.9.2", 'purge = "auto"', 3), "auto"))
    man = matrix(build(job_text("2.9.2", 'purge = { "1>3" = 450, "2>1" = 1200 }', 3), "man"))
    check("manual cell 1>3 is 450 (row 0, column 2)", man[0 * 3 + 2] == "450", man)
    check("manual cell 2>1 is 1200 (row 1, column 0)", man[1 * 3 + 0] == "1200", man)
    same = [i for i in range(9) if i not in (2, 3)]
    check("the other 7 cells equal the auto build", len(same) == 7 and all(man[i] == auto[i] for i in same), (auto, man))
    check("the two set cells differ from auto, so the test can fail", auto[2] != "450" and auto[3] != "1200", auto)
    check("diagonal stays 0", man[0] == man[4] == man[8] == "0", man)
    check("purge = \"auto\" is unchanged (9 cells)", len(auto) == 9)
    dflt = matrix(build(job_text("2.9.2", 'purge = "default"', 3), "dflt"))
    check("purge = \"default\" is unchanged (280 off the diagonal)", dflt[1] == "280" and dflt[0] == "0", dflt)

    # 2. limits per version (900 / 2000, minimum 0)
    for v in ("2.8.2", "2.9.1"):
        refused(job_text(v, 'purge = { "1>2" = 901 }'), ["1>2", "901", "0 to 900", v], "%s: 901 refused" % v)
        check("%s: 900 accepted" % v, matrix(build(job_text(v, 'purge = { "1>2" = 900 }'), "l" + v))[1] == "900")
    check("2.9.2: 2000 accepted", matrix(build(job_text("2.9.2", 'purge = { "1>2" = 2000 }'), "l292"))[1] == "2000")
    refused(job_text("2.9.2", 'purge = { "1>2" = 2001 }'), ["1>2", "2001", "0 to 2000", "2.9.2"], "2.9.2: 2001 refused")
    check("0 accepted", matrix(build(job_text("2.9.2", 'purge = { "1>2" = 0 }'), "l0"))[1] == "0")
    refused(job_text("2.9.2", 'purge = { "1>2" = -1 }'), ["1>2", "-1"], "negative refused")

    # 3. bad entries
    refused(job_text("2.9.2", 'purge = { "1>1" = 100 }'), ["1>1", "itself"], "same slot refused")
    refused(job_text("2.9.2", 'purge = { "1>3" = 100 }'), ["1>3", "slot 3", "2 slots"], "missing slot refused")
    refused(job_text("2.9.2", 'purge = { "0>1" = 100 }'), ["0>1", "slot 0"], "slot 0 refused")
    refused(job_text("2.9.2", 'purge = { "12" = 100 }'), ["'12'", "1>2"], "bad key format refused")
    refused(job_text("2.9.2", 'purge = { "a>b" = 100 }'), ["a>b"], "non-number slots refused")
    refused(job_text("2.9.2", 'purge = { "1>2" = "big" }'), ["1>2", "number"], "non-number value refused")
    refused(job_text("2.9.2", 'purge = "manual"'), ["purge"], "the word \"manual\" alone is refused")

    # 4. the example builds and validates strict
    s = package.build(load_job(os.path.join(ROOT, "examples", "purge_matrix.toml")), os.path.join(tmp, "ex.3mf"))
    m = matrix(s)
    check("example: 1>2 is 1200, 2>1 is 300", m[1] == "1200" and m[2] == "300", m)
    rep = validate.validate(s.path, mode="strict")
    check("example validates strict (0 findings)", not rep.findings, [str(f) for f in rep.findings])
    rep2 = validate.validate(build(job_text("2.9.1", 'purge = { "1>2" = 900 }'), "v291").path, mode="strict")
    check("2.9.1 file with 900 validates strict (0 findings)", not rep2.findings, [str(f) for f in rep2.findings])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n%d checks measured, %d failure(s)" % (measured[0], len(fails)))
if measured[0] == 0:
    print("NOT CHECKED")
    sys.exit(1)
sys.exit(1 if fails else 0)
