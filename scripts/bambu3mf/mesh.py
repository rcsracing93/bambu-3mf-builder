"""Mesh readers (STL binary/ASCII, OBJ, plain 3MF) and mesh checks. Stdlib only.

A Mesh holds vertices as one flat array('d') (x, y, z, ...) and triangles as array('I') (3 indices each).
Vertices are merged by exact coordinates, as Studio does on STL load.
"""
import array
import math
import os
import re
import struct
import sys
import zipfile
import xml.etree.ElementTree as ET


class MeshError(Exception):
    pass


class Mesh:
    def __init__(self, name=""):
        self.name = name
        self.v = array.array("d")
        self.t = array.array("I")
        self._index = {}

    # ---- building -------------------------------------------------------------------------
    def vertex(self, x, y, z):
        key = (x, y, z)
        i = self._index.get(key)
        if i is None:
            i = len(self.v) // 3
            self._index[key] = i
            self.v.extend(key)
        return i

    def triangle(self, a, b, c):
        self.t.extend((a, b, c))

    def finish(self):
        self._index = {}
        return self

    @property
    def n_triangles(self):
        return len(self.t) // 3

    @property
    def n_vertices(self):
        return len(self.v) // 3

    # ---- geometry --------------------------------------------------------------------------
    def bbox(self):
        xs, ys, zs = self.v[0::3], self.v[1::3], self.v[2::3]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def scale(self, f):
        for i in range(len(self.v)):
            self.v[i] *= f

    def volume(self):
        v, t = self.v, self.t
        total = 0.0
        for i in range(0, len(t), 3):
            a, b, c = t[i] * 3, t[i + 1] * 3, t[i + 2] * 3
            ax, ay, az, bx, by, bz, cx, cy, cz = v[a], v[a + 1], v[a + 2], v[b], v[b + 1], v[b + 2], v[c], v[c + 1], v[c + 2]
            total += ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)
        return total / 6.0

    def flip(self):
        t = self.t
        for i in range(0, len(t), 3):
            t[i + 1], t[i + 2] = t[i + 2], t[i + 1]

    def edge_report(self):
        """(open_edges, repeated_directed_edges, 0). Closed manifold with outward orientation
        gives (0, 0, 0): every directed edge appears once and its reverse appears once."""
        seen = {}
        t = self.t
        for i in range(0, len(t), 3):
            a, b, c = t[i], t[i + 1], t[i + 2]
            for e in ((a, b), (b, c), (c, a)):
                seen[e] = seen.get(e, 0) + 1
        open_e = nonman = 0
        for (a, b), n in seen.items():
            if n > 1:
                nonman += 1            # same directed edge twice: shared by 3+ faces, or faces with mixed orientation
            elif (b, a) not in seen:
                open_e += 1            # edge with no partner: hole
        incons = 0
        return open_e, nonman, incons

    def degenerate(self):
        v, t = self.v, self.t
        bad = 0
        for i in range(0, len(t), 3):
            a, b, c = t[i], t[i + 1], t[i + 2]
            if a == b or b == c or a == c:
                bad += 1
        return bad


# ---- readers -----------------------------------------------------------------------------------
def _stl_binary(data, name):
    n = struct.unpack_from("<I", data, 80)[0]
    m = Mesh(name)
    off = 84
    for _ in range(n):
        x = struct.unpack_from("<9f", data, off + 12)
        a = m.vertex(*x[0:3]); b = m.vertex(*x[3:6]); c = m.vertex(*x[6:9])
        m.triangle(a, b, c)
        off += 50
    return m.finish()


_VERT = re.compile(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)")


def _stl_ascii(data, name):
    m = Mesh(name)
    pts = []
    for mt in _VERT.finditer(data):
        pts.append(m.vertex(float(mt.group(1)), float(mt.group(2)), float(mt.group(3))))
        if len(pts) == 3:
            m.triangle(*pts)
            pts = []
    if pts:
        raise MeshError("%s: ASCII STL ends inside a facet" % name)
    return m.finish()


def read_stl(path):
    with open(path, "rb") as f:
        data = f.read()
    name = os.path.basename(path)
    if len(data) >= 84:
        n = struct.unpack_from("<I", data, 80)[0]
        if 84 + 50 * n == len(data):
            return _stl_binary(data, name)
    if data.lstrip()[:5].lower() == b"solid":
        m = _stl_ascii(data, name)
        if m.n_triangles:
            return m
    raise MeshError("%s: not a valid STL file (neither binary nor ASCII)" % name)


def read_obj(path):
    m = Mesh(os.path.basename(path))
    verts = []
    with open(path, "rb") as f:
        for lineno, raw in enumerate(f, 1):
            try:
                if raw.startswith(b"v "):
                    p = raw.split()
                    verts.append((float(p[1]), float(p[2]), float(p[3])))
                    continue
                if not raw.startswith(b"f "):
                    continue
                idx = []
                for tok in raw.split()[1:]:
                    i = int(tok.split(b"/")[0])
                    if i == 0 or abs(i) > len(verts):
                        raise IndexError
                    idx.append(verts[i - 1] if i > 0 else verts[i])
            except (ValueError, IndexError):
                raise MeshError("%s line %d: not a valid OBJ vertex or face (%s)" % (m.name, lineno, raw[:60].decode("utf-8", "replace").strip()))
            ids = [m.vertex(*p) for p in idx]
            for k in range(1, len(ids) - 1):           # fan triangulation of polygons
                m.triangle(ids[0], ids[k], ids[k + 1])
    if not m.n_triangles:
        raise MeshError("%s: no faces found" % m.name)
    return m.finish()


_UNIT_MM = {"micron": 0.001, "millimeter": 1.0, "centimeter": 10.0, "meter": 1000.0, "inch": 25.4, "foot": 304.8}


def read_3mf(path):
    """Plain 3MF (any producer): all mesh objects referenced by build items, transforms applied, unit converted to mm."""
    name = os.path.basename(path)
    z = zipfile.ZipFile(path)
    root_name = None
    rels = z.read("_rels/.rels").decode("utf-8")
    mt = re.search(r'Target="(/?[^"]*3dmodel\.model)"', rels)
    root_name = (mt.group(1).lstrip("/") if mt else "3D/3dmodel.model")
    m = Mesh(name)

    def parse(partname):
        tree = ET.fromstring(z.read(partname))
        ns = tree.tag[1:tree.tag.index("}")] if tree.tag.startswith("{") else ""
        q = lambda t: "{%s}%s" % (ns, t) if ns else t
        scale = _UNIT_MM.get(tree.get("unit", "millimeter"), 1.0)
        objs = {}
        for o in tree.iter(q("object")):
            mesh = o.find(q("mesh"))
            comps = [(c.get("objectid"), c.get("transform"), c.get("{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}path"))
                     for c in o.iter(q("component"))]
            objs[o.get("id")] = (mesh, comps)
        return tree, q, scale, objs

    tree, q, scale, objs = parse(root_name)
    cache = {}

    def mat(s):
        if not s:
            return (1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0)
        return tuple(float(x) for x in s.split())

    def apply(M, p):
        x, y, z_ = p
        return (M[0] * x + M[3] * y + M[6] * z_ + M[9], M[1] * x + M[4] * y + M[7] * z_ + M[10], M[2] * x + M[5] * y + M[8] * z_ + M[11])

    def compose(A, B):                 # A after B
        def col(M, i):
            return (M[i], M[i + 1], M[i + 2])
        out = []
        for c in range(3):
            v = (B[c * 3], B[c * 3 + 1], B[c * 3 + 2])
            out.extend(apply(A[:9] + (0, 0, 0), v))
        t = apply(A, (B[9], B[10], B[11]))
        return tuple(out) + t

    def emit(objid, M, objs_, q_, scale_):
        mesh, comps = objs_[objid]
        if mesh is not None:
            vs = [(float(v.get("x")) * scale_, float(v.get("y")) * scale_, float(v.get("z")) * scale_) for v in mesh.iter(q_("vertex"))]
            ids = [m.vertex(*apply(M, p)) for p in vs]
            for t in mesh.iter(q_("triangle")):
                m.triangle(ids[int(t.get("v1"))], ids[int(t.get("v2"))], ids[int(t.get("v3"))])
        for cid, tr, path in comps:
            Mc = compose(M, mat(tr))
            if path:
                part = path.lstrip("/")
                if part not in cache:
                    cache[part] = parse(part)
                _t, q2, s2, o2 = cache[part]
                emit(cid, Mc, o2, q2, s2)
            else:
                emit(cid, Mc, objs_, q_, scale_)

    items = list(tree.iter(q("item")))
    if not items:
        raise MeshError("%s: no build items" % name)
    for it in items:
        emit(it.get("objectid"), mat(it.get("transform")), objs, q, scale)
    if not m.n_triangles:
        raise MeshError("%s: no triangles found" % name)
    return m.finish()


def read_mesh(path):
    low = path.lower()
    if low.endswith(".stl"):
        return read_stl(path)
    if low.endswith(".obj"):
        return read_obj(path)
    if low.endswith(".3mf"):
        return read_3mf(path)
    raise MeshError("%s: unsupported format" % os.path.basename(path))


# ---- checks ------------------------------------------------------------------------------------
WARN_TRI, STRONG_TRI, STOP_TRI = 2_000_000, 5_000_000, 10_000_000


def check_mesh(mesh, where, units="mm", bed=None, allow_large=False):
    """Returns (errors, warnings). May flip the mesh in place if it is inside-out (reported as a warning).
    bed = (xsize, ysize, zsize) printable volume in mm, or None."""
    errs, warns = [], []
    if units == "inch":
        mesh.scale(25.4)
    for x in mesh.v:
        if not math.isfinite(x):
            errs.append("%s: the mesh has NaN or infinite coordinates" % where)
            return errs, warns
    n = mesh.n_triangles
    if n > STOP_TRI and not allow_large:
        errs.append("%s: %s triangles. Above %s the build stops; reduce the mesh (decimate to 0.01-0.02 mm tolerance) or set allow_large = true in the job" % (
            where, format(n, ","), format(STOP_TRI, ",")))
    elif n > STRONG_TRI:
        warns.append("%s: %s triangles is very large; load and slice will be slow and use a lot of memory. Decimate to 0.01-0.02 mm tolerance." % (where, format(n, ",")))
    elif n > WARN_TRI:
        warns.append("%s: %s triangles is large. A 0.4 mm nozzle cannot print detail finer than 0.01-0.02 mm; decimating is free." % (where, format(n, ",")))
    deg = mesh.degenerate()
    if deg:
        warns.append("%s: %d degenerate triangles (repeated corner); Studio drops them" % (where, deg))
    open_e, nonman, incons = mesh.edge_report()
    if open_e or nonman or incons:
        errs.append("%s: the mesh is not a closed manifold (%d open edges, %d edges shared by 3+ faces, %d with mixed orientation). Repair it in your CAD or mesh tool, or in Studio's repair, and export again." % (
            where, open_e, nonman, incons))
        return errs, warns
    vol = mesh.volume()
    if vol < 0:
        mesh.flip()
        warns.append("%s: the mesh was inside-out (negative volume); the builder turned it outward" % where)
        vol = -vol
    if vol < 1e-9:
        errs.append("%s: the mesh has zero volume" % where)
        return errs, warns
    lo, hi = mesh.bbox()
    size = tuple(hi[i] - lo[i] for i in range(3))
    big = max(size)
    fits_inch = bed is not None and all(s * 25.4 <= b for s, b in zip(size, bed))
    if units == "mm" and big < 10.0 and fits_inch:
        warns.append("%s: the largest side is %.2f mm (%.0f mm if it was drawn in inches, which still fits the bed). If the model is in inches, set units = \"inch\" on this part. The builder never rescales on its own." % (
            where, big, big * 25.4))
    if bed:
        if size[0] > bed[0] or size[1] > bed[1] or size[2] > bed[2]:
            tip = ""
            if units == "mm" and all(s / 25.4 <= b for s, b in zip(size, bed)):
                tip = " It would fit if it were in inches?"
            errs.append("%s: %.1f x %.1f x %.1f mm does not fit the printable volume %.0f x %.0f x %.0f mm.%s" % (
                where, size[0], size[1], size[2], bed[0], bed[1], bed[2], tip))
    return errs, warns
