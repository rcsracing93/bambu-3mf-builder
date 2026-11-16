#!/usr/bin/env python3
"""Run every test. Usage: python3 tests/run_all.py   (exit 1 if any fails)"""
import os, subprocess, sys
H = os.path.dirname(os.path.abspath(__file__))
fixtures = {"test_fixture_282.py": None, "test_oracle_282.py": None}      # need a Studio save passed in; skipped in CI
bad = []
for t in sorted(f for f in os.listdir(H) if f.startswith("test_") and f.endswith(".py")):
    if t in fixtures:
        continue
    r = subprocess.run([sys.executable, "-I", os.path.join(H, t)], capture_output=True, text=True)
    print(("PASS " if r.returncode == 0 else "FAIL ") + t)
    if r.returncode:
        bad.append(t); print(r.stdout[-1500:], r.stderr[-1500:])
sys.exit(1 if bad else 0)
