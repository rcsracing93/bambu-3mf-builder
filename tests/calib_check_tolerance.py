"""Forward check for [calibration] type = "tolerance": reads the meshes from the built 3MF and measures them.
Called from tests/forward_check.py:  checks(raw, f3mf, gcodes) -> [(name, ok, detail)].
Also used by tests/test_calib_tolerance.py (the helpers work on a mesh.Mesh from the job as well as one read from the 3MF).

The clearance is measured from flat faces: (hole side - peg side) / 2, once along x and once along y, for each Hole / Peg pair.
Every check that measures nothing returns ok = False with "NOT CHECKED" in the detail."""
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
from bambu3mf import calibjobs  # noqa: E402
from bambu3mf.mesh import Mesh  # noqa: E402

TOL = 0.01
DEFAULTS = [0.1, 0.15, 0.2, 0.25, 0.3]


def fmt(c):
    return ("%.4f" % c).rstrip("0").rstrip(".")


def shells(mesh):
    """Connected shells of a mesh (by shared vertex): list of (min, max) bounding boxes."""
    parent = list(range(mesh.n_vertices))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    t, v = mesh.t, mesh.v
    for i in range(0, len(t), 3):
        for a, b in ((t[i], t[i + 1]), (t[i + 1], t[i + 2])):
            parent[find(a)] = find(b)
    boxes = {}
    for vid in set(t):
        r = find(vid)
        p = (v[3 * vid], v[3 * vid + 1], v[3 * vid + 2])
        lo, hi = boxes.get(r, (p, p))
        boxes[r] = (tuple(min(a, b) for a, b in zip(lo, p)), tuple(max(a, b) for a, b in zip(hi, p)))
    return list(boxes.values())


def hole_gap(mesh):
    """(gap along x, gap along y) of the square hole of a Hole body (four strips around it), or None."""
    sh = shells(mesh)
    if len(sh) != 4:
        return None
    left, right = min(sh, key=lambda b: b[1][0]), max(sh, key=lambda b: b[0][0])
    front, back = min(sh, key=lambda b: b[1][1]), max(sh, key=lambda b: b[0][1])
    return right[0][0] - left[1][0], back[0][1] - front[1][1]


def peg_size(mesh):
    """(side along x, side along y, height above the flange) of the peg of a Peg body (flange + peg), or None."""
    sh = shells(mesh)
    if len(sh) != 2:
        return None
    area = lambda b: (b[1][0] - b[0][0]) * (b[1][1] - b[0][1])
    peg, flange = min(sh, key=area), max(sh, key=area)
    return peg[1][0] - peg[0][0], peg[1][1] - peg[0][1], peg[1][2] - flange[1][2]


def read_objects(f3mf):
    """{object name: [(part name, subtype, Mesh in object coordinates)]} from a built 3MF."""
    z = zipfile.ZipFile(f3mf)
    cfg = ET.fromstring(z.read("Metadata/model_settings.config"))
    meta = {}
    for o in cfg.iter("object"):
        name = next((m.get("value") for m in o.findall("metadata") if m.get("key") == "name"), None)
        meta[o.get("id")] = (name, {p.get("id"): (next((m.get("value") for m in p.findall("metadata") if m.get("key") == "name"), ""),
                                                   p.get("subtype")) for p in o.findall("part")})
    root = ET.fromstring(z.read("3D/3dmodel.model"))
    ns = root.tag[1:root.tag.index("}")]
    q = lambda t: "{%s}%s" % (ns, t)
    pk = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}path"
    cache = {}

    def part_mesh(path, oid):
        if path not in cache:
            tr = ET.fromstring(z.read(path.lstrip("/")))
            cache[path] = {o.get("id"): o.find(q("mesh")) for o in tr.iter(q("object"))}
        return cache[path][oid]
    out = {}
    for o in root.iter(q("object")):
        comps = list(o.iter(q("component")))
        if o.get("id") not in meta or not comps:
            continue
        name, parts = meta[o.get("id")]
        lst = []
        for c in comps:
            tr = [float(x) for x in c.get("transform").split()]
            m = Mesh(parts.get(c.get("objectid"), ("?", "?"))[0])
            mesh = part_mesh(c.get(pk), c.get("objectid"))
            ids = [m.vertex(float(v.get("x")) + tr[9], float(v.get("y")) + tr[10], float(v.get("z")) + tr[11]) for v in mesh.iter(q("vertex"))]
            for t in mesh.iter(q("triangle")):
                m.triangle(ids[int(t.get("v1"))], ids[int(t.get("v2"))], ids[int(t.get("v3"))])
            lst.append((m.name, parts.get(c.get("objectid"), ("?", "?"))[1], m.finish()))
        out[name] = lst
    return out


def label_boxes(text):
    n = 0
    for ch in text:
        n += len(calibjobs._char_boxes(ch, 0.0, 0.0))
    return n


def checks(raw, f3mf, gcodes):
    cal = raw.get("calibration", {})
    want = [round(float(c), 4) for c in cal.get("clearances", DEFAULTS)]
    size = float(cal.get("size", 10.0))
    depth = float(cal.get("depth", 6.0))
    out = []
    objs = read_objects(f3mf)
    names = ["%s %s" % (k, fmt(c)) for c in want for k in ("Hole", "Peg")]
    missing = [n for n in names if n not in objs]
    out.append(("tolerance objects", not missing and len(objs) == len(names),
                "%d objects measured, want %d (%s)%s" % (len(objs), len(names), ", ".join(names), "; missing " + ", ".join(missing) if missing else "")))
    n_closed = n_parts = 0
    bad = []
    for name, parts in objs.items():
        for pname, sub, m in parts:
            n_parts += 1
            if m.edge_report() == (0, 0, 0) and m.volume() > 0:
                n_closed += 1
            else:
                bad.append("%s/%s %s" % (name, pname, m.edge_report()))
    out.append(("watertight", n_parts > 0 and not bad, "%d parts measured, %d closed manifold%s" % (n_parts, n_closed, "; open: " + "; ".join(bad[:4]) if bad else "")
                + ("" if n_parts else " NOT CHECKED")))
    probs, n_meas = [], 0
    for c in want:
        h, p = objs.get("Hole %s" % fmt(c)), objs.get("Peg %s" % fmt(c))
        if not h or not p:
            probs.append("%s: pair missing" % fmt(c))
            continue
        hg = hole_gap(next(m for _n, s, m in h if s == "normal_part"))
        ps = peg_size(next(m for _n, s, m in p if s == "normal_part"))
        if hg is None or ps is None:
            probs.append("%s: cannot read the hole or the peg" % fmt(c))
            continue
        for axis, a, b in (("x", hg[0], ps[0]), ("y", hg[1], ps[1])):
            n_meas += 1
            got = (a - b) / 2.0
            if abs(got - c) > TOL:
                probs.append("%s %s: measured %.4f" % (fmt(c), axis, got))
        n_meas += 1
        if abs(ps[0] - size) > TOL or abs(ps[1] - size) > TOL or abs(ps[2] - depth) > TOL:
            probs.append("%s: peg %.3f x %.3f x %.3f, want %g x %g x %g" % (fmt(c), ps[0], ps[1], ps[2], size, size, depth))
    out.append(("clearance = label", n_meas > 0 and not probs, "%d measurements (x and y per pair, peg size per pair), within %.2f mm of the label%s%s" % (
        n_meas, TOL, "; " + "; ".join(probs[:5]) if probs else "", "" if n_meas else " NOT CHECKED")))
    lprobs, n_lab = [], 0
    for name, parts in objs.items():
        text = name.split(" ", 1)[1] if " " in name else ""
        labs = [(pn, m) for pn, s, m in parts if s == "negative_part"]
        n_lab += 1
        if len(labs) != 1 or labs[0][0] != "Label " + text:
            lprobs.append("%s: labels %s" % (name, [pn for pn, _m in labs]))
            continue
        m = labs[0][1]
        lo, hi = m.bbox()
        w = calibjobs.label_size(text)[0]
        if len(shells(m)) != label_boxes(text) or abs((hi[0] - lo[0]) - w) > TOL or abs((hi[2] - lo[2]) - (calibjobs.LABEL_DEPTH + 0.3)) > TOL:
            lprobs.append("%s: %d strokes (want %d), width %.2f (want %.2f)" % (name, len(shells(m)), label_boxes(text), hi[0] - lo[0], w))
    out.append(("labels", n_lab > 0 and not lprobs, "%d objects each with one engraved label (strokes, width, 0.5 mm depth)%s%s" % (
        n_lab, "; " + "; ".join(lprobs[:4]) if lprobs else "", "" if n_lab else " NOT CHECKED")))
    out.append(("sliced", len(gcodes) > 0, "%d plate G-code file(s)%s" % (len(gcodes), "" if gcodes else " NOT CHECKED")))
    return out


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
    from bambu3mf.job import tomllib                  # tomllib, or the vendored tomli on Python 3.8-3.10
    with open(sys.argv[1], "rb") as fh:
        r = tomllib.load(fh)
    res = checks(r, sys.argv[2], sys.argv[3:])
    for n, ok, d in res:
        print("%s %s - %s" % ("ok  " if ok else "FAIL", n, d))
    sys.exit(0 if all(ok for _n, ok, _d in res) else 1)
