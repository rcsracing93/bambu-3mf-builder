"""A small worker pool for the test scripts (standard library only).

Each work item runs a subprocess (a test file, a build, a Studio slice), so threads are enough: the work runs in the child processes.
jobs(argv) reads -j N from the command line (default: the CPU count). run(fn, items, n) returns the results in the order of items.
"""
import os
from concurrent.futures import ThreadPoolExecutor


def jobs(argv, default=None):
    """-j N or -jN from argv; default the CPU count. Removes the option from argv."""
    n = default or os.cpu_count() or 1
    for i, a in enumerate(list(argv)):
        if a == "-j" and i + 1 < len(argv):
            n = int(argv[i + 1])
            del argv[i:i + 2]
            break
        if a.startswith("-j") and a[2:].isdigit():
            n = int(a[2:])
            del argv[i]
            break
    return max(1, n)


def run(fn, items, n):
    items = list(items)
    if n <= 1 or len(items) <= 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(max_workers=n) as ex:
        return list(ex.map(fn, items))
