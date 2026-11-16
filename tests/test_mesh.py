import os, struct, sys, tempfile, unittest, zipfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
from bambu3mf.mesh import read_mesh, check_mesh, MeshError

CUBE_V = [(0, 0, 0), (20, 0, 0), (20, 20, 0), (0, 20, 0), (0, 0, 20), (20, 0, 20), (20, 20, 20), (0, 20, 20)]
CUBE_F = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]


def write_bin(path, faces, scale=1.0):
    with open(path, "wb") as f:
        f.write(b"\0" * 80 + struct.pack("<I", len(faces)))
        for t in faces:
            f.write(struct.pack("<3f", 0, 0, 0))
            for i in t:
                f.write(struct.pack("<3f", *[c * scale for c in CUBE_V[i]]))
            f.write(b"\0\0")


class MeshTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def p(self, n):
        return os.path.join(self.d, n)

    def test_ascii_stl(self):
        m = read_mesh(os.path.join(HERE, "data", "cube20.stl"))
        self.assertEqual((m.n_vertices, m.n_triangles), (8, 12))
        self.assertAlmostEqual(m.volume(), 8000.0)
        self.assertEqual(check_mesh(m, "cube", bed=(256, 256, 256)), ([], []))

    def test_binary_stl_same(self):
        write_bin(self.p("c.stl"), CUBE_F)
        m = read_mesh(self.p("c.stl"))
        self.assertAlmostEqual(m.volume(), 8000.0, places=3)

    def test_open_mesh(self):
        write_bin(self.p("o.stl"), CUBE_F[:-1])
        errs, _ = check_mesh(read_mesh(self.p("o.stl")), "o")
        self.assertTrue(errs and "not a closed manifold" in errs[0])

    def test_inside_out_is_flipped(self):
        write_bin(self.p("i.stl"), [(a, c, b) for a, b, c in CUBE_F])
        m = read_mesh(self.p("i.stl"))
        errs, warns = check_mesh(m, "i")
        self.assertFalse(errs)
        self.assertTrue(any("inside-out" in w for w in warns))
        self.assertGreater(m.volume(), 0)

    def test_inch_hint_and_fit(self):
        write_bin(self.p("s.stl"), CUBE_F, scale=0.1)     # 2 mm cube
        _, warns = check_mesh(read_mesh(self.p("s.stl")), "s", bed=(256, 256, 256))
        self.assertTrue(any("inches" in w for w in warns))
        _, warns = check_mesh(read_mesh(self.p("s.stl")), "s")        # modifiers and holes (no bed given) may be small
        self.assertFalse(any("inches" in w for w in warns))
        write_bin(self.p("m.stl"), CUBE_F, scale=0.4)                 # 8 mm cube: under 10 mm, x25.4 = 203 mm fits
        _, warns = check_mesh(read_mesh(self.p("m.stl")), "m", bed=(256, 256, 256))
        self.assertTrue(any("inches" in w for w in warns))
        write_bin(self.p("l.stl"), CUBE_F, scale=0.6)                 # 12 mm cube: no hint
        _, warns = check_mesh(read_mesh(self.p("l.stl")), "l", bed=(256, 256, 256))
        self.assertFalse(any("inches" in w for w in warns))
        write_bin(self.p("t.stl"), CUBE_F, scale=0.5)                 # 10 mm: not under 10
        _, warns = check_mesh(read_mesh(self.p("t.stl")), "t", bed=(256, 256, 256))
        self.assertFalse(any("inches" in w for w in warns))
        write_bin(self.p("b.stl"), CUBE_F, scale=20.0)    # 400 mm cube
        errs, _ = check_mesh(read_mesh(self.p("b.stl")), "b", bed=(256, 256, 256))
        self.assertTrue(errs and "does not fit" in errs[0])
        m = read_mesh(self.p("c2.stl")) if False else None

    def test_units_inch(self):
        write_bin(self.p("n.stl"), CUBE_F, scale=0.1)     # 2 "inch" cube = 50.8 mm
        m = read_mesh(self.p("n.stl"))
        errs, warns = check_mesh(m, "n", units="inch", bed=(256, 256, 256))
        self.assertEqual((errs, warns), ([], []))
        self.assertAlmostEqual(m.bbox()[1][0], 50.8, places=3)

    def test_obj_quads(self):
        quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
        with open(self.p("c.obj"), "w") as f:
            for v in CUBE_V:
                f.write("v %g %g %g\n" % v)
            for q in quads:
                f.write("f " + " ".join("%d/1/1" % (i + 1) for i in q) + "\n")
        m = read_mesh(self.p("c.obj"))
        self.assertEqual(m.n_triangles, 12)
        self.assertAlmostEqual(m.volume(), 8000.0)

    def test_3mf_plain_with_transform_and_inch(self):
        verts = "".join('<vertex x="%g" y="%g" z="%g"/>' % v for v in [(a / 20.0 * 1, b / 20.0, c / 20.0) for a, b, c in CUBE_V])
        tris = "".join('<triangle v1="%d" v2="%d" v3="%d"/>' % t for t in CUBE_F)
        xml = ('<?xml version="1.0"?><model unit="inch" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
               '<resources><object id="1" type="model"><mesh><vertices>%s</vertices><triangles>%s</triangles></mesh></object></resources>'
               '<build><item objectid="1" transform="1 0 0 0 1 0 0 0 1 10 0 0"/></build></model>') % (verts, tris)
        with zipfile.ZipFile(self.p("p.3mf"), "w") as z:
            z.writestr("_rels/.rels", '<Relationships><Relationship Target="/3D/3dmodel.model" Type="x" Id="r"/></Relationships>')
            z.writestr("3D/3dmodel.model", xml)
        m = read_mesh(self.p("p.3mf"))
        lo, hi = m.bbox()
        self.assertAlmostEqual(hi[0] - lo[0], 25.4, places=3)     # 1 inch
        self.assertAlmostEqual(lo[0], 10.0, places=3)


if __name__ == "__main__":
    unittest.main()
