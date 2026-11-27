#!/bin/sh
# Install check for the Claude Code plugin route and the skill zip (run on a computer with Claude Code; needs network for GitHub).
#   1. In a throwaway Claude config (CLAUDE_CONFIG_DIR and HOME both set to a new temp folder, so your own ~/.claude is never read
#      or written): `claude plugin marketplace add <repo>` and `claude plugin install bambu-3mf-builder@bambu-3mf-builder`.
#      PASS when `claude plugin details bambu-3mf-builder` lists the skill bambu-3mf-builder.
#   2. The skill zip has SKILL.md at its root folder (bambu-3mf-builder/SKILL.md), built with scripts/make_release.py.
#   3. Your real ~/.claude/plugins listing is the same before and after (proof that the check did not touch it).
# Usage: sh tests/install_check.sh [--repo OWNER/NAME] [--report FILE]
# The marketplace step installs what is on GitHub now (the public default branch), not this work tree.
set -u
REPO="rcsracing93/bambu-3mf-builder"
REPORT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --report) REPORT="$2"; shift 2 ;;
    *) echo "unknown argument $1"; exit 2 ;;
  esac
done
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
T="$(mktemp -d)"
OUT="$T/report.txt"
PASS=0; FAIL=0
say() { echo "$1" | tee -a "$OUT" >/dev/null; echo "$1"; }
ok() { PASS=$((PASS + 1)); say "PASS $1"; }
bad() { FAIL=$((FAIL + 1)); say "FAIL $1"; }
: > "$OUT"
say "Claude Code: $(claude --version 2>&1 | head -1). Repo: $REPO. Throwaway config: $T/home"

real_before="$(ls -laR "$HOME/.claude/plugins" 2>/dev/null | cksum)"
mkdir -p "$T/home/.claude"
run() { env HOME="$T/home" CLAUDE_CONFIG_DIR="$T/home/.claude" claude "$@" > "$T/last.txt" 2>&1; }

if run plugin marketplace add "$REPO"; then ok "claude plugin marketplace add $REPO"; else bad "claude plugin marketplace add $REPO: $(tail -3 "$T/last.txt")"; fi
if run plugin install bambu-3mf-builder@bambu-3mf-builder; then ok "claude plugin install bambu-3mf-builder@bambu-3mf-builder"; else bad "claude plugin install: $(tail -3 "$T/last.txt")"; fi
run plugin details bambu-3mf-builder
if grep -q "Skills (1)" "$T/last.txt" && grep -q "bambu-3mf-builder" "$T/last.txt"; then
  ok "claude plugin details lists Skills (1) bambu-3mf-builder"
else
  bad "claude plugin details does not list the skill: $(head -20 "$T/last.txt" | tr '\n' ' ')"
fi
grep -i "skills" "$T/last.txt" | head -3 | while read -r l; do say "     $l"; done

python3 -I "$ROOT/scripts/make_release.py" --out "$T/rel" > /dev/null
if python3 -I -m zipfile -l "$T/rel/bambu-3mf-builder-skill.zip" | grep -c "^bambu-3mf-builder/SKILL.md " | grep -qx 1; then
  ok "skill zip (this work tree) has bambu-3mf-builder/SKILL.md at its root"
else
  bad "skill zip has no bambu-3mf-builder/SKILL.md at its root"
fi

real_after="$(ls -laR "$HOME/.claude/plugins" 2>/dev/null | cksum)"
if [ "$real_before" = "$real_after" ]; then ok "your own ~/.claude/plugins is unchanged"; else bad "your own ~/.claude/plugins changed"; fi

say ""
say "$((PASS + FAIL)) checks measured, $FAIL failed"
[ $((PASS + FAIL)) -eq 0 ] && say "NOT CHECKED"
[ -n "$REPORT" ] && cp "$OUT" "$REPORT"
rm -rf "$T"
[ "$FAIL" -eq 0 ] && [ $((PASS + FAIL)) -gt 0 ]
