import os, subprocess, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__))


class DocTests(unittest.TestCase):
    def test_examples_doc_is_current(self):
        r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "make_examples_doc.py"), "--check"])
        self.assertEqual(r.returncode, 0, "docs/examples.md is out of date: run python3 tests/make_examples_doc.py")

    def test_every_example_is_described(self):
        ex = sorted(f[:-5] for f in os.listdir(os.path.join(HERE, "..", "examples")) if f.endswith(".toml") and not f.startswith("_"))
        sys.path.insert(0, HERE)
        import make_examples_doc as m
        self.assertEqual(sorted(m.ORDER), ex)


if __name__ == "__main__":
    unittest.main()
