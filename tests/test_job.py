import os, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
from bambu3mf.job import load_job, JobError

J = os.path.join(HERE, "jobs")


class JobTests(unittest.TestCase):
    def test_good(self):
        j = load_job(os.path.join(J, "p1s_two_color.toml"))
        self.assertEqual(j.studio, "2.8.2.61")
        self.assertEqual(j.settings["sparse_infill_density"], "15%")
        self.assertEqual(j.filaments[1]["settings"]["nozzle_temperature"], "225")

    def test_bad_lists_all(self):
        with self.assertRaises(JobError) as cm:
            load_job(os.path.join(J, "bad.toml"))
        text = str(cm.exception)
        for needle in ("nozle", "Texture PEI", "wall_loops", "gyroid", "filament setting", "outside the allowed range",
                       "#RRGGBB", "slot 3", "file not found"):
            self.assertIn(needle, text)

    def test_process_for_other_printer(self):
        import tempfile
        src = open(os.path.join(J, "p1s_two_color.toml")).read().replace("0.20mm Standard @BBL X1C", "0.20mm Standard @BBL A1")
        p = os.path.join(J, "_tmp_a1.toml")
        open(p, "w").write(src)
        try:
            with self.assertRaises(JobError) as cm:
                load_job(p)
            self.assertIn("is not made for", str(cm.exception))
        finally:
            os.remove(p)

    # ---- friendly names (aliases)
    BASE = open(os.path.join(HERE, "data", "mutation_base.toml")).read().replace('"../../examples/data/', '"%s/' % os.path.join(HERE, "..", "examples", "data"))

    def _job(self, extra_top="", settings=None, fil_settings=None):
        import tempfile
        t = self.BASE
        if settings is not None:
            t = t.replace('[settings]\nsparse_infill_density = "35%"\nwall_loops = 4\n', "[settings]\n" + settings)
        if extra_top:
            t = t.replace('process = "0.20mm Standard @BBL X1C"\n', 'process = "0.20mm Standard @BBL X1C"\n' + extra_top)
        if fil_settings:
            t = t.replace('color = "#111111"\n', 'color = "#111111"\n[filament.settings]\n' + fil_settings, 1)
        d = tempfile.mkdtemp()
        p = os.path.join(d, "a.toml")
        open(p, "w").write(t)
        return p

    def test_alias_process(self):
        j = load_job(self._job(settings='walls = 5\ninfill = "20%"\ntop_layers = 6\nsupports = true\n'))
        self.assertEqual((j.settings["wall_loops"], j.settings["sparse_infill_density"], j.settings["top_shell_layers"], j.settings["enable_support"]),
                         ("5", "20%", "6", "1"))

    def test_alias_equals_real_key(self):
        a = load_job(self._job(settings='walls = 5\n')).settings
        b = load_job(self._job(settings='wall_loops = 5\n')).settings
        self.assertEqual(a, b)

    def test_alias_and_real_key_together(self):
        with self.assertRaises(JobError) as cm:
            load_job(self._job(settings='walls = 5\nwall_loops = 4\n'))
        self.assertIn("another spelling of wall_loops", str(cm.exception))

    def test_alias_filament(self):
        j = load_job(self._job(extra_top='plate_type = "Textured PEI Plate"\n', fil_settings='nozzle_temp = 222\nbed_temp = 66\n'))
        s = j.filaments[0]["settings"]
        self.assertEqual(s["nozzle_temperature"], "222")
        self.assertEqual(s["textured_plate_temp"], "66")

    def test_bed_temp_needs_plate_type(self):
        with self.assertRaises(JobError) as cm:
            load_job(self._job(fil_settings='bed_temp = 66\n'))
        self.assertIn("needs plate_type", str(cm.exception))

    def test_default_infill_is_rectilinear(self):
        self.assertEqual(load_job(self._job(settings="")).settings["sparse_infill_pattern"], "zig-zag")

    def test_rectilinear_by_its_studio_name(self):
        self.assertEqual(load_job(self._job(settings='infill_pattern = "rectilinear"\n')).settings["sparse_infill_pattern"], "zig-zag")

    def test_job_pattern_wins_over_default(self):
        self.assertEqual(load_job(self._job(settings='sparse_infill_pattern = "gyroid"\n')).settings["sparse_infill_pattern"], "gyroid")
        self.assertEqual(load_job(self._job(settings='infill_pattern = "grid"\n')).settings["sparse_infill_pattern"], "grid")

    def test_unknown_name_still_suggests(self):
        with self.assertRaises(JobError) as cm:
            load_job(self._job(settings='wals = 5\n'))
        self.assertIn("Closest", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
