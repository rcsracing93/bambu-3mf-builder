#!/usr/bin/env python3
"""Test 3c: rebuild the project_settings.config of a real Studio 2.8.2.61 save from its presets and its
overrides, and compare key by key. Usage: test_oracle_282.py <studio-saved.3mf>
Keys the builder cannot know from presets (wipe tower position, flush matrix) are reported, not failed."""
import json, os, sys, zipfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from bambu3mf.profiles import ProfileSet
from bambu3mf.config import build_config, dumps


class J:
    pass


def main(path):
    c = json.loads(zipfile.ZipFile(path).read("Metadata/project_settings.config"))
    n = len(c["filament_colour"])
    diff = c["different_settings_to_system"]
    ps = ProfileSet("2.8.2.61")
    job = J()
    job.profiles = ps
    job.process = c["inherits_group"][0] or c["print_settings_id"]
    job.printer = c["inherits_group"][n + 1] or c["printer_settings_id"]
    job.plate_type = c["curr_bed_type"]
    by = ps.options
    job.settings = {k: c[k] for k in filter(None, diff[0].split(";"))}
    job.filaments = []
    vf = len(c["filament_extruder_variant"]) // n
    for i in range(n):
        st = {}
        for k in filter(None, diff[1 + i].split(";")):
            o = by[k]
            if o["vector"]:
                if o["variant"] == "filament":
                    st[k] = c[k][i * vf:(i + 1) * vf]
                else:
                    st[k] = c[k][i]
            else:
                st[k] = c[k]
        job.filaments.append({"preset": c["inherits_group"][1 + i] or c["filament_settings_id"][i], "color": c["filament_colour"][i], "settings": st})
    res = build_config(job, plates=len(c["wipe_tower_x"]))
    mine = res.config
    bad = []
    soft = {"wipe_tower_x", "wipe_tower_y", "flush_volumes_matrix", "flush_volumes_vector", "filament_nozzle_map", "filament_colour_type"}  # last two: the oracle came from a downloaded file (1); a fresh save writes 0
    for k in sorted(set(c) | set(mine)):
        if k in ("filament_settings_id",):   # oracle names a user preset; builder names the system parent
            continue
        a, b = c.get(k, "<absent>"), mine.get(k, "<absent>")
        if a != b:
            bad.append((k, str(a)[:70], str(b)[:70]))
    hard = [b for b in bad if b[0] not in soft]
    print("keys in save: %d, built: %d, differing: %d (hard %d)" % (len(c), len(mine), len(bad), len(hard)))
    for b in bad:
        print(("  SOFT " if b[0] in soft else "  DIFF ") + str(b))
    return 0 if not hard else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
