#!/usr/bin/env bash
# Rebuild the test environment from nothing: both Bambu Studio AppImages (Linux, headless), both Studio source tags, then the unit suite.
# Usage: tools/bootstrap.sh [WORK_DIR]   (default: the folder above the repo). Safe to run again: skips what is already there.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${1:-$(dirname "$REPO")}"
mkdir -p "$WORK" "$WORK/../src"
SRC="$(cd "$WORK/../src" && pwd)"
get_studio () {   # dir tag asset
  local dir="$WORK/$1"
  if [ -x "$dir/squashfs-root/bin/bambu-studio" ]; then echo "Studio $2 already in $dir"; return; fi
  mkdir -p "$dir" && cd "$dir"
  curl -sSL -o studio.AppImage "https://github.com/bambulab/BambuStudio/releases/download/$2/$3"
  chmod +x studio.AppImage && ./studio.AppImage --appimage-extract > /dev/null && rm -f studio.AppImage
  echo "Studio $2 extracted to $dir"
}
get_studio studio282 v02.08.02.61 BambuStudio_ubuntu24.04-v02.08.02.61-20260820225108.AppImage
get_studio studio291 v02.09.01.70 BambuStudio_ubu24-v02.09.01.70-20261103183404.AppImage
for tag in v02.08.02.61 v02.09.01.70; do
  d="$SRC/bs-$tag"
  if [ -d "$d/src/libslic3r" ]; then echo "source $tag already in $d"; continue; fi
  git clone -q --depth 1 --branch "$tag" https://github.com/bambulab/BambuStudio.git "$d" && echo "source $tag cloned to $d"
done
command -v xvfb-run > /dev/null || echo "NOTE: xvfb-run is missing; install xvfb to run Studio headless"
cd "$REPO" && python3 tests/run_all.py
