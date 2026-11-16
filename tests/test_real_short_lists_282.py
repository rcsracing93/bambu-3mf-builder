#!/usr/bin/env python3
"""Regression: R5 must accept the short project lists a real Studio 02.08.02.61 save holds.
Real 2-filament save (face mill cover, P1S): filament_mixed_components, filament_mixed_sublayer_ratios and
filament_mixed_gradient_range have 1 entry for 2 filaments, because Studio grows them lazily (config.LAZY_PROJECT_LISTS;
PresetBundle resizes filament_is_mixed, filament_mixed_gradient, ..._curve and ..._per_part only).
The fixture is only that save's project_settings.config (no model, no personal fields; tests/test_privacy.py scans it).
Set REAL_3MF_PRIVATE=/path/to/the/full/file.3mf to also validate the whole private file (R33 is then expected).
Run: python3 -I tests/test_real_short_lists_282.py"""
import json, os, sys
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
from bambu3mf import rules_config, validate, privacy

cfg = json.load(open(os.path.join(H, "data", "two-color-project-settings-282.json")))
LAZY = ("filament_mixed_components", "filament_mixed_sublayer_ratios", "filament_mixed_gradient_range")
assert len(cfg["filament_colour"]) == 2 and all(len(cfg[k]) == 1 for k in LAZY), "fixture shape changed"


def r5(c):
    rep = validate.Report()
    ps = rules_config.load_profiles(c, "BambuStudio-02.08.02.61", rep)
    rules_config.check(c, rep, ps, 1)
    return [f for f in rep.findings if f.rule == "R5"]


fails = []
leaks = privacy.scan_text('project_settings.config', open(os.path.join(H, 'data', 'two-color-project-settings-282.json')).read())
if leaks:
    fails.append('fixture has personal data: %s' % leaks[:2])
if r5(cfg):
    fails.append("R5 flags the real short lists: %s" % [f.msg for f in r5(cfg)][:3])
# teeth: an eager list that is too short must still be flagged, and a lazy one that is too long too
bad = dict(cfg, filament_mixed_gradient=["0"])
if not r5(bad):
    fails.append("R5 no longer flags a short filament_mixed_gradient")
bad = dict(cfg, filament_mixed_components=["", "", ""])
if not r5(bad):
    fails.append("R5 no longer flags a lazy list longer than the filament count")
bad = dict(cfg, filament_mixed_components=[])
if not r5(bad):
    fails.append("R5 no longer flags an empty lazy list")
p = os.environ.get("REAL_3MF_PRIVATE")
if p and os.path.exists(p):
    rep = validate.validate(p, mode="strict", privacy=True)
    other = [f for f in rep.findings if f.level == "error" and f.rule != "R33"]
    if other:
        fails.append("full file: %s" % [(f.rule, f.msg[:80]) for f in other][:3])
print("FAIL: " + "; ".join(fails) if fails else "OK")
sys.exit(1 if fails else 0)
