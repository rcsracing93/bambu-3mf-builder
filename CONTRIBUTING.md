# Contributing

Thanks for helping. Bug reports with a sanitized file are the most useful thing you can send.

## Before you attach a 3MF

Studio-saved files carry account IDs and paths. Run this first and attach the output file:

```
python3 scripts/sanitize_3mf.py yours.3mf yours-clean.3mf --report
```

If the sanitizer exits with 2, a value still looks like a path, e-mail or IP address. Do not attach the file.

## Run the tests

```
python3 tests/run_all.py
```

Needs only Python 3.8 or newer. The real-slicer tests need a Bambu Studio AppImage (Linux) extracted with `--appimage-extract`:

```
python3 tests/studio_slice.py --studio-dir PATH/squashfs-root --version 2.9.1
python3 tests/studio_roundtrip.py --studio-dir PATH/squashfs-root
```

CI runs both for each supported Studio version (`.github/workflows/studio.yml`).

## Rules for changes

- A format rule needs evidence: a line in the Bambu Studio source (name the tag and file), or a real Studio save. A guess is not a rule.
- A new rule gets a test that fails without it (see `tests/test_teeth.py`) and an entry in `references/rules.md`.
- A new example goes in `examples/`, the support matrix and `docs/examples.md` are regenerated (`tests/make_matrix.py`, `tests/make_examples_doc.py`).
- Test fixtures must be generated or sanitized. `tests/test_privacy.py` scans all of them.
- Standard library only. The one vendored package is tomli (MIT) for Python below 3.11.
- Do not copy code from other projects. Read, then write your own. Prior art is credited in `NOTICE`.

## Adding a printer

The builder already supports every printer that has a machine preset in the vendored profiles. To add a tested example:

1. List the exact names: `python3 scripts/build_bambu_3mf.py --list printer --match NAME`, then `--list process` and `--list filament` with `--printer`.
2. Copy `examples/single_p1s.toml`, change the three names and the filament preset, and save it as `examples/single_NAME.toml`.
3. Add the name to `ORDER` and `ABOUT` in `tests/make_examples_doc.py`, then run `tests/make_examples_doc.py` and the real-slicer test for it.
4. Multi-nozzle printers (H2D) also need the R36 checks and a two-color example that uses both nozzles.

## Adding a Studio version

1. Add the tag and its profiles with `scripts/vendor_profiles.py`, generate the option table with `scripts/gen_bambu_options.py`.
2. Read the diff of the format code between tags (`scripts/gen_version_diff.py` gives the option diff) and add what changed.
3. Add the version to `STUDIO_VERSIONS` and the CI matrix, then run the real-slicer tests.

## License

Contributions are under AGPL-3.0, like the rest of the project.
