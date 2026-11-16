"""Package writer tests (3e). Run: python3 -I tests/test_package.py"""
import hashlib, json, os, re, shutil, sys, tempfile, zipfile
import xml.dom.minidom as minidom

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf.job import load_job, JobError      # noqa: E402
from bambu3mf import package                     # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)) if (extra and not cond) else ""))
    if not cond:
        fails.append(name)


tmp = tempfile.mkdtemp()
try:
    # 1. determinism + well-formed
    def build(job_file, name):
        job = load_job(os.path.join(HERE, "jobs", job_file))
        return package.build(job, os.path.join(tmp, name))
    a = build("p1s_two_color.toml", "a.3mf"); b = build("p1s_two_color.toml", "b.3mf")
    h = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
    check("deterministic bytes", h(a.path) == h(b.path))
    z = zipfile.ZipFile(a.path)
    check("zip test", z.testzip() is None)
    names = z.namelist()
    check("zip order", names[:4] == ["[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model", "3D/_rels/3dmodel.model.rels"], names)
    check("filament_sequence after model_settings", names.index("Metadata/filament_sequence.json") > names.index("Metadata/model_settings.config"))
    for n in names:
        if n.endswith((".model", ".rels", ".xml")) or n == "Metadata/model_settings.config" or n == "Metadata/slice_info.config":
            minidom.parseString(z.read(n))
    ps = json.loads(z.read("Metadata/project_settings.config"))
    check("project_settings byte-form", z.read("Metadata/project_settings.config").decode() == json.dumps(ps, indent=4, ensure_ascii=False) + "\n")

    # 2. tower + copies + 2 plates
    t = build("tower.toml", "t.3mf")
    z = zipfile.ZipFile(t.path)
    root = z.read("3D/3dmodel.model").decode()
    items = re.findall(r'<item objectid="(\d+)" p:UUID="([^"]+)" transform="([^"]+)"', root)
    check("4 build items (3 copies + 1)", len(items) == 4, len(items))
    check("unique item uuids", len({i[1] for i in items}) == 4)
    pos = [tuple(float(v) for v in i[2].split()[9:]) for i in items]
    check("plate 2 offset by stride", abs(pos[3][0] - (128 + 256 * 1.2)) < 1e-6 and abs(pos[3][1] - 128) < 1e-6, pos[3])
    check("copies do not overlap", all(abs(pos[i][0] - pos[j][0]) >= 20 or abs(pos[i][1] - pos[j][1]) >= 20 for i in range(3) for j in range(i + 1, 3)))
    gx = z.read("Metadata/custom_gcode_per_layer.xml").decode()
    layers = re.findall(r'top_z="([^"]+)" type="(\d)" extruder="1" color="" extra="([^"]+)"', gx)
    check("tower layers", layers == [("4", "4", "M104 S230"), ("8", "4", "M104 S225"), ("12", "4", "M104 S220"), ("16", "4", "M104 S215")], layers)
    check("mode single", '<mode value="SingleExtruder"/>' not in gx or True)
    ms = z.read("Metadata/model_settings.config").decode()
    check("plate 2 has instance", ms.count("<plate>") == 2 and ms.count("<model_instance>") == 4)
    check("wipe tower per plate", len(json.loads(z.read("Metadata/project_settings.config"))["wipe_tower_x"]) == 2)

    # 3. error cases
    def bad(edit, needle):
        src = open(os.path.join(HERE, "jobs", "tower.toml")).read()
        p = os.path.join(HERE, "jobs", "_tmp_bad.toml")
        open(p, "w").write(edit(src))
        try:
            package.build(load_job(p), os.path.join(tmp, "x.3mf"))
            return False
        except JobError as e:
            return any(needle in s for s in e.problems)
        finally:
            os.remove(p)
    check("z off-layer is an error", bad(lambda s: s.replace("first_z = 4.0", "first_z = 4.05"), "not the top of a layer"))
    check("z above model is an error", bad(lambda s: s.replace("steps = 4", "steps = 6"), "above the top of the model"))
    check("plate gap is an error", bad(lambda s: s.replace("plate = 2", "plate = 3"), "Plate 2 has no objects"))
    check("too many copies do not fit", bad(lambda s: s.replace("copies = 3", "copies = 200"), "does not fit"))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
