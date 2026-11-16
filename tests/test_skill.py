import json, os, re, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


class SkillTests(unittest.TestCase):
    def setUp(self):
        self.text = open(os.path.join(ROOT, "SKILL.md"), encoding="utf-8").read()
        m = re.match(r"---\nname: (.+)\ndescription: (.+)\n---\n", self.text)
        self.assertTrue(m, "SKILL.md must start with name and description front matter")
        self.name, self.desc = m.group(1), m.group(2)

    def test_front_matter(self):
        self.assertEqual(self.name, "bambu-3mf-builder")
        self.assertLessEqual(len(self.desc), 1024)
        self.assertNotIn("<", self.desc)
        self.assertNotIn(">", self.desc)
        self.assertNotIn("claude", self.name)

    def test_referenced_files_exist(self):
        for rel in re.findall(r"`((?:scripts|references|docs|examples|studio)/[A-Za-z0-9_./<>-]+)`", self.text):
            if "<" in rel:
                continue
            self.assertTrue(os.path.exists(os.path.join(ROOT, rel)), rel)

    def test_commands_in_skill_exist(self):
        for rel in re.findall(r"python3 (scripts/[A-Za-z0-9_]+\.py)", self.text):
            self.assertTrue(os.path.exists(os.path.join(ROOT, rel)), rel)

    def test_trigger_evals_shape(self):
        d = json.load(open(os.path.join(ROOT, "evals", "triggers.json")))
        for k in ("should_trigger", "should_not_trigger"):
            self.assertGreaterEqual(len(d[k]), 8)
            self.assertEqual(len(set(d[k])), len(d[k]))
        self.assertFalse(set(d["should_trigger"]) & set(d["should_not_trigger"]))

    def test_plugin_manifest(self):
        d = json.load(open(os.path.join(ROOT, ".claude-plugin", "plugin.json")))
        self.assertEqual(d["name"], "bambu-3mf-builder")
        self.assertRegex(d["version"], r"^\d+\.\d+\.\d+$")

    def test_marketplace_manifest(self):
        m = json.load(open(os.path.join(ROOT, ".claude-plugin", "marketplace.json")))
        p = json.load(open(os.path.join(ROOT, ".claude-plugin", "plugin.json")))
        self.assertEqual(len(m["plugins"]), 1)
        e = m["plugins"][0]
        self.assertEqual(e["name"], p["name"])             # the install name and the manifest name must match
        self.assertEqual(e["source"], "./")
        self.assertTrue(m["owner"]["name"])

    def test_release_zips(self):
        import tempfile, zipfile, subprocess
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "scripts", "make_release.py"), "--out", d], capture_output=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            a = zipfile.ZipFile(os.path.join(d, "bambu-3mf-builder-skill.zip")).namelist()
            b = zipfile.ZipFile(os.path.join(d, "bambu-3mf-builder-plugin.zip")).namelist()
            self.assertIn("bambu-3mf-builder/SKILL.md", a)
            self.assertIn("skills/bambu-3mf-builder/SKILL.md", b)
            self.assertIn(".claude-plugin/plugin.json", b)
            self.assertFalse([n for n in a + b if "__pycache__" in n or n.endswith(".pyc")])

    def test_rules_doc_lists_every_rule(self):
        doc = open(os.path.join(ROOT, "references", "rules.md"), encoding="utf-8").read()
        for i in range(1, 39):
            self.assertIn("| R%d |" % i, doc)
        for i in range(1, 7):
            self.assertIn("| G%d |" % i, doc)

    def test_docs_do_not_claim_patch_mode_lacks_shipped_features(self):
        """D3: patch mode adds / removes a slot, takes a sliced input and changes the printer; no doc may say it cannot."""
        for name in ("README.md", "SKILL.md", os.path.join("references", "job-format.md")):
            txt = open(os.path.join(ROOT, name), encoding="utf-8").read().lower()
            for bad in ("cannot change the printer", "cannot add a filament slot", "cannot patch a sliced"):
                self.assertNotIn(bad, txt, name)


if __name__ == "__main__":
    unittest.main()
