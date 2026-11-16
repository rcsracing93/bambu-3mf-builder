#!/usr/bin/env python3
"""R28 per-object / per-part vector overrides. Studio writes a numeric vector with "," between the slots and "nil"
for an unset slot of a nullable option ("200,nil", Config.hpp serialize); only string vectors use ";". Synthetic: a builder cube with an
override line added. Run: python3 -I tests/test_r28_vectors.py"""
import os, sys, tempfile, zipfile
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
GOOD = os.path.join(tmp, "good.3mf")
package.build(load_job(os.path.join(H, "jobs", "four_color_282.toml")), GOOD)
MS = "Metadata/model_settings.config"
OBJ_ANCHOR = '<metadata face_count="12"/>'                   # the object's own metadata block
PART_ANCHOR = "<mesh_stat"                                   # last line of the part's metadata


def with_override(k, v, where="part"):
    z = zipfile.ZipFile(GOOD)
    p = os.path.join(tmp, "o_%s_%s.3mf" % (where, abs(hash((k, v)))))
    line = '<metadata key="%s" value="%s"/>\n    ' % (k, v)
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zo:
        for i in z.infolist():
            d = z.read(i.filename)
            if i.filename == MS:
                t = d.decode()
                a = PART_ANCHOR if where == "part" else OBJ_ANCHOR
                assert a in t
                t = t.replace(a, line + a, 1)
                d = t.encode()
            zo.writestr(i.filename, d)
    return p


def r28(path):
    return [f for f in validate.validate(path, mode="strict").findings if f.rule == "R28"]


for where in ("part", "object"):
    check("%s: inner_wall_speed '200,200' passes" % where, not r28(with_override("inner_wall_speed", "200,200", where)), [str(f)[:100] for f in r28(with_override("inner_wall_speed", "200,200", where))])
    check("%s: inner_wall_speed '200,nil' passes (nullable)" % where, not r28(with_override("inner_wall_speed", "200,nil", where)))
    check("%s: inner_wall_speed '200' (one slot) passes" % where, not r28(with_override("inner_wall_speed", "200", where)))
    got = r28(with_override("inner_wall_speed", "200,abc", where))
    check("%s: inner_wall_speed '200,abc' fails, names the value" % where, len(got) == 1 and "200,abc" in got[0].msg and "not a number" in got[0].msg, [str(f)[:100] for f in got])
    got = r28(with_override("top_solid_infill_flow_ratio", "1,nil", where))
    check("%s: nil in a non-nullable vector fails" % where, len(got) == 1 and "nil is not allowed" in got[0].msg, [str(f)[:100] for f in got])
    got = r28(with_override("inner_wall_speed", "200;200", where))
    check("%s: numeric vector with ';' fails (Studio separates numbers with ',')" % where, len(got) == 1, [str(f)[:100] for f in got])
    got = r28(with_override("wall_loops", "abc", where))
    check("%s: a scalar option still fails with a bad value" % where, len(got) == 1)

print("\n%d failed" % len(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
