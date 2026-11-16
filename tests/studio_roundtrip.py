#!/usr/bin/env python3
"""Round trip through a real Studio: build a file, let Studio load and save it three times (--export-3mf), and check
that nothing that matters drifts.

Usage: python3 tests/studio_roundtrip.py --studio-dir DIR [--jobs examples/colors_4.toml ...] [--work DIR]

What Studio 02.08.02.61 does (bbs_3mf.cpp, verified here and in the source of both tags):
  * Loading centers every part mesh on its own bbox (center_geometry_after_creation) and keeps the offset in the part's
    volume transform. Saving writes mesh = part-local, component transform = that offset.
  * Saving writes the part "matrix" as volume matrix * source.transform. On load source.transform is set from the file's
    matrix, so a file whose matrix is already the offset V is saved with 2V, then 3V ... (nothing reads it back for
    geometry; no GUI code uses source.transform). This is Studio's own drift and no file the builder writes can avoid it
    for a part whose offset is not zero.
So the test asserts:
  G1  world-space geometry (vertex + component transform + item transform) is identical in the builder file and after
      1, 2 and 3 Studio saves (tolerance 1e-4 mm)
  G2  component transforms of the builder file equal what Studio writes on its first save (builder writes Studio's own form)
  G3  the matrix of the builder file equals the part offset V, and generation k holds (k+1) x V (documented Studio drift)
  G4  source_offset_x/y/z, source_file and part names do not change
"""
import argparse, glob, os, re, subprocess, shutil, sys, tempfile, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, HERE)
from bambu3mf import package, job as jobmod  # noqa: E402
import studio_slice as S  # noqa: E402

TOL = 1e-4
NUM = r"[-+0-9.eE]+"


def parts_of(path):
    """Return {(object_wrapper_id, part_index): dict(world_bbox, comp, matrix, src)} from a 3MF."""
    z = zipfile.ZipFile(path)
    root = z.read("3D/3dmodel.model").decode()
    ms = z.read("Metadata/model_settings.config").decode()
    items = {}
    for m in re.finditer(r'<item objectid="(\d+)"[^>]*transform="([^"]*)"', root):
        items.setdefault(int(m.group(1)), []).append([float(x) for x in m.group(2).split()])
    res = {}
    meshes = {}
    for m in re.finditer(r'<component p:path="/3D/Objects/(object_\d+\.model)" objectid="(\d+)"[^>]*transform="([^"]*)"', root):
        f, oid, tr = m.group(1), int(m.group(2)), [float(x) for x in m.group(3).split()]
        if f not in meshes:
            txt = z.read("3D/Objects/" + f).decode()
            meshes[f] = {int(o.group(1)): [tuple(map(float, v)) for v in re.findall(
                r'<vertex x="(%s)" y="(%s)" z="(%s)"' % (NUM, NUM, NUM), o.group(2))]
                for o in re.finditer(r'<object id="(\d+)"[^>]*>(.*?)</object>', txt, re.S)}
        res[oid] = {"comp": tr[9:12], "verts": meshes[f][oid], "file": f}
    # wrapper -> parts, and the parts' model_settings entries
    wrappers = {}
    for m in re.finditer(r'<object id="(\d+)"[^>]*type="model">\s*<components>(.*?)</components>', root, re.S):
        wrappers[int(m.group(1))] = [int(x) for x in re.findall(r'objectid="(\d+)"', m.group(2))]
    out = {}
    for m in re.finditer(r'<object id="(\d+)">(.*?)</object>', ms, re.S):
        wid = int(m.group(1))
        for pm in re.finditer(r'<part id="(\d+)"[^>]*>(.*?)</part>', m.group(2), re.S):
            pid, body = int(pm.group(1)), pm.group(2)
            g = lambda k: (re.search(r'key="%s" value="([^"]*)"' % k, body) or [None, None])[1]
            mat = [float(x) for x in g("matrix").split()]
            p = res[pid]
            it = items[wid][0]
            world = [tuple(it[9 + i] + p["comp"][i] + v[i] for i in range(3)) for v in p["verts"]]
            lo = [min(w[i] for w in world) for i in range(3)]
            hi = [max(w[i] for w in world) for i in range(3)]
            out[(wid, pid)] = {"bbox": lo + hi, "comp": p["comp"], "matrix_t": [mat[3], mat[7], mat[11]],
                               "src": [g("source_offset_x"), g("source_offset_y"), g("source_offset_z")],
                               "src_file": g("source_file"), "name": g("name")}
    return out


def close(a, b):
    return all(abs(float(x) - float(y)) <= TOL for x, y in zip(a, b))


def run(jobfile, sdir, work):
    name = os.path.splitext(os.path.basename(jobfile))[0]
    gens = [os.path.join(work, "%s.g%d.3mf" % (name, i)) for i in range(4)]
    package.build(jobmod.load_job(jobfile), gens[0])
    for i in (1, 2, 3):
        rc, log, _ = S.run_studio(sdir, ["--export-3mf", gens[i], gens[i - 1]], work)
        if rc != 0 or not os.path.exists(gens[i]):
            return ["%s: Studio export %d failed rc=%s" % (name, i, rc)]
    P = [parts_of(g) for g in gens]
    fails = []
    keys = sorted(P[0])
    for g in range(1, 4):
        if sorted(P[g]) != keys:
            fails.append("%s: part ids changed in generation %d" % (name, g))
            return fails
    for k in keys:
        b = P[0][k]
        for g in range(1, 4):
            q = P[g][k]
            if not close(b["bbox"], q["bbox"]):
                fails.append("%s part %s: G1 world bbox moved in generation %d: %s -> %s" % (name, k, g, b["bbox"], q["bbox"]))
            if not close(b["comp"], q["comp"]):
                fails.append("%s part %s: G2 component transform differs in generation %d: %s vs %s" % (name, k, g, b["comp"], q["comp"]))
            if not close(q["matrix_t"], [(g + 1) * x for x in b["comp"]]):
                fails.append("%s part %s: G3 matrix in generation %d is %s, expected %d x offset %s" % (name, k, g, q["matrix_t"], g + 1, b["comp"]))
            if (b["src"], b["src_file"], b["name"]) != (q["src"], q["src_file"], q["name"]) and not all(close([x], [y]) for x, y in zip(b["src"], q["src"])):
                fails.append("%s part %s: G4 source data changed in generation %d" % (name, k, g))
        if not close(b["matrix_t"], b["comp"]):
            fails.append("%s part %s: G3 builder matrix %s != component offset %s" % (name, k, b["matrix_t"], b["comp"]))
    print("  %-22s %d parts, 3 saves: %s" % (name, len(keys), "FAIL" if fails else "PASS"))
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--studio-dir", required=True)
    ap.add_argument("--jobs", nargs="*")
    ap.add_argument("--build-version", help="build the jobs for this Studio version instead of their own")
    ap.add_argument("--work")
    a = ap.parse_args()
    jobs = a.jobs or [os.path.join(ROOT, "examples", n + ".toml") for n in ("colors_4", "colors_8", "modifier_negative", "per_object_settings", "single_p1s")]
    work = a.work or tempfile.mkdtemp(prefix="studio_rt_")
    os.makedirs(work, exist_ok=True)
    fails = []
    for j in jobs:
        fails += run(S.retarget(j, a.build_version, work) if a.build_version else j, a.studio_dir, work)
    for f in fails:
        print("FAIL:", f)
    print("OK" if not fails else "%d failures" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
