#!/usr/bin/env python3
"""Fixture test 3a: resolved system presets equal a real Studio 2.8.2.61 save for every key outside its
different_settings_to_system list. Usage: test_fixture_282.py <studio-saved.3mf>"""
import json, os, sys, zipfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from bambu3mf.profiles import ProfileSet
from bambu3mf.serialize import normalize


def main(path):
    c = json.loads(zipfile.ZipFile(path).read("Metadata/project_settings.config"))
    ps = ProfileSet("2.8.2.61")
    diff = c["different_settings_to_system"]            # [print, filament1..n, printer]
    n = len(c["filament_colour"])
    inh = c["inherits_group"]
    print_name = inh[0] or c["print_settings_id"]
    fil_name = inh[1] or c["filament_settings_id"][0]
    mach_name = inh[n + 1] or c["printer_settings_id"]
    res = {"process": ps.resolve("process", print_name)[0],
           "filament": ps.resolve("filament", fil_name)[0],
           "machine": ps.resolve("machine", mach_name)[0]}
    owner_diff = {"process": set(filter(None, diff[0].split(";"))),
                  "filament": set(filter(None, diff[1].split(";"))),
                  "machine": set(filter(None, diff[n + 1].split(";")))}
    checked = 0; bad = []; missing = []
    by = {o["key"]: o for o in json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "studio", "2.8.2.61", "options.json")))["options"]}
    set_by_builder = {"print_settings_id", "filament_settings_id", "printer_settings_id", "compatible_printers", "compatible_printers_condition"}
    for kind, cfg in res.items():
        for k, v in cfg.items():
            if k in owner_diff[kind] or k in set_by_builder:
                continue
            v = normalize(by[k], v)
            if k not in c:
                missing.append((kind, k)); continue
            checked += 1
            if c[k] != v:
                bad.append((kind, k, c[k] if len(str(c[k])) < 80 else str(c[k])[:80], v if len(str(v)) < 80 else str(v)[:80]))
    print("presets:", print_name, "|", fil_name, "|", mach_name)
    print("keys compared:", checked, " mismatches:", len(bad), " resolved keys absent from the save:", len(missing))
    for b in bad[:40]:
        print("  MISMATCH", b)
    for m in missing[:20]:
        print("  ABSENT", m)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
