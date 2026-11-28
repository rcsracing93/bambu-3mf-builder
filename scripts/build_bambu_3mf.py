#!/usr/bin/env python3
"""Build a Bambu Studio project 3MF from a job file.
Usage: build_bambu_3mf.py job.toml [--out file.3mf] [--slice-check [--studio-path PATH]] [--no-summary] [--user-presets DIR] [--strict-version]
       build_bambu_3mf.py --list printer|process|filament [--studio 2.9.2] [--printer "Bambu Lab P1S 0.4 nozzle"] [--match text]
--slice-check slices the built file in your installed Bambu Studio and says whether it worked. The program is found in the usual
install places; --studio-path (a program, an app or an install folder) or the BAMBU_STUDIO_PATH variable override that.
A summary file <name>.summary.md is written next to the 3MF (settings, overrides, warnings; with --slice-check also Studio's time and
grams). --no-summary turns it off.
A job may name one of your own Studio presets (process or filament) in place of a system preset. It is read at run time from
your Studio data folder, or from --user-presets DIR (or the BAMBU_STUDIO_USER_DIR variable): the data folder, its user folder
or one id folder. Only the file of the preset the job names is read. The preset needs a parent (made from a system preset).
With studio = "auto" and an installed Studio newer than the newest supported version, the file is built for the newest supported
version with a warning (a newer major version also runs the slice check by itself). --strict-version (or strict_version = true in the
job) stops instead.
--orca-lint (alone, with the job) lists the keys of the job that OrcaSlicer would drop or change when it opens the 3MF. It writes nothing.
A job with patch = "file.3mf" changes an existing Studio project and writes a new file (see references/job-format.md, Patch mode).
--list prints selectable Studio preset names, one per line. process and filament need --printer to show only the ones made for it."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bambu3mf.job import load_job, JobError          # noqa: E402
from bambu3mf import package, summary                # noqa: E402
from bambu3mf import patch, patch_job                # noqa: E402


def _opt(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 >= len(argv):
            raise SystemExit("%s needs a value" % name)
        return argv[i + 1]
    return default


def list_presets(argv):
    from bambu3mf.profiles import ProfileSet, ProfileError
    from bambu3mf.job import STUDIO_ALIASES
    kinds = {"printer": "machine", "process": "process", "filament": "filament"}
    if not argv or argv[0] not in kinds:
        print("Usage: --list printer|process|filament [--studio 2.9.2] [--printer NAME] [--match text]", file=sys.stderr)
        return 2
    ver = _opt(argv, "--studio", "2.9.2")
    if ver not in STUDIO_ALIASES:
        print("--studio must be one of: %s" % ", ".join(sorted(k for k in STUDIO_ALIASES if k.count(".") == 2)), file=sys.stderr)
        return 2
    printer, match = _opt(argv, "--printer"), _opt(argv, "--match")
    try:
        ps = ProfileSet(STUDIO_ALIASES[ver])
        kind = kinds[argv[0]]
        if kind != "machine" and printer:
            names = ps.compatible(kind, printer)
        else:
            names = ps.names(kind)
    except ProfileError as e:
        print("cannot list: %s" % e, file=sys.stderr)
        return 1
    for n in names:
        if not match or match.lower() in n.lower():
            print(n)
    return 0


def main_patch(job_path, out, do_check, studio_path, write_md):
    """A job with patch = "file.3mf": change an existing Bambu Studio project (patch mode)."""
    try:
        job = patch_job.load_patch_job(job_path)
        r = patch.run(job, out, log=lambda m: print(m, file=sys.stderr))
    except patch_job.JobError as e:
        print("The patch cannot be made:", file=sys.stderr)
        for p in e.problems:
            print("  - " + p, file=sys.stderr)
        return 1
    except Exception as e:                           # noqa: BLE001
        print("The patch cannot be made: %s" % e, file=sys.stderr)
        return 1
    print(patch.format_summary(r), end="")
    info = None
    rc = 0
    if do_check or studio_path:
        from bambu3mf import studio_detect
        exe, how = studio_detect.find_cli(studio_path)
        if not exe:
            print("slice check: %s" % how, file=sys.stderr)
            rc = 1
        else:
            ok, msg, plates = studio_detect.slice_details(exe, r.path)
            print("slice check (%s): %s" % (how, msg))
            for ln in studio_detect.usage_lines(plates):
                print(ln)
            info = (ok, msg, plates)
            rc = 0 if ok else 1
    if write_md:
        patch.write_summary_file(r, info)
    return rc


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if argv else 2
    if argv[0] == "--list":
        return list_presets(argv[1:])
    do_check = "--slice-check" in argv
    argv = [a for a in argv if a != "--slice-check"]
    orca_lint = "--orca-lint" in argv
    argv = [a for a in argv if a != "--orca-lint"]
    write_md = "--no-summary" not in argv
    argv = [a for a in argv if a != "--no-summary"]
    strict_version = "--strict-version" in argv
    argv = [a for a in argv if a != "--strict-version"]
    studio_path = None
    if "--studio-path" in argv:
        i = argv.index("--studio-path")
        if i + 1 >= len(argv):
            print("--studio-path needs the Bambu Studio program, app or folder", file=sys.stderr)
            return 2
        studio_path = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    user_presets = None
    if "--user-presets" in argv:
        i = argv.index("--user-presets")
        if i + 1 >= len(argv):
            print("--user-presets needs the Bambu Studio data folder (or its user folder)", file=sys.stderr)
            return 2
        user_presets = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    out = None
    if "--out" in argv:
        i = argv.index("--out")
        if i + 1 >= len(argv):
            print("--out needs a file name, for example --out cube.3mf", file=sys.stderr)
            return 2
        out = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    if not argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    if orca_lint:
        if do_check or studio_path or out:
            print("--orca-lint only lints: it writes no file, so it cannot be used with --out, --slice-check or --studio-path", file=sys.stderr)
            return 2
        if patch_job.is_patch_job(argv[0]):
            print("--orca-lint does not lint a patch job", file=sys.stderr)
            return 2
        from bambu3mf import orca_lint as _orca_lint
        return _orca_lint.run(argv[0], user_presets=user_presets, strict_version=strict_version)
    if patch_job.is_patch_job(argv[0]):
        return main_patch(argv[0], out, do_check, studio_path, write_md)
    try:
        job = load_job(argv[0], user_presets=user_presets, strict_version=strict_version)
        s = package.build(job, out)
    except JobError as e:
        print("The job cannot be built:", file=sys.stderr)
        for p in e.problems:
            print("  - " + p, file=sys.stderr)
        return 1
    except Exception as e:                           # config errors etc.
        print("The job cannot be built: %s" % e, file=sys.stderr)
        return 1
    if getattr(job, "studio_note", None):
        print("note: " + job.studio_note)
    print(summary.format_summary(job, s), end="")
    auto_check = getattr(job, "auto_slice_check", False) and not (do_check or studio_path)
    if do_check or studio_path or auto_check:
        from bambu3mf import studio_detect
        exe, how = studio_detect.find_cli(studio_path)
        if not exe and auto_check:
            # newer major Studio: the slice check runs only when the program is found; a missing program is not an error
            print("slice check: not run (%s)" % how)
            if write_md:
                summary.write_summary_file(job, s)
            return 0
        if auto_check:
            print("slice check: run automatically, because the installed Studio is a newer major version")
        if not exe:
            if write_md:
                summary.write_summary_file(job, s)
            print("slice check: %s" % how, file=sys.stderr)
            return 1
        ok, msg, plates = studio_detect.slice_details(exe, s.path)
        print("slice check (%s): %s" % (how, msg))
        for ln in studio_detect.usage_lines(plates):
            print(ln)
        if write_md:
            summary.write_summary_file(job, s, (ok, msg, plates))
        return 0 if ok else 1
    if write_md:
        summary.write_summary_file(job, s)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
