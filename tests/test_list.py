import os, subprocess, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
B = os.path.join(HERE, "..", "scripts", "build_bambu_3mf.py")


def run(*a):
    return subprocess.run([sys.executable, "-I", B] + list(a), capture_output=True, text=True)


class ListTests(unittest.TestCase):
    def test_printers(self):
        r = run("--list", "printer", "--match", "P1S")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Bambu Lab P1S 0.4 nozzle", r.stdout.splitlines())

    def test_filament_for_printer(self):
        r = run("--list", "filament", "--printer", "Bambu Lab P1S 0.4 nozzle", "--match", "PLA Basic")
        self.assertIn("Bambu PLA Basic @BBL P1S 0.4 nozzle", r.stdout.splitlines())

    def test_other_version(self):
        r = run("--list", "process", "--studio", "2.8.2", "--printer", "Bambu Lab P1S 0.4 nozzle")
        self.assertIn("0.20mm Standard @BBL X1C", r.stdout.splitlines())

    def test_bad_kind_and_version(self):
        self.assertEqual(run("--list", "bogus").returncode, 2)
        self.assertEqual(run("--list", "printer", "--studio", "1.0").returncode, 2)


if __name__ == "__main__":
    unittest.main()
