"""Checks for jobs that use `recipe = ...` (the lead calls this from tests/forward_check.py).
    checks(raw, f3mf, gcodes) -> [(name, ok, detail)]
raw = the job TOML as a dict, f3mf = the built 3MF, gcodes = the sliced plate G-code files.
The expectation comes from the job file and the recipe table (scripts/bambu3mf/recipes.py), never from what the builder wrote:
each key is the recipe's value, or the job's own [settings] value when the job sets that key.
1. every expected key is in Metadata/project_settings.config of the 3MF with the expected value;
2. every expected key is in the "; key = value" config block of each sliced G-code file with the expected value.
Every check says how many items it measured. 0 measured is NOT CHECKED, a failure."""
import json
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
sys.path.insert(0, HERE)
from bambu3mf import recipes      # noqa: E402
import harness                    # noqa: E402

NC = "NOT CHECKED (0 measured)"


def _norm(v):
    """One comparable text for a value: true/false -> 1/0, numbers without a trailing .0, lists to their first item."""
    if isinstance(v, list):
        v = v[0] if v else ""
    if isinstance(v, bool):
        return "1" if v else "0"
    s = str(v).strip()
    if s.lower() in ("true", "false"):
        return "1" if s.lower() == "true" else "0"
    try:
        f = float(s)
        return repr(int(f)) if f == int(f) else repr(f)
    except ValueError:
        return s


def expected(raw):
    """{key: value} the job must produce: the recipe values, then the job's own [settings] for the same keys. [] names if no recipe."""
    given = raw.get("recipe")
    names = [given] if isinstance(given, str) else list(given or [])
    want = {}
    for n in names:
        want.update(recipes.RECIPES[n]["settings"])
    for k, v in (raw.get("settings") or {}).items():
        if k in want:
            want[k] = v
    return names, want


def checks(raw, f3mf, gcodes):
    names, want = expected(raw)
    out = []
    if not want:
        return [("recipe keys", False, NC + ": the job has no recipe")]
    with zipfile.ZipFile(f3mf) as z:
        cfg = json.loads(z.read("Metadata/project_settings.config").decode())
    bad = [(k, cfg.get(k), v) for k, v in want.items() if k not in cfg or _norm(cfg[k]) != _norm(v)]
    out.append(("recipe %s: %d keys in project_settings.config" % ("+".join(names), len(want)), not bad,
                "measured %d keys, %d wrong: %s" % (len(want), len(bad), bad)))
    if not gcodes:
        out.append(("recipe keys in G-code config block", False, NC + ": no G-code"))
        return out
    for g in gcodes:
        blk = harness.config_block(g)
        bad = [(k, blk.get(k), v) for k, v in want.items() if k not in blk or _norm(blk[k]) != _norm(v)]
        out.append(("recipe keys in %s config block" % os.path.basename(g), not bad,
                    "measured %d keys, %d wrong: %s" % (len(want), len(bad), bad)))
    return out
