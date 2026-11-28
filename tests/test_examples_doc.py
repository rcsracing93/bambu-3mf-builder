import os, subprocess, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__))


class DocTests(unittest.TestCase):
    def test_examples_doc_is_current(self):
        r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "make_examples_doc.py"), "--check"])
        self.assertEqual(r.returncode, 0, "docs/examples.md is out of date: run python3 tests/make_examples_doc.py")

    def test_every_example_is_described(self):
        sys.path.insert(0, HERE)
        from harness import list_examples
        ex = [f[:-5] for f in list_examples()]
        import make_examples_doc as m
        self.assertEqual(sorted(m.ORDER), ex)


if __name__ == "__main__":
    unittest.main()
