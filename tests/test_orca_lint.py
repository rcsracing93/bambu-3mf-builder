#!/usr/bin/env python3
"""--orca-lint: the keys of a job that OrcaSlicer drops or changes. Read-only.
A job with a key Orca lacks, a renamed key, a changed value and a rejected enum value; a job with common keys only; output lines,
citations and counts; an empty measurement is NOT CHECKED; nothing is written; the exit codes; a normal build is the same with and
without a lint run before it; the data file is consistent.
Run: python3 -I tests/test_orca_lint.py"""
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
B = os.path.join(ROOT, "scripts", "build_bambu_3mf.py")
CUBE = os.path.normpath(os.path.join(HERE, "data", "cube20.stl"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from bambu3mf import orca_lint                                   # noqa: E402

HEAD = '''studio  = "2.9.2"
output  = "out.3mf"
title   = "Lint"
printer = "Bambu Lab P1S 0.4 nozzle"
process = "0.20mm Standard @BBL X1C"
plate_type = "Textured PEI Plate"
'''
TAIL = '''
[[filament]]
preset = "Bambu PLA Basic @BBL P1S 0.4 nozzle"
color  = "#000000"
[filament.settings]
nozzle_temperature = 225

[[object]]
name = "Cube"
[object.settings]
brim_type = "no_brim"
[[object.part]]
name = "Body"
mesh = %s
filament = 1
''' % json.dumps(CUBE)

MIXED = HEAD + '''
[settings]
wall_loops = 3
enable_circle_compensation = true
sparse_infill_anchor = 400
sparse_infill_pattern = "zig-zag"
ensure_vertical_shell_thickness = "enabled"
initial_layer_print_height = 0.2
''' + TAIL

COMMON = HEAD + '''
[settings]
wall_loops = 3
sparse_infill_density = "15%"
layer_height = 0.2
''' + TAIL


def run(*a, cwd=None):
    return subprocess.run([sys.executable, "-I", B] + list(a), capture_output=True, text=True, cwd=cwd)


def job_in(tmp, text, name="job.toml"):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def listing(d):
    return sorted(os.listdir(d))


class LintOutput(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_mixed_job(self):
        p = job_in(self.tmp.name, MIXED)
        r = run(p, "--orca-lint", cwd=self.tmp.name)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        finding = [ln for ln in lines if ": dropped:" in ln or ": renamed:" in ln or ": value:" in ln or ": rejected:" in ln]
        self.assertEqual(len(finding), 4, r.stdout)
        by_key = {ln.split()[1]: ln for ln in finding}
        self.assertIn("enable_circle_compensation", by_key)
        self.assertIn("dropped: not defined in Orca PrintConfig.cpp v2.5.2", by_key["enable_circle_compensation"])
        self.assertIn("renames it to infill_anchor", by_key["sparse_infill_anchor"])
        self.assertIn("renamed:", by_key["sparse_infill_anchor"])
        self.assertIn("zig-zag to rectilinear", by_key["sparse_infill_pattern"])
        self.assertIn("no value enabled", by_key["ensure_vertical_shell_thickness"])
        for ln in finding:                                        # every finding names a source line
            self.assertRegex(ln, r"src/libslic3r/\w+\.cpp line \d+", ln)
        self.assertNotIn("initial_layer_print_height", r.stdout)   # an absolute value is not cleared; only a percentage is
        self.assertNotIn("wall_loops", r.stdout)
        self.assertIn("4 finding(s): 1 dropped, 1 renamed, 1 value changed, 1 value rejected", r.stdout)
        self.assertRegex(r.stdout, r"orca-lint: \d+ key\(s\) measured")
        self.assertIn("OrcaSlicer v2.5.2", lines[-1])
        self.assertIn("read-only", lines[-1])

    def test_common_keys_only(self):
        p = job_in(self.tmp.name, COMMON)
        r = run(p, "--orca-lint", cwd=self.tmp.name)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("0 finding(s): 0 dropped, 0 renamed, 0 value changed, 0 value rejected", r.stdout)
        self.assertNotIn("NOT CHECKED", r.stdout)
        measured = int(r.stdout.split("orca-lint: ")[1].split(" key(s)")[0])
        self.assertGreaterEqual(measured, 5)

    def test_builder_default_is_not_reported(self):
        # The builder adds sparse_infill_pattern = zig-zag when a job does not set it. The job did not write it, so it is not listed.
        p = job_in(self.tmp.name, COMMON)
        r = run(p, "--orca-lint", cwd=self.tmp.name)
        self.assertNotIn("sparse_infill_pattern", r.stdout)

    def test_not_checked_when_nothing_measured(self):
        stub = types.SimpleNamespace(settings={}, filaments=[{"settings": {}}], objects=[])
        with mock.patch.object(orca_lint._job, "load_job", return_value=stub), \
                mock.patch.object(orca_lint._job, "read_raw", return_value={}):
            out = []
            rc = orca_lint.run("x.toml", out=out.append)
        self.assertEqual(rc, 0)
        self.assertTrue(any("NOT CHECKED" in ln for ln in out), out)
        self.assertFalse(any("0 key(s) measured" in ln for ln in out))

    def test_unit_checks(self):
        d = orca_lint.load_data()
        self.assertEqual(orca_lint.check_key(d, "wall_loops", 3), [])
        self.assertEqual(orca_lint.check_key(d, "sparse_infill_pattern", "gyroid"), [])
        kinds = [k for k, _ in orca_lint.check_key(d, "top_one_wall_type", "none")]
        self.assertEqual(kinds, ["dropped"])                       # renamed only for a value other than none
        kinds = [k for k, _ in orca_lint.check_key(d, "top_one_wall_type", "all top")]
        self.assertEqual(kinds, ["renamed"])
        kinds = [k for k, _ in orca_lint.check_key(d, "initial_layer_speed", "50%")]
        self.assertEqual(kinds, ["dropped"])
        self.assertEqual(orca_lint.check_key(d, "initial_layer_speed", "50"), [])


class ReadOnly(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_nothing_written(self):
        p = job_in(self.tmp.name, MIXED)
        before = listing(self.tmp.name)
        r = run(p, "--orca-lint", cwd=self.tmp.name)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(listing(self.tmp.name), before)
        self.assertEqual(before, ["job.toml"])                     # no 3MF, no summary file

    def test_flag_position_does_not_matter(self):
        p = job_in(self.tmp.name, MIXED)
        a = run(p, "--orca-lint", cwd=self.tmp.name).stdout
        b = run("--orca-lint", p, cwd=self.tmp.name).stdout
        self.assertEqual(a, b)

    def test_flag_cannot_combine_with_output_flags(self):
        p = job_in(self.tmp.name, MIXED)
        for extra in (["--out", "x.3mf"], ["--slice-check"], ["--studio-path", "/nowhere"]):
            r = run(p, "--orca-lint", *extra, cwd=self.tmp.name)
            self.assertEqual(r.returncode, 2, (extra, r.stderr))
        self.assertEqual(listing(self.tmp.name), ["job.toml"])

    def test_bad_job_is_an_error_and_writes_nothing(self):
        p = job_in(self.tmp.name, HEAD + "\n[settings]\nnot_a_studio_key = 1\n" + TAIL)
        r = run(p, "--orca-lint", cwd=self.tmp.name)
        self.assertEqual(r.returncode, 1)
        self.assertIn("cannot be linted", r.stderr)
        self.assertEqual(listing(self.tmp.name), ["job.toml"])

    def test_normal_build_is_unchanged(self):
        p = job_in(self.tmp.name, COMMON)
        r1 = run(p, "--no-summary", cwd=self.tmp.name)
        self.assertEqual(r1.returncode, 0, r1.stderr)
        with open(os.path.join(self.tmp.name, "out.3mf"), "rb") as f:
            first = f.read()
        os.remove(os.path.join(self.tmp.name, "out.3mf"))
        self.assertEqual(run(p, "--orca-lint", cwd=self.tmp.name).returncode, 0)
        self.assertEqual(listing(self.tmp.name), ["job.toml"])
        r2 = run(p, "--no-summary", cwd=self.tmp.name)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        with open(os.path.join(self.tmp.name, "out.3mf"), "rb") as f:
            self.assertEqual(f.read(), first)
        self.assertNotIn("orca", r1.stdout.lower())               # the normal build says nothing about Orca


class DataFile(unittest.TestCase):
    def setUp(self):
        self.d = orca_lint.load_data()

    def test_header(self):
        self.assertEqual(self.d["orca_tag"], "v2.5.2")
        self.assertEqual(len(self.d["orca_commit"]), 40)

    def test_records_cite_lines(self):
        n = 0
        for sect in ("renamed", "cleared"):
            for k, r in self.d[sect].items():
                self.assertGreater(r["line"], 0, (sect, k))
                n += 1
        for m in self.d["value_maps"]:
            self.assertGreater(m["line"], 0, m)
            n += 1
        for k, r in self.d["enum_reject"].items():
            self.assertGreater(r["line"], 0, k)
            self.assertTrue(r["rejected"], k)
            self.assertFalse(set(r["rejected"]) & set(r["accepted"]), k)
            n += 1
        self.assertGreater(n, 50)                                  # NOT CHECKED if the data were empty
        for k in ("load_rule", "table_rule", "enum_rule"):
            self.assertGreater(self.d[k]["line"], 0, k)

    def test_renames_are_consistent(self):
        n = 0
        for k, r in self.d["renamed"].items():
            self.assertNotIn(k, self.d["keys"], "%s is renamed, so Orca must not define it" % k)
            self.assertEqual(r["to"] in self.d["keys"], r["to_defined"], k)
            n += 1
        self.assertGreater(n, 20)

    def test_every_studio_option_is_classified(self):
        # Each option of the builder's tables is kept, renamed, cleared or dropped: the lint gives one answer for every key.
        n = 0
        for v in ("2.8.2.61", "2.9.1.70", "2.9.2.62"):
            with open(os.path.join(ROOT, "studio", v, "options.json"), encoding="utf-8") as f:
                for o in json.load(f)["options"]:
                    res = orca_lint.check_key(self.d, o["key"], "x")
                    for kind, text in res:
                        self.assertIn(kind, ("dropped", "renamed", "value", "rejected"))
                        self.assertRegex(text, r"Orca")
                    n += 1
        self.assertGreater(n, 1500)


if __name__ == "__main__":
    unittest.main()
