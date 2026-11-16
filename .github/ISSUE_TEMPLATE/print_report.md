---
name: Print report
about: You printed a file the builder made, and it worked or it did not
labels: print-report
---

The support matrix says "CI-verified, not print-tested". Print reports are how a combination gets tested for real.

**Result** (printed fine / printed with problems / did not print):

**Printer and nozzle:**

**Bambu Studio version you sliced in:**

**Filament(s) and plate:**

**Job file** (paste the TOML):

**What looked wrong, if anything** (a photo helps; Studio preview vs. print):

**Validator output** (`python3 scripts/validate_bambu_3mf.py file.3mf`):

Do not attach a 3MF that still carries your account ID. Run `python3 scripts/sanitize_3mf.py in.3mf out.3mf --report` first.
