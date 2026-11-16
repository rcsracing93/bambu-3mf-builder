#!/usr/bin/env python3
"""R11: Studio writes different_settings_to_system and inherits_group each only if one entry is not empty
(PresetBundle.cpp add_if_some_non_empty), so either may be absent; absent = all entries empty. The length n + 2 is checked only when
the list is there. Synthetic: a builder file with both lists, one or both dropped. Run: python3 -I tests/test_r11_lists.py"""
import json, os, sys, tempfile, zipfile
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(H, "..", "scripts"))
os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
from bambu3mf import validate, package  # noqa: E402
from bambu3mf.job import load_job  # noqa: E402

fails = []
def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + ((" - " + str(extra)) if (extra and not cond) else ""))
    if not cond: fails.append(name)

tmp = tempfile.mkdtemp()
LISTS = os.path.join(tmp, "lists.3mf")
package.build(load_job(os.path.join(H, "data", "mutation_base.toml")), LISTS)
sys.path.insert(0, H)
from fixture_lists import add_inherits_group  # noqa: E402
add_inherits_group(LISTS)
PS = "Metadata/project_settings.config"
c0 = json.loads(zipfile.ZipFile(LISTS).read(PS))
check("fixture has both lists", "different_settings_to_system" in c0 and "inherits_group" in c0)


def variant(name, fn):
    z = zipfile.ZipFile(LISTS)
    p = os.path.join(tmp, name + ".3mf")
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zo:
        for i in z.infolist():
            d = z.read(i.filename)
            if i.filename == PS:
                c = json.loads(d)
                fn(c)
                d = json.dumps(c).encode()
            zo.writestr(i.filename, d)
    return p


def r11(p, mode="strict"):
    return [f for f in validate.validate(p, mode=mode).findings if f.rule == "R11"]


check("both lists present: no R11", not r11(LISTS))
check("inherits_group absent: no R11", not r11(variant("noig", lambda c: c.pop("inherits_group"))))
check("different_settings_to_system absent: no R11", not r11(variant("nodl", lambda c: c.pop("different_settings_to_system"))))
check("both absent: no R11", not r11(variant("none", lambda c: (c.pop("inherits_group"), c.pop("different_settings_to_system")))))
check("inherits_group all empty is the same as absent", not r11(variant("empty", lambda c: c.__setitem__("inherits_group", [""] * len(c["inherits_group"])))))
got = r11(variant("shortig", lambda c: c.__setitem__("inherits_group", c["inherits_group"][:-1])))
check("inherits_group one entry short: R11 (length checked when present)", len(got) == 1 and "n + 2" in got[0].msg, [str(f)[:100] for f in got])
got = r11(variant("shortdl", lambda c: c.__setitem__("different_settings_to_system", c["different_settings_to_system"] + [""])))
check("different_settings_to_system one entry long: R11", len(got) == 1, [str(f)[:100] for f in got])
check("default mode: absent list is not even a warning", not r11(variant("noig2", lambda c: c.pop("inherits_group")), "default"))

print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
