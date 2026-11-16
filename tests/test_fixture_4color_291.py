#!/usr/bin/env python3
"""Test 3c-flush: a real Studio 2.9.1.70 save (4 filaments, purge volumes on Auto-Calc, unmodified presets).
Builder must reproduce every key. wipe_tower_y is reported only (Studio's saved value depends on when the
tower was last resized in the GUI). Run: python3 -I tests/test_fixture_4color_291.py"""
import json, os, sys, zipfile
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
from bambu3mf.job import load_job
from bambu3mf import config

real = json.loads(zipfile.ZipFile(os.path.join(H, "data", "four-color-autocalc-291.3mf")).read("Metadata/project_settings.config"))
job = load_job(os.path.join(H, "jobs", "four_color_291.toml"))
job.plate_type = real["curr_bed_type"]
mine = config.build_config(job, plates=1, max_height=25.6).config
extra = set()   # a save with unmodified presets omits different_settings_to_system / inherits_group; so does the builder
missing = sorted(set(real) - set(mine))
unexpected = sorted(set(mine) - set(real) - extra)
hard = [k for k in real if k in mine and mine[k] != real[k] and k not in ("wipe_tower_x", "wipe_tower_y")]
print("keys real %d, built %d; missing %s; unexpected %s; differing %s" % (len(real), len(mine), missing, unexpected, hard))
print("purge matrix identical:", mine["flush_volumes_matrix"] == real["flush_volumes_matrix"], "| wipe_tower x/y built %s/%s real %s/%s (soft)" % (mine["wipe_tower_x"], mine["wipe_tower_y"], real["wipe_tower_x"], real["wipe_tower_y"]))
ok = not (missing or unexpected or hard)
print("OK" if ok else "FAIL")
sys.exit(0 if ok else 1)
