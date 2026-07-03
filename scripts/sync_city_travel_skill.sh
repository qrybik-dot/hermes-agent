#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
source_dir="$repo_root/local-skills/productivity/city-travel-concierge"
hermes_home="${HERMES_HOME:-$HOME/.hermes}"
destination="$hermes_home/skills/productivity/city-travel-concierge"
backup_root="$hermes_home/backups/city-travel-concierge"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="$backup_root/pre-sync-$stamp"

if [[ ! -f "$source_dir/SKILL.md" ]]; then
  echo "BLOCKED: missing source SKILL.md" >&2
  exit 2
fi

umask 077
mkdir -p "$backup_root" "$(dirname "$destination")"
if [[ -d "$destination" ]]; then
  cp -a "$destination" "$backup"
else
  mkdir -p "$backup"
fi
mkdir -p "$destination"
rsync -a --exclude '__pycache__/' --exclude '*.pyc' "$source_dir/" "$destination/"
chmod 600 "$destination/SKILL.md"
chmod 700 "$destination"/scripts/*.py
chmod 600 "$destination"/tests/*.py

python3 -B -m unittest discover -s "$destination/tests"
printf 'READY\nsource=%s\ndestination=%s\nbackup=%s\n' "$source_dir" "$destination" "$backup"
