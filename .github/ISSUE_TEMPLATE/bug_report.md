---
name: Bug report
about: The builder or validator gave a wrong result
labels: bug
---

**What happened**

**What you expected**

**Versions**
- Bambu Studio version:
- Printer and process preset:
- Python version and OS:

**Job file** (paste the TOML)

**Command and full output**

**Validator output** (`python3 scripts/validate_bambu_3mf.py file.3mf`), if a 3MF is involved

**3MF file**: attach only a file cleaned with `python3 scripts/sanitize_3mf.py in.3mf out.3mf --report`. Never attach a file that still carries your account ID.
